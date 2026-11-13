"""«¿Qué puedes hacer?» por voz y correcciones de errores típicos de la transcripción."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jarvis_comandos as c
from jarvis_app import JarvisApp
from jarvis_commands import (
    detectar_comando_monitores,
    detectar_comando_multimedia,
    detectar_comando_ventanas,
    limpiar_transcripcion_voz,
)


class HelpPhraseTests(unittest.TestCase):
    def test_general_help(self) -> None:
        for texto in ("¿Qué puedes hacer?", "qué comandos tienes", "muéstrame los comandos", "ayuda",
                      "¿qué puedo decirte?", "comandos de voz", "Hey Jarvis, ¿qué sabes hacer?"):
            with self.subTest(texto=texto):
                self.assertEqual(c.detectar_ayuda(texto), ("show_commands_help", {}))

    def test_help_about_a_topic(self) -> None:
        casos = {"¿Qué puedes hacer con los monitores?": "Monitores",
                 "qué puedes hacer con la música": "Vídeos y música",
                 "ayuda con el volumen": "Volumen y sonido",
                 "qué sabes hacer con los recordatorios": "Recordatorios y temporizadores"}
        for texto, titulo in casos.items():
            with self.subTest(texto=texto):
                _, argumentos = c.detectar_ayuda(texto)
                self.assertEqual(c.seccion_de_tema(argumentos["topic"]).titulo, titulo)

    def test_other_questions_go_to_the_ai(self) -> None:
        self.assertIsNone(c.detectar_ayuda("¿qué puedes hacer con este error?"))
        self.assertIsNone(c.detectar_ayuda("pon ayuda en youtube"))
        self.assertIsNone(c.detectar_ayuda("busca en youtube comandos de linux"))

    def test_spoken_answers(self) -> None:
        general = c.respuesta_ayuda()
        self.assertIn("monitores", general)
        self.assertIn("? COMANDOS", general)
        propias = c.Seccion("⭐", "Tus comandos (rutinas)", (c.Comando("modo gaming 2k", "x"),))
        self.assertIn("«modo gaming 2k»", c.respuesta_ayuda("", propias))
        self.assertIn("«Desactiva el monitor 3»", c.respuesta_ayuda("monitores"))


class HelpToolTests(unittest.TestCase):
    @patch("jarvis_app.jarvis_routines.listar_automatizaciones", return_value=[])
    def test_tool_opens_the_window_and_answers(self, _rutinas) -> None:
        host = Mock()
        texto = JarvisApp._tool_show_commands_help(host, "monitores")
        host.events.put.assert_called_once_with(("show_commands_help", "monitores"))
        self.assertIn("Para monitores", texto)

    def test_window_filters_by_topic_and_not_from_the_tray(self) -> None:
        host = Mock()
        host.root.state.return_value = "normal"
        JarvisApp._open_commands_help(host, "la música")
        host._show_commands_help.assert_called_once_with("Vídeos y música")
        host = Mock()
        host.root.state.return_value = "withdrawn"
        JarvisApp._open_commands_help(host, "")
        host._show_commands_help.assert_not_called()


class VoiceCorrectionTests(unittest.TestCase):
    def test_typical_errors_become_commands(self) -> None:
        # Errores reales del modelo de voz (comparación del 2026-11-13).
        casos = [
            ("Con el vídeo en pantalla completa.", detectar_comando_multimedia),
            ("Ausa el vídeo del monitor 1 y ranuda el del monitor 2.", detectar_comando_multimedia),
            ("Con el volumen de Spotify el 30.", detectar_comando_multimedia),
            ("Pon el volumen despoticida el 30.", detectar_comando_multimedia),
            ("Arela el escalado del monitor 3.", detectar_comando_monitores),
            ("Regla el escalado del monitor 3.", detectar_comando_monitores),
            ("Minimiza discorda.", detectar_comando_ventanas),
        ]
        for texto, detector in casos:
            with self.subTest(texto=texto):
                self.assertIsNotNone(detector(limpiar_transcripcion_voz(texto)), limpiar_transcripcion_voz(texto))

    def test_routine_phrases(self) -> None:
        for texto in ("Modo gamintosca.", "Modo Camin 2K", "Modogaming 2K.", "Modo gaming tosca."):
            with self.subTest(texto=texto):
                self.assertEqual(limpiar_transcripcion_voz(texto).rstrip(".").casefold(), "modo gaming 2k")
        self.assertEqual(limpiar_transcripcion_voz("Supende el equipo dentro de 10 minutos."),
                         "suspende el equipo dentro de 10 minutos.")

    def test_normal_sentences_are_untouched(self) -> None:
        for texto in ("libera el disco", "con el tiempo que hace hoy", "regla de tres", "pon un disco de vinilo"):
            with self.subTest(texto=texto):
                self.assertEqual(limpiar_transcripcion_voz(texto), texto)


if __name__ == "__main__":
    unittest.main()
