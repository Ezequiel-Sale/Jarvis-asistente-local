"""FPS con PresentMon, estado del equipo y música para la app del celular (sin procesos reales)."""

import queue
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import jarvis_dashboard as jd
import jarvis_fps
import jarvis_hardware as hw
import jarvis_media as jm
from jarvis_system_info import Disco

CSV = [
    "Application,ProcessID,SwapChainAddress,FrameTime,CPUBusy\n",
    "juego.exe,10,0x1,6.9,1.0\n",
    "juego.exe,10,0x1,7.1,1.0\n",
    "brave.exe,20,0x2,16.6,1.0\n",
    "linea,rota\n",
    "juego.exe,10,0x1,7.0,1.0\n",
]


class _Proceso:
    def __init__(self, lineas):
        self.stdout = iter(lineas)
        self.terminado = False

    def poll(self):
        return 0 if self.terminado else None

    def terminate(self):
        self.terminado = True


class FpsTests(unittest.TestCase):
    def _medidor(self, primer_plano):
        self.proceso = _Proceso(CSV)
        ruta = Path(__file__)  # existe: hace de PresentMon.exe
        return jarvis_fps.MedidorFPS(ruta, primer_plano=lambda: primer_plano, lanzar=lambda _r: self.proceso)

    def _esperar_cuadros(self, medidor):
        for _ in range(100):
            if len(medidor._cuadros) >= 4:
                return
            time.sleep(0.01)

    def test_foreground_process_is_measured(self) -> None:
        medidor = self._medidor(10)
        medidor.actual()
        self._esperar_cuadros(medidor)
        self.assertEqual(medidor.calcular(), {"app": "juego", "fps": 143, "frameMs": 7.0})
        medidor.detener()
        self.assertTrue(self.proceso.terminado)

    def test_without_foreground_frames_uses_busiest_process(self) -> None:
        medidor = self._medidor(999)
        medidor.actual()
        self._esperar_cuadros(medidor)
        self.assertEqual(medidor.calcular()["app"], "juego")
        medidor.detener()

    def test_missing_presentmon_is_reported(self) -> None:
        medidor = jarvis_fps.MedidorFPS(Path("no/existe/PresentMon.exe"))
        self.assertIsNone(medidor.actual())
        self.assertIn("Falta PresentMon", medidor.error)


class StatusTests(unittest.TestCase):
    def test_history_and_status_for_phone(self) -> None:
        reloj = {"t": 100.0}
        lecturas = iter([hw.Temperaturas(gpu=50, gpu_carga=10),
                         hw.Temperaturas(gpu=60, gpu_carga=95, gpu_mem_usada=4096.0, gpu_mem_total=16384.0)])
        vigilante = hw.VigilanteSalud(queue.Queue(), leer=lambda: next(lecturas), reloj=lambda: reloj["t"],
                                      memoria=lambda: 42.0)
        vigilante.revisar_una_vez()
        reloj["t"] = 103.0
        vigilante.revisar_una_vez()
        memoria = SimpleNamespace(percent=42.0, total=32, available=16)
        datos = jd.estado_movil(vigilante, discos=[Disco("C:", "", 10, 100)], memoria=memoria, fps={"fps": 60},
                                juegos={"Hades"})
        self.assertEqual(datos["gpu"], 60)
        self.assertEqual([h["t"] for h in datos["history"]], [0.0, 3.0])
        self.assertEqual(datos["history"][0]["gpuLoad"], 10)
        self.assertEqual(datos["ram"], {"pct": 42.0, "used": 16, "total": 32})
        self.assertEqual(datos["gpuMem"], {"pct": 25.0, "used": 4096 * 1024**2, "total": 16384 * 1024**2})
        self.assertEqual([h["gpuMem"] for h in datos["history"]], [None, 25.0])
        self.assertEqual(datos["disks"], [{"unit": "C:", "name": "", "free": 10, "total": 100, "pct": 90.0}])
        self.assertEqual((datos["fps"], datos["games"]), ({"fps": 60}, ["Hades"]))
        self.assertEqual(datos["limits"]["gpu"], hw.LIMITE_GPU)


class MediaTests(unittest.TestCase):
    def test_apps_with_audio_are_grouped(self) -> None:
        def sesion(nombre, volumen, mudo):
            control = SimpleNamespace(GetMasterVolume=lambda: volumen, GetMute=lambda: mudo)
            return SimpleNamespace(Process=SimpleNamespace(name=lambda: nombre), SimpleAudioVolume=control)

        apps = jm.aplicaciones_con_audio(sesiones=lambda: [sesion("Spotify.exe", 0.5, 0),
                                                            sesion("spotify.exe", 0.9, 0),
                                                            sesion("Discord.exe", 1.0, 1)])
        self.assertEqual([(a["name"], a["volume"], a["muted"]) for a in apps],
                         [("Discord", 100, True), ("Spotify", 50, False)])

    def test_winrt_runs_in_its_own_thread(self) -> None:
        hilos = []

        async def consulta():
            hilos.append(threading.current_thread().name)
            return "listo"

        self.assertEqual(jm._ejecutar(consulta), "listo")
        self.assertEqual(hilos, ["JarvisWinRT"])

        async def falla():
            raise OSError("sin sesión")

        with self.assertRaisesRegex(OSError, "sin sesión"):
            jm._ejecutar(falla)


if __name__ == "__main__":
    unittest.main()
