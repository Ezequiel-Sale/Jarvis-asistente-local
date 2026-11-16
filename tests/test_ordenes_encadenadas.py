"""«X luego Y»: varias órdenes directas en una frase, sin la IA (sin tocar ventanas ni la tele reales)."""

import unittest
from unittest.mock import Mock, patch

import jarvis_routines
from jarvis_app import JarvisApp
from jarvis_commands import dividir_ordenes, limpiar_transcripcion_voz


def _pasos(texto):
    host = Mock()
    host._direct_tool_for = lambda t: JarvisApp._direct_tool_for(host, t)
    with patch.object(jarvis_routines, "buscar_rutina_por_frase", return_value=None):
        rutina = JarvisApp._chained_orders(host, limpiar_transcripcion_voz(texto))
    return rutina and [(p.tool, p.arguments) for p in rutina.steps]


class ChainedOrdersTests(unittest.TestCase):
    def test_split_only_on_luego_despues(self) -> None:
        self.assertEqual(dividir_ordenes("trae a Jarvis al frente luego en el monitor 2 coloca Jarvis a la "
                                         "izquierda y Claude a la derecha"),
                         ["trae a Jarvis al frente",
                          "en el monitor 2 coloca Jarvis a la izquierda y Claude a la derecha"])
        self.assertEqual(dividir_ordenes("prende la tele, luego ponla en HDMI 1 y después baja el volumen"),
                         ["prende la tele", "ponla en HDMI 1", "baja el volumen"])
        self.assertEqual(dividir_ordenes("pausa el video y reanuda el otro"), [])  # «y» solo no parte

    def test_the_case_that_went_to_the_ai(self) -> None:
        """La IA contestaba «Trayendo al frente…» sin hacer nada (2026-11-15)."""
        self.assertEqual(_pasos("Trae a Jarvis al frente luego en el monitor 2 coloca Jarvis a la izquierda "
                                "y Claude a la derecha."),
                         [("window_control", {"app": "jarvis", "action": "window_focus"}),
                          ("arrange_windows", {"windows": [{"app": "jarvis", "position": "left"},
                                                           {"app": "claude", "position": "right"}], "monitor": 2})])

    def test_pronouns_refer_to_the_previous_part(self) -> None:
        self.assertEqual(_pasos("prende la tele, luego ponla en HDMI 1"),
                         [("tv_control", {"action": "power_on"}), ("tv_control", {"action": "hdmi1"})])
        self.assertEqual(_pasos("silencia la tele luego pausala")[1], ("tv_control", {"action": "pause"}))
        self.assertEqual(_pasos("pon Brave a la derecha luego muevelo al monitor 2")[1],
                         ("move_window", {"app": "brave", "monitor": 2}))
        self.assertEqual(_pasos("minimiza Discord luego maximizalo")[1],
                         ("window_control", {"app": "discord", "action": "window_maximize"}))

    def test_unknown_part_goes_to_the_ai(self) -> None:
        self.assertIsNone(_pasos("pausa el video luego explicame la fisica cuantica"))
        self.assertIsNone(_pasos("minimiza Discord"))  # una sola orden: no es una cadena


if __name__ == "__main__":
    unittest.main()
