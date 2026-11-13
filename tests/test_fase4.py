"""Fase 4: salud del equipo, limpieza y panel de inicio (sin tocar hardware, disco real ni la base)."""

import os
import queue
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jarvis_cleanup as lim
import jarvis_dashboard as panel
import jarvis_hardware as hw
from jarvis_commands import detectar_comando_limpieza

LECTURAS = [
    ("Cpu", "AMD Ryzen 7 5700X", "Load", "CPU Total", 17.6),
    ("Cpu", "AMD Ryzen 7 5700X", "Temperature", "Core (Tctl/Tdie)", None),  # sin PawnIO
    ("GpuAmd", "AMD Radeon RX 9070 XT", "Temperature", "GPU Core", 36.0),
    ("GpuAmd", "AMD Radeon RX 9070 XT", "Temperature", "GPU Hot Spot", 37.0),
    ("GpuAmd", "AMD Radeon RX 9070 XT", "Temperature", "GPU Memory", 40.0),
    ("GpuAmd", "AMD Radeon RX 9070 XT", "Load", "GPU Core", 3.0),
    ("GpuAmd", "AMD Radeon RX 9070 XT", "Load", "D3D 3D", 0.0),
]


class HardwareTests(unittest.TestCase):
    def test_interpret_real_sensor_names(self) -> None:
        t = hw.interpretar(LECTURAS)
        self.assertEqual((t.gpu, t.gpu_punto_caliente, t.gpu_memoria, t.gpu_carga), (36.0, 37.0, 40.0, 3.0))
        self.assertEqual(t.cpu_carga, 17.6)
        self.assertIsNone(t.cpu)

    def test_zero_degrees_means_no_data(self) -> None:
        sensor = SimpleNamespace(Value=0.0)
        self.assertIsNone(hw._valor(sensor))
        self.assertEqual(hw._valor(SimpleNamespace(Value=41.5)), 41.5)

    def test_description_mentions_pawnio_when_cpu_temp_missing(self) -> None:
        lineas = hw.describir(hw.interpretar(LECTURAS))
        self.assertIn("GPU a 36 °C, punto caliente 37 °C, carga 3 %.", lineas)
        self.assertTrue(any("PawnIO" in l for l in lineas))
        self.assertEqual(hw.describir(hw.Temperaturas()), [])

    def test_alerts_need_two_readings_and_respect_pause(self) -> None:
        caliente = hw.Temperaturas(gpu=95.0)
        eventos: queue.Queue = queue.Queue()
        reloj = {"t": 0.0}
        vigilante = hw.VigilanteSalud(eventos, leer=lambda: caliente, reloj=lambda: reloj["t"])
        vigilante.revisar_una_vez()
        self.assertTrue(eventos.empty())
        vigilante.revisar_una_vez()
        self.assertEqual(eventos.get_nowait(), ("health_alert", "Atención: la GPU está a 95 °C."))
        vigilante.revisar_una_vez()
        vigilante.revisar_una_vez()
        self.assertTrue(eventos.empty())  # dentro de la pausa de 15 min
        reloj["t"] = hw.PAUSA_AVISOS + 1
        vigilante.revisar_una_vez()
        vigilante.revisar_una_vez()
        self.assertFalse(eventos.empty())
        self.assertEqual(vigilante.ultimo, caliente)

    def test_missing_library_does_not_break(self) -> None:
        sensores = hw.SensoresHardware()
        with patch.object(hw, "_carpeta", return_value=Path(tempfile.gettempdir()) / "no_existe_lhm"):
            self.assertEqual(sensores.leer(), hw.Temperaturas())
        self.assertIn("falta", sensores.error)


class CleanupTests(unittest.TestCase):
    def test_sizes(self) -> None:
        self.assertEqual(lim.tamano_legible(5 * 1024**3 + 100 * 1024**2), "5,1 GB")
        self.assertEqual(lim.tamano_legible(2048), "2 KB")
        self.assertEqual(lim.bytes_docker("1.5GB"), 1_500_000_000)
        self.assertEqual(lim.bytes_docker("850.2MB (12%)"), 850_200_000)
        self.assertEqual(lim.bytes_docker("0B"), 0)

    def test_measure_and_delete_only_old_files(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            base = Path(carpeta)
            (base / "sub").mkdir()
            viejo, nuevo = base / "sub" / "viejo.tmp", base / "nuevo.tmp"
            viejo.write_bytes(b"x" * 1000)
            nuevo.write_bytes(b"y" * 500)
            antes = time.time() - 3 * 24 * 3600
            os.utime(viejo, (antes, antes))
            self.assertEqual(lim.medir([base]), 1500)
            self.assertEqual(lim.medir([base], lim.ANTIGUEDAD_TEMPORALES), 1000)
            self.assertEqual(lim.borrar([base], lim.ANTIGUEDAD_TEMPORALES), 1000)
            self.assertFalse(viejo.exists())
            self.assertFalse((base / "sub").exists())  # carpeta vacía eliminada
            self.assertTrue(nuevo.exists())  # lo reciente puede estar en uso

    @patch.object(lim, "modelos_en_uso", return_value={"qwen2.5-coder:14b", "qwen3-vl:8b-instruct-q8_0", "embeddinggemma"})
    @patch.object(lim.requests, "get")
    def test_unused_ollama_models_exclude_the_ones_jarvis_uses(self, get, _uso) -> None:
        get.return_value.json.return_value = {"models": [
            {"name": "qwen2.5-coder:14b", "size": 9}, {"name": "embeddinggemma:latest", "size": 1},
            {"name": "qwen3.6:27b", "size": 17_769_076_933},
        ]}
        self.assertEqual(lim.modelos_sin_usar(), [("qwen3.6:27b", 17_769_076_933)])
        with self.assertRaises(ValueError):
            lim.borrar_modelo("embeddinggemma")

    def test_analysis_skips_empty_and_failing_categories(self) -> None:
        with patch.object(lim, "medir", return_value=0), \
                patch.object(lim, "tamano_papelera", return_value=2048), \
                patch.object(lim, "tamano_docker", side_effect=OSError("sin docker")), \
                patch.object(lim, "modelos_sin_usar", return_value=[("qwen3.6:27b", 10)]):
            categorias = lim.analizar()
        self.assertEqual([c.clave for c in categorias], ["papelera", "ollama:qwen3.6:27b"])
        self.assertTrue(categorias[0].marcada)
        self.assertFalse(categorias[1].marcada)  # los modelos se marcan a mano

    def test_result_text(self) -> None:
        texto = lim.describir_resultado([("temporales", 5 * 1024**3, None), ("docker", 0, "sin docker")])
        self.assertIn("Liberé **5 GB**", texto)
        self.assertIn("- Docker: no se pudo (sin docker)", texto)

    def test_phrases(self) -> None:
        self.assertEqual(detectar_comando_limpieza("Libera espacio."), {"solo": None})
        self.assertEqual(detectar_comando_limpieza("haz una limpieza del disco"), {"solo": None})
        self.assertEqual(detectar_comando_limpieza("vacía la papelera"), {"solo": "papelera"})
        self.assertEqual(detectar_comando_limpieza("borra los archivos temporales"), {"solo": "temporales"})
        self.assertIsNone(detectar_comando_limpieza("limpia la casa"))
        self.assertIsNone(detectar_comando_limpieza("libera la memoria"))


class DashboardTests(unittest.TestCase):
    def test_greeting(self) -> None:
        self.assertEqual(panel.saludo(8), "Buenos días")
        self.assertEqual(panel.saludo(15), "Buenas tardes")
        self.assertEqual(panel.saludo(23), "Buenas noches")
        self.assertEqual(panel.saludo(3), "Buenas noches")

    def test_next_routines(self) -> None:
        r = lambda nombre, hora, dias, activa=True, disparador="hora": SimpleNamespace(  # noqa: E731
            name=nombre, time=hora, days=dias, active=activa, trigger=disparador)
        ahora = datetime(2026, 11, 8, 20, 30)  # domingo
        rutinas = [
            r("Noche", "23:00", (1, 2, 3, 4, 5, 6, 7)),
            r("Inicio", "09:00", (1, 2, 3, 4, 5)),
            r("Apagada", "21:00", (7,), activa=False),
            r("Auriculares", "", (), disparador="auriculares_conectados"),
            r("Sábado", "10:00", (6,)),
        ]
        self.assertEqual(panel.proximas_rutinas(rutinas, ahora),
                         ["Noche · hoy 23:00", "Inicio · mañana 09:00", "Sábado · sábado 10:00"])

    def test_each_source_fails_alone(self) -> None:
        with patch.object(panel.psutil, "virtual_memory", side_effect=OSError("x")), \
                patch.object(panel.requests, "get", side_effect=OSError("sin ollama")), \
                patch.dict("sys.modules", {"jarvis_reminders": Mock(pendientes=Mock(side_effect=OSError))}), \
                patch("jarvis_routines.listar_automatizaciones", return_value=[]), \
                patch("jarvis_media.sesion_actual", return_value=None), \
                patch("jarvis_headset.consultar_g533", return_value=None), \
                patch("plugins.clima.consultar_clima", side_effect=ValueError("sin ciudad")):
            panel._cache.clear()
            datos = panel.recolectar(hw.Temperaturas(gpu=40.0))
        self.assertIsNone(datos.clima)
        self.assertFalse(datos.ollama_ok)
        self.assertEqual(datos.salud.gpu, 40.0)


if __name__ == "__main__":
    unittest.main()


class VoiceCleanupTests(unittest.TestCase):
    def test_voice_command_frees_transcription_state_before_starting(self) -> None:
        """Por voz, Jarvis llega «ocupado» por la transcripción: antes eso cortaba la limpieza."""
        from jarvis_app import JarvisApp

        host = Mock()
        host._closing = False
        host.events = queue.Queue()
        host.events.put(("cleanup_start", "¡Libera espacio!", None))
        orden = Mock()
        host._set_idle.side_effect = lambda: orden.idle()
        host._start_cleanup.side_effect = lambda *a: orden.start(*a)
        JarvisApp._poll_events(host)
        self.assertEqual([c[0] for c in orden.mock_calls], ["idle", "start"])
        orden.start.assert_called_once_with("¡Libera espacio!", None)
        host._add_message.assert_not_called()  # la frase ya la mostró la transcripción


class DiskAlertTests(unittest.TestCase):
    def test_full_disk_alerts_once_a_day_per_disk(self) -> None:
        import queue

        import jarvis_hardware as hw
        from jarvis_system_info import Disco

        GB = 1024**3
        reloj = {"t": 0.0}
        discos = [Disco("C:", "", 56 * GB, 927 * GB), Disco("D:", "Juegos", 300 * GB, 466 * GB)]
        eventos: queue.Queue = queue.Queue()
        vigilante = hw.VigilanteSalud(eventos, leer=lambda: hw.Temperaturas(), reloj=lambda: reloj["t"],
                                      discos=lambda: discos)
        vigilante.revisar_discos()
        tipo, texto, clave = eventos.get_nowait()
        self.assertEqual((tipo, clave), ("disk_alert", "C::lleno"))
        self.assertIn("El disco C: está al 94 %", texto)
        self.assertTrue(eventos.empty())  # D: está al 36 %
        reloj["t"] = 3600
        vigilante.revisar_discos()
        self.assertTrue(eventos.empty())  # una vez al día
        reloj["t"] = hw.PAUSA_AVISOS_DISCO + 1
        vigilante.revisar_discos()
        self.assertEqual(eventos.get_nowait()[0], "disk_alert")


class PhoneActionCardTests(unittest.TestCase):
    def test_successful_phone_media_actions_leave_no_card(self) -> None:
        """Mover un deslizador en la pestaña Música no llena el chat de tarjetas."""
        from jarvis_actions import EstadoAccion, ResultadoAccion
        from jarvis_app import JarvisApp

        def resultado(origen, estado):
            return ResultadoAccion("set_system_volume", "Ajustar volumen del sistema", origen, estado, 0.1)

        host = Mock()
        host._closing = False
        host._last_action_cards = {}
        host.events = queue.Queue()
        host.events.put(("action_result", resultado("celular", EstadoAccion.EXITO)))
        JarvisApp._poll_events(host)
        host._add_action_card.assert_not_called()
        host.events.put(("action_result", resultado("celular", EstadoAccion.ERROR)))
        host.events.put(("action_result", resultado("frase", EstadoAccion.EXITO)))
        JarvisApp._poll_events(host)
        self.assertEqual(host._add_action_card.call_count, 2)
