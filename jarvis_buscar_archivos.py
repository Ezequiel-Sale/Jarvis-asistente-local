"""Buscar y abrir archivos por voz: «abre el PDF de la factura de la luz», «busca las fotos de ayer».

Recorre las carpetas personales (Escritorio, Documentos, Descargas, Imágenes, Vídeos, Música
y OneDrive) buscando las palabras en el nombre (sin tildes ni mayúsculas). Medido el
2026-11-13: 0,2–1,3 s; el índice de Windows (ADO, SystemIndex) tardaba ~4 s buscando también
en el contenido y fallaba en las consultas solo por tipo, así que se descartó. Solo lectura;
abrir un archivo es otra herramienta (``open_file``) que nunca abre programas ni scripts.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

TIPOS: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "document": (".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pdf"),
    "spreadsheet": (".xls", ".xlsx", ".xlsm", ".csv", ".ods"),
    "presentation": (".ppt", ".pptx", ".odp"),
    "image": (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".tif", ".tiff"),
    "video": (".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv"),
    "audio": (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"),
    "any": (),
}
# Nunca se abren: programas, scripts y accesos directos (abrir un archivo no ejecuta nada).
NO_ABRIR = frozenset({
    ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".msp", ".ps1", ".psm1", ".vbs", ".vbe", ".js",
    ".jse", ".wsf", ".wsh", ".hta", ".jar", ".lnk", ".url", ".reg", ".cpl", ".dll", ".sys", ".pif",
    ".appref-ms", ".msc", ".inf",
})
MAX_RESULTADOS = 5
MAX_ARCHIVOS_RECORRIDO = 60_000
_VACIAS = frozenset({"de", "del", "la", "el", "los", "las", "un", "una", "mi", "mis", "con", "que", "y",
                     "en", "por", "para", "a", "al", "se", "llama", "llamado", "llamada", "sobre"})


@dataclass(frozen=True, slots=True)
class Encontrado:
    ruta: str
    modificado: float  # marca de tiempo
    tamano: int = 0

    @property
    def nombre(self) -> str:
        return Path(self.ruta).name


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in texto if not unicodedata.combining(c))


def palabras_de(consulta: str) -> list[str]:
    return [p for p in re.split(r"[^\w]+", normalizar(consulta)) if len(p) >= 2 and p not in _VACIAS]


def puntuar(nombre: str, palabras: list[str]) -> int:
    """Cuántas palabras buscadas aparecen en el nombre (sin tildes ni mayúsculas)."""
    nombre = normalizar(nombre)
    return sum(1 for p in palabras if p in nombre)


def carpetas_personales() -> list[Path]:
    import jarvis_archivos

    raices: list[Path] = []
    for clave in ("documentos", "descargas"):
        try:
            raices.append(jarvis_archivos.carpeta(clave))
        except Exception:
            pass
    inicio = Path.home()
    for nombre in ("Desktop", "Escritorio", "Pictures", "Videos", "Music", "OneDrive"):
        ruta = inicio / nombre
        if ruta.is_dir() and ruta not in raices:
            raices.append(ruta)
    return raices


def _ordenar(encontrados: Iterable[Encontrado], palabras: list[str]) -> list[Encontrado]:
    return sorted(encontrados, key=lambda e: (puntuar(e.nombre, palabras), e.modificado), reverse=True)


def buscar_en_carpetas(
    palabras: list[str], extensiones: tuple[str, ...], desde: float | None,
    raices: Iterable[Path], *, recorrer: Callable = os.walk, limite: int = MAX_ARCHIVOS_RECORRIDO,
) -> list[Encontrado]:
    """Recorre las carpetas y devuelve los que tienen todas las palabras en el nombre."""
    resultados: list[Encontrado] = []
    vistos = 0
    for raiz in raices:
        for carpeta, subcarpetas, archivos in recorrer(raiz):
            subcarpetas[:] = [s for s in subcarpetas if not s.startswith((".", "$")) and s.casefold() not in
                              ("node_modules", "appdata", "__pycache__")]
            for archivo in archivos:
                vistos += 1
                if vistos > limite:
                    return _ordenar(resultados, palabras)
                if extensiones and not archivo.casefold().endswith(extensiones):
                    continue
                if palabras and puntuar(archivo, palabras) < len(palabras):
                    continue
                ruta = os.path.join(carpeta, archivo)
                try:
                    estado = os.stat(ruta)
                except OSError:
                    continue
                if desde is not None and estado.st_mtime < desde:
                    continue
                resultados.append(Encontrado(ruta, estado.st_mtime, estado.st_size))
    return _ordenar(resultados, palabras)


def buscar(consulta: str, tipo: str = "any", dias: int = 0, *, ahora: float | None = None) -> list[Encontrado]:
    palabras = palabras_de(consulta)
    extensiones = TIPOS.get(tipo, ())
    desde = (ahora or time.time()) - dias * 86400 if dias > 0 else None
    return buscar_en_carpetas(palabras, extensiones, desde, carpetas_personales())[:MAX_RESULTADOS]


def describir(encontrados: list[Encontrado], ahora: datetime | None = None) -> str:
    """Lista numerada para el chat («1. factura_luz.pdf — Descargas, ayer»)."""
    ahora = ahora or datetime.now()
    lineas = []
    for numero, e in enumerate(encontrados, 1):
        fecha = datetime.fromtimestamp(e.modificado)
        dias = (ahora.date() - fecha.date()).days
        cuando = "hoy" if dias == 0 else "ayer" if dias == 1 else f"{fecha:%d/%m/%Y}"
        lineas.append(f"{numero}. {e.nombre} — {Path(e.ruta).parent.name}, {cuando}")
    return "\n".join(lineas)


def es_abrible(ruta: str) -> bool:
    return Path(ruta).suffix.casefold() not in NO_ABRIR


def abrir(ruta: str) -> None:
    """Abre con el programa predeterminado. Nunca programas, scripts ni accesos directos."""
    archivo = Path(ruta)
    if not archivo.is_file():
        raise ValueError("Ese archivo ya no existe.")
    if not es_abrible(ruta):
        raise ValueError("Por seguridad no abro programas, scripts ni accesos directos.")
    os.startfile(str(archivo))  # noqa: S606 - abre con la app predeterminada de Windows


# --- Frases -----------------------------------------------------------------------

_TIPO_PALABRA = {
    "pdf": "pdf", "pdfs": "pdf",
    "documento": "document", "documentos": "document", "word": "document", "texto": "document",
    "excel": "spreadsheet", "excels": "spreadsheet", "planilla": "spreadsheet", "planillas": "spreadsheet",
    "hoja de calculo": "spreadsheet",
    "presentacion": "presentation", "presentaciones": "presentation", "powerpoint": "presentation",
    "foto": "image", "fotos": "image", "imagen": "image", "imagenes": "image", "captura": "image",
    "capturas": "image",
    "video": "video", "videos": "video",
    "cancion": "audio", "canciones": "audio", "audio": "audio", "audios": "audio",
    "archivo": "any", "archivos": "any", "fichero": "any", "ficheros": "any",
}
_TIPOS_RE = "|".join(sorted((re.escape(k) for k in _TIPO_PALABRA), key=len, reverse=True))
_CUANDO = {
    "de hoy": 1, "hoy": 1, "de ayer": 2, "ayer": 2, "que baje ayer": 2, "que baje hoy": 1,
    "de esta semana": 7, "de la semana": 7, "recientes": 7, "reciente": 7, "de este mes": 31, "del mes": 31,
}
_CUANDO_RE = "|".join(sorted((re.escape(k) for k in _CUANDO), key=len, reverse=True))
_FRASE = re.compile(
    r"^(?:(?:hey|oye)\s+)?(?:jarvis\s+)?(?:por\s+favor\s+)?"
    r"(?P<verbo>busca(?:me)?|buscar|encuentra(?:me)?|abre(?:me)?|abrir|donde\s+(?:esta|estan|quedo|quedaron|"
    r"guarde|tengo|deje))\s+"
    r"(?:el|la|los|las|un|una|mi|mis)?\s*"
    rf"(?P<tipo>{_TIPOS_RE})"
    r"(?:\s+(?:de|del|con|que\s+se\s+llama|llamad[oa]|sobre|que\s+dice|de\s+la|de\s+los))?"
    r"(?:\s+(?P<texto>.+?))?"
    rf"(?:\s+(?P<cuando>{_CUANDO_RE}))?$"
)


def detectar(texto: str) -> tuple[str, dict[str, object]] | None:
    """«Abre el PDF de la factura de la luz» → find_files(query, kind, open_best=True)."""
    consulta = " ".join(re.sub(r"[^\w\s]", " ", normalizar(texto)).split())
    coincidencia = _FRASE.fullmatch(consulta)
    if coincidencia is None:
        return None
    tipo = _TIPO_PALABRA[coincidencia.group("tipo")]
    buscado = (coincidencia.group("texto") or "").strip()
    cuando = coincidencia.group("cuando")
    if cuando is None and buscado in _CUANDO:  # «las fotos de ayer»
        cuando, buscado = buscado, ""
    if not buscado and cuando is None and tipo == "any":
        return None  # «abre el archivo»: demasiado vago
    if tipo == "video" and re.search(r"\b(?:monitor|pantalla|youtube)\b", buscado):
        return None  # «abre el vídeo del monitor 2» no es un archivo
    argumentos: dict[str, object] = {"query": buscado, "kind": tipo}
    if cuando:
        argumentos["days"] = _CUANDO[cuando]
    if coincidencia.group("verbo").startswith("abr"):
        argumentos["open_best"] = True
    return "find_files", argumentos


_ORDINALES = {"primero": 1, "primer": 1, "1": 1, "uno": 1, "segundo": 2, "2": 2, "dos": 2, "tercero": 3,
              "tercer": 3, "3": 3, "tres": 3, "cuarto": 4, "4": 4, "cuatro": 4, "quinto": 5, "5": 5, "cinco": 5}
_ORDINAL = re.compile(
    r"^(?:(?:hey|oye)\s+)?(?:jarvis\s+)?abre(?:me)?\s+(?:el\s+)?(?:numero\s+)?"
    r"(?P<n>primero|primer|segundo|tercero|tercer|cuarto|quinto|uno|dos|tres|cuatro|cinco|[1-5])"
    r"(?:\s+(?:archivo|resultado|de\s+la\s+lista))?$"
)


def detectar_orden(texto: str) -> int | None:
    """«Abre el segundo» tras una búsqueda → 2."""
    consulta = " ".join(re.sub(r"[^\w\s]", " ", normalizar(texto)).split())
    coincidencia = _ORDINAL.fullmatch(consulta)
    return _ORDINALES[coincidencia.group("n")] if coincidencia else None


def elegir_para_abrir(encontrados: list[Encontrado], palabras: list[str]) -> Encontrado | None:
    """El mejor resultado si es claro: el único, o el que tiene más palabras que el segundo."""
    if not encontrados:
        return None
    if len(encontrados) == 1:
        return encontrados[0]
    primero, segundo = (puntuar(e.nombre, palabras) for e in encontrados[:2])
    return encontrados[0] if primero > segundo else None
