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

    # 2026-11-26/27
    def test_cable_de_red(self):
        import jarvis_red

        for frase, estado in (("Desactiva el cable Internet", "off"), ("Desactive el cable Ethernet", "off"),
                              ("Activa el cable Internet", "on"), ("habilita la placa de red", "on")):
            with self.subTest(frase=frase):
                self.assertEqual(jarvis_red.detectar(frase), ("network_adapter", {"name": "Ethernet", "state": estado}))
        self.assertIsNone(jarvis_red.detectar("desactiva el wifi"))  # eso es de jarvis_ajustes
        self.assertIsNone(jarvis_red.detectar("desactiva el monitor 3"))

    def test_cable_sin_placa_llamada_ethernet(self):
        import jarvis_red
        from jarvis_red import Interfaz

        placas = [Interfaz("Wi-Fi", True, True), Interfaz("Ethernet 2", True, True)]
        self.assertIs(jarvis_red._buscar_placa("Ethernet", placas), placas[1])
        self.assertIs(jarvis_red._buscar_placa("Wi-Fi", placas), placas[0])
        self.assertIsNone(jarvis_red._buscar_placa("otra", placas))

    def test_monitores(self):
        self.assertEqual(jarvis_commands.detectar_comando_monitores("Extiendo el monitor 3"),
                         ("configure_display", {"action": "extend", "monitor": 3}))
        self.assertEqual(jarvis_commands.detectar_comando_monitores("Monitor 1 como principal"),
                         ("configure_display", {"action": "set_primary", "monitor": 1}))

    def test_ventanas_en_infinitivo(self):
        self.assertEqual(jarvis_commands.detectar_comando_ventanas("Traer WhatsApp al frente"),
                         ("window_control", {"app": "whatsapp", "action": "window_focus"}))
        self.assertEqual(jarvis_commands.detectar_comando_monitores("Mover WhatsApp a la izquierda del monitor 2"),
                         ("arrange_windows", {"windows": [{"app": "whatsapp", "position": "left"}], "monitor": 2}))


if __name__ == "__main__":
    unittest.main()
