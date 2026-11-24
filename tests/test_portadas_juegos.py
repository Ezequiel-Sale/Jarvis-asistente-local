"""Juegos en tarjetas con portada (2026-11-23). Sin internet: Steam se simula; carpeta temporal."""

import io
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jarvis_portadas as p
import jarvis_ui_juegos as ui


def _jpg(color=(40, 80, 160), tamano=(460, 215)) -> bytes:
    from PIL import Image

    salida = io.BytesIO()
    Image.new("RGB", tamano, color).save(salida, "JPEG")
    return salida.getvalue()


class _Http:
    def __init__(self, items=(), imagen=b"", tipo="image/jpeg"):
        self.items, self.imagen, self.tipo, self.pedidos = list(items), imagen, tipo, []

    def get(self, url, params=None, timeout=None):
        self.pedidos.append(url)
        if "storesearch" in url:
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"items": self.items})
        return SimpleNamespace(status_code=200, headers={"content-type": self.tipo}, content=self.imagen)


class PortadasTests(unittest.TestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.dir = Path(carpeta.name)
        parche = patch.object(p, "carpeta", lambda: self.dir)
        parche.start()
        self.addCleanup(parche.stop)

    def test_only_a_close_match_is_accepted(self):
        items = [{"id": 1, "name": "ELDEN RING NIGHTREIGN"}, {"id": 2, "name": "ELDEN RING"}]
        self.assertEqual(p.elegir_resultado("Elden Ring", items), 2)
        self.assertIsNone(p.elegir_resultado("7dsgb", [{"id": 9, "name": "The Seven Deadly Sins: Grand Cross"}]))

    def test_downloads_once_and_remembers_misses(self):
        http = _Http([{"id": 1245620, "name": "ELDEN RING"}], _jpg())
        archivo = p.obtener("Elden Ring", http=http)
        self.assertEqual(archivo, self.dir / "elden-ring.jpg")
        from PIL import Image

        with Image.open(archivo) as imagen:
            self.assertEqual(imagen.size, (460, 215))
        pedidos = len(http.pedidos)
        p.obtener("Elden Ring", http=http)
        self.assertEqual(len(http.pedidos), pedidos)  # ya estaba: no vuelve a buscar
        nada = _Http([])
        self.assertIsNone(p.obtener("7dsgb", http=nada))
        self.assertTrue(p.sin_resultado("7dsgb"))
        self.assertFalse(p.hay_que_buscar("7dsgb"))
        self.assertTrue(p.hay_que_buscar("7dsgb", ahora=time.time() + 8 * 86400))  # a la semana, otra vez

    def test_not_an_image_is_not_saved(self):
        http = _Http([{"id": 5, "name": "Clone Hero"}], b"<html>error</html>", tipo="text/html")
        self.assertIsNone(p.obtener("Clone Hero", http=http))
        self.assertIsNone(p.ruta("Clone Hero"))

    def test_steam_manifest_gives_the_app_id(self):
        steamapps = self.dir / "steamapps"
        (steamapps / "common" / "Left 4 Dead 2").mkdir(parents=True)
        (steamapps / "appmanifest_550.acf").write_text('"AppState"\n{\n\t"appid"\t\t"550"\n\t"installdir"\t\t"Left 4 Dead 2"\n}',
                                                      encoding="utf-8")
        self.assertEqual(p.appid_steam(steamapps / "common" / "Left 4 Dead 2"), 550)
        self.assertIsNone(p.appid_steam(self.dir / "Games" / "Elden Ring"))

    def test_manual_cover_is_cropped_and_kept(self):
        origen = self.dir / "mia.png"
        from PIL import Image

        Image.new("RGB", (800, 800), (200, 30, 30)).save(origen)
        destino = p.guardar_manual("7dsgb", origen)
        with Image.open(destino) as imagen:
            self.assertEqual(imagen.size, (460, 215))
        self.assertFalse(p.hay_que_buscar("7dsgb"))
        with self.assertRaises(ValueError):
            texto = self.dir / "no.txt"
            texto.write_text("hola", encoding="utf-8")
            p.guardar_manual("x", texto)
        p.olvidar("7dsgb")
        self.assertIsNone(p.ruta("7dsgb"))


class TarjetasTests(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(ui.iniciales("Call of Duty Modern Warfare"), "COD")
        self.assertEqual(ui.iniciales("7dsgb"), "7DS")
        self.assertEqual(ui.recortar("Call of Duty Modern Warfare 4 2026"), "Call of Duty Modern Warfa…")
        self.assertEqual(ui.columnas_para(1200), 5)  # tarjetas de 228 px + 14 de separación
        self.assertEqual(ui.columnas_para(200), 1)
        juego = SimpleNamespace(carpeta=Path(r"C:\Steam\steamapps\common\Left 4 Dead 2"),
                                ejecutable=Path("left4dead2.exe"))
        self.assertEqual(ui.donde(juego), "Steam · left4dead2.exe")

    def test_cards_with_cover_or_upload_button(self):
        from jarvis_tools import crear_registro_herramientas
        from test_ventana_unica import _Vista

        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        directorio = Path(carpeta.name)
        (directorio / "elden-ring.jpg").write_bytes(_jpg())
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.addCleanup(root.destroy)
        with patch.object(p, "carpeta", lambda: directorio), \
                patch.object(p, "hay_que_buscar", return_value=False), \
                patch("jarvis_core.obtener_preferencia", return_value=None):
            vista = _Vista(root, crear_registro_herramientas(Mock()))
            vista._game_actions = lambda _n: []
            vista._play_game = Mock()
            juegos = [SimpleNamespace(nombre="Elden Ring", carpeta=Path(r"D:\Games\Elden Ring"),
                                      ejecutable=Path("eldenring.exe"), problema=None),
                      SimpleNamespace(nombre="7dsgb", carpeta=Path(r"D:\Games\7dsgb"),
                                      ejecutable=Path("7dsgc.exe"), problema=None)]
            vista._render_games(juegos)
            root.update()
            textos, imagenes, botones = [], 0, []
            pendientes = [vista.games_list]
            while pendientes:
                w = pendientes.pop()
                pendientes.extend(w.winfo_children())
                if isinstance(w, (tk.Label, tk.Button)):
                    textos.append(str(w.cget("text")))
                    imagenes += bool(str(w.cget("image")))
                if isinstance(w, tk.Button):
                    botones.append(w)
            self.assertEqual(imagenes, 1)  # Elden Ring con su portada
            self.assertIn("＋ SUBIR PORTADA", textos)  # 7dsgb sin portada
            self.assertEqual(vista.games_covers.cget("text"), "PORTADAS · 1 DE 2")
            next(b for b in botones if b.cget("text") == "▶  JUGAR").invoke()
            vista._play_game.assert_called_once()
            vista.games_filter.set("elden")
            root.update()
            rejilla = vista.games_list.winfo_children()[0]
            self.assertEqual(len(rejilla.winfo_children()), 1)  # solo la tarjeta de Elden Ring


if __name__ == "__main__":
    unittest.main()
