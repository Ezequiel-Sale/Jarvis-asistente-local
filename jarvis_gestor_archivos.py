"""Gestión de archivos por voz: mover, renombrar, copiar, comprimir, descomprimir y mandar a la
papelera (con «deshacé»), «ordená Descargas» e historial del portapapeles.

Solo dentro de las carpetas personales (Escritorio, Documentos, Descargas, Imágenes, Vídeos,
Música, OneDrive) y los otros discos; nunca Windows, Archivos de programa ni la raíz de un disco.
Nada se borra para siempre: lo que se elimina va a la papelera de reciclaje, de donde «deshacé»
lo devuelve. Cada acción que cambia algo avisa a ``anotar_deshacer`` (lo conecta la app).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import zipfile
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jarvis_archivos

ACCIONES = ("move", "rename", "copy", "zip", "unzip", "trash", "mkdir")
MAX_DESCOMPRIMIR = 8 * 1024**3  # 8 GB: más que eso suele ser un error o una bomba zip

# La app lo reemplaza por una función que anota en su historial de «deshacé».
anotar_deshacer: Callable[[str, Callable[[], object]], None] = lambda _descripcion, _funcion: None


class ErrorArchivo(ValueError):
    pass


# --- Dónde se puede tocar -------------------------------------------------------------------

def raices_permitidas() -> list[Path]:
    import jarvis_buscar_archivos

    raices = [r.resolve() for r in jarvis_buscar_archivos.carpetas_personales()]
    try:
        for disco in jarvis_archivos._discos_extra():
            raices.append(Path(f"{disco.unidad.rstrip(':')}:/").resolve())
    except Exception:
        logging.debug("No se pudieron leer los otros discos.", exc_info=True)
    return raices


def permitido(ruta: str | Path, *, raices: list[Path] | None = None, existe: bool = True) -> Path:
    """La ruta real si está dentro de una carpeta permitida (y no es la carpeta misma)."""
    texto = str(ruta or "").strip().strip('"')
    if not texto:
        raise ErrorArchivo("Falta la ruta.")
    real = Path(os.path.expandvars(os.path.expanduser(texto))).resolve()
    raices = raices if raices is not None else raices_permitidas()
    if real in raices:
        raise ErrorArchivo(f"«{real}» es una de tus carpetas principales: no la muevo, renombro ni borro.")
    if not any(r in real.parents for r in raices):
        raise ErrorArchivo(f"«{real}» está fuera de las carpetas que puedo tocar (las personales y los otros discos).")
    if existe and not real.exists():
        raise ErrorArchivo(f"No existe «{real}».")
    return real


def _carpeta_destino(destino: str | Path, raices: list[Path] | None = None) -> Path:
    """Destino: una ruta permitida o el nombre de una carpeta personal («Documentos», «Escritorio»)."""
    atajos = {"descargas": "descargas", "downloads": "descargas", "documentos": "documentos", "documents": "documentos"}
    clave = _normal(str(destino))
    if clave in atajos:
        return jarvis_archivos.carpeta(atajos[clave])
    for nombre in ("Desktop", "Escritorio", "Pictures", "Videos", "Music"):
        if clave in {_normal(nombre), {"Desktop": "escritorio", "Pictures": "imagenes", "Videos": "videos",
                                        "Music": "musica"}.get(nombre, "")} and (Path.home() / nombre).is_dir():
            return Path.home() / nombre
    carpeta = permitido(destino, raices=raices, existe=False)
    if carpeta.exists() and not carpeta.is_dir():
        raise ErrorArchivo(f"«{carpeta.name}» no es una carpeta.")
    return carpeta


def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    return " ".join(re.sub(r"[^\w\s]", " ", "".join(c for c in texto if not unicodedata.combining(c))).split())


# --- Papelera de reciclaje ------------------------------------------------------------------

def _a_la_papelera(ruta: Path) -> None:
    """SHFileOperation con FOF_ALLOWUNDO: a la papelera, sin diálogos."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    operacion = SHFILEOPSTRUCTW(None, FO_DELETE, str(ruta) + "\0", None,
                                FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, False, None, None)
    codigo = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operacion))
    if codigo or operacion.fAnyOperationsAborted:
        raise OSError(f"Windows no pudo mandar «{ruta.name}» a la papelera (código {codigo}).")


_SCRIPT_RESTAURAR = r"""
$ErrorActionPreference = 'Stop'
$papelera = (New-Object -ComObject Shell.Application).Namespace(10)
$buscada = $env:JARVIS_RUTA
foreach ($item in $papelera.Items()) {
    $origen = $item.ExtendedProperty('System.Recycle.DeletedFrom')
    $nombre = [System.IO.Path]::GetFileName($item.Path)
    $original = $item.ExtendedProperty('System.ItemNameDisplay')
    if ($origen -and ((Join-Path $origen $original) -eq $buscada -or (Join-Path $origen $nombre) -eq $buscada)) {
        $item.InvokeVerb('undelete'); Write-Output 'OK'; exit 0
    }
}
Write-Output 'NO'
"""


def restaurar_de_papelera(ruta: Path) -> str:
    comando = base64.b64encode(_SCRIPT_RESTAURAR.encode("utf-16-le")).decode("ascii")
    salida = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", comando],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        env={**os.environ, "JARVIS_RUTA": str(ruta)}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if "OK" not in (salida.stdout or ""):
        raise OSError(f"No encontré «{ruta.name}» en la papelera: quizás ya la vaciaron.")
    return f"Devolví «{ruta.name}» a {ruta.parent}."


# --- Acciones -------------------------------------------------------------------------------

def _mover(origen: Path, destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    return Path(shutil.move(str(origen), str(destino)))


def accion(action: str, path: str, destination: str = "", new_name: str = "") -> str:
    """Handler de ``file_action``."""
    if action not in ACCIONES:
        raise ErrorArchivo(f"Acción desconocida: {action}.")
    raices = raices_permitidas()
    if action == "mkdir":
        carpeta = permitido(path, raices=raices, existe=False)
        if carpeta.exists():
            return f"La carpeta «{carpeta.name}» ya existía."
        carpeta.mkdir(parents=True)
        anotar_deshacer(f"Borrar la carpeta nueva «{carpeta.name}»", lambda: carpeta.rmdir())
        return f"Creé la carpeta «{carpeta}»."
    origen = permitido(path, raices=raices)
    if action == "rename":
        nombre = jarvis_archivos.nombre_seguro(new_name or destination)
        if not Path(nombre).suffix and origen.suffix and origen.is_file():
            nombre += origen.suffix  # «renombralo a informe» conserva la extensión
        final = jarvis_archivos.destino_libre(origen.parent, nombre)
        origen.rename(final)
        anotar_deshacer(f"Volver a llamar «{origen.name}» a «{final.name}»", lambda: final.rename(origen))
        return f"Renombré «{origen.name}» a «{final.name}»."
    if action in ("move", "copy"):
        carpeta = _carpeta_destino(destination, raices)
        final = jarvis_archivos.destino_libre(carpeta, origen.name)
        if action == "move":
            _mover(origen, final)
            anotar_deshacer(f"Devolver «{final.name}» a {origen.parent}", lambda: _mover(final, origen))
            return f"Moví «{origen.name}» a {carpeta}."
        carpeta.mkdir(parents=True, exist_ok=True)
        if origen.is_dir():
            shutil.copytree(origen, final)
        else:
            shutil.copy2(origen, final)
        anotar_deshacer(f"Mandar a la papelera la copia «{final.name}»", lambda: _a_la_papelera(final))
        return f"Copié «{origen.name}» a {carpeta}."
    if action == "zip":
        final = jarvis_archivos.destino_libre(origen.parent, origen.stem + ".zip")
        with zipfile.ZipFile(final, "w", zipfile.ZIP_DEFLATED) as archivo:
            if origen.is_dir():
                for hijo in sorted(origen.rglob("*")):
                    if hijo.is_file():
                        archivo.write(hijo, hijo.relative_to(origen.parent))
            else:
                archivo.write(origen, origen.name)
        anotar_deshacer(f"Mandar a la papelera «{final.name}»", lambda: _a_la_papelera(final))
        return f"Comprimí «{origen.name}» en «{final.name}» ({jarvis_archivos.tamano_legible(final.stat().st_size)})."
    if action == "unzip":
        if origen.suffix.lower() != ".zip":
            raise ErrorArchivo("Solo descomprimo archivos .zip.")
        carpeta = jarvis_archivos.destino_libre(origen.parent, origen.stem)
        with zipfile.ZipFile(origen) as archivo:
            if sum(i.file_size for i in archivo.infolist()) > MAX_DESCOMPRIMIR:
                raise ErrorArchivo("El zip descomprimido pasaría los 8 GB: no lo abro.")
            base = carpeta.resolve()
            for info in archivo.infolist():  # nada de «../» ni rutas absolutas dentro del zip
                destino = (base / info.filename).resolve()
                if destino != base and base not in destino.parents:
                    raise ErrorArchivo("El zip intenta escribir fuera de su carpeta: no lo abro.")
            archivo.extractall(carpeta)
        anotar_deshacer(f"Mandar a la papelera la carpeta «{carpeta.name}»", lambda: _a_la_papelera(carpeta))
        return f"Descomprimí «{origen.name}» en «{carpeta.name}»."
    # trash
    _a_la_papelera(origen)
    anotar_deshacer(f"Sacar «{origen.name}» de la papelera", lambda: restaurar_de_papelera(origen))
    return f"Mandé «{origen.name}» a la papelera (si te arrepentís, decí «deshacé»)."


# --- «Ordená Descargas» ---------------------------------------------------------------------

CATEGORIAS = {
    "Imágenes": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".svg", ".ico", ".tif", ".tiff"},
    "Videos": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv", ".m4v"},
    "Música": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"},
    "Documentos": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".odt", ".ods", ".csv",
                   ".rtf", ".md", ".epub"},
    "Comprimidos": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Instaladores": {".exe", ".msi", ".msix", ".msixbundle", ".appx", ".iso"},
    "Torrents": {".torrent"},
}
INSTALADOR_VIEJO = 30 * 86400
_ultimo_plan: dict[str, object] = {}


@dataclass(frozen=True)
class Plan:
    carpeta: Path
    movimientos: tuple[tuple[Path, Path], ...]
    duplicados: tuple[tuple[Path, Path], ...]   # (copia, original)
    instaladores_viejos: tuple[Path, ...]


def categoria(archivo: Path) -> str | None:
    extension = archivo.suffix.lower()
    return next((nombre for nombre, extensiones in CATEGORIAS.items() if extension in extensiones), None)


def _huella(archivo: Path) -> str:
    resumen = hashlib.sha256()
    with archivo.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            resumen.update(bloque)
    return resumen.hexdigest()


def planear(carpeta: Path, *, ahora: float | None = None) -> Plan:
    ahora = ahora or time.time()
    archivos = sorted((a for a in carpeta.iterdir() if a.is_file() and not a.name.startswith(".")
                       and a.name.lower() not in {"desktop.ini", "thumbs.db"}
                       and not a.name.endswith((".crdownload", ".part", ".tmp"))), key=lambda a: a.name.lower())
    movimientos = []
    for archivo in archivos:
        nombre = categoria(archivo)
        if nombre is not None:
            movimientos.append((archivo, carpeta / nombre / archivo.name))
    por_tamano: dict[int, list[Path]] = {}
    for archivo in archivos:
        por_tamano.setdefault(archivo.stat().st_size, []).append(archivo)
    duplicados = []
    for grupo in por_tamano.values():
        if len(grupo) < 2 or grupo[0].stat().st_size == 0:
            continue
        vistos: dict[str, Path] = {}
        for archivo in sorted(grupo, key=lambda a: a.stat().st_mtime):
            huella = _huella(archivo)
            if huella in vistos:
                duplicados.append((archivo, vistos[huella]))
            else:
                vistos[huella] = archivo
    viejos = tuple(a for a in archivos if categoria(a) == "Instaladores" and ahora - a.stat().st_mtime > INSTALADOR_VIEJO)
    return Plan(carpeta, tuple(movimientos), tuple(duplicados), viejos)


def describir_plan(plan: Plan) -> str:
    if not plan.movimientos and not plan.duplicados and not plan.instaladores_viejos:
        return f"«{plan.carpeta.name}» ya está ordenada: no hay archivos sueltos para clasificar."
    partes = []
    if plan.movimientos:
        cuenta: dict[str, int] = {}
        for _origen, destino in plan.movimientos:
            cuenta[destino.parent.name] = cuenta.get(destino.parent.name, 0) + 1
        partes.append(f"Movería {len(plan.movimientos)} archivos a subcarpetas: "
                      + ", ".join(f"{n} a {c}" for c, n in sorted(cuenta.items(), key=lambda x: -x[1])) + ".")
    if plan.duplicados:
        ejemplos = ", ".join(f"«{c.name}» (igual a «{o.name}»)" for c, o in plan.duplicados[:3])
        partes.append(f"Hay {len(plan.duplicados)} archivos duplicados, por ejemplo {ejemplos}.")
    if plan.instaladores_viejos:
        espacio = sum(a.stat().st_size for a in plan.instaladores_viejos)
        partes.append(f"Y {len(plan.instaladores_viejos)} instaladores de hace más de un mes "
                      f"({jarvis_archivos.tamano_legible(espacio)}) que probablemente ya no necesitás.")
    partes.append("Decí «sí, ordenala» para mover los archivos; los duplicados e instaladores solo los mando "
                  "a la papelera si me lo pedís («mandá los duplicados a la papelera»).")
    return " ".join(partes)


def _carpeta_a_ordenar(folder: str) -> Path:
    claves = {"downloads": ("descargas", None), "documents": ("documentos", None), "desktop": (None, "Desktop")}
    if folder not in claves:
        raise ErrorArchivo("folder debe ser downloads, documents o desktop.")
    clave, nombre = claves[folder]
    return jarvis_archivos.carpeta(clave) if clave else Path.home() / nombre


def ordenar(folder: str = "downloads", step: str = "plan") -> str:
    """Handler de ``organize_folder``: plan (solo cuenta) | apply (mueve) | trash_duplicates | trash_installers."""
    carpeta = _carpeta_a_ordenar(folder)
    plan = planear(carpeta)
    if step == "plan":
        _ultimo_plan.update(folder=folder, momento=time.monotonic())
        return describir_plan(plan)
    if step == "apply":
        hechos: list[tuple[Path, Path]] = []
        for origen, destino in plan.movimientos:
            final = jarvis_archivos.destino_libre(destino.parent, destino.name) if destino.exists() else destino
            try:
                hechos.append((origen, _mover(origen, final)))
            except OSError:
                logging.info("No se pudo mover un archivo al ordenar (¿abierto?).")
        if hechos:
            anotar_deshacer(f"Devolver {len(hechos)} archivos a «{carpeta.name}»",
                            lambda: [_mover(final, origen) for origen, final in hechos])
        return f"Listo: ordené {len(hechos)} archivos de «{carpeta.name}» en subcarpetas."
    if step in ("trash_duplicates", "trash_installers"):
        elegidos = [c for c, _o in plan.duplicados] if step == "trash_duplicates" else list(plan.instaladores_viejos)
        enviados = []
        for archivo in elegidos:
            try:
                _a_la_papelera(archivo)
                enviados.append(archivo)
            except OSError:
                logging.info("No se pudo mandar un archivo a la papelera al ordenar.")
        if enviados:
            anotar_deshacer(f"Sacar {len(enviados)} archivos de la papelera",
                            lambda: [restaurar_de_papelera(a) for a in enviados])
        que = "duplicados" if step == "trash_duplicates" else "instaladores viejos"
        return f"Mandé {len(enviados)} {que} a la papelera." if enviados else f"No había {que}."
    raise ErrorArchivo("step debe ser plan, apply, trash_duplicates o trash_installers.")


def plan_reciente(segundos: float = 600) -> str | None:
    """La carpeta del último «ordená…» si fue hace menos de ``segundos`` (para «sí, ordenala»)."""
    momento = _ultimo_plan.get("momento")
    if isinstance(momento, float) and time.monotonic() - momento <= segundos:
        return str(_ultimo_plan.get("folder"))
    return None


# --- Historial del portapapeles (solo en memoria) --------------------------------------------

MAX_HISTORIAL = 25
VIGENCIA_PORTAPAPELES = 3 * 3600
MAX_TEXTO = 5000


@dataclass(frozen=True)
class Copiado:
    texto: str
    momento: float


class HistorialPortapapeles:
    """Guarda lo que copiás (texto) mientras Jarvis está abierto. Nunca va al disco ni al registro,
    y no anota lo copiado desde gestores de contraseñas, bancos o pagos (``jarvis_control.protegida``)."""

    def __init__(self, leer=jarvis_archivos.leer_portapapeles, secuencia=None, ventana_protegida=None,
                 reloj=time.monotonic) -> None:
        self._leer = leer
        self._secuencia = secuencia or (lambda: __import__("ctypes").windll.user32.GetClipboardSequenceNumber())
        self._protegida = ventana_protegida or _ventana_activa_protegida
        self._reloj = reloj
        self._items: deque[Copiado] = deque(maxlen=MAX_HISTORIAL)
        self._ultima_secuencia = None
        self._cerrojo = threading.Lock()
        self.ignorar_siguiente = False  # lo que Jarvis mismo pone en el portapapeles

    def revisar(self) -> None:
        secuencia = self._secuencia()
        if secuencia == self._ultima_secuencia:
            return
        self._ultima_secuencia = secuencia
        if self.ignorar_siguiente:
            self.ignorar_siguiente = False
            return
        if self._protegida():
            return
        try:
            texto = self._leer()
        except OSError:
            return
        texto = (texto or "").strip()
        if not texto or len(texto) > MAX_TEXTO:
            return
        with self._cerrojo:
            if self._items and self._items[-1].texto == texto:
                return
            self._items = deque((c for c in self._items if c.texto != texto), maxlen=MAX_HISTORIAL)
            self._items.append(Copiado(texto, self._reloj()))

    def recientes(self) -> list[Copiado]:
        """Del más nuevo (1) al más viejo, solo los de las últimas horas."""
        ahora = self._reloj()
        with self._cerrojo:
            return [c for c in reversed(self._items) if ahora - c.momento <= VIGENCIA_PORTAPAPELES]

    def vigilar(self, detenido: threading.Event, intervalo: float = 1.0) -> None:
        while not detenido.wait(intervalo):
            try:
                self.revisar()
            except Exception:
                logging.debug("No se pudo revisar el portapapeles.", exc_info=True)


def _ventana_activa_protegida() -> bool:
    try:
        import jarvis_control
        import jarvis_windows

        hwnd = jarvis_windows._user32().GetForegroundWindow()
        ventana = jarvis_control._ventana_de(hwnd)
        return ventana is not None and jarvis_control.protegida(ventana)
    except Exception:
        return False


def _vista(texto: str) -> str:
    linea = " ".join(texto.split())
    return linea[:70] + ("…" if len(linea) > 70 else "")


def usar_historial(historial: HistorialPortapapeles, action: str, number: int = 2,
                   escribir=jarvis_archivos.escribir_portapapeles, pegar=None) -> str:
    """Handler de ``clipboard_history``: list | copy | paste (number: 1 = lo último que copiaste)."""
    items = historial.recientes()
    if action == "list":
        if not items:
            return "No tengo nada copiado en las últimas horas (lo guardo solo mientras Jarvis está abierto)."
        return "Lo último que copiaste:\n" + "\n".join(f"{i}. {_vista(c.texto)}" for i, c in enumerate(items[:10], 1))
    if action not in ("copy", "paste"):
        raise ValueError("action debe ser list, copy o paste.")
    if not 1 <= number <= len(items):
        raise ValueError(f"Solo tengo {len(items)} cosas copiadas." if items else "No tengo nada copiado.")
    elegido = items[number - 1]
    historial.ignorar_siguiente = True
    escribir(elegido.texto)
    if action == "copy":
        return f"Lo dejé listo para pegar: «{_vista(elegido.texto)}»."
    (pegar or _pegar)()
    return f"Pegué «{_vista(elegido.texto)}»."


def _pegar() -> None:
    import jarvis_control

    jarvis_control.teclas("ctrl+v")


# --- Frases directas ----------------------------------------------------------------------

_PREFIJO = r"^(?:(?:hey|oye)\s+)?(?:jarvis\s+)?(?:por\s+favor\s+)?"
_ORDENAR = re.compile(_PREFIJO + r"(?:ordena|organiza|acomoda|limpia)\w*\s+(?:la\s+carpeta\s+(?:de\s+)?|el\s+|mis\s+|las\s+)?"
                      r"(?P<c>descargas|downloads|documentos|escritorio)$")
_APLICAR = re.compile(_PREFIJO + r"(?:si|dale|ok|bueno)?\s*,?\s*(?:ordenala|ordenalo|organizala|aplica\w*\s+(?:el\s+)?orden)$")
_DUPLICADOS = re.compile(_PREFIJO + r"(?:manda|tira|borra|elimina)\w*\s+(?:los\s+)?duplicados(?:\s+a\s+la\s+papelera)?$")
_INSTALADORES = re.compile(_PREFIJO + r"(?:manda|tira|borra|elimina)\w*\s+(?:los\s+)?instaladores(?:\s+viejos)?(?:\s+a\s+la\s+papelera)?$")
_LISTA_COPIADO = re.compile(_PREFIJO + r"(?:que\s+(?:copie|tengo\s+copiado)(?:\s+antes)?|"
                            r"(?:mostrame|mostra|decime|deci|lee|lei)\w*\s+(?:el\s+)?historial\s+del\s+portapapeles)$")
_PEGAR_ANTERIOR = re.compile(_PREFIJO + r"pega\w*\s+lo\s+que\s+copie\s+antes$")
_ORDINALES = {"primero": 1, "segundo": 2, "tercero": 3, "cuarto": 4, "quinto": 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
_PEGAR_N = re.compile(_PREFIJO + r"pega\w*\s+(?:el\s+)?(?P<n>primero|segundo|tercero|cuarto|quinto|[1-5])"
                      r"\s+del\s+(?:historial|portapapeles)$")
_CARPETA = {"descargas": "downloads", "downloads": "downloads", "documentos": "documents", "escritorio": "desktop"}


def detectar(texto: str) -> tuple[str, dict[str, object]] | None:
    consulta = _normal(texto or "")
    if (m := _ORDENAR.match(consulta)) is not None:
        return ("organize_folder", {"folder": _CARPETA[m.group("c")], "step": "plan"})
    carpeta = plan_reciente()
    if carpeta is not None:
        if _APLICAR.match(consulta):
            return ("organize_folder", {"folder": carpeta, "step": "apply"})
        if _DUPLICADOS.match(consulta):
            return ("organize_folder", {"folder": carpeta, "step": "trash_duplicates"})
        if _INSTALADORES.match(consulta):
            return ("organize_folder", {"folder": carpeta, "step": "trash_installers"})
    if _LISTA_COPIADO.match(consulta):
        return ("clipboard_history", {"action": "list"})
    if _PEGAR_ANTERIOR.match(consulta):
        return ("clipboard_history", {"action": "paste", "number": 2})
    if (m := _PEGAR_N.match(consulta)) is not None:
        return ("clipboard_history", {"action": "paste", "number": _ORDINALES[m.group("n")]})
    return None
