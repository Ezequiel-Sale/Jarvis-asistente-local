"""Abrir y navegar con la extensión de Brave, sin teclado (2026-11-28: con pantalla completa los atajos no
llegaban y la rutina «Eugenia» dijo haber abierto YouTube sin abrir nada)."""

import unittest
from unittest.mock import Mock

import jarvis_brave


class AbrirUrlTests(unittest.TestCase):
    def test_uses_the_extension_when_connected(self) -> None:
        puente = Mock()
        puente.conectado.return_value = True
        self.assertTrue(jarvis_brave.abrir_url("https://www.youtube.com", nueva=True, puente=puente))
        puente.pedir.assert_called_once_with("abrir_url", url="https://www.youtube.com", nueva=True)

    def test_falls_back_when_not_connected_or_failing(self) -> None:
        desconectado = Mock()
        desconectado.conectado.return_value = False
        self.assertFalse(jarvis_brave.abrir_url("https://x.com", puente=desconectado))
        desconectado.pedir.assert_not_called()
        con_error = Mock()
        con_error.conectado.return_value = True
        con_error.pedir.side_effect = jarvis_brave.ErrorBrave("no")
        self.assertFalse(jarvis_brave.abrir_url("https://x.com", puente=con_error))


if __name__ == "__main__":
    unittest.main()
