"""Modo teatro en Twitch: el reproductor se reconoce por su nombre accesible, en inglés o en español."""

import unittest

import jarvis_media as m


class NombreReproductorTests(unittest.TestCase):
    def test_spanish_and_english_names(self) -> None:
        # 2026-11-24: Twitch pasó a «Reproductor de video de Twitch» y «modo teatro» fallaba.
        for nombre in ("Twitch video player", "Reproductor de video de Twitch", "Reproductor de vídeo de Twitch"):
            with self.subTest(nombre=nombre):
                self.assertIn(m._normal(nombre), m.REPRODUCTOR_TWITCH)


if __name__ == "__main__":
    unittest.main()
