"""Doctor de la PC (jarvis_doctor): eventos simulados, sin leer los registros de Windows."""

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_doctor as d
import jarvis_temas
from jarvis_tools import crear_registro_herramientas

AHORA = datetime(2026, 11, 19, 10, 0)


def ev(minutos_atras, proveedor, id_, dato="", nivel=2):
    return d.Evento(AHORA - timedelta(minutes=minutos_atras), proveedor, id_, "", dato, nivel)


class CuelguesTests(unittest.TestCase):
    def test_unexpected_shutdown_with_gpu_hang_before(self):
        eventos = [ev(605, "Display", 4101, nivel=3),
                   ev(600, "Microsoft-Windows-Kernel-Power", 41, nivel=1),
                   ev(598, "EventLog", 6008)]
        texto = d.diagnosticar_cuelgues(eventos, AHORA)
        self.assertIn("Encontré 1 caída inesperada", texto)
        self.assertIn("Hoy a las 00:00", texto)
        self.assertIn("controlador de video", texto)

    def test_blue_screen_yesterday(self):
        eventos = [ev(60 * 14, "Microsoft-Windows-WER-SystemErrorReporting", 1001, nivel=2)]
        texto = d.diagnosticar_cuelgues(eventos, AHORA)
        self.assertIn("Ayer a las 20:00 el equipo tuvo una pantalla azul", texto)

    def test_no_errors_before_suggests_power(self):
        texto = d.diagnosticar_cuelgues([ev(30, "Microsoft-Windows-Kernel-Power", 41, nivel=1)], AHORA)
        self.assertIn("corte de luz", texto)

    def test_nothing_unexpected(self):
        eventos = [ev(120, "User32", 1074, dato="C:\\Windows\\System32\\shutdown.exe", nivel=4),
                   ev(30, "Application Hang", 1002, dato="lichii.exe")]
        texto = d.diagnosticar_cuelgues(eventos, AHORA)
        self.assertIn("No encontré apagados ni reinicios inesperados", texto)
        self.assertIn("shutdown.exe", texto)
        self.assertIn("lichii.exe", texto)

    def test_same_number_from_another_provider_is_not_a_crash(self):
        """El 55 de Kernel-Processor-Power no es un error de disco; el 41 de otro proveedor no es una caída."""
        eventos = [ev(10, "Microsoft-Windows-Kernel-Processor-Power", 55, nivel=4), ev(5, "Otro", 41)]
        self.assertIn("No encontré", d.diagnosticar_cuelgues(eventos, AHORA))
        self.assertIn("No hay errores de hardware", d.resumir_errores(eventos))


class ErroresYDiscosTests(unittest.TestCase):
    def test_summary_counts_one_crash_per_episode(self):
        eventos = [ev(60, "Microsoft-Windows-Kernel-Power", 41, nivel=1), ev(59, "EventLog", 6008),
                   ev(30, "Application Error", 1000, dato="eldenring.exe"),
                   ev(20, "Application Error", 1000, dato="eldenring.exe"),
                   ev(10, "Microsoft-Windows-DistributedCOM", 10016)]
        texto = d.resumir_errores(eventos)
        self.assertIn("1 apagados inesperados", texto)
        self.assertIn("eldenring.exe (2)", texto)
        self.assertIn("DistributedCOM (1)", texto)

    def test_disks(self):
        sanos = [{"nombre": "KINGSTON", "salud": "Healthy", "temp": 38, "desgaste": 3, "lectura": 0}]
        self.assertEqual(d.diagnosticar_discos(sanos, []),
                         "Los discos están bien. KINGSTON: sano (38 °C, desgaste 3 %).")
        malo = [{"nombre": "TOSHIBA", "salud": "Warning", "lectura": 12}]
        texto = d.diagnosticar_discos(malo, [ev(5, "disk", 7)])
        self.assertIn("Revisá los discos", texto)
        self.assertIn("12 errores de lectura", texto)
        self.assertIn("1 errores de disco", texto)

    def test_question_is_validated(self):
        with patch.object(d, "leer_eventos", return_value=[]):
            with self.assertRaises(ValueError):
                d.consultar("todo")


class FrasesTests(unittest.TestCase):
    def test_direct_phrases(self):
        self.assertEqual(d.detectar("¿Por qué se colgó la PC anoche?"), ("pc_doctor", {"question": "crashes", "hours": 48}))
        self.assertEqual(d.detectar("por qué se reinició"), ("pc_doctor", {"question": "crashes", "hours": 48}))
        self.assertEqual(d.detectar("¿Están sanos los discos?"), ("pc_doctor", {"question": "disks"}))
        self.assertEqual(d.detectar("¿Hubo errores esta semana?"), ("pc_doctor", {"question": "errors", "hours": 168}))
        self.assertIsNone(d.detectar("¿Por qué se apagó la tele?"))

    def test_routed_and_offered_by_topic(self):
        fuente = (Path(__file__).resolve().parent.parent / "jarvis_app_ordenes.py").read_text("utf-8")
        self.assertIn("jarvis_doctor.detectar(text)", fuente)
        self.assertIn("jarvis_doctor.detectar(transcript)", fuente)
        tools = crear_registro_herramientas(Mock()).ollama_tools()
        nombres = lambda t: {x["function"]["name"] for x in t}  # noqa: E731
        self.assertIn("pc_doctor", nombres(jarvis_temas.elegir(tools, "la compu se tildó dos veces")))
        self.assertNotIn("pc_doctor", nombres(jarvis_temas.elegir(tools, "poné música")))


if __name__ == "__main__":
    unittest.main()
