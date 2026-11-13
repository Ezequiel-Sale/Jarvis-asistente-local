"""Modo juego: al abrir un juego, cierra los programas que elegiste y los reabre al terminar.

Lo demás del modo juego vive en ``JarvisApp``: liberar la memoria de video (Ollama y la
voz a texto) y el silencio de Jarvis (los avisos no urgentes se guardan y se resumen al
cerrar el juego). Windows 11 ya activa «No molestar» al jugar en pantalla completa, y el
undervolt de la GPU lo mantiene ``jarvis_gpu_tuning`` cada minuto.

Cerrar: primero como si cerraras la ventana (WM_CLOSE); lo que siga abierto a los
``ESPERA_CIERRE`` s se termina a la fuerza (el usuario eligió esos programas en Ajustes).
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

PREF_SILENCIO = "modo_juego_silencio"  # "0" = Jarvis habla igual al jugar; por defecto, silencio
PREF_CERRAR = "modo_juego_cerrar"      # «Discord, Brave»: programas a cerrar al jugar
ESPERA_CIERRE = 5.0

# Nunca se cierran (sistema, Jarvis, las tiendas de juegos que lanzan el juego…).
PROTEGIDOS = frozenset({
    "explorer", "jarvis", "python", "pythonw", "system", "svchost", "csrss", "winlogon", "dwm",
    "lsass", "services", "smss", "wininit", "taskmgr", "steam", "steamwebhelper", "epicgameslauncher",
    "xboxpcapp", "gamingservices", "radeonsoftware", "amdrsserv", "ollama", "docker desktop",
    "com.docker.backend", "tailscale", "tailscaled",
})


@dataclass(frozen=True, slots=True)
class Cerrado:
    nombre: str       # como lo escribiste («Discord»)
    ejecutable: str   # ruta para volver a abrirlo


def nombres_de(texto: str | None) -> list[str]:
    """«Discord, brave.exe ; Spotify» → ["discord", "brave", "spotify"] (sin los protegidos)."""
    if not texto:
        return []
    salida: list[str] = []
    for parte in texto.replace(";", ",").split(","):
        nombre = parte.strip().casefold().removesuffix(".exe").strip()
        if nombre and nombre not in PROTEGIDOS and nombre not in salida:
            salida.append(nombre)
    return salida


def coincide(proceso: str, nombre: str) -> bool:
    """«Discord.exe» con «discord»; «brave.exe» con «brave» (prefijo si el nombre es largo)."""
    tallo = proceso.casefold().removesuffix(".exe")
    if tallo in PROTEGIDOS:
        return False
    return tallo == nombre or (len(nombre) >= 4 and tallo.startswith(nombre))


def _cerrar_ventanas(pid: int) -> None:
    """WM_CLOSE a las ventanas visibles del proceso (como pulsar la X)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    ventanas: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def recorrer(hwnd, _):
        dueno = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(dueno))
        if dueno.value == pid and user32.IsWindowVisible(hwnd):
            ventanas.append(hwnd)
        return True

    user32.EnumWindows(recorrer, 0)
    for hwnd in ventanas:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def cerrar(
    nombres: Iterable[str],
    *,
    procesos: Callable[[], Iterable] | None = None,
    cerrar_ventanas: Callable[[int], None] = _cerrar_ventanas,
    espera: float = ESPERA_CIERRE,
    dormir: Callable[[float], None] = time.sleep,
) -> list[Cerrado]:
    """Cierra los programas de ``nombres`` que estén abiertos y devuelve cómo reabrirlos."""
    import psutil

    nombres = list(nombres)
    if not nombres:
        return []
    leer = procesos or (lambda: psutil.process_iter(["pid", "name", "exe"]))
    encontrados: list[tuple[str, object]] = []
    for proceso in leer():
        info = getattr(proceso, "info", {}) or {}
        nombre_proceso = str(info.get("name") or "")
        for nombre in nombres:
            if coincide(nombre_proceso, nombre):
                encontrados.append((nombre, proceso))
                break
    if not encontrados:
        return []
    cerrados: dict[str, Cerrado] = {}
    for nombre, proceso in encontrados:
        exe = str(proceso.info.get("exe") or "")
        if exe and nombre not in cerrados:
            cerrados[nombre] = Cerrado(nombre, exe)
        try:
            cerrar_ventanas(int(proceso.info["pid"]))
        except Exception:
            logging.debug("No se pudo cerrar la ventana de %s.", nombre, exc_info=True)
    dormir(espera)
    for nombre, proceso in encontrados:
        try:
            if proceso.is_running():
                proceso.terminate()  # siguió abierto (p. ej. se esconde en la bandeja)
        except Exception:
            logging.debug("No se pudo terminar %s.", nombre, exc_info=True)
    logging.info("Modo juego: cerrados %s.", ", ".join(cerrados) or "ninguno")
    return list(cerrados.values())


def reabrir(cerrados: Iterable[Cerrado], lanzar: Callable[..., object] | None = None) -> list[str]:
    """Vuelve a abrir lo cerrado. Devuelve los nombres que no se pudieron abrir."""
    lanzar = lanzar or (lambda ruta: subprocess.Popen(
        [ruta], close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)))
    fallidos = []
    for cerrado in cerrados:
        try:
            lanzar(cerrado.ejecutable)
        except Exception:
            logging.warning("No se pudo volver a abrir %s (%s).", cerrado.nombre, cerrado.ejecutable)
            fallidos.append(cerrado.nombre)
    return fallidos


def describir(cerrados: Iterable[Cerrado]) -> str:
    nombres = [c.nombre.capitalize() for c in cerrados]
    if not nombres:
        return ""
    return nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]
