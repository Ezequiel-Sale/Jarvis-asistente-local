"""«Deshaz lo último»: antes de cada acción reversible se anota cómo volver atrás.

``ToolRegistry`` llama a ``Historial.capturar`` antes de ejecutar una herramienta y a
``Historial.anotar`` si salió bien. «Deshaz lo último» (herramienta ``undo_last_action``)
saca la última anotación y ejecuta la acción inversa con el registro de herramientas.

Reversibles: volumen del equipo y de un programa, salida de audio, silencio de pestañas,
pausa/reanudar, pantalla completa, modo teatro, tema de Jarvis, minimizar/maximizar
ventanas y cambios de monitores (se vuelve a la configuración exacta de antes).
``inverso`` es lógica pura: el estado actual se lee con funciones que se le pasan.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

CADUCA = 30 * 60   # segundos: algo de hace más de media hora ya no es «lo último»
MAXIMO = 10
NO_SE_ANOTA = frozenset({"deshacer"})  # orígenes cuyas acciones no se anotan (el propio deshacer)


@dataclass
class Deshacer:
    descripcion: str                      # «Volver el volumen del equipo al 40 %»
    herramienta: str | None = None        # acción inversa por el registro de herramientas
    argumentos: dict = field(default_factory=dict)
    accion: Callable[[], object] | None = None  # o una función (monitores)
    momento: float = 0.0


Lectores = Mapping[str, Callable[..., object]]


def inverso(herramienta: str, args: Mapping[str, object], leer: Lectores) -> Deshacer | None:
    """Cómo deshacer ``herramienta(args)`` según el estado de ahora (antes de ejecutarla)."""
    if herramienta == "set_system_volume":
        previo = int(leer["volumen"]())
        return Deshacer(f"Volver el volumen del equipo al {previo} %", "set_system_volume", {"percentage": previo})
    if herramienta == "set_app_volume":
        app = str(args.get("app", ""))
        if "percentage" in args:
            previo = leer["volumen_app"](app)
            if previo is None:
                return None
            return Deshacer(f"Volver el volumen de {app} al {previo} %", "set_app_volume",
                            {"app": app, "percentage": int(previo)})
        if "mute" in args:
            silenciar = not bool(args["mute"])
            return Deshacer(f"{'Silenciar' if silenciar else 'Devolverle el sonido a'} {app}",
                            "set_app_volume", {"app": app, "mute": silenciar})
        return None
    if herramienta == "set_output_device":
        previo = leer["salida"]()
        if previo == args.get("device"):
            return None
        nombre = "los auriculares" if previo == "headphones" else "los parlantes"
        return Deshacer(f"Volver a sacar el sonido por {nombre}", "set_output_device", {"device": previo})
    if herramienta == "mute_browser_tab":
        silenciar = not bool(args.get("mute", True))
        return Deshacer(("Volver a silenciar" if silenciar else "Devolverle el sonido a") + " la pestaña",
                        "mute_browser_tab", {**args, "mute": silenciar})
    if herramienta == "media_control":
        opuesto = {"pause": "play", "play": "pause", "toggle": "toggle"}.get(str(args.get("action")))
        if opuesto is None:
            return None  # «siguiente canción» no tiene vuelta atrás fiable
        texto = {"play": "Reanudar", "pause": "Volver a pausar", "toggle": "Volver a pausar o reanudar"}[opuesto]
        return Deshacer(f"{texto} el vídeo o la música", "media_control", {**args, "action": opuesto})
    if herramienta in ("video_fullscreen", "video_theater"):
        activar = not bool(args.get("enabled", True))
        modo = "pantalla completa" if herramienta == "video_fullscreen" else "modo teatro"
        return Deshacer(f"{'Volver a poner' if activar else 'Quitar'} {modo}", herramienta,
                        {**args, "enabled": activar})
    if herramienta == "set_theme":
        previo = leer["tema"]()
        if previo == args.get("theme"):
            return None
        return Deshacer(f"Volver al tema «{previo}»", "set_theme", {"theme": previo})
    if herramienta == "window_control":
        app = str(args.get("app", ""))
        if args.get("action") in ("window_minimize", "window_maximize") and app and app != "todo":
            return Deshacer(f"Restaurar la ventana de {app}", "window_control",
                            {"app": app, "action": "window_restore"})
        return None
    if herramienta == "configure_display":
        previa = leer["monitores"]()
        restaurar = leer["restaurar_monitores"]
        return Deshacer("Volver los monitores a como estaban", accion=lambda: restaurar(previa))
    return None


REVERSIBLES = frozenset({
    "set_system_volume", "set_app_volume", "set_output_device", "mute_browser_tab", "media_control",
    "video_fullscreen", "video_theater", "set_theme", "window_control", "configure_display",
})


class Historial:
    """Pila de lo que se puede deshacer (las últimas ``MAXIMO``, de menos de ``CADUCA`` s)."""

    def __init__(self, lectores: Lectores, reloj: Callable[[], float] = time.monotonic) -> None:
        self._lectores = lectores
        self._reloj = reloj
        self._pila: deque[Deshacer] = deque(maxlen=MAXIMO)
        self._cerrojo = threading.Lock()

    def capturar(self, herramienta: str, args: Mapping[str, object], origen: str) -> Deshacer | None:
        """Antes de ejecutar: cómo volver atrás (None si no se puede o no hace falta)."""
        if herramienta not in REVERSIBLES or origen in NO_SE_ANOTA:
            return None
        try:
            deshacer = inverso(herramienta, args, self._lectores)
        except Exception:
            logging.debug("No se pudo anotar cómo deshacer %s.", herramienta, exc_info=True)
            return None
        if deshacer is not None:
            deshacer.momento = self._reloj()
        return deshacer

    def anotar(self, deshacer: Deshacer | None) -> None:
        """La acción salió bien: queda como «lo último»."""
        if deshacer is None:
            return
        with self._cerrojo:
            self._pila.append(deshacer)

    def sacar(self) -> Deshacer | None:
        with self._cerrojo:
            while self._pila:
                deshacer = self._pila.pop()
                if self._reloj() - deshacer.momento <= CADUCA:
                    return deshacer
            return None


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", texto).split())


_PIDE = re.compile(
    r"^(?:(?:hey|oye)\s+)?(?:jarvis\s+)?(?:por\s+favor\s+)?"
    r"(?:deshaz(?:lo)?|deshacer|desase|desaz|revierte|revertir|anula|cancela\s+el\s+ultimo\s+cambio|"
    r"vuelve\s+(?:a\s+)?(?:como\s+estaba|dejarlo\s+como\s+estaba)|dejalo\s+como\s+estaba|control\s+z)"
    r"(?:\s+(?:lo\s+ultimo|eso|el\s+ultimo\s+cambio|la\s+ultima\s+accion|lo\s+que\s+hiciste))?"
    r"(?:\s+por\s+favor)?(?:\s+jarvis)?$"
)


def detectar(texto: str) -> tuple[str, dict[str, object]] | None:
    """«Deshaz lo último», «deshazlo», «revierte eso», «déjalo como estaba» → undo_last_action."""
    consulta = _normalizar(texto)
    if consulta in ("revierte", "anula", "revertir"):  # solos son ambiguos
        return None
    return ("undo_last_action", {}) if _PIDE.fullmatch(consulta) else None
