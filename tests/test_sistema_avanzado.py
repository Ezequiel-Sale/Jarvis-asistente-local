"""Capturas, grabación, servicios y procesos colgados (2026-11-27). Sin pantalla, servicios ni procesos reales."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jarvis_sistema_avanzado as s


class FrasesTests(unittest.TestCase):
    def test_phrases(self) -> None:
        casos = {
            "sacá una captura": ("screenshot", {}),
            "captura de pantalla del monitor 2": ("screenshot", {"monitor": 2}),
            "grabá la pantalla del monitor 1 por 20 segundos":
                ("screen_record", {"action": "start", "monitor": 1, "seconds": 20}),
            "graba la pantalla 2 minutos": ("screen_record", {"action": "start", "seconds": 120}),
            "dejá de grabar": ("screen_record", {"action": "stop"}),
            "reiniciá el servicio de impresión": ("service_control", {"name": "impresion", "action": "restart"}),
            "detené el servicio Spooler": ("service_control", {"name": "spooler", "action": "stop"}),
            "¿cómo está el servicio de Windows Update?": ("service_status", {"name": "windows update"}),
            "qué servicios están corriendo": ("service_status", {"only_running": True}),
            "qué programas no responden": ("process_control", {"action": "hung"}),
            "cerrá los programas que no responden": ("process_control", {"action": "kill_hung"}),
            "forzá el cierre de Discord": ("process_control", {"action": "kill_hung", "name": "discord"}),
            "poné Discord en prioridad alta":
                ("process_control", {"action": "priority", "name": "discord", "priority": "high"}),
            "bajale la prioridad a Chrome":
                ("process_control", {"action": "priority", "name": "chrome", "priority": "below_normal"}),
        }
        for frase, esperado in casos.items():
            with self.subTest(frase=frase):
                self.assertEqual(s.detectar(frase), esperado)
        for frase in ("mirá cómo lo hago", "cerrá discord", "abrí steam", "pausa el video"):
            self.assertIsNone(s.detectar(frase), frase)


SALIDA = ("Spooler|Cola de impresión|Running|Automatic\n"
          "PrintDeviceConfigurationService|Servicio de configuración de dispositivos de impresión|Stopped|Manual\n"
          "WinDefend|Servicio Antivirus de Microsoft Defender|Running|Automatic\n")


class ServiciosTests(unittest.TestCase):
    def test_status_and_search(self) -> None:
        leer = lambda _script: SALIDA  # noqa: E731
        self.assertIn("Cola de impresión", s.estado_servicio("spooler", leer=leer))
        self.assertIn("2 en marcha", s.estado_servicio(leer=leer))
        with self.assertRaises(LookupError):
            s.estado_servicio("no existe", leer=leer)

    def test_control_asks_which_and_protects_the_system(self) -> None:
        leer = lambda _script: SALIDA  # noqa: E731
        ejecutados = []
        self.assertEqual(s.controlar_servicio("cola de impresion", "restart", leer=leer,
                                              ejecutar=lambda script, _t: ejecutados.append(script)),
                         "Reinicié el servicio «Cola de impresión».")
        self.assertIn("Restart-Service -Force -Name 'Spooler'", ejecutados[0])
        with self.assertRaises(LookupError):  # dos coinciden: pregunta cuál
            s.controlar_servicio("impresion", "stop", leer=leer, ejecutar=lambda *_: None)
        with self.assertRaises(PermissionError):
            s.controlar_servicio("WinDefend", "stop", leer=leer, ejecutar=lambda *_: None)


class ProcesosTests(unittest.TestCase):
    def test_hung_windows(self) -> None:
        ventanas = [SimpleNamespace(hwnd=1, programa="discord", titulo="Discord", pid=11),
                    SimpleNamespace(hwnd=2, programa="brave", titulo="Brave", pid=12)]
        listar = lambda: ventanas  # noqa: E731
        self.assertEqual(s.control_procesos("hung", listar=listar, colgada=lambda h: h == 1), "No responden: discord.")
        self.assertIn("Ningún programa", s.control_procesos("hung", listar=listar, colgada=lambda h: False))
        matados = []
        with patch("psutil.Process", side_effect=lambda pid: SimpleNamespace(kill=lambda: matados.append(pid))):
            self.assertEqual(s.control_procesos("kill_hung", listar=listar, colgada=lambda h: h == 1),
                             "Cerré a la fuerza: discord.")
            self.assertEqual(matados, [11])
            self.assertIn("no está colgado", s.control_procesos("kill_hung", "brave", listar=listar,
                                                                 colgada=lambda h: h == 1))
        with self.assertRaises(ValueError):
            s.control_procesos("priority", "discord", "realtime")


class CapturaTests(unittest.TestCase):
    def test_screenshot_saves_a_png(self) -> None:
        from PIL import Image

        monitor = SimpleNamespace(numero=2, x=0, y=0, ancho=40, alto=30)
        with tempfile.TemporaryDirectory() as carpeta, \
                patch("jarvis_vision.listar_monitores", return_value=[monitor]), \
                patch("jarvis_vision.elegir_monitor", return_value=monitor), \
                patch("jarvis_vision._captura_rapida", return_value=Image.new("RGB", (40, 30))):
            texto = s.captura(2, carpeta=Path(carpeta))
            archivos = list(Path(carpeta).iterdir())
            self.assertEqual(len(archivos), 1)
            self.assertEqual(archivos[0].suffix, ".png")
            self.assertIn("monitor 2", texto)

    def test_stop_without_recording(self) -> None:
        self.assertEqual(s.dejar_de_grabar(), "No estaba grabando la pantalla.")


if __name__ == "__main__":
    unittest.main()
