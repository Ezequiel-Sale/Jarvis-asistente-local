"""Programas con winget: buscar, instalar, desinstalar y actualizar por voz.

Solo se usa el origen «winget» (el repositorio comunitario de Microsoft), nunca «msstore»: ese
pide aceptar los términos de la Tienda. Instalar acepta la licencia del programa
(``--accept-package-agreements``), por eso instalar, desinstalar y actualizar son de riesgo alto
y la tarjeta lo avisa. winget llegó a esta PC el 2026-11-19 (App Installer 1.29.380; Windows LTSC
no lo trae).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

TIEMPO_BUSCAR = 90
TIEMPO_INSTALAR = 900
ORIGEN = ["--source", "winget"]
COMUNES = ["--disable-interactivity"]


@dataclass(frozen=True)
class Paquete:
    nombre: str
    id: str
    version: str = ""
    disponible: str = ""  # en «upgrade»: la versión nueva
    coincidencia: str = ""  # «Moniker: vlc», «Tag: video»…


def ruta_winget() -> str:
    encontrado = shutil.which("winget")
    if encontrado:
        return encontrado
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "winget.exe"
    if local.exists():
        return str(local)
    raise FileNotFoundError("winget no está instalado en esta PC.")


def _correr(argumentos: list[str], tiempo: int) -> tuple[int, str]:
    salida = subprocess.run(
        [ruta_winget(), *argumentos], capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=tiempo, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    return salida.returncode, limpiar(salida.stdout or "")


def limpiar(texto: str) -> str:
    """Sin barras de progreso: de cada línea queda lo que hay después del último retorno de carro."""
    lineas = []
    for linea in texto.replace("\r\n", "\n").split("\n"):
        linea = linea.split("\r")[-1].rstrip()
        if linea.strip() in {"-", "\\", "|", "/"} or re.fullmatch(r"\s*[█▒░\s]+.*\d+\s*(?:%|[KMG]?B\s*/.*)", linea):
            continue
        lineas.append(linea)
    return "\n".join(lineas)


def tabla(texto: str) -> list[dict[str, str]]:
    """Las filas de una tabla de winget, por la posición de las columnas del encabezado."""
    lineas = texto.split("\n")
    for i, linea in enumerate(lineas):
        if i and re.fullmatch(r"-{10,}", linea.strip()):
            encabezado = lineas[i - 1]
            break
    else:
        return []
    columnas = [(m.group(), m.start()) for m in re.finditer(r"\S+", encabezado)]
    filas = []
    for linea in lineas[i + 1:]:
        if not linea.strip() or re.fullmatch(r"-{10,}", linea.strip()) or re.match(r"^\d+ (?:actualizaci|upgrade|paquete)", linea):
            continue
        fila = {}
        for j, (nombre, inicio) in enumerate(columnas):
            fin = columnas[j + 1][1] if j + 1 < len(columnas) else None
            fila[_clave_columna(nombre)] = linea[inicio:fin].strip() if len(linea) > inicio else ""
        filas.append(fila)
    return filas


def _clave_columna(nombre: str) -> str:
    normal = _normal(nombre)
    return {"nombre": "nombre", "name": "nombre", "id": "id", "version": "version", "disponible": "disponible",
            "available": "disponible", "origen": "origen", "source": "origen", "coincidencia": "coincidencia",
            "match": "coincidencia"}.get(normal, normal)


def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    return " ".join(re.sub(r"[^\w\s.]", " ", "".join(c for c in texto if not unicodedata.combining(c))).split())


def _paquetes(filas: list[dict[str, str]]) -> list[Paquete]:
    return [Paquete(f.get("nombre", ""), f.get("id", ""), f.get("version", ""), f.get("disponible", ""),
                    f.get("coincidencia", "")) for f in filas if f.get("id")]


def buscar(query: str) -> list[Paquete]:
    _codigo, texto = _correr(["search", "--query", query, *ORIGEN, *COMUNES], TIEMPO_BUSCAR)
    return _paquetes(tabla(texto))


def elegir(paquetes: list[Paquete], nombre: str) -> Paquete:
    """El paquete que se pidió, o LookupError si no hay o es ambiguo (con las opciones)."""
    clave = _normal(nombre)
    if not paquetes:
        raise LookupError(f"No encontré «{nombre}» en winget.")
    exactos = [p for p in paquetes if _normal(p.id) == clave or _normal(p.nombre) == clave
               or _normal(p.coincidencia) == f"moniker {clave}"]
    if len(exactos) == 1:
        return exactos[0]
    empiezan = [p for p in paquetes if _normal(p.nombre).startswith(clave)]
    estables = [p for p in empiezan if not _PRUEBA.search(f"{p.nombre} {p.id}")]
    for grupo in (empiezan, estables):
        if len(grupo) == 1:
            return grupo[0]
    if len(paquetes) == 1:
        return paquetes[0]
    opciones = "; ".join(f"{p.nombre} ({p.id})" for p in (exactos or empiezan or paquetes)[:4])
    raise LookupError(f"Hay varios parecidos a «{nombre}»: {opciones}. Decime cuál, por ejemplo con su Id.")


_PRUEBA = re.compile(r"\b(?:nightly|beta|preview|insider|dev|canary|alpha|rc|portable)\b", re.IGNORECASE)


def buscar_programas(query: str) -> str:
    """Handler de ``search_programs``."""
    paquetes = buscar(query)
    if not paquetes:
        return f"No encontré «{query}» en winget."
    return "En winget encontré: " + "; ".join(f"{p.nombre} ({p.id}, versión {p.version or '?'})" for p in paquetes[:6]) + "."


def instalar(name: str) -> str:
    """Handler de ``install_program``."""
    paquete = elegir(buscar(name), name)
    codigo, texto = _correr(["install", "--id", paquete.id, "--exact", *ORIGEN, "--silent",
                             "--accept-package-agreements", *COMUNES], TIEMPO_INSTALAR)
    if codigo == 0 or "instalado correctamente" in texto.lower() or "successfully installed" in texto.lower():
        return f"Instalé {paquete.nombre} ({paquete.version or 'última versión'})."
    if "ya está instalado" in texto.lower() or "already installed" in texto.lower():
        return f"{paquete.nombre} ya estaba instalado."
    raise RuntimeError(f"winget no pudo instalar {paquete.nombre} (código {codigo}). {_ultima_linea(texto)}")


def instalados(name: str) -> list[Paquete]:
    _codigo, texto = _correr(["list", "--query", name, *COMUNES], TIEMPO_BUSCAR)
    return _paquetes(tabla(texto))


def desinstalar(name: str) -> str:
    """Handler de ``uninstall_program``."""
    paquete = elegir(instalados(name), name)
    codigo, texto = _correr(["uninstall", "--id", paquete.id, "--exact", "--silent", *COMUNES], TIEMPO_INSTALAR)
    if codigo == 0 or "desinstalado correctamente" in texto.lower() or "successfully uninstalled" in texto.lower():
        return f"Desinstalé {paquete.nombre}."
    raise RuntimeError(f"winget no pudo desinstalar {paquete.nombre} (código {codigo}). {_ultima_linea(texto)}")


def actualizaciones() -> list[Paquete]:
    _codigo, texto = _correr(["upgrade", *ORIGEN, *COMUNES], TIEMPO_BUSCAR)
    return [p for p in _paquetes(tabla(texto)) if p.disponible]


def listar_actualizaciones() -> str:
    """Handler de ``list_program_updates``."""
    paquetes = actualizaciones()
    if not paquetes:
        return "Tus programas de winget están al día."
    return (f"Hay {len(paquetes)} con actualización: "
            + "; ".join(f"{p.nombre} ({p.version} → {p.disponible})" for p in paquetes[:12]) + ".")


def actualizar(name: str = "all") -> str:
    """Handler de ``update_programs``: un programa o «all»."""
    if _normal(name) in {"all", "todo", "todos", "todos los programas"}:
        pendientes = actualizaciones()
        if not pendientes:
            return "No había nada para actualizar."
        hechos, fallidos = [], []
        for paquete in pendientes:
            codigo, _texto = _correr(["upgrade", "--id", paquete.id, "--exact", *ORIGEN, "--silent",
                                      "--accept-package-agreements", *COMUNES], TIEMPO_INSTALAR)
            (hechos if codigo == 0 else fallidos).append(paquete.nombre)
        texto = f"Actualicé {len(hechos)}: {', '.join(hechos)}." if hechos else "No pude actualizar ninguno."
        if fallidos:
            texto += f" Fallaron: {', '.join(fallidos)} (puede que estén abiertos)."
        return texto
    paquete = elegir(actualizaciones() or instalados(name), name)
    codigo, texto = _correr(["upgrade", "--id", paquete.id, "--exact", *ORIGEN, "--silent",
                             "--accept-package-agreements", *COMUNES], TIEMPO_INSTALAR)
    if codigo == 0:
        return f"Actualicé {paquete.nombre}" + (f" a la {paquete.disponible}." if paquete.disponible else ".")
    if "no se encontr" in texto.lower() or "no applicable" in texto.lower() or "no hay" in texto.lower():
        return f"{paquete.nombre} ya está en la última versión."
    raise RuntimeError(f"winget no pudo actualizar {paquete.nombre} (código {codigo}). {_ultima_linea(texto)}")


def _ultima_linea(texto: str) -> str:
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    return lineas[-1][:200] if lineas else ""


# --- Frases directas ----------------------------------------------------------------------

_PREFIJO = r"^(?:(?:hey|oye)\s+)?(?:jarvis\s+)?(?:por\s+favor\s+)?"
_NO_ES_PROGRAMA = re.compile(r"\b(?:actualizaciones|windows\s+update|drivers?|controladores?|juego|mod)\b")
_INSTALAR = re.compile(_PREFIJO + r"(?:instala|instalame|baja\w*\s+e\s+instala)\w*\s+(?:el\s+programa\s+|la\s+app\s+)?(?P<n>.+)$")
_DESINSTALAR = re.compile(_PREFIJO + r"(?:desinstala\w*(?:\s+el\s+programa|\s+la\s+app)?|elimina\w*\s+el\s+programa|"
                          r"borra\w*\s+el\s+programa)\s+(?P<n>.+)$")
_LISTA = re.compile(_PREFIJO + r"(?:que|cuales)\s+programas\s+(?:tienen|hay\s+para\s+actualizar|necesitan)"
                    r"(?:\s+(?:actualizacion\w*|actualizar\w*))?$|" + _PREFIJO.lstrip("^") +
                    r"hay\s+actualizaciones\s+de\s+(?:los\s+)?programas$")
_TODO = re.compile(_PREFIJO + r"actualiza\w*\s+(?:todos\s+los\s+programas|todo\s+lo\s+de\s+winget|todos\s+mis\s+programas)$")
_UNO = re.compile(_PREFIJO + r"actualiza\w*\s+(?:el\s+programa\s+)?(?P<n>.+)$")


def detectar(texto: str) -> tuple[str, dict[str, object]] | None:
    consulta = _normal(texto or "").rstrip(".")
    if _LISTA.match(consulta):
        return ("list_program_updates", {})
    if _TODO.match(consulta):
        return ("update_programs", {"name": "all"})
    for patron, herramienta in ((_DESINSTALAR, "uninstall_program"), (_INSTALAR, "install_program")):
        if (m := patron.match(consulta)) is not None and not _NO_ES_PROGRAMA.search(m.group("n")):
            return (herramienta, {"name": m.group("n")[:80]})
    if (m := _UNO.match(consulta)) is not None and not _NO_ES_PROGRAMA.search(m.group("n")) \
            and not re.match(r"(?:la|el)\s+(?:pagina|pestana|lista|tele)", m.group("n")):
        return ("update_programs", {"name": m.group("n")[:80]})
    return None
