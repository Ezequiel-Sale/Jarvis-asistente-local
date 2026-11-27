"""«Respuestas habladas» se recuerda al reiniciar (2026-11-26: volvía a hablar)."""

import unittest
from unittest.mock import Mock, patch

from jarvis_app_voz import VozMixin


class PreferenciaAudioTests(unittest.TestCase):
    def test_toggle_saves_and_startup_reads(self) -> None:
        prefs = {}
        host = Mock()
        host._PREFERENCIA_AUDIO = VozMixin._PREFERENCIA_AUDIO
        with patch("jarvis_core.obtener_preferencia", side_effect=prefs.get), \
                patch("jarvis_core.guardar_preferencia", side_effect=prefs.__setitem__), \
                patch("jarvis_core.guardar_turno"):
            self.assertTrue(VozMixin._audio_preferido(host))  # sin guardar: habla
            VozMixin._execute_audio_toggle(host, False, "Botón: silenciar audio")
            self.assertEqual(prefs[VozMixin._PREFERENCIA_AUDIO], "0")
            self.assertFalse(VozMixin._audio_preferido(host))
            VozMixin._execute_audio_toggle(host, True, "Botón: activar audio")
            self.assertTrue(VozMixin._audio_preferido(host))


if __name__ == "__main__":
    unittest.main()
