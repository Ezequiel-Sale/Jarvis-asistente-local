"""Antes de apagar, dejar la PC por cable (jarvis_red): salidas de netsh simuladas, sin tocar la red."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_power
import jarvis_red as r

NETSH = """
Estado admin.    Estado          Tipo             Nombre interfaz
-------------------------------------------------------------------------
Habilitado     Conectado      Dedicado         Radmin VPN
Habilitado     Conectado      Dedicado         Ethernet
Habilitado     Conectado      Dedicado         Wi-Fi
Habilitado     Conectado      Dedicado         Tailscale
"""


class InterfacesTests(unittest.TestCase):
    def test_reads_physical_adapters_only(self):
        self.assertEqual(r.interfaces(NETSH), [r.Interfaz("Ethernet", True, True), r.Interfaz("Wi-Fi", True, True)])
        ingles = "Admin State    State          Type             Interface Name\n" \
                 "Disabled       Disconnected   Dedicated        Ethernet\n"
        self.assertEqual(r.interfaces(ingles), [r.Interfaz("Ethernet", False, False)])


class PrepararTests(unittest.TestCase):
    def preparar(self, *lecturas):
        lecturas = list(lecturas)
        netsh = Mock()
        texto = r.preparar_para_encender(leer=lambda: lecturas.pop(0) if len(lecturas) > 1 else lecturas[0],
                                         netsh=netsh, dormir=lambda _s: None, espera=1)
        return texto, [c.args for c in netsh.call_args_list]

    def test_wifi_connected_with_cable_goes_to_cable(self):
        texto, llamadas = self.preparar([r.Interfaz("Ethernet", True, True), r.Interfaz("Wi-Fi", True, True)])
        self.assertIn("Pasé la conexión del Wi-Fi al cable", texto)
        self.assertEqual(llamadas, [("wlan", "disconnect", "interface=Wi-Fi")])

    def test_already_on_cable(self):
        texto, llamadas = self.preparar([r.Interfaz("Ethernet", True, True), r.Interfaz("Wi-Fi", True, False)])
        self.assertIn("Ya estaba conectada por cable", texto)
        self.assertEqual(llamadas, [])

    def test_disabled_cable_is_enabled_first(self):
        texto, llamadas = self.preparar([r.Interfaz("Ethernet", False, False), r.Interfaz("Wi-Fi", True, True)],
                                        [r.Interfaz("Ethernet", True, True), r.Interfaz("Wi-Fi", True, True)])
        self.assertEqual(llamadas[0], ("interface", "set", "interface", "name=Ethernet", "admin=enabled"))
        self.assertEqual(llamadas[1], ("wlan", "disconnect", "interface=Wi-Fi"))
        self.assertIn("Pasé la conexión", texto)

    def test_unplugged_cable_keeps_wifi_and_warns(self):
        texto, llamadas = self.preparar([r.Interfaz("Ethernet", True, False), r.Interfaz("Wi-Fi", True, True)])
        self.assertIn("el cable de red no está conectado", texto)
        self.assertEqual(llamadas, [])  # sin cable no se corta el Wi-Fi

    def test_no_cable_adapter(self):
        texto, _ = self.preparar([r.Interfaz("Wi-Fi", True, True)])
        self.assertIn("no tiene placa de red por cable", texto)


class EnergiaTests(unittest.TestCase):
    def test_shutdown_and_sleep_check_the_network_first(self):
        antes = Mock(return_value="Pasé la conexión del Wi-Fi al cable.")
        with patch.object(jarvis_power, "antes_de_apagar", antes), \
                patch.object(jarvis_power, "programar_apagado", return_value="Apagaré el equipo en 10 segundos."), \
                patch.object(jarvis_power, "suspender_equipo", return_value="Suspenderé el equipo en 3 segundos."):
            self.assertEqual(jarvis_power.ejecutar_accion_energia("shutdown"),
                             "Pasé la conexión del Wi-Fi al cable. Apagaré el equipo en 10 segundos.")
            jarvis_power.ejecutar_accion_energia("sleep")
            jarvis_power.ejecutar_accion_energia("restart")
        self.assertEqual(antes.call_count, 2)  # reiniciar no hace falta

    def test_windows_shutdown_notice_runs_once(self):
        preparar = Mock(return_value="ok")
        vigilante = r.VigilanteApagado(preparar)
        vigilante.al_apagar()
        vigilante.al_apagar()
        preparar.assert_called_once()

    def test_wired_in_the_app(self):
        fuente = (Path(__file__).resolve().parent.parent / "jarvis_app.py").read_text("utf-8")
        self.assertIn("jarvis_power.antes_de_apagar = jarvis_red.preparar_sin_fallar", fuente)
        self.assertIn("jarvis_red.VigilanteApagado().start()", fuente)


if __name__ == "__main__":
    unittest.main()


class VigiaArranqueTests(unittest.TestCase):
    def test_watchdog_writes_stacks_only_if_startup_hangs(self):
        import faulthandler
        import tempfile
        import time

        from jarvis_app import _vigia_de_arranque

        with tempfile.TemporaryDirectory() as carpeta:
            destino = Path(carpeta) / "logs" / "arranque_trabado.txt"
            apagar = _vigia_de_arranque(destino, segundos=0.2)
            time.sleep(0.6)  # «se trabó»: escribe las pilas
            apagar()
            self.assertIn("Thread 0x", destino.read_text(encoding="utf-8"))
            apagar = _vigia_de_arranque(destino, segundos=30)
            apagar()  # arrancó bien: no escribe nada más que la cabecera
            self.assertNotIn("Thread 0x", destino.read_text(encoding="utf-8"))
        faulthandler.cancel_dump_traceback_later()


class CargaNativaTests(unittest.TestCase):
    def test_the_three_native_loads_take_turns(self):
        """2026-11-20 (pilas del vigía): openwakeword, MediaPipe y .NET cargándose a la vez trababan el arranque."""
        import jarvis_core

        self.assertTrue(hasattr(jarvis_core.CERROJO_CARGA_NATIVA, "acquire"))
        raiz = Path(__file__).resolve().parent.parent
        for modulo in ("jarvis_hardware.py", "jarvis_gestos.py", "jarvis_wakeword.py"):
            self.assertIn("CERROJO_CARGA_NATIVA", (raiz / modulo).read_text("utf-8"), modulo)


class PlacaTests(unittest.TestCase):
    """2026-11-20: habilitar o deshabilitar la placa de cable desde la vista SISTEMA."""

    def test_disable_and_enable(self):
        estado = {"Ethernet": True}
        leer = lambda: [r.Interfaz("Ethernet", estado["Ethernet"], estado["Ethernet"]), r.Interfaz("Wi-Fi", True, False)]  # noqa: E731

        def netsh(*argumentos):
            estado["Ethernet"] = argumentos[-1] == "admin=enabled"
            return Mock(stdout="")

        self.assertEqual(r.cambiar_placa("ethernet", "off", leer=leer, netsh=netsh, dormir=lambda _s: None),
                         "Listo: deshabilité la placa «Ethernet».")
        self.assertFalse(r.placa_habilitada("Ethernet", leer=leer))
        self.assertIn("ya estaba deshabilitada", r.cambiar_placa("Ethernet", "off", leer=leer, netsh=netsh))
        self.assertIn("habilité", r.cambiar_placa("Ethernet", "on", leer=leer, netsh=netsh, dormir=lambda _s: None))
        with self.assertRaisesRegex(ValueError, "No encontré"):
            r.cambiar_placa("Radmin VPN", "off", leer=leer, netsh=netsh)

    def test_undo_and_risk(self):
        import jarvis_deshacer
        from jarvis_tool_registry import ToolRisk
        from jarvis_tools import crear_registro_herramientas

        historial = jarvis_deshacer.Historial({"placa": lambda _n: True})
        historial.anotar(historial.capturar("network_adapter", {"name": "Ethernet", "state": "off"}, "directo"))
        self.assertEqual(historial.sacar().argumentos, {"name": "Ethernet", "state": "on"})
        self.assertIs(crear_registro_herramientas(Mock()).get("network_adapter").risk, ToolRisk.HIGH)
        fuente = (Path(__file__).resolve().parent.parent / "jarvis_app_ordenes.py").read_text("utf-8")
        self.assertIn('"network_adapter")', fuente)  # por botón o por voz, siempre pregunta
