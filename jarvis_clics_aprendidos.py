"""Clics aprendidos: dónde estaba lo que el usuario pidió pulsar la última vez.

Cuando la accesibilidad no encuentra un elemento (logos, botones dibujados, juegos),
hay que recurrir al modelo de visión, que tarda 20–30 s. Tras un clic aprobado y hecho,
se recuerda la posición **relativa a la ventana** de ese programa (desde el borde más
cercano: un logo arriba a la izquierda sigue en su sitio aunque la ventana cambie de
tamaño, y un botón de la derecha se mueve con el borde derecho). La próxima vez, si
la accesibilidad tampoco lo encuentra y esa ventana está visible, se propone ese punto
directamente (siempre con la confirmación y la marca roja). Si el usuario lo rechaza,
se olvida y se ofrece la visión.

Se guarda en la preferencia ``clics_aprendidos`` (JSON, como mucho ``MAXIMO`` entradas):
consulta, programa, bordes y distancias en píxeles, nombre mostrado y veces usado.
Nunca guarda capturas.
"""

from __future__ import annotations

import ctypes
import json
import logging
import time
from collections.abc import Callable
from ctypes import wintypes

import jarvis_screen_actions
import jarvis_windows

PREFERENCIA = "clics_aprendidos"
MAXIMO = 200
GA_ROOT = 2


def _clave(consulta: str) -> str:
    return jarvis_screen_actions.normalizar(consulta)


def cargar(obtener: Callable[[str], str | None]) -> list[dict]:
    try:
        datos = json.loads(obtener(PREFERENCIA) or "[]")
    except (ValueError, TypeError):
        logging.warning("Los clics aprendidos estaban dañados; se empieza de cero.")
        return []
    return [d for d in datos if isinstance(d, dict) and d.get("consulta") and d.get("programa")]


def posicion_relativa(x: int, y: int, rect: tuple[int, int, int, int]) -> dict:
    """Distancia al borde más cercano de la ventana, en cada eje."""
    izquierda, arriba, derecha, abajo = rect
    horizontal = ("izq", x - izquierda) if x - izquierda <= derecha - x else ("der", derecha - x)
    vertical = ("arr", y - arriba) if y - arriba <= abajo - y else ("aba", abajo - y)
    return {"borde_x": horizontal[0], "dx": horizontal[1], "borde_y": vertical[0], "dy": vertical[1]}


def posicion_absoluta(relativa: dict, rect: tuple[int, int, int, int]) -> tuple[int, int] | None:
    izquierda, arriba, derecha, abajo = rect
    x = izquierda + relativa["dx"] if relativa["borde_x"] == "izq" else derecha - relativa["dx"]
    y = arriba + relativa["dy"] if relativa["borde_y"] == "arr" else abajo - relativa["dy"]
    if not (izquierda <= x < derecha and arriba <= y < abajo):
        return None  # la ventana ahora es más chica que la distancia guardada
    return x, y


def recordar(entradas: list[dict], consulta: str, programa: str, x: int, y: int,
             rect: tuple[int, int, int, int], nombre: str) -> list[dict]:
    """Anota (o actualiza) el clic; lo más reciente queda primero."""
    clave = _clave(consulta)
    previa = next((e for e in entradas if e["consulta"] == clave and e["programa"] == programa), None)
    nueva = {"consulta": clave, "programa": programa, "nombre": nombre[:80],
             "veces": (previa or {}).get("veces", 0) + 1, "ultimo": int(time.time()),
             **posicion_relativa(x, y, rect)}
    resto = [e for e in entradas if e is not previa]
    return [nueva, *resto][:MAXIMO]


def olvidar(entradas: list[dict], consulta: str, programa: str) -> list[dict]:
    clave = _clave(consulta)
    return [e for e in entradas if not (e["consulta"] == clave and e["programa"] == programa)]


def candidatos(entradas: list[dict], consulta: str) -> list[dict]:
    clave = _clave(consulta)
    return [e for e in entradas if e["consulta"] == clave]


# --- Windows -------------------------------------------------------------------------

def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    return user32


def _rect(user32, hwnd) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def ventana_en(x: int, y: int) -> tuple[int, tuple[int, int, int, int]] | None:
    """Ventana principal visible en ese punto: (hwnd, rect) en coordenadas físicas."""
    jarvis_screen_actions._per_monitor_dpi()
    user32 = _user32()
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return None
    raiz = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    return int(raiz), _rect(user32, raiz)


def programa_de(hwnd: int) -> str:
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return jarvis_windows._programa(pid.value)


def buscar(entradas: list[dict], consulta: str, monitores: list, excluir_pid: int | None = None):
    """``(Objetivo, monitor, entrada)`` si lo aprendido cae en una ventana visible, o None."""
    elegidos = candidatos(entradas, consulta)
    if not elegidos:
        return None
    jarvis_screen_actions._per_monitor_dpi()
    user32 = _user32()
    for ventana in jarvis_windows.listar_ventanas():  # en orden Z: la de arriba primero
        if ventana.pid == excluir_pid:
            continue
        entrada = next((e for e in elegidos if e["programa"] == ventana.programa), None)
        if entrada is None:
            continue
        rect = _rect(user32, ventana.hwnd)
        punto = posicion_absoluta(entrada, rect)
        if punto is None:
            continue
        encima = ventana_en(*punto)
        if encima is None or encima[0] != ventana.hwnd:
            continue  # tapado por otra ventana
        monitor = next((m for m in monitores if jarvis_screen_actions._dentro(m, *punto)), None)
        if monitor is None:
            continue
        objetivo = jarvis_screen_actions.Objetivo(punto[0], punto[1], entrada["nombre"] or consulta,
                                                  "lo aprendido la última vez")
        return objetivo, monitor, entrada
    return None
