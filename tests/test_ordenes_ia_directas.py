"""Frases reales que fueron a la IA (2026-11-24/25) y ahora son órdenes directas."""
import unittest

import jarvis_commands
import jarvis_pestanas
import jarvis_tv


class OrdenesDeLaIADirectasTests(unittest.TestCase):
    def test_vea_la_pestana(self):
        for frase in ("Vea la pestaña de Twitch", "Vean la pestaña de Twitch"):
            with self.subTest(frase=frase):
                self.assertEqual(jarvis_pestanas.detectar(frase),
                                 ("browser_tab", {"action": "go", "title": "twitch"}))

    def test_hdmi_sin_verbo(self):
        self.assertEqual(jarvis_tv.detectar("HDMI 3 en la tele"), ("tv_control", {"action": "hdmi3"}))
        self.assertEqual(jarvis_tv.detectar("pon la tele en HDMI 2"), ("tv_control", {"action": "hdmi2"}))

    def test_maximice_breve(self):
        self.assertEqual(jarvis_commands.detectar_accion_ventana("maximice breve"), "brave_maximize")
        self.assertIsNone(jarvis_commands.detectar_comando_ventanas("maximice breve"))

    def test_ajarvis_abre(self):
        self.assertEqual(jarvis_commands.detectar_comando_juego("Ajarvis abre el navegador"), "navegador")
        self.assertEqual(jarvis_commands.detectar_comando_juego("A Jarvis, abre el navegador"), "navegador")


if __name__ == "__main__":
    unittest.main()
