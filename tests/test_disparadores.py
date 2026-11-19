"""Automatización: disparadores de USB, programas e internet, y modos que se desactivan."""

import json
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_core as core
import jarvis_deshacer
import jarvis_disparadores as d
import jarvis_routines as routines
from jarvis_tools import crear_registro_herramientas


class _ConBase(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        for patcher in (patch.object(core, "DB_PATH", Path(temp_dir.name) / "jarvis_memory.db"),
                        patch.object(core, "_database_initialized", False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        core.inicializar_base_datos()
        self.registry = crear_registro_herramientas(Mock())

    def crear(self, nombre, trigger, programa=""):
        return routines.crear_rutina(
            nombre, "", "", json.dumps([{"tool": "set_system_volume", "arguments": {"percentage": 20}}]),
            self.registry, trigger=trigger, phrases=(programa,) if programa else ())


class RutinasTests(_ConBase):
    def test_program_routines_match_their_program(self):
        self.crear("Spotify bajo", "program_started", "Spotify")
        self.crear("Chrome cerrado", "program_stopped", "Google Chrome")
        self.crear("Con pendrive", "usb_connected")
        self.assertEqual([r.name for r in routines.rutinas_para_evento(routines.DISPARADOR_PROGRAMA_ABIERTO, "spotify")],
                         ["Spotify bajo"])
        self.assertEqual(routines.rutinas_para_evento(routines.DISPARADOR_PROGRAMA_ABIERTO, "discord"), [])
        self.assertEqual([r.name for r in routines.rutinas_para_evento(routines.DISPARADOR_PROGRAMA_CERRADO, "chrome")],
                         ["Chrome cerrado"])
        self.assertEqual([r.name for r in routines.rutinas_para_evento(routines.DISPARADOR_USB_CONECTADO, "E:")],
                         ["Con pendrive"])
        self.assertEqual(routines.programas_vigilados(), {"Spotify", "Google Chrome"})

    def test_program_trigger_needs_a_program_and_does_not_clash_with_phrases(self):
        with self.assertRaisesRegex(ValueError, "qué programa"):
            self.crear("Sin programa", "program_started")
        routines.crear_rutina("Modo spotify", "", "", json.dumps([{"tool": "set_system_volume",
                              "arguments": {"percentage": 5}}]), self.registry, trigger="frase", phrases=("spotify",))
        self.crear("Al abrir Spotify", "program_started", "spotify")  # la frase «spotify» no choca

    def test_description(self):
        self.assertEqual(routines.describir_disparador(routines.DISPARADOR_PROGRAMA_ABIERTO, "", (), ("Spotify",)),
                         "al abrir Spotify")
        self.assertEqual(routines.describir_disparador(routines.DISPARADOR_SIN_INTERNET, "", ()),
                         "al quedarse sin internet")


class FrasesTests(unittest.TestCase):
    def test_detect_triggers(self):
        casos = {
            "Cuando conecte un pendrive, abrí el Explorador": (routines.DISPARADOR_USB_CONECTADO, "", "abrí el Explorador"),
            "cuando saque el USB decí listo": (routines.DISPARADOR_USB_DESCONECTADO, "", "decí listo"),
            "Cuando abra Spotify, poné el volumen al 20": (routines.DISPARADOR_PROGRAMA_ABIERTO, "Spotify",
                                                          "poné el volumen al 20"),
            "Cuando cierre Discord: decí chau": (routines.DISPARADOR_PROGRAMA_CERRADO, "Discord", "decí chau"),
            "cuando me quede sin internet, avisame al celular": (routines.DISPARADOR_SIN_INTERNET, "",
                                                                  "avisame al celular"),
            "Cuando vuelva internet, decí volvió": (routines.DISPARADOR_CON_INTERNET, "", "decí volvió"),
            "Cuando abra un juego, poné la salida en auriculares": (routines.DISPARADOR_JUEGO_ABIERTO, "",
                                                                   "poné la salida en auriculares"),
        }
        for texto, (trigger, programa, resto) in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(routines.detectar_disparador(texto)[:3], (trigger, programa, resto))
        self.assertIsNone(routines.detectar_disparador("Abrí Spotify"))

    def test_program_names(self):
        self.assertTrue(routines.programa_coincide("Spotify.exe", "Spotify"))
        self.assertTrue(routines.programa_coincide("msedge", "Microsoft Edge"))
        self.assertTrue(routines.programa_coincide("chrome", "Google Chrome"))
        self.assertFalse(routines.programa_coincide("discord", "Spotify"))

    def test_steps_are_built_with_direct_orders(self):
        from jarvis_app import JarvisApp

        host = Mock()
        host._chained_orders.return_value = None
        host._direct_tool_for.return_value = ("set_system_volume", {"percentage": 20})
        pedido = JarvisApp._rutina_de_evento_con_ordenes(host, "Cuando abra Spotify, poné el volumen al 20")
        self.assertEqual((pedido.trigger, pedido.program, pedido.name), ("program_started", "Spotify", "Al abrir Spotify"))
        self.assertEqual(json.loads(pedido.steps_json), [{"tool": "set_system_volume", "arguments": {"percentage": 20}}])
        self.assertEqual(pedido.tool_arguments()["program"], "Spotify")
        host._direct_tool_for.return_value = None
        with patch.object(routines, "buscar_rutina_por_frase", return_value=None):
            self.assertIsNone(JarvisApp._rutina_de_evento_con_ordenes(host, "Cuando abra Spotify, hacé magia"))


class VigilanteTests(unittest.TestCase):
    def armar(self):
        self.usb, self.internet, self.procesos = set(), True, {"explorer"}
        self.eventos = queue.Queue()
        return d.VigilanteDisparadores(self.eventos, leer_usb=lambda: set(self.usb),
                                       leer_internet=lambda: self.internet, leer_procesos=lambda: set(self.procesos),
                                       vigilados=lambda: {"Spotify"})

    def publicados(self):
        salida = []
        while not self.eventos.empty():
            salida.append(self.eventos.get_nowait()[1:])
        return salida

    def test_first_reading_is_the_base_then_changes_fire(self):
        v = self.armar()
        self.usb, self.procesos = {"E:"}, {"explorer", "spotify"}  # ya estaban al abrir Jarvis
        v.revisar()
        self.assertEqual(self.publicados(), [])
        self.usb.add("F:")
        self.procesos.discard("spotify")
        v.revisar()
        self.assertEqual(self.publicados(), [(routines.DISPARADOR_USB_CONECTADO, "F:"),
                                             (routines.DISPARADOR_PROGRAMA_CERRADO, "Spotify")])
        self.procesos.add("Spotify")
        self.usb.discard("E:")
        v.revisar()
        self.assertEqual(self.publicados(), [(routines.DISPARADOR_USB_DESCONECTADO, "E:"),
                                             (routines.DISPARADOR_PROGRAMA_ABIERTO, "Spotify")])

    def test_internet_needs_two_equal_readings(self):
        v = self.armar()
        for _ in range(d.CADA_INTERNET):
            v.revisar()
        self.internet = False
        for _ in range(d.CADA_INTERNET):  # una lectura distinta: todavía no
            v.revisar()
        self.assertEqual(self.publicados(), [])
        for _ in range(d.CADA_INTERNET):
            v.revisar()
        self.assertEqual(self.publicados(), [(routines.DISPARADOR_SIN_INTERNET, "")])


class ModosTests(unittest.TestCase):
    def test_disabling_a_mode_reverts_what_it_changed(self):
        from jarvis_app import JarvisApp

        historial = jarvis_deshacer.Historial({})
        previo = jarvis_deshacer.Deshacer("viejo", "set_system_volume", {"percentage": 70})
        historial.anotar(previo)
        antes = historial.instantanea()
        historial.anotar(jarvis_deshacer.Deshacer("volumen", "set_system_volume", {"percentage": 40}))
        historial.anotar(jarvis_deshacer.Deshacer("luz", "windows_setting", {"setting": "night_light", "state": "off"}))
        nuevas = historial.nuevas_desde(antes)
        self.assertEqual([n.descripcion for n in nuevas], ["volumen", "luz"])

        rutina = routines.Routine(7, "Modo cine", "", (), (routines.RoutineStep("set_system_volume", {"percentage": 10}),),
                                  routines.DISPARADOR_FRASE)
        host = Mock()
        host._modos_activos = {7: nuevas}
        with patch.object(routines, "buscar_rutina_por_frase", return_value=rutina):
            desactivar = JarvisApp._find_undo_routine(host, "Desactivá el modo cine")
        self.assertEqual([(p.tool, p.arguments) for p in desactivar.steps],
                         [("windows_setting", {"setting": "night_light", "state": "off"}),
                          ("set_system_volume", {"percentage": 40})])
        self.assertEqual(host._modos_activos, {})  # una sola vez


if __name__ == "__main__":
    unittest.main()
