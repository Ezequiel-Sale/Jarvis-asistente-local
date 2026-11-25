"""«Navega a Twitch en el monitor 1» (2026-11-24: fallaba «la dirección web no puede contener espacios»)."""

import unittest

from jarvis_commands import detectar_navegacion_web, direccion_de_sitio, normalizar_url_web


class NavegarTests(unittest.TestCase):
    def test_phrases(self) -> None:
        casos = {
            "Navega a Twitch en el monitor 1": {"url": "twitch.tv", "monitor": 1},
            "Navega a twitch.tv en el monitor 1": {"url": "twitch.tv", "monitor": 1},
            "ve a youtube.com en la pantalla dos": {"url": "youtube.com", "monitor": 2},
            "navega a Netflix en la tele": {"url": "netflix.com", "screen_name": "tv"},
            "navega a google.com.ar": {"url": "google.com.ar"},
            "ve a Mercado Libre": {"url": "mercadolibre.com.ar"},
            "En el monitor 1 abre una nueva pestaña y luego navega Twitch.tv":
                {"url": "Twitch.tv", "monitor": 1, "new_tab": True},
            "abrí una pestaña nueva y andá a youtube.com en el monitor 2":
                {"url": "youtube.com", "monitor": 2, "new_tab": True},
            "navega Twitch.tv": {"url": "Twitch.tv"},
        }
        for frase, argumentos in casos.items():
            with self.subTest(frase=frase):
                self.assertEqual(detectar_navegacion_web(frase), ("navigate_browser", argumentos))
                normalizar_url_web(argumentos["url"])  # sin espacios: válida
        self.assertIsNone(detectar_navegacion_web("abrí steam"))

    def test_site_names(self) -> None:
        self.assertEqual(direccion_de_sitio("Twitch"), "twitch.tv")
        self.assertEqual(direccion_de_sitio("reddit"), "reddit.com")
        self.assertEqual(direccion_de_sitio("https://x.com/a"), "https://x.com/a")


if __name__ == "__main__":
    unittest.main()
