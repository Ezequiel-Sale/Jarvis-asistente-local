import json
import queue
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_core as core
import jarvis_routines as routines
from jarvis_app import JarvisApp
from jarvis_events import HeadphonesWatcher
from jarvis_tool_registry import ToolRegistry, ToolRisk
from jarvis_tools import crear_registro_herramientas

CONNECTED = routines.DISPARADOR_AURICULARES_CONECTADOS
DISCONNECTED = routines.DISPARADOR_AURICULARES_DESCONECTADOS


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

    def _steps(self, *steps: tuple[str, dict]) -> str:
        return json.dumps([{"tool": tool, "arguments": args} for tool, args in steps])


class EventRoutineParsingTests(_DbTestCase):
    def test_connect_headphones_automatic(self) -> None:
        request = routines.parsear_solicitud_creacion(
            "Cuando conecte los auriculares, cambia la salida a auriculares automáticamente",
            self.registry,
        )
        self.assertEqual(request.name, "Al conectar auriculares")
        self.assertEqual(request.trigger, "headphones_connected")
        self.assertTrue(request.automatic)
        self.assertEqual(
            json.loads(request.steps_json),
            [{"tool": "set_output_device", "arguments": {"device": "headphones"}}],
        )
        self.assertEqual(
            request.tool_arguments(),
            {
                "name": "Al conectar auriculares",
                "days": "1,2,3,4,5,6,7",
                "steps_json": request.steps_json,
                "trigger": "headphones_connected",
                "automatic": True,
            },
        )

    def test_disconnect_headphones_switches_to_speakers_and_asks_by_default(self) -> None:
        request = routines.parsear_solicitud_creacion(
            "jarvis cuando desconecte los auriculares cambia la salida a parlantes "
            "y pon el volumen al 40%",
            self.registry,
        )
        self.assertEqual(request.trigger, "headphones_disconnected")
        self.assertFalse(request.automatic)
        self.assertEqual(
            [step["tool"] for step in json.loads(request.steps_json)],
            ["set_output_device", "set_system_volume"],
        )
        self.assertEqual(json.loads(request.steps_json)[0]["arguments"], {"device": "speakers"})

    def test_automatic_is_rejected_for_risky_steps(self) -> None:
        with self.assertRaises(ValueError) as context:
            routines.parsear_solicitud_creacion(
                "cuando conecte los auriculares navega a youtube.com sin preguntar",
                self.registry,
            )
        self.assertIn("Navegar en Brave", str(context.exception))

    def test_time_routine_can_be_automatic(self) -> None:
        request = routines.parsear_solicitud_creacion(
            "Programa una rutina llamada Noche a las 23:00 todos los días y pon el "
            "volumen al 10% automáticamente",
            self.registry,
        )
        self.assertTrue(request.automatic)
        self.assertEqual(request.time, "23:00")

    def test_second_event_routine_gets_unique_name(self) -> None:
        routines.crear_rutina(
            "Al conectar auriculares", "", "", self._steps(("set_system_volume", {"percentage": 50})),
            self.registry, trigger="headphones_connected",
        )
        request = routines.parsear_solicitud_creacion(
            "cuando conecte los auriculares pon el volumen al 20%", self.registry
        )
        self.assertEqual(request.name, "Al conectar auriculares 2")


class EventRoutineStorageTests(_DbTestCase):
    def test_event_routines_are_stored_and_found_by_event(self) -> None:
        connected = routines.crear_rutina(
            "Auriculares", "08:00", "", self._steps(("set_output_device", {"device": "headphones"})),
            self.registry, trigger="headphones_connected", automatic=True,
        )
        routines.crear_rutina(
            "Parlantes", "", "", self._steps(("set_output_device", {"device": "speakers"})),
            self.registry, trigger="headphones_disconnected",
        )
        found = routines.rutinas_para_evento(CONNECTED)
        self.assertEqual([r.identifier for r in found], [connected])
        self.assertEqual(found[0].time, "")
        self.assertEqual(found[0].permission, routines.PERMISO_AUTOMATICA)

        routines.cambiar_estado_rutina(connected, False)
        self.assertEqual(routines.rutinas_para_evento(CONNECTED), [])
        self.assertEqual(len(routines.rutinas_para_evento(DISCONNECTED)), 1)

    def test_scheduler_ignores_event_routines(self) -> None:
        routines.crear_rutina(
            "Evento", "", "", self._steps(("set_system_volume", {"percentage": 5})),
            self.registry, trigger="headphones_connected",
        )
        routines.crear_rutina(
            "Hora", "09:00", "", self._steps(("set_system_volume", {"percentage": 5})),
            self.registry,
        )
        due = routines.reclamar_rutinas_vencidas(datetime(2026, 11, 2, 9, 0).astimezone())
        self.assertEqual([r.name for r in due], ["Hora"])

    def test_automatic_permission_only_for_low_risk(self) -> None:
        with self.assertRaises(ValueError):
            routines.crear_rutina(
                "Web", "", "", self._steps(("navigate_browser", {"url": "https://example.com"})),
                self.registry, trigger="headphones_connected", automatic=True,
            )
        web = routines.crear_rutina(
            "Web", "", "", self._steps(("navigate_browser", {"url": "https://example.com"})),
            self.registry, trigger="headphones_connected",
        )
        with self.assertRaises(ValueError):
            routines.cambiar_permiso_rutina(web, True, self.registry)
        volume = routines.crear_rutina(
            "Volumen", "", "", self._steps(("set_system_volume", {"percentage": 30})),
            self.registry, trigger="headphones_connected",
        )
        routines.cambiar_permiso_rutina(volume, True, self.registry)
        info = {r.identifier: r for r in routines.listar_automatizaciones()}
        self.assertEqual(info[volume].permission, routines.PERMISO_AUTOMATICA)
        self.assertEqual(info[web].permission, routines.PERMISO_PREGUNTAR)
        listed = routines.procesar_comando("lista mis rutinas")
        self.assertIn("Volumen — al conectar los auriculares, automática (activa)", listed)

    def test_time_is_required_for_time_trigger(self) -> None:
        with self.assertRaises(ValueError):
            routines.crear_rutina(
                "Sin hora", "", "", self._steps(("set_system_volume", {"percentage": 5})),
                self.registry,
            )

    def test_run_without_asking_is_rechecked_against_current_risk(self) -> None:
        step = routines.RoutineStep("set_system_volume", {"percentage": 10})
        auto = routines.Routine(1, "R", "", (1,), (step,), CONNECTED, routines.PERMISO_AUTOMATICA)
        self.assertTrue(routines.puede_ejecutarse_sin_preguntar(auto, self.registry))
        risky = routines.Routine(
            1, "R", "", (1,), (routines.RoutineStep("navigate_browser", {"url": "x"}),),
            CONNECTED, routines.PERMISO_AUTOMATICA,
        )
        self.assertFalse(routines.puede_ejecutarse_sin_preguntar(risky, self.registry))
        asks = routines.Routine(1, "R", "", (1,), (step,), CONNECTED, routines.PERMISO_PREGUNTAR)
        self.assertFalse(routines.puede_ejecutarse_sin_preguntar(asks, self.registry))


class HeadphonesWatcherTests(unittest.TestCase):
    def _run(self, readings: list[bool], lecturas_estables: int = 2) -> list[tuple]:
        events: queue.Queue[tuple] = queue.Queue()
        pending = list(readings)
        watcher: HeadphonesWatcher

        def read() -> bool:
            if len(pending) == 1:
                watcher.stop()
            return pending.pop(0)

        watcher = HeadphonesWatcher(
            events, intervalo=0.001, lecturas_estables=lecturas_estables, leer_estado=read
        )
        watcher.start()
        watcher.join(timeout=2)
        result = []
        while not events.empty():
            result.append(events.get_nowait())
        return result

    def test_initial_state_does_not_fire(self) -> None:
        self.assertEqual(self._run([True, True, True]), [])

    def test_stable_changes_fire_connect_and_disconnect(self) -> None:
        self.assertEqual(
            self._run([False, True, True, True, False, False]),
            [("device_event", CONNECTED), ("device_event", DISCONNECTED)],
        )

    def test_flicker_is_ignored(self) -> None:
        self.assertEqual(self._run([True, False, True, False, True, True]), [])

    def test_read_errors_do_not_stop_the_watcher(self) -> None:
        events: queue.Queue[tuple] = queue.Queue()
        calls = {"n": 0}
        watcher: HeadphonesWatcher

        def read() -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("COM ocupado")
            watcher.stop()
            return True

        watcher = HeadphonesWatcher(events, intervalo=0.001, leer_estado=read)
        watcher.start()
        watcher.join(timeout=2)
        self.assertEqual(calls["n"], 2)
        self.assertTrue(watcher.estado)


class AppAutomationTests(unittest.TestCase):
    def _registry(self, risk: ToolRisk = ToolRisk.LOW) -> tuple[ToolRegistry, list[str]]:
        registry = ToolRegistry()
        executed: list[str] = []
        registry.register(
            "step", "Step.", {"type": "object", "properties": {}, "required": [],
                              "additionalProperties": False},
            lambda: executed.append("step") or "Hecho.",
            risk=risk, requires_confirmation=risk is not ToolRisk.LOW,
        )
        return registry, executed

    def _host(self, registry: ToolRegistry) -> Mock:
        host = Mock()
        host.tool_registry = registry
        host.events = queue.Queue()
        host._request_plan_confirmation.return_value = False
        return host

    def test_automatic_low_risk_routine_runs_without_approval(self) -> None:
        registry, executed = self._registry()
        host = self._host(registry)
        routine = routines.Routine(
            1, "Auto", "", (1,), (routines.RoutineStep("step", {}),),
            CONNECTED, routines.PERMISO_AUTOMATICA,
        )
        JarvisApp._routine_worker(host, routine)
        host._request_plan_confirmation.assert_not_called()
        self.assertEqual(executed, ["step"])
        self.assertTrue(host.events.get_nowait()[2])

    def test_automatic_flag_is_ignored_for_risky_steps(self) -> None:
        registry, executed = self._registry(ToolRisk.MEDIUM)
        host = self._host(registry)
        routine = routines.Routine(
            1, "Auto", "", (1,), (routines.RoutineStep("step", {}),),
            CONNECTED, routines.PERMISO_AUTOMATICA,
        )
        JarvisApp._routine_worker(host, routine)
        host._request_plan_confirmation.assert_called_once()
        self.assertEqual(executed, [])

    @patch("jarvis_app.jarvis_routines.rutinas_para_evento")
    def test_device_event_queues_matching_routines(self, for_event: Mock) -> None:
        routine = routines.Routine(1, "R", "", (1,), (), CONNECTED)
        for_event.return_value = [routine]
        host = Mock()
        host._pending_routines = queue.Queue()
        host.busy = True
        JarvisApp._handle_device_event(host, CONNECTED)
        for_event.assert_called_once_with(CONNECTED)
        self.assertIs(host._pending_routines.get_nowait(), routine)


if __name__ == "__main__":
    unittest.main()
