"""FPS del juego (o de la ventana en primer plano) con PresentMon de Intel.

- ``vendor/PresentMon/PresentMon.exe`` (consola 2.6.0, MIT, firmado por Intel) se
  lanza solo mientras alguien mira la pantalla de juego del celular: cada consulta
  lo mantiene vivo y se detiene a los ``INACTIVO`` segundos sin consultas.
- Lee el CSV de ``--output_stdout`` (columnas Application, ProcessID y FrameTime en
  ms) de todos los procesos y elige el de la ventana en primer plano; si esa no
  dibuja, el que más cuadros presentó.
- No necesita ser administrador, pero Jarvis ya lo es y así ve todos los procesos.
"""

import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

SESION = "JarvisFPS"
INACTIVO = 20.0  # s sin consultas → se detiene PresentMon
VENTANA = 2.0  # s de cuadros que se promedian
CREATE_NO_WINDOW = 0x08000000


def ejecutable() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "vendor" / "PresentMon" / "PresentMon.exe"


def pid_primer_plano() -> int | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    ventana = user32.GetForegroundWindow()
    if not ventana:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(ventana, ctypes.byref(pid))
    return pid.value or None


def _lanzar(ruta: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [str(ruta), "--output_stdout", "--no_console_stats", "--stop_existing_session",
         "--session_name", SESION, "--v2_metrics"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


class MedidorFPS:
    def __init__(
        self,
        ruta: Path | None = None,
        *,
        reloj: Callable[[], float] = time.monotonic,
        primer_plano: Callable[[], int | None] = pid_primer_plano,
        lanzar: Callable[[Path], object] = _lanzar,
    ) -> None:
        self._ruta = ruta or ejecutable()
        self._reloj = reloj
        self._primer_plano = primer_plano
        self._lanzar = lanzar
        self._lock = threading.Lock()
        self._proceso = None
        self._cuadros: deque[tuple[float, int, str, float]] = deque(maxlen=6000)
        self._pedido = 0.0
        self.error: str | None = None

    # --- Ciclo de vida ------------------------------------------------------------------

    def _asegurar(self) -> None:
        with self._lock:
            if self._proceso is not None and self._proceso.poll() is None:
                return
            if not self._ruta.exists():
                self.error = "Falta PresentMon (vendor/PresentMon/PresentMon.exe)."
                return
            try:
                proceso = self._lanzar(self._ruta)
            except OSError as error:
                self.error = f"No pude iniciar PresentMon: {error}"
                logging.warning(self.error)
                return
            self.error = None
            self._proceso = proceso
            self._cuadros.clear()
            logging.info("PresentMon iniciado para medir los FPS.")
        threading.Thread(target=self._leer, args=(proceso,), name="JarvisFPS", daemon=True).start()
        threading.Thread(target=self._vigilar, args=(proceso,), name="JarvisFPSVigia", daemon=True).start()

    def detener(self) -> None:
        with self._lock:
            proceso, self._proceso = self._proceso, None
        if proceso is None:
            return
        try:
            proceso.terminate()
        except OSError:
            pass
        logging.info("PresentMon detenido (nadie mira los FPS).")

    def _vigilar(self, proceso) -> None:
        while self._proceso is proceso and proceso.poll() is None:
            time.sleep(2)
            if self._reloj() - self._pedido > INACTIVO:
                self.detener()
                return

    def _leer(self, proceso) -> None:
        columnas: dict[str, int] | None = None
        for linea in proceso.stdout:
            partes = linea.rstrip("\r\n").split(",")
            if columnas is None:
                if "Application" in partes and "FrameTime" in partes:
                    columnas = {nombre: i for i, nombre in enumerate(partes)}
                continue
            try:
                app = partes[columnas["Application"]]
                pid = int(partes[columnas["ProcessID"]])
                tiempo = float(partes[columnas["FrameTime"]])
            except (IndexError, ValueError):
                continue
            if tiempo > 0:
                self._cuadros.append((self._reloj(), pid, app, tiempo))

    # --- Consulta -------------------------------------------------------------------

    def actual(self) -> dict | None:
        """{app, fps, frameMs} del proceso medido, o None si todavía no hay datos."""
        self._pedido = self._reloj()
        self._asegurar()
        return self.calcular()

    def calcular(self) -> dict | None:
        ahora = self._reloj()
        por_pid: dict[int, list[tuple[str, float]]] = {}
        for momento, pid, app, tiempo in list(self._cuadros):
            if ahora - momento <= VENTANA:
                por_pid.setdefault(pid, []).append((app, tiempo))
        if not por_pid:
            return None
        objetivo = self._primer_plano()
        if objetivo not in por_pid:
            objetivo = max(por_pid, key=lambda p: len(por_pid[p]))
        cuadros = por_pid[objetivo][-240:]
        promedio = sum(t for _, t in cuadros) / len(cuadros)
        app = cuadros[-1][0]
        return {
            "app": app[:-4] if app.lower().endswith(".exe") else app,
            "fps": round(1000 / promedio),
            "frameMs": round(promedio, 1),
        }
