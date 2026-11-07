"""Iniciar Jarvis con Windows mediante una tarea programada «al iniciar sesión».

Antes se usaba la clave Run del usuario, pero el 2026-11-07 la entrada
desapareció antes del arranque y Windows no la ejecutó (no la borró Jarvis ni
el antivirus). La tarea programada es más robusta. Al activar se borra la entrada Run
antigua para no abrir Jarvis dos veces.

Jarvis se ejecuta como administrador (2026-11-07): sin eso Windows no deja
mover el puntero sobre ventanas elevadas (clic en pantalla). No se usa un
manifiesto «requireAdministrator» porque pediría UAC en cada apertura. En su
lugar, cuando Jarvis ya corre elevado, las tareas se registran con «privilegios
más altos» (se ejecutan elevadas sin preguntar):
- ``TAREA``: al iniciar sesión, con ``--autostart``.
- ``TAREA_ADMIN``: sin disparador; al abrir Jarvis.exe sin permisos,
  ``relanzar_como_administrador`` la ejecuta y la copia sin permisos termina.
  Si aún no existe, se pide UAC una vez. ``--sin-admin`` evita el relanzado.

Solo funciona con la versión instalada (Jarvis.exe). Al arrancar por esta vía
se pasa ``--autostart``: Jarvis empieza oculto en la bandeja y espera más a
Docker Desktop.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TAREA = "Jarvis"
CLAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOMBRE_RUN = "Jarvis"
ARGUMENTO = "--autostart"
TAREA_ADMIN = "Jarvis (administrador)"
ARGUMENTO_SIN_ADMIN = "--sin-admin"
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ejecutable() -> Path | None:
    """Ruta de Jarvis.exe, o None si se ejecuta desde el código fuente."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve()


def arrancado_con_windows(argv: list[str] | None = None) -> bool:
    return ARGUMENTO in (sys.argv if argv is None else argv)


def es_administrador() -> bool:
    """True si este proceso tiene permisos de administrador (elevado)."""
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _usuario() -> str:
    dominio = os.environ.get("USERDOMAIN", "")
    usuario = os.environ.get("USERNAME", "")
    return f"{dominio}\\{usuario}" if dominio else usuario


def xml_tarea(ruta: Path, usuario: str, *, administrador: bool = False,
              al_iniciar_sesion: bool = True) -> str:
    """XML de la tarea. ``al_iniciar_sesion=False`` = tarea para abrir Jarvis
    elevado a mano (sin disparador ni ``--autostart``)."""
    usuario = escape(usuario)
    nivel = "HighestAvailable" if administrador else "LeastPrivilege"
    if al_iniciar_sesion:
        descripcion = "Inicia J.A.R.V.I.S. en la bandeja al iniciar sesión."
        disparadores = f"""
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{usuario}</UserId>
    </LogonTrigger>
  """
        argumentos = f"\n      <Arguments>{ARGUMENTO}</Arguments>"
        # Una sola a la vez; si Jarvis ya está abierto, la instancia única lo resuelve.
        instancias = "IgnoreNew"
    else:
        descripcion = "Abre J.A.R.V.I.S. con permisos de administrador sin pedir UAC."
        disparadores = ""
        argumentos = ""
        # La tarea sigue «en ejecución» mientras Jarvis está abierto: con IgnoreNew
        # una segunda apertura no haría nada (ni mostraría la ventana).
        instancias = "Parallel"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{descripcion}</Description>
  </RegistrationInfo>
  <Triggers>{disparadores}</Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{usuario}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>{nivel}</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>{instancias}</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(ruta))}</Command>{argumentos}
      <WorkingDirectory>{escape(str(ruta.parent))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*argumentos: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["schtasks", *argumentos],
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=_SIN_VENTANA,
        check=False,
    )


def _xml_registrado(nombre: str) -> str | None:
    resultado = _schtasks("/Query", "/TN", nombre, "/XML")
    return resultado.stdout if resultado.returncode == 0 else None


def _campo(xml: str, etiqueta: str) -> str:
    import re

    m = re.search(rf"<{etiqueta}>(.*?)</{etiqueta}>", xml, re.S)
    return m.group(1).strip() if m else ""


def comando_registrado(nombre: str = TAREA) -> str | None:
    """Ejecutable que lanza la tarea, o None si no existe."""
    xml = _xml_registrado(nombre)
    return None if xml is None else _campo(xml, "Command")


def tarea_elevada(nombre: str = TAREA) -> bool:
    xml = _xml_registrado(nombre)
    return xml is not None and _campo(xml, "RunLevel") == "HighestAvailable"


def _crear_tarea(nombre: str, xml: str) -> None:
    descriptor, archivo = tempfile.mkstemp(suffix=".xml", prefix="jarvis_tarea_")
    os.close(descriptor)
    try:
        Path(archivo).write_text(xml, encoding="utf-16")
        resultado = _schtasks("/Create", "/TN", nombre, "/XML", archivo, "/F")
    finally:
        Path(archivo).unlink(missing_ok=True)
    if resultado.returncode != 0:
        detalle = (resultado.stderr or resultado.stdout).strip()
        raise OSError(f"Windows no creó la tarea «{nombre}»: {detalle}")


def esta_activado() -> bool:
    return comando_registrado() is not None


def _borrar_run_antiguo() -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE_RUN, 0, winreg.KEY_SET_VALUE) as clave:
            winreg.DeleteValue(clave, NOMBRE_RUN)
    except FileNotFoundError:
        return
    logging.info("Se quitó la entrada Run antigua de Jarvis.")


def activar(ruta: Path | None = None) -> str:
    ruta = ruta or ejecutable()
    if ruta is None:
        raise OSError("El inicio con Windows solo se puede activar desde Jarvis.exe instalado.")
    # Con «privilegios más altos» solo si Jarvis ya es administrador (si no,
    # Windows no deja crearla); la siguiente apertura elevada la actualiza.
    administrador = es_administrador()
    _crear_tarea(TAREA, xml_tarea(ruta, _usuario(), administrador=administrador))
    _borrar_run_antiguo()
    logging.info("Inicio con Windows activado (tarea programada%s).",
                 ", como administrador" if administrador else "")
    return "Listo: Jarvis se iniciará con Windows, oculto en la bandeja."


def desactivar() -> str:
    resultado = _schtasks("/Delete", "/TN", TAREA, "/F")
    if resultado.returncode != 0 and esta_activado():
        detalle = (resultado.stderr or resultado.stdout).strip()
        raise OSError(f"Windows no borró la tarea de inicio: {detalle}")
    _borrar_run_antiguo()
    logging.info("Inicio con Windows desactivado.")
    return "Listo: Jarvis ya no se iniciará con Windows."


def sincronizar(preferido: bool) -> None:
    """Al abrir la versión instalada: deja la tarea como dice la preferencia y
    actualiza la ruta si Jarvis se movió de carpeta."""
    ruta = ejecutable()
    if ruta is None:
        return
    if es_administrador():
        try:
            asegurar_tarea_admin(ruta)
        except OSError:
            logging.exception("No se pudo crear la tarea para abrir Jarvis como administrador.")
    actual = comando_registrado()
    if preferido and actual is None:
        logging.warning("Faltaba la tarea de inicio con Windows; se vuelve a crear.")
        activar(ruta)
    elif preferido and Path(actual) != ruta:
        logging.info("La tarea de inicio apuntaba a otra ruta; se actualiza.")
        activar(ruta)
    elif preferido and es_administrador() and not tarea_elevada():
        logging.info("La tarea de inicio no era de administrador; se actualiza.")
        activar(ruta)
    elif not preferido and actual is not None:
        desactivar()
    else:
        _borrar_run_antiguo()


# --- Ejecutarse como administrador ---------------------------------------------


def asegurar_tarea_admin(ruta: Path) -> None:
    """Crea o actualiza ``TAREA_ADMIN`` (solo desde un Jarvis ya elevado)."""
    actual = comando_registrado(TAREA_ADMIN)
    if actual is not None and Path(actual) == ruta and tarea_elevada(TAREA_ADMIN):
        return
    _crear_tarea(TAREA_ADMIN, xml_tarea(ruta, _usuario(), administrador=True,
                                        al_iniciar_sesion=False))
    logging.info("Tarea para abrir Jarvis como administrador %s.",
                 "creada" if actual is None else "actualizada")


def debe_relanzar_como_administrador(argv: list[str] | None = None) -> bool:
    """Jarvis.exe abierto a mano sin permisos de administrador.

    En el inicio con Windows no se relanza (sería un aviso de UAC al encender);
    esa tarea pasa a ser elevada en cuanto Jarvis se abre una vez como admin.
    """
    argv = sys.argv if argv is None else argv
    return (
        ejecutable() is not None
        and not es_administrador()
        and ARGUMENTO_SIN_ADMIN not in argv
        and not arrancado_con_windows(argv)
    )


def relanzar_como_administrador() -> bool:
    """Abre otro Jarvis elevado. True si se lanzó (esta copia debe terminar)."""
    ruta = ejecutable()
    if ruta is None:
        return False
    actual = comando_registrado(TAREA_ADMIN)
    if actual is not None and Path(actual) == ruta:
        resultado = _schtasks("/Run", "/TN", TAREA_ADMIN)
        if resultado.returncode == 0:
            logging.info("Jarvis se reabre como administrador (tarea programada).")
            return True
        logging.warning("No se pudo ejecutar la tarea de administrador: %s",
                        (resultado.stderr or resultado.stdout).strip())
    import ctypes

    codigo = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", str(ruta), "", str(ruta.parent), 1
    )
    if codigo > 32:
        logging.info("Jarvis se reabre como administrador (UAC aceptado).")
        return True
    logging.warning("No se pudo abrir Jarvis como administrador (código %s); "
                    "sigue sin permisos de administrador.", codigo)
    return False
