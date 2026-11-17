"""Fase 7 de gestos: «Jarvis, esto». Apuntás con el índice (el puntero de los gestos mueve el
mouse) y una orden con «esto / eso / acá» usa lo que está bajo el puntero:

- ventanas («cerrá esto», «mové esto al monitor 2», «minimizá eso»): la ventana bajo el puntero si
  apuntaste hace menos de ``SEGUNDOS_APUNTADO`` s; si no, la activa, como antes
  (``jarvis_windows.ventana_activa`` consulta ``apuntado``);
- «¿qué es esto?», «leé esto», «¿qué dice acá?»: se mira solo la zona alrededor del puntero, con una
  marca donde apunta (``zona_alrededor``, ``marcar``), y pasa por la vista previa como toda captura;
- «hacé clic acá», «clic derecho ahí», «doble clic acá»: el botón del mouse donde está el puntero.

Sin dependencias pesadas: lo importan ``jarvis_windows`` y la app.
"""

from __future__ import annotations

import ctypes
import re
import threading
import time
import unicodedata

SEGUNDOS_APUNTADO = 20.0  # tras bajar la mano hay tiempo de decir la orden
ZONA_ANCHO, ZONA_ALTO = 1100, 720  # px físicos alrededor del puntero para «¿qué es esto?»

_cerrojo = threading.Lock()
_ultimo: float | None = None  # monotonic de la última vez que el puntero de gestos movió el mouse


def marcar_apuntado(ahora: float | None = None) -> None:
    """La llama el puntero de los gestos cada vez que mueve el mouse."""
    global _ultimo
    with _cerrojo:
        _ultimo = time.monotonic() if ahora is None else ahora


def olvidar() -> None:
    global _ultimo
    with _cerrojo:
        _ultimo = None


def cursor() -> tuple[int, int] | None:
    """Posición del puntero en coordenadas físicas (las mismas que usan ventanas y capturas)."""
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass
    punto = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(punto)):
        return None
    return punto.x, punto.y


def apuntado(segundos: float = SEGUNDOS_APUNTADO, ahora: float | None = None,
             posicion=cursor) -> tuple[int, int] | None:
    """Dónde apuntaste con el dedo, si fue hace menos de ``segundos``; si no, None."""
    with _cerrojo:
        ultimo = _ultimo
    ahora = time.monotonic() if ahora is None else ahora
    if ultimo is None or ahora - ultimo > segundos:
        return None
    return posicion()


# --- Frases ------------------------------------------------------------------------------------

def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    return re.sub(r"^(?:(?:hey|oye)\s+)?jarvis\s+", "", texto)


_ESTO = r"(?:esto|eso|aca|aqui|ahi|esta\s+cosa|esa\s+cosa)"
_MIRAR = re.compile(
    r"^(?:que\s+(?:es|son)\s+" + _ESTO + r"|que\s+(?:dice|pone|hay)\s+" + _ESTO
    + r"|(?:lee|leeme|lei|leime)\s+" + _ESTO + r"|(?:explica|explicame|explicame\s+que\s+es)\s+" + _ESTO
    + r"|(?:mira|mirame|fijate\s+en|describe|describime|describeme)\s+" + _ESTO
    + r"|(?:que\s+significa|que\s+quiere\s+decir|para\s+que\s+sirve)\s+" + _ESTO + r")"
    r"(?:\s+(?:que|y)\s+.+)?$"
)
_CLIC = re.compile(
    r"^(?:(?:hace|haz|hacer|dale|da|pega)\s+(?:un\s+)?)?(?P<doble>doble\s+)?(?:clic|click|clik)"
    r"(?:\s+(?P<derecho>derecho))?\s+(?:(?:aca|aqui|ahi|ahi\s+mismo|en\s+(?:esto|eso)))$"
    r"|^(?:(?:aprieta|apreta|toca|pulsa)\s+(?:aca|aqui|ahi|esto|eso))$"
)
# «cerralo» suelto no: puede ser lo que se nombró antes en la charla.
_CERRAR = re.compile(r"^(?:cierra|cerra)\s+(?:esto|eso|esta\s+ventana|esa\s+ventana)$")


def detectar(texto: str) -> dict | None:
    """``{"tipo": "mirar", "pregunta"}``, ``{"tipo": "clic", "accion"}``, ``{"tipo": "cerrar"}`` o None."""
    consulta = _normal(texto)
    if (m := _MIRAR.fullmatch(consulta)) is not None:
        pregunta = re.sub(r"^\W*(?:(?:hey|oye)\s+)?jarvis\W*", "", texto.strip(), flags=re.IGNORECASE)
        return {"tipo": "mirar", "pregunta": pregunta.strip(" ¿?¡!.") or "¿Qué es esto?"}
    if (m := _CLIC.fullmatch(consulta)) is not None:
        if m.group("doble"):
            accion = "double_click"
        elif m.group("derecho"):
            accion = "right_click"
        else:
            accion = "click"
        return {"tipo": "clic", "accion": accion}
    if consulta != "cierra" and _CERRAR.fullmatch(consulta):
        return {"tipo": "cerrar"}
    return None


# --- Zona para mirar ---------------------------------------------------------------------------

def zona_alrededor(punto: tuple[int, int], monitor, ancho: int = ZONA_ANCHO, alto: int = ZONA_ALTO):
    """(x, y, ancho, alto) en px físicos: un recuadro centrado en el puntero, dentro del monitor."""
    ancho, alto = min(ancho, monitor.ancho), min(alto, monitor.alto)
    x = min(max(punto[0] - ancho // 2, monitor.x), monitor.x + monitor.ancho - ancho)
    y = min(max(punto[1] - alto // 2, monitor.y), monitor.y + monitor.alto - alto)
    return x, y, ancho, alto


def marcar(imagen, punto_en_imagen: tuple[int, int]):
    """Copia de la imagen con un anillo rojo donde apunta (así el modelo sabe qué es «esto»)."""
    from PIL import ImageDraw

    copia = imagen.convert("RGB").copy()
    dibujo = ImageDraw.Draw(copia)
    x, y = punto_en_imagen
    for radio, grosor in ((26, 5), (30, 2)):
        dibujo.ellipse((x - radio, y - radio, x + radio, y + radio), outline=(255, 40, 40), width=grosor)
    return copia


def pregunta_para_el_modelo(pregunta: str) -> str:
    return ("El usuario está señalando lo que hay dentro del círculo rojo de esta captura (es parte "
            f"de su pantalla). Responde sobre eso, no sobre el círculo. Pregunta: {pregunta}")
