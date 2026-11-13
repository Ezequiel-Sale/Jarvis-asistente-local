"""Control de energía. Todo se simula: ningún test apaga, suspende ni bloquea."""

import subprocess
import unittest
from unittest.mock import Mock, patch

import jarvis_power as power
from jarvis_app import JarvisApp
from jarvis_commands import (
    detectar_comando_energia,
    detectar_comando_monitores,
    detectar_decision_energia,
)
from jarvis_tool_registry import ToolRisk
from jarvis_tools import crear_registro_herramientas


def _resultado(codigo: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["shutdown"], codigo, "", "")


@patch.object(power.os, "name", "nt")
class PowerActionTests(unittest.TestCase):
    def test_shutdown_is_scheduled_with_margin(self) -> None:
        ejecutar = Mock(return_value=_resultado(0))
        mensaje = power.programar_apagado(False, segundos=30, ejecutar=ejecutar)
        argumentos = ejecutar.call_args.args[0]
        self.assertEqual(argumentos[:3], ["/s", "/t", "30"])
        self.assertIn("cancela el apagado", argumentos[4])
        self.assertIn("Apagaré el equipo en 30 segundos", mensaje)

    def test_restart_uses_r_flag(self) -> None:
        ejecutar = Mock(return_value=_resultado(0))
        mensaje = power.programar_apagado(True, ejecutar=ejecutar)
        self.assertEqual(ejecutar.call_args.args[0][0], "/r")
        self.assertIn("Reiniciaré", mensaje)

    def test_shutdown_already_scheduled(self) -> None:
        with self.assertRaisesRegex(OSError, "Ya hay un apagado"):
            power.programar_apagado(False, ejecutar=Mock(return_value=_resultado(1190)))

    def test_shutdown_other_error(self) -> None:
        with self.assertRaisesRegex(OSError, "código 5"):
            power.programar_apagado(False, ejecutar=Mock(return_value=_resultado(5)))

    def test_cancel(self) -> None:
        ejecutar = Mock(return_value=_resultado(0))
        self.assertEqual(power.cancelar_apagado(ejecutar=ejecutar), "Cancelé el apagado programado.")
        ejecutar.assert_called_once_with(["/a"])

    def test_cancel_without_pending_shutdown(self) -> None:
        mensaje = power.cancelar_apagado(ejecutar=Mock(return_value=_resultado(1116)))
        self.assertIn("No había", mensaje)

    def test_sleep_runs_later_in_background(self) -> None:
        suspender = Mock()
        mensaje = power.suspender_equipo(espera=0.01, suspender=suspender)
        self.assertIn("Suspenderé", mensaje)
        for _ in range(100):
            if suspender.called:
                break
            import time

            time.sleep(0.01)
        suspender.assert_called_once()

    def test_lock(self) -> None:
        self.assertEqual(power.bloquear_equipo(bloquear=lambda: True), "Bloqueé el equipo.")
        with self.assertRaises(OSError):
            power.bloquear_equipo(bloquear=lambda: False)

    def test_unknown_action(self) -> None:
        with self.assertRaises(ValueError):
            power.ejecutar_accion_energia("hibernate")


class PowerCommandParsingTests(unittest.TestCase):
    def test_recognized_phrases(self) -> None:
        casos = {
            "Apaga el equipo": ("power_control", {"action": "shutdown"}),
            "Jarvis, apaga la PC.": ("power_control", {"action": "shutdown"}),
            "apaga la computadora ahora": ("power_control", {"action": "shutdown"}),
            "Reinicia el ordenador": ("power_control", {"action": "restart"}),
            "reinicia la pc": ("power_control", {"action": "restart"}),
            "Suspende el equipo": ("power_control", {"action": "sleep"}),
            "pon el equipo en suspensión": ("power_control", {"action": "sleep"}),
            "pon la pc en modo reposo": ("power_control", {"action": "sleep"}),
            "Bloquea el equipo": ("lock_computer", {}),
            "bloquea la sesión": ("lock_computer", {}),
            "bloquea la pantalla": ("lock_computer", {}),
            "Cancela el apagado": ("cancel_power_action", {}),
            "cancela el reinicio del equipo": ("cancel_power_action", {}),
            "no apagues el equipo": ("cancel_power_action", {}),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_energia(texto), esperado)

    def test_other_phrases_are_not_power_commands(self) -> None:
        for texto in (
            "apaga el monitor 3",
            "apaga la luz",
            "reinicia Brave",
            "¿cómo apago el equipo?",
            "bloquea a ese usuario",
            "apaga el equipo de sonido del vecino",
        ):
            with self.subTest(texto=texto):
                self.assertIsNone(detectar_comando_energia(texto))

    def test_monitor_command_still_wins(self) -> None:
        self.assertIsNotNone(detectar_comando_monitores("apaga el monitor 3"))


class PowerToolRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = crear_registro_herramientas(Mock())

    def test_power_control_is_high_risk_and_confirmed(self) -> None:
        tool = self.registry.get("power_control")
        self.assertEqual(tool.risk, ToolRisk.HIGH)
        self.assertTrue(tool.requires_confirmation)

    def test_lock_and_cancel_are_low_risk(self) -> None:
        for nombre in ("lock_computer", "cancel_power_action"):
            with self.subTest(nombre=nombre):
                tool = self.registry.get(nombre)
                self.assertEqual(tool.risk, ToolRisk.LOW)
                self.assertFalse(tool.requires_confirmation)


class DirectPowerConfirmationTests(unittest.TestCase):
    def _host(self, aprueba: bool) -> Mock:
        host = Mock()
        host.tool_registry = crear_registro_herramientas(Mock())
        host.tool_registry.execute = Mock(return_value="Apagaré el equipo en 30 segundos.")
        host._request_power_confirmation = Mock(return_value=aprueba)
        return host

    def test_direct_shutdown_asks_and_stops_if_rejected(self) -> None:
        host = self._host(False)
        JarvisApp._direct_tool_worker(
            host, "power_control", {"action": "shutdown"}, "apaga el equipo", "c1", None
        )
        host._request_power_confirmation.assert_called_once()
        self.assertEqual(host._request_power_confirmation.call_args.args[0], "shutdown")
        host.tool_registry.execute.assert_not_called()
        evento = host.events.put.call_args.args[0]
        self.assertEqual(evento[0], "direct_action_error")
        self.assertTrue(evento[2].startswith("Cancelé"))

    def test_direct_shutdown_runs_when_approved(self) -> None:
        host = self._host(True)
        with patch("jarvis_core.guardar_turno"):
            JarvisApp._direct_tool_worker(
                host, "power_control", {"action": "shutdown"}, "apaga el equipo", "c1", None
            )
        host.tool_registry.execute.assert_called_once()
        self.assertEqual(host.events.put.call_args.args[0][0], "direct_action_done")

    def test_lock_does_not_ask(self) -> None:
        host = self._host(False)
        with patch("jarvis_core.guardar_turno"):
            JarvisApp._direct_tool_worker(host, "lock_computer", {}, "bloquea la pc", "c1", None)
        host._request_power_confirmation.assert_not_called()
        host.tool_registry.execute.assert_called_once()


class PowerVoiceDecisionTests(unittest.TestCase):
    def test_approvals(self) -> None:
        for texto, accion in (
            ("Sí.", "shutdown"),
            ("Jarvis, sí", "shutdown"),
            ("sí, apágalo", "shutdown"),
            ("Apágalo ya", "shutdown"),
            ("confirmo", "restart"),
            ("reinícialo", "restart"),
            ("adelante", "sleep"),
            ("sí, suspende el equipo por favor", "sleep"),
        ):
            with self.subTest(texto=texto):
                self.assertTrue(detectar_decision_energia(texto, accion))

    def test_rejections(self) -> None:
        for texto in ("no", "No, cancela", "mejor no", "no lo apagues", "cancelar", "espera"):
            with self.subTest(texto=texto):
                self.assertFalse(detectar_decision_energia(texto, "shutdown"))

    def test_unclear_answers_do_nothing(self) -> None:
        for texto, accion in (
            ("Gracias.", "shutdown"),
            ("apaga la luz", "shutdown"),
            ("reinícialo", "shutdown"),  # verbo de otra acción
            ("", "shutdown"),
        ):
            with self.subTest(texto=texto):
                self.assertIsNone(detectar_decision_energia(texto, accion))


class AgentPowerConfirmationTests(unittest.TestCase):
    def test_agent_power_tool_uses_power_dialog(self) -> None:
        host = Mock()
        host._closing = False
        host._request_power_confirmation = Mock(return_value=True)
        tool = crear_registro_herramientas(Mock()).get("power_control")
        self.assertTrue(
            JarvisApp._request_tool_confirmation(host, tool, {"action": "restart"})
        )
        host._request_power_confirmation.assert_called_once_with("restart")
        host.events.put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
