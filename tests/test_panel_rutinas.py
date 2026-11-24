"""Panel de rutinas rediseñado (2026-11-23): tarjetas, filtros, buscador y columnas según el ancho.
Usa una base de datos temporal, nunca la del usuario."""

import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_core as core
import jarvis_routines
import jarvis_routines_panel as panel
from jarvis_tools import crear_registro_herramientas


class FuncionesTests(unittest.TestCase):
    def test_trigger_kind_and_columns(self):
        self.assertEqual(panel.tipo_disparador("hora"), "hora")
        self.assertEqual(panel.tipo_disparador("frase"), "frase")
        self.assertEqual(panel.tipo_disparador("auriculares_conectados"), "evento")
        self.assertEqual(panel.columnas_para(1200), 3)
        self.assertEqual(panel.columnas_para(600), 1)
        self.assertEqual(panel.columnas_para(99999), 4)


class PanelTests(unittest.TestCase):
    def setUp(self) -> None:
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        for parche in (patch.object(core, "DB_PATH", Path(carpeta.name) / "prueba.db"),
                       patch.object(core, "_database_initialized", False)):
            parche.start()
            self.addCleanup(parche.stop)
        core.inicializar_base_datos()
        self.registry = crear_registro_herramientas(Mock())
        volumen = json.dumps([{"tool": "set_system_volume", "arguments": {"percentage": 30}}])
        salida = json.dumps([{"tool": "set_output_device", "arguments": {"device": "headphones"}}])
        self.pausada = jarvis_routines.crear_rutina("buenos_dias", "07:00", "", volumen, self.registry)
        jarvis_routines.cambiar_estado_rutina(self.pausada, False)
        jarvis_routines.crear_rutina("Al conectar auriculares", "", "", salida, self.registry,
                                     trigger="auriculares_conectados", automatic=True)
        jarvis_routines.crear_rutina("abrir navegador", "", "", json.dumps(
            [{"tool": "open_new_browser_tab", "arguments": {}}]), self.registry, trigger="frase",
            phrases=["abrí el navegador"])
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.addCleanup(self.root.destroy)
        self.root.geometry("1400x900")
        self.panel = panel.RoutinesPanel(self.root, self.registry, incrustado=True)
        self.esperar()

    def esperar(self, segundos=0.3):
        fin = time.monotonic() + segundos
        while time.monotonic() < fin:
            self.root.update()
            time.sleep(0.02)

    def nombres(self) -> list[str]:
        salida = []
        for tarjeta in self.panel._tarjetas:
            pendientes = [tarjeta]
            while pendientes:
                w = pendientes.pop(0)
                pendientes.extend(w.winfo_children())
                if isinstance(w, tk.Label) and str(w.cget("font")).endswith("12 bold"):
                    salida.append(w.cget("text"))
                    break
        return salida

    def test_one_card_per_routine_with_full_text(self):
        self.assertEqual(len(self.panel._tarjetas), 3)
        self.assertIn("buenos_dias", self.nombres())
        textos = []
        pendientes = list(self.panel._tarjetas)
        while pendientes:
            w = pendientes.pop()
            pendientes.extend(w.winfo_children())
            if isinstance(w, tk.Label):
                textos.append(str(w.cget("text")))
        self.assertIn("Al decir «abrí el navegador»", textos)  # entero, sin cortar
        self.assertIn("PAUSADA", textos)

    def test_filters_and_counts(self):
        self.assertEqual(self.panel._botones_filtro["pausadas"].cget("text"), "Pausadas  1")
        self.panel._elegir_filtro("auto")
        self.assertEqual(self.nombres(), ["Al conectar auriculares"])
        self.panel._elegir_filtro("pausadas")
        self.assertEqual(self.nombres(), ["buenos_dias"])
        self.panel._elegir_filtro("preguntan")
        self.assertEqual(sorted(self.nombres()), ["abrir navegador", "buenos_dias"])

    def test_search(self):
        self.panel.busqueda.set("navegador")
        self.assertEqual(self.nombres(), ["abrir navegador"])
        self.panel.busqueda.set("nada")
        self.assertEqual(self.panel._tarjetas, [])

    def test_pause_button_still_works(self):
        rutina = next(r for r in self.panel._rutinas if r.identifier == self.pausada)
        self.panel._toggle_active(rutina)
        self.assertTrue(next(r for r in self.panel._rutinas if r.identifier == self.pausada).active)

    def test_columns_follow_the_width(self):
        self.panel._acomodar(1300)
        self.assertEqual(self.panel._columnas, 3)
        self.panel._acomodar(700)
        self.assertEqual(self.panel._columnas, 1)
        self.assertEqual({int(t.grid_info()["column"]) for t in self.panel._tarjetas}, {0})


if __name__ == "__main__":
    unittest.main()
