"""Temporizadores, alarmas y recordatorios. Usa una base de datos temporal."""

import queue
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_core as core
import jarvis_reminders as r
from jarvis_app import JarvisApp
from jarvis_tools import crear_registro_herramientas

AHORA = datetime(2026, 11, 6, 13, 42)


class _DbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        for patcher in (
            patch.object(core, "DB_PATH", Path(temp_dir.name) / "jarvis_memory.db"),
            patch.object(core, "_database_initialized", False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        core.inicializar_base_datos()


class ParsingTests(unittest.TestCase):
    def _cmd(self, texto: str):
        return r.detectar_comando(texto, AHORA)

    def test_timers(self) -> None:
        casos = {
            "Pon un temporizador de 10 minutos": 600,
            "temporizador de 1 hora y media": 5400,
            "pon un timer de 30 segundos": 30,
            "avísame en 5 minutos": 300,
            "pon un temporizador de media hora": 1800,
            "pon un temporizador de un minuto y medio": 90,
            "pon un temporizador de dos horas y quince minutos": 8100,
            "pon un temporizador de veinticinco minutos": 1500,
            "pon un temporizador de treinta y cinco minutos": 2100,
        }
        for texto, segundos in casos.items():
            with self.subTest(texto=texto):
                herramienta, args = self._cmd(texto)
                self.assertEqual(herramienta, "set_reminder")
                self.assertEqual(args["kind"], "timer")
                self.assertEqual(args["delay_seconds"], segundos)

    def test_timer_label_keeps_accents(self) -> None:
        _, args = self._cmd("pon un timer de 8 minutos para los huevos de Iñaki")
        self.assertEqual(args["text"], "los huevos de Iñaki")

    def test_reminders_in_a_while(self) -> None:
        for texto in (
            "Recuérdame en 20 minutos sacar la ropa",
            "recuérdame sacar la ropa en 20 minutos",
            "Jarvis, recordame que tengo que sacar la ropa dentro de 20 minutos",
        ):
            with self.subTest(texto=texto):
                _, args = self._cmd(texto)
                self.assertEqual(args["kind"], "reminder")
                self.assertEqual(args["delay_seconds"], 1200)
                self.assertIn("sacar la ropa", args["text"])

    def test_reminders_at_a_time(self) -> None:
        casos = {
            "Recuérdame a las 18:30 llamar a mamá": ("2026-11-06 18:30", "llamar a mamá"),
            "recuérdame mañana a las 9 pagar la luz": ("2026-11-07 09:00", "pagar la luz"),
            "recuérdame llamar a Juan a las 6": ("2026-11-06 18:00", "llamar a Juan"),
            "recuérdame ir al médico a las 8 y media de la mañana": ("2026-11-07 08:30", "ir al médico"),
            "recuérdame regar a las 10 de la noche": ("2026-11-06 22:00", "regar"),
            "recuérdame pasado mañana a las 7 y cuarto sacar la basura": (
                "2026-11-08 07:15", "sacar la basura"
            ),
        }
        for texto, (cuando, que) in casos.items():
            with self.subTest(texto=texto):
                _, args = self._cmd(texto)
                self.assertEqual(args["at"], cuando)
                self.assertEqual(args["text"], que)

    def test_alarms(self) -> None:
        self.assertEqual(self._cmd("pon una alarma para mañana a las 7")[1]["at"], "2026-11-07 07:00")
        self.assertEqual(self._cmd("despiértame a las 7 de la mañana")[1]["at"], "2026-11-07 07:00")
        self.assertEqual(self._cmd("pon una alarma a las 15")[1]["at"], "2026-11-06 15:00")

    def test_list_and_cancel(self) -> None:
        self.assertEqual(self._cmd("¿qué recordatorios tengo?"), ("list_reminders", {}))
        self.assertEqual(self._cmd("¿cuánto falta del temporizador?"), ("list_reminders", {}))
        self.assertEqual(self._cmd("cancela el temporizador"), ("cancel_reminder", {"kind": "timer"}))
        self.assertEqual(
            self._cmd("cancela el recordatorio 3"), ("cancel_reminder", {"kind": "reminder", "id": 3})
        )
        self.assertEqual(
            self._cmd("borra todas las alarmas"), ("cancel_reminder", {"kind": "alarm", "all": True})
        )

    def test_not_reminders(self) -> None:
        for texto in (
            "recuérdame que prefiero Brave",  # memoria, no recordatorio
            "pon el volumen a 30",
            "cancela el apagado",
            "¿qué hora es?",
            "pon un temporizador",
        ):
            with self.subTest(texto=texto):
                self.assertIsNone(self._cmd(texto))

    def test_impossible_time(self) -> None:
        self.assertIsNone(self._cmd("recuérdame a las 25 algo"))


class TextTests(unittest.TestCase):
    def test_durations(self) -> None:
        self.assertEqual(r.describir_duracion(600), "10 minutos")
        self.assertEqual(r.describir_duracion(5400), "1 hora y 30 minutos")
        self.assertEqual(r.describir_duracion(61), "1 minuto y 1 segundo")
        self.assertEqual(r.describir_duracion(3600), "1 hora")

    def test_moments(self) -> None:
        self.assertEqual(r.describir_momento(datetime(2026, 11, 6, 18, 30), AHORA), "hoy a las 18:30")
        self.assertEqual(r.describir_momento(datetime(2026, 11, 7, 1, 5), AHORA), "mañana a la 1:05")
        self.assertIn("lunes 9 de noviembre", r.describir_momento(datetime(2026, 11, 9, 9, 0), AHORA))

    def test_due_message(self) -> None:
        aviso = r.Recordatorio(1, "recordatorio", "sacar la ropa", AHORA)
        self.assertEqual(r.describir_aviso(aviso, AHORA), "Recordatorio: sacar la ropa.")
        tarde = r.describir_aviso(aviso, AHORA + timedelta(hours=2))
        self.assertIn("Jarvis no estaba abierto", tarde)
        timer = r.Recordatorio(2, "temporizador", "", AHORA)
        self.assertEqual(r.describir_aviso(timer, AHORA), "Se acabó el temporizador.")


class DatabaseTests(_DbTestCase):
    def test_create_list_claim_once(self) -> None:
        creado = r.crear("recordatorio", "sacar la ropa", AHORA + timedelta(minutes=20), ahora=AHORA)
        self.assertEqual([x.id for x in r.pendientes()], [creado.id])
        self.assertEqual(r.reclamar_vencidos(AHORA + timedelta(minutes=19)), [])
        vencidos = r.reclamar_vencidos(AHORA + timedelta(minutes=20))
        self.assertEqual([x.texto for x in vencidos], ["sacar la ropa"])
        self.assertEqual(r.reclamar_vencidos(AHORA + timedelta(minutes=30)), [])
        self.assertEqual(r.pendientes(), [])

    def test_rejects_past_and_far_future(self) -> None:
        with self.assertRaises(ValueError):
            r.crear("alarma", "", AHORA - timedelta(minutes=1), ahora=AHORA)
        with self.assertRaises(ValueError):
            r.crear("alarma", "", AHORA + timedelta(days=61), ahora=AHORA)

    def test_cancel_next_by_kind_and_all(self) -> None:
        t1 = r.crear("temporizador", "", AHORA + timedelta(minutes=5), ahora=AHORA)
        t2 = r.crear("temporizador", "", AHORA + timedelta(minutes=10), ahora=AHORA)
        rec = r.crear("recordatorio", "x", AHORA + timedelta(minutes=1), ahora=AHORA)
        self.assertEqual([x.id for x in r.cancelar(tipo="temporizador")], [t1.id])
        self.assertEqual({x.id for x in r.pendientes()}, {t2.id, rec.id})
        self.assertEqual(len(r.cancelar(todos=True)), 2)
        self.assertEqual(r.pendientes(), [])

    def test_tools(self) -> None:
        registry = crear_registro_herramientas(Mock())
        respuesta = registry.execute(
            "set_reminder", {"kind": "timer", "delay_seconds": 600}, confirmed=False
        )
        self.assertIn("Temporizador de 10 minutos", respuesta)
        with self.assertRaises(Exception):  # más allá de 60 días
            registry.execute("set_reminder", {"kind": "reminder", "text": "x", "at": "2099-01-01 09:00"})
        with self.assertRaises(Exception):
            registry.execute("set_reminder", {"kind": "reminder", "delay_seconds": 60})  # sin texto
        self.assertIn("Tienes 1 aviso pendiente", registry.execute("list_reminders", {}))
        self.assertIn("Cancelé el temporizador", registry.execute("cancel_reminder", {"kind": "timer"}))
        self.assertIn("No hay", registry.execute("cancel_reminder", {}))


class WatcherTests(unittest.TestCase):
    def test_publishes_due_reminders(self) -> None:
        events: queue.Queue[tuple] = queue.Queue()
        aviso = r.Recordatorio(7, "temporizador", "", AHORA)
        respuestas = [[aviso], []]
        vigilante: r.VigilanteRecordatorios

        def reclamar():
            if len(respuestas) == 1:
                vigilante.stop()
            return respuestas.pop(0)

        vigilante = r.VigilanteRecordatorios(events, intervalo=0.001, reclamar=reclamar)
        vigilante.start()
        vigilante.join(timeout=2)
        self.assertEqual(events.get_nowait(), ("reminder_due", aviso))

    def test_alarm_sound_file(self) -> None:
        import os
        import wave

        ruta = os.path.join(tempfile.mkdtemp(), "alarma.wav")
        r.generar_alarma(ruta)
        try:
            with wave.open(ruta) as archivo:
                self.assertGreater(archivo.getnframes() / archivo.getframerate(), 1.5)
        finally:
            os.remove(ruta)


class AnnounceTests(unittest.TestCase):
    @patch("jarvis_app.threading.Thread")
    def test_announce_does_not_touch_busy_state(self, thread: Mock) -> None:
        host = Mock()
        JarvisApp._announce(host, "Recordatorio: sacar la ropa.", alarm=True)
        host._add_message.assert_called_once_with("assistant", "Recordatorio: sacar la ropa.")
        host.tray_icon.notify.assert_called_once()
        host._set_idle.assert_not_called()
        host._start_busy.assert_not_called()
        thread.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
