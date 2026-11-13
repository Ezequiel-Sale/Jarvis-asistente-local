"""Fecha y hora de los mensajes del chat. Base de datos temporal, nunca la real."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_cards as c
import jarvis_core as core
import jarvis_reminders


class MessageTimeTests(unittest.TestCase):
    def test_format(self) -> None:
        ahora = datetime(2026, 11, 12, 14, 30)
        self.assertEqual(c.hora_mensaje(datetime(2026, 11, 12, 9, 5), ahora), "12/11 09:05")
        self.assertEqual(c.hora_mensaje(datetime(2025, 12, 31, 23, 59), ahora), "31/12/2025 23:59")
        self.assertRegex(c.hora_mensaje(), r"^\d{2}/\d{2} \d{2}:\d{2}$")


class HistoryWithTimeTests(unittest.TestCase):
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

    def test_history_brings_local_time(self) -> None:
        conversacion = core.crear_conversacion()
        antes = datetime.now().replace(microsecond=0)
        core.guardar_turno(conversacion, "hola", "Hola, ¿en qué te ayudo?")
        historial = core.obtener_historial_con_fecha(conversacion)
        self.assertEqual([(rol, texto) for rol, texto, _ in historial],
                         [("user", "hola"), ("assistant", "Hola, ¿en qué te ayudo?")])
        for _, _, momento in historial:
            # Hora local (SQLite la guarda en UTC), no la de Greenwich.
            self.assertLess(abs((momento - antes).total_seconds()), 120)
        self.assertEqual(core.obtener_historial(conversacion), [(r, t) for r, t, _ in historial])


class ExactUserTimeTests(HistoryWithTimeTests):
    def test_user_message_keeps_the_time_it_was_shown(self) -> None:
        conversacion = core.crear_conversacion()
        with patch.object(core, "_ahora_sqlite", return_value="2026-11-13 12:00:00.000"):
            core.marcar_mensaje_usuario(conversacion, "¿qué es Python?")
        # La respuesta tarda: el turno se guarda después.
        with patch.object(core, "_ahora_sqlite", return_value="2026-11-13 12:00:15.000"):
            core.guardar_turno(conversacion, "¿qué es Python?", "Un lenguaje.")
        (_, _, usuario), (_, _, respuesta) = core.obtener_historial_con_fecha(conversacion)
        self.assertEqual((respuesta - usuario).total_seconds(), 15)

    def test_without_mark_uses_now(self) -> None:
        conversacion = core.crear_conversacion()
        core.guardar_turno(conversacion, "hola", "hola")
        (_, _, usuario), (_, _, respuesta) = core.obtener_historial_con_fecha(conversacion)
        self.assertLess(abs((respuesta - usuario).total_seconds()), 2)

    def test_marks_are_bounded(self) -> None:
        for n in range(core._MOMENTOS_MAX + 10):
            core.marcar_mensaje_usuario("c", str(n))
        self.assertLessEqual(len(core._MOMENTOS_USUARIO), core._MOMENTOS_MAX)
        core._MOMENTOS_USUARIO.clear()


class ReminderToolTextTests(unittest.TestCase):
    def test_reminder_tool_says_it_does_not_power_off(self) -> None:
        registro = Mock()
        jarvis_reminders.registrar_herramientas(registro)
        descripcion = next(llamada.args[1] for llamada in registro.register.call_args_list
                           if llamada.args[0] == "set_reminder")
        self.assertIn("NO apaga, suspende ni reinicia", descripcion)
        self.assertIn("schedule_routine", descripcion)


if __name__ == "__main__":
    unittest.main()
