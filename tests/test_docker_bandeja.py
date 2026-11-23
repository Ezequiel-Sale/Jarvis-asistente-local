"""Docker Desktop a la bandeja cuando los contenedores de Jarvis están listos (2026-11-20)."""

import unittest
from pathlib import Path
from unittest.mock import Mock

import jarvis_windows as w


class OcultarDockerTests(unittest.TestCase):
    def test_closes_only_docker_window_when_it_appears(self):
        lecturas = [[w.Ventana(1, "Brave", "brave", 10)],
                    [w.Ventana(1, "Brave", "brave", 10), w.Ventana(2, "Containers - Docker Desktop", "docker desktop", 20)]]
        cerrar = Mock()
        cerradas = w.ocultar_docker_desktop(listar=lambda: lecturas.pop(0), cerrar=cerrar, dormir=lambda _s: None)
        self.assertEqual(cerradas, 1)
        cerrar.assert_called_once_with(2)

    def test_no_window_gives_up(self):
        cerrar = Mock()
        self.assertEqual(w.ocultar_docker_desktop(esperar=0, listar=lambda: [], cerrar=cerrar), 0)
        cerrar.assert_not_called()

    def test_runs_after_docker_services_are_ready(self):
        fuente = (Path(__file__).resolve().parent.parent / "jarvis_app.py").read_text("utf-8")
        listo = fuente.index('self.events.put(("docker_services_ready",))')
        self.assertLess(listo, fuente.index("jarvis_windows.ocultar_docker_desktop()"))


if __name__ == "__main__":
    unittest.main()
