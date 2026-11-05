import queue
import unittest
from unittest.mock import Mock, patch

from jarvis_app import JarvisApp
from jarvis_commands import (
    detectar_decision_aprobacion_plan,
    detectar_decision_guardar_rutina,
)


class PlanVoiceApprovalTests(unittest.TestCase):
    def test_only_explicit_plan_approval_phrases_match(self) -> None:
        self.assertIs(detectar_decision_aprobacion_plan("Ejecutar plan"), True)
        self.assertIs(detectar_decision_aprobacion_plan("Jarvis, ejecutar el plan."), True)
        self.assertIs(
            detectar_decision_aprobacion_plan("Confirmación por voz: Ejecutar plan"),
            True,
        )
        self.assertIs(
            detectar_decision_aprobacion_plan("Sí, ejecutar el plan."),
            True,
        )
        self.assertIs(
            detectar_decision_aprobacion_plan("Jarvis, claro, ejecutar plan por favor."),
            True,
        )
        self.assertIs(
            detectar_decision_aprobacion_plan("Ejecutar el plan ahora."),
            True,
        )
        self.assertIs(detectar_decision_aprobacion_plan("No ejecutar plan"), False)
        self.assertIs(detectar_decision_aprobacion_plan("Cancelar el plan"), False)
        self.assertIs(
            detectar_decision_aprobacion_plan("No, no ejecutar el plan."),
            False,
        )
        self.assertIs(
            detectar_decision_aprobacion_plan("Jarvis, no ejecutes el plan por favor."),
            False,
        )
        self.assertIsNone(
            detectar_decision_aprobacion_plan("Sí, hazlo cuando puedas")
        )
        self.assertIsNone(detectar_decision_aprobacion_plan("Ejecutar"))

    def test_routine_save_decision_requires_explicit_save_routine_words(self) -> None:
        self.assertIs(detectar_decision_guardar_rutina("Guardar rutina"), True)
        self.assertIs(
            detectar_decision_guardar_rutina("Jarvis, guardar la rutina por favor."),
            True,
        )
        self.assertIs(
            detectar_decision_guardar_rutina("No guardar rutina"),
            False,
        )
        self.assertIs(
            detectar_decision_guardar_rutina("No, no guardar la rutina."),
            False,
        )
        self.assertIsNone(detectar_decision_guardar_rutina("Sí"))
        self.assertIsNone(detectar_decision_guardar_rutina("Ejecutar plan"))
        self.assertIsNone(detectar_decision_aprobacion_plan("Guardar rutina"))

    @patch("jarvis_app.limpiar_audio")
    @patch("jarvis_app.core.procesar_stt", return_value="Ejecutar plan")
    def test_voice_worker_routes_approval_phrase_without_running_commands(
        self,
        _stt: Mock,
        _cleanup: Mock,
    ) -> None:
        host = Mock()
        host.events = queue.Queue()
        JarvisApp._voice_worker(host, "approval.wav", 0.0, "conversation-1", True)
        self.assertEqual(
            host.events.get_nowait(),
            ("plan_voice_decision", True, "Ejecutar plan"),
        )

    @patch("jarvis_app.limpiar_audio")
    @patch("jarvis_app.core.procesar_stt", return_value="No guardar rutina")
    def test_voice_worker_routes_routine_save_rejection_without_running_commands(
        self,
        _stt: Mock,
        _cleanup: Mock,
    ) -> None:
        host = Mock()
        host.events = queue.Queue()
        JarvisApp._voice_worker(
            host,
            "approval.wav",
            0.0,
            "conversation-1",
            True,
            "routine_save",
        )
        self.assertEqual(
            host.events.get_nowait(),
            ("plan_voice_decision", False, "No guardar rutina"),
        )

    @patch("jarvis_app.limpiar_audio")
    @patch("jarvis_app.core.procesar_stt", return_value="Guardar rutina")
    def test_voice_worker_routes_routine_save_approval_without_running_commands(
        self,
        _stt: Mock,
        _cleanup: Mock,
    ) -> None:
        host = Mock()
        host.events = queue.Queue()
        JarvisApp._voice_worker(
            host,
            "approval.wav",
            0.0,
            "conversation-1",
            True,
            "routine_save",
        )
        self.assertEqual(
            host.events.get_nowait(),
            ("plan_voice_decision", True, "Guardar rutina"),
        )

    def test_resolving_plan_returns_decision_and_closes_dialog(self) -> None:
        class FakeDialog:
            destroyed = False

            def winfo_exists(self) -> bool:
                return True

            def grab_current(self) -> "FakeDialog":
                return self

            def grab_release(self) -> None:
                pass

            def destroy(self) -> None:
                self.destroyed = True

        host = Mock()
        reply = queue.Queue(maxsize=1)
        dialog = FakeDialog()
        host._pending_plan_reply = reply
        host._pending_approval_kind = "routine_save"
        host._plan_dialog = dialog
        host._recording_for_plan_approval = False
        host._plan_voice_button = None
        host._plan_approve_button = None
        host._plan_reject_button = None
        JarvisApp._resolve_plan_approval(host, False)
        self.assertIs(host._pending_plan_reply, None)
        self.assertIs(host._pending_approval_kind, None)
        self.assertIs(host._plan_dialog, None)
        self.assertTrue(dialog.destroyed)
        self.assertFalse(reply.get_nowait())


if __name__ == "__main__":
    unittest.main()
