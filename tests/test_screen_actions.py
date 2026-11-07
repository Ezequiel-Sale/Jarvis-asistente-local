"""Clic y escritura en pantalla. Ningún test mueve el ratón ni pulsa teclas."""

import queue
import unittest
from unittest.mock import Mock, patch

import jarvis_screen_actions as s
from jarvis_app import JarvisApp
from jarvis_tools import crear_registro_herramientas
from jarvis_vision import Monitor

MONITOR_2 = Monitor(2, -1920, 0, 1920, 1080, False)


class PhraseTests(unittest.TestCase):
    def test_click_phrases(self) -> None:
        casos = {
            "Analiza el monitor 1 y haz click en el video que dice me enamoré": ("me enamore", 1),
            "haz clic en el botón Aceptar del monitor 2": ("aceptar", 2),
            "Hey Jarvis, pulsa suscribirse": ("suscribirse", None),
            "haz clic en me enamore en la pantalla dos": ("me enamore", 2),
        }
        for texto, (consulta, monitor) in casos.items():
            with self.subTest(texto=texto):
                pedido = s.detectar_comando(texto)
                self.assertEqual((pedido["accion"], pedido["consulta"], pedido["monitor"]), ("click", consulta, monitor))

    def test_where_it_says_phrases(self) -> None:
        casos = {
            "Haz clic donde dice GOL AGÓNICO": ("gol agonico", None),
            "haz clic en donde dice Aceptar del monitor 2": ("aceptar", 2),
            "haz click en lo que dice suscribirse": ("suscribirse", None),
            "toca donde pone siguiente": ("siguiente", None),
            "haz clic en Sale el sol": ("sale el sol", None),  # no recorta títulos
        }
        for texto, (consulta, monitor) in casos.items():
            with self.subTest(texto=texto):
                pedido = s.detectar_comando(texto)
                self.assertEqual((pedido["consulta"], pedido["monitor"]), (consulta, monitor))

    def test_type_phrases(self) -> None:
        pedido = s.detectar_comando('escribe "Hola Mundo" en el cuadro de búsqueda del monitor 3')
        self.assertEqual(pedido, {"accion": "type", "consulta": "busqueda", "texto": "Hola Mundo", "monitor": 3})
        self.assertEqual(s.detectar_comando("escribe recetas de pizza en el buscador")["texto"], "recetas de pizza")

    def test_not_screen_commands(self) -> None:
        for texto in ("escribe un poema en inglés", "pausa la música", "abre una pestaña nueva", "¿qué hora es?"):
            with self.subTest(texto=texto):
                self.assertIsNone(s.detectar_comando(texto))


class ScoringTests(unittest.TestCase):
    def test_prefers_exact_short_clickable(self) -> None:
        exacto = s.puntuar("Me enamoré", "me enamore", "HyperlinkControl", False)
        largo = s.puntuar("Me enamoré - Canción oficial con letra y muchas cosas más en la descripción",
                          "me enamore", "HyperlinkControl", False)
        texto = s.puntuar("Me enamoré", "me enamore", "TextControl", False)
        self.assertGreater(exacto, largo)
        self.assertGreater(exacto, texto)
        self.assertEqual(s.puntuar("Otra cosa", "me enamore", "ButtonControl", False), 0)

    def test_tolerates_small_transcription_differences(self) -> None:
        self.assertGreater(s.puntuar("Shakira - Me Enamoré", "me enamoro", "HyperlinkControl", False), 0)
        self.assertGreater(s.puntuar("Suscribirse", "subscribirse", "ButtonControl", False), 0)
        self.assertEqual(s.puntuar("Me gusta", "me enamore", "HyperlinkControl", False), 0)
        exacto = s.puntuar("Me enamoré", "me enamore", "HyperlinkControl", False)
        parecido = s.puntuar("Me enamoro", "me enamore", "HyperlinkControl", False)
        self.assertGreater(exacto, parecido)

    def test_spoken_versus_and_one_different_word(self) -> None:
        titulo = "¡PERSONA 5 vs TODAS las BESTIAS DEMONIACAS! - 7DS Grand Cross"
        dicho = "persona 5 versus todas las bestias demoniacas"
        self.assertGreater(s.puntuar(titulo, dicho, "HyperlinkControl", False), 0)
        # Con 4+ palabras se tolera una distinta; con menos, no.
        self.assertGreater(s.puntuar("Receta de pizza casera fácil", "receta de pizza napolitana facil",
                                     "HyperlinkControl", False), 0)
        self.assertEqual(s.puntuar("Pizza casera", "pizza napolitana", "HyperlinkControl", False), 0)
        # «y» no rompe los comandos: el prefijo «analiza el monitor 1 y …» se sigue entendiendo.
        self.assertEqual(s.detectar_comando("analiza el monitor 2 y pulsa aceptar")["monitor"], 2)

    def test_typing_needs_editable_and_synonyms(self) -> None:
        self.assertEqual(s.puntuar("Buscar", "buscar", "ButtonControl", True), 0)
        self.assertGreater(max(s.puntuar("Buscar", v, "ComboBoxControl", True) for v in s.variantes("búsqueda")), 0)


class _Caja:
    def __init__(self, l, a, r, b):
        self.left, self.top, self.right, self.bottom = l, a, r, b

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top


class _Control:
    def __init__(self, nombre, tipo, caja, pid=100, hijos=()):
        self.Name, self.ControlTypeName, self.BoundingRectangle = nombre, tipo, _Caja(*caja)
        self.IsOffscreen, self.ProcessId, self.hijos = False, pid, list(hijos)


class _UIAFalso:
    """Imita lo que usa buscar_por_accesibilidad del paquete uiautomation."""

    def __init__(self, ventanas):
        self.ventanas = ventanas

    def GetRootControl(self):
        raiz = Mock()
        raiz.GetChildren.return_value = self.ventanas
        return raiz

    @staticmethod
    def WalkControl(control, maxDepth):
        pila = [(h, 1) for h in reversed(control.hijos)]
        while pila:
            actual, profundidad = pila.pop()
            if profundidad > maxDepth:
                continue
            yield actual, profundidad
            pila.extend((h, profundidad + 1) for h in reversed(actual.hijos))

    class UIAutomationInitializerInThread:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False


class AccessibilitySearchTests(unittest.TestCase):
    MONITOR = Monitor(1, 0, 0, 1920, 1080, True)

    def _buscar(self, ventanas, consulta, **extra):
        with patch.dict("sys.modules", {"uiautomation": _UIAFalso(ventanas)}):
            return s.buscar_por_accesibilidad(self.MONITOR, consulta, **extra)

    def test_finds_deep_title_and_skips_tiny_hidden_button(self) -> None:
        titulo = _Control("Mi infancia", "TextControl", (100, 500, 460, 521))
        boton_invisible = _Control("Mi infancia", "ButtonControl", (470, 500, 471, 502))
        profundo = titulo
        for _ in range(60):  # más de 45 niveles, como la cuadrícula de YouTube
            profundo = _Control("", "GroupControl", (0, 0, 1920, 1080), hijos=[profundo])
        ventana = _Control("Brave", "WindowControl", (0, 0, 1920, 1080), hijos=[profundo, boton_invisible])
        objetivo = self._buscar([ventana], "mi infancia")
        self.assertEqual((objetivo.x, objetivo.y), (280, 510))

    def test_ignores_jarvis_windows_and_other_monitors(self) -> None:
        en_jarvis = _Control("Mi infancia", "ButtonControl", (10, 10, 200, 40))
        jarvis = _Control("J.A.R.V.I.S.", "WindowControl", (0, 0, 800, 600), pid=999, hijos=[en_jarvis])
        otro = _Control("Mi infancia", "ButtonControl", (-1500, 10, -1300, 40))
        otro_monitor = _Control("Brave", "WindowControl", (-1920, 0, 0, 1080), hijos=[otro])
        self.assertIsNone(self._buscar([jarvis, otro_monitor], "mi infancia", excluir_pid=999))


class VisionParsingTests(unittest.TestCase):
    def test_coordinates_are_mapped_to_the_monitor(self) -> None:
        objetivo = s.interpretar_vision('{"encontrado": true, "x": 500, "y": 250, "texto": "Aceptar"}', MONITOR_2, 0, 0)
        self.assertEqual((objetivo.x, objetivo.y, objetivo.origen), (-960, 270, "visión"))

    def test_lists_and_cut_answers(self) -> None:
        self.assertEqual(s.interpretar_vision('{"encontrado": true, "x": [400, 600], "y": [0, 1000]}', MONITOR_2, 0, 0).x, -960)
        cortada = '{"encontrado": true, "x": 500, "y": 500, "texto": "un texto muy larg'
        self.assertIsNotNone(s.interpretar_vision(cortada, MONITOR_2, 0, 0))

    def test_not_found_or_invalid(self) -> None:
        for respuesta in ('{"encontrado": false}', "no lo veo", '{"encontrado": true, "x": 1500, "y": 10}', ""):
            with self.subTest(respuesta=respuesta):
                self.assertIsNone(s.interpretar_vision(respuesta, MONITOR_2, 0, 0))


class ToolTests(unittest.TestCase):
    def test_click_tool_is_hidden_from_the_model_and_high_risk(self) -> None:
        registro = crear_registro_herramientas(Mock())
        herramienta = registro.get("screen_click")
        self.assertTrue(herramienta.requires_confirmation)
        self.assertFalse(herramienta.exposed)
        self.assertNotIn("screen_click", {t["function"]["name"] for t in registro.ollama_tools()})

    def test_handler_clicks_then_types(self) -> None:
        with patch.object(s, "hacer_clic") as clic, patch.object(s, "escribir_texto") as escribir, \
                patch.object(s.time, "sleep"):
            self.assertEqual(s.ejecutar_en_pantalla(10, 20, "hola"), "Hice clic y escribí el texto.")
        clic.assert_called_once_with(10, 20)
        escribir.assert_called_once_with("hola")


class WorkerTests(unittest.TestCase):
    def _host(self, aprobaciones: list[bool]) -> Mock:
        host = Mock()
        host.events = queue.Queue()
        host._request_screen_approval.side_effect = aprobaciones
        return host

    def _eventos(self, host) -> list[tuple]:
        eventos = []
        while not host.events.empty():
            eventos.append(host.events.get_nowait())
        return eventos

    @patch.object(s, "buscar_por_vision")
    @patch.object(s, "buscar_por_accesibilidad")
    def test_found_by_accessibility_and_confirmed(self, accesibilidad: Mock, vision: Mock) -> None:
        objetivo = s.Objetivo(100, 200, "Me enamoré", "accesibilidad")
        accesibilidad.return_value = objetivo
        host = self._host([True])
        pedido = {"accion": "click", "consulta": "me enamore", "texto": "", "monitor": 1}
        JarvisApp._screen_action_worker(host, pedido, MONITOR_2, Mock(), "haz clic", "c1")
        vision.assert_not_called()
        self.assertEqual(self._eventos(host), [("screen_execute", objetivo, "", "haz clic", "c1")])

    @patch.object(s, "buscar_por_vision")
    @patch.object(s, "buscar_por_accesibilidad", return_value=None)
    def test_vision_needs_permission(self, _accesibilidad: Mock, vision: Mock) -> None:
        host = self._host([False])
        pedido = {"accion": "click", "consulta": "aceptar", "texto": "", "monitor": 2}
        JarvisApp._screen_action_worker(host, pedido, MONITOR_2, Mock(), "pulsa aceptar", "c1")
        vision.assert_not_called()  # sin permiso no se envía la captura al modelo
        evento = self._eventos(host)[0]
        self.assertEqual(evento[0], "screen_finished")
        self.assertIn("No encontré", evento[1])

    @patch.object(s, "buscar_por_vision")
    @patch.object(s, "buscar_por_accesibilidad", return_value=None)
    def test_vision_result_still_needs_confirmation(self, _accesibilidad: Mock, vision: Mock) -> None:
        vision.return_value = s.Objetivo(5, 5, "Aceptar", "visión")
        host = self._host([True, False])  # sí a usar visión, no al clic
        pedido = {"accion": "click", "consulta": "aceptar", "texto": "", "monitor": 2}
        JarvisApp._screen_action_worker(host, pedido, MONITOR_2, Mock(), "pulsa aceptar", "c1")
        self.assertEqual(host._request_screen_approval.call_count, 2)
        self.assertEqual(self._eventos(host)[0][1], "De acuerdo, no hice nada.")


if __name__ == "__main__":
    unittest.main()
