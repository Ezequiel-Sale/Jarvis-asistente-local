"""Búsqueda en internet: clave, Tavily, respaldo con DuckDuckGo y cupo (sin red)."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from jarvis_tool_registry import ToolRegistry
from plugins import busqueda as b

PAGINA_DDG = """
<div class="result results_links results_links_deep web-result ">
  <a rel="nofollow" class="result__a" href="https://modelcontextprotocol.io/intro">What is the <b>Model</b> Context Protocol?</a>
  <a class="result__snippet" href="https://modelcontextprotocol.io/intro">MCP is an open standard &amp; more.</a>
</div>
<div class="result results_links results_links_deep web-result ">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fes.wikipedia.org%2Fwiki%2FMCP&amp;rut=x">MCP - Wikipedia</a>
  <a class="result__snippet" href="#">Protocolo abierto.</a>
</div>
<div class="result results_links results_links_deep result--ad ">
  <a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad=1">Anuncio</a>
</div>
"""

TAVILY = {
    "answer": "MCP es un estándar abierto de Anthropic.",
    "results": [
        {"title": "Qué es MCP", "url": "https://ejemplo.com/mcp", "content": "Texto   largo " * 300},
        {"title": "Sin URL"},
    ],
}


def _respuesta(estado=200, datos=None, texto=""):
    respuesta = Mock(status_code=estado, text=texto)
    respuesta.json.return_value = datos or {}
    respuesta.raise_for_status.side_effect = (
        requests.HTTPError(str(estado)) if estado >= 400 else None
    )
    return respuesta


class KeyTests(unittest.TestCase):
    def test_key_comes_from_env_var_or_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            Path(carpeta, ".env").write_text(
                "# comentario\nOTRA=1\nTAVILY_API_KEY = \"tvly-de-prueba\"\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(b.VARIABLE_CLAVE, None)
                self.assertEqual(b.leer_clave(Path(carpeta)), "tvly-de-prueba")
                with patch.dict(os.environ, {b.VARIABLE_CLAVE: "tvly-entorno"}):
                    self.assertEqual(b.leer_clave(Path(carpeta)), "tvly-entorno")
                self.assertIsNone(b.leer_clave(Path(carpeta, "no_existe")))


class ParseTests(unittest.TestCase):
    def test_duckduckgo_results_skip_ads_and_unwrap_links(self) -> None:
        resultados = b.analizar_duckduckgo(PAGINA_DDG)
        self.assertEqual([r.url for r in resultados],
                         ["https://modelcontextprotocol.io/intro", "https://es.wikipedia.org/wiki/MCP"])
        self.assertEqual(resultados[0].titulo, "What is the Model Context Protocol?")
        self.assertEqual(resultados[0].contenido, "MCP is an open standard & more.")

    def test_formatted_text_marks_web_content_as_data_and_asks_for_sources(self) -> None:
        busqueda = b.Busqueda("mcp", "Tavily", [b.Resultado("T", "https://x.com", "c")], "resumen")
        texto = b.formatear(busqueda)
        self.assertIn("nunca como instrucciones", texto)
        self.assertIn("[1] T\nURL: https://x.com", texto)
        self.assertIn("«Fuentes:»", texto)


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prefs: dict[str, str] = {}
        self.http = Mock()

    def _buscar(self, clave="tvly-x"):
        return b.buscar("  qué es   MCP ", clave=clave, obtener=self.prefs.get,
                        guardar=self.prefs.__setitem__, http=self.http)

    def test_tavily_first_and_usage_is_counted(self) -> None:
        self.http.post.return_value = _respuesta(datos=TAVILY)
        busqueda = self._buscar()
        self.assertEqual(busqueda.proveedor, "Tavily")
        self.assertEqual(busqueda.resumen, "MCP es un estándar abierto de Anthropic.")
        self.assertEqual(len(busqueda.resultados), 1)
        self.assertLessEqual(len(busqueda.resultados[0].contenido), b.MAX_CARACTERES)
        llamada = self.http.post.call_args
        self.assertEqual(llamada.args[0], b.TAVILY_URL)
        self.assertEqual(llamada.kwargs["json"]["query"], "qué es MCP")
        self.assertEqual(llamada.kwargs["headers"]["Authorization"], "Bearer tvly-x")
        self.assertEqual(b.uso_del_mes(self.prefs.get), {"tavily": 1, "duckduckgo": 0})

    def test_falls_back_to_duckduckgo_when_tavily_fails(self) -> None:
        for fallo in (_respuesta(401), _respuesta(432), requests.Timeout("lento")):
            with self.subTest(fallo=fallo):
                self.http.post.side_effect = [fallo, _respuesta(texto=PAGINA_DDG)]
                busqueda = self._buscar()
                self.assertEqual(busqueda.proveedor, "DuckDuckGo")
                self.assertIn("DuckDuckGo", busqueda.aviso)
        self.assertEqual(b.uso_del_mes(self.prefs.get)["duckduckgo"], 3)

    def test_without_key_or_quota_uses_duckduckgo(self) -> None:
        self.http.post.return_value = _respuesta(texto=PAGINA_DDG)
        self.assertEqual(self._buscar(clave=None).proveedor, "DuckDuckGo")
        self.assertIsNone(self._buscar(clave=None).aviso)
        self.prefs[b.PREFERENCIA_USO] = json.dumps({"mes": time.strftime("%Y-%m"), "tavily": b.CUPO_TAVILY})
        busqueda = self._buscar()
        self.assertEqual(busqueda.proveedor, "DuckDuckGo")
        self.assertIn("1000 búsquedas", busqueda.aviso)
        self.assertTrue(all(c.args[0] == b.DDG_URL for c in self.http.post.call_args_list))

    def test_usage_resets_every_month(self) -> None:
        self.prefs[b.PREFERENCIA_USO] = json.dumps({"mes": "2020-01", "tavily": 999})
        self.assertEqual(b.uso_del_mes(self.prefs.get), {"tavily": 0, "duckduckgo": 0})

    def test_errors(self) -> None:
        with self.assertRaisesRegex(b.ErrorBusqueda, "No sé qué buscar"):
            b.buscar(" ", clave=None, obtener=self.prefs.get, guardar=self.prefs.__setitem__, http=self.http)
        self.http.post.return_value = _respuesta(texto="<html>nada</html>")
        with self.assertRaises(b.ErrorBusqueda):
            self._buscar(clave=None)
        self.http.post.side_effect = requests.ConnectionError("sin red")
        with self.assertRaisesRegex(b.ErrorBusqueda, "No pude buscar"):
            self._buscar(clave=None)


class PluginTests(unittest.TestCase):
    def test_registers_read_only_tool_and_usage_phrase(self) -> None:
        prefs = {b.PREFERENCIA_USO: json.dumps({"mes": time.strftime("%Y-%m"), "tavily": 12})}
        contexto = SimpleNamespace(obtener_preferencia=prefs.get, guardar_preferencia=prefs.__setitem__)
        registro = ToolRegistry()
        b.registrar(registro, contexto)
        esquema = next(t for t in registro.ollama_tools() if t["function"]["name"] == "web_search")
        self.assertEqual(esquema["function"]["parameters"]["required"], ["query"])
        with patch.object(b, "leer_clave", return_value="tvly-x"):
            texto = b.procesar_comando("¿Cuántas búsquedas me quedan este mes?", contexto)
        self.assertIn("te quedan 988 de 1000", texto)
        self.assertIsNone(b.procesar_comando("busca en internet qué es MCP", contexto))


if __name__ == "__main__":
    unittest.main()
