"""Traer, acomodar o mover una ventana oculta en la bandeja (2026-11-20: «traé WhatsApp al frente»)."""

import unittest
from unittest.mock import Mock, patch

import jarvis_windows as w

WHATSAPP = w.Ventana(7, "WhatsApp", "whatsapp.root", 70)


class BandejaTests(unittest.TestCase):
    def test_shows_the_hidden_window_of_that_app_only(self):
        mostrar = Mock()
        ocultas = [w.Ventana(5, "Steam", "steamwebhelper", 50), WHATSAPP]
        self.assertEqual(w.sacar_de_la_bandeja("WhatsApp", ocultas=ocultas, mostrar=mostrar), [WHATSAPP])
        mostrar.assert_called_once_with(7)
        mostrar.reset_mock()
        self.assertEqual(w.sacar_de_la_bandeja("Discord", ocultas=ocultas, mostrar=mostrar), [])
        mostrar.assert_not_called()

    def test_focus_brings_it_from_the_tray(self):
        lecturas = [[], [WHATSAPP]]
        hechas = []
        with patch.object(w, "listar_ventanas", side_effect=lambda: lecturas.pop(0)), \
                patch.object(w, "sacar_de_la_bandeja", return_value=[WHATSAPP]) as sacar, \
                patch.object(w, "enfocar", side_effect=lambda hwnd, _u=None: hechas.append(hwnd) or True), \
                patch.object(w, "_user32", return_value=Mock()), patch("time.sleep"):
            self.assertEqual(w.cambiar_estado("WhatsApp", "window_focus"), (1, ["whatsapp.root"]))
        sacar.assert_called_once_with("WhatsApp")
        self.assertEqual(hechas, [7])

    def test_minimize_does_not_touch_the_tray(self):
        with patch.object(w, "listar_ventanas", return_value=[]), \
                patch.object(w, "sacar_de_la_bandeja") as sacar:
            with self.assertRaises(w.ErrorVentana):
                w.cambiar_estado("WhatsApp", "window_minimize")
        sacar.assert_not_called()

    def test_place_and_move_also_look_in_the_tray(self):
        with patch.object(w, "listar_ventanas", side_effect=[[], [WHATSAPP]]), \
                patch.object(w, "sacar_de_la_bandeja", return_value=[WHATSAPP]), \
                patch.object(w, "colocar", return_value=True) as colocar, patch("time.sleep"):
            self.assertEqual(w.colocar_ventana("WhatsApp", "right", 2), "whatsapp.root")
        colocar.assert_called_once_with(WHATSAPP, "right", 2)


if __name__ == "__main__":
    unittest.main()


class ClaudeCodeTests(unittest.TestCase):
    def test_claude_code_is_the_claude_app(self):
        """2026-11-20: «maximizá Claude Code» no encontraba la ventana (proceso y título: «claude»)."""
        import jarvis_modo_juego

        claude = w.Ventana(9, "Claude", "claude", 90)
        otras = [w.Ventana(7, "WhatsApp", "whatsapp.root", 70), claude]
        self.assertEqual(w.elegir(otras, "Claude Code", 1), [claude])
        self.assertEqual(w.elegir(otras, "claude", 1), [claude])
        self.assertEqual(jarvis_modo_juego.por_ventana(["Claude Code"], lambda: otras), {"claude": "Claude Code"})
