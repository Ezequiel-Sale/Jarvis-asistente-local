import json
import unittest
from unittest.mock import Mock, patch

from jarvis_agent import (
    _extract_tool_calls,
    _requested_action_count,
    procesar_con_herramientas,
)
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
    def test_approved_plan_still_confirms_high_risk_steps_individually(
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
        confirmation.assert_called_once()
        self.assertEqual(
            self.executed,
            [{"high_risk_action": True}, {"percentage": 30}],
        )

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


if __name__ == "__main__":
    unittest.main()
