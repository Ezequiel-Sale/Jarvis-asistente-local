"""«Solo mi voz»: huella del hablante y filtro de órdenes.

No abre el micrófono ni carga el modelo real: la red se reemplaza por una función que
devuelve un vector según el «hablante» simulado.
"""

import queue
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

import jarvis_hablante as h
from jarvis_app import JarvisApp


def _wav(carpeta: Path, nombre: str, muestras: np.ndarray) -> str:
    ruta = carpeta / nombre
    with wave.open(str(ruta), "wb") as archivo:
        archivo.setnchannels(1)
        archivo.setsampwidth(2)
        archivo.setframerate(h.FRECUENCIA)
        archivo.writeframes(np.clip(muestras, -32768, 32767).astype(np.int16).tobytes())
    return str(ruta)


def _voz(segundos: float, frecuencia: float = 220.0) -> np.ndarray:
    t = np.arange(int(segundos * h.FRECUENCIA)) / h.FRECUENCIA
    return 6000 * np.sin(2 * np.pi * frecuencia * t)


class FeatureTests(unittest.TestCase):
    def test_fbank_shape_and_mean_removed(self) -> None:
        bandas = h.bandas_mel(_voz(1.0))
        self.assertEqual(bandas.shape, (1 + (16000 - 400) // 160, 80))
        self.assertTrue(np.allclose(bandas.mean(axis=0), 0, atol=1e-3))
        self.assertEqual(h.bandas_mel(np.zeros(100)).shape, (0, 80))

    def test_mel_bank_is_ordered_and_covers_20hz_to_8khz(self) -> None:
        banco = h._BANCO
        self.assertEqual(banco.shape, (80, 257))
        picos = [int(np.argmax(fila)) for fila in banco]
        self.assertEqual(picos, sorted(picos))  # cada banda, más aguda que la anterior
        self.assertEqual(float(banco[:, 0].sum()), 0.0)  # 0 Hz queda fuera (empieza en 20 Hz)
        self.assertTrue(np.all(banco.max(axis=1) > 0.3))  # ninguna banda vacía (las graves son angostas)
        self.assertEqual(float(banco[:, 256].sum()), 0.0)  # el último bin no se usa (como Kaldi)

    def test_silence_is_removed(self) -> None:
        senal = np.concatenate([np.zeros(16000), _voz(1.0), np.zeros(16000)])
        voz, segundos = h.solo_voz(senal)
        self.assertAlmostEqual(segundos, 1.0, delta=0.05)
        self.assertEqual(h.solo_voz(np.zeros(32000))[1], 0.0)

    def test_similarity_threshold_and_average(self) -> None:
        a, b = np.array([1.0, 0, 0]), np.array([0, 1.0, 0])
        self.assertAlmostEqual(h.similitud(a, a * 5), 1.0)
        self.assertAlmostEqual(h.similitud(a, b), 0.0)
        self.assertAlmostEqual(float(np.linalg.norm(h.promedio([a, b]))), 1.0)
        self.assertEqual(h.umbral_para("alta", 3.0), h.EXIGENCIAS["alta"])
        self.assertAlmostEqual(h.umbral_para("alta", 1.0), h.EXIGENCIAS["alta"] - h.DESCUENTO_CORTA)
        self.assertEqual(h.umbral_para("rara", 3.0), h.EXIGENCIAS["media"])


def _bandas_marcadas(voz: np.ndarray) -> np.ndarray:
    """En lugar de las bandas reales: los cruces por cero (un tono grave cruza pocas veces)."""
    cruces = float(np.mean(np.abs(np.diff(np.sign(voz))) > 0))
    return np.full((10, 80), cruces, dtype=np.float32)


class _Red:
    """Sustituye al modelo: voz grave = «yo», aguda = «otra persona»."""

    def run(self, _salidas, entradas):
        grave = float(entradas["feats"][0, 0, 0]) < 0.1
        return [np.array([[1.0, 0.1, 0.0] if grave else [0.0, 0.1, 1.0]])]


class VerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.carpeta = Path(tempfile.mkdtemp())
        self.ver = h.Verificador(self.carpeta)
        self.ver._sesion = _Red()
        parche = patch.object(h, "bandas_mel", _bandas_marcadas)
        parche.start()
        self.addCleanup(parche.stop)

    def test_without_print_everything_passes(self) -> None:
        ruta = _wav(self.carpeta, "a.wav", _voz(2.0))
        self.assertEqual(self.ver.verificar(ruta, "alta"), h.Resultado(True, None, 0.0))

    def test_save_load_verify_and_delete(self) -> None:
        mia = self.ver.huella_de(_voz(2.0, 200))[0]
        coherencia = self.ver.guardar_huella([mia, mia])
        self.assertAlmostEqual(coherencia, 1.0, places=3)
        nuevo = h.Verificador(self.carpeta)  # se lee del archivo
        nuevo._sesion = _Red()
        self.assertTrue(nuevo.registrada())
        propia = nuevo.verificar(_wav(self.carpeta, "yo.wav", _voz(2.0, 200)), "media")
        ajena = nuevo.verificar(_wav(self.carpeta, "otro.wav", _voz(2.0, 5000)), "media")
        self.assertTrue(propia.aceptada)
        self.assertFalse(ajena.aceptada)
        self.assertLess(ajena.similitud, propia.similitud)
        # Muy poca voz para decidir: se descarta (2026-11-16: ruidos de 0,4 s de un ventilador o un
        # directo pasaban y Whisper los volvía órdenes), salvo en las confirmaciones («sí» seco).
        corta = _wav(self.carpeta, "eh.wav", _voz(0.2, 5000))
        self.assertEqual(nuevo.verificar(corta, "alta"), h.Resultado(False, None, 0.18))
        self.assertTrue(nuevo.verificar(corta, "alta", corta_pasa=True).aceptada)
        # 0,4 s ya alcanza para verificar: antes (mínimo 0,5) se dejaba pasar sin mirar.
        self.assertFalse(nuevo.verificar(_wav(self.carpeta, "ruido.wav", _voz(0.4, 5000)), "media").aceptada)
        nuevo.borrar_huella()
        self.assertFalse(nuevo.registrada())
        self.assertFalse((self.carpeta / h.ARCHIVO_HUELLA).exists())

    def test_damaged_print_means_not_registered(self) -> None:
        (self.carpeta / h.ARCHIVO_HUELLA).write_text("{roto", encoding="utf-8")
        self.assertFalse(h.Verificador(self.carpeta).registrada())

    def test_missing_model_is_reported(self) -> None:
        sin = h.Verificador(self.carpeta)
        self.assertFalse(sin.modelo_disponible())
        with self.assertRaises(FileNotFoundError):
            sin.huella_de(_voz(2.0))


def _host(**estado) -> Mock:
    host = Mock()
    host.events = queue.Queue()
    host._speaker_filter = True
    host._speaker_strictness = "media"
    for clave, valor in estado.items():
        setattr(host, clave, valor)
    return host


class AppFilterTests(unittest.TestCase):
    def test_filter_off_never_checks(self) -> None:
        host = _host(_speaker_filter=False)
        self.assertFalse(JarvisApp._speaker_rejected(host, "a.wav"))
        host.speaker_verifier.verificar.assert_not_called()

    def test_rejects_other_voices_and_fails_open(self) -> None:
        host = _host()
        host.speaker_verifier.verificar.return_value = h.Resultado(False, 0.1, 2.0)
        self.assertTrue(JarvisApp._speaker_rejected(host, "a.wav"))
        host.speaker_verifier.verificar.return_value = h.Resultado(True, 0.7, 2.0)
        self.assertFalse(JarvisApp._speaker_rejected(host, "a.wav"))
        host.speaker_verifier.verificar.side_effect = RuntimeError("roto")
        self.assertFalse(JarvisApp._speaker_rejected(host, "a.wav"))  # ante un error, no bloquea

    @patch("audio_capture.limpiar_audio")
    @patch("jarvis_core.procesar_stt")
    def test_continuous_rejected_voice_is_not_transcribed_and_releases(self, stt: Mock, limpiar: Mock) -> None:
        """Regresión: un ``return`` dentro del ``try`` se saltaba ``continuous_result`` y la
        escucha continua quedaba «transcribiendo» para siempre."""
        host = _host()
        host._speaker_rejected = lambda _ruta, **_k: True
        JarvisApp._continuous_worker(host, "otro.wav", False)
        stt.assert_not_called()
        limpiar.assert_called_once_with("otro.wav")
        self.assertEqual(host.events.get_nowait(), ("continuous_result", None))

    @patch("audio_capture.limpiar_audio")
    def test_no_hint_for_whisper_with_little_voice(self, _limpiar: Mock) -> None:
        """2026-11-16: 0,4 s de ruido → «Maximiza agranda minimiza» (copiado de la pista)."""
        host = _host()
        host._speaker_rejected = lambda _ruta, **_k: False
        for segundos, con_pista in ((0.4, False), (1.5, True)):
            with self.subTest(segundos=segundos), \
                    patch("jarvis_hablante.leer_wav", return_value=np.zeros(10)), \
                    patch("jarvis_hablante.solo_voz", return_value=(np.zeros(10), segundos)), \
                    patch("jarvis_core.procesar_stt", return_value="pausa") as stt:
                JarvisApp._continuous_worker(host, "f.wav", False)
                self.assertEqual(stt.call_args.kwargs, {"pista": con_pista})

    @patch("audio_capture.limpiar_audio")
    @patch("jarvis_core.procesar_stt", return_value="Sí")
    def test_confirmations_accept_short_sounds(self, _stt: Mock, _limpiar: Mock) -> None:
        host = _host()
        host._approval_decision = Mock(return_value=True)
        JarvisApp._free_approval_worker(host, "si.wav", "plan", object())
        host._speaker_rejected.assert_called_once_with("si.wav", corta_pasa=True)

    @patch("audio_capture.limpiar_audio")
    def test_wakeword_command_from_other_voice(self, limpiar: Mock) -> None:
        host = _host()
        host._speaker_rejected = lambda _ruta, **_k: True
        JarvisApp._wakeword_voice_worker(host, "o.wav", 0.0, "c", False, "plan", None)
        host._voice_worker.assert_not_called()
        limpiar.assert_called_once_with("o.wav")
        self.assertEqual(host.events.get_nowait(), ("speaker_rejected", False))
        host._speaker_rejected = lambda _ruta, **_k: False
        JarvisApp._wakeword_voice_worker(host, "yo.wav", 0.0, "c", False, "plan", None)
        host._voice_worker.assert_called_once_with("yo.wav", 0.0, "c", False, "plan", None)

    @patch("audio_capture.limpiar_audio")
    @patch("jarvis_core.procesar_stt")
    def test_other_voice_cannot_confirm(self, stt: Mock, _limpiar: Mock) -> None:
        host = _host()
        host._speaker_rejected = lambda _ruta, **_k: True
        reply = object()
        JarvisApp._free_approval_worker(host, "si.wav", "power", reply)
        stt.assert_not_called()
        self.assertEqual(host.events.get_nowait(), ("free_approval_result", None, "", reply))

    def test_rejected_messages(self) -> None:
        host = _host(busy=True)
        JarvisApp._on_speaker_rejected(host, False)
        host._set_idle.assert_called_once()
        self.assertEqual(host._set_status.call_args.args[0], "NO RECONOCÍ TU VOZ")
        host = _host()
        JarvisApp._on_speaker_rejected(host, True)  # en una confirmación: sigue esperando
        self.assertEqual(host.events.get_nowait(), ("plan_voice_unrecognized",))

    @patch("threading.Thread")
    def test_which_recordings_are_filtered(self, thread: Mock) -> None:
        """Regresión 2026-11-16: el filtro había quedado en F9 y no en «Hey Jarvis»."""
        def host_libre():
            return _host(busy=False, _closing=False, recorder=None, _pending_plan_reply=None,
                         _pending_approval_kind=None, _interrupt_deadline=0.0)

        host = host_libre()
        JarvisApp._on_wakeword_command(host, "hey.wav")
        self.assertIs(thread.call_args.kwargs["target"], host._wakeword_voice_worker)
        host = host_libre()
        JarvisApp._on_wakeword_command(host, "celular.wav", verificar_voz=False)
        self.assertIs(thread.call_args.kwargs["target"], host._voice_worker)
        host = host_libre()
        host.recorder = Mock()
        host.recorder.detener.return_value = "f9.wav"
        host._recording_for_plan_approval = False
        JarvisApp._stop_recording(host)
        self.assertIs(thread.call_args.kwargs["target"], host._voice_worker)  # F9 no se filtra

    def test_not_listening_while_enrolling(self) -> None:
        host = _host(_closing=False, recorder=None, _enrolling=True)
        self.assertFalse(JarvisApp._can_listen_for_wakeword(host))


class SettingsTests(unittest.TestCase):
    def _host(self, registrada: bool, activo: bool = False) -> Mock:
        host = _host(_speaker_filter=activo, _PREFERENCIA_SOLO_MI_VOZ="solo_mi_voz",
                     _PREFERENCIA_EXIGENCIA="exigencia_voz")
        host.speaker_verifier.registrada.return_value = registrada
        host.speaker_verifier.modelo_disponible.return_value = True
        host._set_speaker_filter = lambda activo: JarvisApp._set_speaker_filter(host, activo)
        return host

    def test_first_activation_opens_enrollment(self) -> None:
        host = self._host(registrada=False)
        JarvisApp._toggle_speaker_filter(host)
        host._open_voice_enrollment.assert_called_once_with(activar_al_terminar=True)
        self.assertFalse(host._speaker_filter)

    def test_toggle_saves_preference(self) -> None:
        host = self._host(registrada=True)
        with patch("jarvis_core.guardar_preferencia") as guardar:
            JarvisApp._toggle_speaker_filter(host)
            self.assertTrue(host._speaker_filter)
            guardar.assert_called_with("solo_mi_voz", "1")
            JarvisApp._toggle_speaker_filter(host)
            self.assertFalse(host._speaker_filter)
            guardar.assert_called_with("solo_mi_voz", "0")

    def test_missing_model(self) -> None:
        host = self._host(registrada=True)
        host.speaker_verifier.modelo_disponible.return_value = False
        JarvisApp._toggle_speaker_filter(host)
        self.assertFalse(host._speaker_filter)
        self.assertIn("speaker-onnx", host._add_message.call_args.args[1])

    def test_strictness_cycles(self) -> None:
        host = self._host(registrada=True)
        with patch("jarvis_core.guardar_preferencia") as guardar:
            vistos = []
            for _ in range(3):
                JarvisApp._cycle_speaker_strictness(host)
                vistos.append(host._speaker_strictness)
        self.assertEqual(vistos, ["alta", "baja", "media"])
        guardar.assert_called_with("exigencia_voz", "media")

    def test_buttons(self) -> None:
        host = self._host(registrada=True, activo=True)
        JarvisApp._refresh_speaker_buttons(host)
        self.assertEqual(host.speaker_button.configure.call_args.kwargs["text"], "SOLO MI VOZ: SÍ")
        self.assertIn("VOLVER A REGISTRAR", host.speaker_enroll_button.configure.call_args.kwargs["text"])
        self.assertEqual(host.speaker_strictness_button.configure.call_args.kwargs["text"], "EXIGENCIA: MEDIA")


@unittest.skipUnless(h.Verificador().modelo_disponible(), "sin el modelo speaker-onnx (no está en GitHub)")
class RealModelTests(unittest.TestCase):
    def test_real_model_gives_a_256_vector(self) -> None:
        huella, segundos = h.Verificador().huella_de(_voz(2.0) + np.random.default_rng(0).normal(0, 300, 32000))
        self.assertEqual(huella.shape, (256,))
        self.assertGreater(segundos, 1.5)


if __name__ == "__main__":
    unittest.main()
