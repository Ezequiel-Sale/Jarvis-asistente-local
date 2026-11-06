import unittest
from datetime import datetime
from unittest.mock import Mock, patch

import jarvis_system_info as info
from jarvis_app import JarvisApp
from jarvis_commands import detectar_consulta_fecha_hora
from jarvis_tools import crear_registro_herramientas

VIERNES = datetime(2026, 11, 6, 13, 42)


class DateTimeTextTests(unittest.TestCase):
    def test_time(self) -> None:
        self.assertEqual(info.consultar_fecha_hora("time", ahora=VIERNES), "Son las 13:42.")
        self.assertEqual(
            info.consultar_fecha_hora("time", ahora=datetime(2026, 1, 1, 1, 5)), "Es la 1:05."
        )

    def test_date(self) -> None:
        self.assertEqual(
            info.consultar_fecha_hora("date", ahora=VIERNES),
            "Hoy es viernes 6 de noviembre de 2026.",
        )
        self.assertIn(
            "miércoles 1 de enero",
            info.consultar_fecha_hora("date", ahora=datetime(2025, 1, 1)),
        )

    def test_both_and_agent_context(self) -> None:
        self.assertEqual(
            info.consultar_fecha_hora(ahora=VIERNES),
            "Hoy es viernes 6 de noviembre de 2026. Son las 13:42.",
        )
        self.assertEqual(
            info.contexto_fecha_hora(VIERNES),
            "Fecha y hora actuales: viernes 6 de noviembre de 2026, 13:42.",
        )


class DateTimeCommandTests(unittest.TestCase):
    def test_phrases(self) -> None:
        casos = {
            "¿Qué hora es?": "time",
            "Jarvis, ¿qué hora es ahora?": "time",
            "dime la hora": "time",
            "me decís la hora": "time",
            "¿Qué día es hoy?": "date",
            "¿a qué fecha estamos?": "date",
            "qué día de la semana es": "date",
            "dime la fecha de hoy": "date",
            "dime la fecha y la hora": "both",
            "¿qué día y hora es?": "both",
        }
        for texto, que in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(
                    detectar_consulta_fecha_hora(texto), ("get_current_datetime", {"what": que})
                )

    def test_other_questions_go_to_the_model(self) -> None:
        for texto in ("¿qué hora es en Japón?", "¿qué día es el partido?", "pon la hora en el reloj"):
            with self.subTest(texto=texto):
                self.assertIsNone(detectar_consulta_fecha_hora(texto))


class DateTimeToolTests(unittest.TestCase):
    def test_tool_is_low_risk_and_returns_text(self) -> None:
        registry = crear_registro_herramientas(Mock())
        tool = registry.get("get_current_datetime")
        self.assertFalse(tool.requires_confirmation)
        self.assertRegex(
            registry.execute("get_current_datetime", {"what": "time"}),
            r"^(?:Son las|Es la) \d{1,2}:\d{2}\.$",
        )

    def test_direct_answer_is_spoken(self) -> None:
        host = Mock()
        host.tool_registry = crear_registro_herramientas(Mock())
        with patch("jarvis_app.core.guardar_turno"), patch(
            "jarvis_app.core.generar_audio_tts", return_value="hora.wav"
        ) as tts:
            JarvisApp._direct_tool_worker(
                host, "get_current_datetime", {"what": "time"}, "¿qué hora es?", "c1", None
            )
        evento = host.events.put.call_args.args[0]
        self.assertEqual(evento[0], "answer")
        self.assertTrue(evento[1].startswith(("Son las", "Es la")))
        self.assertEqual(evento[2], "hora.wav")
        tts.assert_called_once_with(evento[1])


if __name__ == "__main__":
    unittest.main()
