"""Voz a texto en la GPU (jarvis_stt_gpu): espectrograma, decodificación y respaldo.

Sin GPU, sin modelo real y sin red: las sesiones de ONNX Runtime se simulan."""

import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import jarvis_core as core
import jarvis_stt_gpu as g


def _reiniciar() -> None:
    g._transcriptor, g._error, g._pausado = None, None, False


class SpectrogramTests(unittest.TestCase):
    def test_filters_shape_and_triangles(self) -> None:
        filtros = g.filtros_mel()
        self.assertEqual(filtros.shape, (128, 201))
        self.assertTrue((filtros >= 0).all())
        self.assertTrue((filtros.max(axis=1) > 0).all())  # ninguna banda vacía
        # Las bandas van de grave a agudo.
        picos = filtros.argmax(axis=1)
        self.assertTrue((np.diff(picos) >= 0).all())

    def test_silence_and_tone(self) -> None:
        silencio = g.log_mel(np.zeros(16000, dtype=np.float32))
        self.assertEqual(silencio.shape, (1, 128, 3000))
        self.assertTrue(np.allclose(silencio, -1.5))  # log10(1e-10) = -10 → (-10 + 4) / 4
        tono = np.sin(2 * np.pi * 440 * np.arange(16000) / 16000).astype(np.float32)
        espectro = g.log_mel(tono)[0, :, 10]
        self.assertLess(espectro.argmax(), 40)  # 440 Hz cae en las bandas graves

    def test_reads_and_resamples_wav(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "a.wav"
            with wave.open(str(ruta), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes((np.ones(22050) * 1000).astype(np.int16).tobytes())
            audio = g.leer_wav(ruta)
        self.assertAlmostEqual(len(audio) / 16000, 1.0, places=2)
        self.assertAlmostEqual(float(audio[100]), 1000 / 32768, places=4)


class _Tokenizador:
    def encode(self, texto, add_special_tokens=False):
        return SimpleNamespace(ids=[7, 8, 9])

    def decode(self, ids, skip_special_tokens=True):
        return "|".join(map(str, ids))


class _Decodificador:
    """Devuelve 11, 12 y fin; guarda lo que recibió para revisar la caché."""

    def __init__(self) -> None:
        self.llamadas = []
        self.respuestas = [11, 12, g.EOT]

    def run(self, _salidas, entrada):
        self.llamadas.append(entrada)
        logits = np.zeros((1, 1, 51866), dtype=np.float32)
        logits[0, -1, self.respuestas[len(self.llamadas) - 1]] = 10
        paso = len(self.llamadas)
        return [logits, np.full((1, 20, paso, 64), paso, np.float32), np.full((1, 20, 1500, 64), paso, np.float32)]


class DecodingTests(unittest.TestCase):
    def _transcriptor(self):
        t = g.TranscriptorGPU.__new__(g.TranscriptorGPU)
        t.codificador = SimpleNamespace(run=lambda _s, e: [np.zeros((1, 1500, 1280), np.float32)])
        t.decodificador = _Decodificador()
        t.tokenizador = _Tokenizador()
        t.cache = ["past_key_values.0.decoder.key", "past_key_values.0.encoder.key"]
        t.salidas = ["logits", "present.0.decoder.key", "present.0.encoder.key"]
        return t

    def test_greedy_decoding_with_cache(self) -> None:
        t = self._transcriptor()
        self.assertEqual(t.transcribir_audio(np.zeros(16000, np.float32), "pista"), "11|12")
        primera, segunda, tercera = t.decodificador.llamadas
        # Primera pasada: pista + inicio en español, sin caché.
        self.assertEqual(primera["input_ids"].tolist(), [[g.PREV, 7, 8, 9, g.SOT, g.ES, g.TRANSCRIBE, g.NO_TS]])
        self.assertFalse(primera["use_cache_branch"][0])
        # Luego un token por paso, con la caché del decodificador nueva y la del codificador de la 1.ª.
        self.assertEqual(segunda["input_ids"].tolist(), [[11]])
        self.assertTrue(segunda["use_cache_branch"][0])
        self.assertEqual(float(tercera["past_key_values.0.decoder.key"][0, 0, 0, 0]), 2)
        self.assertEqual(float(tercera["past_key_values.0.encoder.key"][0, 0, 0, 0]), 1)

    def test_without_hint(self) -> None:
        t = self._transcriptor()
        t.transcribir_audio(np.zeros(16000, np.float32), "")
        self.assertEqual(t.decodificador.llamadas[0]["input_ids"].tolist(), [[g.SOT, g.ES, g.TRANSCRIBE, g.NO_TS]])


class FallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        _reiniciar()
        self.addCleanup(_reiniciar)

    def test_without_model_uses_the_server(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            self.assertIsNone(g.transcribir("x.wav", "", Path(carpeta)))
            self.assertFalse(g.cargar(Path(carpeta)))  # no reintenta en cada orden

    def test_long_audio_and_errors_use_the_server(self) -> None:
        g._transcriptor = SimpleNamespace(transcribir_audio=lambda audio, pista: "hola")
        with patch.object(g, "leer_wav", return_value=np.zeros(16000 * 40, np.float32)):
            self.assertIsNone(g.transcribir("x.wav", "", Path(".")))
        with patch.object(g, "leer_wav", return_value=np.zeros(16000, np.float32)):
            self.assertEqual(g.transcribir("x.wav", "", Path(".")), "hola")
        with patch.object(g, "leer_wav", side_effect=OSError("roto")):
            self.assertIsNone(g.transcribir("x.wav", "", Path(".")))

    def test_game_frees_the_gpu_and_closing_it_loads_again(self) -> None:
        g._transcriptor = SimpleNamespace(transcribir_audio=lambda audio, pista: "hola")
        self.assertTrue(g.liberar())
        self.assertIsNone(g._transcriptor)
        # Durante el juego no se vuelve a cargar sola: se usa el servidor de voz.
        with tempfile.TemporaryDirectory() as carpeta, patch.object(g, "modelo_instalado", return_value=True), \
                patch.object(g, "TranscriptorGPU") as clase:
            self.assertIsNone(g.transcribir("x.wav", "", Path(carpeta)))
            clase.assert_not_called()
            self.assertTrue(g.reanudar(Path(carpeta)))
            clase.assert_called_once()

    def test_app_frees_on_game_start_and_reloads_on_game_stop(self) -> None:
        from jarvis_app import JarvisApp

        class _HiloEnElActo:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        host = Mock()
        with patch("jarvis_app.threading.Thread", _HiloEnElActo), \
                patch.object(g, "liberar") as liberar, patch.object(g, "reanudar") as reanudar, \
                patch("jarvis_app.jarvis_agent.descargar_modelo") as descargar:
            JarvisApp._free_gpu_for_game(host)
            liberar.assert_called_once()
            descargar.assert_called_once()
            JarvisApp._restore_gpu_after_game(host)
            reanudar.assert_called_once_with(core.CARPETA_STT_GPU)
            host._preload_llm.assert_called_once_with("fin del juego")

    @patch("jarvis_core.requests.post")
    def test_core_prefers_gpu_and_falls_back(self, post) -> None:
        with patch("jarvis_stt_gpu.transcribir", return_value="hola desde la GPU"):
            self.assertEqual(core.procesar_stt(__file__), "hola desde la GPU")
        post.assert_not_called()
        post.return_value.json.return_value = {"text": "hola desde Docker"}
        with patch("jarvis_stt_gpu.transcribir", return_value=None):
            self.assertEqual(core.procesar_stt(__file__), "hola desde Docker")
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
