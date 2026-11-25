"""Modo teatro en Twitch: el reproductor se reconoce por su nombre accesible, en inglés o en español."""

import unittest

import jarvis_media as m


class NombreReproductorTests(unittest.TestCase):
    def test_spanish_and_english_names(self) -> None:
        # 2026-11-24: Twitch pasó a «Reproductor de video de Twitch» y «modo teatro» fallaba.
        for nombre in ("Twitch video player", "Reproductor de video de Twitch", "Reproductor de vídeo de Twitch"):
            with self.subTest(nombre=nombre):
                self.assertIn(m._normal(nombre), m.REPRODUCTOR_TWITCH)



class ReintentoTests(unittest.TestCase):
    def test_retries_after_a_com_error(self) -> None:
        from unittest.mock import patch

        class COMError(Exception):
            pass

        llamadas = iter([COMError(-2147220991, "Un evento no pudo invocar a ninguno de los suscriptores"), "ok"])

        def falso(*_a, **_k):
            r = next(llamadas)
            if isinstance(r, Exception):
                raise r
            return r

        with patch.object(m, "_modo_teatro", side_effect=falso), patch.object(m, "ESPERA_REINTENTO_UIA", 0):
            self.assertEqual(m.modo_teatro(True), "ok")
        with patch.object(m, "_modo_teatro", side_effect=ValueError("otro")):
            with self.assertRaises(ValueError):
                m.modo_teatro(True)

if __name__ == "__main__":
    unittest.main()
