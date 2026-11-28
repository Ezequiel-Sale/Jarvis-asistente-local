"""Rutinas que el creador no entiende las arma la IA (2026-11-28); la IA puede crear rutinas por frase."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_core as core
import jarvis_routines as r
from jarvis_app_ordenes import OrdenesMixin
from jarvis_app_rutinas import RutinasMixin
from jarvis_tools import crear_registro_herramientas


class BaseTemporal(unittest.TestCase):
    def setUp(self) -> None:
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        for parche in (patch.object(core, "DB_PATH", Path(carpeta.name) / "jarvis_memory.db"),
                       patch.object(core, "_database_initialized", False)):
            parche.start()
            self.addCleanup(parche.stop)
        core.inicializar_base_datos()


class RutinaPorFraseTests(BaseTemporal):
    def test_ai_can_save_a_phrase_routine(self) -> None:
        registro = crear_registro_herramientas(Mock())
        self.assertIn("phrase", registro.get("schedule_routine").parameters["properties"]["trigger"]["enum"])
        host = Mock()
        host.tool_registry = registro
        host._routine_permission_text = lambda *_a: "Pedirá aprobación."
        pasos = json.dumps([{"tool": "open_new_browser_tab", "arguments": {}},
                            {"tool": "navigate_browser", "arguments": {"url": "https://youtube.com"}}])
        texto = RutinasMixin._tool_schedule_routine(host, "Música favorita", steps_json=pasos, trigger="phrase")
        self.assertIn("«Música favorita»", texto)
        rutina = r.buscar_rutina_por_frase("música favorita")
        self.assertIsNotNone(rutina)


class CreadorSinEntenderTests(unittest.TestCase):
    def test_unparsed_routine_goes_to_the_ai(self) -> None:
        """«crea una rutina llamada música favorita, abre … luego …» (sin hora) respondía el ejemplo de formato."""
        host = Mock()
        host._rutina_de_evento_con_ordenes.return_value = None
        frase = ("crea una rutina llamada música favorita, abre una nueva pestaña y navega a youtube.com "
                 "luego pon mi música favorita")
        with patch("jarvis_app_ordenes.procesar_con_herramientas", return_value="la armé") as agente, \
                patch("jarvis_memory.procesar_comando", return_value=None), \
                patch("jarvis_aprendizaje.procesar_comando", return_value=None), \
                patch("jarvis_actions.procesar_comando", return_value=None), \
                patch("jarvis_routines.procesar_comando", return_value=None), \
                patch("jarvis_core.guardar_turno"):
            try:
                OrdenesMixin._process_agent_query(host, frase, "chat", 0.0, "c", None)
            except Exception:
                pass  # lo de después de la IA (audio) no importa acá
        agente.assert_called_once()
        self.assertEqual(agente.call_args.args[0], frase)


if __name__ == "__main__":
    unittest.main()
