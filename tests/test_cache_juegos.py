"""Caché y búsqueda en paralelo de la lista de juegos (2026-11-23). Carpetas temporales; la caché
va a un archivo temporal, nunca a la de Jarvis."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import jarvis_system as s


class CacheJuegosTests(unittest.TestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        base = Path(carpeta.name)
        self.c, self.d = base / "C" / "Games", base / "D" / "Games"
        for raiz, juegos in ((self.c, ("Elden Ring", "Hades")), (self.d, ("Hades", "Clone Hero", "Vacio"))):
            for juego in juegos:
                (raiz / juego).mkdir(parents=True)
                if juego != "Vacio":
                    (raiz / juego / f"{juego.replace(' ', '')}.exe").write_bytes(b"MZ")
        self.cache = base / "juegos_cache.json"
        for parche in (patch.object(s, "ruta_cache_juegos", lambda: self.cache),
                       patch.object(s, "carpetas_juegos", lambda: [self.c, self.d])):
            parche.start()
            self.addCleanup(parche.stop)
        self.recorridos = []
        original = s._elegir_ejecutable_juego

        def contar(carpeta):
            self.recorridos.append(carpeta.name)
            return original(carpeta)

        parche = patch.object(s, "_elegir_ejecutable_juego", side_effect=contar)
        parche.start()
        self.addCleanup(parche.stop)

    def _nombres(self, juegos):
        return [(j.nombre, j.carpeta.parent.parent.name, j.ejecutable is not None) for j in juegos]

    def test_second_listing_uses_the_cache(self):
        self.assertIsNone(s.juegos_guardados())
        primera = s.listar_juegos()
        # Mismo orden que antes: con .exe primero, por nombre; «Hades» una vez, la de C:.
        self.assertEqual(self._nombres(primera), [("Clone Hero", "D", True), ("Elden Ring", "C", True),
                                                  ("Hades", "C", True), ("Vacio", "D", False)])
        self.assertEqual(sorted(self.recorridos), ["Clone Hero", "Elden Ring", "Hades", "Vacio"])
        self.recorridos.clear()
        self.assertEqual(s.listar_juegos(), primera)
        self.assertEqual(self.recorridos, [])  # nada se volvió a recorrer
        self.assertEqual(s.juegos_guardados(), primera)  # sin tocar los discos

    def test_changed_folder_or_missing_exe_is_scanned_again(self):
        s.listar_juegos()
        self.recorridos.clear()
        hades = self.c / "Hades"
        estado = hades.stat()
        os.utime(hades, (estado.st_atime, estado.st_mtime + 10))  # se instaló o borró algo adentro
        (self.c / "Elden Ring" / "EldenRing.exe").unlink()
        juegos = s.listar_juegos()
        self.assertEqual(sorted(self.recorridos), ["Elden Ring", "Hades"])
        elden = next(j for j in juegos if j.nombre == "Elden Ring")
        self.assertIsNone(elden.ejecutable)
        self.assertIn("No encontré", elden.problema)

    def test_forced_refresh_scans_everything_and_bad_cache_is_ignored(self):
        s.listar_juegos()
        self.recorridos.clear()
        s.listar_juegos(usar_cache=False)
        self.assertEqual(len(self.recorridos), 4)
        self.cache.write_text("{roto", encoding="utf-8")
        self.recorridos.clear()
        self.assertEqual(len(s.listar_juegos()), 4)
        self.assertEqual(len(self.recorridos), 4)
        self.assertIsNotNone(s.juegos_guardados())  # se volvió a guardar bien

    def test_explicit_roots_never_touch_the_cache(self):
        s.listar_juegos([self.c])
        self.assertFalse(self.cache.exists())

    def test_launcher_reuses_the_saved_exe(self):
        s.listar_juegos()
        self.recorridos.clear()
        nombre, ejecutable = s.localizar_ejecutable_juego("elden ring")
        self.assertEqual((nombre, ejecutable.name), ("Elden Ring", "EldenRing.exe"))
        self.assertEqual(self.recorridos, [])  # salió de juegos_cache.json
        (self.c / "Elden Ring" / "EldenRing.exe").unlink()
        (self.c / "Elden Ring" / "Nuevo.exe").write_bytes(b"MZ")
        self.assertEqual(s.localizar_ejecutable_juego("elden ring")[1].name, "Nuevo.exe")
        self.assertEqual(self.recorridos, ["Elden Ring"])  # el guardado ya no existe: se buscó


class AppsInicioTests(unittest.TestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.archivo = Path(carpeta.name) / "apps_inicio.json"
        for parche in (patch.object(s, "ruta_cache_apps", lambda: self.archivo),
                       patch.object(s, "_cache_apps", None)):
            parche.start()
            self.addCleanup(parche.stop)

    def test_saved_list_is_used_after_restart_and_refreshed_in_background(self):
        lecturas = []

        def leer():
            lecturas.append(1)
            return [("WhatsApp", "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"), ("Claude", "Claude")]

        with patch.object(s, "_leer_apps_inicio", side_effect=leer):
            self.assertEqual(len(s.apps_instaladas()), 2)  # primera vez: se lee y se guarda
            self.assertTrue(self.archivo.exists())
            s._cache_apps = None  # «reinicio» de Jarvis
            self.assertEqual(len(s.apps_instaladas()), 2)
            self.assertEqual(len(lecturas), 1)  # salió del disco, sin PowerShell
            s._cache_apps = None
            datos = self.archivo.read_text(encoding="utf-8").replace('"cuando": ', '"cuando": 1, "x": ')
            self.archivo.write_text(datos, encoding="utf-8")  # una lista vieja
            with patch.object(s.threading, "Thread") as hilo:
                self.assertEqual(len(s.apps_instaladas()), 2)  # se usa igual, al instante
            hilo.return_value.start.assert_called_once()  # y se actualiza en otro hilo

    def test_open_app_rereads_when_the_saved_list_misses_it(self):
        listas = iter([[("Claude", "Claude")], [("Claude", "Claude"), ("Discord", "Discord.exe")]])
        with patch.object(s, "apps_instaladas", side_effect=lambda **_k: next(listas)),                 patch.object(s.os, "startfile", create=True) as abrir:
            self.assertEqual(s.abrir_app("Discord"), "Abrí Discord.")
        abrir.assert_called_once_with(r"shell:AppsFolder\Discord.exe")

    def test_matplotlib_folder_is_fixed_only_when_packaged(self):
        import jarvis_core

        self.assertEqual(jarvis_core.CARPETA_MATPLOTLIB, jarvis_core.APP_DIR / "cache" / "matplotlib")


class CargaInstantaneaTests(unittest.TestCase):
    def _host(self):
        import queue
        from types import SimpleNamespace
        from unittest.mock import Mock

        from jarvis_app import JarvisApp

        host = SimpleNamespace(_games=None, events=queue.Queue(), games_summary=Mock())
        host._load_games_page = JarvisApp._load_games_page.__get__(host)
        return host

    def _eventos(self, host, esperados):
        return [host.events.get(timeout=5) for _ in range(esperados)]

    def test_saved_list_first_then_only_if_changed(self):
        guardada = [s.JuegoEncontrado("Hades", Path(r"C:\Games\Hades"), Path("Hades.exe"))]
        nueva = guardada + [s.JuegoEncontrado("Clone Hero", Path(r"D:\Games\Clone Hero"), Path("ch.exe"))]
        host = self._host()
        with patch.object(s, "juegos_guardados", return_value=guardada), \
                patch.object(s, "listar_juegos", return_value=guardada) as listar:
            host._load_games_page()
            self.assertEqual(self._eventos(host, 1), [("games_list", guardada)])
            for _ in range(50):
                if listar.called:
                    break
                time.sleep(0.05)
        time.sleep(0.2)
        self.assertTrue(host.events.empty())  # la revisada era igual: no se redibuja
        host = self._host()
        with patch.object(s, "juegos_guardados", return_value=guardada), \
                patch.object(s, "listar_juegos", return_value=nueva):
            host._load_games_page()
            self.assertEqual(self._eventos(host, 2), [("games_list", guardada), ("games_list", nueva)])

    def test_refresh_button_skips_the_cache(self):
        host = self._host()
        host._games = []
        with patch.object(s, "juegos_guardados") as guardados, patch.object(s, "listar_juegos", return_value=[]) as listar:
            host._load_games_page(forzar=True)
            self.assertEqual(self._eventos(host, 1), [("games_list", [])])
        guardados.assert_not_called()
        listar.assert_called_once_with(usar_cache=False)


if __name__ == "__main__":
    unittest.main()
