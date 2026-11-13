"""Rutinas con acciones nuevas, de una sola vez y con cuenta atrás. Nada se apaga de verdad."""

import json
import queue
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_core as core
import jarvis_power
import jarvis_routines as routines
from jarvis_app import JarvisApp
from jarvis_tool_registry import ToolRegistry, ToolRisk
from jarvis_tools import crear_registro_herramientas


class _DbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        for patcher in (
            patch.object(core, "DB_PATH", Path(temp_dir.name) / "jarvis_memory.db"),
            patch.object(core, "_database_initialized", False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        core.inicializar_base_datos()
        self.registry = crear_registro_herramientas(Mock())


def _pasos(texto: str) -> list[dict]:
    return json.loads(routines.parsear_solicitud_creacion(texto).steps_json)


class NewStepTests(_DbTestCase):
    def test_media_volume_lock_and_power_steps(self) -> None:
        pasos = _pasos(
            "Programa una rutina llamada Noche a las 23:30 de lunes a viernes, pausa la "
            "música, pon el volumen de Spotify al 20, bloquea el equipo y suspende el equipo"
        )
        self.assertEqual(
            pasos,
            [
                {"tool": "media_control", "arguments": {"action": "pause"}},
                {"tool": "set_app_volume", "arguments": {"app": "spotify", "percentage": 20}},
                {"tool": "lock_computer", "arguments": {}},
                {"tool": "power_control", "arguments": {"action": "sleep"}},
            ],
        )

    def test_other_media_steps(self) -> None:
        self.assertEqual(
            _pasos("Cuando conecte los auriculares, reanuda la música y siguiente canción"),
            [
                {"tool": "media_control", "arguments": {"action": "play"}},
                {"tool": "media_control", "arguments": {"action": "next"}},
            ],
        )

    def test_system_volume_still_works(self) -> None:
        self.assertEqual(
            _pasos("Programa una rutina llamada Volumen a las 10:00 pon el volumen del sistema al 30%"),
            [{"tool": "set_system_volume", "arguments": {"percentage": 30}}],
        )

    def test_plural_weekdays(self) -> None:
        pedido = routines.parsear_solicitud_creacion(
            "Programa una rutina llamada Finde a las 10:00 los sábados y domingos pon el volumen al 20%"
        )
        self.assertEqual(pedido.days, "6,7")

    def test_new_tools_are_allowed_and_low_risk_ones_can_be_automatic(self) -> None:
        pasos = json.dumps([
            {"tool": "media_control", "arguments": {"action": "pause"}},
            {"tool": "lock_computer", "arguments": {}},
        ])
        identificador = routines.crear_rutina("Auto", "10:00", "1", pasos, self.registry, automatic=True)
        self.assertGreater(identificador, 0)
        with self.assertRaises(ValueError):  # apagar nunca es automática
            routines.crear_rutina(
                "Apagar", "10:00", "1",
                json.dumps([{"tool": "power_control", "arguments": {"action": "shutdown"}}]),
                self.registry, automatic=True,
            )


class FlexibleTimeTests(_DbTestCase):
    def test_hours_without_minutes_and_spoken_forms(self) -> None:
        casos = {
            "Programa una rutina llamada noche a las 23 pausa la música y bloquea el equipo": "23:00",
            "Programa una rutina llamada Noche a las 11 de la noche, pausa la música": "23:00",
            "Programa una rutina llamada Cena a las 8 y media de la tarde pon el volumen al 20%": "20:30",
            "Programa una rutina llamada Tarde a las 18 horas bloquea el equipo": "18:00",
            "Programa una rutina llamada Temprano a las 7 y cuarto pon el volumen al 10%": "07:15",
            "Programa una rutina llamada Inicio a las 09:00 pon el volumen al 30%": "09:00",
            "Programa una rutina llamada Media a las 7 30 pon el volumen al 10%": "07:30",
        }
        for texto, hora in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(routines.parsear_solicitud_creacion(texto).time, hora)


class PowerShortcutTests(_DbTestCase):
    def _atajo(self, texto: str):
        coincidencia = routines._POWER_SHORTCUT.match(texto)
        return routines._parsear_atajo_energia(coincidencia, datetime(2026, 11, 6, 15, 30))

    def test_once(self) -> None:
        pedido = self._atajo("apaga el equipo a las 2")
        self.assertEqual((pedido.name, pedido.time, pedido.days, pedido.once), ("Apagado 02:00", "02:00", "6", True))
        self.assertTrue(pedido.tool_arguments()["once"])

    def test_recurring(self) -> None:
        casos = {
            "Jarvis, suspende la pc todos los días a las 23:30": ("23:30", "1,2,3,4,5,6,7", "sleep"),
            "apaga el equipo a las 11 de la noche de lunes a viernes": ("23:00", "1,2,3,4,5", "shutdown"),
            "reinicia el equipo los martes y jueves a las 4 de la mañana": ("04:00", "2,4", "restart"),
        }
        for texto, (hora, dias, accion) in casos.items():
            with self.subTest(texto=texto):
                pedido = self._atajo(texto)
                self.assertEqual((pedido.time, pedido.days, pedido.once), (hora, dias, False))
                self.assertEqual(json.loads(pedido.steps_json)[0]["arguments"]["action"], accion)

    def test_relative_delay(self) -> None:
        # «dentro de X»: una sola vez, a la hora en que se cumple (redondeada al minuto siguiente).
        ahora = datetime(2026, 11, 12, 14, 22, 40)

        def atajo(texto):
            return routines._parsear_atajo_energia(routines._POWER_SHORTCUT.match(texto), ahora)

        casos = {
            "Suspende el equipo dentro de 10 minutos.": ("14:33", "sleep"),
            "suspende el equipo dentro de diez minutos": ("14:33", "sleep"),
            "apaga la pc en media hora": ("14:53", "shutdown"),
            "reinicia el equipo en una hora y media": ("15:53", "restart"),
            "apaga el equipo en 30 segundos": ("14:24", "shutdown"),  # nunca menos de 1 minuto
        }
        for texto, (hora, accion) in casos.items():
            with self.subTest(texto=texto):
                pedido = atajo(texto)
                self.assertEqual((pedido.time, pedido.days, pedido.once), (hora, "4", True))
                self.assertEqual(json.loads(pedido.steps_json)[0]["arguments"]["action"], accion)
        # Pasada la medianoche cuenta el día siguiente.
        noche = routines._parsear_atajo_energia(
            routines._POWER_SHORTCUT.match("apaga el equipo en 20 minutos"), datetime(2026, 11, 12, 23, 50))
        self.assertEqual((noche.time, noche.days), ("00:10", "5"))
        self.assertIsNone(atajo("apaga el equipo todos los días en 10 minutos"))
        self.assertIsNone(atajo("apaga el equipo en 10 minutos y pon música"))

    def test_relative_delay_goes_through_routine_creation(self) -> None:
        pedido = routines.parsear_solicitud_creacion("Suspende el equipo dentro de 10 minutos.")
        self.assertTrue(pedido.once)
        self.assertEqual(json.loads(pedido.steps_json), [{"tool": "power_control", "arguments": {"action": "sleep"}}])

    def test_not_a_shortcut(self) -> None:
        self.assertIsNone(routines._POWER_SHORTCUT.match("apaga el equipo"))
        self.assertIsNone(self._atajo("apaga el equipo a las 2 y abre youtube"))
        self.assertIsNone(routines.parsear_solicitud_creacion("apaga el equipo"))

    def test_names_do_not_repeat(self) -> None:
        pedido = self._atajo("apaga el equipo a las 2")
        routines.crear_rutina(pedido.name, pedido.time, pedido.days, pedido.steps_json, self.registry)
        self.assertEqual(self._atajo("apaga el equipo a las 2").name, "Apagado 02:00 2")

    def test_once_routine_deactivates_after_running(self) -> None:
        pasos = json.dumps([{"tool": "lock_computer", "arguments": {}}])
        routines.crear_rutina("Una vez", "02:00", "6", pasos, self.registry, once=True)
        routines.crear_rutina("Siempre", "02:00", "6", pasos, self.registry)
        vencidas = routines.reclamar_rutinas_vencidas(datetime(2026, 11, 7, 2, 0))
        self.assertEqual({r.name for r in vencidas}, {"Una vez", "Siempre"})
        activas = {r.name: r.active for r in routines.listar_automatizaciones()}
        self.assertEqual(activas, {"Una vez": False, "Siempre": True})


class CountdownExecutionTests(unittest.TestCase):
    def _registro(self) -> tuple[ToolRegistry, list[str]]:
        registry = ToolRegistry()
        ejecutadas: list[str] = []
        registry.register(
            "media_control", "M.", {"type": "object", "properties": {"action": {"type": "string"}},
                                    "required": ["action"], "additionalProperties": False},
            lambda action: ejecutadas.append(f"media:{action}") or "ok",
        )
        registry.register(
            "power_control", "P.", {"type": "object", "properties": {"action": {"type": "string"}},
                                    "required": ["action"], "additionalProperties": False},
            lambda action: ejecutadas.append(f"power:{action}") or "ok",
            risk=ToolRisk.HIGH, requires_confirmation=True,
        )
        return registry, ejecutadas

    def _rutina(self) -> routines.Routine:
        return routines.Routine(
            1, "Noche", "23:30", (1,),
            (routines.RoutineStep("media_control", {"action": "pause"}),
             routines.RoutineStep("power_control", {"action": "sleep"})),
        )

    def _host(self, registry: ToolRegistry, cuenta_atras: bool) -> Mock:
        host = Mock()
        host._confirm_display_batch.return_value = None  # sin cambios de monitores
        host.tool_registry = registry
        host.events = queue.Queue()
        host._request_power_countdown.return_value = cuenta_atras
        return host

    def test_runs_without_approval_when_nobody_cancels(self) -> None:
        registry, ejecutadas = self._registro()
        host = self._host(registry, True)
        JarvisApp._routine_worker(host, self._rutina())
        host._request_power_countdown.assert_called_once_with("Noche", "sleep")
        host._request_plan_confirmation.assert_not_called()
        host._request_tool_confirmation.assert_not_called()
        self.assertEqual(ejecutadas, ["media:pause", "power:sleep"])

    def test_cancelled_countdown_runs_nothing(self) -> None:
        registry, ejecutadas = self._registro()
        host = self._host(registry, False)
        JarvisApp._routine_worker(host, self._rutina())
        self.assertEqual(ejecutadas, [])
        evento = host.events.get_nowait()
        self.assertEqual(evento[0], "routine_result")
        self.assertFalse(evento[2])

    def test_texts(self) -> None:
        self.assertEqual(jarvis_power.describir_cuenta_atras("shutdown", 60), "Apagaré el equipo en 60 s.")
        texto = JarvisApp._routine_permission_text(
            json.dumps([{"tool": "power_control", "arguments": {"action": "sleep"}}]), False
        )
        self.assertIn("cuenta atrás", texto)
        self.assertEqual(JarvisApp._routine_permission_text("[]", True), "Se ejecutará automáticamente")


if __name__ == "__main__":
    unittest.main()
