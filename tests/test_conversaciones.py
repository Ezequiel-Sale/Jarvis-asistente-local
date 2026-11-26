"""Bandeja de conversaciones (2026-11-25): listar, abrir y borrar. Base temporal, nunca la real."""

import tempfile
import tkinter as tk
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_core as core
import jarvis_ui_conversaciones as bandeja
from jarvis_app_ordenes import OrdenesMixin


class BaseTemporal(unittest.TestCase):
    def setUp(self) -> None:
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        for parche in (patch.object(core, "DB_PATH", Path(carpeta.name) / "jarvis_memory.db"),
                       patch.object(core, "_database_initialized", False)):
            parche.start()
            self.addCleanup(parche.stop)
        core.inicializar_base_datos()


class CoreTests(BaseTemporal):
    def test_list_open_and_delete(self) -> None:
        primera = core.crear_conversacion()
        core.guardar_turno(primera, "hola, primera", "respuesta")
        segunda = core.crear_conversacion()
        core.guardar_turno(segunda, "segunda charla", "ok")
        core.crear_conversacion()  # vacía: no aparece
        lista = core.listar_conversaciones()
        self.assertEqual([c.titulo for c in lista], ["segunda charla", "hola, primera"])
        self.assertEqual(lista[1].mensajes, 2)
        # Abrir la primera la vuelve la actual (también al reiniciar).
        core.tocar_conversacion(primera)
        self.assertEqual(core.obtener_conversacion_actual(), primera)
        self.assertEqual(core.listar_conversaciones()[0].id, primera)
        core.borrar_conversacion(segunda)
        self.assertEqual([c.id for c in core.listar_conversaciones()], [primera])
        self.assertEqual(core.obtener_historial(segunda), [])
        with self.assertRaises(ValueError):
            core.tocar_conversacion("no-existe")

    def test_when_labels(self) -> None:
        ahora = datetime(2026, 11, 25, 15, 0)
        self.assertEqual(bandeja.cuando(datetime(2026, 11, 25, 9, 5), ahora), "hoy 09:05")
        self.assertEqual(bandeja.cuando(datetime(2026, 11, 24, 9, 5), ahora), "ayer")
        self.assertEqual(bandeja.cuando(datetime(2026, 11, 21, 9, 5), ahora), "sáb 21/11")
        self.assertEqual(bandeja.cuando(datetime(2026, 10, 1), ahora), "01/10")
        self.assertEqual(bandeja.cuando(datetime(2025, 10, 1), ahora), "01/10/2025")


class AbrirTests(BaseTemporal):
    def test_open_conversation_replaces_the_chat(self) -> None:
        primera = core.crear_conversacion()
        core.guardar_turno(primera, "la primera", "respuesta 1")
        host = Mock()
        host.busy = False
        host.recorder = None
        host.conversation_id = core.crear_conversacion()
        host.messages.winfo_children.return_value = [Mock(), Mock()]
        self.assertTrue(OrdenesMixin._open_conversation(host, primera))
        self.assertEqual(host.conversation_id, primera)
        self.assertEqual([c.args[:2] for c in host._add_message.call_args_list],
                         [("user", "la primera"), ("assistant", "respuesta 1")])
        host.busy = True
        self.assertFalse(OrdenesMixin._open_conversation(host, "otra"))  # ocupado: no cambia
        self.assertEqual(host.conversation_id, primera)


class PanelTests(BaseTemporal):
    def test_panel_lists_and_filters(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Sin Tk: {error}")
        root.withdraw()
        self.addCleanup(root.destroy)
        for titulo in ("pon la tele", "abre steam", "pon música"):
            core.guardar_turno(core.crear_conversacion(), titulo, "ok")
        app = Mock()
        app.__dict__["_bandeja_conversaciones"] = None
        app._pages = {"chat": tk.Frame(root)}
        app.conversation_id = core.listar_conversaciones()[0].id
        bandeja.alternar(app)
        root.update()
        filas = app._bandeja_lista.winfo_children()
        self.assertEqual(len(filas), 3)
        bandeja.cerrar(app)
        self.assertIsNone(app._bandeja_conversaciones)


if __name__ == "__main__":
    unittest.main()
