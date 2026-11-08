"""Fase 3: aviso flotante (con Jarvis en la bandeja) y modo compacto (Tk real, oculta)."""

import queue
import time
import tkinter as tk
import types
import unittest
from unittest.mock import Mock, patch

import jarvis_floating as f
from jarvis_app import JarvisApp
from jarvis_commands import detectar_comando_compacto
from jarvis_tools import crear_registro_herramientas
from test_ventana_unica import _Vista


def _esperar(root, segundos=0.3):
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        root.update()
        time.sleep(0.02)


def _textos(ventana):
    pendientes, textos = [ventana], []
    while pendientes:
        w = pendientes.pop()
        pendientes.extend(w.winfo_children())
        if isinstance(w, (tk.Label, tk.Button)):
            textos.append(str(w.cget("text")))
    return " | ".join(textos)


class HelperTests(unittest.TestCase):
    def test_trim_and_reading_time(self) -> None:
        self.assertEqual(f.recortar("hola   mundo"), "hola mundo")
        self.assertTrue(f.recortar("x" * 500).endswith("…"))
        self.assertEqual(f.segundos_visible("hola"), 4 + 1 / 15)
        self.assertEqual(f.segundos_visible("palabra " * 1000), 14.0)

    def test_compact_phrases(self) -> None:
        self.assertEqual(detectar_comando_compacto("Modo compacto."), ("set_compact_mode", {"enabled": True}))
        self.assertEqual(detectar_comando_compacto("ponte en modo mini"), ("set_compact_mode", {"enabled": True}))
        self.assertEqual(detectar_comando_compacto("vuelve a la ventana completa"),
                         ("set_compact_mode", {"enabled": False}))
        self.assertIsNone(detectar_comando_compacto("modo concentración"))


class TkTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_floating_notice_shows_and_hides(self) -> None:
        abrir = Mock()
        aviso = f.AvisoFlotante(self.root, abrir)
        aviso.usuario("Manda Brave a la TV")
        aviso.respuesta("Moví 1 ventana.")
        _esperar(self.root)
        self.assertTrue(aviso.visible())
        self.assertIn("Moví 1 ventana.", _textos(aviso.ventana))
        self.assertTrue(aviso.ventana.overrideredirect())
        self.assertTrue(aviso.ventana.attributes("-topmost"))
        aviso.ocultar()
        _esperar(self.root)
        self.assertFalse(aviso.visible())

    def test_question_stays_until_answered(self) -> None:
        si, no = Mock(), Mock()
        aviso = f.AvisoFlotante(self.root, Mock())
        aviso.pregunta("¿Mantengo esta configuración?", si, no)
        _esperar(self.root)
        self.assertIsNone(aviso._ocultar_id)  # no se oculta sola
        boton_si = [w for w in aviso.ventana.winfo_children()[0].winfo_children()[-1].winfo_children()
                    if isinstance(w, tk.Button)][0]
        boton_si.invoke()
        si.assert_called_once()
        aviso.quitar_pregunta("✓ MANTENER")
        self.assertIn("✓ MANTENER", _textos(aviso.ventana))
        self.assertIsNotNone(aviso._ocultar_id)

    def _vista(self):
        with patch("jarvis_core.obtener_preferencia", return_value=None):
            v = _Vista(self.root, crear_registro_herramientas(Mock()))
        v.floating = f.AvisoFlotante(self.root, Mock())
        v._floating_enabled = True
        return v

    def test_in_tray_messages_and_confirmations_go_to_the_notice(self) -> None:
        v = self._vista()
        v.root.withdraw()
        v._add_message("assistant", "**Listo**, volumen al 30 %.")
        _esperar(self.root)
        self.assertIn("Listo, volumen al 30 %.", _textos(v.floating.ventana))
        v._pending_plan_reply, v._pending_approval_kind, v._plan_dialog = None, None, None
        v._pending_power_action, v._recording_for_plan_approval = None, False
        v._APPROVAL_PHRASES = JarvisApp._APPROVAL_PHRASES
        for nombre in ("_show_power_confirmation", "_resolve_plan_approval", "_approval_hint",
                       "_set_plan_voice_controls"):
            setattr(v, nombre, types.MethodType(getattr(JarvisApp, nombre), v))
        reply: queue.Queue[bool] = queue.Queue(1)
        v._show_power_confirmation("shutdown", reply)
        _esperar(self.root)
        self.assertEqual(self.root.state(), "withdrawn")  # no se abrió la ventana
        self.assertIn("¿Apago el equipo?", _textos(v.floating.ventana))
        v._resolve_plan_approval(False)
        self.assertFalse(reply.get_nowait())
        self.assertIsNone(v.floating._pregunta)

    def test_window_visible_means_no_notice(self) -> None:
        v = self._vista()
        v.root.deiconify()
        _esperar(self.root, 0.1)
        v._add_message("assistant", "Hola")
        self.assertIsNone(v.floating.ventana)
        v.root.withdraw()

    def test_compact_mode_hides_window_and_restores(self) -> None:
        v = self._vista()
        v._compact_position = lambda w, h: (50, 50)
        v._save_compact_position = lambda x, y: None
        v._show_from_tray = lambda: (v._close_compact_window(), v.root.deiconify())
        v.root.deiconify()
        v._enter_compact_mode()
        _esperar(self.root)
        self.assertTrue(v._compact_mode_active())
        self.assertEqual(self.root.state(), "withdrawn")
        self.assertTrue(v.compact_canvas.find_withtag("orb"))  # el orbe también se dibuja ahí
        v._exit_compact_mode()
        _esperar(self.root, 0.1)
        self.assertFalse(v._compact_mode_active())
        self.assertNotEqual(self.root.state(), "withdrawn")
        v.root.withdraw()


if __name__ == "__main__":
    unittest.main()
