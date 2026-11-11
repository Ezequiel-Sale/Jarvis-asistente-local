"""Servidor para la app del celular (chat y órdenes a distancia).

Seguridad (Jarvis corre como administrador y puede apagar el equipo, así que esto
importa):
- Desactivado por defecto; se activa en Ajustes.
- Escucha **solo en la dirección de Tailscale** de la PC (100.64.0.0/10): la red
  privada y cifrada entre los dispositivos del usuario. No se abre nada en el
  Wi-Fi ni en internet, y se rechaza cualquier cliente que no venga de Tailscale.
- Cada celular se vincula una vez con un código de 6 cifras que Jarvis muestra en
  la PC (vale 5 minutos y 5 intentos). A cambio recibe un token aleatorio; aquí
  solo se guarda su hash. Sin token válido, todo responde 401.
- Las órdenes entran por el mismo camino que las escritas en la PC: mismas
  herramientas, mismos permisos y mismas confirmaciones (que se responden desde
  el celular con SÍ / NO).
- El registro no guarda lo que se dice: solo cantidades.

API (JSON, ``Authorization: Bearer <token>`` salvo /api/pair):
- ``POST /api/pair`` {code, device} → {token, name}
- ``GET  /api/events?since=N&wait=25`` → {events, last, busy} (espera larga)
- ``POST /api/message`` {text}
- ``POST /api/audio`` (cuerpo: WAV) → se transcribe con el Whisper de la PC
- ``POST /api/answer`` {qid, approve}
- ``POST /api/cancel``
- ``GET  /api/question/<qid>/image`` → JPEG de la captura de una confirmación
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PUERTO = 8765
PREFERENCIA_ACTIVO = "movil"
PREFERENCIA_DISPOSITIVOS = "movil_dispositivos"
VIGENCIA_CODIGO = 300
MAX_INTENTOS = 5
MAX_TEXTO = 2000
MAX_AUDIO = 6 * 1024 * 1024  # ~3 min a 16 kHz mono
MAX_ESPERA = 25
RED_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


def es_tailscale(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in RED_TAILSCALE
    except ValueError:
        return False


def ip_tailscale() -> str | None:
    """Dirección de esta PC en Tailscale, o None si no está conectado."""
    import psutil

    for direcciones in psutil.net_if_addrs().values():
        for direccion in direcciones:
            if getattr(direccion.family, "name", "") == "AF_INET" and es_tailscale(direccion.address):
                return direccion.address
    return None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Vinculacion:
    """Códigos de vinculación y tokens de los celulares (sin red; se prueba sola)."""

    def __init__(
        self,
        cargar: Callable[[str], str | None],
        guardar: Callable[[str, str], None],
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cargar, self._guardar, self._reloj = cargar, guardar, reloj
        self._lock = threading.Lock()
        self._codigo: str | None = None
        self._vence = 0.0
        self._intentos = 0

    def _leer(self) -> list[dict]:
        try:
            datos = json.loads(self._cargar(PREFERENCIA_DISPOSITIVOS) or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        return [d for d in datos if isinstance(d, dict) and d.get("hash")] if isinstance(datos, list) else []

    def nuevo_codigo(self) -> str:
        with self._lock:
            self._codigo = f"{secrets.randbelow(1_000_000):06d}"
            self._vence = self._reloj() + VIGENCIA_CODIGO
            self._intentos = 0
            return self._codigo

    def segundos_restantes(self) -> int:
        with self._lock:
            return max(0, int(self._vence - self._reloj())) if self._codigo else 0

    def vincular(self, codigo: str, nombre: str) -> str | None:
        """Token nuevo si el código es el vigente; si no, None (y cuenta el intento)."""
        with self._lock:
            if self._codigo is None or self._reloj() > self._vence:
                self._codigo = None
                return None
            if not hmac.compare_digest(str(codigo).strip(), self._codigo):
                self._intentos += 1
                if self._intentos >= MAX_INTENTOS:
                    self._codigo = None  # hay que generar otro en la PC
                return None
            self._codigo = None  # un código vincula un solo celular
            token = secrets.token_urlsafe(32)
            dispositivos = self._leer()
            dispositivos.append({
                "nombre": " ".join(str(nombre or "Celular").split())[:40] or "Celular",
                "hash": _hash(token),
                "desde": time.strftime("%Y-%m-%d %H:%M"),
            })
            self._guardar(PREFERENCIA_DISPOSITIVOS, json.dumps(dispositivos[-10:], ensure_ascii=False))
            return token

    def autorizado(self, token: str | None) -> bool:
        if not token:
            return False
        buscado = _hash(token)
        return any(hmac.compare_digest(buscado, d["hash"]) for d in self._leer())

    def dispositivos(self) -> list[str]:
        return [f"{d.get('nombre', 'Celular')} (desde {d.get('desde', '?')})" for d in self._leer()]

    def desvincular_todos(self) -> None:
        with self._lock:
            self._codigo = None
            self._guardar(PREFERENCIA_DISPOSITIVOS, "[]")


INTERVALO_EN_VIVO = 0.4  # segundos entre actualizaciones de la respuesta en vivo


class RegistroEventos:
    """Lo que ve el celular: mensajes, acciones, preguntas y estado (en memoria)."""

    def __init__(self, maximo: int = 300) -> None:
        self._eventos: deque[dict] = deque(maxlen=maximo)
        self._ultimo = 0
        self._cond = threading.Condition()

    @property
    def ultimo(self) -> int:
        return self._ultimo

    def publicar(self, tipo: str, *, reemplaza: tuple[str, ...] = (), **datos) -> int:
        """Añade un evento; ``reemplaza`` quita antes los eventos de esos tipos.

        La respuesta en vivo («live») se publica muchas veces mientras se escribe: cada
        una reemplaza a la anterior y el mensaje final las quita, para que el historial
        que recibe el celular al abrir la app no se llene de borradores.
        """
        with self._cond:
            if reemplaza:
                vivos = [e for e in self._eventos if e["type"] not in reemplaza]
                self._eventos.clear()
                self._eventos.extend(vivos)
            self._ultimo += 1
            self._eventos.append({"id": self._ultimo, "type": tipo, "ts": int(time.time()), **datos})
            self._cond.notify_all()
            return self._ultimo

    def desde(self, ultimo: int, espera: float = 0.0, recientes: int = 40) -> list[dict]:
        """Eventos con id > ``ultimo``; con ``ultimo`` < 0, los más recientes. Espera si no hay."""
        with self._cond:
            if ultimo < 0:
                return list(self._eventos)[-recientes:]
            if ultimo > self._ultimo:
                ultimo = 0  # Jarvis se reinició: el celular tenía un id viejo
            limite = time.monotonic() + espera
            while self._ultimo <= ultimo:
                restante = limite - time.monotonic()
                if restante <= 0:
                    break
                self._cond.wait(restante)
            return [e for e in self._eventos if e["id"] > ultimo]


class _Error(Exception):
    def __init__(self, estado: int, mensaje: str) -> None:
        super().__init__(mensaje)
        self.estado, self.mensaje = estado, mensaje


class ServidorMovil:
    def __init__(
        self,
        eventos_app,
        vinculacion: Vinculacion,
        *,
        ocupado: Callable[[], bool] = lambda: False,
        nombre: str = "Jarvis",
    ) -> None:
        self.eventos_app = eventos_app  # cola de la app: todo lo que toca Tk pasa por ahí
        self.vinculacion = vinculacion
        self.registro = RegistroEventos()
        self.ocupado = ocupado
        self.nombre = nombre
        self._imagenes: dict[str, bytes] = {}
        self._pendiente: str | None = None
        self._http: ThreadingHTTPServer | None = None
        self.direccion: str | None = None
        self._vivo_enviado = 0.0  # última vez que se publicó la respuesta en vivo
        self._vivo_abierto = False

    # --- Lo que publica la app (hilo de Tk) -------------------------------------

    def mensaje(self, rol: str, texto: str) -> None:
        if rol == "user":
            self.registro.publicar("user", text=texto)
            return
        self._vivo_abierto = False
        self.registro.publicar("assistant", reemplaza=("live",), text=texto)

    def en_vivo(self, texto: str, *, reloj: Callable[[], float] = time.monotonic) -> bool:
        """Respuesta que se está escribiendo (texto completo hasta ahora).

        Como mucho una cada ``INTERVALO_EN_VIVO`` segundos: el mensaje final siempre
        llega con ``mensaje``, así que perder el último trozo no importa.
        Devuelve si se publicó.
        """
        if not texto:  # nueva ronda del modelo: lo escrito hasta ahora ya no vale
            self.fin_en_vivo()
            return False
        ahora = reloj()
        if self._vivo_abierto and ahora - self._vivo_enviado < INTERVALO_EN_VIVO:
            return False
        self._vivo_enviado = ahora
        self._vivo_abierto = True
        self.registro.publicar("live", reemplaza=("live",), text=texto)
        return True

    def fin_en_vivo(self) -> None:
        """La respuesta en vivo se descartó (cancelada o sin texto): el celular la quita."""
        if self._vivo_abierto:
            self._vivo_abierto = False
            self.registro.publicar("live", reemplaza=("live",), text="")

    def estado(self, modo: str) -> None:
        self.registro.publicar("state", mode=modo)

    def pregunta(self, qid: str, texto: str, si: str, no: str, imagen: bytes | None = None) -> None:
        self._imagenes.clear()
        if imagen:
            self._imagenes[qid] = imagen
        self._pendiente = qid
        self.registro.publicar("question", qid=qid, text=texto, yes=si, no=no, image=bool(imagen))

    def pregunta_resuelta(self, qid: str, texto: str) -> None:
        if self._pendiente == qid:
            self._pendiente = None
        self._imagenes.pop(qid, None)
        self.registro.publicar("resolved", qid=qid, text=texto)

    # --- HTTP ----------------------------------------------------------------------

    def activo(self) -> bool:
        return self._http is not None

    def iniciar(self, ip: str, puerto: int = PUERTO) -> None:
        if self._http is not None:
            return
        if not es_tailscale(ip):
            raise ValueError("El servidor del celular solo se abre en la dirección de Tailscale.")
        servidor = ThreadingHTTPServer((ip, puerto), _Manejador)
        servidor.daemon_threads = True
        servidor.movil = self
        self._http = servidor
        self.direccion = f"{ip}:{puerto}"
        threading.Thread(target=servidor.serve_forever, name="JarvisMovil", daemon=True).start()
        logging.info("Servidor del celular escuchando en Tailscale (puerto %s).", puerto)

    def detener(self) -> None:
        servidor, self._http, self.direccion = self._http, None, None
        if servidor is not None:
            threading.Thread(target=lambda: (servidor.shutdown(), servidor.server_close()), daemon=True).start()
            logging.info("Servidor del celular detenido.")

    def manejar(
        self, metodo: str, url: str, cabeceras, cuerpo: bytes, ip: str
    ) -> tuple[int, str, bytes]:
        """Atiende una petición. Devuelve (estado, tipo de contenido, cuerpo)."""
        try:
            return self._despachar(metodo, url, cabeceras, cuerpo, ip)
        except _Error as error:
            return error.estado, "application/json", json.dumps({"error": error.mensaje}).encode("utf-8")
        except Exception:
            logging.exception("Error al atender una petición del celular.")
            return 500, "application/json", b'{"error": "Error interno de Jarvis."}'

    def _json(self, datos: dict, estado: int = 200) -> tuple[int, str, bytes]:
        return estado, "application/json", json.dumps(datos, ensure_ascii=False).encode("utf-8")

    def _cuerpo_json(self, cuerpo: bytes) -> dict:
        try:
            datos = json.loads(cuerpo.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _Error(400, "El cuerpo no es JSON válido.") from error
        if not isinstance(datos, dict):
            raise _Error(400, "El cuerpo debe ser un objeto JSON.")
        return datos

    def _despachar(self, metodo, url, cabeceras, cuerpo, ip) -> tuple[int, str, bytes]:
        if not es_tailscale(ip):
            raise _Error(403, "Solo se aceptan conexiones por Tailscale.")
        partes = urlparse(url)
        ruta, consulta = partes.path.rstrip("/"), parse_qs(partes.query)
        if metodo == "POST" and ruta == "/api/pair":
            datos = self._cuerpo_json(cuerpo)
            token = self.vinculacion.vincular(str(datos.get("code", "")), str(datos.get("device", "")))
            if token is None:
                logging.warning("Intento de vinculación de un celular con un código no válido.")
                raise _Error(403, "Código incorrecto o vencido. Genera otro en Ajustes de Jarvis.")
            logging.info("Celular vinculado.")
            self.eventos_app.put(("mobile_paired",))
            return self._json({"token": token, "name": self.nombre})

        autorizacion = cabeceras.get("Authorization", "") or ""
        token = autorizacion[7:].strip() if autorizacion.lower().startswith("bearer ") else None
        if not self.vinculacion.autorizado(token):
            raise _Error(401, "Este celular no está vinculado con Jarvis.")

        if metodo == "GET" and ruta == "/api/events":
            try:
                desde = int(consulta.get("since", ["-1"])[0])
                espera = min(MAX_ESPERA, max(0.0, float(consulta.get("wait", ["0"])[0])))
            except ValueError as error:
                raise _Error(400, "Parámetros no válidos.") from error
            eventos = self.registro.desde(desde, espera)
            return self._json({"events": eventos, "last": self.registro.ultimo,
                               "busy": bool(self.ocupado()), "pending": self._pendiente})
        if metodo == "GET" and ruta.startswith("/api/question/") and ruta.endswith("/image"):
            imagen = self._imagenes.get(ruta.split("/")[3])
            if imagen is None:
                raise _Error(404, "Esa confirmación ya no tiene imagen.")
            return 200, "image/jpeg", imagen
        if metodo == "POST" and ruta == "/api/message":
            texto = " ".join(str(self._cuerpo_json(cuerpo).get("text", "")).split())
            if not texto:
                raise _Error(400, "El mensaje está vacío.")
            if len(texto) > MAX_TEXTO:
                raise _Error(413, f"El mensaje es demasiado largo (máximo {MAX_TEXTO} caracteres).")
            if self.ocupado():
                raise _Error(409, "Jarvis está ocupado; espera a que termine.")
            logging.info("Orden desde el celular (%s caracteres).", len(texto))
            self.eventos_app.put(("mobile_text", texto))
            return self._json({"ok": True})
        if metodo == "POST" and ruta == "/api/audio":
            if not cuerpo.startswith(b"RIFF") or len(cuerpo) < 1000:
                raise _Error(400, "El audio debe ser un archivo WAV.")
            if self.ocupado() and self._pendiente is None:
                raise _Error(409, "Jarvis está ocupado; espera a que termine.")
            descriptor, ruta_audio = tempfile.mkstemp(prefix="jarvis_movil_", suffix=".wav")
            with os.fdopen(descriptor, "wb") as archivo:
                archivo.write(cuerpo)
            logging.info("Audio desde el celular (%s KB).", len(cuerpo) // 1024)
            self.eventos_app.put(("mobile_audio", ruta_audio))
            return self._json({"ok": True})
        if metodo == "POST" and ruta == "/api/answer":
            datos = self._cuerpo_json(cuerpo)
            qid = str(datos.get("qid", ""))
            if qid != self._pendiente:
                raise _Error(409, "Esa confirmación ya se respondió o venció.")
            self.eventos_app.put(("mobile_answer", qid, bool(datos.get("approve"))))
            return self._json({"ok": True})
        if metodo == "POST" and ruta == "/api/cancel":
            self.eventos_app.put(("mobile_cancel",))
            return self._json({"ok": True})
        raise _Error(404, "Ruta desconocida.")


class _Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Jarvis"
    sys_version = ""

    def log_message(self, *_args) -> None:  # sin registro de peticiones (ni textos ni rutas)
        pass

    def _atender(self, metodo: str) -> None:
        movil: ServidorMovil = self.server.movil
        try:
            largo = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            largo = -1
        if largo < 0 or largo > MAX_AUDIO:
            estado, tipo, cuerpo = 413, "application/json", b'{"error": "Demasiado grande."}'
            self.close_connection = True
        else:
            datos = self.rfile.read(largo) if largo else b""
            estado, tipo, cuerpo = movil.manejar(metodo, self.path, self.headers, datos, self.client_address[0])
        self.send_response(estado)
        self.send_header("Content-Type", tipo + ("; charset=utf-8" if tipo == "application/json" else ""))
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self) -> None:  # noqa: N802 (nombre de http.server)
        self._atender("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._atender("POST")
