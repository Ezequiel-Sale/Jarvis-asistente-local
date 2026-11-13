"""Resumen «Buenos días» y precarga del modelo de lenguaje.

Todo simulado: ni red ni Ollama ni hardware reales, ni la base de datos."""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jarvis_agent as agent
import jarvis_buenos_dias as b
from jarvis_app import JarvisApp


class PhraseTests(unittest.TestCase):
    def test_asks_for_the_brief(self) -> None:
        for texto in ("Buenos días.", "buen día jarvis", "Hey Jarvis, buenos días", "dame el resumen del día",
                      "¿Cómo viene el día?", "resumen del día de hoy"):
            with self.subTest(texto=texto):
                self.assertEqual(b.detectar(texto), ("morning_briefing", {}))

    def test_turns_the_automatic_one_on_and_off(self) -> None:
        self.assertEqual(b.detectar("desactiva el resumen de la mañana"), ("morning_briefing", {"automatic": False}))
        self.assertEqual(b.detectar("activa el resumen matutino"), ("morning_briefing", {"automatic": True}))

    def test_other_orders_are_not_the_brief(self) -> None:
        self.assertIsNone(b.detectar("buenos días, pon música"))
        self.assertIsNone(b.detectar("¿qué día es hoy?"))


class ComposeTests(unittest.TestCase):
    AHORA = datetime(2026, 11, 13, 8, 15)

    def test_full_brief(self) -> None:
        recordatorios = [
            SimpleNamespace(texto="pagar la luz", tipo="recordatorio", vence=datetime(2026, 11, 13, 9, 0)),
            SimpleNamespace(texto="", tipo="temporizador", vence=datetime(2026, 11, 20, 9, 0)),  # otra semana
        ]
        disco = SimpleNamespace(unidad="C:", libre=40 * 1024**3, total=500 * 1024**3, usado_pct=92.0)
        texto = b.componer(self.AHORA, clima="Ahora en Córdoba: 18 °C, despejado.",
                           recordatorios=recordatorios,
                           rutinas=["Noche · hoy 23:00", "Inicio · mañana 09:00"],
                           discos=[disco], alertas_temperatura=["la GPU está a 91 °C"], bateria=15)
        self.assertTrue(texto.startswith("Buenos días. Hoy es viernes 13 de noviembre y son las 8:15."))
        self.assertIn("Córdoba: 18 °C", texto)
        self.assertIn("pagar la luz hoy a las 9:00", texto)
        self.assertNotIn("temporizador", texto)
        self.assertIn("Rutinas de hoy: Noche a las 23:00.", texto)
        self.assertNotIn("Inicio", texto)
        self.assertIn("el disco C está al 92 % (quedan 40 GB)", texto)
        self.assertIn("la GPU está a 91 °C", texto)
        self.assertIn("15 % de batería, conviene cargarlos", texto)

    def test_quiet_day(self) -> None:
        disco = SimpleNamespace(unidad="C:", libre=300 * 1024**3, total=500 * 1024**3, usado_pct=40.0)
        texto = b.componer(datetime(2026, 11, 13, 21, 0), discos=[disco])
        self.assertTrue(texto.startswith("Buenas noches."))
        self.assertIn("No tienes recordatorios para hoy.", texto)
        self.assertIn("El equipo está bien.", texto)
        self.assertNotIn("auriculares", texto)


class AutomaticTests(unittest.TestCase):
    def test_once_a_day_in_the_morning_when_you_use_the_pc(self) -> None:
        manana = datetime(2026, 11, 13, 8, 0)
        self.assertTrue(b.toca_automatico(manana, None, None, 3.0))
        self.assertTrue(b.toca_automatico(manana, "2026-11-12", "1", 3.0))
        self.assertFalse(b.toca_automatico(manana, "2026-11-13", None, 3.0))  # ya salió hoy
        self.assertFalse(b.toca_automatico(manana, None, "0", 3.0))  # apagado
        self.assertFalse(b.toca_automatico(manana, None, None, 600.0))  # no estás en la PC
        self.assertFalse(b.toca_automatico(manana, None, None, None))
        self.assertFalse(b.toca_automatico(datetime(2026, 11, 13, 15, 0), None, None, 3.0))  # por la tarde

    def _host(self, **extra):
        host = Mock(busy=False, recorder=None, _pending_plan_reply=None, _closing=False, **extra)
        host.games_watcher.jugando = set()
        host._MORNING_BRIEF_MS = 60000
        return host

    @patch("jarvis_app.jarvis_buenos_dias.segundos_inactivo", return_value=2.0)
    @patch("jarvis_app.jarvis_buenos_dias.toca_automatico", return_value=True)
    @patch("jarvis_app.core.guardar_preferencia")
    @patch("jarvis_app.core.obtener_preferencia", return_value=None)
    def test_app_runs_it_and_remembers_the_day(self, _obtener, guardar, _toca, _inactivo) -> None:
        host = self._host()
        JarvisApp._check_morning_brief(host)
        host._start_direct_tool_action.assert_called_once_with(
            "morning_briefing", {}, "(resumen automático de la mañana)")
        self.assertEqual(guardar.call_args.args[0], b.PREFERENCIA_ULTIMO)

    @patch("jarvis_app.jarvis_buenos_dias.toca_automatico", return_value=True)
    def test_not_while_playing_or_busy(self, _toca) -> None:
        host = self._host()
        host.games_watcher.jugando = {"Elden Ring"}
        JarvisApp._check_morning_brief(host)
        host.busy = True
        host.games_watcher.jugando = set()
        JarvisApp._check_morning_brief(host)
        host._start_direct_tool_action.assert_not_called()

    @patch("jarvis_app.core.guardar_preferencia")
    def test_voice_toggle_saves_the_preference(self, guardar) -> None:
        texto = JarvisApp._tool_morning_briefing(Mock(), automatic=False)
        guardar.assert_called_once_with(b.PREFERENCIA_ACTIVO, "0")
        self.assertIn("ya no daré el resumen", texto)


class PreloadTests(unittest.TestCase):
    @patch("jarvis_agent.requests.post")
    @patch("jarvis_agent.modelo_cargado", return_value=False)
    def test_loads_with_the_same_context_as_the_chat(self, _cargado, post) -> None:
        self.assertTrue(agent.precargar_modelo())
        cuerpo = post.call_args.kwargs["json"]
        self.assertEqual(cuerpo["options"]["num_ctx"], agent.NUM_CTX)
        self.assertEqual(cuerpo["keep_alive"], agent.KEEP_ALIVE)
        self.assertNotIn("prompt", cuerpo)  # solo carga, no genera

    @patch("jarvis_agent.requests.post")
    @patch("jarvis_agent.modelo_cargado", return_value=True)
    def test_does_nothing_if_already_loaded(self, _cargado, post) -> None:
        self.assertTrue(agent.precargar_modelo())
        post.assert_not_called()

    @patch("jarvis_agent.requests.post")
    @patch("jarvis_agent.modelo_cargado", side_effect=lambda m=None: m == "llm")
    def test_unload_only_what_is_loaded(self, _cargado, post) -> None:
        self.assertEqual(agent.descargar_modelo(["llm", "vision"]), ["llm"])
        self.assertEqual(post.call_args.kwargs["json"], {"model": "llm", "keep_alive": 0})

    @patch("jarvis_agent.requests.get")
    def test_loaded_model_check(self, get) -> None:
        get.return_value.json.return_value = {"models": [{"name": "qwen2.5-coder:14b"}]}
        self.assertTrue(agent.modelo_cargado("qwen2.5-coder:14b"))
        self.assertFalse(agent.modelo_cargado("otro"))

    @patch("jarvis_app.threading.Thread")
    def test_app_throttles_and_skips_while_playing(self, hilo) -> None:
        host = Mock(PRECARGA_INTERVALO=60.0)
        host.games_watcher.jugando = set()
        del host._last_llm_preload
        JarvisApp._preload_llm(host, "micrófono")
        JarvisApp._preload_llm(host, "palabra de activación")  # a los pocos segundos: no repite
        self.assertEqual(hilo.call_count, 1)
        host._last_llm_preload = -1e9
        host.games_watcher.jugando = {"Elden Ring"}
        JarvisApp._preload_llm(host, "micrófono")
        self.assertEqual(hilo.call_count, 1)

    def test_works_with_the_real_game_watcher(self) -> None:
        # Con un Mock, «jugando()» parecía bien; en el vigilante real es una propiedad (fallaba
        # «'set' object is not callable» al decir «Hey Jarvis»).
        import queue

        import jarvis_games_watch

        vigilante = jarvis_games_watch.VigilanteJuegos(queue.Queue())  # antes del patch: crea su hilo
        host = SimpleNamespace(PRECARGA_INTERVALO=60.0, games_watcher=vigilante)
        with patch("jarvis_app.threading.Thread") as hilo:
            JarvisApp._preload_llm(host, "palabra de activación")
            self.assertEqual(hilo.call_count, 1)
            vigilante._confirmado = {"Elden Ring"}
            host._last_llm_preload = -1e9
            JarvisApp._preload_llm(host, "palabra de activación")
            self.assertEqual(hilo.call_count, 1)
        host2 = self._real_host_for_brief(vigilante)
        with patch("jarvis_app.jarvis_buenos_dias.toca_automatico", return_value=True):
            JarvisApp._check_morning_brief(host2)  # jugando: no sale, y no falla
        host2._start_direct_tool_action.assert_not_called()

    @staticmethod
    def _real_host_for_brief(vigilante):
        host = Mock(busy=False, recorder=None, _pending_plan_reply=None, _closing=False, _MORNING_BRIEF_MS=60000)
        host.games_watcher = vigilante
        return host

    def test_never_raises(self) -> None:
        host = SimpleNamespace(PRECARGA_INTERVALO="no es un número")
        JarvisApp._preload_llm(host, "palabra de activación")  # se registra y sigue

    def test_chat_uses_the_same_settings(self) -> None:
        with patch("jarvis_agent._post_con_reintento") as post:
            post.return_value.json.return_value = {"message": {"role": "assistant", "content": "ok"}}
            try:
                agent._request_chat([{"role": "user", "content": "hola"}], [])
            except Exception:
                pass
        cuerpo = post.call_args.args[1]
        self.assertEqual((cuerpo["keep_alive"], cuerpo["options"]["num_ctx"]), (agent.KEEP_ALIVE, agent.NUM_CTX))


if __name__ == "__main__":
    unittest.main()
