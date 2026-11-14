import json
import queue
import unittest

from jarvis_tool_registry import (
    ToolConfirmationRequired,
    ToolNotFoundError,
    ToolRegistry,
    ToolRisk,
    ToolValidationError,
)
from jarvis_tools import crear_registro_herramientas


class _TestHost:
    def __init__(self) -> None:
        self.events: queue.Queue[tuple] = queue.Queue()

    def _record(self, event: tuple, result: str) -> str:
        self.events.put(event)
        return result

    def _tool_set_system_volume(self, percentage: int) -> str:
        return self._record(("volume", percentage), "volume set")

    def _tool_set_output_device(self, device: str) -> str:
        return self._record(("device", device), "device set")

    def _tool_set_jarvis_audio(self, enabled: bool) -> str:
        return self._record(("audio", enabled), "audio set")

    def _tool_open_new_browser_tab(self) -> str:
        return self._record(("new_tab",), "tab opened")

    def _tool_navigate_browser(self, url: str) -> str:
        return self._record(("navigate", url), "navigated")

    def _tool_launch_game(self, name: str) -> str:
        return self._record(("game", name), "game launched")

    def _tool_manage_window(self, action: str) -> str:
        return self._record(("window", action), "window changed")

    def _tool_window_control(self, app: str, action: str) -> str:
        return self._record(("window_control", app, action), "window state changed")

    def _tool_browser_history(self, action: str) -> str:
        return self._record(("browser_history", action), "browser navigated")

    def _tool_search_youtube(self, query: str, play_index: int | None = None) -> str:
        return self._record(("youtube", query, play_index), "YouTube opened")

    def _tool_configure_display(
        self, action: str, monitor: int, second_monitor: int | None = None
    ) -> str:
        return self._record(("display", action, monitor, second_monitor), "display changed")

    def _tool_restore_display(self, routine: str = "") -> str:
        return self._record(("restore_display", routine), "display restored")

    def _tool_tv_control(self, action: str, steps: int = 1, level: int | None = None) -> str:
        return self._record(("tv", action, steps, level), "tv")

    def _tool_tv_open_app(self, app: str) -> str:
        return self._record(("tv_app", app), "tv app")

    def _tool_tv_status(self) -> str:
        return "tv status"

    def _tool_undo_last_action(self) -> str:
        return self._record(("undo",), "undo")

    def _tool_find_files(self, query: str = "", kind: str = "any", days: int = 0, open_best: bool = False) -> str:
        return self._record(("find", query, kind, days, open_best), "found")

    def _tool_open_file(self, path: str) -> str:
        return self._record(("open_file", path), "opened")

    def _tool_show_commands_help(self, topic: str = "") -> str:
        return self._record(("help", topic), "help")

    def _tool_morning_briefing(self, automatic: bool | None = None) -> str:
        return self._record(("briefing", automatic), "briefing")

    def _tool_fix_display_scaling(self, monitor: int | None = None) -> str:
        return self._record(("scaling", monitor), "scaling fixed")

    def _tool_set_theme(self, theme: str) -> str:
        return self._record(("theme", theme), "theme changed")

    def _tool_set_compact_mode(self, enabled: bool) -> str:
        return self._record(("compact", enabled), "compact")

    def _tool_clean_disk(self, categories: list) -> str:
        return self._record(("clean", tuple(categories)), "cleaned")

    def _tool_set_voice(self, voice: str) -> str:
        return self._record(("voice", voice), "voice")

    def _tool_apply_gpu_profile(self) -> str:
        return self._record(("gpu",), "gpu")

    def _tool_gpu_profile_status(self) -> str:
        return "gpu status"

    def _tool_move_window(self, app: str, monitor: int | None = None, screen_name: str | None = None) -> str:
        return self._record(("move", app, monitor, screen_name), "moved")

    def _tool_schedule_routine(
        self,
        name: str,
        time: str,
        days: str = "1,2,3,4,5,6,7",
        steps_json: str = "",
    ) -> str:
        return self._record(
            ("schedule_routine", name, time, days, steps_json),
            "routine scheduled",
        )


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = _TestHost()
        self.registry = crear_registro_herramientas(self.host)

    def test_registered_tools_export_ollama_schemas_without_handlers(self) -> None:
        tools = self.registry.ollama_tools()
        self.assertEqual(len(tools), 49)
        self.assertTrue(all(tool["type"] == "function" for tool in tools))
        self.assertEqual(
            {tool["function"]["name"] for tool in tools},
            {
                "set_system_volume",
                "power_control",
                "get_current_datetime",
                "morning_briefing",
                "show_commands_help",
                "undo_last_action",
                "find_files",
                "open_file",
                "tv_control",
                "tv_open_app",
                "tv_status",
                "press_window_button",
                "set_reminder",
                "list_reminders",
                "cancel_reminder",
                "get_headset_battery",
                "media_control",
                "mute_browser_tab",
                "get_now_playing",
                "set_app_volume",
                "open_program",
                "cancel_power_action",
                "lock_computer",
                "set_output_device",
                "set_jarvis_audio",
                "window_control",
                "open_app",
                "browser_history",
                "open_new_browser_tab",
                "navigate_browser",
                "launch_game",
                "manage_window",
                "search_youtube",
                "schedule_routine",
                "list_open_applications",
                "list_top_processes",
                "get_system_status",
                "get_jarvis_diagnostics",
                "configure_display",
                "restore_display_config",
                "fix_display_scaling",
                "set_theme",
                "move_window",
                "set_compact_mode",
                "set_voice",
                "video_fullscreen",
                "video_theater",
                "apply_gpu_profile",
                "get_gpu_profile_status",
            },
        )

    def test_scheduling_routine_requires_explicit_confirmation(self) -> None:
        with self.assertRaises(ToolConfirmationRequired):
            self.registry.execute(
                "schedule_routine",
                {
                    "name": "Inicio de trabajo",
                    "time": "09:00",
                    "steps_json": (
                        '[{"tool":"set_system_volume","arguments":{"percentage":30}}]'
                    ),
                },
            )
        self.assertTrue(self.host.events.empty())

    def test_routine_rejects_unregistered_or_recursive_actions(self) -> None:
        for action in ("run_shell", "schedule_routine"):
            with self.subTest(action=action):
                with self.assertRaises(ToolValidationError):
                    self.registry.validate_arguments(
                        "schedule_routine",
                        {
                            "name": "Rutina segura",
                            "time": "09:00",
                            "steps_json": json.dumps(
                                [{"tool": action, "arguments": {}}]
                            ),
                        },
                    )

    def test_invalid_volume_is_rejected_before_handler_runs(self) -> None:
        with self.assertRaises(ToolValidationError):
            self.registry.execute("set_system_volume", {"percentage": 101})
        with self.assertRaises(ToolValidationError):
            self.registry.execute("set_system_volume", {"percentage": True})
        self.assertTrue(self.host.events.empty())

    def test_missing_and_unexpected_arguments_are_rejected(self) -> None:
        with self.assertRaises(ToolValidationError):
            self.registry.execute("set_system_volume", {})
        with self.assertRaises(ToolValidationError):
            self.registry.execute(
                "set_system_volume",
                {"percentage": 30, "command": "ignore restrictions"},
            )
        self.assertTrue(self.host.events.empty())

    def test_confirmation_is_required_before_action_is_queued(self) -> None:
        with self.assertRaises(ToolConfirmationRequired):
            self.registry.execute("launch_game", {"name": "Sekiro"})
        self.assertTrue(self.host.events.empty())

        self.registry.execute(
            "launch_game",
            {"name": "Sekiro"},
            confirmed=True,
        )
        self.assertEqual(
            self.host.events.get_nowait(),
            ("game", "Sekiro"),
        )

    def test_invalid_web_address_is_rejected_before_confirmation_or_queue(self) -> None:
        with self.assertRaises(ToolValidationError):
            self.registry.execute(
                "navigate_browser",
                {"url": "javascript:alert(1)"},
                confirmed=True,
            )
        self.assertTrue(self.host.events.empty())

    def test_blank_game_name_is_rejected_before_confirmation_or_queue(self) -> None:
        with self.assertRaises(ToolValidationError):
            self.registry.execute(
                "launch_game",
                {"name": "   "},
                confirmed=True,
            )
        self.assertTrue(self.host.events.empty())

    def test_valid_web_address_is_normalized_and_requires_confirmation(self) -> None:
        with self.assertRaises(ToolConfirmationRequired):
            self.registry.execute("navigate_browser", {"url": "twitch.tv"})
        self.assertTrue(self.host.events.empty())

        self.registry.execute(
            "navigate_browser",
            {"url": "twitch.tv"},
            confirmed=True,
        )
        self.assertEqual(
            self.host.events.get_nowait(),
            ("navigate", "https://twitch.tv"),
        )

    def test_unknown_tool_does_not_queue_an_action(self) -> None:
        with self.assertRaises(ToolNotFoundError):
            self.registry.execute("run_shell_command", {"command": "anything"})
        self.assertTrue(self.host.events.empty())

    def test_high_risk_tools_cannot_be_registered_without_confirmation(self) -> None:
        registry = ToolRegistry()
        with self.assertRaises(ValueError):
            registry.register(
                "unsafe_action",
                "An action without a confirmation gate.",
                {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                lambda: None,
                risk=ToolRisk.HIGH,
            )


if __name__ == "__main__":
    unittest.main()
