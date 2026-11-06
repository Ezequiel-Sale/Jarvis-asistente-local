"""Control de energía del equipo: apagar, reiniciar, suspender y bloquear.

- Apagar y reiniciar se programan con ``shutdown.exe`` y una espera
  (``ESPERA_APAGADO`` s) para que el usuario pueda cancelarlos con
  «cancela el apagado» (``shutdown /a``) y Windows muestre el aviso.
- Suspender llama a ``SetSuspendState`` tras una breve espera, en otro hilo,
  porque la llamada no vuelve hasta que el equipo despierta.
- Bloquear usa ``LockWorkStation``; no cierra nada.

Las funciones reciben sus dependencias del sistema como parámetros para
poder probarlas sin apagar el equipo.
"""

import logging
import os
import subprocess
import threading
from collections.abc import Callable

ESPERA_APAGADO = 10
ESPERA_SUSPENSION = 3.0

ACCIONES_ENERGIA = ("shutdown", "restart", "sleep")

# Códigos de shutdown.exe (errores de Windows).
_SIN_APAGADO_PENDIENTE = 1116
_APAGADO_YA_PROGRAMADO = 1190

Ejecutor = Callable[[list[str]], subprocess.CompletedProcess]


def _ejecutar_shutdown(argumentos: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["shutdown", *argumentos],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _solo_windows() -> None:
    if os.name != "nt":
        raise OSError("El control de energía solo está disponible en Windows.")


def programar_apagado(
    reiniciar: bool,
    *,
    segundos: int = ESPERA_APAGADO,
    ejecutar: Ejecutor = _ejecutar_shutdown,
) -> str:
    _solo_windows()
    accion = "reinicio" if reiniciar else "apagado"
    aviso = (
        f"Jarvis: {accion} en {segundos} segundos. "
        f"Di «cancela el {accion}» para detenerlo."
    )
    resultado = ejecutar(["/r" if reiniciar else "/s", "/t", str(segundos), "/c", aviso])
    if resultado.returncode == _APAGADO_YA_PROGRAMADO:
        raise OSError(
            "Ya hay un apagado o reinicio programado. Di «cancela el apagado» si quieres detenerlo."
        )
    if resultado.returncode != 0:
        raise OSError(f"shutdown.exe devolvió el código {resultado.returncode}.")
    logging.info("%s del equipo programado en %s s.", accion.capitalize(), segundos)
    verbo = "Reiniciaré" if reiniciar else "Apagaré"
    return (
        f"{verbo} el equipo en {segundos} segundos. "
        f"Di «cancela el {accion}» si quieres detenerlo."
    )


def cancelar_apagado(*, ejecutar: Ejecutor = _ejecutar_shutdown) -> str:
    _solo_windows()
    resultado = ejecutar(["/a"])
    if resultado.returncode == _SIN_APAGADO_PENDIENTE:
        return "No había ningún apagado ni reinicio programado."
    if resultado.returncode != 0:
        raise OSError(f"shutdown.exe devolvió el código {resultado.returncode}.")
    logging.info("Apagado o reinicio del equipo cancelado.")
    return "Cancelé el apagado programado."


def _suspender_ahora() -> None:
    import ctypes

    # SetSuspendState(hibernar=False, forzar=False, desactivar_eventos_activacion=False)
    if not ctypes.WinDLL("powrprof").SetSuspendState(False, False, False):
        logging.error("Windows no pudo suspender el equipo.")


def suspender_equipo(
    *,
    espera: float = ESPERA_SUSPENSION,
    suspender: Callable[[], None] = _suspender_ahora,
) -> str:
    _solo_windows()
    temporizador = threading.Timer(espera, suspender)
    temporizador.name = "JarvisSuspension"
    temporizador.daemon = True
    temporizador.start()
    logging.info("Suspensión del equipo en %s s.", espera)
    return f"Suspenderé el equipo en {int(espera)} segundos."


def _bloquear_ahora() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.LockWorkStation())


def bloquear_equipo(*, bloquear: Callable[[], bool] = _bloquear_ahora) -> str:
    _solo_windows()
    if not bloquear():
        raise OSError("Windows no pudo bloquear el equipo.")
    logging.info("Equipo bloqueado.")
    return "Bloqueé el equipo."


def ejecutar_accion_energia(action: str) -> str:
    if action == "shutdown":
        return programar_apagado(reiniciar=False)
    if action == "restart":
        return programar_apagado(reiniciar=True)
    if action == "sleep":
        return suspender_equipo()
    raise ValueError(f"Acción de energía no reconocida: {action}")


PREGUNTAS_CONFIRMACION = {
    "shutdown": f"¿Apago el equipo? Tendrás {ESPERA_APAGADO} segundos para cancelarlo.",
    "restart": f"¿Reinicio el equipo? Tendrás {ESPERA_APAGADO} segundos para cancelarlo.",
    "sleep": "¿Suspendo el equipo?",
}


# Rutinas que apagan/reinician/suspenden: aviso antes de actuar (nadie suele
# estar delante para aprobarlas; si nadie cancela, se ejecutan).
CUENTA_ATRAS_RUTINA = 60
VERBOS_RUTINA = {"shutdown": "apagaré", "restart": "reiniciaré", "sleep": "suspenderé"}


def describir_cuenta_atras(action: str, segundos: int) -> str:
    verbo = VERBOS_RUTINA.get(action, "apagaré")
    return f"{verbo.capitalize()} el equipo en {segundos} s."
