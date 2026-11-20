"""«Mirá cómo lo hago» (jarvis_grabador): sin ganchos reales, ventanas ni teclado."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_core as core
import jarvis_grabador as g


class PasosTests(unittest.TestCase):
    def test_clean_merges_repeats(self):
        pasos = [g.Paso("abrir", "notepad", valor="C:/n.exe"), g.Paso("abrir", "notepad", valor="C:/n.exe"),
                 g.Paso("completar", "brave", "EditControl", "Buscar", "piz"),
                 g.Paso("completar", "brave", "EditControl", "Buscar", "pizza"),
                 g.Paso("escribir", "notepad", valor="hola "), g.Paso("escribir", "notepad", valor="mundo")]
        self.assertEqual(g.limpiar(pasos), [
            g.Paso("abrir", "notepad", valor="C:/n.exe"), g.Paso("completar", "brave", "EditControl", "Buscar", "pizza"),
            g.Paso("escribir", "notepad", valor="hola mundo")])

    def test_description_uses_the_window_name(self):
        texto = g.describir([g.Paso("abrir", "win32calc", valor="C:/c.exe", etiqueta="Calculadora"),
                             g.Paso("click", "win32calc", "ButtonControl", "5", etiqueta="Calculadora"),
                             g.Paso("teclas", "notepad", valor="ctrl+s")])
        self.assertEqual(texto, "1. Abrir Calculadora; 2. Pulsar «5» en Calculadora; 3. Apretar ctrl+s en notepad")
        self.assertEqual(g.etiqueta_de("Sin título: Bloc de notas", "notepad"), "Bloc de notas")

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as carpeta, patch.object(core, "APP_DIR", Path(carpeta)):
            pasos = [g.Paso("click", "win32calc", "ButtonControl", "5")]
            ruta = g.guardar("Pedir turno", pasos)
            self.assertEqual(ruta.name, "pedir-turno.json")
            self.assertEqual(g.cargar("pedir turno"), pasos)
            with self.assertRaisesRegex(FileNotFoundError, "ninguna grabación"):
                g.cargar("otra")

    def test_phrases(self):
        for texto in ("Mirá cómo lo hago", "Jarvis, fijate cómo lo hago", "Grabá lo que hago"):
            self.assertTrue(g.detectar_inicio(texto), texto)
        self.assertFalse(g.detectar_inicio("Mirá el partido"))
        self.assertEqual(g.detectar_fin("Listo, guardalo como pedir turno"), (True, "pedir turno"))
        self.assertEqual(g.detectar_fin("Ya está"), (True, ""))
        self.assertEqual(g.detectar_fin("Cancelá la grabación"), (False, ""))
        self.assertIsNone(g.detectar_fin("abrí Spotify"))


class TeclasTests(unittest.TestCase):
    """La parte del teclado: lo escrito en un campo sin valor legible se guarda como «escribir»;
    las contraseñas no."""

    def armar(self):
        grabador = g.Grabador()
        ventana = Mock(programa="notepad", titulo="Sin título: Bloc de notas")
        self.parches = [patch("jarvis_control._ventana_de", return_value=ventana),
                        patch("jarvis_control.protegida", return_value=False),
                        patch.object(grabador, "_caracter", side_effect=lambda vk: chr(vk).lower())]
        for p in self.parches:
            p.start()
            self.addCleanup(p.stop)
        return grabador

    def test_typed_text_and_shortcut(self):
        grabador = self.armar()
        auto = Mock()
        auto.GetFocusedControl.return_value = Mock(ControlTypeName="DocumentControl", Name="", IsPassword=False,
                                                   **{"GetValuePattern.return_value": None})
        for vk in b"HOLA":
            grabador._tecla(auto, vk, True)
        grabador._tecla(auto, 0x11, True)   # ctrl
        grabador._tecla(auto, ord("S"), True)
        grabador._tecla(auto, 0x11, False)
        self.assertEqual([(p.tipo, p.valor, p.etiqueta) for p in grabador.pasos],
                         [("escribir", "hola", "Bloc de notas"), ("teclas", "ctrl+s", "Bloc de notas")])

    def test_password_field_is_not_recorded(self):
        grabador = self.armar()
        auto = Mock()
        auto.GetFocusedControl.return_value = Mock(ControlTypeName="EditControl", Name="Clave", IsPassword=True)
        for vk in b"1234":
            grabador._tecla(auto, vk, True)
        grabador._cerrar_campo(auto)
        self.assertEqual(grabador.pasos, [])


class ReproductorTests(unittest.TestCase):
    def setUp(self):
        self.registry = Mock()
        self.registry.execute.return_value = "ok"
        ventana = Mock(programa="win32calc", hwnd=5)
        foto = Mock(elementos=[Mock(tipo="ButtonControl", nombre="5", n=7), Mock(tipo="ButtonControl", nombre="Borrar", n=9)])
        for objetivo, valor in (("jarvis_windows.listar_ventanas", [ventana]), ("jarvis_control.traer_al_frente", True),
                                ("jarvis_control.fijar", None), ("jarvis_control.fotografiar", foto)):
            parche = patch(objetivo, return_value=valor)
            parche.start()
            self.addCleanup(parche.stop)

    def test_replays_through_the_registry(self):
        r = g.Reproductor(self.registry, Mock(), dormir=lambda _s: None)
        texto = r.repetir([g.Paso("abrir", "win32calc", valor="C:/c.exe"), g.Paso("click", "win32calc", "ButtonControl", "5"),
                           g.Paso("teclas", "win32calc", valor="enter")], "suma")
        self.assertEqual(texto, "Listo: repetí «suma» (3 pasos).")
        self.assertEqual([c.args[:2] for c in self.registry.execute.call_args_list],
                         [("window_action", {"n": 7, "action": "click"}), ("press_keys", {"keys": "enter"})])
        self.assertTrue(all(c.kwargs["origin"] == "grabacion" for c in self.registry.execute.call_args_list))

    def test_delicate_buttons_ask_and_missing_ones_go_to_the_autopilot(self):
        r = g.Reproductor(self.registry, Mock(return_value=False), dormir=lambda _s: None, intentos=1)
        self.assertIn("Paré antes de pulsar «Borrar»", r.repetir([g.Paso("click", "win32calc", "ButtonControl", "Borrar")]))
        piloto = Mock(return_value="Hecho.")
        r = g.Reproductor(self.registry, Mock(), piloto=piloto, dormir=lambda _s: None, intentos=1)
        texto = r.repetir([g.Paso("click", "win32calc", "ButtonControl", "Sumar")], "suma")
        self.assertIn("el resto lo terminó el piloto: Hecho.", texto)
        self.assertIn("Pulsar «Sumar»", piloto.call_args.args[0])


class AppTests(unittest.TestCase):
    def test_save_creates_a_phrase_command(self):
        from jarvis_app import JarvisApp

        host = Mock()
        pasos = [g.Paso("click", "win32calc", "ButtonControl", "5")]
        with patch.object(g, "guardar") as guardar, patch("jarvis_routines.crear_rutina") as crear:
            JarvisApp._guardar_grabacion(host, "Sumar cinco", pasos)
        guardar.assert_called_once_with("Sumar cinco", pasos)
        argumentos = crear.call_args
        self.assertEqual(argumentos.kwargs["trigger"], "frase")
        self.assertEqual(argumentos.kwargs["phrases"], ("Sumar cinco",))
        self.assertEqual(json.loads(argumentos.args[3]), [{"tool": "replay_recording", "arguments": {"name": "Sumar cinco"}}])
        self.assertIn("decí «Sumar cinco»", host._add_message.call_args.args[1])

    def test_routed_for_text_and_voice(self):
        fuente = (Path(__file__).resolve().parent.parent / "jarvis_app_ordenes.py").read_text("utf-8")
        self.assertIn("self._es_orden_de_grabacion(text) is True", fuente)
        self.assertIn("self._es_orden_de_grabacion(transcript) is True", fuente)


if __name__ == "__main__":
    unittest.main()


class VozTests(unittest.TestCase):
    def test_voice_order_frees_jarvis_so_it_keeps_listening(self):
        """2026-11-19: tras «mirá cómo lo hago» por voz, Jarvis quedaba ocupado y no oía el «listo»."""
        from jarvis_app import JarvisApp

        host = Mock(_grabador=None, _grabacion_sin_nombre=None)
        host.games_watcher.jugando = set()
        with patch.object(g.Grabador, "iniciar"):
            JarvisApp._manejar_grabacion(host, "Mirá cómo lo hago")
        host._set_idle.assert_called_once()
        host.root.after.assert_called_once()


class NombresTests(unittest.TestCase):
    """2026-11-19: las pestañas de Brave cambian de nombre solas («Uso de memoria: 772 MB»)."""

    def test_stable_names(self):
        self.assertEqual(g.nombre_estable("IlloJuan - Twitch: Reproducción de audio: Uso de memoria: 772 MB"),
                         "IlloJuan - Twitch")
        self.assertEqual(g.nombre_estable("Sin título: Bloc de notas"), "Sin título: Bloc de notas")

    def test_replay_finds_the_tab_even_if_its_name_changed(self):
        self.assertEqual(g.parecido("IlloJuan - Twitch: Uso de memoria: 772 MB",
                                    ["YouTube", "IlloJuan - Twitch: Uso de memoria: 901 MB"]), 1)
        self.assertEqual(g.parecido("ZonaLeRoS | Descargar juegos para PC", ["ZonaLeRoS | Descargar juegos para PC - Fijada"]), 0)
        self.assertIsNone(g.parecido("Igual a", ["Igual", "Borrar"]))
        self.assertIsNone(g.parecido("5", ["6"]))


class PestanasTests(unittest.TestCase):
    def test_brave_tabs_go_by_title_even_if_hidden(self):
        """2026-11-20: las pestañas de un grupo miden 2 px y la lectura no las veía; van por browser_tab."""
        registry = Mock()
        registry.execute.return_value = "ok"
        with patch("jarvis_windows.listar_ventanas", return_value=[Mock(programa="brave", hwnd=1)]), \
                patch("jarvis_control.traer_al_frente"), patch("jarvis_control.fijar"), \
                patch("jarvis_control.fotografiar") as fotografiar:
            texto = g.Reproductor(registry, Mock(), dormir=lambda _s: None).repetir(
                [g.Paso("click", "brave", "TabItemControl", "IlloJuan - Twitch: Uso de memoria: 772 MB")], "tabs")
        self.assertEqual(texto, "Listo: repetí «tabs» (1 pasos).")
        registry.execute.assert_called_once_with("browser_tab", {"action": "go", "title": "IlloJuan - Twitch"},
                                                 confirmed=True, origin="grabacion")
        fotografiar.assert_not_called()


class RuedaTests(unittest.TestCase):
    """2026-11-20: el usuario abre favoritos con la rueda (pestaña nueva) y después cambia a esa pestaña."""

    def test_new_tab_number(self):
        import jarvis_pestanas

        self.assertEqual(jarvis_pestanas.pestana_nueva(["A", "B"], ["A", "Just a moment...", "B"]), 2)
        self.assertEqual(jarvis_pestanas.pestana_nueva(["A", "B"], ["A", "B", "C"]), 3)
        self.assertIsNone(jarvis_pestanas.pestana_nueva(["A", "B"], ["A", "B"]))

    def test_wheel_click_then_the_new_tab_even_without_its_title(self):
        registry = Mock()

        def ejecutar(herramienta, argumentos, **_k):
            if herramienta == "browser_tab" and argumentos["action"] == "go":
                raise RuntimeError("No encontré ninguna pestaña")  # todavía dice «Just a moment…»
            return "ok"

        registry.execute.side_effect = ejecutar
        foto = Mock(elementos=[Mock(tipo="ButtonControl", nombre="ZonaLeRoS", n=4)])
        r = g.Reproductor(registry, Mock(), dormir=lambda _s: None, intentos=2)
        titulos = iter([["Twitch"], ["Twitch", "Just a moment..."]])
        with patch("jarvis_windows.listar_ventanas", return_value=[Mock(programa="brave", hwnd=1)]), \
                patch("jarvis_control.traer_al_frente"), patch("jarvis_control.fijar"), \
                patch("jarvis_control.fotografiar", return_value=foto), \
                patch.object(r, "_titulos", side_effect=lambda: next(titulos)):
            texto = r.repetir([g.Paso("click_rueda", "brave", "ButtonControl", "ZonaLeRoS"),
                               g.Paso("click", "brave", "TabItemControl", "ZonaLeRoS | Descargar juegos para PC")], "fav")
        self.assertEqual(texto, "Listo: repetí «fav» (2 pasos).")
        llamadas = [c.args[:2] for c in registry.execute.call_args_list]
        self.assertEqual(llamadas[0], ("window_action", {"n": 4, "action": "middle_click"}))
        self.assertEqual(llamadas[-1], ("browser_tab", {"action": "number", "number": 2}))

    def test_description(self):
        self.assertIn("pestaña nueva (clic con la rueda)",
                      g.describir_paso(g.Paso("click_rueda", "brave", "ButtonControl", "ZonaLeRoS", etiqueta="Brave")))
