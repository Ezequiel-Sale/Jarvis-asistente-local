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
            "pon el tema futurista": "nebula",
            "cambia al tema Nébula": "nebula",
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
        for nombre in ("mark", "nebula", "claro", "cian"):
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



class EstiloNebulaTests(unittest.TestCase):
    """2026-11-23: «Nébula» cambia íconos, botones y núcleo; al volver a otro tema, todo como antes."""

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
        self.vista._show_page("chat")
        _esperar_animaciones(self.root)

    def cambiar(self, nombre: str) -> None:
        mapa = jarvis_theme.activar(nombre)
        jarvis_theme.recolorear(self.root, mapa)
        self.vista._show_page(self.vista._current_page)
        self.vista._aplicar_estilo_tema()
        _esperar_animaciones(self.root)

    def foto(self) -> dict:
        botones = [w for w in _todos(self.root) if isinstance(w, tk.Button)]
        return {
            "barra": {k: (b.cget("text"), str(b.cget("image"))) for k, b in self.vista._nav_buttons.items()},
            "mic": (self.vista.mic_button.cget("text"), str(self.vista.mic_button.cget("image"))),
            "enviar": (self.vista.send_button.cget("text"), str(self.vista.send_button.cget("image"))),
            "fuentes": [str(b.cget("font")) for b in botones],
        }

    def test_nebula_and_back_leaves_everything_as_before(self) -> None:
        antes = self.foto()
        self.cambiar("nebula")
        self.assertEqual(self.vista._nav_buttons["chat"].cget("text"), "  CHAT")
        self.assertTrue(str(self.vista._nav_buttons["chat"].cget("image")))
        self.assertEqual(self.vista.mic_button.cget("text"), "  MIC")
        self.assertIn("Bahnschrift", str(self.vista.send_button.cget("font")))
        paleta = {v.upper() for v in jarvis_theme.TEMAS["nebula"].values()}
        self.assertLessEqual(_colores(self.root), paleta, _colores(self.root) - paleta)
        for otro in ("mark", "claro", "cian"):
            with self.subTest(tema=otro):
                self.cambiar("nebula")
                self.cambiar(otro)
                ahora = self.foto()
                self.assertEqual(ahora["barra"], {k: (t, "") for k, (t, _i) in antes["barra"].items()})
                self.assertEqual(ahora["mic"], antes["mic"])
                self.assertEqual(ahora["enviar"], antes["enviar"])
                self.assertEqual(ahora["fuentes"], antes["fuentes"])
                paleta = {v.upper() for v in jarvis_theme.TEMAS[otro].values()}
                self.assertLessEqual(_colores(self.root), paleta, _colores(self.root) - paleta)

    def test_hexagonal_core_only_in_nebula(self) -> None:
        def poligonos() -> int:
            self.vista._draw_orb_on(self.vista.orb_canvas)
            return sum(1 for i in self.vista.orb_canvas.find_withtag("orb")
                       if self.vista.orb_canvas.type(i) == "polygon")

        self.vista.orb_canvas.configure(width=220, height=210)
        self.root.deiconify()
        self.root.update()
        self.assertEqual(poligonos(), 0)
        self.cambiar("nebula")
        self.assertEqual(poligonos(), 2)
        self.cambiar("cian")
        self.assertEqual(poligonos(), 0)

    def test_icons_draw_for_every_section(self) -> None:
        import jarvis_iconos

        for clave in self.vista._nav_buttons:
            self.assertIn(clave, jarvis_iconos.FIGURAS)
        imagen = jarvis_iconos.imagen("chat", "#3D8BFF", 20)
        self.assertEqual(imagen.size, (20, 20))
        self.assertGreater(sum(imagen.getchannel("A").histogram()[1:]), 20)  # tiene trazos


def _todos(raiz: tk.Misc) -> list:
    salida, pendientes = [], [raiz]
    while pendientes:
        w = pendientes.pop()
        salida.append(w)
        pendientes.extend(w.winfo_children())
    return salida


if __name__ == "__main__":
    unittest.main()
