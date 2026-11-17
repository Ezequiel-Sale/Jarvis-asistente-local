import json
import unittest
from unittest.mock import Mock, patch

from jarvis_agent import (
    _extract_tool_calls,
    _requested_action_count,
    procesar_con_herramientas,
)
import jarvis_media as media
from jarvis_tool_registry import ToolRegistry, ToolRisk


def _response(message: dict) -> Mock:
    response = Mock()
    response.json.return_value = {"message": message}
    response.raise_for_status.return_value = None
    return response


class AgentToolCallTests(unittest.TestCase):
    def setUp(self) -> None:
        memory_search = patch(
            "jarvis_agent.jarvis_memory.buscar_recuerdos",
            return_value=[],
        )
        self.memory_search = memory_search.start()
        self.addCleanup(memory_search.stop)
        self.registry = ToolRegistry()
        self.executed: list[dict] = []
        self.registry.register(
            "set_volume",
            "Set system volume.",
            {
                "type": "object",
                "properties": {
                    "percentage": {"type": "integer", "minimum": 0, "maximum": 100}
                },
                "required": ["percentage"],
                "additionalProperties": False,
            },
            lambda percentage: self.executed.append({"percentage": percentage})
            or f"Volume set to {percentage}.",
        )
        self.registry.register(
            "navigate",
            "Navigate to a website.",
            {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
            lambda url: self.executed.append({"url": url}) or "Navigated.",
            risk=ToolRisk.MEDIUM,
            requires_confirmation=True,
        )
        self.registry.register(
            "open_tab",
            "Open a browser tab.",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            lambda: self.executed.append({"open_tab": True}) or "Tab opened.",
        )

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[("user", "Earlier question")])
    @patch("jarvis_agent.requests.post")
    def test_plain_answer_does_not_execute_tools(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.return_value = _response({"role": "assistant", "content": "Hola."})
        answer = procesar_con_herramientas(
            "Saluda",
            "conversation-1",
            self.registry,
            Mock(return_value=False),
        )
        self.assertEqual(answer, "Hola.")
        self.assertEqual(self.executed, [])
        self.assertEqual(post.call_count, 1)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "qwen2.5-coder:14b")
        self.assertEqual(payload["messages"][1]["content"], "Earlier question")
        self.assertEqual(len(payload["tools"]), 3)
        # «¿Qué más me puedes contar?» sigue el tema anterior en vez de preguntar cuál.
        self.assertIn("seguimiento corto", payload["messages"][0]["content"])
        self.assertEqual(payload["options"]["num_predict"], 1024)

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_answer_cut_by_length_says_so(self, post: Mock, _history: Mock, _context: Mock) -> None:
        respuesta = _response({"role": "assistant", "content": "1. Uno\n2. Do"})
        respuesta.json.return_value["done_reason"] = "length"
        post.return_value = respuesta
        answer = procesar_con_herramientas("Explícame algo largo", "c1", self.registry, Mock(return_value=False))
        self.assertTrue(answer.startswith("1. Uno\n2. Do…"))
        self.assertIn("dime «cuéntame más»", answer)
        # «sigue» solo es reanudar el vídeo (jarvis_media): el aviso no debe sugerirlo.
        self.assertIsNone(media.detectar_comando("cuentame mas"))

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_relevant_memories_are_included_as_context(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        self.memory_search.return_value = [(7, "Prefiero Brave como navegador")]
        post.return_value = _response(
            {"role": "assistant", "content": "Usas Brave."}
        )
        answer = procesar_con_herramientas(
            "¿Qué navegador prefiero?",
            "conversation-1",
            self.registry,
            Mock(return_value=False),
        )
        system_message = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertIn("Prefiero Brave como navegador", system_message)
        self.assertEqual(answer, "Usas Brave.")

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_tool_call_runs_and_result_is_returned_to_ollama(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "set_volume",
                                "arguments": {"percentage": 35},
                            }
                        }
                    ],
                }
            ),
            _response({"role": "assistant", "content": "Volumen ajustado."}),
        ]
        answer = procesar_con_herramientas(
            "Baja el volumen al 35%",
            "conversation-1",
            self.registry,
            Mock(return_value=False),
        )
        self.assertEqual(answer, "Volumen ajustado.")
        self.assertEqual(self.executed, [{"percentage": 35}])
        follow_up = post.call_args_list[1].kwargs["json"]["messages"]
        self.assertEqual(follow_up[-1]["role"], "tool")
        self.assertEqual(
            json.loads(follow_up[-1]["content"]),
            {"success": True, "result": "Volume set to 35."},
        )

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_multi_action_plan_is_approved_before_ordered_execution(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "set_volume",
                                "arguments": {"percentage": 30},
                            }
                        },
                        {
                            "function": {
                                "name": "navigate",
                                "arguments": {"url": "https://example.com"},
                            }
                        },
                    ],
                }
            ),
            _response({"role": "assistant", "content": "Plan completado."}),
        ]

        def approve_plan(goal: str, plan: tuple) -> bool:
            self.assertEqual(goal, "Prepara el entorno de trabajo")
            self.assertEqual(len(plan), 2)
            self.assertEqual(self.executed, [])
            self.assertEqual(plan[0].tool.name, "set_volume")
            self.assertEqual(plan[1].arguments, {"url": "https://example.com"})
            return True

        answer = procesar_con_herramientas(
            "Prepara el entorno de trabajo",
            "conversation-1",
            self.registry,
            Mock(return_value=True),
            approve_plan=approve_plan,
        )
        self.assertEqual(answer, "Plan completado.")
        self.assertEqual(
            self.executed,
            [
                {"percentage": 30},
                {"url": "https://example.com"},
            ],
        )
        confirm = post.call_args_list[1].kwargs["json"]
        self.assertNotIn("tools", confirm)
        self.assertEqual(
            [json.loads(message["content"]) for message in confirm["messages"][-2:]],
            [
                {"success": True, "result": "Volume set to 30."},
                {"success": True, "result": "Navigated."},
            ],
        )

    def test_extracts_concatenated_json_calls_as_a_single_tool_batch(self) -> None:
        message = {
            "role": "assistant",
            "content": (
                '{\n  "name": "set_volume",\n'
                '  "arguments": {"percentage": 30}\n}\n'
                '{\n  "name": "navigate",\n'
                '  "arguments": {"url": "https://example.com"}\n}'
            ),
        }
        calls = _extract_tool_calls(message)
        self.assertEqual(
            calls,
            [
                {
                    "function": {
                        "name": "set_volume",
                        "arguments": {"percentage": 30},
                    }
                },
                {
                    "function": {
                        "name": "navigate",
                        "arguments": {"url": "https://example.com"},
                    }
                },
            ],
        )

    def test_extracts_three_calls_from_markdown_fenced_json_array(self) -> None:
        message = {
            "role": "assistant",
            "content": (
                "```json\n"
                "[\n"
                '  {"name":"set_volume","arguments":{"percentage":50}},\n'
                '  {"name":"open_tab","arguments":{}},\n'
                '  {"name":"navigate","arguments":{"url":"https://google.com.ar"}}\n'
                "]\n"
                "```"
            ),
        }
        calls = _extract_tool_calls(message)
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            [call["function"]["name"] for call in calls],
            ["set_volume", "open_tab", "navigate"],
        )

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_three_step_fenced_plan_is_approved_then_executed_in_order(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": (
                        "```json\n"
                        "[\n"
                        '  {"name":"set_volume","arguments":{"percentage":50}},\n'
                        '  {"name":"open_tab","arguments":{}},\n'
                        '  {"name":"navigate","arguments":{"url":"https://google.com.ar"}}\n'
                        "]\n"
                        "```"
                    ),
                }
            ),
            _response({"role": "assistant", "content": "Plan completado."}),
        ]
        approve_plan = Mock(return_value=True)
        answer = procesar_con_herramientas(
            "Sube el volumen al 50%, luego abre una pestaña y navega a google.com.ar",
            "conversation-1",
            self.registry,
            Mock(return_value=True),
            approve_plan=approve_plan,
        )
        self.assertEqual(answer, "Plan completado.")
        approve_plan.assert_called_once()
        self.assertEqual(
            [action.tool.name for action in approve_plan.call_args.args[1]],
            ["set_volume", "open_tab", "navigate"],
        )
        self.assertEqual(
            self.executed,
            [
                {"percentage": 50},
                {"open_tab": True},
                {"url": "https://google.com.ar"},
            ],
        )

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_retries_once_when_model_omits_tools_then_executes_complete_three_step_plan(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": "Voy a bajar el volumen y abrir Google.",
                }
            ),
            _response(
                {
                    "role": "assistant",
                    "content": (
                        '[{"name":"set_volume","arguments":{"percentage":30}},'
                        '{"name":"open_tab","arguments":{}},'
                        '{"name":"navigate","arguments":{"url":"https://google.com.ar"}}]'
                    ),
                }
            ),
            _response({"role": "assistant", "content": "Plan completado."}),
        ]
        approve_plan = Mock(return_value=True)
        answer = procesar_con_herramientas(
            "Bajar el volumen al 30% luego abrir una nueva pestaña y navegar a google.com.ar",
            "conversation-1",
            self.registry,
            Mock(return_value=True),
            approve_plan=approve_plan,
        )
        self.assertEqual(answer, "Plan completado.")
        self.assertEqual(post.call_count, 3)
        self.assertEqual(
            [action.tool.name for action in approve_plan.call_args.args[1]],
            ["set_volume", "open_tab", "navigate"],
        )
        self.assertEqual(
            self.executed,
            [
                {"percentage": 30},
                {"open_tab": True},
                {"url": "https://google.com.ar"},
            ],
        )
        retry_messages = post.call_args_list[1].kwargs["json"]["messages"]
        self.assertTrue(
            any("Reintenta ahora" in message["content"] for message in retry_messages)
        )

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_incomplete_three_action_plan_does_not_run_any_step(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.return_value = _response(
            {
                "role": "assistant",
                "content": (
                    '[{"name":"set_volume","arguments":{"percentage":50}},'
                    '{"name":"open_tab","arguments":{}}]'
                ),
            }
        )
        approve_plan = Mock(return_value=True)
        answer = procesar_con_herramientas(
            "Sube el volumen al 50%, abre una pestaña y navega a google.com.ar",
            "conversation-1",
            self.registry,
            Mock(return_value=True),
            approve_plan=approve_plan,
        )
        self.assertIn("plan propuesto estaba incompleto", answer)
        self.assertEqual(self.executed, [])
        approve_plan.assert_not_called()

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_action_request_without_tool_calls_never_claims_success(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": "Subí el volumen y abrí una pestaña.",
                }
            ),
            _response(
                {
                    "role": "assistant",
                    "content": "No puedo realizar esa solicitud.",
                }
            ),
        ]
        answer = procesar_con_herramientas(
            "Sube el volumen al 50% y abre una pestaña",
            "conversation-1",
            self.registry,
            Mock(return_value=True),
        )
        self.assertIn("No ejecuté ninguna acción", answer)
        self.assertEqual(self.executed, [])
        self.assertEqual(post.call_count, 2)

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_concatenated_json_calls_use_the_plan_confirmation_flow(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": (
                        '{"name":"set_volume","arguments":{"percentage":30}}\n'
                        '{"name":"navigate","arguments":{"url":"https://example.com"}}'
                    ),
                }
            ),
            _response({"role": "assistant", "content": "Plan completado."}),
        ]
        approve_plan = Mock(return_value=True)
        confirmation = Mock(return_value=True)
        answer = procesar_con_herramientas(
            "Baja el volumen al 30% y abre una pestaña en example.com",
            "conversation-1",
            self.registry,
            confirmation,
            approve_plan=approve_plan,
        )
        self.assertEqual(answer, "Plan completado.")
        approve_plan.assert_called_once()
        confirmation.assert_not_called()
        self.assertEqual(
            self.executed,
            [
                {"percentage": 30},
                {"url": "https://example.com"},
            ],
        )

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_approved_plan_covers_its_high_risk_steps(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        self.registry.register(
            "high_risk_action",
            "Perform a high-risk action.",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            lambda: self.executed.append({"high_risk_action": True}) or "Done.",
            risk=ToolRisk.HIGH,
            requires_confirmation=True,
        )
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "high_risk_action",
                                "arguments": {},
                            }
                        },
                        {
                            "function": {
                                "name": "set_volume",
                                "arguments": {"percentage": 30},
                            }
                        },
                    ],
                }
            ),
            _response({"role": "assistant", "content": "Plan completado."}),
        ]
        confirmation = Mock(return_value=True)
        answer = procesar_con_herramientas(
            "Ejecuta la acción y cambia el volumen",
            "conversation-1",
            self.registry,
            confirmation,
            approve_plan=Mock(return_value=True),
        )
        self.assertEqual(answer, "Plan completado.")
        # Aprobar el plan completo ya autoriza sus pasos (antes se volvía a preguntar).
        confirmation.assert_not_called()
        self.assertEqual(
            self.executed,
            [{"high_risk_action": True}, {"percentage": 30}],
        )

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_power_and_similar_steps_still_ask_inside_an_approved_plan(
        self, post: Mock, _historial: Mock, _contexto: Mock
    ) -> None:
        self.registry.register(
            "power_control",
            "Apaga el equipo.",
            {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            lambda: self.executed.append({"power": True}) or "Apagando.",
            risk=ToolRisk.HIGH,
            requires_confirmation=True,
        )
        post.side_effect = [
            _response({"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "set_volume", "arguments": {"percentage": 30}}},
                {"function": {"name": "power_control", "arguments": {}}},
            ]}),
            _response({"role": "assistant", "content": "Listo."}),
        ]
        confirmation = Mock(return_value=True)
        procesar_con_herramientas("Baja el volumen y apaga", "conversation-1", self.registry,
                                  confirmation, approve_plan=Mock(return_value=True))
        confirmation.assert_called_once()

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_plan_scope_wraps_the_approved_plan_and_adds_its_notices(
        self, post: Mock, _historial: Mock, _contexto: Mock
    ) -> None:
        import contextlib

        eventos = []

        @contextlib.contextmanager
        def alcance(plan):
            eventos.append(("inicio", len(plan)))
            avisos = []
            yield avisos
            eventos.append(("fin", list(self.executed)))
            avisos.append("No confirmaste los cambios de monitores")

        post.side_effect = [
            _response({"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "set_volume", "arguments": {"percentage": 30}}},
                {"function": {"name": "set_volume", "arguments": {"percentage": 40}}},
            ]}),
            _response({"role": "assistant", "content": "Listo."}),
        ]
        answer = procesar_con_herramientas("Sube y baja", "conversation-1", self.registry, Mock(return_value=True),
                                           approve_plan=Mock(return_value=True), plan_scope=alcance)
        self.assertEqual(eventos, [("inicio", 2), ("fin", [{"percentage": 30}, {"percentage": 40}])])
        self.assertIn("No confirmaste los cambios de monitores", answer)

    def test_plan_covers_rule(self) -> None:
        from jarvis_agent import plan_cubre

        def definicion(nombre, riesgo):
            from types import SimpleNamespace

            return SimpleNamespace(name=nombre, risk=riesgo)

        self.assertTrue(plan_cubre(definicion("configure_display", ToolRisk.HIGH)))
        self.assertTrue(plan_cubre(definicion("set_volume", ToolRisk.MEDIUM)))
        for nombre in ("power_control", "clean_disk", "mobile_remote_control", "screen_click", "mcp_borrar"):
            with self.subTest(nombre=nombre):
                self.assertFalse(plan_cubre(definicion(nombre, ToolRisk.HIGH)))


    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_rejected_plan_never_executes_any_step(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "set_volume",
                                "arguments": {"percentage": 30},
                            }
                        },
                        {
                            "function": {
                                "name": "navigate",
                                "arguments": {"url": "https://example.com"},
                            }
                        },
                    ],
                }
            ),
            _response({"role": "assistant", "content": "Plan cancelado."}),
        ]
        plan_approval = Mock(return_value=False)
        confirmation = Mock(return_value=True)
        answer = procesar_con_herramientas(
            "Prepara el entorno",
            "conversation-1",
            self.registry,
            confirmation,
            approve_plan=plan_approval,
        )
        self.assertEqual(answer, "Plan cancelado.")
        self.assertEqual(self.executed, [])
        plan_approval.assert_called_once()
        confirmation.assert_not_called()
        self.assertNotIn("tools", post.call_args_list[1].kwargs["json"])
        self.assertTrue(
            all(
                json.loads(message["content"])["cancelled"]
                for message in post.call_args_list[1].kwargs["json"]["messages"][-2:]
            )
        )

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_invalid_plan_step_prevents_all_execution_and_preview(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "set_volume",
                                "arguments": {"percentage": 30},
                            }
                        },
                        {
                            "function": {
                                "name": "navigate",
                                "arguments": {"url": 42},
                            }
                        },
                    ],
                }
            ),
            _response({"role": "assistant", "content": "No se ejecutó el plan."}),
        ]
        plan_approval = Mock(return_value=True)
        answer = procesar_con_herramientas(
            "Prepara el entorno",
            "conversation-1",
            self.registry,
            Mock(return_value=True),
            approve_plan=plan_approval,
        )
        self.assertEqual(answer, "No se ejecutó el plan.")
        self.assertEqual(self.executed, [])
        plan_approval.assert_not_called()
        self.assertNotIn("tools", post.call_args_list[1].kwargs["json"])

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_plan_stops_after_first_failed_step(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        self.registry.register(
            "failing_action",
            "Fails for the test.",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            lambda: (_ for _ in ()).throw(RuntimeError("step failed")),
        )
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "failing_action",
                                "arguments": {},
                            }
                        },
                        {
                            "function": {
                                "name": "set_volume",
                                "arguments": {"percentage": 30},
                            }
                        },
                    ],
                }
            ),
            _response({"role": "assistant", "content": "El plan se detuvo."}),
        ]
        answer = procesar_con_herramientas(
            "Ejecuta los dos pasos",
            "conversation-1",
            self.registry,
            Mock(return_value=True),
            approve_plan=Mock(return_value=True),
        )
        self.assertTrue(answer.startswith("El plan se detuvo."))
        self.assertIn("Atención: «failing_action» falló: step failed", answer)
        self.assertEqual(self.executed, [])
        result_messages = post.call_args_list[1].kwargs["json"]["messages"][-2:]
        first_result, second_result = (
            json.loads(message["content"]) for message in result_messages
        )
        self.assertFalse(first_result["success"])
        self.assertIn("step failed", first_result["error"])
        self.assertTrue(second_result["cancelled"])

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_strict_json_tool_envelope_works_with_qwen_output(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": '{"name":"set_volume","arguments":{"percentage":35}}',
                }
            ),
            _response({"role": "assistant", "content": "Volumen ajustado."}),
        ]
        answer = procesar_con_herramientas(
            "Baja el volumen al 35%",
            "conversation-1",
            self.registry,
            Mock(return_value=False),
        )
        self.assertEqual(answer, "Volumen ajustado.")
        self.assertEqual(self.executed, [{"percentage": 35}])
        messages = post.call_args_list[1].kwargs["json"]["messages"]
        self.assertEqual(messages[-2]["role"], "assistant")
        self.assertEqual(messages[-1]["role"], "tool")

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_confirmed_tool_waits_for_explicit_approval(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "navigate",
                                "arguments": {"url": "https://example.com"},
                            }
                        }
                    ],
                }
            ),
            _response({"role": "assistant", "content": "Abrí el sitio."}),
        ]
        confirm = Mock(return_value=True)
        answer = procesar_con_herramientas(
            "Abre example.com",
            "conversation-1",
            self.registry,
            confirm,
        )
        self.assertEqual(answer, "Abrí el sitio.")
        self.assertEqual(confirm.call_count, 1)
        self.assertEqual(self.executed, [{"url": "https://example.com"}])

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_declined_confirmation_never_executes_tool(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "navigate",
                                "arguments": {"url": "https://example.com"},
                            }
                        }
                    ],
                }
            ),
            _response({"role": "assistant", "content": "Acción cancelada."}),
        ]
        answer = procesar_con_herramientas(
            "Abre example.com",
            "conversation-1",
            self.registry,
            Mock(return_value=False),
        )
        self.assertEqual(answer, "Acción cancelada.")
        self.assertEqual(self.executed, [])
        follow_up = post.call_args_list[1].kwargs["json"]["messages"]
        self.assertTrue(json.loads(follow_up[-1]["content"])["cancelled"])

    @patch("jarvis_agent.core.cargar_contexto_db", return_value="System context.")
    @patch("jarvis_agent.core.obtener_historial", return_value=[])
    @patch("jarvis_agent.requests.post")
    def test_invalid_tool_arguments_never_prompt_or_execute(
        self,
        post: Mock,
        _history: Mock,
        _context: Mock,
    ) -> None:
        post.side_effect = [
            _response(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "set_volume",
                                "arguments": {"percentage": 200},
                            }
                        }
                    ],
                }
            ),
            _response({"role": "assistant", "content": "No se pudo cambiar."}),
        ]
        confirm = Mock(return_value=True)
        answer = procesar_con_herramientas(
            "Sube el volumen",
            "conversation-1",
            self.registry,
            confirm,
        )
        self.assertEqual(answer, "No se pudo cambiar.")
        confirm.assert_not_called()
        self.assertEqual(self.executed, [])
        result = json.loads(post.call_args_list[1].kwargs["json"]["messages"][-1]["content"])
        self.assertFalse(result["success"])
        self.assertIn("máximo", result["error"])

    def test_attempt_limit_settings_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            procesar_con_herramientas(
                "Hola",
                "conversation-1",
                self.registry,
                Mock(return_value=False),
                max_tool_rounds=0,
            )

    def test_scheduling_request_counts_as_one_create_action(self) -> None:
        self.assertEqual(
            _requested_action_count(
                "Programa una rutina a las 09:00 para bajar volumen y abrir una pestaña."
            ),
            1,
        )


class WebSearchFallbackTests(unittest.TestCase):
    """«¿Qué es MCP?»: el modelo decía que no sabía en vez de usar web_search."""

    def setUp(self) -> None:
        for objetivo, valor in (
            ("jarvis_agent.jarvis_memory.buscar_recuerdos", []),
            ("jarvis_agent.core.cargar_contexto_db", "Contexto."),
        ):
            parche = patch(objetivo, return_value=valor)
            parche.start()
            self.addCleanup(parche.stop)
        self.busquedas: list[str] = []
        self.registry = ToolRegistry()
        self.registry.register(
            "web_search", "Busca en internet.",
            {"type": "object", "properties": {"query": {"type": "string"}},
             "required": ["query"], "additionalProperties": False},
            lambda query: self.busquedas.append(query) or "Resultados: MCP es un protocolo abierto.",
        )
        self.no_se = "Lo siento, pero no tengo información específica sobre \"MCP\"."

    @patch("jarvis_agent.requests.post")
    def test_searches_by_itself_when_the_model_says_it_does_not_know(self, post: Mock) -> None:
        post.side_effect = [
            _response({"role": "assistant", "content": self.no_se}),
            _response({"role": "assistant", "content": "MCP es un protocolo abierto.\n\nFuentes: x"}),
        ]
        with patch("jarvis_agent.core.obtener_historial", return_value=[]):
            answer = procesar_con_herramientas("¿Qué es MCP?", "c", self.registry, Mock(return_value=True))
        self.assertEqual(self.busquedas, ["¿Qué es MCP?"])
        self.assertTrue(answer.startswith("MCP es un protocolo abierto."))
        sistema = post.call_args_list[0].kwargs["json"]["messages"][0]["content"]
        self.assertIn("nunca digas que no tienes información sin haber buscado", sistema)
        segunda = post.call_args_list[1].kwargs["json"]["messages"]
        self.assertEqual(segunda[-1]["role"], "tool")

    @patch("jarvis_agent.requests.post")
    def test_only_once_and_old_answers_are_dropped_from_history(self, post: Mock) -> None:
        post.side_effect = [
            _response({"role": "assistant", "content": self.no_se}),
            _response({"role": "assistant", "content": self.no_se}),
        ]
        historial = [("user", "¿Qué es MCP?"), ("assistant", self.no_se),
                     ("user", "hola"), ("assistant", "Hola, ¿en qué te ayudo?")]
        with patch("jarvis_agent.core.obtener_historial", return_value=historial):
            answer = procesar_con_herramientas("¿Qué es MCP?", "c", self.registry, Mock(return_value=True))
        self.assertEqual(answer, self.no_se)  # no insiste en bucle
        self.assertEqual(len(self.busquedas), 1)
        # (la lista de mensajes es la misma en todas las llamadas: crece después)
        enviados = [m["content"] for m in post.call_args_list[0].kwargs["json"]["messages"][1:4]]
        self.assertEqual(enviados, ["hola", "Hola, ¿en qué te ayudo?", "¿Qué es MCP?"])

    @patch("jarvis_agent.requests.post")
    def test_without_search_tool_nothing_changes(self, post: Mock) -> None:
        post.return_value = _response({"role": "assistant", "content": self.no_se})
        historial = [("user", "¿Qué es MCP?"), ("assistant", self.no_se)]
        with patch("jarvis_agent.core.obtener_historial", return_value=historial):
            answer = procesar_con_herramientas("¿Qué es MCP?", "c", ToolRegistry(), Mock(return_value=True))
        self.assertEqual(answer, self.no_se)
        self.assertEqual(post.call_count, 1)
        mensajes = post.call_args.kwargs["json"]["messages"]
        self.assertEqual(len(mensajes), 4)  # el historial queda igual
        self.assertNotIn("web_search", mensajes[0]["content"])

    @patch("jarvis_agent.requests.post")
    def test_explicit_request_searches_without_asking_the_model(self, post: Mock) -> None:
        """2026-11-16: «Busca en internet cuándo…» → «no puedo buscar en tiempo real»."""
        post.return_value = _response({"role": "assistant", "content": "El próximo amistoso es el 20."})
        with patch("jarvis_agent.core.obtener_historial", return_value=[]):
            answer = procesar_con_herramientas(
                "Busca en internet cuándo es el próximo amistoso de la selección", "c",
                self.registry, Mock(return_value=True))
        self.assertEqual(self.busquedas, ["cuándo es el próximo amistoso de la selección"])
        self.assertEqual(post.call_count, 1)  # solo para redactar con los resultados
        self.assertEqual(post.call_args.kwargs["json"]["messages"][-1]["role"], "tool")
        self.assertTrue(answer.startswith("El próximo amistoso es el 20."))

    @patch("jarvis_agent.requests.post")
    def test_cannot_search_in_real_time_counts_as_not_knowing(self, post: Mock) -> None:
        post.side_effect = [
            _response({"role": "assistant", "content": "Lo siento, pero no puedo buscar en internet en tiempo "
                                                       "real. Te recomiendo que consultes un sitio confiable."}),
            _response({"role": "assistant", "content": "Juega el 20."}),
        ]
        with patch("jarvis_agent.core.obtener_historial", return_value=[]):
            answer = procesar_con_herramientas("¿Cuándo juega la selección?", "c", self.registry,
                                               Mock(return_value=True))
        self.assertEqual(self.busquedas, ["¿Cuándo juega la selección?"])
        self.assertTrue(answer.startswith("Juega el 20."))

    LLAMADA_ESCRITA = ('{\n  "name": "web_search",\n  "arguments": {\n    "query": "próximo amistoso"\n  }\n}'
                       "\n\nFuentes:\n1. https://ejemplo.com/amistosos")

    @patch("jarvis_agent.requests.post")
    def test_after_a_search_the_model_must_write_not_call(self, post: Mock) -> None:
        """2026-11-16: tras buscar, escribió otra llamada en JSON + «Fuentes:» y se mostró y leyó."""
        post.side_effect = [
            _response({"role": "assistant", "content": self.LLAMADA_ESCRITA}),
            _response({"role": "assistant", "content": "Argentina juega el 20 contra Islandia.\n\nFuentes: x"}),
        ]
        with patch("jarvis_agent.core.obtener_historial", return_value=[]):
            answer = procesar_con_herramientas("googlea cuándo es el próximo amistoso", "c", self.registry,
                                               Mock(return_value=True))
        self.assertTrue(answer.startswith("Argentina juega el 20"))
        self.assertNotIn('"name"', answer)
        self.assertEqual(len(self.busquedas), 1)  # la llamada escrita no se ejecuta
        for llamada in post.call_args_list:  # tras la búsqueda pedida, sin herramientas
            self.assertNotIn("tools", llamada.kwargs["json"])
        self.assertIn("sin JSON", post.call_args_list[1].kwargs["json"]["messages"][-1]["content"])

    @patch("jarvis_agent.requests.post")
    def test_written_call_is_never_shown(self, post: Mock) -> None:
        post.return_value = _response({"role": "assistant", "content": self.LLAMADA_ESCRITA})
        with patch("jarvis_agent.core.obtener_historial", return_value=[]):
            answer = procesar_con_herramientas("googlea cuándo es el próximo amistoso", "c", self.registry,
                                               Mock(return_value=True))
        self.assertNotIn("{", answer)
        self.assertTrue(answer.startswith("Encontré resultados, pero no pude redactar la respuesta."))
        self.assertIn("https://ejemplo.com/amistosos", answer)

    @patch("jarvis_agent.requests.post")
    def test_too_many_searches_answer_instead_of_failing(self, post: Mock) -> None:
        """2026-11-16: tres búsquedas y «Ollama siguió solicitando herramientas…»."""
        def buscar(q):
            return {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "web_search", "arguments": {"query": q}}}]}
        post.side_effect = [
            _response(buscar("argentina costa rica hora")),
            _response(buscar("argentina costa rica 17 noviembre")),
            _response(buscar("otra más")),  # ya no hay herramientas: se le pide responder
            _response({"role": "assistant", "content": "Juega a las 20 h en Córdoba."}),
        ]
        with patch("jarvis_agent.core.obtener_historial", return_value=[]):
            answer = procesar_con_herramientas("¿A qué hora juega Argentina contra Costa Rica?", "c",
                                               self.registry, Mock(return_value=True))
        self.assertEqual(len(self.busquedas), 2)
        self.assertTrue(answer.startswith("Juega a las 20 h en Córdoba."))
        self.assertNotIn("tools", post.call_args_list[2].kwargs["json"])

    @patch("jarvis_agent.requests.post")
    def test_keeps_asking_for_tools_gives_a_notice(self, post: Mock) -> None:
        llamada = {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "x"}}}]}
        post.return_value = _response(llamada)
        with patch("jarvis_agent.core.obtener_historial", return_value=[]):
            answer = procesar_con_herramientas("¿A qué hora juega Argentina?", "c", self.registry,
                                               Mock(return_value=True))
        self.assertTrue(answer.startswith("Encontré resultados, pero no pude redactar la respuesta."))

    @patch("jarvis_agent.requests.post")
    def test_claimed_action_without_a_tool_is_not_believed(self, post: Mock) -> None:
        """2026-11-16: «Puse Jarvis a la izquierda del monitor 2.» sin llamar a ninguna herramienta."""
        from jarvis_agent import AVISO_SIN_ACCION

        post.return_value = _response({"role": "assistant", "content": "Puse Jarvis a la izquierda del monitor 2."})
        with patch("jarvis_agent.core.obtener_historial", return_value=[]):
            answer = procesar_con_herramientas("deja a Jarvis donde quieras", "c", self.registry,
                                               Mock(return_value=True))
        self.assertEqual(answer, AVISO_SIN_ACCION)
        post.return_value = _response({"role": "assistant", "content": "Python es un lenguaje de programación."})
        with patch("jarvis_agent.core.obtener_historial", return_value=[]):
            answer = procesar_con_herramientas("¿Qué es Python?", "c", self.registry, Mock(return_value=True))
        self.assertEqual(answer, "Python es un lenguaje de programación.")

    @patch("jarvis_agent.requests.post")
    def test_actions_done_but_no_final_text_is_summarized(self, post: Mock) -> None:
        """2026-11-16 «Pon Brave al frente»: tres pasos hechos y «No se obtuvo una respuesta final»."""
        hechas: list[str] = []
        self.registry.register(
            "focus_window", "Trae una ventana al frente.",
            {"type": "object", "properties": {"app": {"type": "string"}}, "required": ["app"],
             "additionalProperties": False},
            lambda app: hechas.append(app) or "Listo.", label="Controlar ventanas")
        post.return_value = _response({"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "focus_window", "arguments": {"app": "brave"}}}]})
        with patch("jarvis_core.obtener_historial", return_value=[]):
            answer = procesar_con_herramientas("trae Brave", "c", self.registry, Mock(return_value=True))
        self.assertTrue(hechas)
        self.assertTrue(answer.startswith("Listo. Acciones ya completadas: «Controlar ventanas»"))

    def test_any_past_tense_claim_is_caught(self) -> None:
        """2026-11-16: «Arregla el escalador» → «Arreglé el escalador del monitor 3» sin ninguna acción."""
        from jarvis_agent import _ACCION_INVENTADA

        for inventada in ("Arreglé el escalador del monitor 3 (Smart TV Pro).",
                          "Reajusté el escalado del monitor 3: lo puse al 200 %.", "Revisé y no hay nada.",
                          "Ya lo moví al monitor 2.", "He reiniciado la tele.", "Listo, corregí la barra."):
            with self.subTest(inventada=inventada):
                self.assertTrue(_ACCION_INVENTADA.match(inventada))
        for normal in ("Aquí tienes la información.", "Así es: Python es un lenguaje.", "También puedo ayudarte.",
                       "Sí, claro.", "Qué bueno.", "El clima hoy es soleado.", "Lo siento, no puedo hacer eso."):
            with self.subTest(normal=normal):
                self.assertFalse(_ACCION_INVENTADA.match(normal))

    def test_written_call_detection(self) -> None:
        from jarvis_agent import _llamada_escrita, _sin_llamadas_escritas

        self.assertTrue(_llamada_escrita(self.LLAMADA_ESCRITA))
        self.assertTrue(_llamada_escrita('```json\n{"name": "x", "arguments": {}}\n```'))
        self.assertFalse(_llamada_escrita("Argentina juega el 20."))
        self.assertFalse(_llamada_escrita("[1, 2]"))
        self.assertEqual(_sin_llamadas_escritas(self.LLAMADA_ESCRITA), "Fuentes:\n1. https://ejemplo.com/amistosos")

    def test_explicit_search_phrases(self) -> None:
        from jarvis_agent import consulta_explicita

        casos = {
            "Busca en internet cuándo es el próximo amistoso de la selección argentina de fútbol":
                "cuándo es el próximo amistoso de la selección argentina de fútbol",
            "Jarvis, googlea precio del dólar": "precio del dólar",
            "el resultado de River búscalo en internet": "el resultado de River",
            "buscá en google qué es MCP": "qué es MCP",
            "¿Podrías buscar en internet quién ganó?": "quién ganó",
            "Busque en internet la hora del partido entre Argentina y Colombia":
                "la hora del partido entre Argentina y Colombia",
            "busca el archivo de fotos": None,
            "abre YouTube y busca música": None,
            "qué hora es": None,
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(consulta_explicita(texto), esperado)


if __name__ == "__main__":
    unittest.main()
