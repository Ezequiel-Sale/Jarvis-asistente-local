"""Vistas nuevas de la barra lateral (jarvis_ui_vistas): se dibujan con datos de ejemplo, sin leer el sistema."""

import queue
import tkinter as tk
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_grabador
import jarvis_ui_vistas as v
from jarvis_commands import detectar_navegacion
from jarvis_ui import JarvisUIMixin


class Host(v.VistasMixin, JarvisUIMixin):
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        self._current_page = "programas"
        self._start_direct_tool_action = Mock()
        self._set_status = Mock()
        self._manejar_grabacion = Mock()


def textos(widget) -> list[str]:
    salida = []
    for hijo in widget.winfo_children():
        try:
            salida.append(hijo.cget("text"))
        except tk.TclError:
            pass
        salida += textos(hijo)
    return salida


class VistasTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.host = Host(self.root)
        self.paginas = {n: tk.Frame(self.root) for n in v.VISTAS}
        self.host._build_vistas(self.paginas)

    def render(self, nombre, datos):
        self.host._render_vista(nombre, datos)
        return " | ".join(textos(self.host._vista_cuerpos[nombre]))

    def test_system_view(self):
        texto = self.render("sistema", {"ajustes": {"night_light": False, "dark_mode": True, "wifi": None, "bluetooth": True},
                                        "red": [("Ethernet", True, True), ("Wi-Fi", True, False)],
                                        "encendido": ["Desactivá el inicio rápido."], "errores": []})
        self.assertIn("Luz nocturna", texto)
        self.assertIn("ACTIVAR", texto)
        self.assertIn("no se pudo leer", texto)
        self.assertIn("Ethernet", texto)
        self.assertIn("CAÍDAS (7 DÍAS)", texto)

    def test_programs_view_buttons_use_the_tools(self):
        texto = self.render("programas", {"actualizaciones": [("Git", "Git.Git", "2.55", "2.56")],
                                          "inicio": [("Discord", True), ("Steam", False)], "error_winget": ""})
        self.assertIn("ACTUALIZACIONES DISPONIBLES (1)", texto)
        self.assertIn("2.55 → 2.56", texto)
        self.assertIn("ARRANCAN CON WINDOWS (1 de 2)", texto)
        self.host._accion_vista("update_programs", {"name": "Git.Git"}, "Actualizar Git")
        self.host._start_direct_tool_action.assert_called_once_with("update_programs", {"name": "Git.Git"}, "Actualizar Git")

    def test_messages_view(self):
        texto = self.render("mensajeria", {"cuentas": ["google"], "correos": [("Ana", "Factura")],
                                           "eventos": [(datetime(2026, 11, 21, 10, 0), "Dentista", False)], "errores": []})
        self.assertIn("conectada", texto)
        self.assertIn("CONECTAR", texto)  # Outlook sin conectar
        self.assertIn("Factura", texto)
        self.assertIn("Dentista", texto)
        self.assertIn("REVISAR", texto)

    def test_tasks_view(self):
        pasos = [jarvis_grabador.Paso("click", "brave", "ButtonControl", "YouTube", etiqueta="Brave")]
        texto = self.render("tareas", {"grabaciones": [("Abrir YouTube", pasos, Path("x.json"))], "copiados": ["hola mundo"]})
        self.assertIn("TAREAS GRABADAS (1)", texto)
        self.assertIn("▶ REPETIR", texto)
        self.assertIn("hola mundo", texto)

    def test_error_and_busy(self):
        self.assertIn("NO PUDE LEER", self.render("sistema", {"error": "algo"}))
        self.host.busy = True
        self.host._accion_vista("windows_setting", {}, "x")
        self.host._start_direct_tool_action.assert_not_called()


class NavegacionTests(unittest.TestCase):
    def test_voice_navigation_to_the_new_views(self):
        self.assertEqual(detectar_navegacion("Jarvis, abrí los programas"), "programas")
        self.assertEqual(detectar_navegacion("mostrame las actualizaciones"), "programas")
        self.assertEqual(detectar_navegacion("andá a sistema"), "sistema")
        self.assertEqual(detectar_navegacion("abrí el calendario"), "mensajeria")
        self.assertEqual(detectar_navegacion("abrí las tareas grabadas"), "tareas")
        self.assertEqual(detectar_navegacion("ve a ajustes"), "ajustes")

    def test_data_readers_survive_errors(self):
        with patch("jarvis_winget.actualizaciones", side_effect=FileNotFoundError("sin winget")), \
                patch("jarvis_ajustes.listar_inicio", return_value=[]):
            datos = v.datos_programas()
        self.assertEqual(datos["error_winget"], "sin winget")
        with patch("jarvis_correo.conectadas", return_value=[]):
            self.assertEqual(v.datos_mensajeria()["cuentas"], [])


if __name__ == "__main__":
    unittest.main()


class BarraTests(unittest.TestCase):
    def test_progress_bar_in_programs_view(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        root.withdraw()
        self.addCleanup(root.destroy)
        host = Host(root)
        host._build_vistas({n: tk.Frame(root) for n in v.VISTAS})
        host._progreso_winget({"paquete": "Git", "fase": "descargando", "fraccion": 0.27, "detalle": "12 MB de 45 MB",
                               "indice": 1, "cantidad": 3})
        fila, texto, barra = host._progresos["Git"]
        self.assertEqual(texto.cget("text"), "[1 de 3] Git — descargando: 12 MB de 45 MB")
        self.assertEqual(barra["value"], 27)
        host._progreso_winget({"paquete": "Git", "fase": "instalando", "fraccion": None, "detalle": "Instalando…",
                               "indice": 1, "cantidad": 3})
        self.assertEqual(str(barra.cget("mode")), "indeterminate")
        host._progreso_winget({"paquete": "Git", "fase": "terminado", "fraccion": 1.0, "detalle": "Listo",
                               "indice": 1, "cantidad": 3})
        self.assertEqual(barra["value"], 100)
        host._quitar_progreso("Git")
        self.assertNotIn("Git", host._progresos)


class PlacaVistaTests(unittest.TestCase):
    def test_adapter_buttons(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        root.withdraw()
        self.addCleanup(root.destroy)
        host = Host(root)
        host._build_vistas({n: tk.Frame(root) for n in v.VISTAS})
        host._render_vista("sistema", {"ajustes": {}, "red": [("Ethernet", True, True), ("Wi-Fi", False, False)],
                                       "encendido": [], "errores": []})
        texto = " | ".join(textos(host._vista_cuerpos["sistema"]))
        self.assertIn("DESHABILITAR", texto)
        self.assertIn("HABILITAR", texto)
        self.assertIn("deshabilitada", texto)
