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

    def test_up_to_fifteen_steps(self) -> None:
        pasos = [{"tool": "media_control", "arguments": {"action": "next"}}] * 15
        routines.crear_rutina("Quince", "08:00", "1", json.dumps(pasos), self.registry)
        with self.assertRaises(ValueError):
            routines.crear_rutina("Dieciséis", "08:00", "1", json.dumps(pasos * 2), self.registry)

    def test_new_basic_actions(self) -> None:
        """Esperar, Decir un mensaje, Avisar al celular y Cerrar un programa, en una rutina."""
        import jarvis_modo_juego

        pasos = [
            {"tool": "close_program", "arguments": {"names": "Discord"}},
            {"tool": "routine_wait", "arguments": {"seconds": 5}},
            {"tool": "say_text", "arguments": {"text": "Listo para jugar"}},
            {"tool": "notify_phone", "arguments": {"text": "Empezó el juego"}},
            {"tool": "morning_briefing", "arguments": {}},
        ]
        routines.crear_rutina("A jugar", "", "", json.dumps(pasos), self.registry, trigger="frase",
                              phrases=["a jugar"])
        with patch.object(jarvis_modo_juego, "cerrar", return_value=[jarvis_modo_juego.Cerrado("discord", "d.exe")]):
            self.assertEqual(jarvis_modo_juego.cerrar_programas("Discord, Jarvis"), "Cerré discord.")
        with patch.object(jarvis_modo_juego, "cerrar", return_value=[]):
            self.assertEqual(jarvis_modo_juego.cerrar_programas("spotify"), "No estaba abierto: spotify.")
        with self.assertRaises(ValueError):
            jarvis_modo_juego.cerrar_programas("jarvis, explorer")  # protegidos: no queda nada
        import jarvis_tools

        with patch.object(jarvis_tools.time, "sleep") as dormir:
            self.assertEqual(self.registry.get("routine_wait").handler(seconds=5), "Esperé 5 s.")
        dormir.assert_called_once_with(5)

    def test_say_and_phone_from_the_app(self) -> None:
        import queue

        host = Mock()
        host.events = queue.Queue()
        self.assertEqual(JarvisApp._tool_say_text(host, " Hola "), "Dije: «Hola».")
        self.assertEqual(host.events.get_nowait(), ("routine_say", "Hola"))
        host.mobile.activo.return_value = True
        self.assertEqual(JarvisApp._tool_notify_phone(host, "Hola"), "Le avisé al celular.")
        host.mobile.mensaje.assert_called_once_with("assistant", "Hola", aviso="info")
        host.mobile.activo.return_value = False
        with self.assertRaises(Exception):
            JarvisApp._tool_notify_phone(host, "Hola")


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

    def test_store_apps_open_by_package_family(self) -> None:
        # WinError 5 al ejecutar el .exe de WindowsApps: se abre por su familia de paquete,
        # que no cambia cuando la app se actualiza de versión.
        ruta = (r"C:\Program Files\WindowsApps\5319275A.WhatsAppDesktop_2.1.0.0_x64__"
                r"cv1g1gvanyjgm\WhatsApp.Root.exe")
        with patch.object(jarvis_system.os, "startfile", create=True) as abrir:
            self.assertIn("WhatsApp.Root", jarvis_system.abrir_programa(ruta))
        abrir.assert_called_once_with(r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App")
        self.assertIsNone(jarvis_system.app_de_la_tienda(Path(r"C:\Windows\notepad.exe")))
        self.assertIsNone(jarvis_system.app_de_la_tienda(Path(r"C:\Program Files\WindowsApps")))

    def test_store_app_id_comes_from_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            paquete = Path(carpeta) / "WindowsApps" / "Editor.Notas_1.0.0.0_x64__abc123"
            paquete.mkdir(parents=True)
            (paquete / "AppxManifest.xml").write_text(
                '<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10">'
                '<Applications><Application Id="Principal" Executable="otro.exe"/>'
                r'<Application Id="Notas" Executable="bin\Notas.exe"/></Applications></Package>',
                encoding="utf-8",
            )
            self.assertEqual(jarvis_system.app_de_la_tienda(paquete / "bin" / "notas.exe"),
                             r"shell:AppsFolder\Editor.Notas_abc123!Notas")


class PhraseExecutionTests(unittest.TestCase):
    def _host(self, aprueba_energia: bool = True) -> Mock:
        host = Mock()
        host._confirm_display_batch.return_value = None  # sin cambios de monitores
        host.tool_registry = Mock()
        host.tool_registry.execute.side_effect = lambda tool, args, **_k: f"{tool} hecho."
        host.tool_registry.get.return_value.display_name = "Acción"
        host.events = queue.Queue()
        host.audio_enabled = False
        host._request_power_confirmation.return_value = aprueba_energia
        return host

    def _rutina(self, *pasos) -> routines.Routine:
        return routines.Routine(1, "Modo noche", "", (1,), tuple(routines.RoutineStep(t, a) for t, a in pasos))

    @patch("jarvis_core.guardar_turno")
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

    @patch("jarvis_core.guardar_turno")
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


class PowerInMinutesTests(_DbTestCase):
    def test_power_off_in_ten_minutes_passes_validation(self) -> None:
        """«Apaga el equipo dentro de 10 minutos»: la hora va al segundo (HH:MM:SS) y antes el
        esquema de schedule_routine solo admitía HH:MM («el parámetro 'time' es demasiado largo»)."""
        from jarvis_commands import limpiar_transcripcion_voz

        pedido = routines.parsear_solicitud_creacion(
            limpiar_transcripcion_voz("Apaga el equipo dentro de 10 minutos."), self.registry)
        argumentos = self.registry.validate_arguments("schedule_routine", pedido.tool_arguments())
        self.assertEqual(len(argumentos["time"]), 8)
        self.assertTrue(argumentos["once"])


class ActionPickerTests(_DbTestCase):
    def test_catalog_covers_every_routine_action_with_unique_titles(self) -> None:
        filas = form.catalogo_acciones(self.registry)
        self.assertEqual({f[3] for f in filas}, {n for _, n in form.herramientas_disponibles(self.registry)})
        self.assertNotIn("Otras", {f[0] for f in filas})  # todas tienen su categoría
        titulos = [f[1] for f in filas]
        self.assertEqual(len(titulos), len(set(titulos)))  # antes había dos «Navegar en Brave»
        self.assertEqual([f[0] for f in filas[:4]], ["Básicas"] * 3 + ["Tele"])

    def test_search_by_words_without_accents(self) -> None:
        filas = form.catalogo_acciones(self.registry)

        def nombres(busqueda):
            return [f[3] for f in form.filtrar_acciones(filas, busqueda, self.registry)]

        self.assertEqual(nombres("hdmi"), ["tv_control"])
        self.assertIn("press_window_button", nombres("jugar"))
        self.assertIn("set_system_volume", nombres("VOLÚMEN"))
        self.assertEqual(nombres("volumen tele"), ["tv_control", "tv_status"])
        self.assertIn("media_control", nombres("Controlar reproducción"))  # el nombre de siempre también
        self.assertEqual(nombres("zzz"), [])
        self.assertEqual(len(nombres("")), len(filas))


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

    def test_tv_steps_with_clear_labels(self) -> None:
        """Rutina «cine»: prender la tele, HDMI 1 y abrir Netflix (elegida de la lista o escrita)."""
        guardado = Mock()
        ventana = form.RoutineForm(self.root, self.registry, guardado)
        ventana.nombre.set("Cine")
        ventana.disparador.set("frase")
        ventana.frases.set("modo cine")
        ventana.herramienta.set("Control de la tele")
        ventana._mostrar_campos()
        opciones = list(ventana.campos["action"]._jarvis_enum)
        self.assertEqual(opciones[:3], ["Encender", "Apagar", "Entrada HDMI 1"])
        self.assertIn("Atrás", opciones)  # no «página anterior», que es del navegador
        self.assertFalse({"power", "wakeup", "sleep", "suspender"} & set(opciones))
        for accion in ("Encender", "Entrada HDMI 1"):
            ventana.campos["action"].set(accion)
            ventana._agregar()
        ventana.herramienta.set("Abrir app en la tele")
        ventana._mostrar_campos()
        ventana.campos["app"].set("Netflix")
        ventana._agregar()
        ventana.campos["app"].set("com.otra.app")
        ventana._agregar()
        # Las apps aprendidas de la tele (las registra JarvisApp) también salen en la lista.
        with patch.dict(form.SUGERENCIAS, {"tv_open_app": lambda: {"Netflix": "netflix", "Star": "com.star.app"}}):
            ventana._mostrar_campos()
            self.assertEqual(ventana.campos["app"]._jarvis_sugerencias, {"Netflix": "netflix", "Star": "com.star.app"})
            self.assertEqual(form.etiqueta_valor("tv_open_app", "com.star.app"), "Star")
        self.assertEqual(ventana.lista.get(0, "end")[1].split(". ", 1)[-1], "Controlar la tele (Entrada HDMI 1)")
        ventana._guardar()
        rutina = routines.buscar_rutina_por_frase("modo cine")
        self.assertEqual([(s.tool, s.arguments) for s in rutina.steps], [
            ("tv_control", {"action": "power_on"}), ("tv_control", {"action": "hdmi1"}),
            ("tv_open_app", {"app": "netflix"}), ("tv_open_app", {"app": "com.otra.app"})])
        # Solo tocan la tele: una rutina por horario las puede hacer sin preguntar.
        self.assertEqual(routines.acciones_con_permiso(["tv_control", "tv_open_app"], self.registry), [])

    def test_pick_from_the_tree_after_searching(self) -> None:
        ventana = form.RoutineForm(self.root, self.registry, Mock())
        ventana.busqueda.set("atras recargar")
        self.assertEqual(ventana.arbol.get_children(ventana.arbol.get_children()[0]), ("accion:browser_history",))
        ventana.arbol.selection_set("accion:browser_history")
        self.root.update()  # procesa <<TreeviewSelect>>
        self.assertEqual(ventana._nombre_herramienta(), "browser_history")
        self.assertIn("action", ventana.campos)
        self.assertIn("Atrás, adelante o recargar", ventana.descripcion.cget("text"))

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
