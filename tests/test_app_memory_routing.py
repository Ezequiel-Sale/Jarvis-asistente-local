import time
import unittest
from unittest.mock import Mock, patch

from jarvis_app import JarvisApp


class _AppWorkerHost:
    tool_registry = object()
    _request_tool_confirmation = Mock(return_value=False)
    _request_plan_confirmation = Mock(return_value=True)
    _new_live_speaker = Mock(return_value=None)  # sin voz en vivo
    _live_text_callback = Mock(return_value=None)


class AppMemoryRoutingTests(unittest.TestCase):
    @patch("jarvis_app.jarvis_memory.procesar_comando", return_value="Lo recordaré.")
    @patch("jarvis_app.procesar_con_herramientas")
    @patch("jarvis_app.core.guardar_turno")
    @patch("jarvis_app.core.guardar_respuesta", return_value="response-id")
    @patch("jarvis_app.core.generar_audio_tts", return_value="response.wav")
    def test_memory_command_bypasses_model_and_is_saved_as_conversation(
        self,
        _tts: Mock,
        _save_response: Mock,
        save_turn: Mock,
        agent: Mock,
        _memory_command: Mock,
    ) -> None:
        answer, audio = JarvisApp._process_agent_query(
            _AppWorkerHost(),
            "recuerda que prefiero Brave",
            "chat",
            time.perf_counter(),
            "conversation-1",
        )
        self.assertEqual((answer, audio), ("Lo recordaré.", "response.wav"))
        save_turn.assert_called_once_with(
            "conversation-1",
            "recuerda que prefiero Brave",
            "Lo recordaré.",
        )
        agent.assert_not_called()

    @patch("jarvis_app.jarvis_memory.procesar_comando", return_value=None)
    @patch("jarvis_app.procesar_con_herramientas", return_value="Plan completado.")
    @patch("jarvis_app.core.guardar_turno")
    @patch("jarvis_app.core.guardar_respuesta", return_value="response-id")
    @patch("jarvis_app.core.generar_audio_tts", return_value="response.wav")
    def test_agent_receives_plan_approval_callback(
        self,
        _tts: Mock,
        _save_response: Mock,
        _save_turn: Mock,
        agent: Mock,
        _memory_command: Mock,
    ) -> None:
        host = _AppWorkerHost()
        answer, _audio = JarvisApp._process_agent_query(
            host,
            "Prepara el entorno de trabajo",
            "chat",
            time.perf_counter(),
            "conversation-1",
        )
        self.assertEqual(answer, "Plan completado.")
        self.assertIs(
            agent.call_args.kwargs["approve_plan"],
            host._request_plan_confirmation,
        )

    @patch("jarvis_app.jarvis_memory.procesar_comando", return_value=None)
    @patch("jarvis_app.jarvis_routines.procesar_comando", return_value="Rutina pausada.")
    @patch("jarvis_app.procesar_con_herramientas")
    @patch("jarvis_app.core.guardar_turno")
    @patch("jarvis_app.core.guardar_respuesta", return_value="response-id")
    @patch("jarvis_app.core.generar_audio_tts", return_value="response.wav")
    def test_routine_management_command_bypasses_model(
        self,
        _tts: Mock,
        _save_response: Mock,
        save_turn: Mock,
        agent: Mock,
        routine_command: Mock,
        _memory_command: Mock,
    ) -> None:
        answer, audio = JarvisApp._process_agent_query(
            _AppWorkerHost(),
            "Pausa rutina Inicio de trabajo",
            "chat",
            time.perf_counter(),
            "conversation-1",
        )
        self.assertEqual((answer, audio), ("Rutina pausada.", "response.wav"))
        routine_command.assert_called_once_with("Pausa rutina Inicio de trabajo")
        save_turn.assert_called_once_with(
            "conversation-1",
            "Pausa rutina Inicio de trabajo",
            "Rutina pausada.",
        )
        agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
