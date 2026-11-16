"""Acciones al abrir un juego (sin lanzar juegos de verdad ni tocar la base real)."""

import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_acciones_juego as aj
import jarvis_routine_form as form
from jarvis_tools import crear_registro_herramientas

ESPERAR = {"tool": "routine_wait", "arguments": {"seconds": 10}}
JUGAR = {"tool": "press_window_button", "arguments": {"program": "Netmarble Launcher", "button": "Jugar"}}


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prefs: dict[str, str] = {}
        self.obtener, self.guardar = self.prefs.get, self.prefs.__setitem__
        self.registry = crear_registro_herramientas(Mock())

    def test_save_load_and_clear_by_name(self) -> None:
        aj.guardar(self.obtener, self.guardar, "7dsgb", [ESPERAR, JUGAR])
        self.assertEqual(aj.pasos_de(self.obtener, "7DSGB"), [ESPERAR, JUGAR])  # sin importar mayúsculas
        self.assertEqual(aj.pasos_de(self.obtener, "Hades"), [])
        aj.guardar(self.obtener, self.guardar, "7dsgb", [])
        self.assertEqual(aj.pasos_de(self.obtener, "7dsgb"), [])

    def test_validation(self) -> None:
        self.assertEqual(aj.validar([ESPERAR, JUGAR], self.registry), [ESPERAR, JUGAR])
        for paso in ({"tool": "launch_game", "arguments": {"name": "Hades"}},          # se encadenaría
                     {"tool": "power_control", "arguments": {"action": "shutdown"}},
                     {"tool": "screen_click", "arguments": {}}):                       # no es de rutinas
            with self.subTest(paso=paso["tool"]), self.assertRaises(ValueError):
                aj.validar([paso], self.registry)
        with self.assertRaises(Exception):
            aj.validar([{"tool": "routine_wait", "arguments": {"seconds": 0}}], self.registry)

    def test_runs_in_order_and_stops_at_first_failure(self) -> None:
        hechos = []

        def hacer(herramienta, argumentos):
            hechos.append(herramienta)
            if herramienta == "press_window_button":
                raise RuntimeError("No encontré el botón")
            return "ok"

        resultados, error = aj.ejecutar([ESPERAR, JUGAR, ESPERAR], hacer, {"press_window_button": "Pulsar"}.get)
        self.assertEqual((hechos, resultados), (["routine_wait", "press_window_button"], ["ok"]))
        self.assertEqual(error, "«Pulsar» falló: No encontré el botón")


class LaunchTests(unittest.TestCase):
    def test_launch_then_actions_in_background(self) -> None:
        import jarvis_app_herramientas as h
        from jarvis_app import JarvisApp

        host = Mock()
        host._run_game_actions.return_value = 2
        with patch.object(h, "localizar_ejecutable_juego", return_value=("7dsgb", Path("C:/x/7dsgc.exe"))), \
                patch.object(h, "iniciar_ejecutable_juego") as iniciar:
            texto = JarvisApp._tool_launch_game(host, "7ds")
        iniciar.assert_called_once()
        host._run_game_actions.assert_called_once_with("7dsgb")
        self.assertEqual(texto, "Inicié 7dsgb con 7dsgc.exe. Después hago sus 2 acciones.")


class EditorTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk no disponible: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        for nombre in ("showerror", "showwarning", "showinfo"):
            patcher = patch.object(form.messagebox, nombre, side_effect=AssertionError)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_game_mode_only_edits_actions(self) -> None:
        guardados, listo = [], Mock()
        ventana = form.RoutineForm(self.root, crear_registro_herramientas(Mock()), listo, juego="7dsgb",
                                   pasos=[ESPERAR], al_guardar=guardados.append)
        nombres = {f[3] for f in ventana.acciones}
        self.assertNotIn("launch_game", nombres)
        self.assertNotIn("power_control", nombres)
        self.assertIn("press_window_button", nombres)
        self.assertEqual(ventana.lista.size(), 1)
        ventana.busqueda.set("boton")
        ventana.arbol.selection_set("accion:press_window_button")
        self.root.update()
        ventana.campos["program"].set("Netmarble Launcher")
        ventana.campos["button"].set("Jugar")
        ventana._agregar()
        ventana._guardar()
        self.assertEqual(guardados, [[ESPERAR, JUGAR]])
        listo.assert_called_once()


if __name__ == "__main__":
    unittest.main()
