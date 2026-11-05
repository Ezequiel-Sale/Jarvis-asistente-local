import json
import queue
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_core as core
import jarvis_routines
from jarvis_agent import procesar_con_herramientas
from jarvis_app import JarvisApp
from jarvis_tool_registry import ToolRegistry, ToolRisk
from jarvis_tools import crear_registro_herramientas


def _response(message: dict) -> Mock:
    response = Mock()
    response.json.return_value = {"message": message}
    response.raise_for_status.return_value = None
    return response


class JarvisRoutineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path_patch = patch.object(
            core,
            "DB_PATH",
            Path(self.temp_dir.name) / "jarvis_memory.db",
        )
        self.db_path_patch.start()
        self.initialized_patch = patch.object(core, "_database_initialized", False)
        self.initialized_patch.start()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self.initialized_patch.stop)
        self.addCleanup(self.db_path_patch.stop)
        self.host = Mock()
        self.host.events = queue.Queue()
        self.registry = crear_registro_herramientas(self.host)
        core.inicializar_base_datos()

    def _create_routine(
        self,
        *,
        name: str = "Inicio de trabajo",
        time: str = "09:00",
        days: str = "1,2,3,4,5",
        steps: list[dict] | None = None,
    ) -> int:
        steps_json = json.dumps(
            steps
            or [
                {
                    "tool": "set_system_volume",
                    "arguments": {"percentage": 30},
                },
                {"tool": "open_new_browser_tab", "arguments": {}},
            ]
        )
        return jarvis_routines.crear_rutina(
            name,
            time,
            days,
            steps_json,
            self.registry,
        )

    def test_routine_persists_and_is_claimed_once_on_its_scheduled_weekday(self) -> None:
        identifier = self._create_routine()
        due = jarvis_routines.reclamar_rutinas_vencidas(
            datetime(2026, 11, 2, 9, 0)
        )
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].identifier, identifier)
        self.assertEqual(due[0].name, "Inicio de trabajo")
        self.assertEqual(
            [step.tool for step in due[0].steps],
            ["set_system_volume", "open_new_browser_tab"],
        )
        self.assertEqual(
            jarvis_routines.reclamar_rutinas_vencidas(
                datetime(2026, 11, 2, 9, 0)
            ),
            [],
        )
        next_day_due = jarvis_routines.reclamar_rutinas_vencidas(
            datetime(2026, 11, 3, 9, 0)
        )
        self.assertEqual(len(next_day_due), 1)
        self.assertEqual(
            jarvis_routines.reclamar_rutinas_vencidas(
                datetime(2026, 11, 3, 9, 0)
            ),
            [],
        )
        self.assertEqual(
            jarvis_routines.listar_rutinas(),
            [(identifier, "Inicio de trabajo", "09:00", (1, 2, 3, 4, 5), True)],
        )

    def test_routine_is_not_run_after_its_scheduled_minute(self) -> None:
        self._create_routine(time="09:00")
        self.assertEqual(
            jarvis_routines.reclamar_rutinas_vencidas(
                datetime(2026, 11, 2, 9, 1)
            ),
            [],
        )

    def test_routine_management_commands_list_pause_resume_and_delete(self) -> None:
        identifier = self._create_routine(name="Mi mañana")
        listed = jarvis_routines.procesar_comando("Lista mis rutinas")
        self.assertIn(
            f"{identifier}. Mi mañana — a las 09:00, de lunes a viernes, "
            "pregunta antes (activa)",
            listed,
        )
        self.assertEqual(
            jarvis_routines.procesar_comando("Pausa rutina Mi mañana"),
            "Rutina pausada.",
        )
        self.assertFalse(jarvis_routines.listar_rutinas()[0][4])
        self.assertEqual(
            jarvis_routines.procesar_comando("Reanuda rutina Mi mañana"),
            "Rutina reanudada.",
        )
        self.assertEqual(
            jarvis_routines.procesar_comando(f"Elimina rutina {identifier}"),
            "Rutina eliminada.",
        )
        self.assertEqual(jarvis_routines.listar_rutinas(), [])

    def test_duplicate_names_are_rejected_case_insensitively(self) -> None:
        self._create_routine(name="Rutina de prueba")
        with self.assertRaisesRegex(ValueError, "Ya existe"):
            self._create_routine(name="rutina DE PRUEBA")

    def test_invalid_schedule_or_steps_are_rejected_before_saving(self) -> None:
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            self._create_routine(time="25:00")
        with self.assertRaisesRegex(ValueError, "del 1 al 7"):
            self._create_routine(days="1,8")
        with self.assertRaisesRegex(ValueError, "no se permite"):
            self._create_routine(
                steps=[{"tool": "run_shell", "arguments": {"command": "whoami"}}]
            )
        with self.assertRaisesRegex(ValueError, "máximo"):
            self._create_routine(
                steps=[
                    {
                        "tool": "set_system_volume",
                        "arguments": {"percentage": 101},
                    }
                ]
            )
        self.assertEqual(jarvis_routines.listar_rutinas(), [])

    def test_voice_friendly_creation_parser_extracts_spoken_time_weekdays_and_steps(self) -> None:
        request = jarvis_routines.parsear_solicitud_creacion(
            "Programa una rutina llamada Inicio de Trabajo a las 13 y 23 "
            "de lunes a viernes baja el volumen al 10% y abre una pestaña."
        )
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.name, "Inicio de Trabajo")
        self.assertEqual(request.time, "13:23")
        self.assertEqual(request.days, "1,2,3,4,5")
        self.assertEqual(
            json.loads(request.steps_json),
            [
                {
                    "tool": "set_system_volume",
                    "arguments": {"percentage": 10},
                },
                {"tool": "open_new_browser_tab", "arguments": {}},
            ],
        )

    def test_creation_parser_accepts_increasing_volume_from_reported_phrase(self) -> None:
        request = jarvis_routines.parsear_solicitud_creacion(
            "Programa una rutina llamada Descanso a las 13 y 38 de lunes a "
            "viernes sube el volumen al 30% y abre una pestaña."
        )
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.name, "Descanso")
        self.assertEqual(request.time, "13:38")
        self.assertEqual(
            json.loads(request.steps_json),
            [
                {
                    "tool": "set_system_volume",
                    "arguments": {"percentage": 30},
                },
                {"tool": "open_new_browser_tab", "arguments": {}},
            ],
        )

    def test_creation_parser_accepts_dot_time_and_rejects_unparsed_actions(self) -> None:
        request = jarvis_routines.parsear_solicitud_creacion(
            "Programa una rutina llamada Estudio a las 13.23 todos los días "
            "abre una pestaña y navega a google.com.ar"
        )
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.time, "13:23")
        self.assertEqual(request.days, "1,2,3,4,5,6,7")
        self.assertEqual(
            json.loads(request.steps_json),
            [
                {"tool": "open_new_browser_tab", "arguments": {}},
                {
                    "tool": "navigate_browser",
                    "arguments": {"url": "https://google.com.ar"},
                },
            ],
        )
        with self.assertRaisesRegex(ValueError, "No reconocí acciones compatibles"):
            jarvis_routines.parsear_solicitud_creacion(
                "Programa una rutina llamada Estudio a las 13:23 todos los días "
                "borra mis archivos"
            )

    @patch("jarvis_app.jarvis_memory.procesar_comando", return_value=None)
    @patch("jarvis_app.procesar_con_herramientas")
    @patch("jarvis_app.core.guardar_turno")
    @patch("jarvis_app.core.guardar_respuesta", return_value="response-id")
    @patch("jarvis_app.core.generar_audio_tts", return_value="response.wav")
    def test_natural_routine_request_saves_as_routine_without_running_its_actions(
        self,
        _tts: Mock,
        _save_response: Mock,
        save_turn: Mock,
        agent: Mock,
        _memory_command: Mock,
    ) -> None:
        app_host = Mock()
        app_host.tool_registry = crear_registro_herramientas(app_host)
        app_host._request_routine_save_confirmation.return_value = True

        def persist_routine(
            name: str,
            time: str,
            days: str = "1,2,3,4,5,6,7",
            steps_json: str = "",
        ) -> str:
            jarvis_routines.crear_rutina(
                name,
                time,
                days,
                steps_json,
                app_host.tool_registry,
            )
            return "Rutina guardada."

        app_host._tool_schedule_routine.side_effect = persist_routine
        command = (
            "Programa una rutina llamada Inicio de Trabajo a las 13 y 23 "
            "de lunes a viernes baja el volumen al 10% y abre una pestaña."
        )
        answer, audio = JarvisApp._process_agent_query(
            app_host,
            command,
            "chat",
            1.0,
            "conversation-1",
        )
        self.assertEqual((answer, audio), ("Rutina guardada.", "response.wav"))
        confirmation = app_host._request_routine_save_confirmation.call_args
        self.assertEqual(confirmation.args[1].name, "schedule_routine")
        self.assertEqual(confirmation.args[2]["time"], "13:23")
        self.assertEqual(
            [step.tool for step in jarvis_routines.reclamar_rutinas_vencidas(
                datetime(2026, 11, 2, 13, 23)
            )[0].steps],
            ["set_system_volume", "open_new_browser_tab"],
        )
        agent.assert_not_called()
        save_turn.assert_called_once_with(
            "conversation-1",
            command,
            "Rutina guardada.",
        )

    @patch("jarvis_app.jarvis_memory.procesar_comando", return_value=None)
    @patch("jarvis_app.procesar_con_herramientas")
    @patch("jarvis_app.core.guardar_turno")
    @patch("jarvis_app.core.guardar_respuesta", return_value="response-id")
    @patch("jarvis_app.core.generar_audio_tts", return_value="response.wav")
    def test_declining_routine_creation_does_not_save_or_run_any_actions(
        self,
        _tts: Mock,
        _save_response: Mock,
        _save_turn: Mock,
        agent: Mock,
        _memory_command: Mock,
    ) -> None:
        app_host = Mock()
        app_host.tool_registry = crear_registro_herramientas(app_host)
        app_host._request_routine_save_confirmation.return_value = False
        command = (
            "Programa una rutina llamada Inicio de Trabajo a las 13.23 "
            "de lunes a viernes baja el volumen al 10% y abre una pestaña."
        )
        answer, _audio = JarvisApp._process_agent_query(
            app_host,
            command,
            "chat",
            1.0,
            "conversation-1",
        )
        self.assertEqual(answer, "No guardé la rutina «Inicio de Trabajo».")
        app_host._request_routine_save_confirmation.assert_called_once()
        self.assertEqual(jarvis_routines.listar_rutinas(), [])
        agent.assert_not_called()

    def test_legacy_sample_routine_is_not_kept_as_an_active_automation(self) -> None:
        with core.conexion_db() as connection:
            row = connection.execute(
                "SELECT activa, accion FROM rutinas WHERE nombre = 'buenos_dias'"
            ).fetchone()
        self.assertIsNone(row)

    def test_existing_database_migrates_and_disables_obsolete_sample_routine(self) -> None:
        with core.conexion_db() as connection:
            connection.execute("DROP TABLE rutinas")
            connection.execute(
                "CREATE TABLE rutinas ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT UNIQUE, "
                "hora TEXT, accion TEXT, activa INTEGER DEFAULT 1)"
            )
            connection.execute(
                "INSERT INTO rutinas (nombre, hora, accion, activa) "
                "VALUES ('buenos_dias', '07:00', 'notificar_clima', 1)"
            )
        core._database_initialized = False
        core.inicializar_base_datos()
        with core.conexion_db() as connection:
            row = connection.execute(
                "SELECT activa, accion, dias_semana, ultima_ejecucion "
                "FROM rutinas WHERE nombre = 'buenos_dias'"
            ).fetchone()
        self.assertEqual(row, (0, "[]", "1,2,3,4,5,6,7", None))

    def test_scheduled_run_is_approved_before_ordered_execution(self) -> None:
        registry = ToolRegistry()
        executed: list[str] = []
        registry.register(
            "first_step",
            "First routine step.",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            lambda: executed.append("first") or "First done.",
        )
        registry.register(
            "second_step",
            "Second routine step.",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            lambda: executed.append("second") or "Second done.",
        )
        routine = jarvis_routines.Routine(
            1,
            "Inicio de trabajo",
            "09:00",
            (1, 2, 3, 4, 5),
            (
                jarvis_routines.RoutineStep("first_step", {}),
                jarvis_routines.RoutineStep("second_step", {}),
            ),
        )
        host = Mock()
        host.tool_registry = registry
        host.events = queue.Queue()
        host._request_plan_confirmation.return_value = True
        JarvisApp._routine_worker(host, routine)

        event = host.events.get_nowait()
        self.assertEqual(event[:3], ("routine_result", routine, True))
        self.assertEqual(executed, ["first", "second"])
        approval = host._request_plan_confirmation.call_args
        self.assertEqual(
            [action.tool.name for action in approval.args[1]],
            ["first_step", "second_step"],
        )

    def test_high_risk_routine_step_keeps_individual_confirmation(self) -> None:
        registry = ToolRegistry()
        executed: list[str] = []
        registry.register(
            "high_risk_step",
            "High risk routine step.",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            lambda: executed.append("high") or "Done.",
            risk=ToolRisk.HIGH,
            requires_confirmation=True,
        )
        routine = jarvis_routines.Routine(
            2,
            "Acción sensible",
            "09:00",
            (1,),
            (jarvis_routines.RoutineStep("high_risk_step", {}),),
        )
        host = Mock()
        host.tool_registry = registry
        host.events = queue.Queue()
        host._request_plan_confirmation.return_value = True
        host._request_tool_confirmation.return_value = True
        JarvisApp._routine_worker(host, routine)

        event = host.events.get_nowait()
        self.assertTrue(event[2])
        host._request_plan_confirmation.assert_called_once()
        host._request_tool_confirmation.assert_called_once()
        self.assertEqual(executed, ["high"])

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_creating_routine_through_agent_requires_approval_and_persists(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        steps_json = json.dumps(
            [
                {
                    "tool": "set_system_volume",
                    "arguments": {"percentage": 30},
                },
                {"tool": "open_new_browser_tab", "arguments": {}},
            ]
        )
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "schedule_routine",
                                "arguments": {
                                    "name": "Inicio de trabajo",
                                    "time": "09:00",
                                    "days": "1,2,3,4,5",
                                    "steps_json": steps_json,
                                },
                            }
                        }
                    ],
                }
            ),
            _response(
                {
                    "role": "assistant",
                    "content": "Guardé la rutina de inicio de trabajo.",
                }
            ),
        ]
        host = Mock()
        registry = crear_registro_herramientas(host)
        def persist_routine(
            name: str,
            time: str,
            days: str = "1,2,3,4,5,6,7",
            steps_json: str = "",
        ) -> str:
            jarvis_routines.crear_rutina(
                name,
                time,
                days,
                steps_json,
                registry,
            )
            return "Rutina guardada."

        host._tool_schedule_routine.side_effect = persist_routine
        confirmation = Mock(return_value=True)

        answer = procesar_con_herramientas(
            "Programa una rutina a las 09:00 para bajar volumen y abrir pestaña",
            "conversation-1",
            registry,
            confirmation,
        )

        self.assertEqual(answer, "Guardé la rutina de inicio de trabajo.")
        confirmation.assert_called_once()
        self.assertEqual(
            jarvis_routines.listar_rutinas(),
            [(1, "Inicio de trabajo", "09:00", (1, 2, 3, 4, 5), True)],
        )


if __name__ == "__main__":
    unittest.main()
