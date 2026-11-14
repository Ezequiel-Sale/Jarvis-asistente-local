"""Correcciones de errores típicos de Whisper antes de interpretar la orden."""

import unittest

import jarvis_core as core
from jarvis_commands import detectar_comando_multimedia, limpiar_transcripcion_voz


class CorrectionTests(unittest.TestCase):
    def test_known_mishearings(self) -> None:
        casos = {
            "Pausa el video del monitor 2 y ya anuda el video del monitor 1.":
                "Pausa el video del monitor 2 y reanuda el video del monitor 1.",
            "re anuda el video": "reanuda el video",
            "deshutea la pestaña": "desmutea la pestaña",
            "sube el volumen de spoticia el 30%.": "sube el volumen de Spotify al 30 por ciento.",
            "Hey Jarbis, pausa": "Hey Jarvis pausa",
            "Abre triplay en la telena.": "Abre triplay en la tele.",
        }
        for oido, esperado in casos.items():
            with self.subTest(oido=oido):
                self.assertEqual(limpiar_transcripcion_voz(oido), esperado)

    def test_leaves_normal_speech_alone(self) -> None:
        for texto in ("Ya está bien", "Explícame cómo funciona un monitor OLED.", "la cuerda se anuda sola"):
            with self.subTest(texto=texto):
                self.assertEqual(limpiar_transcripcion_voz(texto), texto)

    def test_corrected_order_is_understood(self) -> None:
        texto = limpiar_transcripcion_voz("Pausa el video del monitor 2 y ya anuda el video del monitor 1.")
        self.assertEqual(detectar_comando_multimedia(texto)[0], "media_sequence")

    def test_prompt_is_short(self) -> None:
        # Una pista larga se cuela a veces en la transcripción.
        self.assertLess(len(core.STT_PROMPT), 220)


if __name__ == "__main__":
    unittest.main()
