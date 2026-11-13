"""Respuesta en vivo: frases, voz por partes y streaming del agente. Sin audio real."""

import json
import os
import tempfile
import threading
import unittest
import wave
from unittest.mock import Mock, patch

import jarvis_agent as agent
import jarvis_voz_stream as voz


def _wav(ruta: str, muestras: int = 100) -> str:
    with wave.open(ruta, "wb") as archivo:
        archivo.setnchannels(1)
        archivo.setsampwidth(2)
        archivo.setframerate(22050)
        archivo.writeframes(b"\x01\x00" * muestras)
    return ruta


class SentenceTests(unittest.TestCase):
    def test_complete_sentences_and_rest(self) -> None:
        frases, resto = voz.extraer_frases("Hola, ¿cómo estás hoy? Todo bien por aquí. Y tú")
        self.assertEqual(frases, ["Hola, ¿cómo estás hoy?", "Todo bien por aquí."])
        self.assertEqual(resto, "Y tú")

    def test_short_pieces_are_joined(self) -> None:
        frases, resto = voz.extraer_frases("Sí. Claro. Lo hago ahora mismo. ")
        self.assertEqual(frases, ["Sí. Claro. Lo hago ahora mismo."])
        self.assertEqual(resto, "")

    def test_final_flushes_rest(self) -> None:
        self.assertEqual(voz.extraer_frases("Sin punto final", final=True), (["Sin punto final"], ""))

    def test_long_text_without_period_is_cut_at_comma(self) -> None:
        texto = "uno, " * 40
        frases, resto = voz.extraer_frases(texto)
        self.assertEqual(len(frases), 1)
        self.assertLessEqual(len(resto), 10)

    def test_markdown_is_not_read_aloud(self) -> None:
        self.assertEqual(
            voz.limpiar_para_voz("**Hola**\n- uno\n- dos `x` https://a.com"),
            "Hola uno dos x el enlace",
        )


class SpeakerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.dichas: list[str] = []
        self.reproducidas: list[str] = []

    def _hablador(self) -> voz.HabladorEnVivo:
        def sintetizar(frase: str, identificador: str) -> str:
            self.dichas.append(frase)
            return _wav(os.path.join(self.dir, identificador + ".wav"))

        def reproducir(ruta: str, _cancelado: threading.Event) -> None:
            self.reproducidas.append(os.path.basename(ruta))

        return voz.HabladorEnVivo(sintetizar, reproducir=reproducir, prefijo="p")

    def test_speaks_while_feeding_and_merges_audio(self) -> None:
        h = self._hablador()
        for trozo in ("Hola, esto es ", "una prueba de voz. ", "Segunda frase ", "completa."):
            h.alimentar(trozo)
        destino = os.path.join(self.dir, "final.wav")
        ruta = h.terminar("Hola, esto es una prueba de voz. Segunda frase completa.", destino)
        self.assertEqual(self.dichas, ["Hola, esto es una prueba de voz.", "Segunda frase completa."])
        self.assertEqual(self.reproducidas, ["p_001.wav", "p_002.wav"])
        self.assertEqual(ruta, destino)
        with wave.open(ruta) as archivo:
            self.assertEqual(archivo.getnframes(), 200)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "p_001.wav")))

    def test_final_warnings_are_also_spoken(self) -> None:
        h = self._hablador()
        h.alimentar("Listo, ya lo hice. ")
        h.terminar("Listo, ya lo hice. \n\nAtención: «X» falló.", None)
        self.assertEqual(self.dichas, ["Listo, ya lo hice.", "Atención: «X» falló."])

    def test_new_round_compares_only_with_last_round(self) -> None:
        h = self._hablador()
        h.alimentar(None)
        h.alimentar("Voy a revisar tus aplicaciones ahora. ")
        h.alimentar(None)
        h.alimentar("Tienes tres aplicaciones abiertas.")
        h.terminar("Tienes tres aplicaciones abiertas.", None)
        self.assertEqual(
            self.dichas,
            ["Voy a revisar tus aplicaciones ahora.", "Tienes tres aplicaciones abiertas."],
        )

    def test_different_final_text_is_spoken(self) -> None:
        h = self._hablador()
        h.alimentar("Un momento, por favor. ")
        h.terminar("No pude completar la acción.", None)
        self.assertEqual(self.dichas[-1], "No pude completar la acción.")

    def test_nothing_fed_means_not_started(self) -> None:
        h = self._hablador()
        self.assertFalse(h.empezo)
        self.assertIsNone(h.terminar("", None))

    def test_cancel_stops_speaking(self) -> None:
        h = self._hablador()
        h.cancelar()
        h.alimentar("Esto no debería sonar nunca. ")
        h.terminar("Esto no debería sonar nunca.", None)
        self.assertEqual(self.reproducidas, [])


class CancelCheckTests(unittest.TestCase):
    def test_external_cancel_stops_waiting(self) -> None:
        bloqueo = threading.Event()
        sonando = threading.Event()

        def reproducir(_ruta: str, cancelado: threading.Event) -> None:
            sonando.set()
            cancelado.wait(5)  # simula una frase larga hasta que se cancela
            bloqueo.set()

        carpeta = tempfile.mkdtemp()
        h = voz.HabladorEnVivo(
            lambda _f, i: _wav(os.path.join(carpeta, i + ".wav")), reproducir=reproducir
        )
        h.alimentar("Una frase bastante larga para hablar. ")
        self.assertIsNone(h.terminar("Una frase bastante larga para hablar.", None, cancelar_si=sonando.is_set))
        self.assertTrue(bloqueo.is_set())


class LiveBubbleTests(unittest.TestCase):
    """La burbuja provisional se crea, crece y se quita (Tk real, ventana oculta)."""

    def setUp(self) -> None:
        import tkinter as tk

        from jarvis_app import JarvisApp

        try:
            self.root = tk.Tk()
        except tk.TclError as error:  # sin pantalla
            self.skipTest(f"Tk no disponible: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

        class Host:
            _show_live_answer = JarvisApp._show_live_answer
            _clear_live_answer = JarvisApp._clear_live_answer
            _notify_mobile_live = JarvisApp._notify_mobile_live
            _bubble_header = JarvisApp._bubble_header

            def _scroll_chat_to_bottom(self) -> None:
                pass

        self.host = Host()
        self.host.messages = tk.Frame(self.root)
        self.host._live_row = None
        self.host._live_label = None
        self.host._live_text = ""
        self.host.mobile = Mock()
        self.host.mobile.activo.return_value = True

    def test_grows_and_clears(self) -> None:
        self.host._show_live_answer(None)
        self.host._show_live_answer("Hola")
        self.host._show_live_answer(", ¿qué tal?")
        self.assertEqual(self.host._live_label.cget("text"), "Hola, ¿qué tal?")
        self.host._show_live_answer(None)  # nueva ronda
        self.assertEqual(self.host._live_label.cget("text"), "…")
        self.host._clear_live_answer()
        self.assertIsNone(self.host._live_row)
        self.assertEqual(self.host.messages.winfo_children(), [])
        # El celular recibe el texto acumulado (sin markdown) y el aviso de descarte.
        movil = self.host.mobile
        self.assertEqual([c.args[0] for c in movil.en_vivo.call_args_list], ["", "Hola", "Hola, ¿qué tal?", ""])
        movil.fin_en_vivo.assert_called_once()


class _RespuestaFalsa:
    def __init__(self, lineas: list[dict]) -> None:
        self.lineas = [json.dumps(linea).encode() for linea in lineas]

    def iter_lines(self):
        return iter(self.lineas)


class AgentStreamTests(unittest.TestCase):
    def test_gate_hides_json_tool_calls(self) -> None:
        recibido: list[str] = []
        compuerta = agent._CompuertaTexto(recibido.append)
        for trozo in ("  ", '{"name": "x"', ', "arguments": {}}'):
            compuerta(trozo)
        self.assertEqual(recibido, [])
        recibido.clear()
        compuerta = agent._CompuertaTexto(recibido.append)
        for trozo in ("  ", "Hola", " mundo"):
            compuerta(trozo)
        self.assertEqual(recibido, ["Hola", " mundo"])

    def test_stream_is_assembled(self) -> None:
        trozos: list[str] = []
        respuesta = _RespuestaFalsa([
            {"message": {"content": "Hola"}, "done": False},
            {"message": {"content": " mundo"}, "done": False},
            {"message": {"content": "", "tool_calls": [{"function": {"name": "t", "arguments": {}}}]}, "done": False},
            {"message": {"content": ""}, "done": True},
        ])
        with patch.object(agent.core, "registrar_latencia"):
            mensaje = agent._leer_stream(respuesta, trozos.append, 0.0)
        self.assertEqual(mensaje["content"], "Hola mundo")
        self.assertEqual(len(mensaje["tool_calls"]), 1)
        self.assertEqual(trozos, ["Hola", " mundo"])
        self.assertNotIn("done_reason", mensaje)

    def test_stream_reports_length_cut(self) -> None:
        respuesta = _RespuestaFalsa([
            {"message": {"content": "Uno, do"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "length"},
        ])
        with patch.object(agent.core, "registrar_latencia"):
            mensaje = agent._leer_stream(respuesta, lambda _: None, 0.0)
        self.assertEqual(mensaje["done_reason"], "length")

    def test_no_streaming_while_actions_are_pending(self) -> None:
        """Si el usuario pidió una acción y aún no hay llamadas, no se habla:
        esa respuesta podría descartarse para reintentar."""
        transmitidos: list[object] = []

        def solicitar(_messages, _tools, _token, on_text=None):
            transmitidos.append(on_text)
            return {"role": "assistant", "content": "Hecho, abrí la pestaña."}

        with patch.object(agent, "_solicitar_chat", side_effect=solicitar), patch.object(
            agent.core, "obtener_historial", return_value=[]
        ), patch.object(agent.core, "cargar_contexto_db", return_value=""), patch.object(
            agent.jarvis_memory, "buscar_recuerdos", return_value=[]
        ):
            from jarvis_tool_registry import ToolRegistry

            agent.procesar_con_herramientas(
                "¿cuál es la capital de Francia?", "c", ToolRegistry(), lambda *_: True,
                on_text=lambda _t: None,
            )
            self.assertIsNotNone(transmitidos[-1])
            transmitidos.clear()
            agent.procesar_con_herramientas(
                "abre una pestaña nueva", "c", ToolRegistry(), lambda *_: True,
                on_text=lambda _t: None,
            )
            self.assertIsNone(transmitidos[0])


if __name__ == "__main__":
    unittest.main()
