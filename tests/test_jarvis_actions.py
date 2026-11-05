import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import jarvis_actions
import jarvis_core as core
from jarvis_actions import (
    CancellationToken,
    EstadoAccion,
    OperacionCancelada,
    ResultadoAccion,
    TiempoAgotado,
    Verificacion,
    ejecutar_con_limites,
)
from jarvis_agent import procesar_con_herramientas
from jarvis_tool_registry import (
    ToolCancelledError,
    ToolConfirmationRequired,
    ToolRegistry,
    ToolRisk,
    ToolTimeoutError,
    ToolVerificationError,
)
from jarvis_verifiers import crear_verificadores, verificar_volumen

_EMPTY_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _response(message: dict) -> Mock:
    response = Mock()
    response.json.return_value = {"message": message}
    response.raise_for_status.return_value = None
    return response


class EjecutarConLimitesTests(unittest.TestCase):
    def test_returns_value(self) -> None:
        self.assertEqual(ejecutar_con_limites(lambda: 42, timeout=1, descripcion="x"), 42)

    def test_propagates_original_exception(self) -> None:
        def fail() -> None:
            raise KeyError("boom")

        with self.assertRaises(KeyError):
            ejecutar_con_limites(fail, timeout=1, descripcion="x")

    def test_timeout_stops_waiting(self) -> None:
        started = time.monotonic()
        with self.assertRaises(TiempoAgotado):
            ejecutar_con_limites(lambda: time.sleep(2), timeout=0.2, descripcion="lenta")
        self.assertLess(time.monotonic() - started, 1.5)

    def test_cancel_while_running(self) -> None:
        token = CancellationToken()
        threading.Timer(0.15, token.cancel).start()
        with self.assertRaises(OperacionCancelada):
            ejecutar_con_limites(
                lambda: time.sleep(2), timeout=5, descripcion="lenta", cancel_token=token
            )

    def test_cancelled_token_never_starts(self) -> None:
        token = CancellationToken()
        token.cancel()
        handler = Mock()
        with self.assertRaises(OperacionCancelada):
            ejecutar_con_limites(handler, timeout=1, descripcion="x", cancel_token=token)
        handler.assert_not_called()


class SupervisedRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records: list[ResultadoAccion] = []
        self.registry = ToolRegistry(recorder=self.records.append)

    def _register(self, name: str, handler, **kwargs) -> None:
        self.registry.register(name, f"{name} test.", _EMPTY_SCHEMA, handler, **kwargs)

    def test_success_is_recorded_with_duration_and_label(self) -> None:
        self._register("ok", lambda: "hecho", label="Acción correcta")
        self.assertEqual(self.registry.execute("ok", {}, origin="directo"), "hecho")
        record = self.records[0]
        self.assertEqual(
            (record.herramienta, record.etiqueta, record.origen, record.estado),
            ("ok", "Acción correcta", "directo", EstadoAccion.EXITO),
        )
        self.assertGreaterEqual(record.duracion, 0)

    def test_timeout_raises_tool_error_and_is_recorded(self) -> None:
        self._register("slow", lambda: time.sleep(2), timeout_seconds=0.2)
        with self.assertRaises(ToolTimeoutError):
            self.registry.execute("slow", {})
        self.assertEqual(self.records[0].estado, EstadoAccion.TIEMPO_AGOTADO)

    def test_handler_error_keeps_original_type(self) -> None:
        def fail() -> None:
            raise OSError("sin dispositivo")

        self._register("fail", fail)
        with self.assertRaises(OSError):
            self.registry.execute("fail", {})
        self.assertEqual(self.records[0].estado, EstadoAccion.ERROR)
        self.assertEqual(self.records[0].error, "sin dispositivo")

    def test_failed_verification_raises_and_is_recorded(self) -> None:
        self._register(
            "unverified",
            lambda: "hecho",
            verifier=lambda _args, _result: Verificacion(False, "no cambió"),
        )
        with self.assertRaises(ToolVerificationError) as context:
            self.registry.execute("unverified", {})
        self.assertIn("no cambió", str(context.exception))
        self.assertEqual(self.records[0].estado, EstadoAccion.NO_VERIFICADA)

    def test_verifier_crash_does_not_fail_the_action(self) -> None:
        def broken(_args, _result):
            raise RuntimeError("sin acceso")

        self._register("ok", lambda: "hecho", verifier=broken)
        self.assertEqual(self.registry.execute("ok", {}), "hecho")
        record = self.records[0]
        self.assertEqual(record.estado, EstadoAccion.EXITO)
        self.assertIsNone(record.verificacion.ok)

    def test_cancelled_before_confirmation_is_not_executed(self) -> None:
        handler = Mock(return_value="no")
        self._register("risky", handler, risk=ToolRisk.HIGH, requires_confirmation=True)
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(ToolCancelledError):
            self.registry.execute("risky", {}, cancel_token=token)
        handler.assert_not_called()
        self.assertEqual(self.records[0].estado, EstadoAccion.CANCELADA)

    def test_confirmation_is_still_required(self) -> None:
        self._register("risky", Mock(), risk=ToolRisk.HIGH, requires_confirmation=True)
        with self.assertRaises(ToolConfirmationRequired):
            self.registry.execute("risky", {})
        self.assertEqual(self.records, [])

    def test_recorder_failure_does_not_break_action(self) -> None:
        registry = ToolRegistry(recorder=Mock(side_effect=RuntimeError("bd caída")))
        registry.register("ok", "ok test.", _EMPTY_SCHEMA, lambda: "hecho")
        self.assertEqual(registry.execute("ok", {}), "hecho")

    def test_invalid_timeout_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._register("bad", lambda: None, timeout_seconds=0)


class ActionLogTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        for patcher in (
            patch.object(core, "DB_PATH", Path(temp_dir.name) / "jarvis_memory.db"),
            patch.object(core, "_database_initialized", False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _result(estado: EstadoAccion = EstadoAccion.EXITO, **kwargs) -> ResultadoAccion:
        values = {
            "herramienta": "set_system_volume",
            "etiqueta": "Ajustar volumen",
            "origen": "directo",
            "estado": estado,
            "duracion": 0.25,
            "argumentos": {"percentage": 30},
        }
        values.update(kwargs)
        return ResultadoAccion(**values)

    def test_records_are_persisted_and_listed(self) -> None:
        jarvis_actions.guardar_accion(
            self._result(verificacion=Verificacion(True, "Windows reporta 30%."))
        )
        jarvis_actions.guardar_accion(
            self._result(EstadoAccion.ERROR, error="Brave no está instalado.")
        )
        acciones = jarvis_actions.listar_acciones()
        self.assertEqual([a["estado"] for a in acciones], ["error", "exito"])
        self.assertEqual(acciones[1]["duracion_ms"], 250)
        self.assertTrue(str(acciones[1]["verificacion"]).startswith("ok:"))

    def test_long_text_is_truncated(self) -> None:
        jarvis_actions.guardar_accion(self._result(EstadoAccion.ERROR, error="x" * 2000))
        error = jarvis_actions.listar_acciones(1)[0]["error"]
        self.assertEqual(len(error), jarvis_actions.MAX_TEXTO_GUARDADO)

    def test_only_latest_records_are_kept(self) -> None:
        with patch.object(jarvis_actions, "MAX_ACCIONES_GUARDADAS", 3):
            for _ in range(5):
                jarvis_actions.guardar_accion(self._result())
        self.assertEqual([a["id"] for a in jarvis_actions.listar_acciones()], [5, 4, 3])

    def test_history_commands(self) -> None:
        self.assertEqual(
            jarvis_actions.procesar_comando("¿Qué acciones hiciste?"),
            "Todavía no registré ninguna acción.",
        )
        jarvis_actions.guardar_accion(
            self._result(EstadoAccion.TIEMPO_AGOTADO, error="Superó 10 s.")
        )
        history = jarvis_actions.procesar_comando("historial de acciones")
        self.assertIn("Ajustar volumen — tiempo agotado", history)
        self.assertIn("Motivo: Superó 10 s.", history)
        self.assertTrue(
            jarvis_actions.procesar_comando("Jarvis, ¿cuál fue la última acción?")
            .startswith("Última acción:")
        )
        self.assertIsNone(jarvis_actions.procesar_comando("abre una pestaña nueva"))


@patch("jarvis_agent.jarvis_memory.buscar_recuerdos", return_value=[])
@patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
@patch("jarvis_agent.core.obtener_historial", return_value=[])
class AgentCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ToolRegistry()
        self.executed: list[str] = []
        self.token = CancellationToken()

        def first() -> str:
            self.executed.append("first")
            self.token.cancel()
            return "Primero listo."

        self.registry.register("first", "First.", _EMPTY_SCHEMA, first, label="Primer paso")
        self.registry.register(
            "second",
            "Second.",
            _EMPTY_SCHEMA,
            lambda: self.executed.append("second") or "Segundo listo.",
        )

    @patch("jarvis_agent.requests.post")
    def test_cancel_stops_remaining_plan_steps(self, post: Mock, *_mocks: Mock) -> None:
        post.return_value = _response(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "first", "arguments": {}}},
                    {"function": {"name": "second", "arguments": {}}},
                ],
            }
        )
        answer = procesar_con_herramientas(
            "Haz los dos pasos",
            "conversation-1",
            self.registry,
            Mock(return_value=True),
            approve_plan=Mock(return_value=True),
            cancel_token=self.token,
        )
        self.assertEqual(self.executed, ["first"])
        self.assertTrue(answer.startswith("Cancelé la operación."))
        self.assertIn("«Primer paso»", answer)
        post.assert_called_once()

    @patch("jarvis_agent.requests.post")
    def test_cancel_while_waiting_for_ollama(self, post: Mock, *_mocks: Mock) -> None:
        post.side_effect = lambda *args, **kwargs: time.sleep(3)
        threading.Timer(0.2, self.token.cancel).start()
        started = time.monotonic()
        answer = procesar_con_herramientas(
            "Hola",
            "conversation-1",
            self.registry,
            Mock(return_value=True),
            cancel_token=self.token,
        )
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(answer, "Cancelé la operación. No se llegó a ejecutar ninguna acción.")

    @patch("jarvis_agent.requests.post", side_effect=requests.ConnectionError("refused"))
    def test_ollama_down_has_clear_message(self, _post: Mock, *_mocks: Mock) -> None:
        with self.assertRaises(RuntimeError) as context:
            procesar_con_herramientas(
                "Hola", "conversation-1", self.registry, Mock(return_value=True)
            )
        self.assertIn("aplicación de Ollama", str(context.exception))

    @patch("jarvis_agent.requests.post")
    def test_total_time_limit(self, post: Mock, *_mocks: Mock) -> None:
        post.return_value = _response({"role": "assistant", "content": "Hola."})
        answer = procesar_con_herramientas(
            "Hola",
            "conversation-1",
            self.registry,
            Mock(return_value=True),
            max_total_seconds=1e-9,
        )
        self.assertIn("superó el tiempo máximo", answer)
        post.assert_not_called()


class VerifierTests(unittest.TestCase):
    def test_volume_verifier_compares_with_windows_value(self) -> None:
        ok = verificar_volumen({"percentage": 40}, None, leer_volumen=lambda: 40)
        self.assertTrue(ok.ok)
        wrong = verificar_volumen({"percentage": 40}, None, leer_volumen=lambda: 10)
        self.assertFalse(wrong.ok)
        self.assertIn("10%", wrong.detalle)

    def test_jarvis_audio_verifier_reads_host_state(self) -> None:
        host = Mock(audio_enabled=False)
        verifier = crear_verificadores(host)["set_jarvis_audio"]
        self.assertTrue(verifier({"enabled": False}, None).ok)
        self.assertFalse(verifier({"enabled": True}, None).ok)


if __name__ == "__main__":
    unittest.main()
