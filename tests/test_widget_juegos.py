"""Widget de juegos en el escritorio (2026-11-23). Sin base de datos real: preferencias en memoria;
la ventana se abre fuera de la pantalla y nunca se manda al fondo de verdad."""

import io
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jarvis_portadas
import jarvis_widget_juegos as w
from jarvis_commands import detectar_comando_widget_juegos


def _juego(nombre, carpeta=None, exe="juego.exe"):
    return SimpleNamespace(nombre=nombre, carpeta=Path(carpeta or rf"D:\Games\{nombre}"),
                           ejecutable=Path(exe) if exe else None, problema=None if exe else "sin .exe")


class DeteccionTests(unittest.TestCase):
    def test_voice_orders(self):
        self.assertEqual(detectar_comando_widget_juegos("Mostrá el widget de juegos"),
                         ("games_widget", {"visible": True}))
        self.assertEqual(detectar_comando_widget_juegos("poné el widget de juegos en el escritorio"),
                         ("games_widget", {"visible": True}))
        self.assertEqual(detectar_comando_widget_juegos("Jarvis, ocultá el widget de los juegos"),
                         ("games_widget", {"visible": False}))
        self.assertEqual(detectar_comando_widget_juegos("cerrá el widget de juegos"),
                         ("games_widget", {"visible": False}))
        self.assertIsNone(detectar_comando_widget_juegos("abrí juegos"))
        self.assertIsNone(detectar_comando_widget_juegos("mostrá el widget del clima"))

    def test_tool_is_registered(self):
        from jarvis_tools import crear_registro_herramientas

        definicion = crear_registro_herramientas(Mock()).get("games_widget")
        self.assertEqual(definicion.display_name, "Widget de juegos")
        self.assertEqual(definicion.timeout_seconds, 20)


class AyudantesTests(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(w.posicion_de("120,-40"), (120, -40))
        self.assertIsNone(w.posicion_de(None))
        self.assertIsNone(w.posicion_de("arriba"))
        juegos = [_juego("Elden Ring"), _juego("Clone Hero"), _juego("Carpeta rara", exe=None)]
        self.assertEqual([j.nombre for j in w.filtrar(juegos, "")], ["Elden Ring", "Clone Hero"])
        self.assertEqual([j.nombre for j in w.filtrar(juegos, " clone ")], ["Clone Hero"])
        self.assertEqual(w.tamano_de("520x700"), (520, 700))
        self.assertEqual(w.tamano_de("100x9000"), (w.MINIMO[0], w.MAXIMO[1]))
        self.assertEqual(w.tamano_de("grande"), (w.ANCHO, w.ALTO))
        self.assertEqual(w.columnas_para(w.ANCHO), 2)
        self.assertEqual(w.columnas_para(w.MINIMO[0]), 1)
        self.assertEqual(w.columnas_para(900), 5)
        self.assertEqual(w.transparencia_de("75"), 75)
        self.assertEqual(w.transparencia_de("62,4"), 62)
        self.assertEqual(w.transparencia_de(140), 100)
        self.assertEqual(w.transparencia_de(None), 0)
        self.assertEqual(w.color_clave("#05070C"), "#05070D")
        self.assertEqual(w.color_clave("#EEF3FF"), "#EEF3FE")
        l4d = _juego("Left 4 Dead 2", r"C:\Steam\steamapps\common\Left 4 Dead 2")
        self.assertTrue(w.abierto(l4d, {"left 4 dead 2"}))
        self.assertFalse(w.abierto(l4d, {"Elden Ring"}))


class _App:
    def __init__(self, root, juegos, acciones):
        self.root = root
        self._games = juegos
        self.busy = False
        self.acciones = acciones
        self._play_game = Mock()
        self._edit_game_actions = Mock()
        self._render_games = Mock()
        self._load_games_page = Mock()
        self._show_from_tray = Mock()
        self._show_page = Mock()
        self._set_games_widget = Mock()
        self.tool_registry = None
        self.games_watcher = SimpleNamespace(jugando={"Clone Hero"})

    def _game_actions(self, nombre):
        return [dict(p) for p in self.acciones.get(nombre.casefold(), [])]


class WidgetTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.dir = Path(carpeta.name)
        from PIL import Image

        salida = io.BytesIO()
        Image.new("RGB", (460, 215), (40, 80, 160)).save(salida, "JPEG")
        (self.dir / "elden-ring.jpg").write_bytes(salida.getvalue())
        self.prefs = {w.PREF_POSICION: "-4000,-4000"}  # fuera de la pantalla
        self.acciones = {"elden ring": [{"tool": "set_system_volume", "arguments": {"percentage": 30}},
                                        {"tool": "media_control", "arguments": {"action": "pause"}}]}
        for parche in (patch.object(jarvis_portadas, "carpeta", lambda: self.dir),
                       patch("jarvis_core.obtener_preferencia", side_effect=self.prefs.get),
                       patch("jarvis_core.guardar_preferencia", side_effect=self.prefs.__setitem__),
                       patch.object(w, "_en_pantalla", return_value=True),
                       patch.object(w.WidgetJuegos, "al_fondo")):
            parche.start()
            self.addCleanup(parche.stop)

    def _textos(self, raiz):
        textos, pendientes = [], [raiz]
        while pendientes:
            widget = pendientes.pop()
            pendientes.extend(widget.winfo_children())
            if isinstance(widget, (tk.Label, tk.Button)):
                textos.append(str(widget.cget("text")))
        return textos

    def _boton(self, raiz, texto):
        pendientes = [raiz]
        while pendientes:
            widget = pendientes.pop()
            pendientes.extend(widget.winfo_children())
            if isinstance(widget, tk.Button) and widget.cget("text") == texto:
                return widget
        raise AssertionError(f"No hay botón «{texto}»")

    def test_cards_list_actions_and_preferences(self):
        app = _App(self.root, [_juego("Elden Ring"), _juego("Clone Hero"), _juego("7dsgb")], self.acciones)
        jarvis_portadas._anotar("7dsgb", "sin_resultado")
        widget = w.WidgetJuegos(app)
        widget.mostrar()
        self.root.update()
        self.assertTrue(widget.visible)
        self.assertEqual(self.prefs[w.PREF_VISIBLE], "1")
        textos = self._textos(widget.ventana)
        self.assertIn("3 juegos · Jarvis", textos)
        self.assertIn("● ABIERTO", textos)  # Clone Hero está abierto
        self.assertIn("＋ PORTADA", textos)  # 7dsgb no tiene portada
        self.assertEqual(textos.count("▶ JUGAR"), 3)

        self._boton(widget.ventana, "⚙ 2").invoke()  # Elden Ring: dos acciones
        textos = self._textos(widget.ventana)
        self.assertIn("ACCIONES AL ABRIR", textos)
        self.assertEqual(widget.seleccionado, "Elden Ring")
        with patch("jarvis_acciones_juego.guardar") as guardar:
            widget._quitar_accion("Elden Ring", 0)
        self.assertEqual(guardar.call_args.args[2:], ("Elden Ring", [self.acciones["elden ring"][1]]))
        app._render_games.assert_called_once()
        self._boton(widget.ventana, "＋ EDITAR ACCIONES").invoke()
        app._edit_game_actions.assert_called_once_with("Elden Ring", padre=widget.ventana)

        self.assertEqual(self._textos(widget.ventana).count("✎"), 3)  # cambiar portada en cada tarjeta
        with patch.object(tk.Menu, "tk_popup"):
            widget._menu_portada(app._games[0], 0, 0)
        self.assertEqual([widget.menu_portada.entrycget(i, "label") for i in range(2)],
                         ["Elegir una imagen de la PC…", "Buscar de nuevo en Steam"])
        with patch("jarvis_ui_juegos.subir_portada") as subir:
            widget.menu_portada.invoke(0)
        subir.assert_called_once_with(app, "Elden Ring", padre=widget.ventana)
        with patch("jarvis_ui_juegos.buscar_de_nuevo") as buscar:
            widget.menu_portada.invoke(1)
        buscar.assert_called_once_with(app, app._games[0])

        widget._jugar("Clone Hero")
        app._play_game.assert_called_once_with("Clone Hero")
        app.busy = True
        widget._jugar("Elden Ring")
        app._play_game.assert_called_once()  # ocupado: no lo lanza

        widget._cambiar_modo()
        self.assertEqual(self.prefs[w.PREF_MODO], "lista")
        textos = self._textos(widget.ventana)
        self.assertIn("● Abierto", textos)
        self.assertIn("2 acciones al abrir", textos)
        widget.filtro.set("elden")
        self.root.update()
        self.assertEqual(len(widget.lista.winfo_children()), 1)

        widget.ocultar()
        self.assertFalse(widget.visible)
        self.assertEqual(self.prefs[w.PREF_VISIBLE], "0")
        self.assertEqual(w.WidgetJuegos(app).modo, "lista")  # recuerda la vista

    def test_resize_changes_columns_and_is_remembered(self):
        app = _App(self.root, [_juego(f"Juego {i}") for i in range(7)], {})
        widget = w.WidgetJuegos(app)
        widget.mostrar()
        self.root.update()
        rejilla = widget.lista.winfo_children()[0]
        self.assertEqual(max(int(c.grid_info()["column"]) for c in rejilla.winfo_children()), 1)
        evento = lambda x, y: SimpleNamespace(x_root=x, y_root=y)
        widget._empezar_estirar(evento(500, 500))
        widget._estirar(evento(800, 400))  # +300 de ancho, -100 de alto
        self.root.update()
        widget._soltar_estirar(evento(800, 400))
        self.assertEqual(self.prefs[w.PREF_TAMANO], f"{w.ANCHO + 300}x{w.ALTO - 100}")
        rejilla = widget.lista.winfo_children()[0]
        self.assertEqual(max(int(c.grid_info()["column"]) for c in rejilla.winfo_children()),
                         w.columnas_para(w.ANCHO + 300) - 1)
        widget._empezar_estirar(evento(0, 0))
        widget._estirar(evento(-5000, -5000))  # no baja del mínimo
        self.root.update()
        self.assertEqual((widget.ventana.winfo_width(), widget.ventana.winfo_height()), w.MINIMO)
        widget.ocultar()
        otra = w.WidgetJuegos(app)
        otra.mostrar()
        self.root.update()
        self.assertEqual(otra.ventana.winfo_width(), w.ANCHO + 300)  # el tamaño guardado al soltar
        otra.ocultar()

    def test_only_the_background_is_transparent(self):
        self.prefs[w.PREF_OPACIDAD_VIEJA] = "0.80"  # la versión anterior: 20 % de toda la ventana
        app = _App(self.root, [_juego("Elden Ring")], {})
        widget = w.WidgetJuegos(app)
        self.assertEqual(widget.transparencia, 20)
        widget.mostrar()
        self.root.update()
        clave = w.color_clave(w.t.BG)
        self.assertEqual(str(widget.ventana.attributes("-transparentcolor")).lower(), clave.lower())
        self.assertAlmostEqual(float(widget.fondo.attributes("-alpha")), 0.8, places=2)
        self.assertEqual(float(widget.ventana.attributes("-alpha")), 1.0)  # el contenido, siempre opaco
        self.assertEqual(widget.lista.cget("bg"), clave)

        widget._alternar_apariencia()
        self.root.update()
        self.assertIsNotNone(widget.apariencia)
        widget.botones_atajo[100].invoke()
        self.assertEqual(float(widget.fondo.attributes("-alpha")), 0.0)
        self.assertEqual(widget.valor_transparencia.cget("text"), "100 %")
        widget.barra.event_generate("<ButtonPress-1>", x=widget.barra.winfo_width() // 2, y=10)
        self.assertEqual(widget.transparencia, 50)
        widget.poner_borde(False)
        self.assertEqual(self.prefs[w.PREF_BORDE], "0")
        self.assertEqual(widget.ventana.cget("bg"), clave)
        widget.botones_vista["lista"].invoke()
        self.assertEqual(widget.modo, "lista")
        widget.restablecer_apariencia()
        self.assertEqual((widget.transparencia, widget.borde), (0, True))

        widget.ventana.geometry("500x400+-3900+-3900")  # el fondo sigue al contenido
        self.root.update()
        self.assertEqual((widget.fondo.winfo_width(), widget.fondo.winfo_x()), (500, -3900))
        widget.ocultar()  # guarda lo pendiente
        self.assertEqual(self.prefs[w.PREF_TRANSPARENCIA], "0")
        self.assertIsNone(widget.fondo)

    def test_asks_for_the_games_when_there_is_no_list_yet(self):
        app = _App(self.root, None, {})
        widget = w.WidgetJuegos(app)
        widget.mostrar()
        self.root.update()
        app._load_games_page.assert_called_once()
        self.assertIn("Buscando juegos…", self._textos(widget.ventana))
        app._games = [_juego("Elden Ring")]
        widget.refrescar()
        self.assertIn("1 juego · Jarvis", self._textos(widget.ventana))
        widget.reconstruir()  # cambio de tema
        self.assertIn("1 juego · Jarvis", self._textos(widget.ventana))
        widget.ocultar()


if __name__ == "__main__":
    unittest.main()
