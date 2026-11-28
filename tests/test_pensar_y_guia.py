"""«Pensá y…» (razonamiento, directo a la IA) y la guía de frases (2026-11-27). Sin Ollama real."""

import unittest
from unittest.mock import Mock, patch

import jarvis_agent
import jarvis_guia_ordenes as g
from jarvis_app_ordenes import OrdenesMixin


class SepararTests(unittest.TestCase):
    def test_prefix(self) -> None:
        self.assertEqual(g.separar_pensar("Pensá y decime cómo sería la orden para crear una rutina"),
                         (True, "decime cómo sería la orden para crear una rutina"))
        self.assertEqual(g.separar_pensar("piensa, ¿cuánto es 17 por 23?"), (True, "¿cuánto es 17 por 23?"))
        self.assertEqual(g.separar_pensar("Jarvis, pensalo bien: qué conviene"), (True, "qué conviene"))
        for frase in ("pensá en un número", "piensa y", "pausa la música"):
            self.assertEqual(g.separar_pensar(frase), (False, frase))


class GuiaTests(unittest.TestCase):
    def test_finds_exact_phrases(self) -> None:
        texto = g.guia("crear una rutina que abra youtube y reproduzca música a una hora")
        self.assertIn("Crea una rutina llamada", texto)
        primeras = g.guia("mover una pestaña a otro monitor").splitlines()[1:3]
        self.assertTrue(any("Mueve la pestaña número 3 al monitor 2" in linea for linea in primeras))
        self.assertIn("no hay una frase directa", g.guia("xyzzy qwerty").casefold())


class PayloadTests(unittest.TestCase):
    def test_think_only_while_thinking(self) -> None:
        enviados = []

        def post(_url, payload, stream=False):
            enviados.append(payload)
            return Mock(json=lambda: {"message": {"role": "assistant", "content": "ok"}})

        with patch.object(jarvis_agent, "_post_con_reintento", side_effect=post):
            jarvis_agent._request_chat([{"role": "user", "content": "hola"}], [])
            jarvis_agent._PENSANDO = True
            try:
                jarvis_agent._request_chat([{"role": "user", "content": "hola"}], [])
            finally:
                jarvis_agent._PENSANDO = False
        self.assertFalse(enviados[0]["think"])
        self.assertTrue(enviados[1]["think"])
        self.assertEqual(enviados[1]["options"]["num_predict"], jarvis_agent.MAX_TOKENS_PENSANDO)

    def test_flag_is_reset_after_the_query(self) -> None:
        vistos = []
        with patch.object(jarvis_agent, "_procesar_con_herramientas",
                          side_effect=lambda *a, **k: vistos.append((jarvis_agent._PENSANDO,
                                                                     k["max_total_seconds"])) or "ok"):
            jarvis_agent.procesar_con_herramientas("hola", "c", Mock(), Mock(), pensar=True)
        self.assertEqual(vistos, [(True, jarvis_agent.DEFAULT_MAX_TOTAL_SECONDS * jarvis_agent.FACTOR_TIEMPO_PENSANDO)])
        self.assertFalse(jarvis_agent._PENSANDO)


class SinCreadorDeRutinasTests(unittest.TestCase):
    def test_thinking_skips_the_routine_parser(self) -> None:
        """«pensá y decime cómo armar una rutina…» iba al creador de rutinas, que respondía su error."""
        host = Mock()
        with patch("jarvis_app_ordenes.procesar_con_herramientas", return_value="respuesta") as agente, \
                patch("jarvis_routines.parsear_solicitud_creacion") as parser, \
                patch("jarvis_memory.procesar_comando") as memoria, \
                patch("jarvis_core.guardar_turno"), patch("jarvis_core.generar_audio_tts", return_value=None,
                                                          create=True):
            try:
                OrdenesMixin._process_agent_query(host, "cómo armo una rutina que abra youtube", "chat", 0.0, "c",
                                                  None, pensar=True)
            except Exception:
                pass  # lo que pase después de la IA (audio, etc.) no importa acá
        parser.assert_not_called()
        memoria.assert_not_called()
        self.assertTrue(agente.call_args.kwargs["pensar"])


if __name__ == "__main__":
    unittest.main()
