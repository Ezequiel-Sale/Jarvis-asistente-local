"""Ventana única: barra lateral con secciones Chat, Rutinas y Ajustes (Tk real, oculta)."""

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_core as core
import jarvis_routine_form as form
from jarvis_player import AudioPlayerMixin
from jarvis_routines_panel import RoutinesPanel
from jarvis_tools import crear_registro_herramientas
from jarvis_ui import JarvisUIMixin


class _Vista(JarvisUIMixin, AudioPlayerMixin):
    """Solo la interfaz: el resto de JarvisApp se sustituye por funciones vacías."""

    def __init__(self, root: tk.Tk, registry) -> None:
        self.root = root
        self.registry = registry
        self._orb_mode, self._orb_phase, self._orb_pulse_started = "idle", 0.0, 0.0
        self.recorder, self.busy, self._chat_following, self._closing = None, False, True, False
        self._routines_panel = None
        self._configure_styles()
        self._build_layout()

    def __getattr__(self, nombre):
        if nombre.startswith("_"):
            return lambda *a, **k: None
        raise AttributeError(nombre)

    def _load_routines_page(self) -> None:
        if self._routines_panel is None:
            self._routines_panel = RoutinesPanel(self._pages["rutinas"], self.registry, incrustado=True)


class SingleWindowTests(unittest.TestCase):
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
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.vista = _Vista(self.root, crear_registro_herramientas(Mock()))

    def test_chat_is_part_of_the_main_window(self) -> None:
        self.assertIs(self.vista.chat_window, self.root)
        self.assertEqual(self.vista._current_page, "chat")
        self.assertEqual(self.vista.page_title.cget("text"), "CHAT")

    def test_sections_switch_without_new_windows(self) -> None:
        antes = set(self.root.winfo_children())
        self.vista._show_page("rutinas")
        self.assertEqual(self.vista.page_title.cget("text"), "RUTINAS Y AUTOMATIZACIONES")
        self.assertIsInstance(self.vista._routines_panel.window, tk.Frame)
        self.vista._show_page("ajustes")
        nuevas = set(self.root.winfo_children()) - antes
        self.assertFalse([w for w in nuevas if isinstance(w, tk.Toplevel)])
        # Los interruptores viven en Ajustes.
        for boton in ("audio_toggle_button", "wakeword_button", "autostart_button"):
            self.assertTrue(str(getattr(self.vista, boton)).startswith(str(self.vista._pages["ajustes"])))

    def test_routine_form_covers_the_section_and_goes_back(self) -> None:
        self.vista._show_page("rutinas")
        panel = self.vista._routines_panel
        with patch.object(form.RoutineForm, "_actualizar_lista"):
            formulario = form.RoutineForm(panel.window, panel.registry, Mock(), incrustado=True)
        self.assertIsInstance(formulario.window, tk.Frame)
        self.assertEqual(formulario.window.place_info().get("relwidth"), "1")
        formulario.window.destroy()  # «← VOLVER»
        self.assertTrue(panel.is_open())


if __name__ == "__main__":
    unittest.main()
