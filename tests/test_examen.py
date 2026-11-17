"""La corrección del examen del modelo (``examen/evaluar.py``). Sin Ollama ni red."""

import json
import unittest
from pathlib import Path

from examen.evaluar import (ACTUO_SIN_PEDIDO, ARGUMENTOS, CORRECTO, FALTAN_PASOS, HERRAMIENTA, NO_ACTUO, ORDEN,
                            PASOS_DE_MAS, corregir)


def llamada(nombre, **args):
    return {"function": {"name": nombre, "arguments": args}}


VOLUMEN = {"esperado": [{"tool": "set_system_volume", "args": {"percentage": 40}}]}
PLAN = {"esperado": [{"tool": "open_app", "args": {"name": "spotify"}},
                     {"tool": "set_system_volume", "args": {"percentage": 30}}]}


class GradingTests(unittest.TestCase):
    def test_single_action(self) -> None:
        self.assertEqual(corregir(VOLUMEN, [llamada("set_system_volume", percentage=40)])[0], CORRECTO)
        self.assertEqual(corregir(VOLUMEN, [llamada("set_system_volume", percentage="40")])[0], CORRECTO)
        self.assertEqual(corregir(VOLUMEN, [llamada("set_system_volume", percentage=50)])[0], ARGUMENTOS)
        self.assertEqual(corregir(VOLUMEN, [llamada("set_app_volume", app="x")])[0], HERRAMIENTA)
        self.assertEqual(corregir(VOLUMEN, [])[0], NO_ACTUO)

    def test_text_args_ignore_case_accents_and_accept_options(self) -> None:
        caso = {"esperado": [{"tool": "open_app", "args": {"name": "whatsapp"}}]}
        self.assertEqual(corregir(caso, [llamada("open_app", name="WhatsApp Desktop")])[0], CORRECTO)
        caso = {"esperado": [{"tool": "web_search", "args": {"query": "dolar|cotización"}}]}
        self.assertEqual(corregir(caso, [llamada("web_search", query="Dólar blue hoy")])[0], CORRECTO)

    def test_plans(self) -> None:
        bien = [llamada("open_app", name="Spotify"), llamada("set_system_volume", percentage=30)]
        self.assertEqual(corregir(PLAN, bien)[0], CORRECTO)
        self.assertEqual(corregir(PLAN, bien[::-1])[0], ORDEN)
        self.assertEqual(corregir(PLAN, bien[:1])[0], FALTAN_PASOS)
        self.assertEqual(corregir(PLAN, [bien[0], llamada("window_control", app="spotify", action="window_focus"),
                                         bien[1]])[0], PASOS_DE_MAS)

    def test_no_tool_expected(self) -> None:
        caso = {"esperado": None}
        self.assertEqual(corregir(caso, [])[0], CORRECTO)
        self.assertEqual(corregir(caso, [llamada("web_search", query="x")])[0], ACTUO_SIN_PEDIDO)

    def test_alternatives_and_routine_steps(self) -> None:
        caso = {"esperado": [{"tool": "media_control", "args": {"action": "pause"}}],
                "alternativas": [[{"tool": "media_control", "args": {"action": "toggle"}}]]}
        self.assertEqual(corregir(caso, [llamada("media_control", action="toggle")])[0], CORRECTO)
        rutina = {"esperado": [{"tool": "schedule_routine", "args": {"once": True, "_pasos": ["power_control"]}}]}
        pasos = json.dumps([{"tool": "power_control", "arguments": {"action": "shutdown"}}])
        self.assertEqual(corregir(rutina, [llamada("schedule_routine", once=True, steps_json=pasos)])[0], CORRECTO)
        self.assertEqual(corregir(rutina, [llamada("schedule_routine", once=True, steps_json="[]")])[0], ARGUMENTOS)

    def test_window_list(self) -> None:
        caso = {"esperado": [{"tool": "arrange_windows", "args": {"windows": [
            {"app": "jarvis", "position": "left"}, {"app": "claude", "position": "right"}]}}]}
        bien = llamada("arrange_windows", windows=[{"app": "Jarvis", "position": "left"},
                                                  {"app": "Claude", "position": "right"}])
        self.assertEqual(corregir(caso, [bien])[0], CORRECTO)
        mal = llamada("arrange_windows", windows=[{"app": "Claude", "position": "left"}])
        self.assertEqual(corregir(caso, [mal])[0], ARGUMENTOS)

    def test_cases_file_is_valid(self) -> None:
        """Cada caso tiene lo necesario y las herramientas existen en Jarvis."""
        from unittest.mock import Mock

        from jarvis_tools import crear_registro_herramientas

        nombres = {t["function"]["name"] for t in crear_registro_herramientas(Mock()).ollama_tools()}
        nombres |= {"web_search", "get_weather"}  # plugins
        casos = json.loads((Path(__file__).resolve().parent.parent / "examen" / "casos.json").read_text(
            encoding="utf-8"))
        ids = [c["id"] for c in casos]
        self.assertEqual(len(ids), len(set(ids)))
        for caso in casos:
            with self.subTest(caso=caso["id"]):
                self.assertTrue(caso["texto"] and caso["grupo"] and caso["categoria"])
                for opcion in [caso["esperado"]] + caso.get("alternativas", []):
                    for paso in opcion or []:
                        self.assertIn(paso["tool"], nombres)


if __name__ == "__main__":
    unittest.main()
