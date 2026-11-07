"""Iniciar Jarvis con Windows (clave Run del usuario; sin permisos de administrador).

Solo funciona con la versión instalada (Jarvis.exe): en desarrollo no hay un
ejecutable estable que registrar. Al arrancar por esta vía se pasa
``--autostart`` para que Jarvis empiece oculto en la bandeja y espere más a
Docker Desktop, que también se está iniciando.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

CLAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOMBRE = "Jarvis"
ARGUMENTO = "--autostart"


def ejecutable() -> Path | None:
    """Ruta de Jarvis.exe, o None si se ejecuta desde el código fuente."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve()


def comando(ruta: Path) -> str:
    return f'"{ruta}" {ARGUMENTO}'


def arrancado_con_windows(argv: list[str] | None = None) -> bool:
    return ARGUMENTO in (sys.argv if argv is None else argv)


def _abrir(escritura: bool = False):
    import winreg

    acceso = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE if escritura else winreg.KEY_QUERY_VALUE
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE_RUN, 0, acceso)


def valor_registrado() -> str | None:
    import winreg

    try:
        with _abrir() as clave:
            valor, _tipo = winreg.QueryValueEx(clave, NOMBRE)
    except FileNotFoundError:
        return None
    return str(valor)


def esta_activado() -> bool:
    return valor_registrado() is not None


def activar(ruta: Path | None = None) -> str:
    import winreg

    ruta = ruta or ejecutable()
    if ruta is None:
        raise OSError("El inicio con Windows solo se puede activar desde Jarvis.exe instalado.")
    with _abrir(escritura=True) as clave:
        winreg.SetValueEx(clave, NOMBRE, 0, winreg.REG_SZ, comando(ruta))
    logging.info("Inicio con Windows activado.")
    return "Listo: Jarvis se iniciará con Windows, oculto en la bandeja."


def desactivar() -> str:
    import winreg

    try:
        with _abrir(escritura=True) as clave:
            winreg.DeleteValue(clave, NOMBRE)
    except FileNotFoundError:
        pass
    logging.info("Inicio con Windows desactivado.")
    return "Listo: Jarvis ya no se iniciará con Windows."


def sincronizar(preferido: bool) -> None:
    """Al abrir la versión instalada: deja el registro como dice la preferencia y
    actualiza la ruta si Jarvis se movió de carpeta."""
    ruta = ejecutable()
    if ruta is None:
        return
    actual = valor_registrado()
    if preferido and actual != comando(ruta):
        activar(ruta)
    elif not preferido and actual is not None:
        desactivar()
