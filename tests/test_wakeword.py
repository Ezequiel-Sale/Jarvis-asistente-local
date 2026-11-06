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


class WakewordCommandRoutingTests(unittest.TestCase):
    @patch("jarvis_app.threading.Thread")
    def test_command_goes_to_voice_pipeline(self, thread: Mock) -> None:
        host = _host()
        JarvisApp._on_wakeword_command(host, "orden.wav")
        host._start_busy.assert_called_once()
        argumentos = thread.call_args.kwargs["args"]
        self.assertEqual(argumentos[0], "orden.wav")
        self.assertFalse(argumentos[3])  # no es una aprobación
        thread.return_value.start.assert_called_once()

    @patch("jarvis_app.threading.Thread")
    def test_command_answers_pending_confirmation(self, thread: Mock) -> None:
        host = _host(busy=True, _pending_plan_reply=object(), _pending_approval_kind="power")
        JarvisApp._on_wakeword_command(host, "orden.wav")
        host._start_busy.assert_not_called()
        argumentos = thread.call_args.kwargs["args"]
        self.assertTrue(argumentos[3])
        self.assertEqual(argumentos[4], "power")

    @patch("jarvis_app.limpiar_audio")
    @patch("jarvis_app.threading.Thread")
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
