import io
import queue
import threading
import unittest
from unittest.mock import Mock, patch

import requests
from PIL import Image

import jarvis_vision as vision
from jarvis_actions import CancellationToken, EstadoAccion, OperacionCancelada
from jarvis_app import JarvisApp
from jarvis_commands import detectar_decision_vision, detectar_vision

M1 = vision.Monitor(1, 0, 0, 2560, 1440, True)
M2 = vision.Monitor(2, -1920, 0, 1920, 1080, False)


class VisionPhraseTests(unittest.TestCase):
    def test_capture_requests(self) -> None:
        casos = {
            "mira mi pantalla": (None, ""),
            "Jarvis, mira el monitor 2": (2, ""),
            "analiza la pantalla 1 y dime qué error sale": (1, "dime qué error sale"),
            "¿qué hay en el monitor dos?": (2, ""),
            "captura el monitor principal": (None, ""),
            "mira el monitor tres y resume el artículo": (3, "resume el artículo"),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_vision(texto), esperado)
        for texto in ("abre la pantalla", "sube el volumen", "qué aplicaciones tengo abiertas"):
            self.assertIsNone(detectar_vision(texto))

    def test_voice_decisions(self) -> None:
        for texto in ("analizar", "sí, analízala", "adelante", "envíala"):
            self.assertIs(detectar_decision_vision(texto), True, texto)
        for texto in ("cancelar", "no", "descartar la captura", "no la envíes"):
            self.assertIs(detectar_decision_vision(texto), False, texto)
        self.assertIsNone(detectar_decision_vision("no sé"))


class MonitorTests(unittest.TestCase):
    def test_choose_monitor(self) -> None:
        self.assertIs(vision.elegir_monitor([M2, M1], None), M1)
        self.assertIs(vision.elegir_monitor([M1, M2], 2), M2)
        with self.assertRaises(ValueError) as context:
            vision.elegir_monitor([M1, M2], 3)
        self.assertIn("Monitores disponibles: 1, 2", str(context.exception))
        self.assertEqual(M1.descripcion, "Monitor 1 (2560×1440, principal)")

    def test_image_is_downscaled_in_memory(self) -> None:
        jpeg = vision.preparar_imagen(Image.new("RGB", (3840, 2160), "white"))
        self.assertEqual(Image.open(io.BytesIO(jpeg)).size, (1920, 1080))


class AnalysisTests(unittest.TestCase):
    def _response(self, payload: dict, status: int = 200) -> Mock:
        response = Mock(status_code=status)
        response.json.return_value = payload
        response.raise_for_status.side_effect = (
            requests.HTTPError("400") if status >= 400 else None
        )
        return response

    @patch("jarvis_vision.requests.post")
    def test_sends_image_to_vision_model_without_thinking(self, post: Mock) -> None:
        post.return_value = self._response({"message": {"content": "Un error de Steam."}})
        self.assertEqual(vision.analizar_imagen(b"jpeg", "¿qué error sale?"), "Un error de Steam.")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], vision.core.MODEL_VISION)
        self.assertFalse(payload["think"])
        self.assertEqual(payload["messages"][1]["content"], "¿qué error sale?")
        self.assertEqual(len(payload["messages"][1]["images"]), 1)

    @patch("jarvis_vision.requests.post")
    def test_missing_model_has_clear_message(self, post: Mock) -> None:
        post.return_value = self._response({"error": "model 'x' not found"}, status=404)
        with self.assertRaises(RuntimeError) as context:
            vision.analizar_imagen(b"jpeg", "")
        self.assertIn("no está instalado", str(context.exception))

    @patch("jarvis_vision.requests.post", side_effect=lambda *a, **k: __import__("time").sleep(3))
    def test_analysis_can_be_cancelled(self, _post: Mock) -> None:
        token = CancellationToken()
        threading.Timer(0.2, token.cancel).start()
        with self.assertRaises(OperacionCancelada):
            vision.analizar_imagen(b"jpeg", "", token)


class VisionWorkerTests(unittest.TestCase):
    def _host(self) -> Mock:
        host = Mock()
        host.events = queue.Queue()
        return host

    def _datos(self) -> dict:
        return {
            "monitor": M1,
            "pregunta": "¿qué ves?",
            "command": "mira mi pantalla",
            "imagen": Image.new("RGB", (100, 50)),
            "conversation_id": "conv-1",
            "cancel_token": CancellationToken(),
        }

    def test_rejected_capture_is_discarded_without_calling_the_model(self) -> None:
        host, datos = self._host(), self._datos()
        reply: queue.Queue[bool] = queue.Queue()
        reply.put(False)
        with patch("jarvis_vision.analizar_imagen") as analizar:
            JarvisApp._vision_worker(host, reply, datos)
        analizar.assert_not_called()
        self.assertIsNone(datos["imagen"])
        self.assertEqual(host.events.get_nowait(), ("vision_cancelled",))

    @patch("jarvis_core.generar_audio_tts", return_value=None)
    @patch("jarvis_core.guardar_respuesta", return_value="respuesta_1")
    @patch("jarvis_core.guardar_turno")
    @patch("jarvis_vision.analizar_imagen", return_value="Veo un error.")
    def test_approved_capture_is_analysed_saved_and_recorded(
        self, analizar: Mock, guardar_turno: Mock, _respuesta: Mock, _tts: Mock
    ) -> None:
        host, datos = self._host(), self._datos()
        host._record_vision_action = lambda *args, **kwargs: JarvisApp._record_vision_action(host, *args, **kwargs)
        reply: queue.Queue[bool] = queue.Queue()
        reply.put(True)
        JarvisApp._vision_worker(host, reply, datos)
        self.assertEqual(analizar.call_args.args[1], "¿qué ves?")
        guardar_turno.assert_called_once_with("conv-1", "mira mi pantalla", "Veo un error.")
        resultado = host._record_action.call_args.args[0]
        self.assertEqual((resultado.estado, resultado.argumentos), (EstadoAccion.EXITO, {"monitor": 1}))
        self.assertIsNone(resultado.resultado)
        evento = host.events.get_nowait()
        self.assertEqual(evento[:2], ("answer", "Veo un error."))

    @patch("audio_capture.limpiar_audio")
    @patch("jarvis_core.procesar_stt", return_value="analizar")
    def test_voice_approval_uses_vision_phrases(self, _stt: Mock, _clean: Mock) -> None:
        host = self._host()
        JarvisApp._voice_worker(host, "a.wav", 0.0, "conv-1", True, "vision")
        self.assertEqual(host.events.get_nowait(), ("plan_voice_decision", True, "analizar"))


if __name__ == "__main__":
    unittest.main()
