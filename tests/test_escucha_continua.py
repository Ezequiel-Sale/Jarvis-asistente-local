"""Escucha continua: todo lo que se dice con el micrófono abierto es una orden.

No abre el micrófono ni carga Whisper: el micrófono es una lista de bloques falsos.
"""

import math
import queue
import struct
import threading
import unittest
from unittest.mock import Mock, patch

import jarvis_wakeword as ww
from jarvis_app import JarvisApp
from jarvis_commands import detectar_modo_continuo, frase_continua


def _bloque(valor: int) -> bytes:
    return struct.pack(f"<{ww.BLOQUE}h", *([valor] * ww.BLOQUE))


class _MicrofonoFalso:
    def __init__(self, bloques: list[bytes]) -> None:
        self.bloques = list(bloques)

    def read(self, _n, exception_on_overflow=False) -> bytes:
        return self.bloques.pop(0) if self.bloques else _bloque(0)


class PhraseFilterTests(unittest.TestCase):
    def test_commands_pass_and_jarvis_prefix_is_removed(self) -> None:
        self.assertEqual(frase_continua("Abre Steam"), ("orden", "Abre Steam"))
        self.assertEqual(frase_continua("Jarvis, abre Steam"), ("orden", "abre Steam"))
        self.assertEqual(frase_continua("Hey Jarvis pon música"), ("orden", "pon música"))
        self.assertEqual(frase_continua("pausa"), ("orden", "pausa"))
        self.assertEqual(frase_continua("pon subtítulos en la tele"), ("orden", "pon subtítulos en la tele"))

    def test_whisper_noise_and_fillers_are_ignored(self) -> None:
        for texto in ("Gracias por ver el video.", "Subtítulos realizados por la comunidad de Amara.org",
                      "¡Suscríbete!", "Música", "Eh.", "Mmm", "Sí", "Jarvis", "...", ""):
            with self.subTest(texto=texto):
                self.assertIsNone(frase_continua(texto))

    def test_while_speaking_only_stop_or_jarvis_counts(self) -> None:
        self.assertEqual(frase_continua("Cállate", hablando=True), ("callar", ""))
        self.assertEqual(frase_continua("Jarvis, para", hablando=True), ("callar", ""))
        self.assertIsNone(frase_continua("qué buen gol", hablando=True))
        self.assertEqual(frase_continua("Jarvis, qué hora es", hablando=True), ("orden", "qué hora es"))
        self.assertIsNone(frase_continua("Cállate"))  # sin voz que cortar, no hace nada

    def test_mode_phrases(self) -> None:
        casos = {
            "Activa la escucha continua": True,
            "escúchame siempre": True,
            "escúchame todo el tiempo": True,
            "desactiva la escucha continua": False,
            "vuelve a usar Hey Jarvis": False,
            # Como se dice de verdad (2026-11-16: «escucha activa» terminó en la IA).
            "Activa la escucha activa.": True,
            "Hey Jarvis, activa la escucha continua por favor": True,
            "Activa el modo escucha continua": True,
            "Quiero que me escuches todo el tiempo": True,
            "escúchame siempre, Jarvis": True,
            "Desactiva la escucha activa": False,
            "Deja de escucharme todo el tiempo": False,
            "deja de escucharme": None,  # esa apaga toda la escucha (detectar_comando_escucha)
            "activa hey jarvis": None,
            "escucha esta canción": None,
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_modo_continuo(texto), esperado)


class RecordingTests(unittest.TestCase):
    def _detector(self) -> ww.DetectorPalabraActivacion:
        return ww.DetectorPalabraActivacion(queue.Queue(), lambda: True, continua=lambda: True)

    @patch("jarvis_wakeword.guardar_wav", return_value="frase.wav")
    def test_phrase_records_until_silence_with_pre_roll(self, guardar: Mock) -> None:
        detector = self._detector()
        previos = [_bloque(0)] * ww.PREVIO_FRASE + [_bloque(3000)]
        mic = _MicrofonoFalso([_bloque(3000)] * 10 + [_bloque(0)] * 20 + [_bloque(3000)] * 5)
        self.assertEqual(detector._grabar_frase(mic, 0.0, previos), "frase.wav")
        bloques = guardar.call_args.args[0]
        silencio = math.ceil(ww.SILENCIO_FRASE / ww.SEGUNDOS_POR_BLOQUE - 1e-6)  # corta al llegar
        self.assertEqual(len(bloques), ww.PREVIO_FRASE + 11 + silencio)
        self.assertEqual(len(mic.bloques), 20 - silencio + 5)  # lo que sigue no entra

    @patch("jarvis_wakeword.guardar_wav")
    def test_clicks_and_coughs_are_dropped(self, guardar: Mock) -> None:
        detector = self._detector()
        mic = _MicrofonoFalso([_bloque(3000)] + [_bloque(0)] * 30)
        self.assertIsNone(detector._grabar_frase(mic, 0.0, [_bloque(3000)]))
        guardar.assert_not_called()

    @patch("jarvis_wakeword.guardar_wav", return_value="larga.wav")
    def test_maximum_length(self, guardar: Mock) -> None:
        detector = self._detector()
        mic = _MicrofonoFalso([_bloque(3000)] * 1000)
        detector._grabar_frase(mic, 0.0, [_bloque(3000)])
        duracion = len(guardar.call_args.args[0]) * ww.SEGUNDOS_POR_BLOQUE
        self.assertAlmostEqual(duracion, ww.MAXIMO_FRASE, delta=0.1)


def _host(**estado) -> Mock:
    host = Mock()
    host._speaker_clip = lambda ruta: JarvisApp._speaker_clip(host, ruta)
    host._speaker_rejected = lambda _ruta, **_k: False  # «Solo mi voz» desactivado
    host._closing = False
    host.recorder = None
    host.busy = False
    host._pending_plan_reply = None
    host._continuous_enabled = True
    host._continuous_transcribing = False
    host._continuous_partial = None
    host._interrupt_deadline = 0.0
    host._jarvis_speaking = lambda: False
    host._continuous_listening = lambda: JarvisApp._continuous_listening(host)
    for clave, valor in estado.items():
        setattr(host, clave, valor)
    return host


class AppRoutingTests(unittest.TestCase):
    @patch("threading.Thread")
    def test_utterance_is_transcribed_without_marking_busy(self, thread: Mock) -> None:
        host = _host()
        JarvisApp._on_continuous_utterance(host, "frase.wav")
        host._start_busy.assert_not_called()
        self.assertTrue(host._continuous_transcribing)
        self.assertEqual(thread.call_args.kwargs["args"], ("frase.wav", False, False))

    @patch("audio_capture.limpiar_audio")
    @patch("threading.Thread")
    def test_utterance_is_dropped_when_not_listening(self, thread: Mock, limpiar: Mock) -> None:
        for estado in ({"busy": True}, {"_continuous_transcribing": True}, {"_continuous_enabled": False},
                       {"_pending_plan_reply": object()}, {"recorder": object()}):
            with self.subTest(estado=estado):
                limpiar.reset_mock()
                JarvisApp._on_continuous_utterance(_host(**estado), "frase.wav")
                limpiar.assert_called_once_with("frase.wav")
        thread.assert_not_called()

    @patch("audio_capture.limpiar_audio")
    @patch("jarvis_core.procesar_stt", return_value="Gracias por ver el video.")
    def test_worker_ignores_noise_and_deletes_audio(self, _stt: Mock, limpiar: Mock) -> None:
        host = _host(events=queue.Queue())
        JarvisApp._continuous_worker(host, "frase.wav", False)
        self.assertEqual(host.events.get_nowait(), ("continuous_result", None))
        limpiar.assert_called_once_with("frase.wav")

    @patch("audio_capture.limpiar_audio")
    @patch("jarvis_core.procesar_stt", return_value="Jarvis, abre Steam")
    def test_worker_passes_the_command(self, _stt: Mock, _limpiar: Mock) -> None:
        host = _host(events=queue.Queue())
        JarvisApp._continuous_worker(host, "frase.wav", False)
        self.assertEqual(host.events.get_nowait(), ("continuous_result", ("orden", "abre Steam")))

    @patch("threading.Thread")
    def test_command_goes_through_the_voice_pipeline(self, thread: Mock) -> None:
        host = _host()
        host._run_continuous_command = lambda texto: JarvisApp._run_continuous_command(host, texto)
        JarvisApp._on_continuous_result(host, ("orden", "abre Steam"))
        self.assertFalse(host._continuous_transcribing)
        host._start_busy.assert_called_once()
        self.assertEqual(thread.call_args.kwargs["kwargs"], {"transcript": "abre Steam"})
        self.assertIsNone(thread.call_args.kwargs["args"][0])  # sin audio: ya está transcripta

    def test_stop_phrase_interrupts_speech(self) -> None:
        host = _host(_jarvis_speaking=lambda: True)
        JarvisApp._on_continuous_result(host, ("callar", ""))
        host._interrupt_speech.assert_called_once()
        host._run_continuous_command.assert_not_called()

    def test_command_after_interrupt_waits_for_cancellation(self) -> None:
        import time as _time

        host = _host(busy=True, _interrupt_deadline=_time.monotonic() + 5)
        JarvisApp._run_continuous_command(host, "abre Steam")
        host.root.after.assert_called_once()
        host._start_busy.assert_not_called()

    def test_voice_worker_routes_a_ready_transcript(self) -> None:
        host = Mock()
        host.events = queue.Queue()
        host._find_phrase_routine.return_value = None
        host._split_orders.return_value = None  # una sola orden
        with patch("jarvis_core.procesar_stt") as stt:
            JarvisApp._voice_worker(host, None, 0.0, "conv", transcript="desactiva la escucha continua")
        stt.assert_not_called()
        eventos = [host.events.get_nowait() for _ in range(host.events.qsize())]
        self.assertIn(("continuous_toggle", False, "desactiva la escucha continua"), eventos)

    def test_toggle_starts_listening_and_saves_preferences(self) -> None:
        host = _host(_continuous_enabled=False, _PREFERENCIA_CONTINUA=JarvisApp._PREFERENCIA_CONTINUA,
                     _PREFERENCIA_WAKEWORD=JarvisApp._PREFERENCIA_WAKEWORD)
        host._start_wakeword.return_value = True
        with patch("jarvis_core.guardar_preferencia") as guardar, patch("jarvis_core.guardar_turno"):
            JarvisApp._execute_continuous_toggle(host, True, "escúchame siempre")
        self.assertTrue(host._continuous_enabled)
        guardar.assert_any_call("escucha_continua", "1")
        guardar.assert_any_call("palabra_activacion", "1")
        self.assertIn("todo lo que digas", host._add_message.call_args.args[1])

    def test_button_text(self) -> None:
        host = _host()
        host._wakeword_active = lambda: True
        JarvisApp._refresh_continuous_button(host)
        self.assertEqual(host.continuous_button.configure.call_args.kwargs["text"], "ESCUCHA CONTINUA ON")
        host._continuous_enabled = False
        JarvisApp._refresh_continuous_button(host)
        self.assertEqual(host.continuous_button.configure.call_args.kwargs["text"], "ESCUCHA CONTINUA OFF")


class DetectorLoopTests(unittest.TestCase):
    def test_continuous_mode_publishes_phrases_without_the_model(self) -> None:
        """El bucle real del detector con el modelo y PyAudio falsos."""
        import sys
        import types

        eventos: queue.Queue = queue.Queue()
        detector = ww.DetectorPalabraActivacion(eventos, lambda: True, continua=lambda: True)
        bloques = [_bloque(0)] * 20 + [_bloque(3000)] * 10 + [_bloque(0)] * 40
        mic = _MicrofonoFalso(bloques)
        original = mic.read

        def leer(n, exception_on_overflow=False):
            if not mic.bloques:
                detector.stop()
            return original(n)

        mic.read = leer
        mic.stop_stream = mic.close = lambda: None
        pyaudio = types.SimpleNamespace(paInt16=8, PyAudio=lambda: types.SimpleNamespace(
            open=lambda **_k: mic, terminate=lambda: None))
        modelo = Mock()
        openwakeword = types.SimpleNamespace(model=types.SimpleNamespace(Model=lambda **_k: modelo))
        with patch.dict(sys.modules, {"pyaudio": pyaudio, "openwakeword": openwakeword,
                                      "openwakeword.model": openwakeword.model}), \
                patch("jarvis_wakeword.guardar_wav", return_value="frase.wav"):
            hilo = threading.Thread(target=detector._escuchar)
            hilo.start()
            hilo.join(5)
        publicados = [eventos.get_nowait() for _ in range(eventos.qsize())]
        self.assertIn(("continuous_utterance", "frase.wav"), publicados)
        modelo.predict.assert_not_called()


class HallucinationTests(unittest.TestCase):
    """2026-11-16: 0,4 s de un ruido → «Rápido Brave YouTube Discord … Instagram Instagram …»."""

    REAL = "Rápido Brave YouTube Discord Facebook Instagram Facebook Instagram " + "Instagram " * 60

    def test_the_real_case_is_ignored(self) -> None:
        from jarvis_commands import es_alucinacion

        self.assertIsNone(frase_continua(self.REAL))
        self.assertIsNone(frase_continua("Abre Steam, pon música de fondo, sube el volumen y prende la tele "
                                         "del cuarto", segundos_voz=0.2))  # 77 caracteres en 0,2 s
        self.assertTrue(es_alucinacion("hola hola hola hola"))
        self.assertTrue(es_alucinacion("brave youtube brave youtube brave youtube brave youtube brave youtube "
                                       "brave youtube"))

    def test_real_orders_pass(self) -> None:
        from jarvis_commands import es_alucinacion

        # 76 caracteres con 1,6 s de voz: lo que pasó de verdad.
        largo = "Busca en internet cuándo es el próximo amistoso de la selección argentina de"
        self.assertFalse(es_alucinacion(largo, 1.6))
        self.assertFalse(es_alucinacion("sube sube sube el volumen"))
        self.assertEqual(frase_continua("Abre Steam", segundos_voz=0.4), ("orden", "Abre Steam"))


class PausedPhraseTests(unittest.TestCase):
    """2026-11-16: «… la selección argentina de» [pausa] «fútbol» se cortaba en «de»."""

    def test_dangling_endings(self) -> None:
        from jarvis_commands import frase_incompleta

        self.assertTrue(frase_incompleta("busca cuándo juega la selección argentina de"))
        self.assertTrue(frase_incompleta("pon el volumen al"))
        for texto in ("abre steam", "pausa", "sube el volumen de la tele", "de"):
            with self.subTest(texto=texto):
                self.assertFalse(frase_incompleta(texto))

    def _host(self) -> Mock:
        host = _host()
        host._run_continuous_command = Mock()
        host._continuous_partial = None
        host.ESPERA_CONTINUACION = JarvisApp.ESPERA_CONTINUACION
        host._flush_continuous_partial = lambda token: JarvisApp._flush_continuous_partial(host, token)
        return host

    def test_waits_for_the_rest_and_joins(self) -> None:
        host = self._host()
        JarvisApp._on_continuous_result(host, ("orden", "busca la selección argentina de"))
        host._run_continuous_command.assert_not_called()
        host.root.after.assert_called_once()
        JarvisApp._on_continuous_result(host, ("orden", "fútbol"))
        host._run_continuous_command.assert_called_once_with("busca la selección argentina de fútbol")
        host.root.after.call_args.args[1]()  # vence la espera: ya no queda nada pendiente
        host._run_continuous_command.assert_called_once()

    def test_runs_as_it_is_if_nothing_follows(self) -> None:
        host = self._host()
        JarvisApp._on_continuous_result(host, ("orden", "pon el volumen al"))
        host.root.after.call_args.args[1]()
        host._run_continuous_command.assert_called_once_with("pon el volumen al")

    def test_noise_in_between_keeps_waiting(self) -> None:
        host = self._host()
        JarvisApp._on_continuous_result(host, ("orden", "abre la carpeta de"))
        JarvisApp._on_continuous_result(host, None)  # ruido o voz ajena
        host._run_continuous_command.assert_not_called()
        JarvisApp._on_continuous_result(host, ("orden", "descargas"))
        host._run_continuous_command.assert_called_once_with("abre la carpeta de descargas")


if __name__ == "__main__":
    unittest.main()


class FraseCortadaTests(unittest.TestCase):
    """2026-11-20: una orden larga se cortaba a los 12 s y el resto se descartaba como «voz ajena»."""

    @patch("jarvis_wakeword.guardar_wav", return_value="larga.wav")
    def test_recording_knows_it_was_cut(self, _guardar: Mock) -> None:
        detector = ww.DetectorPalabraActivacion(queue.Queue(), lambda: True, continua=lambda: True)
        self.assertGreaterEqual(ww.MAXIMO_FRASE, 25.0)
        detector._grabar_frase(_MicrofonoFalso([_bloque(3000)] * 1000), 0.0, [_bloque(3000)])
        self.assertTrue(detector.ultima_cortada)
        detector._grabar_frase(_MicrofonoFalso([_bloque(3000)] * 10 + [_bloque(0)] * 40), 0.0, [_bloque(3000)])
        self.assertFalse(detector.ultima_cortada)

    @patch("threading.Thread")
    def test_cut_phrase_waits_and_joins_the_rest_without_the_voice_filter(self, thread: Mock) -> None:
        host = _host(_continuous_cut=False, _continuous_partial_cut=False, ESPERA_CONTINUACION=4.0)
        host._run_continuous_command = Mock()
        JarvisApp._on_continuous_utterance(host, "parte1.wav", cortada=True)
        self.assertEqual(thread.call_args.kwargs["args"], ("parte1.wav", False, False))
        JarvisApp._on_continuous_result(host, ("orden", "trae Jarvis al frente, después abre Brave y"))
        host._run_continuous_command.assert_not_called()  # espera el resto
        self.assertEqual(host._continuous_partial, "trae Jarvis al frente, después abre Brave y")
        JarvisApp._on_continuous_utterance(host, "parte2.wav")
        self.assertEqual(thread.call_args.kwargs["args"], ("parte2.wav", False, True))  # sin «solo mi voz»
        JarvisApp._on_continuous_result(host, ("orden", "ponelo a la derecha del monitor 2"))
        host._run_continuous_command.assert_called_once_with(
            "trae Jarvis al frente, después abre Brave y ponelo a la derecha del monitor 2")

    @patch("audio_capture.limpiar_audio")
    @patch("jarvis_core.procesar_stt", return_value="ponelo a la derecha")
    def test_trusted_continuation_skips_the_speaker_check(self, _stt: Mock, _limpiar: Mock) -> None:
        host = _host(events=queue.Queue())
        host._speaker_clip = Mock(return_value=None)  # «no es tu voz»
        JarvisApp._continuous_worker(host, "parte2.wav", False, True)
        host._speaker_clip.assert_not_called()
        self.assertEqual(host.events.get_nowait(), ("continuous_result", ("orden", "ponelo a la derecha")))


class MicrofonoCaidoTests(unittest.TestCase):
    """2026-11-23: al conectar los auriculares, «Stream not open» dejaba la escucha muerta."""

    def detector(self):
        eventos = queue.Queue()
        detector = ww.DetectorPalabraActivacion(eventos, lambda: True)
        detector.ESPERA_REINTENTO = 0
        return detector, eventos

    def test_reopens_the_microphone_after_it_drops(self) -> None:
        detector, eventos = self.detector()
        intentos = []

        def escuchar():
            intentos.append(1)
            if len(intentos) < 3:
                raise OSError("Stream not open")

        detector._escuchar = escuchar
        detector.run()
        self.assertEqual(len(intentos), 3)
        self.assertTrue(eventos.empty())  # sin «dejó de funcionar»

    def test_gives_up_and_warns_after_many_failures(self) -> None:
        detector, eventos = self.detector()
        detector.REINTENTOS_MICROFONO = 3
        detector._escuchar = Mock(side_effect=OSError("Invalid input device"))
        detector.run()
        self.assertEqual(detector._escuchar.call_count, 3)
        self.assertEqual(eventos.get_nowait()[0], "wakeword_error")

    def test_other_errors_still_stop(self) -> None:
        detector, eventos = self.detector()
        detector._escuchar = Mock(side_effect=RuntimeError("modelo roto"))
        detector.run()
        self.assertEqual(detector._escuchar.call_count, 1)
        self.assertEqual(eventos.get_nowait(), ("wakeword_error", "modelo roto"))
