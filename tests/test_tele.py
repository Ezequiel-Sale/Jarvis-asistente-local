"""Control de la tele (jarvis_tv, jarvis_app_tv y /api/tv). Sin red ni tele real: la
conexión de androidtvremote2 se reemplaza por una falsa."""

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_app_tv as at
import jarvis_tv as tv
from jarvis_app import JarvisApp


class _RemotoFalso:
    """Imita AndroidTVRemote: guarda las teclas y apps que recibe."""

    def __init__(self, control, *, encendida=True, falla_conexion=False):
        self.control = control
        self.teclas, self.apps = [], []
        self.is_on, self.current_app = encendida, "com.netflix.ninja"
        self._falla = falla_conexion
        self.callbacks = {}

    async def async_generate_cert_if_missing(self):
        return True

    async def async_start_pairing(self):
        pass

    async def async_finish_pairing(self, codigo):
        if codigo != "ABC123":
            raise RuntimeError("código incorrecto")

    async def async_get_name_and_mac(self):
        return "TV de prueba", "AA:BB:CC:DD:EE:FF"

    async def async_connect(self):
        if self._falla:
            raise ConnectionError("sin red")

    def keep_reconnecting(self, invalid_auth_callback=None):
        self.callbacks["auth"] = invalid_auth_callback

    def disconnect(self):
        pass

    def send_key_command(self, tecla, direccion="SHORT"):
        self.teclas.append(tecla if direccion == "SHORT" else f"{tecla}:{direccion}")

    def send_launch_app_command(self, enlace):
        self.apps.append(enlace)

    def add_is_on_updated_callback(self, f):
        self.callbacks["on"] = f

    def add_current_app_updated_callback(self, f):
        self.callbacks["app"] = f

    def add_volume_info_updated_callback(self, f):
        self.callbacks["volume"] = f

    def add_is_available_updated_callback(self, f):
        self.callbacks["available"] = f


def _esperar_bucle(control):
    """Las teclas se mandan con call_soon_threadsafe: espera a que el bucle las procese."""
    asyncio.run_coroutine_threadsafe(asyncio.sleep(0), control._asegurar_bucle()).result(2)


class ControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.carpeta = Path(tempfile.mkdtemp())
        self.remotos = []

        def fabrica(control):
            remoto = _RemotoFalso(control, **getattr(self, "opciones", {}))
            self.remotos.append(remoto)
            return remoto

        self.cambios = Mock()
        self.en_la_red = []  # lo que «encuentra» la búsqueda por mDNS (nunca la red real)
        self.control = tv.ControlTV(self.carpeta, al_cambiar=self.cambios, fabrica=fabrica,
                                    buscador=lambda segundos: list(self.en_la_red))
        self.mac_actual = None    # lo que «responde» el ARP (nunca el real)
        self.tele_viva = True     # lo que «responde» la comprobación de conexión (nunca la real)
        self.control._mac_de_ip = lambda ip: self.mac_actual
        self.control._responde = lambda ip, puerto=6466: self.tele_viva
        self.addCleanup(self.control.detener)

    def _vincular(self):
        self.control.elegir("192.168.1.50", "TV de prueba")
        self.control.iniciar_vinculacion()
        (self.carpeta / "jarvis_tv_cert.pem").write_text("cert")  # lo crea la librería real
        self.control.terminar_vinculacion("abc 123")

    def test_pairing_saves_config_and_connects(self) -> None:
        self._vincular()
        datos = json.loads((self.carpeta / tv.ARCHIVO_CONFIG).read_text(encoding="utf-8"))
        self.assertEqual((datos["host"], datos["vinculada"], datos["mac"]), ("192.168.1.50", True, "AA:BB:CC:DD:EE:FF"))
        self.assertTrue(self.control.estado.vinculada and self.control.estado.conectada)
        self.assertEqual(self.control.estado.app, "com.netflix.ninja")
        # Otra instancia (al reiniciar Jarvis) recuerda la tele.
        otra = tv.ControlTV(self.carpeta)
        self.assertTrue(otra.estado.vinculada)
        self.assertEqual(otra.estado.nombre, "TV de prueba")

    def test_wrong_code_and_missing_steps(self) -> None:
        with self.assertRaises(tv.ErrorTV):
            self.control.iniciar_vinculacion()  # sin elegir la tele
        self.control.elegir("192.168.1.50")
        with self.assertRaises(tv.ErrorTV):
            self.control.terminar_vinculacion("ABC123")  # sin pulsar VINCULAR
        self.control.iniciar_vinculacion()
        with self.assertRaises(tv.ErrorTV):
            self.control.terminar_vinculacion("ZZZ999")
        with self.assertRaises(tv.ErrorTV):
            self.control.terminar_vinculacion("123")

    def test_keys_apps_and_volume(self) -> None:
        self._vincular()
        remoto = self.remotos[-1]
        self.control.tecla("volume_up", 3)
        self.control.tecla("hdmi2")
        self.assertEqual(self.control.abrir_app("netflix"), "Netflix")
        self.control.abrir_app("com.otra.app")
        remoto.callbacks["volume"]({"level": 20, "max": 100, "muted": False})
        self.control.fijar_volumen(25)
        _esperar_bucle(self.control)
        # La TCL ignora las teclas HDMI: la entrada se abre con su enlace «passthrough».
        self.assertEqual(remoto.teclas, ["KEYCODE_VOLUME_UP"] * 3 + ["KEYCODE_VOLUME_UP"] * 5)
        self.assertEqual(remoto.apps, [
            "content://android.media.tv/passthrough/com.tcl.tvinput%2F.passthroughinput.TvPassThroughService%2FHW1413744384",
            "https://www.netflix.com/title", "com.otra.app"])
        with self.assertRaises(tv.ErrorTV):
            self.control.tecla("autodestruccion")

    def test_settings_and_inputs_hold_home(self) -> None:
        """La TCL ignora SETTINGS y TV_INPUT: INICIO presionado abre el panel de Google TV."""
        self._vincular()
        remoto = self.remotos[-1]
        with patch.object(tv, "DURACION_LARGA", 0.0):
            self.control.tecla("settings")
            self.control.tecla("input")
            _esperar_bucle(self.control)
            time.sleep(0.05)
            _esperar_bucle(self.control)
        self.assertCountEqual(remoto.teclas, ["KEYCODE_HOME:START_LONG", "KEYCODE_HOME:END_LONG"] * 2)

    def test_already_on_and_power_off(self) -> None:
        self._vincular()
        remoto = self.remotos[-1]
        remoto.callbacks["on"](True)
        self.assertEqual(self.control.encender(), "La tele ya está encendida.")
        self.assertEqual(self.control.apagar(), "Apagué la tele.")
        _esperar_bucle(self.control)
        self.assertEqual(remoto.teclas, ["KEYCODE_POWER"])  # la TCL ignora SLEEP
        # POWER alterna: si ya está en reposo (control y Chromecast lo dicen), no se toca.
        self.control._estado_cast = lambda host: {"isStandBy": True}
        self.assertEqual(self.control.apagar(), "La tele ya está apagada.")
        self.control._estado_cast = lambda host: {"isStandBy": False}  # el aviso del control no llegó
        self.assertEqual(self.control.apagar(), "Apagué la tele.")
        _esperar_bucle(self.control)
        self.assertEqual(remoto.teclas, ["KEYCODE_POWER"] * 2)
        # Sin respuesta en la red: ya está apagada (no es un error).
        self.control.desconectar()
        self.opciones = {"falla_conexion": True}
        self.assertIn("ya está apagada", self.control.apagar())

    def _encender_desde_reposo(self, cast):
        """Tele en «Red en Espera»: el WOL la devuelve a la red y ``cast`` decide si enciende."""
        self._vincular()
        self.control.desconectar()
        self.tele_viva = False
        self.opciones = {"falla_conexion": True}
        reloj = iter(x / 2 for x in range(0, 2000))
        enviados = []

        def despierta(macs, ip):
            enviados.append(tuple(macs))
            self.tele_viva = True
            self.opciones = {"encendida": False}  # vuelve a la red, con la pantalla apagada

        self.control._lanzar_cast = cast
        self.control._estado_cast = lambda host: getattr(self, "estado_cast", None)
        self.cerradas = []
        self.control._cerrar_cast = lambda host, sesion: self.cerradas.append(sesion) or True
        with patch.object(tv, "despertar", side_effect=despierta), patch.object(tv.time, "sleep"), \
                patch.object(tv.time, "monotonic", side_effect=lambda: next(reloj)):
            try:
                return self.control.encender(), enviados
            finally:
                _esperar_bucle(self.control)

    def test_standby_tv_turns_on_with_chromecast_then_home(self) -> None:
        # En «Red en Espera» la TCL ignora WAKEUP/POWER pero se enciende con un pedido de Cast.
        pedidos = []

        def cast(host):
            pedidos.append(host)
            self.remotos[-1].callbacks["on"](True)  # la pantalla se enciende
            return "sesion-1"

        texto, enviados = self._encender_desde_reposo(cast)
        self.assertEqual(texto, "Encendí la tele.")
        self.assertEqual(enviados, [("AA:BB:CC:DD:EE:FF",)])
        self.assertEqual(pedidos, ["192.168.1.50"])
        # Se cierra la app de Cast (si no, queda una pantalla gris) y se pulsa INICIO.
        self.assertEqual(self.cerradas, ["sesion-1"])
        self.assertEqual(self.remotos[-1].teclas, ["KEYCODE_HOME"])

    def test_on_detected_by_cast_when_the_remote_does_not_say_it(self) -> None:
        # El 2026-11-13 la tele se encendió y el control no lo avisó: Jarvis dio error.
        self.estado_cast = {"isStandBy": False, "isActiveInput": True}
        texto, _ = self._encender_desde_reposo(lambda host: "")
        self.assertEqual(texto, "Encendí la tele.")
        self.assertEqual(self.cerradas, [""])

    def test_cast_accepted_but_screen_stays_off(self) -> None:
        self.estado_cast = {"isStandBy": True}
        with self.assertRaises(tv.ErrorTV) as error:
            self._encender_desde_reposo(lambda host: "")
        self.assertIn("no encendió la pantalla", str(error.exception))
        self.assertEqual(self.remotos[-1].teclas, ["KEYCODE_WAKEUP"])  # último intento

    def test_cast_message_format(self) -> None:
        mensaje = tv.mensaje_cast("urn:x-cast:com.google.cast.receiver", {"type": "LAUNCH", "appId": "233637DE"})
        largo = int.from_bytes(mensaje[:4], "big")
        self.assertEqual(largo, len(mensaje) - 4)
        self.assertTrue(mensaje[4:6] == b"\x08\x00")
        self.assertIn(b"receiver-0", mensaje)
        self.assertIn(b'"appId": "233637DE"', mensaje)
        self.assertEqual(tv._varint(300), b"\xac\x02")

    def test_reading_cast_messages(self) -> None:
        estado = {"type": "RECEIVER_STATUS", "status": {"isStandBy": False,
                                                         "applications": [{"appId": "233637DE", "sessionId": "abc"}]}}
        datos = tv.mensaje_cast("x", {"type": "LAUNCH_STATUS", "status": "USER_ALLOWED"}) + tv.mensaje_cast("x", estado)
        mensajes, resto = tv.leer_mensajes_cast(datos + datos[:7])  # el último llega a medias
        self.assertEqual([m["type"] for m in mensajes], ["LAUNCH_STATUS", "RECEIVER_STATUS"])
        self.assertEqual(resto, datos[:7])
        self.assertEqual(tv._estado_receptor(mensajes)["applications"][0]["sessionId"], "abc")

    def test_off_tv_uses_wake_on_lan(self) -> None:
        self._vincular()
        self.control.desconectar()
        self.tele_viva = False  # ni el WOL la devuelve a la red
        self.opciones = {"falla_conexion": True}
        enviados = []
        reloj = iter(range(0, 1000))  # cada consulta del reloj avanza 1 s: no espera de verdad
        with patch.object(tv, "despertar", side_effect=lambda macs, ip: enviados.extend(macs)), \
                patch.object(tv.time, "sleep"), patch.object(tv.time, "monotonic", side_effect=lambda: next(reloj)), \
                self.assertRaises(tv.ErrorTV) as error:
            self.control.encender()
        self.assertEqual(enviados, ["AA:BB:CC:DD:EE:FF"])
        self.assertIn("Red en Espera", str(error.exception))
        self.assertLess(next(reloj), 60)  # acotado por TIEMPO_DESPERTAR, no por los reintentos

    def test_stale_connection_goes_to_wake_on_lan(self) -> None:
        # Al pasar a reposo la tele no cerró la conexión: Jarvis decía «encendí» sin hacer nada.
        self._vincular()
        self.tele_viva = False
        self.opciones = {"falla_conexion": True}
        enviados = []
        reloj = iter(range(0, 1000))
        with patch.object(tv, "despertar", side_effect=lambda macs, ip: enviados.append((tuple(macs), ip))), \
                patch.object(tv.time, "sleep"), patch.object(tv.time, "monotonic", side_effect=lambda: next(reloj)), \
                self.assertRaises(tv.ErrorTV):
            self.control.encender()
        self.assertEqual(enviados, [(("AA:BB:CC:DD:EE:FF",), "192.168.1.50")])
        self.assertFalse(self.control.estado.conectada)

    def test_learns_the_mac_in_use(self) -> None:
        # Con Wi-Fi y cable la tele tiene dos MAC: se guardan las dos para el Wake-on-LAN.
        self.mac_actual = "11:22:33:44:55:66"
        self._vincular()
        self.assertEqual(self.control.macs(), ["AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"])
        datos = json.loads((self.carpeta / tv.ARCHIVO_CONFIG).read_text(encoding="utf-8"))
        self.assertEqual(datos["macs"], ["AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"])

    def test_background_retry_connects_when_the_tv_comes_back(self) -> None:
        self._vincular()
        self.control.desconectar()
        self.opciones = {"falla_conexion": True}
        self.assertFalse(self.control.intentar_en_segundo_plano())
        self.opciones = {}  # alguien prendió la tele con su control
        self.mac_actual = "11:22:33:44:55:66"
        self.assertTrue(self.control.intentar_en_segundo_plano())
        self.assertIn("11:22:33:44:55:66", self.control.macs())

    def test_wake_on_lan_goes_to_the_home_network(self) -> None:
        self.assertEqual(tv.destinos_despertar("192.168.1.102"), ["192.168.1.255", "255.255.255.255"])
        self.assertEqual(tv.destinos_despertar(""), ["255.255.255.255"])
        enviados = []
        tv.despertar(["AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"], "192.168.1.102",
                     enviar=lambda datos, destino: enviados.append((datos[6:12].hex(), destino)))
        self.assertEqual(enviados, [("aabbccddeeff", "192.168.1.255"), ("aabbccddeeff", "255.255.255.255"),
                                    ("112233445566", "192.168.1.255"), ("112233445566", "255.255.255.255")])

    def test_new_ip_is_found_by_name(self) -> None:
        # El router le dio otra IP a la tele (pasó de verdad: .101 → .102).
        self._vincular()
        self.control.desconectar()
        self.opciones = {"falla_conexion": True}
        self.assertFalse(self.control.conectar())  # en la red no aparece nada
        self.en_la_red = [("Otra tele", "192.168.1.77"), ("TV de prueba", "192.168.1.102")]
        intentos = []

        def fabrica_nueva(control):
            intentos.append(control.estado.host)
            remoto = _RemotoFalso(control, falla_conexion=control.estado.host != "192.168.1.102")
            self.remotos.append(remoto)
            return remoto

        self.control._fabrica = fabrica_nueva
        self.assertTrue(self.control.conectar())
        self.assertEqual(intentos, ["192.168.1.50", "192.168.1.102"])
        datos = json.loads((self.carpeta / tv.ARCHIVO_CONFIG).read_text(encoding="utf-8"))
        self.assertEqual(datos["host"], "192.168.1.102")
        self.assertTrue(datos["vinculada"])  # el certificado sigue valiendo

    def test_lost_authorization(self) -> None:
        self._vincular()
        self.remotos[-1].callbacks["auth"]()
        self.assertFalse(self.control.estado.vinculada)
        with self.assertRaises(tv.ErrorTV):
            self.control.tecla("home")

    def test_magic_packet(self) -> None:
        paquete = tv.paquete_magico("AA:BB:CC:DD:EE:FF")
        self.assertEqual(len(paquete), 102)
        self.assertEqual(paquete[:6], b"\xff" * 6)
        self.assertEqual(paquete[6:12], bytes.fromhex("AABBCCDDEEFF"))
        with self.assertRaises(ValueError):
            tv.paquete_magico("nope")


class PhraseTests(unittest.TestCase):
    def test_tv_phrases(self) -> None:
        casos = {
            "Prendé la tele": ("tv_control", {"action": "power_on"}),
            "apaga la tv": ("tv_control", {"action": "power_off"}),
            "Hey Jarvis, apagá la tele por favor": ("tv_control", {"action": "power_off"}),
            "Sube el volumen de la tele": ("tv_control", {"action": "volume_up", "steps": 5}),
            "bajale el volumen a la tele 10": ("tv_control", {"action": "volume_down", "steps": 10}),
            "pon el volumen de la tele al 20": ("tv_control", {"action": "set_volume", "level": 20}),
            "silencia la tele": ("tv_control", {"action": "mute"}),
            "pausa la tele": ("tv_control", {"action": "pause"}),
            "dale play en la tele": ("tv_control", {"action": "play"}),
            "Abrí Netflix en la tele": ("tv_open_app", {"app": "netflix"}),
            "pon Disney Plus en la tele": ("tv_open_app", {"app": "disney"}),
            "pon la tele en HDMI 2": ("tv_control", {"action": "hdmi2"}),
            "cambia a hdmi 1 en la tele": ("tv_control", {"action": "hdmi1"}),
            "inicio en la tele": ("tv_control", {"action": "home"}),
            "¿Qué está pasando en la tele?": ("tv_status", {}),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(tv.detectar(texto), esperado)

    def test_pc_orders_are_not_for_the_tv(self) -> None:
        for texto in ("sube el volumen", "abre youtube", "pausa el video del monitor 1", "apaga el equipo",
                      "manda Brave a la TV", "silencia discord"):
            with self.subTest(texto=texto):
                self.assertIsNone(tv.detectar(texto))


class AppTests(unittest.TestCase):
    def _host(self):
        host = Mock()
        host.tv.encender.return_value = "Encendí la tele."
        host.tv.abrir_app.return_value = "Netflix"
        return host

    def test_tools(self) -> None:
        host = self._host()
        self.assertEqual(JarvisApp._tool_tv_control(host, "power_on"), "Encendí la tele.")
        self.assertIn("Subí el volumen de la tele (4 pasos)", JarvisApp._tool_tv_control(host, "volume_up", 4))
        host.tv.tecla.assert_called_with("volume_up", 4)
        self.assertEqual(JarvisApp._tool_tv_control(host, "hdmi3"), "Puse la tele en la entrada HDMI 3.")
        self.assertEqual(JarvisApp._tool_tv_open_app(host, "netflix"), "Abrí Netflix en la tele.")
        with self.assertRaises(ValueError):
            JarvisApp._tool_tv_control(host, "set_volume")

    def test_status_text(self) -> None:
        estado = tv.EstadoTV(vinculada=True, conectada=True, encendida=True, nombre="TV de Habitación",
                             app="com.google.android.youtube.tv", volumen=12, volumen_max=100)
        self.assertEqual(at.describir_estado(estado),
                         "TV de Habitación está encendida, con YouTube; volumen en 12 de 100.")
        self.assertIn("no está vinculada", at.describir_estado(tv.EstadoTV()))
        self.assertIn("apagada", at.describir_estado(tv.EstadoTV(vinculada=True, conectada=True, encendida=False)))

    def test_mobile_buttons_go_through_the_registry(self) -> None:
        host = Mock()
        host.tool_registry.execute.return_value = "ok"
        JarvisApp._tv_action_from_mobile(host, "app:youtube")
        JarvisApp._tv_action_from_mobile(host, "volume_up")
        llamadas = [c.args[:2] for c in host.tool_registry.execute.call_args_list]
        self.assertEqual(llamadas, [("tv_open_app", {"app": "youtube"}), ("tv_control", {"action": "volume_up"})])
        host.tv.estado = tv.EstadoTV(app="com.nousguide.android.rbtv")
        datos = JarvisApp._tv_for_mobile(host)
        self.assertEqual(datos["appName"], "Red Bull TV")
        self.assertIn({"id": "netflix", "name": "Netflix"}, datos["apps"])

    def test_status_card_summary(self) -> None:
        """Tarjeta de estado del control (PC y celular): nombres legibles, no paquetes de Android."""
        self.assertEqual(at.etiqueta_app("com.google.android.apps.tv.dreamx"), "Protector de pantalla")
        self.assertEqual(at.etiqueta_app("com.google.android.apps.tv.launcherx"), "Inicio")
        self.assertEqual(at.etiqueta_app("com.tcl.tv"), "Entrada HDMI")
        encendida = tv.EstadoTV(vinculada=True, conectada=True, encendida=True, app="com.netflix.ninja",
                                volumen=11, volumen_max=100, silenciada=True)
        self.assertEqual(at.resumen_estado(encendida), ("Encendida", "Netflix · Volumen 11 · silenciada", "on"))
        self.assertEqual(at.resumen_estado(tv.EstadoTV(vinculada=True, conectada=True, encendida=False))[::2],
                         ("Apagada", "off"))
        self.assertEqual(at.resumen_estado(tv.EstadoTV(vinculada=True))[::2], ("Sin conexión", "error"))
        self.assertEqual(at.resumen_estado(tv.EstadoTV())[0], "Sin vincular")
        host = Mock()
        host.tv.estado = tv.EstadoTV(app="com.google.android.apps.tv.dreamx")
        self.assertEqual(JarvisApp._tv_for_mobile(host)["appName"], "Protector de pantalla")


class MobileEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        import queue

        import jarvis_mobile as m
        from test_movil import _pedir, _vincular

        self.pedidas = []

        def accion(nombre):
            self.pedidas.append(nombre)
            if nombre == "power_on":
                raise tv.ErrorTV("No encuentro la tele en la red.")
            return f"hecho {nombre}"

        guardado = {}
        self.servidor = m.ServidorMovil(queue.Queue(), m.Vinculacion(guardado.get, guardado.__setitem__),
                                        tele=lambda: {"on": True, "name": "TV"}, tele_accion=accion)
        self.token = _vincular(self.servidor)
        self.pedir = lambda metodo, cuerpo=None, token=self.token: _pedir(self.servidor, metodo, "/api/tv", cuerpo, token=token)

    def test_state_buttons_and_errors(self) -> None:
        self.assertEqual(self.pedir("GET"), (200, {"on": True, "name": "TV"}))
        estado, datos = self.pedir("POST", {"action": "volume_up"})
        self.assertEqual((estado, datos["text"], datos["tv"]["name"]), (200, "hecho volume_up", "TV"))
        self.assertEqual(self.pedir("POST", {"action": "app:netflix"})[0], 200)
        estado, datos = self.pedir("POST", {"action": "power_on"})
        self.assertEqual((estado, datos["error"]), (409, "No encuentro la tele en la red."))
        self.assertEqual(self.pedir("POST", {"action": "rm -rf /"})[0], 400)
        self.assertEqual(self.pedir("GET", token=None)[0], 401)
        self.assertEqual(self.pedidas, ["volume_up", "app:netflix", "power_on"])


if __name__ == "__main__":
    unittest.main()
