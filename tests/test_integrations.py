import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jarvis_integraciones as integ
from jarvis_mcp import (
    GestorMCP,
    HerramientaMCP,
    formatear_resultado,
    nombre_registro,
    normalizar_esquema,
    riesgo_de,
)
from jarvis_plugins import GestorPlugins
from jarvis_tool_registry import ToolRegistry, ToolRisk, ToolValidationError
from plugins import clima


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.dir = Path(temp_dir.name)

    def test_default_file_is_created_with_read_only_tools(self) -> None:
        result = integ.cargar_integraciones(self.dir)
        self.assertTrue((self.dir / integ.NOMBRE_ARCHIVO).exists())
        self.assertIn("clima", result.plugins)
        archivos = result.servidores_mcp[0]
        self.assertEqual(archivos.nombre, "archivos")
        self.assertTrue(archivos.lecturas_sin_preguntar)
        self.assertNotIn("write_file", archivos.herramientas_permitidas)
        self.assertIn(str(Path.home() / "Documents"), archivos.args)

    def test_invalid_json_disables_integrations(self) -> None:
        (self.dir / integ.NOMBRE_ARCHIVO).write_text("{ roto", encoding="utf-8")
        result = integ.cargar_integraciones(self.dir)
        self.assertEqual((result.plugins, result.servidores_mcp), ({}, ()))
        self.assertIn("inválido", result.error)

    def test_inactive_entries_are_skipped_and_bad_entries_rejected(self) -> None:
        datos = integ.configuracion_por_defecto()
        datos["plugins"]["clima"]["activo"] = False
        datos["mcp_servers"]["archivos"]["activo"] = False
        (self.dir / integ.NOMBRE_ARCHIVO).write_text(json.dumps(datos), encoding="utf-8")
        result = integ.cargar_integraciones(self.dir)
        self.assertEqual((result.plugins, result.servidores_mcp, result.error), ({}, (), None))

        datos["mcp_servers"]["archivos"].update(activo=True, args="no es lista")
        (self.dir / integ.NOMBRE_ARCHIVO).write_text(json.dumps(datos), encoding="utf-8")
        self.assertIsNotNone(integ.cargar_integraciones(self.dir).error)


class WeatherPluginTests(unittest.TestCase):
    def test_phrases(self) -> None:
        casos = {
            "¿Qué tiempo hace?": {},
            "clima en Córdoba": {"city": "cordoba"},
            "¿va a llover mañana?": {"day": "tomorrow"},
            "el clima de mañana en Rosario": {"day": "tomorrow", "city": "rosario"},
            "Jarvis, ¿qué temperatura hace en Buenos Aires?": {"city": "buenos aires"},
        }
        for texto, argumentos in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(clima.detectar_comando(texto), ("get_weather", argumentos))
        for texto in ("el tiempo vuela", "sube el volumen", "qué hora es"):
            self.assertIsNone(clima.detectar_comando(texto))

    def test_format_today_and_tomorrow(self) -> None:
        lugar = {"name": "Córdoba", "country": "Argentina"}
        datos = {
            "current": {"temperature_2m": 11.3, "apparent_temperature": 10.7,
                        "relative_humidity_2m": 95, "weather_code": 0, "wind_speed_10m": 5.2},
            "daily": {"weather_code": [0, 63], "temperature_2m_max": [26.3, 27.6],
                      "temperature_2m_min": [11.2, 14.7],
                      "precipitation_probability_max": [0, 66]},
        }
        hoy = clima.formatear_clima(lugar, datos, "today")
        self.assertTrue(hoy.startswith("Ahora en Córdoba, Argentina: 11 °C (sensación 11 °C), despejado"))
        self.assertIn("máxima 26 °C", hoy)
        self.assertEqual(
            clima.formatear_clima(lugar, datos, "tomorrow"),
            "Mañana en Córdoba, Argentina: lluvia, mínima 15 °C y máxima 28 °C, "
            "probabilidad de lluvia 66 %.",
        )

    def test_without_city_asks_for_it(self) -> None:
        with patch.dict(clima._preferencias, {"obtener": lambda _clave: None}):
            with self.assertRaises(ValueError) as context:
                clima.consultar_clima()
        self.assertIn("mi ciudad es", str(context.exception))

    ECUADOR = {"name": "Santa Rosa", "admin1": "El Oro", "admin2": "Santa Rosa",
               "country": "Ecuador", "latitude": -3.3, "longitude": -79.9}
    LA_PAMPA = {"name": "Santa Rosa", "admin1": "Provincia de La Pampa",
               "admin2": "Departamento Capital", "country": "Argentina",
               "latitude": -26.77, "longitude": -65.27}

    def _geocode(self, nombre: str) -> list[dict]:
        return [self.ECUADOR, self.LA_PAMPA] if nombre == "santa rosa" else []

    def test_province_and_country_hints_pick_the_right_city(self) -> None:
        with patch.object(clima, "_geocodificar", side_effect=self._geocode):
            for texto in ("Santa Rosa, La Pampa, Argentina", "santa rosa pampa",
                          "santa rosa de pampa"):
                with self.subTest(texto=texto):
                    self.assertEqual(clima.buscar_ciudad(texto)["country"], "Argentina")
            self.assertEqual(clima.buscar_ciudad("santa rosa")["country"], "Ecuador")
            self.assertEqual(
                clima.buscar_ciudad("santa rosa", pais_preferido="Argentina")["country"],
                "Argentina",
            )
            with self.assertRaises(ValueError):
                clima.buscar_ciudad("santa rosa chile")

    def test_set_city_stores_coordinates_and_warns_if_ambiguous(self) -> None:
        contexto = Mock()
        with patch.object(clima, "_geocodificar", side_effect=self._geocode):
            respuesta = clima.procesar_comando("Mi ciudad es Santa Rosa, La Pampa, Argentina", contexto)
            ambigua = clima.procesar_comando("mi ciudad es santa rosa", contexto)
        clave, valor = contexto.guardar_preferencia.call_args_list[0].args
        self.assertEqual(clave, clima.PREFERENCIA_CIUDAD)
        guardado = json.loads(valor)
        self.assertEqual((guardado["country"], guardado["latitude"]), ("Argentina", -26.77))
        self.assertEqual(respuesta, "Listo, usaré Santa Rosa, La Pampa, Argentina como tu ciudad para el clima.")
        self.assertIn("También existe: Santa Rosa, La Pampa, Argentina", ambigua)
        self.assertIsNone(clima.procesar_comando("hola", contexto))

    def test_saved_city_is_used_without_searching_again(self) -> None:
        guardado = json.dumps({"name": "Santa Rosa", "admin1": "Provincia de La Pampa",
                               "country": "Argentina", "latitude": -26.77, "longitude": -65.27})
        respuesta = Mock()
        respuesta.json.return_value = {
            "current": {"temperature_2m": 20, "apparent_temperature": 20, "relative_humidity_2m": 50,
                        "weather_code": 1, "wind_speed_10m": 3},
            "daily": {"weather_code": [1, 1], "temperature_2m_max": [25, 26],
                      "temperature_2m_min": [15, 16], "precipitation_probability_max": [5, 5]},
        }
        with patch.dict(clima._preferencias, {"obtener": lambda c: guardado if c == clima.PREFERENCIA_CIUDAD else None}), \
                patch.object(clima, "_geocodificar") as geocode, \
                patch.object(clima.requests, "get", return_value=respuesta) as get:
            texto = clima.consultar_clima()
        geocode.assert_not_called()
        self.assertEqual(get.call_args.kwargs["params"]["latitude"], -26.77)
        self.assertTrue(texto.startswith("Ahora en Santa Rosa, La Pampa, Argentina"))


class PluginManagerTests(unittest.TestCase):
    def test_loads_known_plugins_and_reports_unknown(self) -> None:
        registry = ToolRegistry()
        gestor = GestorPlugins()
        gestor.cargar({"clima": {"activo": True}, "inexistente": {}}, registry)
        self.assertEqual([p.nombre for p in gestor.cargados], ["clima"])
        self.assertIn("inexistente", gestor.errores)
        self.assertIs(registry.get("get_weather").risk, ToolRisk.LOW)
        self.assertEqual(gestor.detectar_comando("clima en Madrid"), ("get_weather", {"city": "madrid"}))


def _tool(name: str, read_only: bool | None, destructive: bool | None = False, schema=None):
    return SimpleNamespace(
        name=name,
        title=None,
        description=f"{name} description",
        input_schema=schema if schema is not None else {"type": "object", "properties": {"path": {"type": "string"}}},
        annotations=SimpleNamespace(read_only_hint=read_only, destructive_hint=destructive, title=None),
    )


class McpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = integ.ServidorMCPConfig(
            nombre="archivos",
            command="npx",
            args=(),
            descripcion="Archivos (solo lectura)",
            herramientas_permitidas=("read_text_file", "write_file", "list_directory"),
            lecturas_sin_preguntar=True,
        )

    def test_schema_normalization(self) -> None:
        self.assertEqual(
            normalizar_esquema({"$schema": "x", "type": "object"}),
            {"type": "object", "properties": {}, "required": []},
        )
        self.assertEqual(
            normalizar_esquema({"properties": {"a": {"type": "string"}}, "required": ["a", "b"]})["required"],
            ["a"],
        )

    def test_risk_matrix(self) -> None:
        def herramienta(solo_lectura: bool, destructiva: bool) -> HerramientaMCP:
            return HerramientaMCP("s", "t", "t", "d", {}, solo_lectura, destructiva)

        self.assertEqual(riesgo_de(herramienta(True, False), True), (ToolRisk.LOW, False))
        self.assertEqual(riesgo_de(herramienta(True, False), False), (ToolRisk.MEDIUM, True))
        self.assertEqual(riesgo_de(herramienta(False, False), True), (ToolRisk.MEDIUM, True))
        self.assertEqual(riesgo_de(herramienta(False, True), True), (ToolRisk.HIGH, True))

    def test_only_allowed_tools_are_kept_with_annotations(self) -> None:
        tools = [_tool("read_text_file", True), _tool("write_file", False, True),
                 _tool("move_file", False, True), _tool("list_directory", None)]
        filtradas = GestorMCP._filtrar(self.config, tools)
        self.assertEqual([h.nombre for h in filtradas], ["read_text_file", "write_file", "list_directory"])
        self.assertEqual(filtradas[0].titulo, "leer archivo")
        self.assertFalse(filtradas[2].solo_lectura)

    def test_registration_applies_permissions_and_privacy(self) -> None:
        gestor = GestorMCP((self.config,), Path(tempfile.gettempdir()))
        registry = ToolRegistry()
        herramientas = GestorMCP._filtrar(
            self.config, [_tool("read_text_file", True), _tool("write_file", False, True)]
        )
        nombres = gestor.registrar_herramientas(registry, "archivos", herramientas)
        self.assertEqual(nombres, ["mcp_archivos_read_text_file", "mcp_archivos_write_file"])
        lectura = registry.get("mcp_archivos_read_text_file")
        escritura = registry.get("mcp_archivos_write_file")
        self.assertEqual((lectura.risk, lectura.requires_confirmation, lectura.record_result),
                         (ToolRisk.LOW, False, False))
        self.assertEqual((escritura.risk, escritura.requires_confirmation), (ToolRisk.HIGH, True))
        self.assertTrue(lectura.description.startswith("[Archivos (solo lectura)]"))
        with patch.object(gestor, "llamar", return_value="contenido") as llamar:
            self.assertEqual(registry.execute("mcp_archivos_read_text_file", {"path": "a.txt"}), "contenido")
        llamar.assert_called_once_with("archivos", "read_text_file", {"path": "a.txt"})

    def test_call_without_connection_fails_clearly(self) -> None:
        gestor = GestorMCP((self.config,), Path(tempfile.gettempdir()))
        with self.assertRaises(RuntimeError):
            gestor.llamar("archivos", "read_text_file", {})

    def test_result_formatting(self) -> None:
        ok = SimpleNamespace(is_error=False, content=[SimpleNamespace(text="x" * 5000), SimpleNamespace()])
        texto = formatear_resultado(ok)
        self.assertIn("recortado", texto)
        self.assertLess(len(texto), 4200)
        error = SimpleNamespace(is_error=True, content=[SimpleNamespace(text="Access denied")])
        with self.assertRaises(RuntimeError):
            formatear_resultado(error)
        self.assertEqual(nombre_registro("mi-servidor", "read.file"), "mcp_mi_servidor_read_file")


class RegistrySchemaTests(unittest.TestCase):
    def test_array_object_and_union_types(self) -> None:
        registry = ToolRegistry()
        registry.register(
            "complex",
            "Complex schema.",
            {
                "type": "object",
                "properties": {
                    "paths": {"type": "array"},
                    "options": {"type": "object"},
                    "limit": {"type": ["integer", "null"]},
                    "mode": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                },
                "required": ["paths"],
            },
            lambda **kwargs: kwargs,
        )
        self.assertEqual(
            registry.execute("complex", {"paths": ["a"], "options": {}, "limit": None, "mode": 3}),
            {"paths": ["a"], "options": {}, "limit": None, "mode": 3},
        )
        with self.assertRaises(ToolValidationError):
            registry.execute("complex", {"paths": "a"})
        with self.assertRaises(ToolValidationError):
            registry.execute("complex", {"paths": [], "limit": "diez"})


if __name__ == "__main__":
    unittest.main()
