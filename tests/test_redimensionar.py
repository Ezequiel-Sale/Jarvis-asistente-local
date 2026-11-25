"""La ventana al cambiar de tamaño (2026-11-24, «se va trabando»): solo la sección visible está en la
grilla, el contenido se congela mientras se arrastra el borde y lo pesado se posterga."""

import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jarvis_ui_kit as kit


class RedimensionarTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def _esperar(self, segundos=0.3):
        fin = time.monotonic() + segundos
        while time.monotonic() < fin:
            self.root.update()
            time.sleep(0.01)

    def test_only_the_visible_page_is_in_the_grid(self):
        from jarvis_tools import crear_registro_herramientas
        from test_ventana_unica import _Vista

        import queue

        import jarvis_system

        with patch("jarvis_core.obtener_preferencia", return_value=None):
            vista = _Vista(self.root, crear_registro_herramientas(Mock()))
        vista.events = queue.Queue()
        # Mostrar «juegos» pide la lista: sin leer los discos reales ni guardar su caché.
        for parche in (patch.object(jarvis_system, "juegos_guardados", return_value=None),
                       patch.object(jarvis_system, "listar_juegos", return_value=[])):
            parche.start()
            self.addCleanup(parche.stop)
        self.root.geometry("1180x800+-4000+-4000")  # mapeada (para tener tamaño), fuera de la pantalla
        self.root.deiconify()
        self.root.update()
        vista._show_page("juegos")
        mapeadas = [n for n, pagina in vista._pages.items() if pagina.winfo_manager()]
        self.assertEqual(mapeadas, ["juegos"])
        vista._show_page("chat")
        self.assertEqual([n for n, p in vista._pages.items() if p.winfo_manager()], ["chat"])

        # Arrastrar el borde: la celda del contenido deja de seguir a la ventana hasta que se suelta.
        evento = lambda ancho, alto, widget=self.root: SimpleNamespace(widget=widget, width=ancho, height=alto)
        # Los <Configure> reales de la ventana (mostrar páginas) pueden haberla congelado ya: se parte de cero.
        self.root.update()
        vista._descongelar_contenido()
        vista._tam_raiz = None
        vista._al_redimensionar_raiz(evento(1180, 800))  # la primera vez solo se anota
        self.assertFalse(vista._contenido_congelado)
        vista._al_redimensionar_raiz(evento(1180, 800, widget=vista._pages["chat"]))  # de un hijo: nada
        self.assertFalse(vista._contenido_congelado)
        vista._al_redimensionar_raiz(evento(1100, 800))
        self.assertTrue(vista._contenido_congelado)
        self.assertEqual(int(self.root.grid_columnconfigure(1)["weight"]), 0)
        self._esperar(vista.DEMORA_DESCONGELAR_MS / 1000 + 0.2)
        self.assertFalse(vista._contenido_congelado)
        self.assertEqual(int(self.root.grid_columnconfigure(1)["weight"]), 1)
        self.assertEqual(int(self.root.grid_rowconfigure(0)["minsize"]), 0)

    def test_postpone_runs_once_after_the_last_call(self):
        llamadas = []
        for valor in range(5):
            kit.postergar(self.root, lambda v=valor: llamadas.append(v), 50, clave="x")
        self._esperar(0.25)
        self.assertEqual(llamadas, [4])
        etiqueta = tk.Label(self.root)
        kit.postergar(etiqueta, lambda: llamadas.append("no"), 50)
        etiqueta.destroy()
        self._esperar(0.15)
        self.assertEqual(llamadas, [4])  # el widget ya no existe: no se corre


if __name__ == "__main__":
    unittest.main()
