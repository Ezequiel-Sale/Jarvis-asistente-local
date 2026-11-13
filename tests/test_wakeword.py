"""Palabra de activación. No abre el micrófono ni carga el modelo."""

import struct
import unittest
from unittest.mock import Mock, patch

import jarvis_wakeword as ww
from jarvis_app import JarvisApp
from jarvis_commands import detectar_comando_escucha


def _bloque(valor: int, muestras: int = ww.BLOQUE) -> bytes:
    return struct.pack(f"<{muestras}h", *([valor] * muestras))


class AudioEnergyTests(unittest.TestCase):
    def test_rms(self) -> None:
        self.assertEqual(ww.rms(b""), 0.0)
        self.assertEqual(ww.rms(_bloque(0)), 0.0)
        self.assertAlmostEqual(ww.rms(_bloque(1000)), 1000.0)
        self.assertAlmostEqual(ww.rms(_bloque(-1000)), 1000.0)


class SegmenterTests(unittest.TestCase):
    def _alimentar(self, seg: ww.SegmentadorVoz, energias: list[float]) -> int:
        for indice, energia in enumerate(energias, start=1):
            if seg.agregar(energia, 0.1):
                return indice
        return -1

    def test_threshold_adapts_to_background_noise(self) -> None:
        self.assertEqual(ww.SegmentadorVoz(50).umbral, 300)
        self.assertEqual(ww.SegmentadorVoz(200).umbral, 600)

    def test_stops_after_silence_following_speech(self) -> None:
        seg = ww.SegmentadorVoz(50, silencio_final=0.5)
        # 0,3 s de silencio, 1 s hablando y luego silencio.
        fin = self._alimentar(seg, [10] * 3 + [2000] * 10 + [10] * 20)
        self.assertTrue(seg.hablo)
        self.assertEqual(fin, 3 + 10 + 5)

    def test_short_pauses_do_not_cut_the_command(self) -> None:
        seg = ww.SegmentadorVoz(50, silencio_final=0.5)
        fin = self._alimentar(seg, [2000] * 5 + [10] * 3 + [2000] * 5 + [10] * 10)
        self.assertEqual(fin, 5 + 3 + 5 + 5)

    def test_gives_up_if_nobody_speaks(self) -> None:
        seg = ww.SegmentadorVoz(50, espera_inicio=1.0)
        fin = self._alimentar(seg, [10] * 30)
        self.assertFalse(seg.hablo)
        self.assertEqual(fin, 10)

    def test_natural_pause_does_not_cut_long_command(self) -> None:
        seg = ww.SegmentadorVoz(50)  # valores por defecto
        # 2 s hablando, 1 s de pausa para pensar, 3 s más y luego silencio.
        energias = [2000] * 25 + [10] * 12 + [2000] * 37 + [10] * 30
        fin = -1
        for indice, energia in enumerate(energias, start=1):
            if seg.agregar(energia):
                fin = indice
                break
        self.assertGreater(fin, 25 + 12 + 37)  # no cortó en la pausa

    def test_steady_background_after_speech_counts_as_silence(self) -> None:
        """Un vídeo que se cuela en el micrófono (400) por encima del umbral fijo (300)."""
        seg = ww.SegmentadorVoz(50)
        energias = [400] * 2 + [3000] * 20 + [400] * 40
        fin = -1
        for indice, energia in enumerate(energias, start=1):
            if seg.agregar(energia, 0.1):
                fin = indice
                break
        self.assertEqual(fin, 2 + 20 + 13)  # 1,3 s después de dejar de hablar, no a los 25 s
        self.assertAlmostEqual(seg.umbral_fin, 600)

    def test_soft_words_still_count_as_voice(self) -> None:
        seg = ww.SegmentadorVoz(50, silencio_final=0.5)
        # Voz fuerte y luego una palabra suave (800 > 0,2 × 3000): no corta.
        fin = self._alimentar(seg, [3000] * 10 + [800] * 8 + [10] * 10)
        self.assertEqual(fin, 10 + 8 + 5)

    def test_long_command_fits(self) -> None:
        self.assertGreaterEqual(ww.SegmentadorVoz(50).maximo, 20)

    def test_noise_estimate_ignores_speech(self) -> None:
        # 3 s de voz («Hey Jarvis») y 2 s de fondo: el ruido debe ser el fondo.
        self.assertEqual(ww.estimar_ruido([2000] * 37 + [50] * 25), 50)
        self.assertEqual(ww.estimar_ruido([]), 0.0)

    def test_maximum_length(self) -> None:
        seg = ww.SegmentadorVoz(50, maximo=2.0)
        self.assertEqual(self._alimentar(seg, [2000] * 100), 20)

    def test_beep_at_start_is_not_speech(self) -> None:
        seg = ww.SegmentadorVoz(50, espera_inicio=1.0)
        # 0,2 s de pitido captado por el micrófono y luego nadie habla.
        fin = self._alimentar(seg, [5000] * 2 + [10] * 30)
        self.assertFalse(seg.hablo)
        self.assertEqual(fin, 10)

    def test_beep_file(self) -> None:
        import os
        import tempfile
        import wave

        ruta = os.path.join(tempfile.mkdtemp(), "pitido.wav")
        ww.generar_pitido(ruta)
        try:
            with wave.open(ruta) as archivo:
                duracion = archivo.getnframes() / archivo.getframerate()
                datos = archivo.readframes(archivo.getnframes())
            self.assertAlmostEqual(duracion, 0.18, places=2)
            self.assertGreater(ww.rms(datos), 1000)
        finally:
            os.remove(ruta)

    def test_wav_is_written(self) -> None:
        import os
        import wave

        ruta = ww.guardar_wav([_bloque(100), _bloque(200)])
        try:
            with wave.open(ruta) as archivo:
                self.assertEqual(archivo.getframerate(), ww.RATE)
                self.assertEqual(archivo.getnframes(), 2 * ww.BLOQUE)
        finally:
            os.remove(ruta)


class ListeningCommandTests(unittest.TestCase):
    def test_phrases(self) -> None:
        casos = {
            "Activa la palabra de activación": True,
            "activa hey jarvis": True,
            "escúchame siempre": True,
            "Desactiva la escucha": False,
            "apaga la escucha continua": False,
            "deja de escucharme": False,
            "activa el audio": None,
            "apaga el equipo": None,
            "escucha esta canción": None,
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_escucha(texto), esperado)


def _host(**estado) -> Mock:
    host = Mock()
    host._closing = False
    host.recorder = None
    host.busy = False
    host._pending_plan_reply = None
    host._pending_approval_kind = None
    host.player_thread = None
    host.player_paused = False
    host._live_speaker = None
    host._interrupt_enabled = True
    host._interrupt_deadline = 0.0
    host._headphones_output = lambda: False  # por defecto, altavoces
    host._jarvis_speaking = lambda: JarvisApp._jarvis_speaking(host)
    for clave, valor in estado.items():
        setattr(host, clave, valor)
    return host


class CanListenTests(unittest.TestCase):
    def test_idle_listens(self) -> None:
        self.assertTrue(JarvisApp._can_listen_for_wakeword(_host()))

    def test_does_not_listen_while_busy_recording_or_speaking(self) -> None:
        hablando = Mock()
        hablando.is_alive.return_value = True
        for estado in (
            {"busy": True},
            {"recorder": object()},
            {"_closing": True},
            {"player_thread": hablando},
        ):
            with self.subTest(estado=estado):
                self.assertFalse(JarvisApp._can_listen_for_wakeword(_host(**estado)))

    def test_listens_during_a_pending_confirmation(self) -> None:
        host = _host(busy=True, _pending_plan_reply=object())
        self.assertTrue(JarvisApp._can_listen_for_wakeword(host))

    def test_paused_player_does_not_block(self) -> None:
        pausado = Mock()
        pausado.is_alive.return_value = True
        host = _host(player_thread=pausado, player_paused=True)
        self.assertTrue(JarvisApp._can_listen_for_wakeword(host))


class InterruptTests(unittest.TestCase):
    def _hablando(self, **estado):
        reproductor = Mock()
        reproductor.is_alive.return_value = True
        return _host(player_thread=reproductor, **estado)

    def test_listens_while_speaking_only_with_headphones(self) -> None:
        self.assertFalse(JarvisApp._can_listen_for_wakeword(self._hablando()))
        self.assertTrue(JarvisApp._can_listen_for_wakeword(self._hablando(_headphones_output=lambda: True)))
        apagado = self._hablando(_headphones_output=lambda: True, _interrupt_enabled=False)
        self.assertFalse(JarvisApp._can_listen_for_wakeword(apagado))

    def test_live_answer_counts_as_speaking(self) -> None:
        hablador = Mock(empezo=True)
        hablador.cancelado.is_set.return_value = False
        host = _host(busy=True, _live_speaker=hablador, _headphones_output=lambda: True)
        self.assertTrue(JarvisApp._can_listen_for_wakeword(host))
        hablador.cancelado.is_set.return_value = True  # ya se calló
        self.assertFalse(JarvisApp._can_listen_for_wakeword(host))

    def test_detection_while_speaking_stops_voice_and_cancels(self) -> None:
        hablador = Mock(empezo=True)
        hablador.cancelado.is_set.return_value = False
        host = _host(busy=True, _live_speaker=hablador)
        host._interrupt_speech = lambda: JarvisApp._interrupt_speech(host)
        host.INTERRUPCION_ESPERA = JarvisApp.INTERRUPCION_ESPERA
        with patch("jarvis_wakeword.reproducir_pitido"):
            JarvisApp._on_wakeword_detected(host)
        host._stop_player.assert_called_once()
        hablador.cancelar.assert_called_once()
        host.cancel_token.cancel.assert_called_once()
        self.assertGreater(host._interrupt_deadline, 0)

    @patch("audio_capture.limpiar_audio")
    def test_command_after_interrupt_waits_for_cancellation(self, limpiar: Mock) -> None:
        import time as _time

        host = _host(busy=True, _interrupt_deadline=_time.monotonic() + 5)
        JarvisApp._on_wakeword_command(host, "orden.wav")
        limpiar.assert_not_called()  # no se descarta: se reintenta
        host.root.after.assert_called_once()


class WakewordCommandRoutingTests(unittest.TestCase):
    @patch("threading.Thread")
    def test_command_goes_to_voice_pipeline(self, thread: Mock) -> None:
        host = _host()
        JarvisApp._on_wakeword_command(host, "orden.wav")
        host._start_busy.assert_called_once()
        argumentos = thread.call_args.kwargs["args"]
        self.assertEqual(argumentos[0], "orden.wav")
        self.assertFalse(argumentos[3])  # no es una aprobación
        thread.return_value.start.assert_called_once()

    @patch("threading.Thread")
    def test_command_answers_pending_confirmation(self, thread: Mock) -> None:
        host = _host(busy=True, _pending_plan_reply=object(), _pending_approval_kind="power")
        JarvisApp._on_wakeword_command(host, "orden.wav")
        host._start_busy.assert_not_called()
        argumentos = thread.call_args.kwargs["args"]
        self.assertTrue(argumentos[3])
        self.assertEqual(argumentos[4], "power")

    @patch("audio_capture.limpiar_audio")
    @patch("threading.Thread")
    def test_command_is_discarded_while_busy(self, thread: Mock, limpiar: Mock) -> None:
        JarvisApp._on_wakeword_command(_host(busy=True), "orden.wav")
        thread.assert_not_called()
        limpiar.assert_called_once_with("orden.wav")


class IndicatorTests(unittest.TestCase):
    def _host(self, vivo: bool | None) -> Mock:
        host = Mock()
        if vivo is None:
            host.wakeword_detector = None
        else:
            host.wakeword_detector.is_alive.return_value = vivo
        host._wakeword_active = lambda: JarvisApp._wakeword_active(host)
        return host

    def test_indicator_text(self) -> None:
        for vivo, texto in ((True, "HEY JARVIS ON"), (False, "HEY JARVIS OFF"), (None, "HEY JARVIS OFF")):
            with self.subTest(vivo=vivo):
                host = self._host(vivo)
                JarvisApp._refresh_wakeword_indicator(host)
                self.assertEqual(host.wakeword_button.configure.call_args.kwargs["text"], texto)

    def test_button_toggles(self) -> None:
        host = self._host(None)
        JarvisApp._toggle_wakeword_button(host)
        self.assertTrue(host._execute_wakeword_toggle.call_args.args[0])
        host = self._host(True)
        JarvisApp._toggle_wakeword_button(host)
        self.assertFalse(host._execute_wakeword_toggle.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
