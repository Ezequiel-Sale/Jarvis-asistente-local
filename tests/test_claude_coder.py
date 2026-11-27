"""«cierra Claude Coder» (voz, 2026-11-27): «Coder» es «Code» y se cierra la app de Claude."""

import unittest

import jarvis_commands as c
import jarvis_modo_juego as m


class ClaudeCoderTests(unittest.TestCase):
    def test_coder_is_code(self) -> None:
        texto = c.limpiar_transcripcion_voz("cierra Claude Coder")
        self.assertEqual(texto, "cierra Claude Code")
        self.assertEqual(c.detectar_cerrar_programa(texto), ("close_program", {"names": "claude code"}))
        self.assertEqual(m.ALIAS["claude code"], "claude")
        self.assertEqual(m.ALIAS["claude coder"], "claude")  # si llega escrito así


if __name__ == "__main__":
    unittest.main()
