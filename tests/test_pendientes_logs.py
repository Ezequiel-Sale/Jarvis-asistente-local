"""Errores que quedaban en los logs (revisión del 2026-11-23)."""

import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_app_herramientas
import jarvis_audio_devices
from jarvis_app import JarvisApp

RAIZ = Path(__file__).resolve().parent.parent


class NumeracionAntesDeGestosTests(unittest.TestCase):
    def test_fixed_numbering_is_set_before_gestures_start(self):
        fuente = (RAIZ / "jarvis_app.py").read_text("utf-8")
        self.assertLess(fuente.index("jarvis_vision.configurar_numeracion(_numeracion_fija_monitores)"),
                        fuente.index("self._init_gestures()"))


class VozDelResumenTests(unittest.TestCase):
    def test_waits_for_docker_then_retries(self):
        host = Mock(ESPERA_VOZ_AL_ARRANCAR=0)
        host._docker_start_finished = threading.Event()
        tts = Mock(side_effect=[ConnectionRefusedError(10061, "denegó"), "resumen.wav"])
        with patch("jarvis_core.generar_audio_tts", tts):
            self.assertEqual(JarvisApp._tts_con_espera(host, "Buen día", dormir=lambda _s: None), "resumen.wav")
        self.assertEqual(tts.call_count, 2)

    def test_retries_while_the_voice_model_loads_and_gives_up(self):
        host = Mock()
        host._docker_start_finished = threading.Event()
        host._docker_start_finished.set()
        dormidas = []
        error = ConnectionError("El servidor TTS cerró la conexión sin completar el audio.")
        with patch("jarvis_core.generar_audio_tts", Mock(side_effect=error)) as tts:
            with self.assertRaises(ConnectionError):
                JarvisApp._tts_con_espera(host, "Buen día", dormir=dormidas.append)
        self.assertEqual(tts.call_count, 4)
        self.assertEqual(dormidas, [5.0, 5.0, 5.0])

    def test_used_for_spoken_direct_answers(self):
        fuente = (RAIZ / "jarvis_app_ordenes.py").read_text("utf-8")
        self.assertIn("audio = self._tts_con_espera(response)", fuente)


class SalidaDeAudioTests(unittest.TestCase):
    def test_retries_when_windows_has_no_outputs_for_a_moment(self):
        sin_nada = jarvis_audio_devices.DispositivoNoEncontrado(
            "No encontré altavoces High Definition Audio Device. Dispositivos de salida disponibles: ninguno.")
        cambiar = Mock(side_effect=[sin_nada, "Speakers (High Definition Audio Device)"])
        dormidas = []
        host = Mock(ESPERAS_SALIDA_AUDIO=JarvisApp.ESPERAS_SALIDA_AUDIO)
        with patch.object(jarvis_app_herramientas, "cambiar_dispositivo_salida", cambiar):
            texto = JarvisApp._tool_set_output_device(host, "speakers", dormir=dormidas.append)
        self.assertEqual(texto, "Salida de audio cambiada a Speakers (High Definition Audio Device).")
        self.assertEqual(dormidas, [1.5])

    def test_a_missing_device_among_others_fails_at_once(self):
        falta = jarvis_audio_devices.DispositivoNoEncontrado(
            "No encontré auriculares G533 Gaming Headset. Dispositivos de salida disponibles: Speakers.")
        host = Mock(ESPERAS_SALIDA_AUDIO=JarvisApp.ESPERAS_SALIDA_AUDIO)
        with patch.object(jarvis_app_herramientas, "cambiar_dispositivo_salida", Mock(side_effect=falta)) as cambiar:
            with self.assertRaises(jarvis_audio_devices.DispositivoNoEncontrado):
                JarvisApp._tool_set_output_device(host, "headphones", dormir=lambda _s: None)
        self.assertEqual(cambiar.call_count, 1)


if __name__ == "__main__":
    unittest.main()
