"""Textos al tamaño del monitor (2026-11-28: en la tele 4K al 300 % se veían diminutos)."""

import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from jarvis_tools import crear_registro_herramientas
from test_ventana_unica import _Vista


class DpiTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.root.tk.call("tk", "scaling", 96 / 72)
        with patch("jarvis_core.obtener_preferencia", return_value=None):
            self.vista = _Vista(self.root, crear_registro_herramientas(Mock()))

    def test_fonts_and_sidebar_follow_the_monitor_dpi(self) -> None:
        self.root.update()
        alto_antes = self.vista.page_title.winfo_reqheight()
        self.vista._aplicar_dpi(288)
        self.root.update()
        self.assertGreater(self.vista.page_title.winfo_reqheight(), alto_antes * 2.5)
        self.assertEqual(int(self.vista._sidebar_contenedor.cget("width")), 750)
        self.assertEqual(int(self.vista.status.cget("wraplength")), 630)
        self.vista._aplicar_dpi(96)
        self.root.update()
        self.assertLessEqual(abs(self.vista.page_title.winfo_reqheight() - alto_antes), 1)
        self.assertEqual(int(self.vista._sidebar_contenedor.cget("width")), 250)


if __name__ == "__main__":
    unittest.main()
