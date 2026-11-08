"""Temas: paletas completas y cambio en vivo sin reiniciar (Tk real, oculta)."""

import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import jarvis_theme
import jarvis_ui
from jarvis_commands import detectar_comando_tema
from jarvis_tools import crear_registro_herramientas
from test_ventana_unica import _Vista


def _colores(raiz: tk.Misc) -> set[str]:
    vistos: set[str] = set()
    pendientes = [raiz]
    while pendientes:
        w = pendientes.pop()
        pendientes.extend(w.winfo_children())
        for opcion in ("background", "foreground", "activebackground", "highlightbackground"):
            try:
                valor = str(w.cget(opcion))
            except tk.TclError:
                continue
            if valor.startswith("#"):
                vistos.add(valor.upper())
        if isinstance(w, tk.Canvas):
            for item in w.find_all():
                for opcion in ("fill", "outline"):
                    try:
                        valor = str(w.itemcget(item, opcion))
                    except tk.TclError:
                        continue
                    if valor.startswith("#"):
                        vistos.add(valor.upper())
    return vistos


def _esperar_animaciones(root: tk.Misc) -> None:
    """Deja terminar los fundidos (≈150 ms) para comparar colores finales."""
    fin = time.monotonic() + 0.4
    while time.monotonic() < fin:
        root.update()
        time.sleep(0.02)


class PaletteTests(unittest.TestCase):
    def test_every_theme_defines_every_token_with_unique_colors(self) -> None:
        for nombre, paleta in jarvis_theme.TEMAS.items():
            with self.subTest(tema=nombre):
                self.assertEqual(set(paleta), set(jarvis_theme.TOKENS))
                valores = [v.upper() for v in paleta.values()]
                self.assertEqual(len(valores), len(set(valores)), "colores repetidos")

    def test_unknown_theme_falls_back_to_default(self) -> None:
        self.assertEqual(jarvis_theme.nombre_valido("violeta"), "cian")
        self.assertEqual(jarvis_theme.nombre_valido(None), "cian")

    def test_phrases(self) -> None:
        casos = {
            "Cambia al tema rojo.": "mark",
            "pon el modo claro": "claro",
            "usa el tema cian": "cian",
            "cambia el tema a Iron Man": "mark",
        }
        for texto, tema in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_tema(texto), ("set_theme", {"theme": tema}))
        for texto in ("pon el tema", "cambia el modo de juego", "activa el modo concentración"):
            self.assertIsNone(detectar_comando_tema(texto))


class LiveSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.addCleanup(jarvis_theme.activar, "cian")
        with patch("jarvis_core.obtener_preferencia", return_value=None):
            self.vista = _Vista(self.root, crear_registro_herramientas(Mock()))
        self.vista._add_message("assistant", "Hola")
        self.vista._add_message("user", "Qué tal")
        _esperar_animaciones(self.root)

    def test_switch_recolors_existing_widgets_and_new_drawings(self) -> None:
        cian = {v.upper() for v in jarvis_theme.TEMAS["cian"].values()}
        for nombre in ("mark", "claro", "cian"):
            with self.subTest(tema=nombre):
                mapa = jarvis_theme.activar(nombre)
                jarvis_theme.recolorear(self.root, mapa)
                self.vista._draw_orb()
                _esperar_animaciones(self.root)
                paleta = {v.upper() for v in jarvis_theme.TEMAS[nombre].values()}
                colores = _colores(self.root)
                self.assertTrue(colores, "no se encontró ningún color")
                self.assertLessEqual(colores, paleta, colores - paleta)
                # Las constantes de los módulos también cambian (el orbe se redibuja con ellas).
                self.assertEqual(jarvis_ui.CYAN, jarvis_theme.TEMAS[nombre]["CYAN"])
        self.assertLessEqual(_colores(self.root), cian)


if __name__ == "__main__":
    unittest.main()
