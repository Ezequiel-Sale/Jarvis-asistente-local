"""Ajustes agrupados y piezas comunes de las vistas (2026-11-24). Sin base de datos: preferencias vacías."""

import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import jarvis_ui_ajustes as a
import jarvis_ui_kit as kit


def _dentro(widget, marco) -> bool:
    while widget is not None:
        if widget is marco:
            return True
        widget = widget.master
    return False


class GruposTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        from jarvis_tools import crear_registro_herramientas
        from test_ventana_unica import _Vista

        with patch("jarvis_core.obtener_preferencia", return_value=None):
            self.vista = _Vista(self.root, crear_registro_herramientas(Mock()))
        self.grupos = self.vista._ajustes_grupos

    def test_every_setting_is_in_its_group(self):
        v, g = self.vista, self.grupos
        self.assertEqual(list(g), [ident for ident, *_ in a.GRUPOS])
        for ident, marco in g.items():
            self.assertTrue(marco.winfo_children(), ident)  # ningún grupo vacío
        esperados = {"voz": (v.audio_toggle_button, v.wakeword_button, v.interrupt_button, v.speaker_button),
                     "gestos": (v.gestures_button, v.gesture_sensitivity_scale),
                     "pantallas": (v.monitor_canvas, v.taskbar_button),
                     "apariencia": (v._theme_buttons["nebula"], v.floating_button, v.animations_button),
                     "juego": (v.gpu_profile_button, v.game_quiet_button),
                     "conexiones": (v.tv_settings_status, v.search_info),
                     "inicio": (v.autostart_button, v.morning_brief_button)}
        for ident, widgets in esperados.items():
            for widget in widgets:
                self.assertTrue(_dentro(widget, g[ident]), f"{widget} no está en {ident}")

    def test_only_one_group_is_shown_and_search_filters(self):
        v = self.vista
        self.assertEqual(v._ajustes_actual, "voz")
        self.assertTrue(self.grupos["voz"].winfo_manager())
        self.assertFalse(self.grupos["gestos"].winfo_manager())
        a.mostrar(v, "conexiones")
        self.assertFalse(self.grupos["voz"].winfo_manager())
        self.assertEqual(v._ajustes_titulo.cget("text"), "Conexiones")
        v._ajustes_busqueda.set("cámara")
        visibles = [i for i, (boton, *_r) in v._ajustes_nav.items() if boton.jarvis_visible]
        self.assertEqual(visibles, ["gestos"])
        self.assertEqual(v._ajustes_actual, "gestos")  # el elegido ya no estaba: muestra el primero que queda
        v._ajustes_busqueda.set("undervolt")
        self.assertEqual(v._ajustes_actual, "juego")
        v._ajustes_busqueda.set("algo que no existe")
        self.assertFalse(any(boton.jarvis_visible for boton, *_r in v._ajustes_nav.values()))
        v._ajustes_busqueda.set("")
        self.assertTrue(all(boton.jarvis_visible for boton, *_r in v._ajustes_nav.values()))

    def test_narrow_window_moves_groups_on_top(self):
        v = self.vista
        a._acomodar(v, 700)
        self.assertEqual(v._ajustes_columnas_nav, 3)
        self.assertEqual(int(v._ajustes_contenido.grid_info()["row"]), 1)
        a._acomodar(v, 500)
        self.assertEqual(v._ajustes_columnas_nav, 2)
        a._acomodar(v, 1100)
        self.assertEqual(v._ajustes_columnas_nav, 1)
        self.assertEqual(int(v._ajustes_contenido.grid_info()["column"]), 1)


class KitTests(unittest.TestCase):
    def test_grid_columns_and_switch(self):
        self.assertEqual(kit.Rejilla.columnas_para(1000, 200, 14, 4), 4)
        self.assertEqual(kit.Rejilla.columnas_para(500, 200, 14, 4), 2)
        self.assertEqual(kit.Rejilla.columnas_para(100, 200, 14, 4), 1)
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        root.withdraw()
        self.addCleanup(root.destroy)
        llamado = Mock()
        prendido = kit.interruptor(root, True, llamado)
        prendido.jarvis_alternar()
        llamado.assert_called_once()
        self.assertIsNone(kit.interruptor(root, None, llamado).jarvis_alternar)  # desconocido: no hace nada
        rejilla = kit.Rejilla(root, 200, max_columnas=3)
        for _ in range(5):
            rejilla.agregar(tk.Frame(rejilla, width=10, height=10))
        rejilla._al_cambiar_ancho(type("E", (), {"width": 700})())
        self.assertEqual(rejilla.columnas, 3)
        self.assertEqual([(int(h.grid_info()["row"]), int(h.grid_info()["column"])) for h in rejilla._hijos],
                         [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)])


if __name__ == "__main__":
    unittest.main()
