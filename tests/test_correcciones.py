"""«¿Quisiste decir…?» (2026-11-24): palabras mal entendidas, sugerencias y lo aprendido. Preferencias en
memoria (nunca la base real)."""

import unittest
from unittest.mock import Mock, patch

import jarvis_correcciones as c
from jarvis_app_ordenes import OrdenesMixin
from test_varias_ordenes import _host


class LogicaTests(unittest.TestCase):
    def test_suggestion_only_when_it_makes_an_order(self):
        with patch("jarvis_routines.buscar_rutina_por_frase", return_value=None):
            host = _host()
            es_orden = lambda t: OrdenesMixin._es_orden_completa(host, t)
            self.assertEqual(c.sugerir("desmuntea twitch", es_orden), ("desmuntea", "desmutea", "desmutea twitch"))
            self.assertEqual(c.sugerir("Cierra WhatsApp y desmuntea Twitch", es_orden)[2],
                             "Cierra WhatsApp y desmutea Twitch")
            self.assertEqual(c.sugerir("pauza el video", es_orden)[1], "pausa")
            for frase in ("qué clima hace hoy", "contame un chiste", "pausa", "abrí steam"):
                self.assertIsNone(c.sugerir(frase, es_orden), frase)

    def test_learned_words_are_saved_applied_and_forgotten(self):
        prefs = {}
        aprendidas = c.guardar(prefs.get, prefs.__setitem__, "Desmuntea", "desmutea")
        self.assertEqual(aprendidas, {"desmuntea": "desmutea"})
        self.assertEqual(c.cargar(prefs.get), {"desmuntea": "desmutea"})
        self.assertEqual(c.aplicar("DESMUNTEA Twitch y pausa", aprendidas), "desmutea Twitch y pausa")
        self.assertEqual(c.reemplazar("desmúntea twitch", "desmuntea", "desmutea"), "desmutea twitch")
        self.assertEqual(c.olvidar(prefs.get, prefs.__setitem__, "desmuntea"), {})
        self.assertEqual(c.cargar(lambda _k: "{roto"), {})

    def test_verb_with_pronoun_is_never_a_mistake(self):
        """2026-11-24: se aprendió «muévelo → mueve» y un gesto movió Brave en vez de Jarvis."""
        self.assertTrue(c.es_variante("muévelo", "mueve"))
        self.assertTrue(c.es_variante("ponmelo", "pon"))
        self.assertFalse(c.es_variante("desmuntea", "desmutea"))
        self.assertNotIn("mueve", c.candidatas("muévelo"))
        # Lo ya guardado así se ignora (sin tocar lo demás) y no se vuelve a guardar.
        guardado = lambda _k: '{"muevelo": "mueve", "pauza": "pausa"}'
        self.assertEqual(c.cargar(guardado), {"pauza": "pausa"})
        self.assertEqual(c.aplicar("Trae Jarvis al frente y muévelo al monitor 1", c.cargar(guardado)),
                         "Trae Jarvis al frente y muévelo al monitor 1")
        prefs = {}
        self.assertEqual(c.guardar(prefs.get, prefs.__setitem__, "muévelo", "mueve"), {})

    def test_answers(self):
        self.assertEqual(c.respuesta("Sí"), ("si", None))
        self.assertEqual(c.respuesta("sí, Jarvis"), ("si", None))
        self.assertEqual(c.respuesta("no"), ("no", None))
        self.assertEqual(c.respuesta("la palabra es desmutea"), ("palabra", "desmutea"))
        self.assertEqual(c.respuesta("no, quise decir «silencia»"), ("palabra", "silencia"))
        self.assertIsNone(c.respuesta("abrí Steam"))


class _Host:
    def __init__(self):
        self.root = Mock()
        self.root.after.side_effect = lambda _ms, funcion: funcion()
        self._send_text_as_typed = Mock()
        self._refresh_correcciones = Mock()
        self.tarjeta = Mock()
        self._correccion_pendiente = {"texto": "Cierra WhatsApp y desmuntea Twitch", "escuchada": "desmuntea",
                                      "correcta": "desmutea", "corregido": "Cierra WhatsApp y desmutea Twitch",
                                      "tarjeta": self.tarjeta}

    _resolver_correccion = OrdenesMixin._resolver_correccion
    _corregir_palabras = OrdenesMixin._corregir_palabras
    _buscar_correccion = OrdenesMixin._buscar_correccion


class RespuestasTests(unittest.TestCase):
    def setUp(self):
        self.prefs = {}
        for parche in (patch("jarvis_core.obtener_preferencia", side_effect=self.prefs.get),
                       patch("jarvis_core.guardar_preferencia", side_effect=self.prefs.__setitem__)):
            parche.start()
            self.addCleanup(parche.stop)

    def test_yes_learns_and_runs_the_corrected_order(self):
        host = _Host()
        host._resolver_correccion("si")
        host._send_text_as_typed.assert_called_once_with("Cierra WhatsApp y desmutea Twitch")
        self.assertEqual(host._correcciones_aprendidas, {"desmuntea": "desmutea"})
        host.tarjeta.jarvis_resolver.assert_called_once_with(True, "✓  Aprendí: «desmuntea» → «desmutea»")
        self.assertIsNone(host._correccion_pendiente)
        # La próxima vez se corrige sola, sin preguntar.
        self.assertEqual(host._corregir_palabras("desmuntea youtube"), "desmutea youtube")

    def test_no_sends_it_as_it_was_without_asking_again(self):
        host = _Host()
        host._resolver_correccion("no")
        host._send_text_as_typed.assert_called_once_with("Cierra WhatsApp y desmuntea Twitch")
        self.assertEqual(self.prefs, {})  # no aprende nada
        self.assertIsNone(host._buscar_correccion("Cierra WhatsApp y desmuntea Twitch"))  # va a la IA

    def test_typed_word_is_learned_instead(self):
        host = _Host()
        host._resolver_correccion("palabra", "  ")
        host._send_text_as_typed.assert_not_called()  # vacío: sigue esperando
        host._resolver_correccion("palabra", "silencia")
        host._send_text_as_typed.assert_called_once_with("Cierra WhatsApp y silencia Twitch")
        self.assertEqual(c.cargar(self.prefs.get), {"desmuntea": "silencia"})


class FraseExactaTests(unittest.TestCase):
    def test_gesture_phrase_is_not_corrected(self):
        """La frase de un gesto va tal cual: ni palabras aprendidas ni «¿Quisiste decir…?»."""
        host = _host()
        host._correcciones_aprendidas = {"jarvis": "brave"}  # una corrección absurda, para que se note
        host.input = Mock()
        host.input.get.return_value = ""
        enviados = []

        def send_text():
            exacto = host.__dict__.get("_texto_exacto") is True
            texto = host.input.insert.call_args.args[1]
            enviados.append(texto if exacto else OrdenesMixin._corregir_palabras(host, texto))

        host._send_text = send_text
        OrdenesMixin._send_text_as_typed(host, "Trae Jarvis al frente", exacto=True)
        OrdenesMixin._send_text_as_typed(host, "Trae Jarvis al frente")
        self.assertEqual(enviados, ["Trae Jarvis al frente", "Trae brave al frente"])
        self.assertFalse(host._texto_exacto)
        # Si la frase del gesto se parte en dos órdenes, las dos siguen exactas.
        host._send_text_as_typed = Mock()
        host._texto_exacto = True
        OrdenesMixin._run_orders(host, ["Trae Jarvis al frente", "muévelo al monitor 1"])
        host._send_text_as_typed.assert_called_once_with("Trae Jarvis al frente", exacto=True)


class ListaEnAjustesTests(unittest.TestCase):
    def test_long_list_scrolls_inside_a_box(self):
        import tkinter as tk

        from jarvis_tools import crear_registro_herramientas
        from test_ventana_unica import _Vista

        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        root.withdraw()
        self.addCleanup(root.destroy)
        with patch("jarvis_core.obtener_preferencia", return_value=None):
            vista = _Vista(root, crear_registro_herramientas(Mock()))
        vista._correcciones_aprendidas = {f"palabra{i}": f"correcta{i}" for i in range(15)}
        vista._dibujar_correcciones()
        root.update()
        vista._correcciones_lista.event_generate("<Configure>")
        root.update()
        self.assertLessEqual(int(vista._correcciones_lienzo.cget("height")), vista.ALTO_LISTA_CORRECCIONES)
        self.assertIs(vista._correcciones_lista.jarvis_scroll, vista._correcciones_lienzo)  # la rueda la mueve
        vista._correcciones_aprendidas = {"desmuntea": "desmutea"}
        vista._dibujar_correcciones()
        root.update()
        vista._correcciones_lista.event_generate("<Configure>")
        root.update()
        self.assertFalse(hasattr(vista._correcciones_lista, "jarvis_scroll"))  # corta: sin scroll propio


if __name__ == "__main__":
    unittest.main()
