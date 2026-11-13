"""Juegos en las carpetas «Games» de todos los discos y elección del ejecutable (carpetas temporales)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jarvis_system as s


def _exe(ruta: Path) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(b"MZ")
    return ruta


class FoldersTests(unittest.TestCase):
    def test_games_folders_on_every_disk(self) -> None:
        disco_d, disco_e = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (disco_d / "Games").mkdir()
        (disco_d / "Epic Games").mkdir()
        (disco_d / "SteamLibrary" / "steamapps" / "common").mkdir(parents=True)
        (disco_e / "Games").mkdir()
        usuario = Path(tempfile.mkdtemp())  # sin «Games»: no se usa la carpeta real del usuario
        with patch.object(s, "GAME_DIRECTORIES", ()), patch.object(s.Path, "home", return_value=usuario):
            carpetas = s.carpetas_juegos(discos=[disco_d, disco_e], steam=[])
        self.assertEqual(carpetas, [disco_d / "Games", disco_d / "Epic Games",
                                    disco_d / "SteamLibrary" / "steamapps" / "common", disco_e / "Games"])

    def test_games_folder_in_the_user_profile(self) -> None:
        # C:\Users\…\Games (p. ej. con un emulador): la usan el lanzador y el modo juego.
        usuario = Path(tempfile.mkdtemp())
        _exe(usuario / "Games" / "Hookshot" / "hookshot.exe")
        with patch.object(s, "GAME_DIRECTORIES", ()), patch.object(s.Path, "home", return_value=usuario):
            self.assertEqual(s.carpetas_juegos(discos=[], steam=[]), [usuario / "Games"])

    def test_same_game_twice_uses_the_first_place(self) -> None:
        primera, segunda = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        _exe(primera / "GarrysMod" / "gmod.exe")
        _exe(segunda / "GarrysMod" / "gmod.exe")
        _exe(segunda / "Luto" / "Luto.exe")
        with patch.object(s, "carpetas_juegos", return_value=[primera, segunda]):
            self.assertEqual(s.localizar_ejecutable_juego("garrys mod")[1], primera / "GarrysMod" / "gmod.exe")
            self.assertEqual(s.localizar_ejecutable_juego("Luto")[1], segunda / "Luto" / "Luto.exe")


class ExecutableTests(unittest.TestCase):
    def _elegir(self, carpeta: str, nombres: list[str]) -> str | None:
        elegido = s._ejecutable_principal(carpeta, [Path(f"X:/{carpeta}/{n}") for n in nombres])
        return elegido.name if elegido else None

    def test_main_executable_among_several(self) -> None:
        casos = [
            ("Elden Ring", ["ELDEN RING Adventure Guide.exe", "ELDEN RING Digital Artbook & Soundtrack.exe",
                            "eldenring.exe", "start_protected_game.exe"], "eldenring.exe"),
            ("rocketleague", ["Launcher.exe", "RocketLeague.exe", "RocketLeague_EAC.exe"], "RocketLeague.exe"),
            ("Pro Evolution Soccer 2018", ["PES2018 DpFileList Generator 1.4.exe", "PES2018.exe", "Settings.exe"],
             "PES2018.exe"),
            ("Luto", ["Luto-Win64-Shipping.exe", "Luto.exe"], "Luto.exe"),
        ]
        for carpeta, nombres, esperado in casos:
            with self.subTest(carpeta=carpeta):
                self.assertEqual(self._elegir(carpeta, nombres), esperado)

    def test_no_clear_choice_is_not_guessed(self) -> None:
        self.assertIsNone(self._elegir("Algo", ["uno.exe", "dos.exe"]))

    def test_initials(self) -> None:
        self.assertEqual(s._iniciales("Pro Evolution Soccer 2018"), "pes2018")


class GamesListTests(unittest.TestCase):
    def test_list_with_executables_problems_and_duplicates(self) -> None:
        primera, segunda = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        _exe(primera / "Hades" / "Hades.exe")
        _exe(primera / "Elden Ring" / "eldenring.exe")
        _exe(primera / "Elden Ring" / "ELDEN RING Adventure Guide.exe")
        (primera / "__MACOSX").mkdir()
        _exe(segunda / "Hades" / "Hades.exe")  # repetido: queda el primero
        _exe(segunda / "Paquete" / "Juego update 1.2.exe")  # solo una actualización
        juegos = s.listar_juegos([primera, segunda])
        self.assertEqual([(j.nombre, j.ejecutable.name if j.ejecutable else None) for j in juegos],
                         [("Elden Ring", "eldenring.exe"), ("Hades", "Hades.exe"), ("Paquete", None)])
        self.assertEqual(juegos[1].carpeta.parent, primera)
        self.assertIn("No encontré un ejecutable", juegos[2].problema)


class GamesPageTests(unittest.TestCase):
    def test_page_lists_filters_and_plays(self) -> None:
        import queue
        import tkinter as tk

        from jarvis_app import JarvisApp

        try:
            raiz = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk no disponible: {error}")
        raiz.withdraw()
        self.addCleanup(raiz.destroy)
        jugados = []

        class Host:
            _build_games_page = JarvisApp._build_games_page
            _render_games = JarvisApp._render_games
            _play_game = JarvisApp._play_game
            _ui_button = JarvisApp._ui_button
            busy = False
            events = queue.Queue()

            def _start_direct_tool_action(self, herramienta, argumentos, orden):
                jugados.append((herramienta, argumentos, orden))

            def _set_status(self, *_):
                pass

            def _load_games_page(self):
                pass

        host = Host()
        pagina = tk.Frame(raiz)
        host._build_games_page(pagina)
        juegos = [s.JuegoEncontrado("Hades", Path("D:/Games/Hades"), Path("D:/Games/Hades/Hades.exe")),
                  s.JuegoEncontrado("Elden Ring", Path("D:/Games/Elden Ring"), Path("D:/Games/Elden Ring/eldenring.exe")),
                  s.JuegoEncontrado("Roto", Path("E:/Games/Roto"), None, "Sin ejecutable.")]
        host._render_games(juegos)
        self.assertIn("2 juegos reconocidos", host.games_summary.cget("text"))
        botones = [w for fila in host.games_list.winfo_children() for w in fila.winfo_children()
                   if isinstance(w, tk.Button)]
        self.assertEqual(len(botones), 2)
        botones[0].invoke()
        self.assertEqual(jugados, [("launch_game", {"name": "Hades"}, "abre Hades")])
        host.games_filter.set("elden")
        filas = [f for f in host.games_list.winfo_children() if isinstance(f, tk.Frame)]
        self.assertEqual(len(filas), 1)


if __name__ == "__main__":
    unittest.main()
