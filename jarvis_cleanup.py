"""Liberar espacio: primero se mide, después se borra solo lo que el usuario marca.

Categorías (solo aparecen si ocupan algo):
- ``temporales``: archivos de %TEMP% con más de 24 h (los recientes pueden estar en uso).
- ``temporales_windows``: lo mismo en C:\\Windows\\Temp (Jarvis corre como administrador).
- ``papelera``: la Papelera de reciclaje; **se vacía para siempre** (SHEmptyRecycleBin).
- ``cache_brave``: caché de páginas, código y sombreadores de Brave (se regenera sola).
- ``docker``: imágenes de Docker sin etiqueta («dangling») y caché de compilación;
  nunca las imágenes que usan los contenedores de voz.
- ``ollama:<modelo>``: cada modelo de Ollama que Jarvis no usa (texto, visión y
  recuerdos se excluyen); hay que volver a descargarlo para usarlo. Sin marcar por defecto.

Los archivos bloqueados (en uso) se saltan sin error. Nunca se sigue un enlace
simbólico fuera de la carpeta medida. Todo se registra sin rutas personales.
"""

import ctypes
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

import jarvis_core as core

ANTIGUEDAD_TEMPORALES = 24 * 3600
_SIN_VENTANA = 0x08000000  # CREATE_NO_WINDOW


@dataclass
class Categoria:
    clave: str
    nombre: str
    descripcion: str
    bytes: int
    marcada: bool = True
    rutas: list[Path] = field(default_factory=list, repr=False)


def tamano_legible(cantidad: int) -> str:
    for unidad, factor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if cantidad >= factor:
            texto = f"{cantidad / factor:.1f}".rstrip("0").rstrip(".")
            return texto.replace(".", ",") + f" {unidad}"
    return f"{cantidad} B"


# --- Carpetas -------------------------------------------------------------------

def _archivos(carpeta: Path, antiguedad: float | None):
    """(ruta, tamaño) de los archivos de ``carpeta`` (sin seguir enlaces)."""
    # os.scandir: en Windows el tamaño y la fecha vienen con el listado de la carpeta,
    # sin abrir cada archivo (con os.walk + lstat, %TEMP% tardaba ~45 s en frío).
    limite = time.time() - antiguedad if antiguedad else None
    pendientes = [str(carpeta)]
    while pendientes:
        try:
            entradas = os.scandir(pendientes.pop())
        except OSError:
            continue
        with entradas:
            for entrada in entradas:
                try:
                    if entrada.is_symlink() or getattr(entrada, "is_junction", lambda: False)():
                        continue
                    if entrada.is_dir(follow_symlinks=False):
                        pendientes.append(entrada.path)
                        continue
                    info = entrada.stat(follow_symlinks=False)
                except OSError:
                    continue
                if limite is not None and info.st_mtime > limite:
                    continue
                yield entrada.path, info.st_size


def medir(carpetas: list[Path], antiguedad: float | None = None) -> int:
    return sum(t for c in carpetas if c.is_dir() for _r, t in _archivos(c, antiguedad))


def borrar(carpetas: list[Path], antiguedad: float | None = None) -> int:
    """Borra los archivos (los bloqueados se saltan) y las carpetas que queden vacías."""
    liberado = 0
    for carpeta in carpetas:
        if not carpeta.is_dir():
            continue
        for ruta, tamano in list(_archivos(carpeta, antiguedad)):
            try:
                os.remove(ruta)
                liberado += tamano
            except OSError:
                continue
        for raiz, dirs, _archivos_ in os.walk(carpeta, topdown=False):
            for d in dirs:
                try:
                    os.rmdir(os.path.join(raiz, d))  # solo si está vacía
                except OSError:
                    pass
    return liberado


def carpetas_brave() -> list[Path]:
    datos = Path(os.environ.get("LOCALAPPDATA", "")) / "BraveSoftware" / "Brave-Browser" / "User Data"
    if not datos.is_dir():
        return []
    carpetas = [datos / "ShaderCache", datos / "GrShaderCache"]
    for perfil in datos.iterdir():
        if perfil.is_dir() and (perfil.name == "Default" or perfil.name.startswith("Profile ")):
            carpetas += [perfil / "Cache" / "Cache_Data", perfil / "Code Cache", perfil / "GPUCache"]
    return [c for c in carpetas if c.is_dir()]


# --- Papelera -----------------------------------------------------------------------

class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_int64), ("i64NumItems", ctypes.c_int64)]


def tamano_papelera() -> int:
    if os.name != "nt":
        return 0
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
        return 0
    return int(info.i64Size)


def vaciar_papelera() -> int:
    antes = tamano_papelera()
    # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    resultado = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
    if resultado not in (0, -2147418113):  # S_OK o E_UNEXPECTED (ya estaba vacía)
        raise OSError(f"Windows no vació la papelera (código {resultado}).")
    return antes


# --- Docker -------------------------------------------------------------------------

_UNIDADES = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}


def bytes_docker(texto: str) -> int:
    """«1.234GB», «850.2MB (12%)» → bytes (Docker usa unidades decimales)."""
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?B)", texto, re.I)
    return int(float(m.group(1)) * _UNIDADES[m.group(2).upper()]) if m else 0


def _docker(*args: str, timeout: int = 60) -> str:
    salida = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout,
        creationflags=_SIN_VENTANA if os.name == "nt" else 0,
    )
    if salida.returncode != 0:
        raise OSError(salida.stderr.strip() or "docker falló")
    return salida.stdout


def tamano_docker() -> int:
    total = sum(bytes_docker(l) for l in _docker("images", "-f", "dangling=true", "--format", "{{.Size}}").splitlines())
    for linea in _docker("system", "df", "--format", "{{.Type}}|{{.Reclaimable}}").splitlines():
        tipo, _, reclamable = linea.partition("|")
        if tipo.strip().lower() == "build cache":
            total += bytes_docker(reclamable)
    return total


def limpiar_docker() -> int:
    antes = tamano_docker()
    _docker("image", "prune", "-f", timeout=300)  # solo imágenes sin etiqueta
    _docker("builder", "prune", "-f", timeout=300)
    return max(0, antes - tamano_docker())


# --- Ollama -------------------------------------------------------------------------

def _ollama() -> str:
    return core.LLM_URL.rsplit("/", 1)[0]


def modelos_en_uso() -> set[str]:
    import jarvis_embeddings

    return {core.MODEL_LLM, core.MODEL_VISION, jarvis_embeddings.MODELO}


def _mismo_modelo(a: str, b: str) -> bool:
    norma = lambda n: n if ":" in n else n + ":latest"  # noqa: E731
    return norma(a) == norma(b)


def modelos_sin_usar() -> list[tuple[str, int]]:
    datos = requests.get(_ollama() + "/tags", timeout=10).json()
    en_uso = modelos_en_uso()
    return [
        (m["name"], int(m.get("size", 0)))
        for m in datos.get("models", [])
        if not any(_mismo_modelo(m["name"], u) for u in en_uso)
    ]


def borrar_modelo(nombre: str) -> None:
    if any(_mismo_modelo(nombre, u) for u in modelos_en_uso()):
        raise ValueError(f"{nombre} lo usa Jarvis; no se borra.")
    respuesta = requests.delete(_ollama() + "/delete", json={"model": nombre}, timeout=60)
    respuesta.raise_for_status()


# --- Análisis y limpieza --------------------------------------------------------------

def _carpetas_temporales() -> list[Path]:
    return [Path(os.environ.get("TEMP", ""))] if os.environ.get("TEMP") else []


def _carpetas_temporales_windows() -> list[Path]:
    return [Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp"]


def analizar() -> list[Categoria]:
    """Mide cada categoría (puede tardar unos segundos). Solo devuelve las que ocupan algo."""
    categorias: list[Categoria] = []

    def agregar(clave, nombre, descripcion, medidor, marcada=True):
        try:
            tamano = medidor()
        except Exception as error:
            logging.info("Limpieza: no se pudo medir %s (%s).", clave, error)
            return
        if tamano > 0:
            categorias.append(Categoria(clave, nombre, descripcion, tamano, marcada))

    agregar("temporales", "Archivos temporales", "De más de un día, en tu carpeta temporal.",
            lambda: medir(_carpetas_temporales(), ANTIGUEDAD_TEMPORALES))
    agregar("temporales_windows", "Temporales de Windows", "De más de un día, en Windows\\Temp.",
            lambda: medir(_carpetas_temporales_windows(), ANTIGUEDAD_TEMPORALES))
    agregar("papelera", "Papelera de reciclaje", "Se vacía para siempre.", tamano_papelera)
    agregar("cache_brave", "Caché de Brave", "Páginas, código y sombreadores; se regenera sola.",
            lambda: medir(carpetas_brave()))
    agregar("docker", "Docker sin usar", "Imágenes sin etiqueta y caché de compilación.", tamano_docker)
    try:
        for nombre, tamano in modelos_sin_usar():
            if tamano > 0:
                categorias.append(Categoria(
                    f"ollama:{nombre}", f"Modelo {nombre}", "Jarvis no lo usa; habría que volver a descargarlo.",
                    tamano, marcada=False,
                ))
    except Exception as error:
        logging.info("Limpieza: no se pudieron listar los modelos de Ollama (%s).", error)
    return categorias


def limpiar(claves: list[str]) -> list[tuple[str, int, str | None]]:
    """(clave, bytes liberados, error) de cada categoría pedida."""
    resultados = []
    for clave in claves:
        try:
            if clave == "temporales":
                liberado = borrar(_carpetas_temporales(), ANTIGUEDAD_TEMPORALES)
            elif clave == "temporales_windows":
                liberado = borrar(_carpetas_temporales_windows(), ANTIGUEDAD_TEMPORALES)
            elif clave == "papelera":
                liberado = vaciar_papelera()
            elif clave == "cache_brave":
                liberado = borrar(carpetas_brave())
            elif clave == "docker":
                liberado = limpiar_docker()
            elif clave.startswith("ollama:"):
                nombre = clave.split(":", 1)[1]
                tamano = dict(modelos_sin_usar()).get(nombre, 0)
                borrar_modelo(nombre)
                liberado = tamano
            else:
                raise ValueError(f"Categoría desconocida: {clave}")
            resultados.append((clave, liberado, None))
        except Exception as error:
            logging.warning("Limpieza de %s falló: %s", clave, error)
            resultados.append((clave, 0, str(error)))
    logging.info("Limpieza terminada: %s categorías, %s liberados.", len(claves),
                 tamano_legible(sum(r[1] for r in resultados)))
    return resultados


NOMBRES = {
    "temporales": "archivos temporales", "temporales_windows": "temporales de Windows",
    "papelera": "papelera", "cache_brave": "caché de Brave", "docker": "Docker",
}


def describir_resultado(resultados: list[tuple[str, int, str | None]]) -> str:
    total = sum(r[1] for r in resultados)
    partes = []
    for clave, liberado, error in resultados:
        nombre = NOMBRES.get(clave, clave.replace("ollama:", "modelo "))
        partes.append(f"- {nombre}: " + (f"no se pudo ({error})" if error else tamano_legible(liberado)))
    return f"Liberé **{tamano_legible(total)}**.\n" + "\n".join(partes)


def disco_libre() -> int:
    return shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\").free
