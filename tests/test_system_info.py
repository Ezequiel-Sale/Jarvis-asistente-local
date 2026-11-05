import os
import unittest
from unittest.mock import Mock, patch

import jarvis_routines
import jarvis_system_info as info
from jarvis_actions import ResultadoAccion
from jarvis_commands import detectar_consulta_sistema
from jarvis_tool_registry import ToolRisk
from jarvis_tools import crear_registro_herramientas

GB = 1024**3


class FormattingTests(unittest.TestCase):
    def test_applications_are_grouped_without_titles_by_default(self) -> None:
        ventanas = [
            info.VentanaAbierta("Brave", "Correo privado - Bandeja"),
            info.VentanaAbierta("Brave", "YouTube"),
            info.VentanaAbierta("Discord", "Amigos"),
        ]
        texto = info.formatear_aplicaciones(ventanas)
        self.assertEqual(
            texto, "Tienes 2 aplicaciones con ventanas abiertas: Brave (2 ventanas), Discord."
        )
        self.assertNotIn("Correo privado", texto)

    def test_titles_only_when_requested_and_truncated(self) -> None:
        largo = "x" * 200
        texto = info.formatear_aplicaciones(
            [info.VentanaAbierta("Bloc de notas", largo)], incluir_titulos=True
        )
        self.assertTrue(texto.startswith("Ventanas abiertas:\n- Bloc de notas: «"))
        self.assertIn("…", texto)
        self.assertLess(len(texto), 140)

    def test_single_and_empty_applications(self) -> None:
        self.assertEqual(
            info.formatear_aplicaciones([info.VentanaAbierta("Steam", "Steam")]),
            "Tienes 1 aplicación con ventanas abiertas: Steam.",
        )
        self.assertEqual(
            info.formatear_aplicaciones([]), "No encontré aplicaciones con ventanas abiertas."
        )

    def test_processes_sorted_by_memory_and_cpu(self) -> None:
        procesos = [
            info.UsoProceso("Brave", 20, 2 * GB, 1.5),
            info.UsoProceso("Ollama (modelo cargado)", 1, 8 * GB, 0.2),
            info.UsoProceso("Juego", 1, 300 * 1024**2, 40.25),
        ]
        memoria = info.formatear_procesos(procesos, "memory", limite=2)
        self.assertEqual(
            memoria,
            "Programas que más memoria usan:\n"
            "1. Ollama (modelo cargado) — 8,0 GB\n"
            "2. Brave (20 procesos) — 2,0 GB",
        )
        cpu = info.formatear_procesos(procesos, "cpu", limite=1)
        self.assertEqual(cpu, "Programas que más CPU usan ahora mismo:\n1. Juego — 40,2 % de CPU")
        with self.assertRaises(ValueError):
            info.formatear_procesos(procesos, "disk")

    def test_system_status(self) -> None:
        estado = info.EstadoEquipo(
            cpu_porcentaje=12.4,
            nucleos=16,
            ram_usada=8 * GB,
            ram_total=32 * GB,
            disco_unidad="C:",
            disco_libre=124 * GB,
            disco_total=927 * GB,
            encendido_desde=1_000_000.0,
            bateria_porcentaje=80,
            bateria_cargando=True,
        )
        texto = info.formatear_estado(estado, ahora=1_000_000.0 + 26 * 3600 + 5 * 60)
        self.assertIn("CPU al 12 % (16 núcleos).", texto)
        self.assertIn("RAM: 8,0 GB de 32,0 GB en uso (25 %).", texto)
        self.assertIn("Disco C: 124 GB libres de 927 GB.", texto)
        self.assertIn("Encendido desde hace 1 d 2 h", texto)
        self.assertIn("Batería: 80 % (cargando).", texto)

    def test_friendly_names(self) -> None:
        self.assertEqual(info.nombre_aplicacion("brave.exe"), "Brave")
        self.assertEqual(info.nombre_aplicacion("ApplicationFrameHost.exe", "Calculadora"), "Calculadora")
        self.assertEqual(info.nombre_aplicacion("python.exe", pid=os.getpid()), "Jarvis")
        self.assertEqual(info.nombre_aplicacion("miapp.exe"), "Miapp")


class SystemQueryDetectionTests(unittest.TestCase):
    def test_recognized_queries(self) -> None:
        casos = {
            "¿Qué aplicaciones tengo abiertas?": ("list_open_applications", {"include_titles": False}),
            "Jarvis, qué ventanas hay": ("list_open_applications", {"include_titles": False}),
            "muéstrame los títulos de las ventanas abiertas": (
                "list_open_applications", {"include_titles": True},
            ),
            "¿Qué está consumiendo más memoria?": ("list_top_processes", {"sort_by": "memory"}),
            "qué usa más CPU ahora": ("list_top_processes", {"sort_by": "cpu"}),
            "dime los procesos que más RAM consumen": ("list_top_processes", {"sort_by": "memory"}),
            "¿Cómo está el equipo?": ("get_system_status", {}),
            "estado del sistema": ("get_system_status", {}),
            "¿cuánta RAM estoy usando?": ("get_system_status", {}),
            "cuánto espacio libre queda en el disco": ("get_system_status", {}),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_consulta_sistema(texto), esperado)

    def test_actions_and_unrelated_phrases_are_ignored(self) -> None:
        for texto in (
            "cierra las aplicaciones abiertas",
            "mata el proceso de Brave que más memoria usa",
            "abre las ventanas",
            "sube el volumen al 30%",
            "qué recuerdas sobre Brave",
            "qué acciones hiciste",
            "qué programas me recomiendas",
        ):
            with self.subTest(texto=texto):
                self.assertIsNone(detectar_consulta_sistema(texto))


class SystemToolRegistrationTests(unittest.TestCase):
    NAMES = ("list_open_applications", "list_top_processes", "get_system_status")

    def setUp(self) -> None:
        self.records: list[ResultadoAccion] = []
        self.registry = crear_registro_herramientas(Mock(), recorder=self.records.append)

    def test_tools_are_low_risk_read_only(self) -> None:
        for name in self.NAMES:
            definition = self.registry.get(name)
            self.assertIs(definition.risk, ToolRisk.LOW)
            self.assertFalse(definition.requires_confirmation)
            self.assertFalse(definition.record_result)
            self.assertNotIn(name, jarvis_routines._ROUTINE_ALLOWED_TOOLS)

    def test_result_is_returned_but_not_recorded(self) -> None:
        ventanas = [info.VentanaAbierta("Brave", "Banco - saldo")]
        with patch.object(info, "obtener_ventanas_abiertas", return_value=ventanas):
            result = self.registry.execute(
                "list_open_applications", {"include_titles": True}, origin="directo"
            )
        self.assertIn("Banco - saldo", result)
        self.assertEqual(len(self.records), 1)
        self.assertTrue(self.records[0].exitosa)
        self.assertIsNone(self.records[0].resultado)

    def test_process_arguments_are_validated(self) -> None:
        with patch.object(info, "obtener_uso_procesos", return_value=[]):
            self.assertEqual(
                self.registry.execute("list_top_processes", {"sort_by": "cpu", "limit": 3}),
                "No pude leer información de los procesos.",
            )
        with self.assertRaises(Exception):
            self.registry.execute("list_top_processes", {"limit": 99})


if __name__ == "__main__":
    unittest.main()
