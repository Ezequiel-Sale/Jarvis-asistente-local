"""Ventanas de cualquier programa y página anterior/siguiente/recargar en Brave (sin tocar ventanas reales)."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jarvis_routines
import jarvis_windows as w
from jarvis_commands import detectar_accion_ventana, detectar_comando_ventanas

PROPIO = 999
VENTANAS = [
    w.Ventana(1, "Discord", "discord", 10),
    w.Ventana(2, "Jarvis", "jarvis", PROPIO),
    w.Ventana(3, "General - Discord", "discord", 10),
    w.Ventana(4, "Spotify Premium", "spotify", 20),
    w.Ventana(5, "YouTube - Brave", "brave", 30),
]


class PhraseTests(unittest.TestCase):
    def test_browser_history(self) -> None:
        casos = {
            "página anterior": "back", "vuelve atrás": "back", "atrás": "back", "regresa a la página anterior": "back",
            "página siguiente": "forward", "ve a la página siguiente": "forward",
            "recarga la página": "reload", "refresca": "reload", "F5": "reload", "pulsa F5": "reload",
            "actualiza la página en Brave": "reload",
        }
        for frase, accion in casos.items():
            with self.subTest(frase=frase):
                self.assertEqual(detectar_comando_ventanas(frase), ("browser_history", {"action": accion}))

    def test_window_control(self) -> None:
        casos = {
            "minimiza Discord": ("discord", "window_minimize"),
            "maximiza la ventana de Spotify": ("spotify", "window_maximize"),
            "restaura el explorador": ("explorador", "window_restore"),
            "trae WhatsApp al frente": ("whatsapp", "window_focus"),
            "cambia a la ventana de Discord": ("discord", "window_focus"),
            "muestra el escritorio": ("todo", "window_minimize"),
        }
        for frase, (app, accion) in casos.items():
            with self.subTest(frase=frase):
                self.assertEqual(detectar_comando_ventanas(frase), ("window_control", {"app": app, "action": accion}))

    def test_other_commands_are_not_stolen(self) -> None:
        for frase in ("ve a youtube.com", "muestra la hora", "siguiente", "anterior", "actualiza la app",
                      "agranda la letra", "minimiza Brave", "minimiza la ventana", "maximiza"):
            with self.subTest(frase=frase):
                self.assertIsNone(detectar_comando_ventanas(frase))
        # Brave y Jarvis siguen con sus frases de siempre.
        self.assertEqual(detectar_accion_ventana("minimiza Brave"), "brave_minimize")
        self.assertEqual(detectar_accion_ventana("minimiza la ventana"), "minimize")


class StateTests(unittest.TestCase):
    def _aplicar(self, app, accion):
        hechas = []
        resultado = w.cambiar_estado(app, accion, ventanas=VENTANAS, propio_pid=PROPIO,
                                     mostrar=lambda v, comando: hechas.append((v.hwnd, comando)) or True)
        return resultado, hechas

    def test_all_windows_of_a_program(self) -> None:
        (cuantas, programas), hechas = self._aplicar("discord", "window_minimize")
        self.assertEqual((cuantas, programas), (2, ["discord"]))
        self.assertEqual(hechas, [(1, w.SW_MINIMIZE), (3, w.SW_MINIMIZE)])

    def test_focus_only_the_top_window_and_never_jarvis(self) -> None:
        self.assertEqual(self._aplicar("discord", "window_focus")[1], [(1, None)])
        (cuantas, _), hechas = self._aplicar("todo", "window_minimize")
        self.assertEqual(cuantas, 4)
        self.assertNotIn(2, [h for h, _ in hechas])  # la de Jarvis no se toca

    def test_by_title_and_errors(self) -> None:
        self.assertEqual(self._aplicar("premium", "window_maximize")[1], [(4, w.SW_MAXIMIZE)])
        with self.assertRaisesRegex(w.ErrorVentana, "No encontré"):
            self._aplicar("photoshop", "window_minimize")
        with self.assertRaises(w.ErrorVentana):
            self._aplicar("discord", "cerrar")

    def test_brave_window_choice_and_keys(self) -> None:
        otra = w.Ventana(6, "Google - Brave", "brave", 30)
        self.assertEqual(w.ventana_brave(VENTANAS + [otra], activa=6).hwnd, 6)  # la que está al frente
        self.assertEqual(w.ventana_brave(VENTANAS + [otra], activa=1).hwnd, 5)  # si no, la de más arriba
        self.assertIsNone(w.ventana_brave(VENTANAS[:4], activa=1))
        self.assertEqual(w.TECLAS_NAVEGADOR["back"][1], ("tecla", 0x25, 0, 0))  # Alt + ←
        self.assertEqual(w.TECLAS_NAVEGADOR["reload"][0][1], 0x74)  # F5


class ToolTests(unittest.TestCase):
    def test_messages_and_jarvis_case(self) -> None:
        from jarvis_app import JarvisApp

        host = SimpleNamespace(_tool_manage_window=lambda accion: f"jarvis:{accion}")
        with patch.object(w, "cambiar_estado", return_value=(2, ["discord"])):
            self.assertEqual(JarvisApp._tool_window_control(host, "Discord", "window_minimize"),
                             "Minimicé 2 ventanas de discord.")
        with patch.object(w, "cambiar_estado", return_value=(5, ["a", "b"])):
            self.assertEqual(JarvisApp._tool_window_control(host, "todo", "window_minimize"),
                             "Minimicé todas las ventanas (5).")
        self.assertEqual(JarvisApp._tool_window_control(host, "Jarvis", "window_maximize"), "jarvis:maximize")

    def test_usable_in_routines(self) -> None:
        for nombre in ("window_control", "browser_history"):
            self.assertIn(nombre, jarvis_routines._ROUTINE_ALLOWED_TOOLS)


if __name__ == "__main__":
    unittest.main()


class TranscriptionToleranceTests(unittest.TestCase):
    """Whisper escribe en español los nombres en inglés: Jarvis tiene que entenderlos igual."""

    def test_misheard_program_names(self) -> None:
        abiertos = {"claude", "discord", "spotify", "brave", "whatsapp.root"}
        for oido in ("Cloudy", "clau de", "Clouder", "Cláudel"):
            with self.subTest(oido=oido):
                self.assertEqual(w.programa_parecido(oido, abiertos), "claude")
        self.assertEqual(w.programa_parecido("Spotifai", abiertos), "spotify")
        self.assertIsNone(w.programa_parecido("photoshop", abiertos))
        self.assertIsNone(w.programa_parecido("ab", abiertos))

    def test_ambiguous_names_are_not_guessed(self) -> None:
        self.assertIsNone(w.programa_parecido("Cloudy", {"claude", "klaude"}))  # suenan igual

    def test_choose_uses_similar_name_only_as_last_resort(self) -> None:
        ventanas = VENTANAS + [w.Ventana(7, "Claude", "claude", 40)]
        self.assertEqual([v.hwnd for v in w.elegir(ventanas, "Cloudy", PROPIO)], [7])
        self.assertEqual([v.hwnd for v in w.elegir(ventanas, "discord", PROPIO)], [1, 3])  # exacto primero

    def test_split_verbs_and_reload(self) -> None:
        self.assertEqual(detectar_comando_ventanas("Mini Mesa Claude."),
                         ("window_control", {"app": "claude", "action": "window_minimize"}))
        self.assertEqual(detectar_comando_ventanas("Maxi Mesa Spotify"),
                         ("window_control", {"app": "spotify", "action": "window_maximize"}))
        self.assertEqual(detectar_comando_ventanas("Precarga la página."), ("browser_history", {"action": "reload"}))

    def test_whisper_hint_includes_current_names(self) -> None:
        import jarvis_core as core

        try:
            core.configurar_vocabulario_stt(lambda: ["Claude", "Discord", "Claude", ""])
            pista = core.prompt_stt()
            self.assertTrue(pista.startswith(core.STT_PROMPT))
            self.assertTrue(pista.endswith("Nombres: Claude, Discord."))
            core.configurar_vocabulario_stt(lambda: [f"Programa{n}" for n in range(40)])
            self.assertLessEqual(len(core.prompt_stt()), core.MAX_PISTA_STT)
            core.configurar_vocabulario_stt(lambda: 1 / 0)  # si falla, queda la pista fija
            self.assertEqual(core.prompt_stt(), core.STT_PROMPT)
        finally:
            core.configurar_vocabulario_stt(None)
