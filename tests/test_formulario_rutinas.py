"""Comandos personalizados por frase y formulario de rutinas (Tk real, ventana oculta)."""

import json
import os
import queue
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_core as core
import jarvis_routine_form as form
import jarvis_routines as routines
import jarvis_system
from jarvis_app import JarvisApp
from jarvis_tools import crear_registro_herramientas

PAUSA = json.dumps([{"tool": "media_control", "arguments": {"action": "pause"}}])


class _DbTestCase(unittest.TestCase):
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
        self.registry = crear_registro_herramientas(Mock())


class PhraseRoutineTests(_DbTestCase):
    def test_found_by_phrase_with_any_wording(self) -> None:
        routines.crear_rutina(
            "Modo juego", "", "", PAUSA, self.registry, trigger="frase", phrases=["Modo juego", "a jugar"]
        )
        for texto in ("modo juego", "Hey Jarvis, modo juego.", "MODO JUEGO", "jarvis a jugar"):
            with self.subTest(texto=texto):
                rutina = routines.buscar_rutina_por_frase(texto)
                self.assertIsNotNone(rutina)
                self.assertEqual(rutina.phrases, ("Modo juego", "a jugar"))
        self.assertIsNone(routines.buscar_rutina_por_frase("modo juego ahora"))  # solo frase exacta

    def test_paused_routine_is_not_triggered(self) -> None:
        identificador = routines.crear_rutina(
            "Modo juego", "", "", PAUSA, self.registry, trigger="frase", phrases=["modo juego"]
        )
        routines.cambiar_estado_rutina(identificador, False)
        self.assertIsNone(routines.buscar_rutina_por_frase("modo juego"))

    def test_phrase_validation(self) -> None:
        with self.assertRaises(ValueError):  # sin frase
            routines.crear_rutina("Sin frase", "", "", PAUSA, self.registry, trigger="frase")
        routines.crear_rutina("Uno", "", "", PAUSA, self.registry, trigger="frase", phrases=["modo cine"])
        with self.assertRaises(ValueError):  # repetida en otra rutina
            routines.crear_rutina("Dos", "", "", PAUSA, self.registry, trigger="frase", phrases=["Modo cine"])

    def test_update_keeps_its_own_phrase(self) -> None:
        identificador = routines.crear_rutina(
            "Cine", "", "", PAUSA, self.registry, trigger="frase", phrases=["modo cine"]
        )
        routines.actualizar_rutina(
            identificador, "Cine", "", "", PAUSA, self.registry, trigger="frase",
            phrases=["modo cine", "peli"],
        )
        info = next(r for r in routines.listar_automatizaciones() if r.identifier == identificador)
        self.assertEqual(info.phrases, ("modo cine", "peli"))
        self.assertIn("«peli»", routines.describir_disparador(info.trigger, "", info.days, info.phrases))

    def test_up_to_ten_steps(self) -> None:
        pasos = json.dumps([{"tool": "media_control", "arguments": {"action": "next"}}] * 10)
        routines.crear_rutina("Diez", "08:00", "1", pasos, self.registry)
        with self.assertRaises(ValueError):
            routines.crear_rutina("Once", "08:00", "1", json.dumps(json.loads(pasos) * 2), self.registry)


class OpenProgramTests(unittest.TestCase):
    def test_opens_existing_path_only(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as archivo:
            ruta = archivo.name
        self.addCleanup(os.remove, ruta)
        with patch.object(jarvis_system.os, "startfile", create=True) as abrir:
            self.assertIn("Abrí", jarvis_system.abrir_programa(ruta))
            abrir.assert_called_once()
        with self.assertRaises(FileNotFoundError):
            jarvis_system.abrir_programa(r"C:\no\existe\programa.exe")


class PhraseExecutionTests(unittest.TestCase):
    def _host(self, aprueba_energia: bool = True) -> Mock:
        host = Mock()
        host.tool_registry = Mock()
        host.tool_registry.execute.side_effect = lambda tool, args, **_k: f"{tool} hecho."
        host.tool_registry.get.return_value.display_name = "Acción"
        host.events = queue.Queue()
        host.audio_enabled = False
        host._request_power_confirmation.return_value = aprueba_energia
        return host

    def _rutina(self, *pasos) -> routines.Routine:
        return routines.Routine(1, "Modo noche", "", (1,), tuple(routines.RoutineStep(t, a) for t, a in pasos))

    @patch("jarvis_app.core.guardar_turno")
    def test_runs_all_steps_directly(self, _guardar: Mock) -> None:
        host = self._host()
        rutina = self._rutina(("media_control", {"action": "pause"}), ("lock_computer", {}))
        JarvisApp._phrase_routine_worker(host, rutina, "modo noche", "c1", None)
        llamadas = host.tool_registry.execute.call_args_list
        self.assertEqual([c.args[0] for c in llamadas], ["media_control", "lock_computer"])
        self.assertTrue(all(c.kwargs["confirmed"] for c in llamadas))
        evento = host.events.get_nowait()
        self.assertEqual(evento[0], "answer")
        self.assertIn("Modo noche:", evento[1])

    @patch("jarvis_app.core.guardar_turno")
    def test_power_step_still_asks(self, _guardar: Mock) -> None:
        host = self._host(aprueba_energia=False)
        rutina = self._rutina(("media_control", {"action": "pause"}), ("power_control", {"action": "shutdown"}))
        JarvisApp._phrase_routine_worker(host, rutina, "a dormir", "c1", None)
        host._request_power_confirmation.assert_called_once_with("shutdown")
        self.assertEqual([c.args[0] for c in host.tool_registry.execute.call_args_list], ["media_control"])


class FormHelperTests(_DbTestCase):
    def test_available_tools_and_conversion(self) -> None:
        nombres = {n for _, n in form.herramientas_disponibles(self.registry)}
        self.assertIn("open_program", nombres)
        self.assertIn("media_control", nombres)
        self.assertNotIn("list_open_applications", nombres)
        esquema = self.registry.get("set_app_volume").parameters
        self.assertEqual(
            form.argumentos_desde_campos(esquema, {"app": " spotify ", "percentage": "30", "mute": False}),
            {"app": "spotify", "percentage": 30, "mute": False},
        )
        with self.assertRaises(ValueError):
            form.argumentos_desde_campos(esquema, {"app": "", "percentage": "30"})


class FormWindowTests(_DbTestCase):
    def setUp(self) -> None:
        super().setUp()
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

    def test_create_phrase_command_with_the_form(self) -> None:
        guardado = Mock()
        ventana = form.RoutineForm(self.root, self.registry, guardado)
        ventana.nombre.set("Modo juego")
        ventana.disparador.set("frase")
        ventana.frases.set("modo juego, a jugar")
        ventana.herramienta.set("Controlar reproducción")
        ventana._mostrar_campos()
        ventana.campos["action"].set("pausar")
        ventana._agregar()
        ventana.herramienta.set("Cambiar salida de audio")
        ventana._mostrar_campos()
        ventana.campos["device"].set("auriculares")
        ventana._agregar()
        self.assertEqual(ventana.lista.size(), 2)
        ventana._guardar()
        guardado.assert_called_once()
        rutina = routines.buscar_rutina_por_frase("a jugar")
        self.assertEqual(
            [(s.tool, s.arguments) for s in rutina.steps],
            [("media_control", {"action": "pause"}), ("set_output_device", {"device": "headphones"})],
        )

    def test_name_field_is_not_hidden_behind_its_row(self) -> None:
        ventana = form.RoutineForm(self.root, self.registry, Mock())
        # El campo debe ser hijo de la fila con la etiqueta «Nombre» (si se crea
        # en otro contenedor, Tk lo dibuja detrás de la fila y no se ve).
        etiquetas = [w.cget("text") for w in ventana.campo_nombre.master.winfo_children()
                     if isinstance(w, tk.Label)]
        self.assertEqual(etiquetas, ["Nombre"])

    def test_phrase_is_used_as_default_name(self) -> None:
        ventana = form.RoutineForm(self.root, self.registry, Mock())
        ventana.disparador.set("frase")
        ventana.frases.set("abrir navegador")
        ventana.pasos = [{"tool": "media_control", "arguments": {"action": "pause"}}]
        ventana._guardar()
        rutina = routines.buscar_rutina_por_frase("abrir navegador")
        self.assertEqual(rutina.name, "Abrir navegador")

    def test_edit_prefills_and_saves(self) -> None:
        identificador = routines.crear_rutina("Noche", "23:00", "1,2,3", PAUSA, self.registry)
        info = next(r for r in routines.listar_automatizaciones() if r.identifier == identificador)
        ventana = form.RoutineForm(self.root, self.registry, Mock(), info)
        self.assertEqual((ventana.hora.get(), ventana.minuto.get()), ("23", "00"))
        self.assertEqual([v.get() for v in ventana.dias][:4], [True, True, True, False])
        ventana.minuto.set("30")
        ventana._guardar()
        info = next(r for r in routines.listar_automatizaciones() if r.identifier == identificador)
        self.assertEqual(info.time, "23:30")

    def test_power_disables_automatic(self) -> None:
        ventana = form.RoutineForm(self.root, self.registry, Mock())
        ventana.disparador.set("time")
        ventana._actualizar_cuando()
        ventana.pasos = [{"tool": "power_control", "arguments": {"action": "sleep"}}]
        ventana._actualizar_lista()
        self.assertEqual(str(ventana.casilla_auto.cget("state")), "disabled")
        self.assertIn("cuenta atrás", ventana.nota_permiso.cget("text"))


if __name__ == "__main__":
    unittest.main()
