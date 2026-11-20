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
