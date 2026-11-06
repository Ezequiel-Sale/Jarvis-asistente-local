"""Control multimedia: reproducción, «¿qué está sonando?» y volumen por aplicación.

- Reproducción: sesiones multimedia de Windows (GSMTC, ``winrt``), las mismas
  que controla el panel de volumen de Windows: Spotify, YouTube en Brave,
  reproductores… Permite pausar o reanudar de forma explícita (no solo
  alternar). Si no hay sesión o falta ``winrt``, se usan las teclas multimedia.
- Volumen por aplicación: sesiones de audio de Windows (``pycaw``).

El título de lo que suena no se escribe en los registros.
"""

import asyncio
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

ACCIONES = ("play", "pause", "toggle", "next", "previous", "stop")

# Teclas multimedia (Virtual-Key codes) para cuando no hay sesión GSMTC.
_TECLAS = {"toggle": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}

_NOMBRES_APP = {
    "spotify": "Spotify",
    "brave": "Brave",
    "chrome": "Chrome",
    "msedge": "Edge",
    "firefox": "Firefox",
    "vlc": "VLC",
    "discord": "Discord",
    "zunemusic": "Reproductor multimedia",
    "microsoft.media.player": "Reproductor multimedia",
}

# Palabras que el usuario usa para referirse a una aplicación.
ALIAS_APP = {
    "navegador": "brave",
    "youtube": "brave",
    "musica": "spotify",
    "spotify": "spotify",
    "discord": "discord",
    "chrome": "chrome",
    "edge": "msedge",
    "firefox": "firefox",
    "steam": "steam",
    "juego": None,
}


def nombre_app(identificador: str) -> str:
    """«Spotify.exe» o «308046B0AF4A39CB» → nombre legible."""
    base = identificador.rsplit("\\", 1)[-1].rsplit("!", 1)[-1]
    base = re.sub(r"\.exe$", "", base, flags=re.IGNORECASE)
    clave = base.lower()
    for fragmento, nombre in _NOMBRES_APP.items():
        if fragmento in clave:
            return nombre
    return (base[:1].upper() + base[1:]) if base else "la aplicación"


# --- Reproducción (GSMTC) -------------------------------------------------------


@dataclass(frozen=True)
class Sesion:
    app: str
    estado: str  # reproduciendo | pausado | detenido | desconocido
    titulo: str = ""
    artista: str = ""


def _ejecutar(corrutina_factory: Callable):
    return asyncio.run(corrutina_factory())


def _estado_texto(codigo) -> str:
    valor = int(getattr(codigo, "value", codigo))
    return {4: "reproduciendo", 5: "pausado", 3: "detenido"}.get(valor, "desconocido")


async def _sesion_actual():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Gestor,
    )

    gestor = await Gestor.request_async()
    return gestor.get_current_session()


def sesion_actual(*, con_titulo: bool = False) -> Sesion | None:
    """Sesión multimedia activa, o None si no hay (o falta ``winrt``)."""
    try:
        import winrt.windows.media.control  # noqa: F401
    except ImportError:
        return None

    async def leer():
        sesion = await _sesion_actual()
        if sesion is None:
            return None
        titulo = artista = ""
        if con_titulo:
            try:
                props = await sesion.try_get_media_properties_async()
                titulo, artista = props.title or "", props.artist or ""
            except OSError:
                pass
        return Sesion(
            nombre_app(sesion.source_app_user_model_id or ""),
            _estado_texto(sesion.get_playback_info().playback_status),
            titulo,
            artista,
        )

    return _ejecutar(leer)


def _pulsar_tecla(vk: int) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


_FRASES = {
    "play": "Reanudé {app}.",
    "pause": "Pausé {app}.",
    "toggle": "Alterné la reproducción de {app}.",
    "next": "Siguiente en {app}.",
    "previous": "Anterior en {app}.",
    "stop": "Detuve {app}.",
}


def controlar_reproduccion(action: str, *, tecla: Callable[[int], None] = _pulsar_tecla) -> str:
    if action not in ACCIONES:
        raise ValueError(f"Acción multimedia no reconocida: {action}")
    try:
        import winrt.windows.media.control  # noqa: F401

        hay_winrt = True
    except ImportError:
        hay_winrt = False

    if hay_winrt:
        async def actuar():
            sesion = await _sesion_actual()
            if sesion is None:
                return None
            metodo = {
                "play": sesion.try_play_async,
                "pause": sesion.try_pause_async,
                "toggle": sesion.try_toggle_play_pause_async,
                "next": sesion.try_skip_next_async,
                "previous": sesion.try_skip_previous_async,
                "stop": sesion.try_stop_async,
            }[action]
            return nombre_app(sesion.source_app_user_model_id or ""), bool(await metodo())

        resultado = _ejecutar(actuar)
        if resultado is not None:
            app, aceptado = resultado
            if not aceptado:
                raise OSError(f"{app} no admite esa acción ahora mismo.")
            return _FRASES[action].format(app=app)

    # Sin sesión multimedia: las teclas solo sirven para alternar/saltar.
    if action in {"play", "pause"}:
        if hay_winrt:
            return "No hay nada reproduciéndose ahora mismo."
        action = "toggle"
    tecla(_TECLAS[action])
    return {
        "toggle": "Envié la tecla de reproducir/pausar.",
        "next": "Envié la tecla de siguiente pista.",
        "previous": "Envié la tecla de pista anterior.",
        "stop": "Envié la tecla de detener.",
    }[action]


def que_suena() -> str:
    sesion = sesion_actual(con_titulo=True)
    if sesion is None:
        return "No hay nada reproduciéndose ahora mismo."
    if not sesion.titulo:
        return f"Hay algo {sesion.estado} en {sesion.app}, pero no indica el título."
    de = f" de {sesion.artista}" if sesion.artista else ""
    verbo = "Está sonando" if sesion.estado == "reproduciendo" else f"Está {sesion.estado}"
    return f"{verbo} «{sesion.titulo}»{de} en {sesion.app}."


# --- Volumen por aplicación (pycaw) ----------------------------------------------


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in texto if not unicodedata.combining(c)).strip()


def _sesiones_audio():
    from pycaw.pycaw import AudioUtilities

    return [s for s in AudioUtilities.GetAllSessions() if s.Process is not None]


def _con_com(funcion: Callable):
    """pycaw usa COM: cada hilo de herramienta debe inicializarlo."""
    import comtypes

    comtypes.CoInitialize()
    try:
        return funcion()
    finally:
        comtypes.CoUninitialize()


def resolver_app(app: str) -> str:
    clave = _normalizar(app)
    clave = re.sub(r"^(?:el|la|los|las)\s+", "", clave)
    return ALIAS_APP.get(clave, clave) or clave


def volumen_aplicacion(
    app: str,
    percentage: int | None = None,
    mute: bool | None = None,
    *,
    sesiones: Callable[[], list] | None = None,
) -> str:
    if percentage is None and mute is None:
        raise ValueError("Indica percentage o mute.")
    if percentage is not None and not 0 <= percentage <= 100:
        raise ValueError("El volumen debe estar entre 0 y 100.")
    buscado = resolver_app(app)

    def aplicar() -> str:
        lista = (sesiones or _sesiones_audio)()
        coincidencias = [
            s for s in lista if buscado and buscado in _normalizar(s.Process.name())
        ]
        if not coincidencias:
            disponibles = sorted({nombre_app(s.Process.name()) for s in lista})
            extra = f" Aplicaciones con audio ahora: {', '.join(disponibles)}." if disponibles else ""
            return f"No encuentro audio de «{app}».{extra}"
        nombre = nombre_app(coincidencias[0].Process.name())
        for sesion in coincidencias:
            control = sesion.SimpleAudioVolume
            if percentage is not None:
                control.SetMasterVolume(percentage / 100, None)
                if percentage > 0:
                    control.SetMute(0, None)
            if mute is not None:
                control.SetMute(1 if mute else 0, None)
        if mute is True:
            return f"Silencié {nombre}."
        if percentage is None:
            return f"Quité el silencio de {nombre}."
        return f"Volumen de {nombre} al {percentage} %."

    return aplicar() if sesiones is not None else _con_com(aplicar)


def leer_volumen_aplicacion(app: str) -> int | None:
    buscado = resolver_app(app)

    def leer() -> int | None:
        for sesion in _sesiones_audio():
            if buscado in _normalizar(sesion.Process.name()):
                return round(sesion.SimpleAudioVolume.GetMasterVolume() * 100)
        return None

    return _con_com(leer)


# --- Frases ------------------------------------------------------------------------

_COSA = r"(?:\s+(?:la\s+|el\s+)?(?:musica|cancion|tema|reproduccion|video|spotify|youtube))?"
_PAUSA = re.compile(rf"^(?:pausa|pausar|pon\s+pausa|deten|detener|para|parar)(?:\s+la)?{_COSA}$")
_REANUDA = re.compile(
    rf"^(?:reanuda|reanudar|continua|continuar|sigue|seguir|play|dale\s+play|quita\s+(?:la\s+)?pausa"
    rf"|reproduce|despausa){_COSA}$"
)
_SIGUIENTE = re.compile(
    r"^(?:siguiente|la\s+siguiente|pasa|salta|saltea|cambia|otra|proxima)"
    r"(?:\s+(?:de\s+)?(?:la\s+|el\s+)?(?:cancion|tema|pista|video))?(?:\s+siguiente)?$"
    r"|^(?:siguiente|proxima|otra)\s+(?:cancion|tema|pista|video)$"
)
_ANTERIOR = re.compile(
    r"^(?:anterior|la\s+anterior|vuelve\s+a\s+la\s+anterior|pon\s+la\s+anterior"
    r"|(?:la\s+)?(?:cancion|tema|pista|video)\s+anterior)$"
)
_QUE_SUENA = re.compile(
    r"^(?:que|cual)\s+(?:cancion|tema|musica)\s+(?:es\s+esta|esta\s+sonando|suena|es)(?:\s+ahora)?$"
    r"|^que\s+(?:esta\s+sonando|suena)(?:\s+ahora)?$|^como\s+se\s+llama\s+esta\s+cancion$"
)
_NO_APP = {"equipo", "sistema", "pc", "windows", "todo", "jarvis", "tu", "tu voz", "computadora"}
_VOLUMEN_APP = re.compile(
    r"^(?:pon|poner|baja|bajar|sube|subir|ajusta|ajustar|deja|dejar|establece|establecer)\s+"
    r"(?:el\s+)?volumen\s+(?:de|del|de\s+la)\s+(?P<app>[a-z0-9]+(?:\s+[a-z0-9]+)?)\s+"
    r"(?:a|al|en)\s+(?P<n>\d{1,3})(?:\s+por\s+ciento)?$"
)
_SILENCIAR_APP = re.compile(
    r"^(?:silencia|silenciar|mutea|mutear)\s+(?:a\s+|el\s+|la\s+)?(?P<app>[a-z0-9]+)$"
    r"|^quita\s+el\s+(?:sonido|audio)\s+(?:a|de|del)\s+(?P<app2>[a-z0-9]+)$"
)
_ACTIVAR_APP = re.compile(
    r"^(?:quita\s+el\s+silencio|desmutea|activa\s+el\s+(?:sonido|audio))\s+(?:a\s+|de\s+|del\s+)?"
    r"(?:la\s+|el\s+)?(?P<app>[a-z0-9]+)$"
)


def detectar_comando(consulta: str) -> tuple[str, dict[str, object]] | None:
    """``consulta`` ya normalizada (sin tildes ni signos, ver jarvis_commands)."""
    if _PAUSA.fullmatch(consulta):
        return "media_control", {"action": "pause"}
    if _REANUDA.fullmatch(consulta):
        return "media_control", {"action": "play"}
    if _SIGUIENTE.fullmatch(consulta):
        return "media_control", {"action": "next"}
    if _ANTERIOR.fullmatch(consulta):
        return "media_control", {"action": "previous"}
    if _QUE_SUENA.fullmatch(consulta):
        return "get_now_playing", {}
    if (m := _VOLUMEN_APP.fullmatch(consulta)) and m.group("app") not in _NO_APP:
        porcentaje = int(m.group("n"))
        if porcentaje > 100:
            return None
        return "set_app_volume", {"app": m.group("app"), "percentage": porcentaje}
    if (m := _SILENCIAR_APP.fullmatch(consulta)):
        app = m.group("app") or m.group("app2")
        if app not in _NO_APP:
            return "set_app_volume", {"app": app, "mute": True}
    if (m := _ACTIVAR_APP.fullmatch(consulta)) and m.group("app") not in _NO_APP:
        return "set_app_volume", {"app": m.group("app"), "mute": False}
    return None


def registrar_herramientas(registry, verificadores=None) -> None:
    verificadores = verificadores or {}
    registry.register(
        "media_control",
        (
            "Controla lo que se reproduce en Windows (Spotify, YouTube en el navegador…): "
            "play, pause, toggle, next, previous o stop."
        ),
        {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": list(ACCIONES)}},
            "required": ["action"],
            "additionalProperties": False,
        },
        controlar_reproduccion,
        label="Controlar reproducción",
        timeout_seconds=10,
        verifier=verificadores.get("media_control"),
    )
    registry.register(
        "get_now_playing",
        "Solo lectura. Qué canción o vídeo se está reproduciendo y en qué aplicación.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        que_suena,
        label="Consultar qué está sonando",
        timeout_seconds=10,
        record_result=False,
    )
    registry.register(
        "set_app_volume",
        (
            "Cambia el volumen (percentage 0-100) o silencia (mute) una aplicación concreta "
            "que esté sonando: spotify, brave (navegador/YouTube), discord, steam…"
        ),
        {
            "type": "object",
            "properties": {
                "app": {"type": "string", "minLength": 1, "maxLength": 40},
                "percentage": {"type": "integer", "minimum": 0, "maximum": 100},
                "mute": {"type": "boolean"},
            },
            "required": ["app"],
            "additionalProperties": False,
        },
        volumen_aplicacion,
        label="Ajustar volumen de una aplicación",
        timeout_seconds=10,
        verifier=verificadores.get("set_app_volume"),
    )
