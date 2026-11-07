"""Vídeo de un monitor concreto y silenciar pestañas. Sin tocar el navegador real."""

import unittest
from unittest.mock import Mock, patch

import jarvis_media as media
from jarvis_commands import detectar_comando_multimedia
from jarvis_tools import crear_registro_herramientas


class PhraseTests(unittest.TestCase):
    def test_playback_on_a_monitor(self) -> None:
        casos = {
            "Reanuda el video del monitor 2.": ("media_control", {"action": "play", "monitor": 2}),
            "pausa el video de la pantalla uno": ("media_control", {"action": "pause", "monitor": 1}),
            "siguiente canción en el monitor 3": ("media_control", {"action": "next", "monitor": 3}),
            "pausa la música": ("media_control", {"action": "pause"}),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_multimedia(texto), esperado)

    def test_tab_mute(self) -> None:
        casos = {
            "silencia la pestaña": {"mute": True},
            "mutea la pestaña de youtube": {"mute": True, "match": "youtube"},
            "silencia la pestaña del monitor 2": {"mute": True, "monitor": 2},
            "silencia la pestaña que suena": {"mute": True},
            "quita el silencio de la pestaña": {"mute": False},
            "activa el sonido de la pestaña de youtube": {"mute": False, "match": "youtube"},
        }
        for texto, argumentos in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_multimedia(texto), ("mute_browser_tab", argumentos))
        # «silencia Discord» sigue siendo el volumen de una aplicación.
        self.assertEqual(detectar_comando_multimedia("silencia Discord")[0], "set_app_volume")
        self.assertEqual(detectar_comando_multimedia("quita el sonido a discord")[0], "set_app_volume")

    def test_more_ways_to_say_it(self) -> None:
        casos = {
            "dale play al video del segundo monitor": ("media_control", {"action": "play", "monitor": 2}),
            "ponle pausa al video en la pantalla 1": ("media_control", {"action": "pause", "monitor": 1}),
            "quítale la pausa al vídeo del monitor uno": ("media_control", {"action": "play", "monitor": 1}),
            "pausalo": ("media_control", {"action": "pause"}),
            "continúa con el vídeo": ("media_control", {"action": "play"}),
            "silencia el video del monitor 2": ("mute_browser_tab", {"mute": True, "monitor": 2}),
            "mutea youtube": ("mute_browser_tab", {"mute": True, "match": "youtube"}),
            "quítale el sonido a la pestaña que está sonando": ("mute_browser_tab", {"mute": True}),
            "ponle mute al video": ("mute_browser_tab", {"mute": True}),
            "devuélvele el sonido al vídeo": ("mute_browser_tab", {"mute": False}),
            "desmutea la pestaña": ("mute_browser_tab", {"mute": False}),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_multimedia(texto), esperado)


class SequenceTests(unittest.TestCase):
    def test_compound_orders(self) -> None:
        herramienta, argumentos = detectar_comando_multimedia(
            "Pausa el video del monitor 1 y reanuda el del monitor 2."
        )
        self.assertEqual(herramienta, "media_sequence")
        self.assertEqual(argumentos["steps"], [
            {"tool": "media_control", "arguments": {"action": "pause", "monitor": 1}},
            {"tool": "media_control", "arguments": {"action": "play", "monitor": 2}},
        ])
        # Si una parte no es multimedia, decide el modelo como siempre.
        self.assertIsNone(detectar_comando_multimedia("pausa la música y abre Spotify"))

    def test_runs_each_step_in_order_through_the_registry(self) -> None:
        llamadas = []
        with patch.object(media, "controlar_reproduccion", lambda **a: llamadas.append(a) or "ok."):
            registro = crear_registro_herramientas(Mock())
            self.assertNotIn("media_sequence", {t["function"]["name"] for t in registro.ollama_tools()})
            pasos = detectar_comando_multimedia("pausa el video del monitor 1 y reanuda el video del monitor 2")
            resultado = registro.execute(*pasos, confirmed=True, origin="directo")
        self.assertEqual(llamadas, [{"action": "pause", "monitor": 1}, {"action": "play", "monitor": 2}])
        self.assertIn("ok.", str(resultado))


class SessionChoiceTests(unittest.TestCase):
    def test_title_in_a_window_of_that_monitor(self) -> None:
        a, b = object(), object()
        sesiones = [(a, "Mi infancia"), (b, "Gol agónico")]
        ventanas = ["Gol agónico - YouTube - Brave", "Explorador de archivos"]
        self.assertIs(media.elegir_sesion(sesiones, ventanas), b)

    def test_no_guessing_with_a_single_session(self) -> None:
        # Brave expone una sola sesión (la última que sonó), quizá de otro monitor.
        unica = object()
        self.assertIsNone(media.elegir_sesion([(unica, "Podcast")], ["Bloc de notas"]))
        self.assertIsNone(media.elegir_sesion([(unica, "Podcast")], ["Podcast - Brave"], ["Podcast - Brave"]))

    def test_ambiguous_returns_none(self) -> None:
        self.assertIsNone(media.elegir_sesion([(object(), "A"), (object(), "B")], ["Otra cosa"]))


class PlayerButtonTests(unittest.TestCase):
    def test_button_names(self) -> None:
        casos = {
            "Reproducir (k)": "play",
            "Reproducir combinación de teclas k": "play",
            "Pausa combinación de teclas k": "pause",
            "Pause (space/k)": "pause",  # Twitch
            "Siguiente (Mayús+n)": "next",
            "Reproducir todo": None,
            "Silenciar pestaña": None,
        }
        for nombre, esperado in casos.items():
            with self.subTest(nombre=nombre):
                self.assertEqual(media.clasificar_boton(nombre), esperado)


class TabChoiceTests(unittest.TestCase):
    NOMBRES = [
        "Crunchyroll: Parte del grupo caps: Uso de memoria: 238 MB",
        "Partido - YouTube: Reproducción de audio: Uso de memoria: 400 MB",
        "Otro vídeo - YouTube: Uso de memoria: 300 MB",
    ]

    def test_mute_only_the_tab_that_sounds(self) -> None:
        self.assertEqual(media.elegir_pestanas(self.NOMBRES, True), [1])
        self.assertEqual(media.elegir_pestanas(self.NOMBRES, True, "youtube"), [1])

    def test_named_tab_can_be_muted_while_quiet(self) -> None:
        self.assertEqual(media.elegir_pestanas(self.NOMBRES[:1] + self.NOMBRES[2:], True, "youtube"), [1])
        self.assertEqual(media.elegir_pestanas(self.NOMBRES[2:], True), [])

    def test_unmute_prefers_muted_tabs(self) -> None:
        nombres = ["Twitch: Audio silenciado: Uso de memoria: 583 MB", "Otra: Uso de memoria: 1 MB"]
        self.assertEqual(media.elegir_pestanas(nombres, False), [0])
        self.assertEqual(media.elegir_pestanas(nombres[1:], False), [0])


class TabMenuTests(unittest.TestCase):
    def test_menu_options(self) -> None:
        self.assertTrue(media._opcion_menu("Silenciar pestaña", True))
        self.assertFalse(media._opcion_menu("Silenciar sitio", True))  # solo la pestaña
        self.assertTrue(media._opcion_menu("Activar sonido de la pestaña", False))
        self.assertFalse(media._opcion_menu("Silenciar pestaña", False))

    def test_tool_is_registered_low_risk(self) -> None:
        herramienta = crear_registro_herramientas(Mock()).get("mute_browser_tab")
        self.assertFalse(herramienta.requires_confirmation)


if __name__ == "__main__":
    unittest.main()
