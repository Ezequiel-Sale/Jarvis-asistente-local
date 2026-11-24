"""Portadas de la página 🎮 JUEGOS (2026-11-23, pedido del usuario: tarjetas con una imagen que Jarvis
busque en internet y, si no la encuentra, un botón para subirla a mano).

De dónde salen:
1. Juegos de Steam instalados: el número de la app sale del ``appmanifest_*.acf`` de su biblioteca
   (exacto, sin buscar por nombre).
2. Los demás: la búsqueda pública de la tienda de Steam por nombre; solo se acepta un resultado
   muy parecido (si no, mejor sin portada que la de otro juego).
3. La imagen es la cabecera de la tienda (460×215), que encaja en las tarjetas.

Se descargan una vez y quedan en ``APP_DIR/portadas`` (fuera de git). Lo que no se encontró se anota
en ``indice.json`` y no se vuelve a buscar hasta dentro de ``REINTENTAR_DIAS``. Una portada subida a mano
(``guardar_manual``) nunca se reemplaza sola. Solo se manda a internet el nombre del juego.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

ANCHO, ALTO = 460, 215
REINTENTAR_DIAS = 7
TIEMPO_MAXIMO = 10
MAX_BYTES = 3 * 1024 * 1024
PARECIDO_MINIMO = 0.86
BUSQUEDA = "https://store.steampowered.com/api/storesearch/"
CABECERAS = (
    "https://shared.cloudflare.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg",
    "https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg",
)
_cerrojo = threading.Lock()


def carpeta() -> Path:
    import jarvis_core

    destino = Path(jarvis_core.APP_DIR) / "portadas"
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def clave(nombre: str) -> str:
    texto = unicodedata.normalize("NFKD", nombre.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", texto).strip("-") or "juego"


def ruta(nombre: str) -> Path | None:
    """La portada guardada de ``nombre``, o None."""
    archivo = carpeta() / f"{clave(nombre)}.jpg"
    return archivo if archivo.is_file() else None


# --- Índice: qué se buscó y cuándo -------------------------------------------------------------

def _indice_ruta() -> Path:
    return carpeta() / "indice.json"


def _leer_indice() -> dict:
    try:
        return json.loads(_indice_ruta().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _anotar(nombre: str, estado: str) -> None:
    with _cerrojo:
        indice = _leer_indice()
        indice[clave(nombre)] = {"estado": estado, "cuando": time.time()}
        _indice_ruta().write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")


def hay_que_buscar(nombre: str, *, ahora: float | None = None) -> bool:
    """Sin portada y sin una búsqueda fallida reciente."""
    if ruta(nombre) is not None:
        return False
    dato = _leer_indice().get(clave(nombre))
    if not dato or dato.get("estado") != "sin_resultado":
        return True
    return (ahora or time.time()) - float(dato.get("cuando", 0)) > REINTENTAR_DIAS * 86400


def sin_resultado(nombre: str) -> bool:
    """Ya se buscó y no se encontró (la tarjeta muestra «SUBIR PORTADA»)."""
    return ruta(nombre) is None and _leer_indice().get(clave(nombre), {}).get("estado") == "sin_resultado"


# --- Dónde buscar ------------------------------------------------------------------------------

def appid_steam(carpeta_juego: Path) -> int | None:
    """Número de la app de un juego instalado con Steam (``steamapps/common/<carpeta>``)."""
    try:
        if carpeta_juego.parent.name.casefold() != "common":
            return None
        steamapps = carpeta_juego.parent.parent
        for manifiesto in steamapps.glob("appmanifest_*.acf"):
            texto = manifiesto.read_text(encoding="utf-8", errors="replace")
            dir_inst = re.search(r'"installdir"\s+"([^"]+)"', texto)
            appid = re.search(r'"appid"\s+"(\d+)"', texto)
            if dir_inst and appid and dir_inst.group(1).casefold() == carpeta_juego.name.casefold():
                return int(appid.group(1))
    except OSError:
        logging.debug("No se pudieron leer los manifiestos de Steam.", exc_info=True)
    return None


def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", texto).split())


def elegir_resultado(nombre: str, resultados: list[dict]) -> int | None:
    """El resultado de la tienda que corresponde a ``nombre``: igual o muy parecido; si no, None."""
    buscado = _normal(nombre)
    mejor, puntaje = None, 0.0
    for item in resultados:
        candidato = _normal(str(item.get("name", "")))
        if not candidato or "id" not in item:
            continue
        valor = 1.0 if candidato == buscado else SequenceMatcher(None, buscado, candidato).ratio()
        if valor > puntaje:
            mejor, puntaje = int(item["id"]), valor
    return mejor if puntaje >= PARECIDO_MINIMO else None


def buscar_appid(nombre: str, *, http=None) -> int | None:
    http = http or _requests()
    respuesta = http.get(BUSQUEDA, params={"term": nombre, "l": "spanish", "cc": "US"}, timeout=TIEMPO_MAXIMO)
    respuesta.raise_for_status()
    return elegir_resultado(nombre, list(respuesta.json().get("items", [])))


def _requests():
    import requests

    return requests


def _guardar_imagen(datos: bytes, destino: Path) -> None:
    """Valida que sea una imagen, la lleva a 460×215 (recortando al centro) y la guarda en JPG."""
    import io

    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(datos)) as imagen:
        imagen = ImageOps.fit(imagen.convert("RGB"), (ANCHO, ALTO), Image.LANCZOS)
        temporal = destino.with_suffix(".tmp")
        imagen.save(temporal, "JPEG", quality=88)
    temporal.replace(destino)


def descargar(appid: int, nombre: str, *, http=None) -> Path | None:
    http = http or _requests()
    for plantilla in CABECERAS:
        try:
            respuesta = http.get(plantilla.format(appid=appid), timeout=TIEMPO_MAXIMO)
        except Exception:
            logging.debug("No se pudo bajar la portada de %s.", nombre, exc_info=True)
            continue
        tipo = str(respuesta.headers.get("content-type", ""))
        if respuesta.status_code == 200 and tipo.startswith("image/") and 0 < len(respuesta.content) <= MAX_BYTES:
            destino = carpeta() / f"{clave(nombre)}.jpg"
            _guardar_imagen(respuesta.content, destino)
            return destino
    return None


def obtener(nombre: str, carpeta_juego: Path | None = None, *, http=None) -> Path | None:
    """La portada de un juego: la guardada o, si hace falta, la de Steam. Anota si no hubo suerte."""
    guardada = ruta(nombre)
    if guardada is not None or not hay_que_buscar(nombre):
        return guardada
    try:
        appid = appid_steam(carpeta_juego) if carpeta_juego is not None else None
        if appid is None:
            appid = buscar_appid(nombre, http=http)
        archivo = descargar(appid, nombre, http=http) if appid is not None else None
    except Exception as error:
        logging.info("No pude buscar la portada de %s (%s); reintento otro día.", nombre, error)
        _anotar(nombre, "sin_resultado")
        return None
    _anotar(nombre, "steam" if archivo else "sin_resultado")
    logging.info("Portada de %s: %s.", nombre, "descargada de Steam" if archivo else "no encontrada")
    return archivo


def guardar_manual(nombre: str, archivo: str | Path) -> Path:
    """Portada elegida por el usuario (JPG, PNG, WEBP…). Se recorta a la forma de la tarjeta."""
    origen = Path(archivo)
    if not origen.is_file():
        raise FileNotFoundError(f"No encontré la imagen {origen}.")
    if origen.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("La imagen es demasiado grande (más de 25 MB).")
    destino = carpeta() / f"{clave(nombre)}.jpg"
    try:
        _guardar_imagen(origen.read_bytes(), destino)
    except Exception as error:
        raise ValueError(f"Ese archivo no es una imagen que pueda abrir ({error}).") from error
    _anotar(nombre, "manual")
    return destino


def olvidar(nombre: str) -> None:
    """Borra la portada guardada y lo anotado, para buscarla de nuevo."""
    archivo = ruta(nombre)
    if archivo is not None:
        archivo.unlink(missing_ok=True)
    with _cerrojo:
        indice = _leer_indice()
        indice.pop(clave(nombre), None)
        _indice_ruta().write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
