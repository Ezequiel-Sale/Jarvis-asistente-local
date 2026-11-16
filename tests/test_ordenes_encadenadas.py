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

    def test_y_splits_when_every_part_is_a_direct_order(self) -> None:
        """2026-11-16: «Mueve Brave al monitor 3 y pon pantalla completa» fue a la IA, que lo hizo
        pero respondió «no pude mover la ventana»."""
        self.assertEqual(_pasos("Mueve Brave al monitor 3 y pon pantalla completa"), [
            ("move_window", {"app": "brave", "monitor": 3}),
            ("video_fullscreen", {"enabled": True, "monitor": 3}),  # el monitor del paso anterior
        ])
        self.assertEqual(_pasos("prende la tele y ponla en HDMI 1"),
                         [("tv_control", {"action": "power_on"}), ("tv_control", {"action": "hdmi1"})])
        self.assertIsNone(_pasos("pausa el video y el audio"))  # «el audio» no es una orden
        self.assertIsNone(_pasos("qué es Python y cómo se usa"))

    def test_move_then_place_with_pronoun(self) -> None:
        """2026-11-16: la IA respondió «Puse Jarvis a la izquierda del monitor 2» sin hacer nada."""
        esperado = [("move_window", {"app": "jarvis", "monitor": 2}),
                    ("arrange_windows", {"windows": [{"app": "jarvis", "position": "left"}], "monitor": 2})]
        self.assertEqual(_pasos("Mueve Jarvis al monitor 2 y posiciónalo a la izquierda"), esperado)

    def test_placement_phrases(self) -> None:
        from jarvis_commands import detectar_acomodar_ventanas

        self.assertEqual(detectar_acomodar_ventanas("posiciona Jarvis a la izquierda"),
                         ("arrange_windows", {"windows": [{"app": "jarvis", "position": "left"}]}))
        self.assertEqual(detectar_acomodar_ventanas("mueve Jarvis al monitor 2 a la izquierda"),
                         ("arrange_windows", {"windows": [{"app": "jarvis", "position": "left"}], "monitor": 2}))
        self.assertEqual(detectar_acomodar_ventanas("ubica Brave en la pantalla 3 arriba a la derecha"),
                         ("arrange_windows", {"windows": [{"app": "brave", "position": "top_right"}], "monitor": 3}))

    def test_unknown_part_goes_to_the_ai(self) -> None:
        self.assertIsNone(_pasos("pausa el video luego explicame la fisica cuantica"))
        self.assertIsNone(_pasos("minimiza Discord"))  # una sola orden: no es una cadena


if __name__ == "__main__":
    unittest.main()
