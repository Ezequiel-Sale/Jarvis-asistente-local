"""Control multimedia: reproducción, «¿qué está sonando?» y volumen por aplicación.

- Reproducción: sesiones multimedia de Windows (GSMTC, ``winrt``), las mismas
  que controla el panel de volumen de Windows: Spotify, YouTube en Brave,
  reproductores… Permite pausar o reanudar de forma explícita (no solo
  alternar). Si no hay sesión o falta ``winrt``, se usan las teclas multimedia.
- Volumen por aplicación: sesiones de audio de Windows (``pycaw``).

El título de lo que suena no se escribe en los registros.
"""

import asyncio
from ctypes import wintypes
import ctypes
import threading
import logging
import time
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


def _ejecutar(corrutina_factory: Callable, *, limite: float = 15.0):
    """Corre la consulta WinRT en un hilo propio.

    Si el hilo que llama ya usó COM con pycaw (CoInitialize/CoUninitialize), WinRT
    se cuelga en ese mismo hilo; en uno nuevo no. ``limite`` evita esperar para siempre.
    """
    resultado: dict = {}

    def correr() -> None:
        try:
            resultado["valor"] = asyncio.run(corrutina_factory())
        except BaseException as error:  # se relanza en el hilo que llamó
            resultado["error"] = error

    hilo = threading.Thread(target=correr, name="JarvisWinRT", daemon=True)
    hilo.start()
    hilo.join(limite)
    if hilo.is_alive():
        raise OSError("Windows no respondió a la consulta multimedia.")
    if "error" in resultado:
        raise resultado["error"]
    return resultado.get("valor")


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


def estado_reproduccion() -> dict:
    """Lo que suena, para la app del celular: {app, state, title, artist, cover}."""
    try:
        import winrt.windows.media.control  # noqa: F401
    except ImportError:
        return {}

    async def leer():
        sesion = await _sesion_actual()
        if sesion is None:
            return {}
        titulo = artista = ""
        portada = False
        try:
            props = await sesion.try_get_media_properties_async()
            titulo, artista = props.title or "", props.artist or ""
            portada = props.thumbnail is not None
        except OSError:
            pass
        return {
            "app": nombre_app(sesion.source_app_user_model_id or ""),
            "state": _estado_texto(sesion.get_playback_info().playback_status),
            "title": titulo,
            "artist": artista,
            "cover": portada,
        }

    return _ejecutar(leer)


def portada_actual() -> bytes | None:
    """Imagen (PNG o JPEG) de la carátula de lo que suena, o None."""
    try:
        from winrt.windows.storage.streams import DataReader
    except ImportError:
        return None

    async def leer():
        sesion = await _sesion_actual()
        if sesion is None:
            return None
        props = await sesion.try_get_media_properties_async()
        if props.thumbnail is None:
            return None
        flujo = await props.thumbnail.open_read_async()
        tamano = int(flujo.size)
        if not 0 < tamano <= 5 * 1024 * 1024:
            return None
        lector = DataReader(flujo)
        await lector.load_async(tamano)
        datos = bytearray(tamano)
        lector.read_bytes(datos)
        return bytes(datos)

    try:
        return _ejecutar(leer)
    except OSError:
        return None


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


def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", (texto or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", texto).split())


def titulos_en_monitor(numero: int) -> list[str]:
    """Títulos de las ventanas visibles cuyo centro está en ese monitor."""
    import ctypes
    from ctypes import wintypes

    import jarvis_vision

    monitor = jarvis_vision.elegir_monitor(jarvis_vision.listar_monitores(), numero)
    user32 = ctypes.windll.user32
    anterior = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    titulos: list[str] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def recolectar(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        largo = user32.GetWindowTextLengthW(hwnd)
        if not largo:
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        if monitor.x <= cx < monitor.x + monitor.ancho and monitor.y <= cy < monitor.y + monitor.alto:
            buffer = ctypes.create_unicode_buffer(largo + 1)
            user32.GetWindowTextW(hwnd, buffer, largo + 1)
            titulos.append(buffer.value)
        return True

    try:
        user32.EnumWindows(recolectar, 0)
    finally:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(anterior))
    return titulos


def _aparece(titulo: str, ventanas: list[str]) -> bool:
    titulo = _normal(titulo)
    # Títulos muy cortos coincidirían con cualquier ventana.
    return len(titulo) >= 4 and any(titulo in _normal(v) for v in ventanas)


def elegir_sesion(
    sesiones: list[tuple[object, str]],
    titulos_ventanas: list[str],
    titulos_otros: list[str] = (),
):
    """La sesión cuyo título aparece en una ventana del monitor (p. ej. Spotify).
    Sin coincidencia clara no se adivina: Brave/Chrome exponen una sola sesión
    (la última que sonó), que puede ser de una pestaña de otro monitor."""
    coincidentes = [
        sesion
        for sesion, titulo in sesiones
        if _aparece(titulo, titulos_ventanas) and not _aparece(titulo, list(titulos_otros))
    ]
    if len(coincidentes) == 1:
        return coincidentes[0]
    return None


# Botones del reproductor (YouTube y similares) tal como los expone la
# accesibilidad del navegador, ya normalizados: «Reproducir (k)»,
# «Pausa combinación de teclas k», «Siguiente (Mayús+n)»…
# YouTube: «Reproducir (k)», «Pausa combinación de teclas k»; Twitch: «Pause (space/k)».
# Otros reproductores (Crunchyroll y similares) no ponen el atajo en el nombre:
# «Reproducir», «Pantalla completa», «Enter fullscreen». El atajo es opcional.
_TECLAS_BOTON = r"(?:\s+[a-z]+){0,4}?"
_BOTON_PLAY = re.compile(rf"^(?:reproducir|play)(?:{_TECLAS_BOTON}\s+k)?$")
_BOTON_PAUSA = re.compile(rf"^(?:pausa|pausar|pause)(?:{_TECLAS_BOTON}\s+k)?$")
_BOTON_SIGUIENTE = re.compile(rf"^(?:siguiente|next){_TECLAS_BOTON}\s+(?:mayus|shift)\s+n$")
_BOTON_ANTERIOR = re.compile(rf"^(?:anterior|previous){_TECLAS_BOTON}\s+(?:mayus|shift)\s+p$")
# YouTube: «Pantalla completa (f)» / «Salir de pantalla completa (f)»; en inglés «Full screen (f)».
_BOTON_PANTALLA_COMPLETA = re.compile(
    rf"^(?:entrar\s+(?:en|a)\s+(?:la\s+)?|activar\s+(?:la\s+)?|ver\s+en\s+|enter\s+)?"
    rf"(?:pantalla\s+completa|full\s*screen)(?:\s+mode)?(?:{_TECLAS_BOTON}\s+f)?$"
)
_BOTON_SALIR_PANTALLA_COMPLETA = re.compile(
    rf"^(?:(?:salir\s+de|desactivar)\s+(?:la\s+)?pantalla\s+completa|exit\s+full\s*screen(?:\s+mode)?)"
    rf"(?:{_TECLAS_BOTON}\s+f)?$"
)

# Modo teatro / modo cine: YouTube «Modo cine (t)» y, activo, «Vista predeterminada (t)»
# (en inglés «Theater mode (t)» / «Default view (t)»); Twitch «Theatre Mode (alt+t)».
_BOTON_MODO_TEATRO = re.compile(
    rf"^(?:modo\s+(?:cine|teatro)|theat(?:er|re)\s+mode)(?:{_TECLAS_BOTON}\s+t)?$"
)
_BOTON_SALIR_MODO_TEATRO = re.compile(
    rf"^(?:salir\s+del\s+modo\s+(?:cine|teatro)|exit\s+theat(?:er|re)\s+mode|vista\s+predeterminada|default\s+view)"
    rf"(?:{_TECLAS_BOTON}\s+t)?$"
)


def _pulsar(control) -> bool:
    """Pulsa un control de accesibilidad por la vía que ofrezca, sin mover el ratón.

    Cada web expone sus botones distinto: YouTube admite «Invoke», pero los de
    Twitch solo la acción por defecto de accesibilidad (LegacyIAccessible), y otros
    reproductores, «Toggle». Antes solo se usaba Invoke y en Twitch fallaba con
    «'NoneType' object has no attribute 'Invoke'». Devuelve si pudo pulsarlo.
    """
    for obtener, accion in (("GetInvokePattern", "Invoke"), ("GetTogglePattern", "Toggle"),
                            ("GetLegacyIAccessiblePattern", "DoDefaultAction")):
        try:
            patron = getattr(control, obtener)()
        except Exception:
            continue
        if patron is None:
            continue
        try:
            getattr(patron, accion)()
            return True
        except Exception:
            logging.debug("El botón no aceptó %s.", accion, exc_info=True)
    return False


def clasificar_boton(nombre: str) -> str | None:
    """«play», «pause», «next», «previous» o None según el nombre del botón."""
    nombre = _normal(nombre)
    for clave, patron in (
        ("play", _BOTON_PLAY),
        ("pause", _BOTON_PAUSA),
        ("next", _BOTON_SIGUIENTE),
        ("previous", _BOTON_ANTERIOR),
        ("exit_fullscreen", _BOTON_SALIR_PANTALLA_COMPLETA),
        ("fullscreen", _BOTON_PANTALLA_COMPLETA),
        ("exit_theater", _BOTON_SALIR_MODO_TEATRO),
        ("theater", _BOTON_MODO_TEATRO),
    ):
        if patron.match(nombre):
            return clave
    return None


def _en_zona(rect, zona) -> bool:
    cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    return zona.x <= cx < zona.x + zona.ancho and zona.y <= cy < zona.y + zona.alto


def _reproductor_en_monitor(action: str, numero: int) -> str | None:
    """Pulsa el botón del reproductor de la pestaña ACTIVA del navegador que está
    en ese monitor (las pestañas de fondo no exponen su página). None si no hay."""
    import ctypes

    import uiautomation as auto

    import jarvis_vision

    zona = jarvis_vision.elegir_monitor(jarvis_vision.listar_monitores(), numero)
    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    with auto.UIAutomationInitializerInThread():
        for ventana in auto.GetRootControl().GetChildren():  # de arriba abajo
            if ventana.ClassName != "Chrome_WidgetWin_1" or not _en_zona(ventana.BoundingRectangle, zona):
                continue
            botones: dict[str, object] = {}
            for control, _ in auto.WalkControl(ventana, maxDepth=40):
                if control.ControlTypeName != "ButtonControl":
                    continue
                clave = clasificar_boton(control.Name)
                if clave is not None and clave not in botones:
                    botones[clave] = control
            if "play" not in botones and "pause" not in botones:
                continue
            reproduciendo = "pause" in botones
            if action == "play" and reproduciendo:
                return f"El vídeo del monitor {numero} ya se está reproduciendo."
            if action in {"pause", "stop"} and not reproduciendo:
                return f"El vídeo del monitor {numero} ya está en pausa."
            if action in {"next", "previous"}:
                boton = botones.get(action)
                if boton is None:
                    destino = "siguiente" if action == "next" else "anterior"
                    return f"El reproductor del monitor {numero} no tiene botón de vídeo {destino}."
            else:
                boton = botones["pause" if reproduciendo else "play"]
            if not _pulsar(boton):
                raise OSError(f"El reproductor del monitor {numero} no dejó pulsar su botón.")
            return {
                "play": "Reanudé el vídeo",
                "pause": "Pausé el vídeo",
                "stop": "Pausé el vídeo",
                "toggle": "Pausé el vídeo" if reproduciendo else "Reanudé el vídeo",
                "next": "Pasé al siguiente vídeo",
                "previous": "Volví al vídeo anterior",
            }[action] + f" del monitor {numero}."
    return None


def _botones_reproductor(zona, claves=("fullscreen", "exit_fullscreen")) -> list[dict[str, object]]:
    """Botones clasificados de cada ventana Chromium (de arriba abajo), opcionalmente de un
    monitor; solo las ventanas que tienen alguno de ``claves``."""
    import uiautomation as auto

    encontrados = []
    for ventana in auto.GetRootControl().GetChildren():
        if ventana.ClassName != "Chrome_WidgetWin_1":
            continue
        if zona is not None and not _en_zona(ventana.BoundingRectangle, zona):
            continue
        botones: dict[str, object] = {}
        for control, _ in auto.WalkControl(ventana, maxDepth=40):
            if control.ControlTypeName != "ButtonControl":
                continue
            clave = clasificar_boton(control.Name)
            if clave is not None and clave not in botones:
                botones[clave] = control
        if any(clave in botones for clave in claves):
            encontrados.append(botones)
    return encontrados


NAVEGADORES = ("brave", "chrome", "msedge", "firefox", "opera", "vivaldi")
VK_F, VK_ESCAPE, VK_T, VK_MENU = 0x46, 0x1B, 0x54, 0x12
ESPERA_CAMBIO = 1.2  # s que se espera a que el navegador entre o salga de pantalla completa


def _ventana_navegador(zona):
    """La ventana del navegador de más arriba (la del monitor pedido, si se indicó), o None.
    Nunca otras apps Chromium como Claude o VS Code."""
    import jarvis_windows

    user32 = ctypes.windll.user32
    for ventana in jarvis_windows.listar_ventanas():  # de arriba abajo
        if ventana.programa not in NAVEGADORES:
            continue
        if zona is not None:
            rect = wintypes.RECT()
            user32.GetWindowRect(ventana.hwnd, ctypes.byref(rect))
            if not _en_zona(rect, zona):
                continue
        return ventana
    return None


def _en_pantalla_completa(hwnd: int) -> bool:
    """Si la ventana cubre todo su monitor (así queda el navegador con un vídeo en pantalla completa)."""
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
        return False
    m = info.rcMonitor
    return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom


def _esperar_estado(hwnd: int, quiero: bool, comprobar, esperar: float) -> bool:
    fin = time.monotonic() + esperar
    while True:
        if comprobar(hwnd) == quiero:
            return True
        if time.monotonic() >= fin:
            return False
        time.sleep(0.15)


def _clic_real(control) -> bool:
    """Clic de verdad en el centro del botón (y el puntero vuelve a donde estaba).

    Los navegadores solo dejan entrar en pantalla completa con un gesto real del
    usuario: la pulsación por accesibilidad «funciona» pero no cuenta (Twitch)."""
    import jarvis_screen_actions as acciones

    rect = control.BoundingRectangle
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    antes = acciones._posicion_puntero(user32)
    try:
        acciones.hacer_clic((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    except OSError:
        logging.debug("No se pudo hacer clic en el botón del reproductor.", exc_info=True)
        return False
    finally:
        if antes is not None:
            try:
                acciones.mover_puntero(*antes)
            except OSError:
                pass
    return True


def _tecla_en(hwnd: int, vk: int, con_alt: bool = False) -> bool:
    """Una tecla (F, Esc, T, Alt+T) en la ventana del navegador (traída al frente): gesto real."""
    import jarvis_input
    import jarvis_windows

    if ctypes.windll.user32.GetForegroundWindow() != hwnd:
        jarvis_windows.enfocar(hwnd)
        time.sleep(0.3)
    try:
        teclas = [("tecla", vk, 0, 0), ("tecla", vk, 0, 2)]
        if con_alt:
            teclas = [("tecla", VK_MENU, 0, 0), *teclas, ("tecla", VK_MENU, 0, 2)]
        jarvis_input._send_input(teclas)
    except OSError:
        logging.debug("Windows no aceptó la tecla del reproductor.", exc_info=True)
        return False
    return True


def pantalla_completa(enabled: bool, monitor: int | None = None, *, espera: float = 1.5,
                      comprobar=None, esperar_cambio: float = ESPERA_CAMBIO) -> str:
    """Pone el vídeo en pantalla completa o lo saca, y comprueba que de verdad cambió.

    Intenta, de más suave a más directo, hasta que la ventana del navegador cubra el
    monitor (o deje de cubrirlo): 1) pulsar el botón del reproductor por
    accesibilidad (sin mover el ratón; vale en YouTube), 2) un clic real en ese
    botón (Twitch: el navegador exige un gesto real), 3) la tecla F o Esc. Si nada
    funciona, lo dice en vez de dar por hecho que funcionó.

    Sin ``monitor``, el primer reproductor de arriba abajo (la ventana de delante).
    Brave expone la página tras la primera consulta de accesibilidad: si no se ve
    ningún reproductor, se espera un momento y se vuelve a mirar.
    """
    import uiautomation as auto

    import jarvis_vision

    zona = jarvis_vision.elegir_monitor(jarvis_vision.listar_monitores(), monitor) if monitor else None
    donde = f" del monitor {monitor}" if monitor else ""
    comprobar = comprobar or _en_pantalla_completa
    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    with auto.UIAutomationInitializerInThread():
        reproductores = _botones_reproductor(zona)
        if not reproductores:
            time.sleep(max(0.0, espera))
            reproductores = _botones_reproductor(zona)
        ventana = _ventana_navegador(zona)
        botones = reproductores[0] if reproductores else {}
        if ventana is None and not botones:
            raise LookupError(f"No encontré ningún navegador abierto{donde} con un vídeo.")
        hwnd = ventana.hwnd if ventana is not None else None
        # Estado actual: la ventana manda; sin ventana, el nombre del botón.
        ahora = comprobar(hwnd) if hwnd is not None else "exit_fullscreen" in botones
        if ahora == enabled:
            return f"El vídeo{donde} ya está en pantalla completa." if enabled else \
                f"El vídeo{donde} no está en pantalla completa."
        boton = botones.get("fullscreen" if enabled else "exit_fullscreen")
        intentos = []
        if boton is not None:
            intentos.append(("botón", lambda: _pulsar(boton)))
            if enabled:
                intentos.append(("clic", lambda: _clic_real(boton)))
        if hwnd is not None:
            intentos.append(("tecla", lambda: _tecla_en(hwnd, VK_F if enabled else VK_ESCAPE)))
        hecho = "Puse el vídeo{} en pantalla completa." if enabled else "Saqué el vídeo{} de pantalla completa."
        for via, intentar in intentos:
            if not intentar():
                continue
            if hwnd is None:  # sin ventana que mirar: no se puede comprobar
                return hecho.format(donde)
            if _esperar_estado(hwnd, enabled, comprobar, esperar_cambio):
                logging.info("Pantalla completa %s con: %s.", "activada" if enabled else "quitada", via)
                return hecho.format(donde)
        tecla = "F" if enabled else "Esc"
        raise LookupError(
            f"No pude {'poner' if enabled else 'sacar'} el vídeo{donde} "
            f"{'en' if enabled else 'de'} pantalla completa: el reproductor no respondió al botón, "
            f"a un clic ni a la tecla {tecla}. Haz clic una vez en el vídeo y vuelve a pedírmelo."
        )


def _firma_teatro(botones: dict) -> tuple:
    """Lo que delata un cambio de modo teatro: el nombre del botón (YouTube pasa a
    «Vista predeterminada») o su posición (el reproductor cambia de tamaño)."""
    boton = botones.get("exit_theater") or botones.get("theater")
    rect = getattr(boton, "BoundingRectangle", None)
    lados = tuple(getattr(rect, lado, None) for lado in ("left", "top", "right", "bottom"))
    return "exit_theater" in botones, lados


def modo_teatro(enabled: bool, monitor: int | None = None, *, espera: float = 1.5,
                esperar_cambio: float = ESPERA_CAMBIO) -> str:
    """Activa o quita el modo teatro (modo cine) del reproductor de la pestaña activa.

    Igual que la pantalla completa, comprueba que algo cambió y escala: pulsar el
    botón por accesibilidad → clic real en él → la tecla del sitio (T en YouTube,
    Alt+T en Twitch, según el atajo que anuncia el botón).
    """
    import uiautomation as auto

    import jarvis_vision

    zona = jarvis_vision.elegir_monitor(jarvis_vision.listar_monitores(), monitor) if monitor else None
    donde = f" del monitor {monitor}" if monitor else ""
    claves = ("theater", "exit_theater")
    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    with auto.UIAutomationInitializerInThread():
        reproductores = _botones_reproductor(zona, claves)
        if not reproductores:
            time.sleep(max(0.0, espera))
            reproductores = _botones_reproductor(zona, claves)
        if not reproductores:
            raise LookupError(f"No encontré ningún reproductor{donde} con modo teatro (modo cine).")
        botones = reproductores[0]
        activo = "exit_theater" in botones
        if activo == enabled:
            return f"El vídeo{donde} ya está en modo teatro." if enabled else \
                f"El vídeo{donde} no está en modo teatro."
        boton = botones.get("exit_theater") or botones["theater"]
        con_alt = " alt " in f" {_normal(getattr(boton, 'Name', '') or '')} "
        ventana = _ventana_navegador(zona)
        antes = _firma_teatro(botones)

        def cambio() -> bool:
            fin = time.monotonic() + esperar_cambio
            while True:
                ahora = _botones_reproductor(zona, claves)
                if ahora and _firma_teatro(ahora[0]) != antes:
                    return True
                if time.monotonic() >= fin:
                    return False
                time.sleep(0.15)

        intentos = [("botón", lambda: _pulsar(boton)), ("clic", lambda: _clic_real(boton))]
        if ventana is not None:
            intentos.append(("tecla", lambda: _tecla_en(ventana.hwnd, VK_T, con_alt)))
        for via, intentar in intentos:
            if intentar() and cambio():
                logging.info("Modo teatro %s con: %s.", "activado" if enabled else "quitado", via)
                return f"Puse el vídeo{donde} en modo teatro." if enabled else \
                    f"Quité el modo teatro del vídeo{donde}."
        raise LookupError(
            f"No pude {'activar' if enabled else 'quitar'} el modo teatro{donde}: el reproductor no "
            "respondió al botón, a un clic ni a su tecla. Haz clic una vez en el vídeo y vuelve a pedírmelo."
        )


def controlar_reproduccion(
    action: str, monitor: int | None = None, *, tecla: Callable[[int], None] = _pulsar_tecla
) -> str:
    """``monitor``: actuar sobre lo que se reproduce en ese monitor (p. ej. el vídeo
    de la pestaña de Brave que está ahí) en vez de sobre la sesión actual."""
    if action not in ACCIONES:
        raise ValueError(f"Acción multimedia no reconocida: {action}")
    if monitor is not None:
        return _controlar_en_monitor(action, monitor)
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


def _controlar_en_monitor(action: str, numero: int) -> str:
    import jarvis_vision

    # 1) El reproductor de la pestaña activa del navegador de ese monitor.
    try:
        respuesta = _reproductor_en_monitor(action, numero)
    except ImportError:
        respuesta = None
    if respuesta is not None:
        logging.info("Reproducción en el monitor %s: botón del reproductor.", numero)
        return respuesta

    # 2) Otra aplicación (p. ej. Spotify) cuya sesión aparece en una ventana de ese monitor.
    titulos = titulos_en_monitor(numero)
    otros = [
        titulo
        for m in jarvis_vision.listar_monitores()
        if m.numero != numero
        for titulo in titulos_en_monitor(m.numero)
    ]

    async def actuar():
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Gestor,
        )

        gestor = await Gestor.request_async()
        sesiones = []
        for sesion in gestor.get_sessions():
            try:
                props = await sesion.try_get_media_properties_async()
                sesiones.append((sesion, props.title or ""))
            except OSError:
                sesiones.append((sesion, ""))
        elegida = elegir_sesion(sesiones, titulos, otros)
        if elegida is None:
            return None, len(sesiones)
        metodo = {
            "play": elegida.try_play_async,
            "pause": elegida.try_pause_async,
            "toggle": elegida.try_toggle_play_pause_async,
            "next": elegida.try_skip_next_async,
            "previous": elegida.try_skip_previous_async,
            "stop": elegida.try_stop_async,
        }[action]
        return (nombre_app(elegida.source_app_user_model_id or ""), bool(await metodo())), len(sesiones)

    resultado, cantidad = _ejecutar(actuar)
    # Sin textos en el log: solo cuántas sesiones y ventanas había.
    logging.info("Reproducción en el monitor %s: %s sesión(es), %s ventana(s).", numero, cantidad, len(titulos))
    if resultado is None:
        return (
            f"No encontré ningún vídeo ni reproductor en el monitor {numero}. "
            "Deja visible en ese monitor la pestaña del vídeo y vuelve a pedírmelo."
        )
    app, aceptado = resultado
    if not aceptado:
        raise OSError(f"{app} no admite esa acción ahora mismo.")
    return _FRASES[action].format(app=app) + f" (monitor {numero})"


# --- Silenciar una pestaña del navegador -----------------------------------------
# El botón de audio de la pestaña está deshabilitado en Brave/Chrome salvo con una
# opción experimental; se usa la opción «Silenciar pestaña» de su menú contextual
# (clic derecho sobre la pestaña; el puntero vuelve a su sitio).

MENU_SILENCIAR = "silenciar pestana"
MENU_ACTIVAR = ("activar sonido de la pestana", "dejar de silenciar", "reactivar sonido")
MAX_PESTANAS = 5
# Brave/Chrome añaden el estado al nombre accesible de la pestaña que suena:
# «Título: Reproducción de audio: Uso de memoria: …».
_SUENA = ("reproduccion de audio", "reproduciendo audio", "audio playing")
_SILENCIADA = ("audio silenciado", "audio muted")


def pestana_suena(nombre: str) -> bool:
    nombre = _normal(nombre)
    return any(p in nombre for p in _SUENA)


def elegir_pestanas(nombres: list[str], mute: bool, match: str = "") -> list[int]:
    """Índices de las pestañas (con indicador de audio) sobre las que actuar.
    Silenciar: solo las que suenan; si se nombró una («la de YouTube») y ninguna
    de esas suena, también las que coinciden aunque estén calladas.
    Devolver el sonido: todas (el menú dice cuáles están silenciadas)."""
    buscado = _normal(match)
    candidatas = [i for i, n in enumerate(nombres) if not buscado or buscado in _normal(n)]
    if not mute:
        # «Audio silenciado» solo aparece si la pestaña intenta sonar; si no hay
        # ninguna así, se prueban todas (el menú dice cuáles están silenciadas).
        calladas = [i for i in candidatas if any(p in _normal(nombres[i]) for p in _SILENCIADA)]
        return calladas or candidatas
    suenan = [i for i in candidatas if pestana_suena(nombres[i])]
    if suenan or not buscado:
        return suenan
    return candidatas


def _opcion_menu(nombre: str, mute: bool) -> bool:
    nombre = _normal(nombre)
    if mute:
        return nombre.startswith(MENU_SILENCIAR)
    return any(p in nombre for p in MENU_ACTIVAR)


def _clic_derecho(x: int, y: int) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    anterior = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(anterior))
    user32.SetCursorPos(x, y)
    time.sleep(0.05)
    user32.mouse_event(0x0008, 0, 0, 0, 0)  # botón derecho abajo
    user32.mouse_event(0x0010, 0, 0, 0, 0)  # botón derecho arriba
    user32.SetCursorPos(anterior.x, anterior.y)


def silenciar_pestana(mute: bool = True, monitor: int | None = None, match: str = "") -> str:
    """Silencia (o devuelve el sonido a) las pestañas de Brave/Chrome que suenan
    (o están silenciadas), opcionalmente solo las de un monitor o cuyo título
    contiene ``match``."""
    import ctypes

    import uiautomation as auto

    import jarvis_vision

    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    zona = None
    if monitor is not None:
        zona = jarvis_vision.elegir_monitor(jarvis_vision.listar_monitores(), monitor)
    hechas = 0
    with auto.UIAutomationInitializerInThread():
        pestanas = []
        for ventana in auto.GetRootControl().GetChildren():
            if ventana.ClassName != "Chrome_WidgetWin_1":
                continue
            if zona is not None:
                r = ventana.BoundingRectangle
                cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                if not (zona.x <= cx < zona.x + zona.ancho and zona.y <= cy < zona.y + zona.alto):
                    continue
            for control, _ in auto.WalkControl(ventana, maxDepth=12):
                if control.ControlTypeName != "TabItemControl":
                    continue
                hijos = control.GetChildren()
                indicador = hijos[0] if hijos and hijos[0].ControlTypeName == "ButtonControl" else None
                # El indicador solo se ve en pestañas que suenan o están silenciadas.
                # Su nombre no cambia al silenciar: el estado se lee del menú.
                if indicador is None or indicador.BoundingRectangle.width() <= 0:
                    continue
                pestanas.append(control)
        elegidas = elegir_pestanas([p.Name for p in pestanas], mute, match)
        pestanas = [pestanas[i] for i in elegidas]
        for pestana in pestanas[:MAX_PESTANAS]:
            caja = pestana.BoundingRectangle
            _clic_derecho((caja.left + caja.right) // 2, (caja.top + caja.bottom) // 2)
            opcion = None
            limite = time.monotonic() + 1.5
            while opcion is None and time.monotonic() < limite:
                time.sleep(0.15)
                for ventana in auto.GetRootControl().GetChildren():
                    if ventana.ClassName != "Chrome_WidgetWin_1":
                        continue
                    for control, _ in auto.WalkControl(ventana, maxDepth=8):
                        if control.ControlTypeName == "MenuItemControl" and _opcion_menu(control.Name, mute):
                            opcion = control
                            break
                    if opcion is not None:
                        break
            if opcion is None:
                auto.SendKeys("{Esc}")  # ya estaba como se pide (o no hay menú)
                continue
            if not _pulsar(opcion):
                auto.SendKeys("{Esc}")
                continue
            hechas += 1
            time.sleep(0.2)
    logging.info("Pestañas %s: %s.", "silenciadas" if mute else "con sonido", hechas)
    donde = f" en el monitor {monitor}" if monitor is not None else ""
    if not hechas:
        if mute:
            return f"No encontré ninguna pestaña que esté sonando{donde}."
        return f"No encontré ninguna pestaña silenciada{donde}."
    plural = "s" if hechas > 1 else ""
    if mute:
        return f"Silencié {hechas} pestaña{plural}{donde}."
    return f"Le devolví el sonido a {hechas} pestaña{plural}{donde}."


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


def aplicaciones_con_audio(*, sesiones: Callable[[], list] | None = None) -> list[dict]:
    """Apps con sesión de audio: [{app, name, volume, muted}] (una por ejecutable)."""

    def leer() -> list[dict]:
        apps: dict[str, dict] = {}
        for sesion in (sesiones or _sesiones_audio)():
            ejecutable = sesion.Process.name()
            clave = _normalizar(ejecutable).removesuffix(".exe")
            if clave in apps:
                continue
            control = sesion.SimpleAudioVolume
            apps[clave] = {
                "app": clave,
                "name": nombre_app(ejecutable),
                "volume": round(control.GetMasterVolume() * 100),
                "muted": bool(control.GetMute()),
            }
        return sorted(apps.values(), key=lambda a: a["name"].casefold())

    return leer() if sesiones is not None else _con_com(leer)


def leer_volumen_aplicacion(app: str) -> int | None:
    buscado = resolver_app(app)

    def leer() -> int | None:
        for sesion in _sesiones_audio():
            if buscado in _normalizar(sesion.Process.name()):
                return round(sesion.SimpleAudioVolume.GetMasterVolume() * 100)
        return None

    return _con_com(leer)


# --- Frases ------------------------------------------------------------------------

_COSA = (
    r"(?:\s+(?:al\s+|a\s+la\s+|con\s+el\s+|con\s+la\s+|la\s+|el\s+|este\s+|esta\s+|ese\s+|esa\s+)?"
    r"(?:musica|cancion|tema|reproduccion|video|videos|spotify|youtube|serie|pelicula|peli"
    r"|podcast|capitulo|episodio|stream|directo))?"
)
_PAUSA = re.compile(
    rf"^(?:pausa|pausar|pausalo|pausala|pon(?:le)?\s+(?:en\s+)?pausa|deja\s+en\s+pausa|deten|detener"
    rf"|detenlo|detenla|para|parar|paralo|parala|frena|frenalo|frenala|stop)(?:\s+la)?{_COSA}$"
)
_REANUDA = re.compile(
    rf"^(?:reanuda|reanudar|reanudalo|reanudala|continua|continuar|sigue|seguir|play"
    rf"|dale\s+(?:al\s+)?play|pon(?:le)?\s+play|quita(?:le)?\s+(?:la\s+)?pausa|reproduce|reproducir"
    rf"|despausa|despausalo|despausala|vuelve\s+a\s+(?:reproducir|poner)|resume){_COSA}$"
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


_EN_MONITOR = re.compile(
    r"\s+(?:del|de\s+la|en\s+el|en\s+la|de|en)\s+"
    r"(?:(?:monitor|pantalla)\s+(?:numero\s+)?(?P<n>\d|uno|dos|tres|cuatro)"
    r"|(?P<ord>primer|primera|segundo|segunda|tercer|tercera|cuarto|cuarta)\s+(?:monitor|pantalla))$"
)
_NUMEROS_MONITOR = {
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "primer": 1, "primera": 1, "segundo": 2, "segunda": 2,
    "tercer": 3, "tercera": 3, "cuarto": 4, "cuarta": 4,
}
_PESTANA = r"(?:la\s+|las\s+|esta\s+|esa\s+|una\s+)?(?:pestana|pestanas|tab|tabs)"
# «el vídeo», «YouTube»: la pestaña donde está (no el volumen de toda la app).
_VIDEO = r"(?:(?:el\s+|este\s+|ese\s+)?video|youtube)"
_DE_PESTANA = r"(?:\s+(?:de|del)\s+(?P<match>[a-z0-9 ]+?))?"
_QUE_SUENA_PESTANA = r"(?:\s+que\s+(?:suena|esta\s+sonando|tiene\s+sonido|hace\s+ruido|tiene\s+audio))?"
_VERBO_SILENCIAR = (
    r"(?:silencia|silenciar|silenciame|mutea|mutear|muteame|calla|callar|callame"
    r"|pon(?:le)?\s+(?:en\s+)?silencio(?:\s+al?)?|pon(?:le)?\s+mute(?:\s+al?)?"
    r"|quita(?:le|r)?\s+el\s+(?:sonido|audio)\s+(?:a|al|de|del))"
)
_VERBO_ACTIVAR = (
    r"(?:(?:quita|quitale|quitar)\s+el\s+(?:silencio|mute)\s+(?:a|al|de|del)"
    r"|(?:activa|activar|activale|devuelve|devuelvele|reactiva|dale|ponle|vuelve\s+a\s+activar)"
    r"\s+el\s+(?:sonido|audio)\s+(?:a|al|de|del)|desmutea|desmutear|desmuteame|desilencia|desilenciar)"
)
_SILENCIAR_PESTANA = re.compile(
    rf"^{_VERBO_SILENCIAR}\s+{_PESTANA}{_DE_PESTANA}{_QUE_SUENA_PESTANA}$"
    rf"|^{_VERBO_SILENCIAR}\s+(?P<video>{_VIDEO})$"
)
_ACTIVAR_PESTANA = re.compile(
    rf"^{_VERBO_ACTIVAR}\s+{_PESTANA}{_DE_PESTANA}$|^{_VERBO_ACTIVAR}\s+(?P<video>{_VIDEO})$"
)


_PANTALLA_COMPLETA = r"(?:la\s+)?pantalla\s+completa"
_PONER_PANTALLA_COMPLETA = re.compile(
    rf"^(?:pon|ponme|poner|ponlo|ponle|haz|hace|deja|dejalo|mete|abre)\s+(?:{_VIDEO}\s+)?(?:en\s+|a\s+)?{_PANTALLA_COMPLETA}"
    rf"(?:\s+(?:a|al|el|en\s+el)\s+{_VIDEO})?$"
    rf"|^{_PANTALLA_COMPLETA}(?:\s+(?:al|el|en\s+el)\s+{_VIDEO})?$"
    rf"|^(?:agranda|agrandar|amplia|ampliar|maximiza|maximizar)\s+{_VIDEO}$"
)
_QUITAR_PANTALLA_COMPLETA = re.compile(
    rf"^(?:sal|salir|salte|sali|quita|quitar|quitale|saca|sacar|sacalo|desactiva|cierra)\s+(?:de\s+)?{_PANTALLA_COMPLETA}"
    rf"(?:\s+(?:a|al|del|de|en\s+el)\s+{_VIDEO})?$"
    rf"|^(?:saca|sacar|sacalo)\s+{_VIDEO}\s+de\s+{_PANTALLA_COMPLETA}$"
    rf"|^(?:achica|achicar|reduce|reducir|minimiza|minimizar)\s+{_VIDEO}$"
    rf"|^(?:vuelve|volve|deja|pon)\s+{_VIDEO}\s+(?:a|al|en)\s+(?:tamano\s+normal|modo\s+normal|normal)$"
)


def _separar_monitor(consulta: str) -> tuple[str, int | None]:
    m = _EN_MONITOR.search(consulta)
    if m is None:
        return consulta, None
    valor = m.group("n") or m.group("ord")
    numero = int(valor) if valor.isdigit() else _NUMEROS_MONITOR[valor]
    return consulta[: m.start()], numero

_MODO_TEATRO = r"(?:el\s+)?modo\s+(?:teatro|cine)"
_PONER_MODO_TEATRO = re.compile(
    rf"^(?:pon|ponme|poner|ponlo|ponle|activa|activar|activame|deja|dejalo|cambia|cambiar)\s+"
    rf"(?:{_VIDEO}\s+)?(?:en\s+|a\s+|al\s+)?{_MODO_TEATRO}(?:\s+(?:a|al|del|en\s+el)\s+{_VIDEO})?$"
    rf"|^{_MODO_TEATRO}(?:\s+(?:al|del|en\s+el)\s+{_VIDEO})?$"
)
_QUITAR_MODO_TEATRO = re.compile(
    rf"^(?:sal|salir|salte|sali|quita|quitar|quitale|saca|sacar|sacalo|desactiva|desactivar|cierra)\s+"
    rf"(?:(?:de|del)\s+)?(?:{_VIDEO}\s+(?:de|del)\s+)?{_MODO_TEATRO}(?:\s+(?:a|al|del|de|en\s+el)\s+{_VIDEO})?$"
)


def _argumentos_pestana(m: re.Match, mute: bool, extra: dict) -> dict[str, object]:
    match = m.group("match") or ("youtube" if m.group("video") == "youtube" else "")
    return {"mute": mute, **extra, **({"match": match} if match else {})}


# «pausa el vídeo del monitor 1 y reanuda el del monitor 2»: varias órdenes seguidas.
_UNION = re.compile(r"\s+(?:y\s+(?:luego\s+|despues\s+|tambien\s+)?|luego\s+|despues\s+)")
_PASOS_SECUENCIA = {"media_control", "mute_browser_tab", "video_fullscreen", "video_theater"}
MAX_PASOS = 4


def detectar_secuencia(consulta: str) -> list[dict[str, object]] | None:
    partes = _UNION.split(consulta)
    if not 2 <= len(partes) <= MAX_PASOS:
        return None
    pasos = []
    for parte in partes:
        comando = _detectar_uno(parte.strip())
        if comando is None or comando[0] not in _PASOS_SECUENCIA:
            return None
        pasos.append({"tool": comando[0], "arguments": comando[1]})
    return pasos


def ejecutar_secuencia(steps: list[dict[str, object]]) -> str:
    """Ejecuta en orden pasos multimedia de riesgo bajo (órdenes compuestas)."""
    funciones = {"media_control": controlar_reproduccion, "mute_browser_tab": silenciar_pestana,
                 "video_fullscreen": pantalla_completa, "video_theater": modo_teatro}
    if not 1 <= len(steps) <= MAX_PASOS:
        raise ValueError(f"Indica entre 1 y {MAX_PASOS} pasos.")
    resultados = []
    for paso in steps:
        funcion = funciones.get(str(paso.get("tool")))
        argumentos = paso.get("arguments") or {}
        if funcion is None or not isinstance(argumentos, dict):
            raise ValueError(f"Paso no permitido: {paso.get('tool')}")
        try:
            resultados.append(funcion(**argumentos))
        except (OSError, ValueError, LookupError) as error:
            resultados.append(f"No pude: {error}")
    return " ".join(resultados)


def detectar_comando(consulta: str) -> tuple[str, dict[str, object]] | None:
    """``consulta`` ya normalizada (sin tildes ni signos, ver jarvis_commands)."""
    if (pasos := detectar_secuencia(consulta)) is not None:
        return "media_sequence", {"steps": pasos}
    return _detectar_uno(consulta)


def _detectar_uno(consulta: str) -> tuple[str, dict[str, object]] | None:
    sin_monitor, monitor = _separar_monitor(consulta)
    extra = {"monitor": monitor} if monitor is not None else {}
    if monitor is not None:
        # «… y reanuda el del monitor 2»: «el»/«la» sin sustantivo.
        sin_monitor = re.sub(r"\s+(?:el|la|lo)$", "", sin_monitor)
    if (m := _SILENCIAR_PESTANA.fullmatch(sin_monitor)) is not None:
        return "mute_browser_tab", _argumentos_pestana(m, True, extra)
    if (m := _ACTIVAR_PESTANA.fullmatch(sin_monitor)) is not None:
        return "mute_browser_tab", _argumentos_pestana(m, False, extra)
    if _QUITAR_MODO_TEATRO.fullmatch(sin_monitor):
        return "video_theater", {"enabled": False, **extra}
    if _PONER_MODO_TEATRO.fullmatch(sin_monitor):
        return "video_theater", {"enabled": True, **extra}
    if _QUITAR_PANTALLA_COMPLETA.fullmatch(sin_monitor):
        return "video_fullscreen", {"enabled": False, **extra}
    if _PONER_PANTALLA_COMPLETA.fullmatch(sin_monitor):
        return "video_fullscreen", {"enabled": True, **extra}
    if _PAUSA.fullmatch(sin_monitor):
        return "media_control", {"action": "pause", **extra}
    if _REANUDA.fullmatch(sin_monitor):
        return "media_control", {"action": "play", **extra}
    if _SIGUIENTE.fullmatch(sin_monitor):
        return "media_control", {"action": "next", **extra}
    if _ANTERIOR.fullmatch(sin_monitor):
        return "media_control", {"action": "previous", **extra}
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
        "mute_browser_tab",
        (
            "Silencia (mute=true) o quita el silencio (mute=false) de pestañas de Brave/Chrome "
            "sin afectar al resto: las que suenan, las de un monitor (monitor) o las cuyo "
            "título contiene match (p. ej. youtube)."
        ),
        {
            "type": "object",
            "properties": {
                "mute": {"type": "boolean"},
                "monitor": {"type": "integer", "minimum": 1, "maximum": 9},
                "match": {"type": "string", "maxLength": 80},
            },
            "required": [],
            "additionalProperties": False,
        },
        silenciar_pestana,
        label="Silenciar pestaña",
        timeout_seconds=15,
    )
    registry.register(
        "media_sequence",
        "Varias órdenes multimedia seguidas («pausa el vídeo del monitor 1 y reanuda el del 2»).",
        {
            "type": "object",
            "properties": {"steps": {"type": "array"}},
            "required": ["steps"],
            "additionalProperties": False,
        },
        ejecutar_secuencia,
        label="Órdenes multimedia",
        timeout_seconds=45,
        # Solo lo usan las frases directas; el modelo ya tiene las herramientas sueltas.
        exposed=False,
    )
    registry.register(
        "media_control",
        (
            "Controla lo que se reproduce en Windows (Spotify, YouTube en el navegador…): "
            "play, pause, toggle, next, previous o stop. monitor (opcional): actuar sobre "
            "lo que se reproduce en ese monitor."
        ),
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACCIONES)},
                "monitor": {"type": "integer", "minimum": 1, "maximum": 9},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        controlar_reproduccion,
        label="Controlar reproducción",
        timeout_seconds=10,
        verifier=verificadores.get("media_control"),
    )
    registry.register(
        "video_fullscreen",
        (
            "Pone en pantalla completa (enabled=true) o saca de ella (enabled=false) el vídeo "
            "de YouTube u otro reproductor de la pestaña activa de Brave/Chrome; monitor opcional."
        ),
        {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "monitor": {"type": "integer", "minimum": 1, "maximum": 9},
            },
            "required": ["enabled"],
            "additionalProperties": False,
        },
        pantalla_completa,
        label="Pantalla completa del vídeo",
        timeout_seconds=20,
    )
    registry.register(
        "video_theater",
        (
            "Activa (enabled=true) o quita (enabled=false) el modo teatro / modo cine del "
            "reproductor de la pestaña activa del navegador (YouTube, Twitch…); monitor opcional."
        ),
        {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "monitor": {"type": "integer", "minimum": 1, "maximum": 9},
            },
            "required": ["enabled"],
            "additionalProperties": False,
        },
        modo_teatro,
        label="Modo teatro del vídeo",
        timeout_seconds=20,
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
