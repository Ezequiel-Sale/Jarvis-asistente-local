"""Los textos del chat se achican con la ventana (Tk real, ventana oculta)."""

import tkinter as tk
import unittest

import jarvis_ui


class _Chat(jarvis_ui.JarvisUIMixin):
    pass


class ChatWidthTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.raiz = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk no disponible: {error}")
        self.raiz.withdraw()
        self.addCleanup(self.raiz.destroy)

    def test_labels_and_text_shrink_and_grow_back(self) -> None:
        chat = _Chat()
        chat.chat_canvas = tk.Canvas(self.raiz)
        chat.messages = tk.Frame(chat.chat_canvas)
        tarjeta = tk.Frame(chat.messages)
        tarjeta.pack()
        texto = tk.Label(tarjeta, text="Podrá mover el mouse…", wraplength=680)
        texto.pack()
        sin_ajuste = tk.Label(tarjeta, text="corto")
        sin_ajuste.pack()
        caja = tk.Text(tarjeta, width=80)
        caja._jarvis_ancho_px, caja._jarvis_px_letra = 650, 8
        caja.pack()
        for ancho, esperado_wrap, esperado_cols in ((520, 400, 50), (1200, 680, 82)):
            chat.chat_canvas.winfo_width = lambda a=ancho: a
            chat._fit_chat_text()
            self.assertEqual(int(float(str(texto.cget("wraplength")))), esperado_wrap)
            self.assertEqual(int(caja.cget("width")), esperado_cols)
        self.assertEqual(int(float(str(sin_ajuste.cget("wraplength")))), 0)  # sin ajuste: no se toca


if __name__ == "__main__":
    unittest.main()
