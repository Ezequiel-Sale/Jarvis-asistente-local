"""Fase 2: mensajes con formato, tarjetas de acción, confirmaciones en el chat y animaciones."""

import queue
import time
import tkinter as tk
import types
import unittest
from unittest.mock import Mock, patch

import jarvis_cards
import jarvis_markdown as md
import jarvis_theme
from jarvis_actions import EstadoAccion, ResultadoAccion, Verificacion
from jarvis_app import JarvisApp
from jarvis_tools import crear_registro_herramientas
from test_ventana_unica import _Vista


def _esperar(root: tk.Misc, segundos: float = 0.4) -> None:
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        root.update()
        time.sleep(0.02)


class MarkdownTests(unittest.TestCase):
    def test_inline_formats(self) -> None:
        tramos = md.analizar("Esto es **importante**, *suave* y `código` en [la web](https://ollama.com).")
        self.assertIn(("importante", ("b",), None), tramos)
        self.assertIn(("suave", ("i",), None), tramos)
        self.assertIn(("código", ("code",), None), tramos)
        self.assertIn(("la web", ("link",), "https://ollama.com"), tramos)

    def test_bare_url_without_trailing_punctuation(self) -> None:
        self.assertIn(("https://ollama.com/library", ("link",), "https://ollama.com/library"),
                      md.analizar("Mira https://ollama.com/library."))

    def test_lists_headings_code_and_quotes(self) -> None:
        tramos = md.analizar("# Título\n- uno\n2. dos\n```\nprint(1)\n```\n> cita")
        self.assertEqual(tramos[0], ("Título", ("h",), None))
        self.assertIn(("•  ", ("li",), None), tramos)
        self.assertIn(("2. ", ("li",), None), tramos)
        self.assertIn(("print(1)", ("pre",), None), tramos)
        self.assertIn(("cita", ("quote",), None), tramos)

    def test_table_is_aligned(self) -> None:
        tabla = [t for t in md.analizar("| A | Largo |\n|---|---|\n| xx | 1 |") if t[1] == ("table",)][0][0]
        lineas = tabla.split("\n")
        self.assertEqual(lineas[0], "A   Largo")
        self.assertTrue(set(lineas[1]) <= {"─", " "})
        self.assertEqual(lineas[2], "xx  1")

    def test_no_false_positives(self) -> None:
        texto = "snake_case_var y 2*3*4 y un_nombre_largo"
        self.assertEqual(md.analizar(texto), [(texto, (), None)])
        self.assertEqual(md.texto_plano("**hola** `x`"), "hola x")

    def test_only_web_links(self) -> None:
        self.assertFalse(any(t[2] for t in md.analizar("[mal](javascript:alert(1)) file:///c:/x")))


class CardLogicTests(unittest.TestCase):
    def _resultado(self, estado, verificacion=None, error=None):
        return ResultadoAccion("move_window", "Mover ventanas", "directo", estado, 0.42,
                               {"app": "brave"}, verificacion=verificacion, error=error)

    def test_states(self) -> None:
        self.assertEqual(jarvis_cards.estado(self._resultado(EstadoAccion.EXITO)), ("✓ HECHO", "CYAN"))
        verificada = self._resultado(EstadoAccion.EXITO, Verificacion(True, "ok"))
        self.assertEqual(jarvis_cards.estado(verificada)[0], "✓ VERIFICADA")
        self.assertEqual(jarvis_cards.estado(self._resultado(EstadoAccion.ERROR))[1], "ERROR")
        self.assertEqual(jarvis_cards.estado(self._resultado(EstadoAccion.CANCELADA))[1], "MUTED")

    def test_summary_icon_and_detail(self) -> None:
        resultado = self._resultado(EstadoAccion.ERROR, error="sin ventanas")
        self.assertEqual(jarvis_cards.resumen(resultado), "0.4 s · directo")
        self.assertEqual(jarvis_cards.icono("move_window"), "🗔")
        self.assertEqual(jarvis_cards.icono("herramienta_rara"), "⚙")
        self.assertIn("Error: sin ventanas", jarvis_cards.detalle(resultado))
        self.assertIn('"app": "brave"', jarvis_cards.detalle(resultado))


class ChatWidgetTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        self.root.geometry("1180x800")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.addCleanup(jarvis_theme.activar, "cian")
        with patch("jarvis_core.obtener_preferencia", return_value=None):
            self.v = _Vista(self.root, crear_registro_herramientas(Mock()))
        self.v._animations = True

    def test_rich_message_uses_formatted_text(self) -> None:
        self.v._add_message("assistant", "Hola **mundo**\n- uno\n- dos")
        _esperar(self.root)
        cajas = [w for w in _todos(self.v.messages) if isinstance(w, tk.Text)]
        self.assertEqual(len(cajas), 1)
        self.assertIn("mundo", cajas[0].get("1.0", "end"))
        self.assertTrue(cajas[0].tag_ranges("b"))
        self.assertEqual(cajas[0].cget("state"), "disabled")

    def test_action_card_holds_the_response(self) -> None:
        resultado = ResultadoAccion("move_window", "Mover ventanas", "directo", EstadoAccion.EXITO, 0.4)
        tarjeta = self.v._add_action_card(resultado)
        tarjeta.jarvis_poner_cuerpo("Moví 1 ventana de brave al monitor 3.")
        _esperar(self.root)
        textos = " ".join(w.get("1.0", "end") for w in _todos(tarjeta) if isinstance(w, tk.Text))
        self.assertIn("Moví 1 ventana", textos)
        etiquetas = [w.cget("text") for w in _todos(tarjeta) if isinstance(w, tk.Label)]
        self.assertIn("✓ HECHO", etiquetas)

    def test_confirmation_card_resolves_in_place(self) -> None:
        v = self.v
        v._pending_plan_reply, v._pending_approval_kind, v._plan_dialog = None, None, None
        v._pending_power_action, v._recording_for_plan_approval = None, False
        v._APPROVAL_PHRASES = JarvisApp._APPROVAL_PHRASES
        for nombre in ("_show_power_confirmation", "_resolve_plan_approval", "_approval_hint",
                       "_set_plan_voice_controls"):
            setattr(v, nombre, types.MethodType(getattr(JarvisApp, nombre), v))
        reply: queue.Queue[bool] = queue.Queue(1)
        v._show_power_confirmation("shutdown", reply)
        tarjeta = v._plan_dialog
        self.assertIs(tarjeta.winfo_toplevel(), self.root)
        v._plan_reject_button.invoke()
        self.assertFalse(reply.get_nowait())
        self.assertTrue(tarjeta.winfo_exists())
        self.assertFalse(any(isinstance(w, tk.Button) for w in _todos(tarjeta)))

    def test_fade_ends_with_current_theme_even_if_switched_midway(self) -> None:
        self.v._add_message("assistant", "Hola")
        jarvis_theme.recolorear(self.root, jarvis_theme.activar("claro"))  # a mitad del fundido
        _esperar(self.root, 1.5)  # margen: con la PC muy cargada el fundido tarda más de 0,4 s
        paleta = {c.upper() for c in jarvis_theme.TEMAS["claro"].values()}
        for w in _todos(self.v.messages):
            if isinstance(w, (tk.Label, tk.Text)):
                self.assertIn(str(w.cget("foreground")).upper(), paleta)

    def test_animations_can_be_disabled(self) -> None:
        self.v._animations = False
        etiqueta = tk.Label(self.root, text="x", fg=jarvis_theme.TEXT, bg=jarvis_theme.PANEL)
        self.v._fade_in(etiqueta)
        self.assertEqual(str(etiqueta.cget("foreground")).upper(), jarvis_theme.TEXT.upper())


def _todos(widget):
    pendientes = [widget]
    while pendientes:
        w = pendientes.pop()
        yield w
        pendientes.extend(w.winfo_children())


if __name__ == "__main__":
    unittest.main()
