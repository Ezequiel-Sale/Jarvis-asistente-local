"""Barra lateral desplazable (2026-11-27): con la ventana baja, las secciones de abajo quedaban fuera."""

import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from jarvis_tools import crear_registro_herramientas
from test_ventana_unica import _Vista


class BarraLateralTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.addCleanup(self.root.destroy)
        with patch("jarvis_core.obtener_preferencia", return_value=None):
            self.vista = _Vista(self.root, crear_registro_herramientas(Mock()))
        self.root.maxsize(4000, 4000)

    def medir(self, alto: int) -> None:
        self.root.geometry(f"1100x{alto}+-4000+-4000")
        self.root.deiconify()
        import time

        fin = time.monotonic() + (self.vista.DEMORA_DESCONGELAR_MS + 250) / 1000  # que se descongele la grilla
        while time.monotonic() < fin:
            self.root.update()
            time.sleep(0.02)
        self.vista._ajustar_sidebar()
        self.root.update()

    def test_scroll_only_when_it_does_not_fit(self) -> None:
        self.medir(1300)
        self.assertFalse(self.vista._sidebar_barra.winfo_ismapped())
        self.assertNotIn("jarvis_scroll", self.vista._sidebar.__dict__)  # la rueda sigue moviendo el chat
        self.medir(500)
        self.assertTrue(self.vista._sidebar_barra.winfo_ismapped())
        self.assertIs(self.vista._sidebar.jarvis_scroll, self.vista._sidebar_lienzo)
        # Desplazable hasta el último botón (Ajustes).
        self.vista._sidebar_lienzo.yview_moveto(1)
        self.root.update()
        ajustes = self.vista._nav_buttons["ajustes"]
        abajo = ajustes.winfo_rooty() + ajustes.winfo_height()
        self.assertLessEqual(abajo, self.vista._sidebar_lienzo.winfo_rooty() + self.vista._sidebar_lienzo.winfo_height())


if __name__ == "__main__":
    unittest.main()
