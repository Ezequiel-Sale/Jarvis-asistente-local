"""Pulsar un botón por su texto en la ventana de un programa (UI Automation, sin mover el mouse).

Pensado para rutinas: «abre Netmarble Launcher» y después «pulsa Jugar en Netmarble Launcher».
El launcher tarda en abrir (y a veces se actualiza), así que se espera hasta ``espera`` segundos
a que aparezca el botón. Funciona con ventanas de Chromium/Electron (como el launcher) y con
las nativas. Jarvis corre como administrador: puede leer también ventanas de programas elevados.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path

ESPERA_MAXIMA = 110
PAUSA = 1.5
TIPOS_PULSABLES = {"ButtonControl", "HyperlinkControl", "MenuItemControl", "ListItemControl", "TextControl",
                   "ImageControl", "CustomControl", "GroupControl", "TabItemControl"}


def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", (texto or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", texto).split())


def coincidencia(nombre: str, buscado: str) -> int:
    """2 = igual («JUGAR» y «Jugar»), 1 = lo contiene como palabras («Jugar ahora»), 0 = no."""
    nombre, buscado = _normal(nombre), _normal(buscado)
    if not nombre or not buscado:
        return 0
    if nombre == buscado:
        return 2
    return 1 if re.search(rf"(?:^|\s){re.escape(buscado)}(?:\s|$)", nombre) else 0


def es_del_programa(programa: str, ejecutable: str, titulo: str) -> bool:
    """La ventana es del programa si su ejecutable o su título lo nombran («Netmarble Launcher»)."""
    buscado = _normal(programa.removesuffix(".exe"))
    return bool(buscado) and (buscado in _normal(Path(ejecutable or "").stem) or buscado in _normal(titulo))


def elegir(controles: Iterable[tuple[str, str, object]], buscado: str):
    """De (tipo, nombre, control), el mejor: igual antes que «contiene», y los botones primero."""
    mejor, puntaje = None, 0
    for tipo, nombre, control in controles:
        if tipo not in TIPOS_PULSABLES:
            continue
        valor = coincidencia(nombre, buscado) * 2 + (tipo == "ButtonControl")
        if coincidencia(nombre, buscado) and valor > puntaje:
            mejor, puntaje = control, valor
    return mejor


def _ventanas_y_controles(programa: str):
    """[(título, [(tipo, nombre, control)…])] de las ventanas del programa (UIA real)."""
    import ctypes

    import psutil
    import uiautomation as auto

    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    resultado = []
    for ventana in auto.GetRootControl().GetChildren():
        try:
            ejecutable = psutil.Process(ventana.ProcessId).exe()
        except Exception:
            ejecutable = ""
        if not es_del_programa(programa, ejecutable, ventana.Name):
            continue
        controles = [(c.ControlTypeName, c.Name or "", c) for c, _ in auto.WalkControl(ventana, maxDepth=40)]
        resultado.append((ventana.Name or Path(ejecutable).stem, controles))
    return resultado


def pulsar_boton(programa: str, boton: str, espera: float = 60, *,
                 buscar: Callable[[str], list] | None = None, pulsar: Callable[[object], bool] | None = None,
                 cancelado: Callable[[], bool] = lambda: False, dormir=time.sleep) -> str:
    """Espera hasta ``espera`` s la ventana de ``programa`` con el botón ``boton`` y lo pulsa."""
    if not programa.strip() or not boton.strip():
        raise ValueError("Indica el programa y el texto del botón.")
    if buscar is None or pulsar is None:
        import uiautomation as auto

        from jarvis_media import _pulsar

        # Buscar y pulsar dentro de la misma inicialización de UIA (los controles no sirven fuera).
        with auto.UIAutomationInitializerInThread():
            return pulsar_boton(programa, boton, espera, buscar=buscar or _ventanas_y_controles,
                                pulsar=pulsar or _pulsar, cancelado=cancelado, dormir=dormir)
    limite = time.monotonic() + max(1.0, min(float(espera), ESPERA_MAXIMA))
    vio_ventana = False
    while True:
        for titulo, controles in buscar(programa):
            vio_ventana = True
            control = elegir(controles, boton)
            if control is not None and pulsar(control):
                logging.info("Pulsé «%s» en «%s».", boton, titulo)
                return f"Pulsé «{boton}» en {titulo}."
        if cancelado() or time.monotonic() >= limite:
            break
        dormir(PAUSA)
    if not vio_ventana:
        raise RuntimeError(f"No apareció la ventana de «{programa}» (¿está abierto?).")
    raise RuntimeError(f"No encontré el botón «{boton}» en {programa}. Fíjate cómo se llama exactamente "
                       "en la ventana.")
