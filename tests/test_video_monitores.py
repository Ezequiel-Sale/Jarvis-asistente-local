"""Vídeo al celular tras cambiar los monitores (sin capturar la pantalla de verdad)."""

import sys
import types
import unittest
from unittest.mock import Mock, patch

import jarvis_video_movil as v


class StaleMonitorListTests(unittest.TestCase):
    def test_retries_with_a_fresh_monitor_list(self) -> None:
        """«modo gaming 4K» cambia los monitores: la primera captura falla con la lista vieja de
        dxcam («Error no especificado»), se renueva la lista y la segunda funciona."""
        camara = Mock()
        falso = types.SimpleNamespace(create=Mock(side_effect=[OSError("Error no especificado"), camara]))
        with patch.dict(sys.modules, {"dxcam": falso}), patch.object(v, "_salida_dxgi", return_value=0), \
                patch.object(v, "_refrescar_dxcam") as refrescar:
            self.assertIs(v._abrir_camara(3), camara)
        refrescar.assert_called_once()

    def test_gives_up_after_one_retry(self) -> None:
        falso = types.SimpleNamespace(create=Mock(side_effect=OSError("Error no especificado")))
        with patch.dict(sys.modules, {"dxcam": falso}), patch.object(v, "_salida_dxgi", return_value=0), \
                patch.object(v, "_refrescar_dxcam"):
            with self.assertRaises(OSError):
                v._abrir_camara(3)
        self.assertEqual(falso.create.call_count, 2)

    def test_refresh_replaces_the_factory(self) -> None:
        class Singleton(type):
            _instances: dict = {}

        class Fabrica:
            def __init__(self):
                self.limpiada = False

            def clean_up(self):
                self.limpiada = True

        vieja = Fabrica()
        falso = types.ModuleType("dxcam")
        falso.Singleton = Singleton
        Singleton._instances[Fabrica] = vieja
        setattr(falso, "__factory", vieja)
        with patch.dict(sys.modules, {"dxcam": falso}):
            v._refrescar_dxcam()
        nueva = getattr(falso, "__factory")
        self.assertTrue(vieja.limpiada)
        self.assertIsNot(nueva, vieja)
        self.assertNotIn(Fabrica, Singleton._instances)


if __name__ == "__main__":
    unittest.main()
