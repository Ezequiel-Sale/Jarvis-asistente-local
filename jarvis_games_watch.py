"""Modo juego automático: detecta cuándo se abre y se cierra un juego.

Un proceso cuenta como juego si su ejecutable está dentro de una carpeta de
juegos: las del lanzador de Jarvis (``jarvis_system.GAME_DIRECTORIES``), todas
las bibliotecas de Steam (``libraryfolders.vdf``), Epic Games y Xbox. Se ignoran
lo que no es un juego (Wallpaper Engine, redistribuibles, antitrampas,
informes de errores…).

``VigilanteJuegos`` revisa los procesos cada 5 s y publica:
- ``("game_started", nombre)`` cuando pasa de ningún juego a alguno,
- ``("game_stopped", nombre)`` cuando se cierra el último.
El cambio tiene que verse en dos revisiones seguidas (un lanzador que se abre y
se cierra enseguida no dispara nada). La app ejecuta las rutinas con los
disparadores «al abrir un juego» / «al cerrar un juego».
"""

import logging
import os
import re
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

import psutil

CARPETAS_IGNORADAS = {"wallpaper_engine", "steamworks shared", "steam controller configs", "steamvr",
                      "steamlinux runtime", "proton experimental"}
EJECUTABLES_IGNORADOS = re.compile(
    r"crash|report|easyanticheat|eac_|battleye|beservice|redist|vcredist|dxsetup|prereq|"
    r"cefprocess|webhelper|uninstall|unins\d|setup|installer|updater|cleanup", re.I
)


def bibliotecas_steam(steam: Path = Path(r"C:\Program Files (x86)\Steam")) -> list[Path]:
    """Carpetas «steamapps\\common» de todas las bibliotecas de Steam."""
    rutas = {steam}
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    try:
        for valor in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore")):
            rutas.add(Path(valor.replace("\\\\", "\\")))
    except OSError:
        pass
    return [r / "steamapps" / "common" for r in rutas]


def carpetas_de_juegos() -> list[Path]:
    from jarvis_system import GAME_DIRECTORIES

    programas = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidatas = [*GAME_DIRECTORIES, *bibliotecas_steam(), programas / "Epic Games", Path(r"C:\XboxGames")]
    vistas, carpetas = set(), []
    for carpeta in candidatas:
        clave = str(carpeta).lower()
        if clave not in vistas and carpeta.is_dir():
            vistas.add(clave)
            carpetas.append(carpeta)
    return carpetas


def juego_de(ejecutable: str, raices: Iterable[Path]) -> str | None:
    """Nombre del juego (su carpeta) si ``ejecutable`` está dentro de una carpeta de juegos."""
    if not ejecutable or EJECUTABLES_IGNORADOS.search(Path(ejecutable).name):
        return None
    ruta = ejecutable.lower().replace("/", "\\")
    for raiz in raices:
        prefijo = str(raiz).lower().rstrip("\\") + "\\"
        if ruta.startswith(prefijo):
            carpeta = ejecutable[len(prefijo):].replace("/", "\\").split("\\", 1)[0]
            if carpeta.lower() in CARPETAS_IGNORADAS or "\\" not in ruta[len(prefijo):]:
                return None
            return carpeta
    return None


def juegos_abiertos(raices: list[Path]) -> set[str]:
    abiertos = set()
    for proceso in psutil.process_iter(["exe"]):
        try:
            juego = juego_de(proceso.info.get("exe") or "", raices)
        except (psutil.Error, OSError):
            continue
        if juego:
            abiertos.add(juego)
    return abiertos


class VigilanteJuegos:
    def __init__(
        self,
        eventos,
        *,
        intervalo: float = 5.0,
        leer: Callable[[], set[str]] | None = None,
    ) -> None:
        self._eventos = eventos
        self._intervalo = intervalo
        self._leer = leer
        self._parar = threading.Event()
        self._confirmado: set[str] = set()
        self._candidato: set[str] | None = None
        self._iniciado = False
        self._hilo = threading.Thread(target=self._bucle, name="JarvisJuegos", daemon=True)

    def start(self) -> None:
        self._hilo.start()

    def stop(self) -> None:
        self._parar.set()

    @property
    def jugando(self) -> set[str]:
        return set(self._confirmado)

    def revisar_una_vez(self) -> None:
        if self._leer is None:
            raices = carpetas_de_juegos()
            self._leer = lambda: juegos_abiertos(raices)
        try:
            actuales = self._leer()
        except Exception:
            logging.debug("No se pudieron revisar los juegos abiertos.", exc_info=True)
            return
        if not self._iniciado:
            # Al arrancar Jarvis con un juego ya abierto no se dispara «al abrir».
            self._iniciado, self._confirmado = True, actuales
            return
        if bool(actuales) == bool(self._confirmado):
            self._candidato = None
            self._confirmado = actuales if actuales else self._confirmado
            return
        if self._candidato is None or bool(self._candidato) != bool(actuales):
            self._candidato = actuales  # se confirma en la próxima revisión
            return
        anteriores, self._confirmado, self._candidato = self._confirmado, actuales, None
        if actuales:
            nombre = sorted(actuales)[0]
            logging.info("Juego abierto: %s.", nombre)
            self._eventos.put(("game_started", nombre))
        else:
            nombre = sorted(anteriores)[0] if anteriores else ""
            logging.info("Juego cerrado: %s.", nombre)
            self._eventos.put(("game_stopped", nombre))

    def _bucle(self) -> None:
        while not self._parar.is_set():
            self.revisar_una_vez()
            self._parar.wait(self._intervalo)
