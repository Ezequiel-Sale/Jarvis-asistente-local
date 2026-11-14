"""Control del televisor (Google TV / Android TV) por la red de casa.

Usa el protocolo de la app «Google TV» del celular (Android TV Remote v2) con la
librería ``androidtvremote2``: se busca la TV por mDNS (``_androidtvremote2._tcp``),
se vincula una vez con el código de 6 caracteres que muestra la pantalla y queda un
certificado propio en ``APP_DIR/tv`` (no sale de la PC), con ``tv.json`` (IP, nombre, MAC). Después Jarvis manda teclas
(volumen, reproducción, flechas, inicio, entradas HDMI), abre apps y sabe si la TV
está encendida, qué app tiene delante y su volumen.

Todo lo de red corre en un bucle asyncio propio, en un hilo; los métodos públicos son
síncronos (con tiempo máximo) para llamarlos desde las herramientas de Jarvis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ARCHIVO_CONFIG = "tv.json"     # {"host", "nombre", "mac", "vinculada"}, junto al certificado
SERVICIO_MDNS = "_androidtvremote2._tcp.local."
CLIENTE = "Jarvis"            # nombre que muestra la TV al vincular
TIEMPO_MAXIMO = 10.0
TIEMPO_DESPERTAR = 15.0  # a que la tele vuelva a la red tras el Wake-on-LAN
TIEMPO_ENCENDER = 20.0   # a que encienda la pantalla tras el pedido de Chromecast (medido: 2,5 s y más)
ESPERA_APP_CAST = 4.0    # a que termine de abrir la app de Cast antes de cerrarla (si no, queda encima)
# La herramienta tv_control tiene 75 s en total.

# Teclas por nombre corto (las mismas que usan los comandos de voz, la página y el celular).
TECLAS = {
    "power": "KEYCODE_POWER", "wakeup": "KEYCODE_WAKEUP", "sleep": "KEYCODE_SLEEP", "volume_up": "KEYCODE_VOLUME_UP", "volume_down": "KEYCODE_VOLUME_DOWN",
    "mute": "KEYCODE_VOLUME_MUTE", "play_pause": "KEYCODE_MEDIA_PLAY_PAUSE", "play": "KEYCODE_MEDIA_PLAY",
    "pause": "KEYCODE_MEDIA_PAUSE", "stop": "KEYCODE_MEDIA_STOP", "next": "KEYCODE_MEDIA_NEXT",
    "previous": "KEYCODE_MEDIA_PREVIOUS", "forward": "KEYCODE_MEDIA_FAST_FORWARD",
    "rewind": "KEYCODE_MEDIA_REWIND", "home": "KEYCODE_HOME", "back": "KEYCODE_BACK",
    "up": "KEYCODE_DPAD_UP", "down": "KEYCODE_DPAD_DOWN", "left": "KEYCODE_DPAD_LEFT",
    "right": "KEYCODE_DPAD_RIGHT", "ok": "KEYCODE_DPAD_CENTER", "settings": "KEYCODE_SETTINGS",
    "input": "KEYCODE_TV_INPUT", "channel_up": "KEYCODE_CHANNEL_UP", "channel_down": "KEYCODE_CHANNEL_DOWN",
    "hdmi1": "KEYCODE_TV_INPUT_HDMI_1", "hdmi2": "KEYCODE_TV_INPUT_HDMI_2",
    "hdmi3": "KEYCODE_TV_INPUT_HDMI_3", "hdmi4": "KEYCODE_TV_INPUT_HDMI_4",
}
# Comprobado con la TCL del usuario el 2026-11-13: ignora SETTINGS, TV_INPUT, MENU y las teclas
# HDMI. Ajustes y entradas se abren dejando INICIO presionado (el panel lateral de Google TV, con
# Configuración y Entradas), y cada HDMI se elige abriendo su entrada «passthrough» de Android TV.
TECLAS_LARGAS = {"settings": "KEYCODE_HOME", "input": "KEYCODE_HOME"}
DURACION_LARGA = 1.5
_ENTRADA_TCL = "content://android.media.tv/passthrough/com.tcl.tvinput%2F.passthroughinput.TvPassThroughService%2FHW{}"
ENTRADAS = {f"hdmi{n}": _ENTRADA_TCL.format(1413744128 + 256 * (n - 1)) for n in range(1, 5)}
# Apps: enlaces que Google TV abre en la app (los de Home Assistant) o el paquete de Android.
APPS = {
    "netflix": ("Netflix", "https://www.netflix.com/title"),
    "youtube": ("YouTube", "https://www.youtube.com"),
    "disney": ("Disney+", "https://www.disneyplus.com"),
    "prime": ("Prime Video", "https://app.primevideo.com"),
    "max": ("Max", "https://play.max.com"),
    "spotify": ("Spotify", "com.spotify.tv.android"),
    "twitch": ("Twitch", "tv.twitch.android.app"),
    "crunchyroll": ("Crunchyroll", "com.crunchyroll.crunchyroid"),
    "plex": ("Plex", "com.plexapp.android"),
    "paramount": ("Paramount+", "com.cbs.ott"),
    "apple": ("Apple TV", "com.apple.atve.androidtv.appletv"),
    "flow": ("Flow", "com.cablevision.flow"),
}
MAX_PASOS_VOLUMEN = 30
# Acciones de la herramienta tv_control, en el orden en que aparecen en las rutinas. «power»
# (alterna), «wakeup» y «sleep» quedan para uso interno: la TCL ignora las dos últimas.
ACCIONES = (
    "power_on", "power_off", "hdmi1", "hdmi2", "hdmi3", "hdmi4", "set_volume", "volume_up",
    "volume_down", "mute", "play_pause", "play", "pause", "stop", "next", "previous", "forward",
    "rewind", "home", "back", "settings", "input", "up", "down", "left", "right", "ok",
    "channel_up", "channel_down",
)


# Paquetes del sistema que no se ofrecen como apps (inicio, protector de pantalla, entradas,
# ajustes, asistente…): la tele los muestra como «app actual» pero no tiene sentido abrirlos.
_SISTEMA = ("com.google.android.apps.tv.", "com.google.android.tv", "com.google.android.katniss",
            "com.google.android.gms", "com.google.android.backdrop", "com.android.", "com.tcl.",
            "com.mediatek.", "android")


def es_app_de_usuario(paquete: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]\w*(?:\.[A-Za-z_]\w*)+", paquete or "")) and not paquete.startswith(_SISTEMA)


def _clave_nombre(texto: str) -> str:
    """«Star+» / «star plus» / «STAR» → «star» para comparar nombres dichos con los guardados."""
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c)).replace("+", " plus")
    return re.sub(r"[^a-z0-9]", "", texto)


class ErrorTV(RuntimeError):
    """Mensaje en español para el usuario."""


@dataclass
class EstadoTV:
    configurada: bool = False
    vinculada: bool = False
    conectada: bool = False
    encendida: bool | None = None
    nombre: str = ""
    host: str = ""
    app: str = ""
    volumen: int | None = None
    volumen_max: int | None = None
    silenciada: bool | None = None
    esperando_codigo: bool = False
    adb: str = ""  # jarvis_adb: conectado / sin_autorizar / sin_respuesta / sin_adb ("" = sin probar)
    extra: dict = field(default_factory=dict)

    def como_dict(self) -> dict:
        return {"configured": self.configurada, "paired": self.vinculada, "connected": self.conectada,
                "on": self.encendida, "name": self.nombre, "host": self.host, "app": self.app,
                "volume": self.volumen, "volumeMax": self.volumen_max, "muted": self.silenciada,
                "waitingCode": self.esperando_codigo}


def buscar(segundos: float = 4.0) -> list[tuple[str, str]]:
    """Televisores Google TV / Android TV de la red: [(nombre, ip)]."""
    from zeroconf import ServiceBrowser, Zeroconf

    encontrados: dict[str, tuple[str, str]] = {}

    class _Oyente:
        def add_service(self, zc, tipo, nombre):
            info = zc.get_service_info(tipo, nombre, timeout=3000)
            if info is None:
                return
            ipv4 = [d for d in info.parsed_addresses() if ":" not in d]
            if ipv4:
                encontrados[nombre] = (nombre.removesuffix("." + SERVICIO_MDNS), ipv4[0])

        def update_service(self, *_):
            pass

        def remove_service(self, *_):
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, SERVICIO_MDNS, _Oyente())
        time.sleep(segundos)
    finally:
        zc.close()
    return sorted(encontrados.values())


def paquete_magico(mac: str) -> bytes:
    """Wake-on-LAN: 6 × FF y 16 veces la MAC."""
    limpio = mac.replace(":", "").replace("-", "").strip()
    if len(limpio) != 12:
        raise ValueError("MAC inválida.")
    return b"\xff" * 6 + bytes.fromhex(limpio) * 16


def destinos_despertar(ip_tele: str) -> list[str]:
    """Adónde mandar el paquete: la difusión de la red de la tele (192.168.1.255) y la general.
    En Windows, 255.255.255.255 sale por una sola placa (a veces Tailscale u otra virtual) y no
    llega a la red de casa; la dirigida sí."""
    destinos = []
    partes = ip_tele.split(".")
    if len(partes) == 4 and all(p.isdigit() for p in partes):
        destinos.append(".".join(partes[:3] + ["255"]))
    destinos.append("255.255.255.255")
    return destinos


def despertar(macs, ip_tele: str = "", enviar: Callable[[bytes, str], None] | None = None) -> None:
    """Wake-on-LAN a todas las MAC conocidas de la tele (Wi-Fi y cable), 3 veces cada una."""
    if isinstance(macs, str):
        macs = [macs]
    paquetes = [paquete_magico(m) for m in macs if m]
    destinos = destinos_despertar(ip_tele)
    if enviar is not None:
        for datos in paquetes:
            for destino in destinos:
                enviar(datos, destino)
        return
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            for datos in paquetes:
                for destino in destinos:
                    for puerto in (9, 7):
                        try:
                            s.sendto(datos, (destino, puerto))
                        except OSError:
                            logging.debug("No se pudo mandar Wake-on-LAN a %s.", destino, exc_info=True)


# --- Cast v2 mínimo (puerto 8009) -----------------------------------------------------
# En «Red en Espera» la TCL ignora las teclas de encendido del protocolo del control (WAKEUP y
# POWER llegan, pero sigue apagada), pero sí se enciende al recibir un pedido de Chromecast,
# como hace Google Home. Medido el 2026-11-13: abrir YouTube por Cast la encendió en 2,5 s.
# Protocolo: mensajes CastMessage (protobuf) con JSON, sobre TLS, con 4 bytes de largo delante.
CAST_PUERTO = 8009
CAST_APP_DESPERTAR = "233637DE"  # YouTube (la más rápida en encender la pantalla)


def _varint(numero: int) -> bytes:
    salida = b""
    while True:
        byte = numero & 0x7F
        numero >>= 7
        salida += bytes([byte | (0x80 if numero else 0)])
        if not numero:
            return salida


def _campo_texto(campo: int, valor: str) -> bytes:
    datos = valor.encode()
    return bytes([(campo << 3) | 2]) + _varint(len(datos)) + datos


def mensaje_cast(espacio: str, datos: dict, destino: str = "receiver-0", origen: str = "sender-0") -> bytes:
    """CastMessage: versión 0, origen, destino, espacio, tipo texto y el JSON."""
    cuerpo = (b"\x08\x00" + _campo_texto(2, origen) + _campo_texto(3, destino) + _campo_texto(4, espacio)
              + b"\x28\x00" + _campo_texto(6, json.dumps(datos)))
    return len(cuerpo).to_bytes(4, "big") + cuerpo


_RECEPTOR = "urn:x-cast:com.google.cast.receiver"
_CONEXION = "urn:x-cast:com.google.cast.tp.connection"


def leer_mensajes_cast(datos: bytes) -> tuple[list[dict], bytes]:
    """Separa los mensajes completos (4 bytes de largo + CastMessage) y devuelve sus JSON y el
    resto sin terminar. Del protobuf solo interesa el JSON (campo 6), que va al final."""
    mensajes = []
    while len(datos) >= 4:
        largo = int.from_bytes(datos[:4], "big")
        if len(datos) < 4 + largo:
            break
        cuerpo, datos = datos[4:4 + largo], datos[4 + largo:]
        inicio = cuerpo.find(b'{"')
        if inicio >= 0:
            try:
                mensajes.append(json.loads(cuerpo[inicio:].decode("utf-8")))
            except ValueError:
                pass
    return mensajes, datos


def hablar_cast(host: str, pedidos: list[dict], hasta: Callable[[list[dict]], bool],
                segundos: float = 6.0) -> list[dict]:
    """Conecta por TLS al puerto 8009, manda CONNECT y ``pedidos`` al receptor y lee mensajes
    hasta que ``hasta(mensajes)`` sea cierto o pasen ``segundos``."""
    import ssl

    contexto = ssl.create_default_context()
    contexto.check_hostname = False
    contexto.verify_mode = ssl.CERT_NONE  # la tele usa un certificado propio (red de casa)
    recibidos: list[dict] = []
    with socket.create_connection((host, CAST_PUERTO), timeout=segundos) as crudo, \
            contexto.wrap_socket(crudo) as conexion:
        conexion.sendall(mensaje_cast(_CONEXION, {"type": "CONNECT"}))
        for pedido in pedidos:
            conexion.sendall(mensaje_cast(_RECEPTOR, pedido))
        conexion.settimeout(1.0)
        resto = b""
        limite = time.monotonic() + segundos
        while time.monotonic() < limite and not hasta(recibidos):
            try:
                trozo = conexion.recv(8192)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            if not trozo:
                break
            nuevos, resto = leer_mensajes_cast(resto + trozo)
            recibidos.extend(nuevos)
    return recibidos


def _estado_receptor(mensajes: list[dict]) -> dict | None:
    for mensaje in reversed(mensajes):
        if mensaje.get("type") == "RECEIVER_STATUS":
            return mensaje.get("status") or {}
    return None


def lanzar_cast(host: str, app_id: str = CAST_APP_DESPERTAR, segundos: float = 6.0) -> str | None:
    """Pide abrir una app por Chromecast. Devuelve el id de la sesión abierta ("" si la tele
    aceptó sin decirlo todavía) o None si no lo aceptó."""
    def con_sesion(mensajes):
        estado = _estado_receptor(mensajes) or {}
        return any(a.get("appId") == app_id for a in estado.get("applications") or [])

    mensajes = hablar_cast(host, [{"type": "LAUNCH", "appId": app_id, "requestId": 1}], con_sesion, segundos)
    estado = _estado_receptor(mensajes) or {}
    for app in estado.get("applications") or []:
        if app.get("appId") == app_id:
            return str(app.get("sessionId") or "")
    aceptado = any(m.get("type") == "LAUNCH_STATUS" and m.get("status") == "USER_ALLOWED" for m in mensajes)
    return "" if aceptado else None


def estado_cast(host: str, segundos: float = 3.0) -> dict | None:
    """Estado del receptor de Chromecast: {isStandBy, isActiveInput, applications…}."""
    mensajes = hablar_cast(host, [{"type": "GET_STATUS", "requestId": 2}],
                           lambda m: _estado_receptor(m) is not None, segundos)
    return _estado_receptor(mensajes)


def cerrar_cast(host: str, sesion: str = "", app_id: str = CAST_APP_DESPERTAR, segundos: float = 4.0) -> bool:
    """Cierra la app abierta por Cast (como «dejar de transmitir»): la tele vuelve a lo anterior."""
    if not sesion:
        estado = estado_cast(host) or {}
        sesion = next((str(a.get("sessionId")) for a in estado.get("applications") or []
                       if a.get("appId") == app_id and a.get("sessionId")), "")
    if not sesion:
        return False
    hablar_cast(host, [{"type": "STOP", "sessionId": sesion, "requestId": 3}],
                lambda m: _estado_receptor(m) is not None, segundos)
    return True


def mac_de_ip(ip: str) -> str | None:
    """MAC de la placa que usa esa IP en la red local (SendARP de Windows); None si no se sabe."""
    try:
        import ctypes

        destino = int.from_bytes(socket.inet_aton(ip), "little")
        mac = (ctypes.c_ubyte * 6)()
        largo = ctypes.c_ulong(6)
        if ctypes.windll.iphlpapi.SendARP(destino, 0, ctypes.byref(mac), ctypes.byref(largo)) != 0:
            return None
        return ":".join(f"{b:02X}" for b in bytes(mac)[:largo.value]) or None
    except (AttributeError, OSError, ValueError):
        return None


def responde(ip: str, puerto: int = 6466, segundos: float = 1.5) -> bool:
    """¿La tele acepta conexiones ahora? (una conexión vieja puede seguir «abierta» tras el reposo)."""
    try:
        with socket.create_connection((ip, puerto), timeout=segundos):
            return True
    except OSError:
        return False


class ControlTV:
    """Conexión con la TV. ``al_cambiar`` se llama (desde otro hilo) cuando cambia el estado."""

    def __init__(self, carpeta: Path, al_cambiar: Callable[[], None] | None = None, fabrica=None,
                 buscador: Callable[[float], list[tuple[str, str]]] | None = None,
                 nombrar: Callable[[str], str] | None = None, adb=None) -> None:
        self._carpeta = carpeta
        # Nombre legible de un paquete de Android (la app usa jarvis_app_tv.nombre_app).
        base = nombrar or (lambda paquete: paquete.rsplit(".", 1)[-1].capitalize())
        # Primero los nombres puestos a mano en tv.json («nombres»: {paquete: nombre}; queda en la PC).
        self._nombrar = lambda paquete: (self._config.get("nombres") or {}).get(paquete) or base(paquete)
        # jarvis_adb.ADB (la app lo pasa; los tests usan uno falso o ninguno): abre las apps sin
        # enlace web y lista las instaladas.
        self._adb = adb
        self._adb_revisado = 0.0
        self._buscar = buscador  # para los tests; por defecto, buscar() por mDNS
        self._mac_de_ip = None   # para los tests; por defecto, mac_de_ip() (ARP)
        self._responde = None    # para los tests; por defecto, responde() (conexión TCP)
        self._lanzar_cast = None  # para los tests; por defecto, lanzar_cast() (Chromecast)
        self._estado_cast = None  # para los tests; por defecto, estado_cast()
        self._cerrar_cast = None  # para los tests; por defecto, cerrar_cast()
        self._al_cambiar = al_cambiar or (lambda: None)
        self._fabrica = fabrica
        self._loop: asyncio.AbstractEventLoop | None = None
        self._hilo: threading.Thread | None = None
        self._remoto = None
        self._cerrojo = threading.Lock()
        self.estado = EstadoTV()
        self._config = self._leer_config()
        self._aplicar_config()

    # --- configuración -------------------------------------------------------
    @property
    def certificado(self) -> Path:
        return self._carpeta / "jarvis_tv_cert.pem"

    @property
    def clave(self) -> Path:
        return self._carpeta / "jarvis_tv_key.pem"

    def _leer_config(self) -> dict:
        try:
            datos = json.loads((self._carpeta / ARCHIVO_CONFIG).read_text(encoding="utf-8"))
            return datos if isinstance(datos, dict) else {}
        except (OSError, ValueError):
            return {}

    def _guardar_config(self) -> None:
        self._carpeta.mkdir(parents=True, exist_ok=True)
        (self._carpeta / ARCHIVO_CONFIG).write_text(json.dumps(self._config, ensure_ascii=False), encoding="utf-8")

    def _aplicar_config(self) -> None:
        self.estado.host = str(self._config.get("host") or "")
        self.estado.nombre = str(self._config.get("nombre") or "")
        self.estado.configurada = bool(self.estado.host)
        self.estado.vinculada = bool(self._config.get("vinculada")) and self.certificado.is_file()

    # --- bucle asyncio ---------------------------------------------------------
    def _asegurar_bucle(self) -> asyncio.AbstractEventLoop:
        with self._cerrojo:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._hilo = threading.Thread(target=self._loop.run_forever, name="JarvisTV", daemon=True)
                self._hilo.start()
            return self._loop

    def _ejecutar(self, corrutina, tiempo: float = TIEMPO_MAXIMO):
        futuro = asyncio.run_coroutine_threadsafe(corrutina, self._asegurar_bucle())
        try:
            return futuro.result(timeout=tiempo)
        except TimeoutError as error:
            futuro.cancel()
            raise ErrorTV("La tele no respondió a tiempo.") from error

    def _nuevo_remoto(self):
        if self._fabrica is not None:
            return self._fabrica(self)
        from androidtvremote2 import AndroidTVRemote

        self._carpeta.mkdir(parents=True, exist_ok=True)
        return AndroidTVRemote(CLIENTE, str(self.certificado), str(self.clave), self.estado.host,
                               loop=self._asegurar_bucle())

    def _escuchar(self, remoto) -> None:
        def encendida(valor):
            self.estado.encendida = valor
            self._al_cambiar()

        def app(valor):
            self.estado.app = valor or ""
            self._aprender_app(self.estado.app)
            self._al_cambiar()

        def volumen(info):
            self.estado.volumen = info.get("level") if isinstance(info, dict) else getattr(info, "level", None)
            self.estado.volumen_max = info.get("max") if isinstance(info, dict) else getattr(info, "max", None)
            self.estado.silenciada = info.get("muted") if isinstance(info, dict) else getattr(info, "muted", None)
            self._al_cambiar()

        def disponible(valor):
            self.estado.conectada = bool(valor)
            self._al_cambiar()

        remoto.add_is_on_updated_callback(encendida)
        remoto.add_current_app_updated_callback(app)
        remoto.add_volume_info_updated_callback(volumen)
        remoto.add_is_available_updated_callback(disponible)

    # --- vincular ---------------------------------------------------------------
    def elegir(self, host: str, nombre: str = "") -> None:
        """Guarda qué TV usar (al cambiar de TV hay que volver a vincular)."""
        if host != self._config.get("host"):
            self.desconectar()
            self._config = {"host": host, "nombre": nombre or host}
            self._guardar_config()
            self._aplicar_config()
            self._al_cambiar()

    def iniciar_vinculacion(self) -> None:
        """La TV muestra un código de 6 caracteres."""
        if not self.estado.host:
            raise ErrorTV("Primero elige la tele (botón BUSCAR en Ajustes).")
        self.desconectar()

        async def empezar():
            remoto = self._nuevo_remoto()
            await remoto.async_generate_cert_if_missing()
            await remoto.async_start_pairing()
            return remoto

        try:
            self._remoto = self._ejecutar(empezar())
        except ErrorTV:
            raise
        except Exception as error:
            raise ErrorTV(f"No pude empezar la vinculación ({error}). ¿La tele está encendida?") from error
        self.estado.esperando_codigo = True
        self._al_cambiar()

    def terminar_vinculacion(self, codigo: str) -> None:
        codigo = codigo.strip().upper().replace(" ", "")
        if not self.estado.esperando_codigo or self._remoto is None:
            raise ErrorTV("Primero pulsa VINCULAR para que la tele muestre el código.")
        if len(codigo) != 6:
            raise ErrorTV("El código de la tele tiene 6 caracteres.")
        remoto = self._remoto

        async def terminar():
            await remoto.async_finish_pairing(codigo)
            try:
                _nombre, mac = await remoto.async_get_name_and_mac()
            except Exception:
                mac = ""
            return mac

        try:
            mac = self._ejecutar(terminar(), tiempo=20)
        except ErrorTV:
            raise
        except Exception as error:
            raise ErrorTV(f"La tele no aceptó el código ({error}). Pulsa VINCULAR y prueba de nuevo.") from error
        self._config.update(vinculada=True, mac=mac or self._config.get("mac", ""))
        self._guardar_config()
        self.estado.esperando_codigo = False
        self._aplicar_config()
        self._remoto = None
        self.conectar()

    def olvidar(self) -> None:
        self.desconectar()
        self._config["vinculada"] = False
        self._guardar_config()
        self._aplicar_config()
        self._al_cambiar()

    # --- conexión ---------------------------------------------------------------
    def redescubrir(self, segundos: float = 3.0) -> bool:
        """El router puede darle otra IP a la tele (pasó el 2026-11-13: .101 → .102, y la .101
        quedó para la PC). Se la busca por su nombre en la red y se guarda la IP nueva."""
        nombre = self.estado.nombre
        try:
            encontradas = (self._buscar or buscar)(segundos)
        except Exception:
            logging.debug("No se pudo buscar la tele en la red.", exc_info=True)
            return False
        for encontrado, ip in encontradas:
            if encontrado == nombre and ip != self.estado.host:
                logging.info("La tele cambió de IP: %s → %s.", self.estado.host, ip)
                self.desconectar()
                self._config["host"] = ip
                self._guardar_config()
                self._aplicar_config()
                return True
        return False

    def conectar(self, *, redescubrir: bool = True) -> bool:
        """Conecta (y se reconecta solo) si la TV está vinculada. False si no se pudo ahora.
        Si no responde en su IP, la busca por nombre por si el router le dio otra."""
        if self._conectar_una_vez():
            return True
        return redescubrir and self.estado.vinculada and self.redescubrir() and self._conectar_una_vez()

    def _conectar_una_vez(self, silencioso: bool = False) -> bool:
        if not self.estado.vinculada:
            return False
        if self._remoto is not None and self.estado.conectada:
            return True

        async def abrir():
            remoto = self._nuevo_remoto()
            self._escuchar(remoto)
            await remoto.async_connect()
            remoto.keep_reconnecting(invalid_auth_callback=self._autorizacion_perdida)
            return remoto

        try:
            self._remoto = self._ejecutar(abrir())
        except Exception as error:
            (logging.debug if silencioso else logging.info)("No me pude conectar a la tele (%s).", error)
            self.estado.conectada = False
            if not silencioso:
                self._al_cambiar()
            return False
        self.estado.conectada = True
        self.estado.encendida = getattr(self._remoto, "is_on", None)
        self.estado.app = getattr(self._remoto, "current_app", None) or ""
        self._aprender_app(self.estado.app)
        self._aprender_mac()
        self._revisar_adb()
        self._al_cambiar()
        return True

    def macs(self) -> list[str]:
        """Todas las MAC conocidas de la tele (con Wi-Fi y cable tiene dos)."""
        lista = list(self._config.get("macs") or [])
        if self._config.get("mac") and self._config["mac"] not in lista:
            lista.insert(0, self._config["mac"])
        return lista

    def _aprender_mac(self) -> None:
        """Guarda la MAC de la placa que usa ahora la tele (la del cable, si está enchufado): la que
        informa al vincular puede ser la del Wi-Fi, y el Wake-on-LAN suele andar solo por cable."""
        mac = (self._mac_de_ip or mac_de_ip)(self.estado.host)
        conocidas = self.macs()
        if mac and mac not in conocidas and mac != "00:00:00:00:00:00":
            logging.info("Nueva MAC de la tele: %s (%s).", mac, self.estado.host)
            self._config["macs"] = [*conocidas, mac]
            self._guardar_config()

    def _sigue_viva(self) -> bool:
        """Al pasar a reposo la tele no siempre cierra la conexión: se comprueba que responda."""
        if not self.estado.conectada:
            return False
        if (self._responde or responde)(self.estado.host):
            return True
        logging.info("La conexión con la tele quedó colgada (no responde): la doy por cerrada.")
        self.desconectar()
        return False

    def intentar_en_segundo_plano(self) -> bool:
        """Cada minuto (desde la app): si está vinculada y desconectada, prueba conectarse sin
        avisar. Así, al prender la tele con su control, Jarvis se conecta solo y aprende su MAC."""
        if not self.estado.vinculada or self.estado.conectada or self.estado.esperando_codigo:
            return self.estado.conectada
        return self._conectar_una_vez(silencioso=True)

    def _autorizacion_perdida(self) -> None:
        logging.warning("La tele ya no reconoce a Jarvis: hay que volver a vincular.")
        self._config["vinculada"] = False
        self._guardar_config()
        self._aplicar_config()
        self.estado.conectada = False
        self._al_cambiar()

    def desconectar(self) -> None:
        remoto, self._remoto = self._remoto, None
        if remoto is not None and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(remoto.disconnect)
            except Exception:
                pass
        self.estado.conectada = False

    def _listo(self):
        if not self.estado.vinculada:
            raise ErrorTV("La tele no está vinculada: ve a ⚙ Ajustes → Televisor y pulsa VINCULAR.")
        if (self._remoto is None or not self.estado.conectada) and not self.conectar():
            raise ErrorTV("No encuentro la tele en la red. ¿Está enchufada y conectada al Wi-Fi?")
        return self._remoto

    # --- órdenes -----------------------------------------------------------------
    def tecla(self, nombre: str, veces: int = 1) -> None:
        if nombre not in TECLAS:
            raise ErrorTV(f"No conozco la tecla «{nombre}».")
        remoto = self._listo()
        if nombre in ENTRADAS:
            self._loop.call_soon_threadsafe(remoto.send_launch_app_command, ENTRADAS[nombre])
            return
        if nombre in TECLAS_LARGAS:
            codigo = TECLAS_LARGAS[nombre]

            def mantener():
                remoto.send_key_command(codigo, "START_LONG")
                self._loop.call_later(DURACION_LARGA, remoto.send_key_command, codigo, "END_LONG")
            self._loop.call_soon_threadsafe(mantener)
            return
        for _ in range(max(1, min(veces, MAX_PASOS_VOLUMEN))):
            self._loop.call_soon_threadsafe(remoto.send_key_command, TECLAS[nombre])

    # --- apps aprendidas ----------------------------------------------------------
    def _aprender_app(self, paquete: str) -> None:
        """La tele no dice qué apps tiene instaladas, pero sí cuál está abierta: cada app nueva
        (que no sea del sistema ni de la lista fija) se guarda en tv.json para ofrecerla."""
        if not es_app_de_usuario(paquete):
            return
        aprendidas = self._config.setdefault("apps", {})
        if paquete in aprendidas:
            return
        nombre = self._nombrar(paquete)
        if any(_clave_nombre(nombre) == _clave_nombre(fija) for fija, _enlace in APPS.values()):
            return  # ya está en la lista fija (Netflix, YouTube…)
        aprendidas[paquete] = nombre
        self._guardar_config()
        logging.info("Aprendí una app de la tele: %s (%s).", nombre, paquete)

    # --- ADB ---------------------------------------------------------------------------
    def _revisar_adb(self, forzar: bool = False) -> None:
        """En segundo plano, como mucho cada 10 min: conecta ADB y guarda las apps instaladas."""
        if self._adb is None or not self.estado.host:
            return
        if not forzar and time.monotonic() - self._adb_revisado < 600 and self._adb_revisado:
            return
        self._adb_revisado = time.monotonic()
        host = self.estado.host

        def revisar() -> None:
            try:
                self.estado.adb = self._adb.conectar(host)
                if self.estado.adb == "conectado":
                    instaladas = sorted(p for p in self._adb.apps(host) if es_app_de_usuario(p))
                    if instaladas and instaladas != self._config.get("instaladas"):
                        self._config["instaladas"] = instaladas
                        self._guardar_config()
                        logging.info("Apps de la tele (por ADB): %d.", len(instaladas))
            except Exception as error:
                logging.info("No se pudo revisar ADB en la tele (%s).", error)
            self._al_cambiar()

        threading.Thread(target=revisar, name="JarvisTVADB", daemon=True).start()

    def _paquetes_conocidos(self) -> list[str]:
        """Instaladas (por ADB) y aprendidas (al abrirlas), sin repetir."""
        paquetes = list(self._config.get("instaladas") or [])
        return paquetes + [p for p in self._config.get("apps") or {} if p not in paquetes]

    def apps(self) -> list[tuple[str, str]]:
        """(clave, nombre) de las apps que se ofrecen. Con la lista de ADB: las de la lista fija que
        están instaladas y después todas las demás; sin ella, la lista fija y las aprendidas."""
        # El nombre se recalcula (si mejora la forma de nombrar, se corrigen las ya guardadas).
        otras = {paquete: self._nombrar(paquete) for paquete in self._paquetes_conocidos()}
        fijas = [(clave, nombre) for clave, (nombre, _enlace) in APPS.items()]
        if self._config.get("instaladas"):
            instaladas = {_clave_nombre(nombre) for nombre in otras.values()}
            fijas = [(clave, nombre) for clave, nombre in fijas if _clave_nombre(nombre) in instaladas]
        repetidas = {_clave_nombre(nombre) for _clave, nombre in fijas}
        otras = {p: n for p, n in otras.items() if _clave_nombre(n) not in repetidas}
        return fijas + sorted(otras.items(), key=lambda par: par[1].casefold())

    def olvidar_app(self, paquete: str) -> bool:
        if paquete not in (self._config.get("apps") or {}):
            return False
        del self._config["apps"][paquete]
        self._guardar_config()
        return True

    def _resolver_app(self, app: str) -> tuple[str, str]:
        """(nombre, enlace) por clave de la lista fija, por nombre (fijo o aprendido) o por paquete."""
        texto = app.strip()
        buscado = _clave_nombre(texto)
        fija = APPS.get(texto.casefold()) or next(
            ((n, e) for n, e in APPS.values() if buscado and buscado == _clave_nombre(n)), None)
        if fija is not None:
            nombre, enlace = fija
            if "://" not in enlace:
                # Paquete: el instalado puede ser otro (Paramount+ es com.cbs.ca en Argentina).
                enlace = next((p for p in self._paquetes_conocidos()
                               if _clave_nombre(self._nombrar(p)) == _clave_nombre(nombre)), enlace)
            return nombre, enlace
        for paquete in self._paquetes_conocidos():
            nombre = self._nombrar(paquete)
            if texto == paquete or (buscado and buscado == _clave_nombre(nombre)):
                return nombre, paquete
        if es_app_de_usuario(texto) or "://" in texto:
            return texto, texto
        parecida = self._app_parecida(texto)
        if parecida is not None:
            return self._resolver_app(parecida)
        raise ErrorTV(f"No conozco la app «{texto}» en la tele. Ábrela una vez con el control de la tele "
                      "y la aprendo; o usa el nombre del paquete de Android.")

    def _app_parecida(self, texto: str, minimo: float = 0.8) -> str | None:
        """Nombre de app que suena como ``texto`` (Whisper oye «Tripley» como «triple ey», «triplay» o
        «triple A»), si hay uno claro; con la misma clave fonética que las ventanas («Cloudy» → Claude)."""
        from difflib import SequenceMatcher

        from jarvis_windows import clave_fonetica

        buscado = clave_fonetica(texto)
        if len(buscado) < 3:
            return None
        nombres = {nombre for nombre, _enlace in APPS.values()}
        nombres |= {self._nombrar(p) for p in self._paquetes_conocidos()}
        puntajes = sorted(((SequenceMatcher(None, buscado, clave_fonetica(n)).ratio(), n) for n in nombres),
                          reverse=True)
        if not puntajes or puntajes[0][0] < minimo:
            return None
        if len(puntajes) > 1 and puntajes[1][0] >= puntajes[0][0] - 0.05:
            return None  # dos parecidas por igual: mejor no adivinar
        logging.info("«%s» se tomó como la app %s de la tele (nombre parecido).", texto, puntajes[0][1])
        return puntajes[0][1]

    def abrir_app(self, app: str) -> str:
        """Enlace web por el protocolo del control; un paquete, por ADB (la TCL rechaza abrir
        paquetes por el control). Sin ADB se intenta igual por el control."""
        nombre, enlace = self._resolver_app(app)
        if "://" not in enlace and self._adb is not None and self.estado.host:
            try:
                if self._adb.abrir(self.estado.host, enlace):
                    return nombre
            except Exception as error:
                logging.info("ADB no pudo abrir %s (%s); pruebo por el control.", enlace, error)
        remoto = self._listo()
        self._loop.call_soon_threadsafe(remoto.send_launch_app_command, enlace)
        return nombre

    def encender(self) -> str:
        """1) Wake-on-LAN para despertar su red; 2) conexión del control; 3) pedido de Chromecast,
        que es lo que de verdad enciende la pantalla (en «Red en Espera» la TCL ignora WAKEUP y
        POWER, como comprobamos el 2026-11-13; Google Home la enciende igual, por Cast); 4) con la
        tele encendida, INICIO para que quede en la pantalla principal y no en YouTube."""
        if not self.estado.vinculada:
            raise ErrorTV("La tele no está vinculada: ve a ⚙ Ajustes → Televisor y pulsa VINCULAR.")
        if self._sigue_viva() and self.estado.encendida:
            return "La tele ya está encendida."
        macs = self.macs()
        if macs:
            logging.info("Encender la tele: Wake-on-LAN a %s.", ", ".join(macs))
            despertar(macs, self.estado.host)
        if not self._esperar_red():
            # En la TCL (Google TV) del usuario: Ajustes → Sistema → Encendido y energía → Modo de Espera.
            # El 2026-11-13 pasó con «Red en Espera» ya elegido: la tele se había apagado del todo.
            raise ErrorTV("La tele no aparece en la red: parece apagada del todo (sin corriente o en reposo "
                          "profundo), así que hay que prenderla con su control. Si pasa siempre, revisa en la tele "
                          "Ajustes → Sistema → Encendido y energía → Modo de Espera: tiene que estar «Red en Espera».")
        self._conectar_una_vez()
        try:
            sesion = (self._lanzar_cast or lanzar_cast)(self.estado.host)
        except OSError as error:
            logging.info("El pedido de Chromecast a la tele falló (%s).", error)
            sesion = None
        if self._esperar_encendida(TIEMPO_ENCENDER):
            self._volver_al_inicio(sesion)
            return "Encendí la tele."
        if self.estado.conectada:
            self.tecla("wakeup")  # por si en otro modo de reposo sí la obedece
        raise ErrorTV("La tele despertó su red pero no encendió la pantalla"
                      + ("" if sesion is not None else " (no aceptó el pedido de Chromecast)")
                      + ". Prueba de nuevo en unos segundos.")

    def _volver_al_inicio(self, sesion: str | None) -> None:
        """La app que abrió Cast para encenderla queda en pantalla (una pantalla gris): se espera a
        que termine de abrir (si se cierra antes, se vuelve a abrir encima), se cierra la sesión de
        Cast como al «dejar de transmitir» y se pulsa INICIO."""
        time.sleep(ESPERA_APP_CAST)
        try:
            (self._cerrar_cast or cerrar_cast)(self.estado.host, sesion or "")
        except OSError as error:
            logging.info("No se pudo cerrar la app de Cast en la tele (%s).", error)
        time.sleep(1.0)
        if self.estado.conectada or self._conectar_una_vez(silencioso=True):
            self.tecla("home")

    def _esperar_red(self) -> bool:
        """Hasta TIEMPO_DESPERTAR s a que la tele responda (Cast o control); a la mitad, la
        busca por nombre por si volvió con otra IP."""
        limite = time.monotonic() + TIEMPO_DESPERTAR
        buscada = False
        probar = self._responde or responde
        while time.monotonic() < limite:
            if probar(self.estado.host, CAST_PUERTO) or probar(self.estado.host):
                return True
            if not buscada and time.monotonic() > limite - TIEMPO_DESPERTAR / 2:
                buscada = True
                self.redescubrir()
            time.sleep(1)
        return False

    def _esperar_encendida(self, segundos: float) -> bool:
        """Encendida si lo dice el control (is_on) o Chromecast (``isStandBy`` falso): el aviso
        del control a veces no llega (el 2026-11-13 la tele se encendió y Jarvis dio error)."""
        limite = time.monotonic() + segundos
        consultar = self._estado_cast or estado_cast
        while time.monotonic() < limite:
            if self.estado.encendida:
                return True
            try:
                estado = consultar(self.estado.host)
            except OSError:
                estado = None
            if estado is not None and estado.get("isStandBy") is False:
                self.estado.encendida = True
                return True
            if not self.estado.conectada:
                self._conectar_una_vez(silencioso=True)
            time.sleep(1.0)
        return bool(self.estado.encendida)

    def apagar(self) -> str:
        """POWER, solo si está encendida: la TCL ignora SLEEP (comprobado el 2026-11-13) y POWER
        alterna, así que antes se confirma que esté prendida (control o Chromecast)."""
        if not self.estado.vinculada:
            raise ErrorTV("La tele no está vinculada: ve a ⚙ Ajustes → Televisor y pulsa VINCULAR.")
        if not (self._sigue_viva() or self._conectar_una_vez()):
            return "La tele ya está apagada (no responde en la red)."
        if not self.estado.encendida:
            try:
                estado = (self._estado_cast or estado_cast)(self.estado.host)
            except OSError:
                estado = None
            if not (estado is not None and estado.get("isStandBy") is False):
                return "La tele ya está apagada."
        self.tecla("power")
        self.estado.encendida = False
        return "Apagué la tele."

    def fijar_volumen(self, nivel: int) -> str:
        """Lleva el volumen a ``nivel`` (0-100 de la escala de la tele, si la informa)."""
        self._listo()
        actual, maximo = self.estado.volumen, self.estado.volumen_max
        if actual is None or not maximo:
            raise ErrorTV("La tele no informa su volumen; prueba con «sube el volumen de la tele 5».")
        objetivo = round(max(0, min(100, nivel)) * maximo / 100)
        pasos = objetivo - actual
        if pasos:
            self.tecla("volume_up" if pasos > 0 else "volume_down", abs(pasos))
        return f"Volumen de la tele al {nivel} %."

    def detener(self) -> None:
        self.desconectar()
        if self._adb is not None:
            self._adb.detener()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)


# --- Frases ---------------------------------------------------------------------

def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^\w\s%]", " ", texto)
    texto = re.sub(r"^(?:(?:hey|oye|ey)\s+)?(?:jarvis\s+)?(?:por\s+favor\s+)?", "", " ".join(texto.split()))
    return re.sub(r"\s+(?:por\s+favor|jarvis)$", "", texto)


_TELE = r"(?:la\s+)?(?:tele|tv|teve|televisor|television|smart\s*tv)"
_EN_TELE = rf"(?:\s+(?:de|a|en|para)\s+{_TELE})"
_NUM = r"(?P<n>\d{1,3})"
_PATRONES: list[tuple[re.Pattern, Callable[[re.Match], tuple[str, dict]]]] = []


def _frase(patron: str):
    def registrar(funcion):
        _PATRONES.append((re.compile(patron), funcion))
        return funcion
    return registrar


@_frase(rf"^(?:prende|prendeme|enciende|encende|enciendeme|activa)\s+{_TELE}$")
def _encender(_m):
    return "tv_control", {"action": "power_on"}


@_frase(rf"^(?:apaga|apagame|desactiva)\s+{_TELE}$")
def _apagar(_m):
    return "tv_control", {"action": "power_off"}


@_frase(rf"^(?:sube|subi|subile|subele|aumenta)\s+(?:el\s+)?(?:volumen|sonido){_EN_TELE}(?:\s+(?:en\s+)?{_NUM})?$")
def _subir(m):
    return "tv_control", {"action": "volume_up", "steps": int(m.group("n") or 5)}


@_frase(rf"^(?:baja|baji|bajale|bajele|disminuye)\s+(?:el\s+)?(?:volumen|sonido){_EN_TELE}(?:\s+(?:en\s+)?{_NUM})?$")
def _bajar(m):
    return "tv_control", {"action": "volume_down", "steps": int(m.group("n") or 5)}


@_frase(rf"^(?:pon|pone|deja|cambia)\s+(?:el\s+)?(?:volumen|sonido){_EN_TELE}\s+(?:al|en|a)\s+{_NUM}(?:\s*%|\s+por\s+ciento)?$")
def _volumen(m):
    return "tv_control", {"action": "set_volume", "level": min(100, int(m.group("n")))}


@_frase(rf"^(?:silencia|mutea|mutear|silenciar|desmutea|desilencia)\s+{_TELE}$|^(?:quita|quitale)\s+(?:el\s+)?silencio\s+a\s+{_TELE}$")
def _silencio(_m):
    return "tv_control", {"action": "mute"}


_MEDIA = {"pausa": "pause", "pausar": "pause", "para": "pause", "deten": "pause", "reanuda": "play",
          "dale play": "play", "play": "play", "reproduce": "play", "continua": "play",
          "adelanta": "forward", "retrocede": "rewind", "atrasa": "rewind", "siguiente": "next",
          "anterior": "previous"}
_MEDIA_RE = "|".join(sorted(map(re.escape, _MEDIA), key=len, reverse=True))


@_frase(rf"^(?P<v>{_MEDIA_RE})(?:\s+(?:el\s+video|la\s+serie|la\s+peli(?:cula)?|eso))?\s+(?:en|de|a)\s+{_TELE}$"
        rf"|^(?P<v2>{_MEDIA_RE})\s+{_TELE}$")
def _media(m):
    return "tv_control", {"action": _MEDIA[m.group("v") or m.group("v2")]}


_APPS_DICHAS = {
    "netflix": "netflix", "youtube": "youtube", "you tube": "youtube", "disney": "disney",
    "disney plus": "disney", "disney+": "disney", "prime": "prime", "prime video": "prime",
    "amazon prime": "prime", "amazon": "prime", "max": "max", "hbo": "max", "hbo max": "max",
    "spotify": "spotify", "twitch": "twitch", "crunchyroll": "crunchyroll", "plex": "plex",
    "paramount": "paramount", "paramount plus": "paramount", "apple tv": "apple", "flow": "flow",
}
_APPS_RE = "|".join(sorted(map(re.escape, _APPS_DICHAS), key=len, reverse=True))


@_frase(rf"^(?:abre|abri|abrime|pon|pone|ponme|inicia|lanza)\s+(?:el\s+|la\s+)?(?P<app>{_APPS_RE})\s+(?:en|de)\s+{_TELE}$")
def _app(m):
    return "tv_open_app", {"app": _APPS_DICHAS[m.group("app")]}


@_frase(rf"^(?:pon|pone|cambia|pasa)\s+(?:{_TELE}\s+)?(?:(?:a|en)\s+)?(?:la\s+)?(?:entrada\s+)?hdmi\s*(?P<n>[1-4])"
        rf"(?:\s+(?:en|de)\s+{_TELE})?$")
def _hdmi(m):
    return "tv_control", {"action": f"hdmi{m.group('n')}"}


@_frase(rf"^(?:ve\s+al\s+|volve\s+al\s+|vuelve\s+al\s+)?inicio\s+(?:en|de)\s+{_TELE}$|^(?:pon|lleva)\s+{_TELE}\s+al\s+inicio$")
def _inicio(_m):
    return "tv_control", {"action": "home"}


@_frase(rf"^(?:atras|volve\s+atras|vuelve\s+atras)\s+(?:en|de)\s+{_TELE}$")
def _atras(_m):
    return "tv_control", {"action": "back"}


@_frase(rf"^(?:como\s+esta|que\s+(?:esta\s+)?(?:pasando|viendo|hay)\s+en|que\s+tiene)\s+{_TELE}$|^estado\s+de\s+{_TELE}$")
def _estado(_m):
    return "tv_status", {}


@_frase(rf"^(?:abre|abri|abrime|inicia|lanza)\s+(?:el\s+|la\s+)?(?P<app>[a-z0-9][a-z0-9 ]{{1,30}}?)\s+(?:en|de)\s+{_TELE}$")
def _app_libre(m):
    """Cualquier otro nombre («abre Star en la tele»): la herramienta lo busca entre las aprendidas."""
    return "tv_open_app", {"app": m.group("app")}


def detectar(texto: str) -> tuple[str, dict[str, object]] | None:
    """«Prende la tele», «sube el volumen de la tele 10», «abre Netflix en la tele»,
    «pon la tele en HDMI 2», «pausa la tele»… (solo con «tele/TV/televisor» en la frase)."""
    consulta = _normalizar(texto)
    if not re.search(r"\b(?:tele|tv|teve|televisor|television|smart\s*tv)\b", consulta):
        return None
    for patron, funcion in _PATRONES:
        coincidencia = patron.fullmatch(consulta)
        if coincidencia:
            return funcion(coincidencia)
    return None