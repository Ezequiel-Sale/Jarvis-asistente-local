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

import jarvis_routines
from jarvis_actions import Verificacion, Verificador
from jarvis_audio_devices import obtener_volumen_sistema
from jarvis_system import encontrar_brave, obtener_ventanas_brave

TOLERANCIA_VOLUMEN = 1
ESPERA_VENTANA_BRAVE = 5.0


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


def crear_verificadores(host: VerifierHost) -> dict[str, Verificador]:
    def verificar_audio_jarvis(arguments: Mapping[str, object], _result: object) -> Verificacion:
        esperado = bool(arguments["enabled"])
        if host.audio_enabled == esperado:
            return Verificacion(
                True,
                "El audio de Jarvis está " + ("activado." if esperado else "silenciado."),
            )
        return Verificacion(False, "el estado del audio de Jarvis no cambió.")

    return {
        "set_system_volume": verificar_volumen,
        "set_jarvis_audio": verificar_audio_jarvis,
        "open_new_browser_tab": verificar_ventana_brave,
        "navigate_browser": verificar_ventana_brave,
        "search_youtube": verificar_ventana_brave,
        "schedule_routine": verificar_rutina_guardada,
    }
