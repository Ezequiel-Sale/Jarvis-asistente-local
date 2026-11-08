"""La confirmación «¿Mantener esta configuración de pantallas?» también se responde por voz."""

import queue
import types
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from jarvis_app import JarvisApp
from jarvis_commands import detectar_decision_monitores
from jarvis_tools import crear_registro_herramientas
from test_ventana_unica import _Vista


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
    """La confirmación es una tarjeta dentro del chat (interfaz real, oculta)."""

    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with patch("jarvis_core.obtener_preferencia", return_value=None):
            self.vista = _Vista(self.root, crear_registro_herramientas(Mock()))
        v = self.vista
        v.DISPLAY_KEEP_SECONDS = 15
        v._pending_plan_reply = None
        v._pending_approval_kind = None
        v._pending_power_action = None
        v._plan_dialog = None
        v._recording_for_plan_approval = False
        for nombre in ("_show_display_keep_dialog", "_resolve_plan_approval"):
            setattr(v, nombre, types.MethodType(getattr(JarvisApp, nombre), v))

    def test_dialog_waits_for_voice_and_resolves(self) -> None:
        v = self.vista
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        v._show_display_keep_dialog("Desactivé el monitor 2.", reply)
        self.assertIs(v._pending_plan_reply, reply)
        self.assertEqual(v._pending_approval_kind, "display")
        tarjeta = v._plan_dialog
        self.assertIsNot(tarjeta.winfo_toplevel(), None)
        self.assertIs(tarjeta.winfo_toplevel(), self.root)  # dentro de la ventana principal
        # Lo que hace un «sí» por voz (plan_voice_decision → _resolve_plan_approval).
        v._resolve_plan_approval(True)
        self.assertTrue(reply.get_nowait())
        self.assertIsNone(v._pending_plan_reply)
        self.assertTrue(tarjeta.winfo_exists())  # queda en el historial con el resultado
        self.assertFalse(any(isinstance(w, tk.Button) for w in _descendientes(tarjeta)))

    def test_button_also_clears_pending_approval(self) -> None:
        v = self.vista
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        v._show_display_keep_dialog("Cambio.", reply)
        v._plan_reject_button.invoke()  # REVERTIR
        self.root.update()
        self.assertFalse(reply.get_nowait())
        self.assertIsNone(v._pending_plan_reply)

    def test_other_pending_approval_keeps_buttons_only(self) -> None:
        v = self.vista
        otra: queue.Queue[bool] = queue.Queue(maxsize=1)
        v._pending_plan_reply = otra
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        v._show_display_keep_dialog("Cambio.", reply)
        self.assertIs(v._pending_plan_reply, otra)


def _descendientes(widget):
    pendientes = [widget]
    while pendientes:
        w = pendientes.pop()
        yield w
        pendientes.extend(w.winfo_children())


if __name__ == "__main__":
    unittest.main()
