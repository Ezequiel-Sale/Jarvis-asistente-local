"""Comprobaciones posteriores de las herramientas de Jarvis.

Cada verificador recibe los argumentos validados y el resultado del handler y
devuelve una ``Verificacion``. Solo se conectan en la app real
(``crear_verificadores``); los tests usan registros sin verificadores para no
tocar el sistema.

Sin verificador a propósito:
- ``set_output_device``: el handler ya comprueba la salida predeterminada.
- ``launch_game``: muchos juegos arrancan mediante lanzadores que terminan
  enseguida, así que comprobar el proceso daría falsos negativos.
- ``manage_window``: depende del estado de Tk o de ventanas ajenas.
- ``power_control``, ``cancel_power_action`` y ``lock_computer``: el handler ya
  comprueba el código de Windows, y después de apagar, suspender o bloquear
  no hay nada que Jarvis pueda comprobar.
"""

import time
from collections.abc import Callable, Mapping
from typing import Protocol

import jarvis_displays
import jarvis_media
import jarvis_vision
import jarvis_reminders
import jarvis_routines
from jarvis_actions import Verificacion, Verificador
from jarvis_audio_devices import obtener_volumen_sistema
from jarvis_system import encontrar_brave, obtener_ventanas_brave

TOLERANCIA_VOLUMEN = 1
ESPERA_VENTANA_BRAVE = 5.0


def verificar_barras_de_tareas(
    _arguments: Mapping[str, object],
    _result: object,
    *,
    espera: float = 1.5,
) -> Verificacion:
    """Tras reajustar el escalado, cada barra de tareas debe quedar pegada a un borde."""
    time.sleep(espera)  # Explorer recoloca la barra unos instantes después
    try:
        monitores = [(m.x, m.y, m.x + m.ancho, m.y + m.alto) for m in jarvis_vision.listar_monitores()]
        barras = jarvis_displays.barras_de_tareas()
    except OSError:
        return Verificacion(None, "No pude leer la posición de las barras de tareas.")
    if not barras:
        return Verificacion(None, "No encontré barras de tareas visibles (¿ocultas automáticamente?).")
    mal = [b for b in barras if not jarvis_displays.barra_bien_ubicada(b, monitores)]
    if mal:
        return Verificacion(False, f"{len(mal)} barra(s) de tareas siguen fuera de su sitio.")
    return Verificacion(True, "Todas las barras de tareas están en su borde.")


class VerifierHost(Protocol):
    audio_enabled: bool


def verificar_volumen(
    arguments: Mapping[str, object],
    _result: object,
    *,
    leer_volumen: Callable[[], int] = obtener_volumen_sistema,
) -> Verificacion:
    esperado = int(arguments["percentage"])  # type: ignore[arg-type]
    actual = leer_volumen()
    if abs(actual - esperado) <= TOLERANCIA_VOLUMEN:
        return Verificacion(True, f"Windows reporta el volumen en {actual}%.")
    return Verificacion(False, f"se pidió {esperado}%, pero Windows reporta {actual}%.")


def verificar_ventana_brave(
    _arguments: Mapping[str, object],
    _result: object,
    *,
    espera: float = ESPERA_VENTANA_BRAVE,
) -> Verificacion:
    """Comprobación básica: Brave tiene al menos una ventana visible."""
    brave = encontrar_brave()
    if brave is None:
        return Verificacion(None, "No encontré Brave para comprobar la ventana.")
    deadline = time.monotonic() + espera
    while True:
        ventanas = obtener_ventanas_brave(brave)
        if ventanas:
            return Verificacion(True, f"Brave tiene {len(ventanas)} ventana(s) abierta(s).")
        if time.monotonic() >= deadline:
            return Verificacion(False, "no encontré ninguna ventana de Brave abierta.")
        time.sleep(0.5)


def verificar_rutina_guardada(arguments: Mapping[str, object], _result: object) -> Verificacion:
    nombre = str(arguments["name"]).casefold()
    for identifier, name, time_value, _days, active in jarvis_routines.listar_rutinas():
        if name.casefold() == nombre:
            estado = "activa" if active else "pausada"
            cuando = f"a las {time_value}" if time_value else "para su evento"
            return Verificacion(
                True,
                f"La rutina {identifier} quedó guardada {cuando} ({estado}).",
            )
    return Verificacion(False, "la rutina no aparece en la base de datos.")


def verificar_aviso_guardado(_arguments: Mapping[str, object], _result: object) -> Verificacion:
    """El aviso recién creado aparece como pendiente en la base de datos."""
    from datetime import datetime, timedelta

    hace_poco = datetime.now() - timedelta(seconds=30)
    for aviso in jarvis_reminders.pendientes():
        if aviso.creado is not None and aviso.creado >= hace_poco:
            return Verificacion(True, f"El aviso {aviso.id} quedó guardado como pendiente.")
    return Verificacion(False, "el aviso no aparece como pendiente en la base de datos.")


def verificar_reproduccion(
    arguments: Mapping[str, object],
    _result: object,
    *,
    leer: Callable[[], object] = jarvis_media.sesion_actual,
    espera: float = 1.0,
) -> Verificacion:
    """Tras pausar o reanudar, Windows debe informar ese estado."""
    esperado = {"pause": "pausado", "play": "reproduciendo"}.get(str(arguments.get("action")))
    if esperado is None:
        return Verificacion(None, "No se comprueba el salto de pista.")
    deadline = time.monotonic() + espera
    while True:
        sesion = leer()
        if sesion is None:
            return Verificacion(None, "No hay sesión multimedia para comprobar.")
        if sesion.estado == esperado:
            return Verificacion(True, f"{sesion.app} está {esperado}.")
        if time.monotonic() >= deadline:
            return Verificacion(False, f"{sesion.app} sigue {sesion.estado}.")
        time.sleep(0.2)


def verificar_volumen_app(
    arguments: Mapping[str, object],
    result: object,
    *,
    leer: Callable[[str], int | None] = jarvis_media.leer_volumen_aplicacion,
) -> Verificacion:
    if arguments.get("percentage") is None or str(result).startswith("No encuentro"):
        return Verificacion(None, "Nada que comprobar.")
    esperado = int(arguments["percentage"])  # type: ignore[arg-type]
    actual = leer(str(arguments["app"]))
    if actual is None:
        return Verificacion(None, "La aplicación ya no tiene audio.")
    if abs(actual - esperado) <= TOLERANCIA_VOLUMEN:
        return Verificacion(True, f"Windows reporta el volumen de la aplicación en {actual}%.")
    return Verificacion(False, f"se pidió {esperado}%, pero Windows reporta {actual}%.")


def crear_verificadores(host: VerifierHost) -> dict[str, Verificador]:
    def verificar_audio_jarvis(arguments: Mapping[str, object], _result: object) -> Verificacion:
        esperado = bool(arguments["enabled"])
        if host.audio_enabled == esperado:
            return Verificacion(
                True,
                "El audio de Jarvis está " + ("activado." if esperado else "silenciado."),
            )
        return Verificacion(False, "el estado del audio de Jarvis no cambió.")

    def verificar_tema(arguments: Mapping[str, object], _result: object) -> Verificacion:
        import jarvis_theme

        if jarvis_theme.tema_actual() == arguments["theme"]:
            return Verificacion(True, f"Tema activo: {jarvis_theme.ETIQUETAS[jarvis_theme.tema_actual()]}.")
        return Verificacion(False, "el tema no cambió.")

    return {
        "set_theme": verificar_tema,
        "set_system_volume": verificar_volumen,
        "set_jarvis_audio": verificar_audio_jarvis,
        "open_new_browser_tab": verificar_ventana_brave,
        "navigate_browser": verificar_ventana_brave,
        "search_youtube": verificar_ventana_brave,
        "schedule_routine": verificar_rutina_guardada,
        "set_reminder": verificar_aviso_guardado,
        "media_control": verificar_reproduccion,
        "set_app_volume": verificar_volumen_app,
        "fix_display_scaling": verificar_barras_de_tareas,
    }
