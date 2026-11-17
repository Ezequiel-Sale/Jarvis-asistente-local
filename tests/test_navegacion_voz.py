"""Navegar por las secciones de Jarvis por voz («Jarvis rutinas», «ve a ajustes»…).

No abre ventanas: la app es un Mock.
"""

import queue
import unittest
from unittest.mock import Mock, patch

from jarvis_app import JarvisApp
from jarvis_app_voz import palabra_suelta_desconocida
from jarvis_commands import detectar_navegacion, limpiar_transcripcion_voz


class PhraseTests(unittest.TestCase):
    def test_sections(self) -> None:
        casos = {
            "Jarvis chat": "chat",
            "Jarvis rutinas": "rutinas",
            "Jarvis inicio": "inicio",
            "Jarvis, juegos": "juegos",
            "rutinas": "rutinas",
            "ve a ajustes": "ajustes",
            "abre la configuración": "ajustes",
            "muéstrame la tele": "tele",
            "vuelve al inicio": "inicio",
            "llévame a la sección de juegos": "juegos",
            "Jarvis, el panel principal": "inicio",
            "Jarvis, comandos": "comandos",
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_navegacion(texto), esperado)

    def test_other_orders_are_not_navigation(self) -> None:
        for texto in ("abre Steam", "prende la tele", "pon la tele en HDMI 1", "abre el chat de Discord",
                      "inicio en la tele", "configuración de la tele", "qué rutinas tengo",
                      "abre el juego de fútbol"):
            with self.subTest(texto=texto):
                self.assertIsNone(detectar_navegacion(texto))


class AppTests(unittest.TestCase):
    def _host(self) -> Mock:
        host = Mock()
        host._PAGE_TITLES = JarvisApp._PAGE_TITLES
        host.busy = True
        return host

    def test_shows_the_window_and_the_page(self) -> None:
        host = self._host()
        JarvisApp._navigate_by_voice(host, "rutinas", "Jarvis rutinas")
        host._show_from_tray.assert_called_once()
        host._show_page.assert_called_once_with("rutinas")
        host._set_idle.assert_called_once()  # venía de una orden de voz «ocupada»
        self.assertEqual(host._set_status.call_args.args[0], "RUTINAS Y AUTOMATIZACIONES")

    def test_commands_open_the_guide(self) -> None:
        host = self._host()
        JarvisApp._navigate_by_voice(host, "comandos", "Jarvis comandos")
        host._show_commands_help.assert_called_once()
        host._show_page.assert_not_called()

    def test_voice_worker_routes_navigation(self) -> None:
        host = Mock()
        host.events = queue.Queue()
        host._find_phrase_routine.return_value = None
        host._split_orders.return_value = None  # una sola orden
        with patch("jarvis_core.procesar_stt"):
            JarvisApp._voice_worker(host, None, 0.0, "c", transcript="Jarvis rutinas")
        eventos = [host.events.get_nowait() for _ in range(host.events.qsize())]
        self.assertIn(("navigate", "rutinas", "Jarvis rutinas"), eventos)

    def test_typed_text_routes_navigation(self) -> None:
        host = Mock()
        host._find_phrase_routine.return_value = None
        host._split_orders.return_value = None  # una sola orden
        host.input.get.return_value = "ve a ajustes"
        host.busy = False
        host.recorder = None
        with patch("jarvis_app_ordenes.detectar_borrado_historial", return_value=False):
            JarvisApp._send_text(host)
        host._navigate_by_voice.assert_called_once_with("ajustes", "ve a ajustes")


class ContinuousTests(unittest.TestCase):
    def _directa(self, texto):
        return JarvisApp._direct_tool_for(Mock(), texto)

    def test_single_unknown_word_is_ignored(self) -> None:
        """2026-11-16: «Yerda» (0,5 s de voz) fue a la IA: «No entendí tu solicitud»."""
        with patch("jarvis_routines.buscar_rutina_por_frase", return_value=None):
            self.assertTrue(palabra_suelta_desconocida("Yerda", self._directa))
            for texto in ("pausa", "chat", "rutinas", "abre Steam"):
                with self.subTest(texto=texto):
                    self.assertFalse(palabra_suelta_desconocida(texto, self._directa))
        with patch("jarvis_routines.buscar_rutina_por_frase", return_value=Mock()):
            self.assertFalse(palabra_suelta_desconocida("cine", self._directa))  # frase de una rutina

    def test_pantaja_is_pantalla(self) -> None:
        self.assertEqual(limpiar_transcripcion_voz("Pantaja completa"), "pantalla completa")
        self.assertEqual(limpiar_transcripcion_voz("los pantalones"), "los pantalones")


if __name__ == "__main__":
    unittest.main()
