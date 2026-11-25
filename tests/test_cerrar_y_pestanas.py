"""«Cierra WhatsApp y luego desmutea Twitch» (2026-11-24): cerrar un programa es orden directa, las frases
con «luego» o «y» se parten si cada parte es una orden, y «desmutea Twitch» va a la pestaña de Brave."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jarvis_media
from jarvis_commands import detectar_cerrar_programa
from test_varias_ordenes import _host


class CerrarProgramaTests(unittest.TestCase):
    def test_detection(self):
        self.assertEqual(detectar_cerrar_programa("Cierra Whatsapp"), ("close_program", {"names": "whatsapp"}))
        self.assertEqual(detectar_cerrar_programa("cerrá Discord y Spotify"),
                         ("close_program", {"names": "discord, spotify"}))
        self.assertEqual(detectar_cerrar_programa("cerrá el bloc de notas"),
                         ("close_program", {"names": "bloc de notas"}))
        for frase in ("cierra la pestaña", "cierra esto", "cerrá pantalla completa", "cierra sesión",
                      "cierra twitch", "cerrá YouTube", "cierra la ventana de Brave"):
            self.assertIsNone(detectar_cerrar_programa(frase), frase)

    @patch("jarvis_routines.buscar_rutina_por_frase", return_value=None)
    def test_compound_phrase_is_split_only_when_every_part_is_an_order(self, _rutina):
        host = _host()
        self.assertEqual(host._split_orders("Cierra Whatsapp y luego desmutea Twitch"),
                         ["Cierra Whatsapp", "desmutea Twitch"])
        self.assertEqual(host._split_orders("cerrá Discord y pausa"), ["cerrá Discord", "pausa"])
        self.assertIsNone(host._split_orders("cerrá Discord y luego contame un chiste"))  # la 2.ª no es orden
        self.assertIsNone(host._split_orders("Abre Steam"))
        # 2026-11-24 17:34: Whisper escribió «desmuntea»; la IA lo tomó por «desmontar» y cerró la pestaña.
        from jarvis_commands import limpiar_transcripcion_voz

        texto = limpiar_transcripcion_voz("Cierra WhatsApp y desmuntea Twitch")
        self.assertEqual(texto, "Cierra WhatsApp y desmutea Twitch")
        self.assertEqual(host._split_orders(texto), ["Cierra WhatsApp", "desmutea Twitch"])


class _Sesion:
    def __init__(self, nombre):
        self.Process = SimpleNamespace(name=lambda: nombre)
        self.SimpleAudioVolume = Mock()


class PestanaComoAppTests(unittest.TestCase):
    def test_unknown_app_falls_back_to_the_browser_tab(self):
        sesiones = lambda: [_Sesion("brave.exe"), _Sesion("Steam.exe")]
        pestana = Mock(return_value="Le devolví el sonido a 1 pestaña.")
        self.assertEqual(jarvis_media.volumen_aplicacion("twitch", mute=False, sesiones=sesiones, pestana=pestana),
                         "Le devolví el sonido a 1 pestaña.")
        pestana.assert_called_once_with(mute=False, match="twitch")
        # Sin pestaña tampoco: se dice las dos cosas.
        nada = Mock(return_value="No encontré ninguna pestaña silenciada.")
        respuesta = jarvis_media.volumen_aplicacion("twitch", mute=True, sesiones=sesiones, pestana=nada)
        self.assertIn("No encuentro audio de «twitch»", respuesta)
        self.assertIn("Tampoco encontré una pestaña", respuesta)
        # Una app que sí tiene audio no toca las pestañas; un volumen en % tampoco.
        otra = Mock()
        self.assertEqual(jarvis_media.volumen_aplicacion("steam", mute=True, sesiones=sesiones, pestana=otra),
                         "Silencié Steam.")
        jarvis_media.volumen_aplicacion("twitch", percentage=30, sesiones=sesiones, pestana=otra)
        otra.assert_not_called()


if __name__ == "__main__":
    unittest.main()
