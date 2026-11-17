"""Varias oraciones en una frase, «pon HDMI 2» sin «tele» y «CloudCode» (2026-11-17). Sin ventanas reales."""

import queue
import unittest
from unittest.mock import Mock, patch

import jarvis_tv
from jarvis_app import JarvisApp
from jarvis_commands import dividir_oraciones, limpiar_transcripcion_voz


class SentenceTests(unittest.TestCase):
    def test_split_and_drop_courtesy(self) -> None:
        self.assertEqual(dividir_oraciones("Desactiva los gestos. Gracias. ¿Qué clima hace?"),
                         ["Desactiva los gestos", "¿Qué clima hace?"])
        self.assertEqual(dividir_oraciones("Pausa. Gracias."), ["Pausa"])
        self.assertEqual(dividir_oraciones("Abre Steam"), ["Abre Steam"])
        self.assertEqual(dividir_oraciones("Gracias."), [])


def _host() -> Mock:
    host = Mock()
    host._closing = False
    host.busy = False
    host.recorder = None
    host._pending_plan_reply = None
    host.ESPERA_SIGUIENTE_ORDEN_MS = JarvisApp.ESPERA_SIGUIENTE_ORDEN_MS
    host._is_known_order = lambda texto: JarvisApp._is_known_order(host, texto)
    host._direct_tool_for = lambda texto: JarvisApp._direct_tool_for(host, texto)
    host._split_orders = lambda texto: JarvisApp._split_orders(host, texto)
    host._drain_pending_orders = lambda: JarvisApp._drain_pending_orders(host)
    return host


class SplitOrdersTests(unittest.TestCase):
    @patch("jarvis_routines.buscar_rutina_por_frase", return_value=None)
    def test_the_real_case(self, _rutina: Mock) -> None:
        """Iba entera a la IA, que inventó un plan con HDMI 2 y el escalado."""
        host = _host()
        self.assertEqual(host._split_orders("Desactiva los gestos. Gracias. ¿Qué clima hace?"),
                         ["Desactiva los gestos", "¿Qué clima hace?"])
        self.assertEqual(host._split_orders("Pausa. Gracias."), ["Pausa"])  # solo cortesía de más
        self.assertIsNone(host._split_orders("Abre Steam"))  # una sola: como siempre
        # Ninguna parte es una orden conocida: entera a la IA, con su contexto.
        self.assertIsNone(host._split_orders("Tengo un problema con el disco. ¿Cómo lo arreglo?"))
        self.assertIsNone(host._split_orders("Hola Jarvis, ¿cómo estás?"))

    def test_orders_run_one_at_a_time(self) -> None:
        host = _host()
        JarvisApp._run_orders(host, ["Desactiva los gestos", "¿Qué clima hace?"])
        host._send_text_as_typed.assert_called_once_with("Desactiva los gestos")
        espera = host.root.after.call_args.args[1]
        host.busy = True  # la primera todavía se está haciendo
        espera()
        host._send_text_as_typed.assert_called_once()
        host.busy = False
        espera()
        host._send_text_as_typed.assert_called_with("¿Qué clima hace?")
        self.assertEqual(host._pending_orders, [])

    @patch("jarvis_routines.buscar_rutina_por_frase", return_value=None)
    def test_voice_sends_them_to_the_main_thread(self, _rutina: Mock) -> None:
        host = _host()
        host.events = queue.Queue()
        with patch("jarvis_core.procesar_stt"):
            JarvisApp._voice_worker(host, None, 0.0, "c", transcript="Desactiva los gestos. Gracias. ¿Qué clima hace?")
        self.assertEqual(host.events.get_nowait(), ("multi_orders", ["Desactiva los gestos", "¿Qué clima hace?"]))


class OtherFixesTests(unittest.TestCase):
    def test_hdmi_without_saying_tv(self) -> None:
        """«pon HDMI 2» se tomaba como mover una ventana llamada «hdmi 2»."""
        self.assertEqual(jarvis_tv.detectar("pon HDMI 2"), ("tv_control", {"action": "hdmi2"}))
        self.assertEqual(jarvis_tv.detectar("cambia a HDMI 1"), ("tv_control", {"action": "hdmi1"}))
        self.assertIsNone(jarvis_tv.detectar("abre Steam"))

    def test_cloudcode(self) -> None:
        self.assertEqual(limpiar_transcripcion_voz("Abre CloudCode"), "Abre Claude Code")
        self.assertEqual(limpiar_transcripcion_voz("abre cloud code"), "abre Claude Code")


if __name__ == "__main__":
    unittest.main()
