"""La confirmación «¿Mantener esta configuración de pantallas?» también se responde por voz."""

import queue
import tkinter as tk
import unittest
from unittest.mock import Mock

from jarvis_app import JarvisApp
from jarvis_commands import detectar_decision_monitores


class PhraseTests(unittest.TestCase):
    def test_keep_phrases(self) -> None:
        for texto in ("sí", "Sí.", "dale", "mantener", "mantenla", "déjala así", "está bien", "ok"):
            with self.subTest(texto=texto):
                self.assertIs(detectar_decision_monitores(texto), True)

    def test_revert_phrases(self) -> None:
        for texto in ("no", "No.", "revierte", "revertir", "vuelve atrás", "cancela", "como estaba"):
            with self.subTest(texto=texto):
                self.assertIs(detectar_decision_monitores(texto), False)

    def test_other_speech_is_ignored(self) -> None:
        for texto in ("qué hora es", "pon música", ""):
            with self.subTest(texto=texto):
                self.assertIsNone(detectar_decision_monitores(texto))

    def test_approval_decision_uses_display_detector(self) -> None:
        host = Mock()
        self.assertIs(JarvisApp._approval_decision(host, "display", "Hey Jarvis, mantenla"), True)
        self.assertIs(JarvisApp._approval_decision(host, "display", "revierte"), False)


class DialogTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def _host(self) -> Mock:
        host = Mock()
        host.root = self.root
        host.DISPLAY_KEEP_SECONDS = 15
        host._pending_plan_reply = None
        host._pending_approval_kind = None
        host._plan_dialog = None
        host._resolve_plan_approval = lambda valor: JarvisApp._resolve_plan_approval(host, valor)
        host._recording_for_plan_approval = False
        return host

    def test_dialog_waits_for_voice_and_resolves(self) -> None:
        host = self._host()
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        JarvisApp._show_display_keep_dialog(host, "Desactivé el monitor 2.", reply)
        self.assertIs(host._pending_plan_reply, reply)
        self.assertEqual(host._pending_approval_kind, "display")
        dialogo = host._plan_dialog
        # Lo que hace un «sí» por voz (plan_voice_decision → _resolve_plan_approval).
        JarvisApp._resolve_plan_approval(host, True)
        self.assertTrue(reply.get_nowait())
        self.assertIsNone(host._pending_plan_reply)
        self.assertFalse(dialogo.winfo_exists())

    def test_button_also_clears_pending_approval(self) -> None:
        host = self._host()
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        JarvisApp._show_display_keep_dialog(host, "Cambio.", reply)
        host._plan_dialog.event_generate("<Escape>")
        self.root.update()
        self.assertFalse(reply.get_nowait())
        self.assertIsNone(host._pending_plan_reply)

    def test_other_pending_approval_keeps_buttons_only(self) -> None:
        host = self._host()
        otra: queue.Queue[bool] = queue.Queue(maxsize=1)
        host._pending_plan_reply = otra
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        JarvisApp._show_display_keep_dialog(host, "Cambio.", reply)
        self.assertIs(host._pending_plan_reply, otra)


if __name__ == "__main__":
    unittest.main()
