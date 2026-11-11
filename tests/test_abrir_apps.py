"""«Abre WhatsApp»: apps del menú Inicio por su nombre, también de la Tienda (sin abrir nada real)."""

import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jarvis_routines
import jarvis_system as s
from jarvis_commands import detectar_comando_juego, limpiar_transcripcion_voz

APPS = [
    ("WhatsApp", "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"),
    ("Claude", "Claude_pzs8sxrjxfjjc!Claude"),
    ("Discord", "com.squirrel.Discord.Discord"),
    ("Bloc de notas", r"{1AC14E77}\notepad.exe"),
    ("Visual Studio Code", "Microsoft.VisualStudioCode"),
]


class LookupTests(unittest.TestCase):
    def test_names_as_people_say_them(self) -> None:
        casos = {"WhatsApp": "WhatsApp", "whatsapp": "WhatsApp", "la app de WhatsApp": "WhatsApp",
                 "guasap": "WhatsApp", "Wasap": "WhatsApp", "Cloudy": "Claude", "bloc de notas": "Bloc de notas",
                 "visual studio": "Visual Studio Code", "code": "Visual Studio Code"}
        for dicho, app in casos.items():
            with self.subTest(dicho=dicho):
                self.assertEqual(s.buscar_app(dicho, APPS)[0], app)
        for dicho in ("photoshop", "x", ""):
            self.assertIsNone(s.buscar_app(dicho, APPS))

    def test_web_links_and_uninstallers_are_skipped(self) -> None:
        leidas = APPS + [("Steam Support Center", "http://support.steampowered.com/"),
                         ("Desinstalar Brave", r"{7C5A}\uninstall.exe"), ("Ayuda de OBS", "obs-help")]
        apps = s.apps_instaladas(leer=lambda: leidas)
        self.assertEqual([n for n, _ in apps], [n for n, _ in APPS])

    def test_open_uses_the_store_id(self) -> None:
        with patch.object(s, "apps_instaladas", return_value=APPS), patch.object(s.os, "startfile", create=True) as abrir:
            self.assertEqual(s.abrir_app("guasap"), "Abrí WhatsApp.")
            abrir.assert_called_once_with(r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App")
            with self.assertRaisesRegex(FileNotFoundError, "menú Inicio"):
                s.abrir_app("photoshop")


class PhraseTests(unittest.TestCase):
    def test_open_phrases(self) -> None:
        for frase in ("¡Abre WhatsApp!", "¿Abre WhatsApp?", "Abre WhatsApp.", limpiar_transcripcion_voz("Initial WhatsApp.")):
            with self.subTest(frase=frase):
                self.assertEqual(detectar_comando_juego(frase), "WhatsApp")

    def test_games_first_then_apps(self) -> None:
        from jarvis_app import JarvisApp

        def decidir(es_juego, nombre="WhatsApp"):
            host = SimpleNamespace(events=queue.Queue())
            with patch("jarvis_app.localizar_ejecutable_juego",
                       side_effect=None if es_juego else FileNotFoundError("no es juego")), \
                    patch.object(s, "apps_instaladas", return_value=APPS), \
                    patch("threading.Thread", side_effect=lambda target, **_k: SimpleNamespace(start=target)):
                JarvisApp._execute_game_action(host, nombre, "abre " + nombre)
            return host.events.get_nowait()[1:3]

        self.assertEqual(decidir(es_juego=True, nombre="Hades"), ("launch_game", {"name": "Hades"}))
        self.assertEqual(decidir(es_juego=False), ("open_app", {"name": "WhatsApp"}))
        self.assertEqual(decidir(es_juego=False, nombre="Photoshop"), ("launch_game", {"name": "Photoshop"}))

    def test_usable_in_routines(self) -> None:
        self.assertIn("open_app", jarvis_routines._ROUTINE_ALLOWED_TOOLS)


if __name__ == "__main__":
    unittest.main()
