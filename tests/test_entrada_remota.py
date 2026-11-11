"""Touchpad del celular: eventos → entradas de SendInput (sin tocar el sistema) y zona del cursor."""

import unittest

import jarvis_input as ji
import jarvis_vision as v


class InputTests(unittest.TestCase):
    def test_events_become_inputs(self) -> None:
        entradas = ji.eventos_a_entradas([
            {"t": "move", "dx": 5.4, "dy": -3}, {"t": "move", "dx": 0, "dy": 0},
            {"t": "click", "b": "right"}, {"t": "down"}, {"t": "up"},
            {"t": "scroll", "dy": -2}, {"t": "key", "k": "alttab"},
        ])
        self.assertEqual(entradas, [
            ("mouse", 5, -3, ji.MOUSEEVENTF_MOVE, 0),
            ("mouse", 0, 0, 0x0008, 0), ("mouse", 0, 0, 0x0010, 0),
            ("mouse", 0, 0, 0x0002, 0), ("mouse", 0, 0, 0x0004, 0),
            ("mouse", 0, 0, ji.MOUSEEVENTF_WHEEL, -240),
            ("tecla", ji.VK_MENU, 0, 0), ("tecla", 0x09, 0, 0), ("tecla", 0x09, 0, 2), ("tecla", ji.VK_MENU, 0, 2),
        ])

    def test_text_is_unicode_and_newline_is_enter(self) -> None:
        entradas = ji.eventos_a_entradas([{"t": "text", "s": "ñ\n😀"}])
        self.assertEqual(entradas[:2], [("tecla", 0, 0xF1, 4), ("tecla", 0, 0xF1, 6)])
        self.assertEqual(entradas[2:4], [("tecla", 0x0D, 0, 0), ("tecla", 0x0D, 0, 2)])
        self.assertEqual([e[2] for e in entradas[4::2]], [0xD83D, 0xDE00])  # par sustituto

    def test_limits_and_bad_events(self) -> None:
        self.assertEqual(ji.eventos_a_entradas([{"t": "move", "dx": 99999, "dy": 0}])[0][1], ji.MAX_MOVIMIENTO)
        for malo in ([{"t": "borrar_todo"}], [{"t": "key", "k": "ctrl+alt+supr"}], [{"t": "click", "b": "x"}],
                     [{"t": "move", "dx": "a"}], "no-es-lista", [{"t": "move"}] * (ji.MAX_EVENTOS + 1)):
            with self.assertRaises(ji.EventoNoValido):
                ji.eventos_a_entradas(malo)

    def test_apply_uses_sender(self) -> None:
        enviadas = []
        self.assertEqual(ji.aplicar([{"t": "click"}], enviar=lambda e: enviadas.append(e) or len(e)), 2)
        self.assertEqual(ji.aplicar([], enviar=enviadas.append), 0)
        self.assertEqual(len(enviadas), 1)


class CursorTests(unittest.TestCase):
    def test_relative_position(self) -> None:
        monitores = [v.Monitor(1, 0, 0, 2000, 1000, True), v.Monitor(2, -1000, 0, 1000, 500, False)]
        self.assertEqual(v.posicion_relativa((500, 250), monitores, 1), (0.25, 0.25))
        self.assertIsNone(v.posicion_relativa((500, 250), monitores, 2))
        self.assertEqual(v.posicion_relativa((-1000, 0), monitores, 0), (0.0, 0.0))
        self.assertIsNone(v.posicion_relativa(None, monitores, 1))


if __name__ == "__main__":
    unittest.main()
