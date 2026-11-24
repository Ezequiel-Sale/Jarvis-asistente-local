"""Página 📺 TELE rediseñada y adaptable al ancho (2026-11-23)."""

import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import jarvis_theme
import jarvis_ui_tele as tele
from jarvis_tools import crear_registro_herramientas
from test_ventana_unica import _Vista


class CalculosTests(unittest.TestCase):
    def test_columns_for_width(self):
        self.assertEqual(tele.columnas_para(520, 170), 3)
        self.assertEqual(tele.columnas_para(100, 170), 1)
        self.assertEqual(tele.columnas_para(5000, 170), 8)

    def test_app_search(self):
        lista = [("netflix", "Netflix"), ("youtube", "YouTube"), ("ytmusic", "YouTube Music")]
        self.assertEqual(tele.filtrar_apps(lista, "tube"), lista[1:])
        self.assertEqual(tele.filtrar_apps(lista, ""), lista)
        self.assertEqual(tele.filtrar_apps(lista, "spotify"), [])

    def test_chips_flow_to_next_row(self):
        self.assertEqual(tele.repartir([100, 100, 100], 250), [0, 0, 1])
        self.assertEqual(tele.repartir([300, 50], 200), [0, 1])  # una sola más ancha que todo: igual va


class PaginaTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.addCleanup(self.root.destroy)
        self.addCleanup(jarvis_theme.activar, "cian")
        with patch("jarvis_core.obtener_preferencia", return_value=None):
            self.vista = _Vista(self.root, crear_registro_herramientas(Mock()))
        self.vista._tv_button = Mock()
        self.vista._show_page("tele")

    def esperar(self, segundos=0.4):
        fin = time.monotonic() + segundos
        while time.monotonic() < fin:
            self.root.update()
            time.sleep(0.02)

    def botones(self):
        salida, pendientes = [], [self.vista._pages["tele"]]
        while pendientes:
            w = pendientes.pop()
            pendientes.extend(w.winfo_children())
            if isinstance(w, tk.Button):
                salida.append(w)
        return salida

    def test_buttons_go_to_the_tv(self):
        prender = next(b for b in self.botones() if "PRENDER" in b.cget("text"))
        prender.invoke()
        hdmi = next(b for b in self.botones() if b.cget("text") == "HDMI 3")
        hdmi.invoke()
        acciones = [c.args[0] for c in self.vista._tv_button.call_args_list]
        self.assertEqual(acciones, ["power_on", "hdmi3"])

    def test_status_and_learned_apps(self):
        self.vista._paint_tv_status("TV de Habitación", "Sin conexión", "Puede estar apagada.", "error")
        self.assertEqual(self.vista.tv_page_name.cget("text"), "TV de Habitación")
        self.assertEqual(self.vista.tv_page_pill.cget("text"), "SIN CONEXIÓN")
        self.vista._render_tv_apps([("netflix", "Netflix"), ("zeus", "Zeus")])
        self.esperar(0.1)
        textos = [w.cget("text") for w in self.vista._tv_apps_grid.winfo_children()[0].winfo_children()]
        self.assertIn("Netflix", textos)

    def test_search_filters_the_grid(self):
        self.vista._render_tv_apps([("netflix", "Netflix"), ("zeus", "Zeus"), ("max", "Max")])
        self.vista._tv_buscar_app.set("ze")
        self.esperar(0.1)
        tarjetas = self.vista._tv_apps_grid.winfo_children()
        self.assertEqual(len(tarjetas), 1)
        self.vista._tv_buscar_app.set("nada que coincida")
        self.esperar(0.1)
        self.assertEqual(self.vista._tv_apps_grid.winfo_children()[0].cget("text"), "Ninguna app con ese nombre.")

    def test_two_columns_wide_one_column_narrow(self):
        def columnas_de_la_derecha():
            return "dos" if self.vista._tv_columnas == 2 else "una"

        self.root.geometry("1500x900")
        self.esperar()
        self.assertEqual(columnas_de_la_derecha(), "dos")
        self.root.geometry("950x900")
        self.esperar()
        self.assertEqual(columnas_de_la_derecha(), "una")


if __name__ == "__main__":
    unittest.main()
