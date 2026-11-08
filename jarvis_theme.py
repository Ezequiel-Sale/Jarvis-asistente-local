"""Temas visuales de Jarvis.

Todos los colores de la interfaz salen de aquí, por nombre (token). Los módulos
los importan como constantes (``from jarvis_theme import CYAN``); al cambiar de
tema, ``activar`` reasigna esas constantes en todos los módulos ``jarvis_*`` ya
cargados (así lo que se dibuje después usa el tema nuevo) y ``recolorear``
cambia los widgets que ya existen, color por color. Por eso, dentro de un tema,
cada token debe tener un color distinto (``test_temas`` lo comprueba).
"""

import sys
import tkinter as tk
from tkinter import ttk

TOKENS = (
    "BG", "PANEL", "PANEL_LIGHT", "CYAN", "CYAN_DARK", "VIOLET", "TEXT", "MUTED", "ERROR",
    "HOVER", "BORDER", "BORDER_STRONG", "ACCENT_ACTIVE",
    "ORB_IDLE", "ORB_BRIGHT", "ORB_BRIGHT_ALT", "ORB_RING_A", "ORB_RING_B", "ORB_RING_C",
    "ORB_RING_D", "ORB_TICK", "ORB_ARC_B", "ORB_ARC_C", "ORB_ARC_IDLE", "ORB_CORE", "ORB_CORE_LINE",
)

TEMAS: dict[str, dict[str, str]] = {
    "cian": {
        "BG": "#07111C", "PANEL": "#0D1B2A", "PANEL_LIGHT": "#132538", "CYAN": "#42E8D8",
        "CYAN_DARK": "#123C43", "VIOLET": "#A98BFF", "TEXT": "#E8F0FA", "MUTED": "#8293A8",
        "ERROR": "#FF7188", "HOVER": "#1D374B", "BORDER": "#193246", "BORDER_STRONG": "#24445A",
        "ACCENT_ACTIVE": "#1A545B", "ORB_IDLE": "#1F6873", "ORB_BRIGHT": "#8EFFF2",
        "ORB_BRIGHT_ALT": "#D0BCFF", "ORB_RING_A": "#102B38", "ORB_RING_B": "#173B49",
        "ORB_RING_C": "#0E2633", "ORB_RING_D": "#102A38", "ORB_TICK": "#28505D",
        "ORB_ARC_B": "#3CA9B7", "ORB_ARC_C": "#45D9E8", "ORB_ARC_IDLE": "#34818B",
        "ORB_CORE": "#0A1B27", "ORB_CORE_LINE": "#193B49",
    },
    "mark": {  # rojo y dorado
        "BG": "#120A0A", "PANEL": "#1C0F0E", "PANEL_LIGHT": "#2A1614", "CYAN": "#FFC24A",
        "CYAN_DARK": "#4A3412", "VIOLET": "#FF6B5B", "TEXT": "#F8EDE6", "MUTED": "#AE9389",
        "ERROR": "#FF4D7A", "HOVER": "#3A1F1C", "BORDER": "#331B18", "BORDER_STRONG": "#4D2A25",
        "ACCENT_ACTIVE": "#5C4316", "ORB_IDLE": "#7A571C", "ORB_BRIGHT": "#FFE5A3",
        "ORB_BRIGHT_ALT": "#FFB4A9", "ORB_RING_A": "#2B1512", "ORB_RING_B": "#3B1D18",
        "ORB_RING_C": "#24110F", "ORB_RING_D": "#2D1613", "ORB_TICK": "#5E3A2B",
        "ORB_ARC_B": "#D1312A", "ORB_ARC_C": "#FFD27A", "ORB_ARC_IDLE": "#8E6A2C",
        "ORB_CORE": "#170B0A", "ORB_CORE_LINE": "#3E211C",
    },
    "claro": {
        "BG": "#EEF3F8", "PANEL": "#FFFFFF", "PANEL_LIGHT": "#E3EBF3", "CYAN": "#0B7F80",
        "CYAN_DARK": "#CDEDEA", "VIOLET": "#6A4BD6", "TEXT": "#142233", "MUTED": "#5A6B7F",
        "ERROR": "#C9304F", "HOVER": "#D3DFEA", "BORDER": "#C8D5E2", "BORDER_STRONG": "#A8BACC",
        "ACCENT_ACTIVE": "#B6E2DE", "ORB_IDLE": "#7DBFC2", "ORB_BRIGHT": "#075F60",
        "ORB_BRIGHT_ALT": "#4A31B0", "ORB_RING_A": "#DAE6EF", "ORB_RING_B": "#CBDDE9",
        "ORB_RING_C": "#E5EEF5", "ORB_RING_D": "#D6E3ED", "ORB_TICK": "#A2BCCD",
        "ORB_ARC_B": "#2A9FA5", "ORB_ARC_C": "#13B2AC", "ORB_ARC_IDLE": "#6DA8B1",
        "ORB_CORE": "#F7FAFC", "ORB_CORE_LINE": "#BCD1DF",
    },
}

ETIQUETAS = {"cian": "Cian", "mark": "Mark (rojo y dorado)", "claro": "Claro"}
TEMA_POR_DEFECTO = "cian"
PREFERENCIA = "tema"

# Valores iniciales: tema por defecto.
BG = PANEL = PANEL_LIGHT = CYAN = CYAN_DARK = VIOLET = TEXT = MUTED = ERROR = ""
HOVER = BORDER = BORDER_STRONG = ACCENT_ACTIVE = ""
ORB_IDLE = ORB_BRIGHT = ORB_BRIGHT_ALT = ORB_RING_A = ORB_RING_B = ORB_RING_C = ""
ORB_RING_D = ORB_TICK = ORB_ARC_B = ORB_ARC_C = ORB_ARC_IDLE = ORB_CORE = ORB_CORE_LINE = ""
globals().update(TEMAS[TEMA_POR_DEFECTO])
_actual = TEMA_POR_DEFECTO


def tema_actual() -> str:
    return _actual


def nombre_valido(nombre: str | None) -> str:
    return nombre if nombre in TEMAS else TEMA_POR_DEFECTO


def activar(nombre: str) -> dict[str, str]:
    """Cambia las constantes de color en todos los módulos jarvis_* cargados.

    Devuelve el mapa ``color viejo → color nuevo`` (en mayúsculas) para
    recolorear los widgets que ya existen.
    """
    global _actual
    nombre = nombre_valido(nombre)
    viejo, nuevo = TEMAS[_actual], TEMAS[nombre]
    for modulo in list(sys.modules.values()):
        if not getattr(modulo, "__name__", "").startswith("jarvis"):
            continue
        for token in TOKENS:
            if getattr(modulo, token, None) == viejo[token]:
                setattr(modulo, token, nuevo[token])
    _actual = nombre
    return {viejo[t].upper(): nuevo[t] for t in TOKENS if viejo[t] != nuevo[t]}


_OPCIONES = (
    "background", "foreground", "activebackground", "activeforeground",
    "highlightbackground", "highlightcolor", "insertbackground", "selectbackground",
    "selectforeground", "disabledforeground", "troughcolor", "readonlybackground",
)


def _cambiar(valor: object, mapa: dict[str, str]) -> str | None:
    return mapa.get(str(valor).upper()) if isinstance(valor, str) and valor.startswith("#") else None


def recolorear(raiz: tk.Misc, mapa: dict[str, str]) -> int:
    """Aplica ``mapa`` a ``raiz`` y todos sus descendientes. Devuelve cuántos cambios hizo."""
    if not mapa:
        return 0
    cambios = 0
    pendientes = [raiz]
    while pendientes:
        widget = pendientes.pop()
        pendientes.extend(widget.winfo_children())
        if isinstance(widget, ttk.Widget):
            continue  # los ttk se recolorean con sus estilos
        for opcion in _OPCIONES:
            try:
                nuevo = _cambiar(widget.cget(opcion), mapa)
            except tk.TclError:
                continue
            if nuevo:
                widget.configure({opcion: nuevo})
                cambios += 1
        if isinstance(widget, tk.Canvas):
            for item in widget.find_all():
                for opcion in ("fill", "outline", "activefill"):
                    try:
                        nuevo = _cambiar(widget.itemcget(item, opcion), mapa)
                    except tk.TclError:
                        continue
                    if nuevo:
                        widget.itemconfigure(item, {opcion: nuevo})
                        cambios += 1
        elif isinstance(widget, tk.Text):
            for etiqueta in widget.tag_names():
                for opcion in ("foreground", "background"):
                    nuevo = _cambiar(widget.tag_cget(etiqueta, opcion), mapa)
                    if nuevo:
                        widget.tag_configure(etiqueta, {opcion: nuevo})
                        cambios += 1
    return cambios
