"""ADB (depuración de Android por la red) para abrir cualquier app de la tele y listarlas.

El protocolo del control (androidtvremote2) solo abre apps con enlace web (Netflix, YouTube…):
la TCL del usuario rechaza ``market://launch``, ``intent:`` y ``android-app://`` (comprobado el
2026-11-13 con una app instalada fuera de Play Store y con YouTube). Con ADB sí:
``monkey -p <paquete> -c LEANBACK_LAUNCHER 1`` la abre y ``cmd package query-activities``
lista las apps que se pueden abrir.

- ``vendor/adb``: adb.exe 37.0.1 de Google (platform-tools, Apache 2.0, ver NOTICE.txt).
- En la tele: Opciones para desarrolladores → «Depuración por USB» (en las TCL también deja
  conectarse por la red, puerto 5555). La primera vez la tele pregunta si permite esta PC; la
  clave queda en ``%USERPROFILE%\\.android\\adbkey``.
- Todo con tiempo máximo y sin ventana; si falta adb o la tele no lo autorizó, se informa y el
  resto del control de la tele sigue igual.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

PUERTO = 5555
TIEMPO = 8.0
CONECTADO, SIN_AUTORIZAR, SIN_RESPUESTA, SIN_ADB = "conectado", "sin_autorizar", "sin_respuesta", "sin_adb"


def ruta_adb() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "vendor" / "adb" / "adb.exe"


def _ejecutar(*argumentos: str, tiempo: float = TIEMPO) -> str:
    adb = ruta_adb()
    if not adb.is_file():
        raise FileNotFoundError(f"falta {adb}")
    resultado = subprocess.run(
        [str(adb), *argumentos], capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=tiempo, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return (resultado.stdout or "") + (resultado.stderr or "")


def interpretar_devices(salida: str, destino: str) -> str:
    """Estado de ``destino`` (ip:puerto) en la salida de ``adb devices``."""
    for linea in salida.splitlines():
        partes = linea.split()
        if len(partes) >= 2 and partes[0] == destino:
            return {"device": CONECTADO, "unauthorized": SIN_AUTORIZAR}.get(partes[1], SIN_RESPUESTA)
    return SIN_RESPUESTA


def interpretar_actividades(salida: str) -> list[str]:
    """Paquetes de ``cmd package query-activities --brief --components`` (paquete/actividad)."""
    paquetes = []
    for linea in salida.splitlines():
        coincidencia = re.fullmatch(r"\s*([A-Za-z]\w*(?:\.\w+)+)/\S+\s*", linea)
        if coincidencia and coincidencia.group(1) not in paquetes:
            paquetes.append(coincidencia.group(1))
    return paquetes


class ADB:
    """ADB contra una tele (``host``). Los métodos bloquean: llamarlos fuera del hilo de Tk."""

    def __init__(self, ejecutar=None) -> None:
        self._ejecutar = ejecutar or _ejecutar
        self.estado = ""

    def conectar(self, host: str) -> str:
        destino = f"{host}:{PUERTO}"
        try:
            self._ejecutar("connect", destino)
            self.estado = interpretar_devices(self._ejecutar("devices"), destino)
        except FileNotFoundError:
            self.estado = SIN_ADB
        except (OSError, subprocess.SubprocessError) as error:
            logging.info("ADB no pudo conectarse a la tele (%s).", error)
            self.estado = SIN_RESPUESTA
        return self.estado

    def _shell(self, host: str, *comando: str) -> str:
        if self.estado != CONECTADO and self.conectar(host) != CONECTADO:
            raise RuntimeError(self.estado)
        return self._ejecutar("-s", f"{host}:{PUERTO}", "shell", *comando)

    def apps(self, host: str) -> list[str]:
        """Paquetes de las apps que la tele muestra en su inicio (incluye las del sistema)."""
        return interpretar_actividades(self._shell(
            host, "cmd", "package", "query-activities", "--brief", "--components",
            "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LEANBACK_LAUNCHER"))

    def abrir(self, host: str, paquete: str) -> bool:
        if not re.fullmatch(r"[A-Za-z]\w*(?:\.\w+)+", paquete):
            raise ValueError(f"paquete no válido: {paquete}")
        salida = self._shell(host, "monkey", "-p", paquete, "-c", "android.intent.category.LEANBACK_LAUNCHER", "1")
        return "Events injected: 1" in salida

    def detener(self) -> None:
        """Cierra el servidor de adb (si no, adb.exe queda abierto y bloquea la carpeta de Jarvis)."""
        try:
            self._ejecutar("kill-server", tiempo=5)
        except Exception:
            pass


def describir(estado: str) -> str:
    return {
        CONECTADO: "ADB: conectado (abre cualquier app de la tele).",
        SIN_AUTORIZAR: "ADB: falta permitir esta PC en la tele (aparece un aviso en pantalla).",
        SIN_RESPUESTA: "ADB: sin respuesta. En la tele: Opciones para desarrolladores → «Depuración por USB».",
        SIN_ADB: "ADB: falta vendor/adb/adb.exe.",
    }.get(estado, "ADB: sin probar todavía.")
