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
- ``GET  /api/screens`` → {allowed, screens: [{number, width, height, primary}]}
- ``GET  /api/screen?n=0&w=1280&q=60`` → JPEG de la pantalla en vivo (n=0: todas
  juntas). Solo para ver: no se puede controlar la PC. Se desactiva en Ajustes, y
  mientras un celular mira, la PC muestra un aviso.
- ``POST /api/tts`` {text} → WAV con la voz de Piper elegida en la PC (la app la
  usa para leer las respuestas con la misma voz que Jarvis).
- Los avisos (recordatorios, alertas del equipo) llegan como mensajes ``assistant``
  con ``alert`` = "alarm" o "info"; el celular los muestra como notificación.
- ``GET  /api/media`` → {playing: {app, state, title, artist, cover}, apps: [{app,
  name, volume, muted}], volume, output, outputs}; ``GET /api/media/cover`` → carátula.
- ``POST /api/media`` {tool, args} → una de ``HERRAMIENTAS_MOVIL`` (riesgo bajo) por
  ``tool_registry.execute``, con registro, como si se pidiera en la PC.
- ``GET  /api/status?fps=1`` → temperaturas, cargas, RAM, discos, historial de ~10 min
  y, con ``fps=1``, los FPS de la ventana en primer plano (PresentMon).
- ``POST /api/share`` {text} → un link se abre en Brave (``navigate_browser``); otro
  texto va al portapapeles de la PC.
- ``POST /api/upload?name=foto.jpg`` (cuerpo: el archivo, con Content-Length) → se
  guarda en Descargas sin cargarlo entero en memoria (hasta 4 GB).
- ``GET  /api/files/roots`` → carpetas permitidas (Descargas, Documentos y los otros
  discos, «disco:D»); ``GET /api/files?root=…&path=sub/carpeta`` → lista;
  ``GET /api/files/download?root=…&path=…`` → el archivo, por partes. Sin salir de
  esas carpetas (jarvis_archivos.resolver).
- ``GET/POST /api/clipboard`` {text} → leer o escribir el portapapeles de la PC.
- Eventos ``file`` {root, path, name, size} y ``clipboard`` {text}: lo que Jarvis
  le manda al celular (herramientas ``send_file_to_phone`` y ``send_clipboard_to_phone``).
- ``GET  /api/wol`` → MAC de las placas, IP, difusión de la red de casa y qué falta
  configurar (jarvis_encendido); el celular lo guarda para encender la PC sin Jarvis.
- ``GET  /api/app`` → {versionCode, versionName, size, sha256} de la última app
  compilada (``movil/Jarvis.apk`` y ``Jarvis.apk.json`` junto a Jarvis); 404 si no hay.
- ``GET  /api/app/apk`` → el APK, para que la app se actualice sola.
- ``GET  /api/video?n=1&fps=60&w=1920&kbps=8000`` → vídeo H.264 en vivo de un
  monitor, como flujo continuo de paquetes: 4 bytes de largo (big endian), 1 byte
  de banderas y el contenido. Banderas: 1 = cuadro clave H.264 (Annex B); 2 = puntero
  (">ff" con x, y en fracción del monitor, o vacío si no se ve); 8 = formato del
  audio (frecuencia >I y canales >H) y 4 = audio PCM s16le, si se pidió ``audio=1``.
  Uno a la vez: abrir otro corta el anterior. Mismo permiso y aviso que /api/screen.
  /api/screen manda el puntero en la cabecera ``X-Cursor`` ("x,y" o "none").
- ``GET/POST /api/control`` {on} → pedir o cortar el control del mouse y el teclado:
  hace falta el ajuste de la PC y aprobar (en la PC o con huella en el celular) la
  herramienta de riesgo alto ``mobile_remote_control``.
- ``POST /api/input`` {events} → movimientos, clics, rueda y teclas (jarvis_input),
  solo con el control activo.
- ``GET  /api/tv`` → estado de la tele (encendida, app, volumen, apps); ``POST /api/tv``
  {action} → un botón del control remoto (``tv_control``, o ``app:<id>`` → ``tv_open_app``)
  por ``tool_registry.execute`` (jarvis_tv).
- ``POST /api/vision`` {n, x, y, w, h, question} → «¿Qué es esto?» sobre una zona del
  monitor (fracciones 0–1); la respuesta llega como un mensaje más.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import queue
from pathlib import Path
import secrets
import socket
import struct
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import urllib.parse
from urllib.parse import parse_qs, urlparse

PUERTO = 8765
PREFERENCIA_ACTIVO = "movil"
PREFERENCIA_DISPOSITIVOS = "movil_dispositivos"
VIGENCIA_CODIGO = 300
MAX_INTENTOS = 5
MAX_TEXTO = 2000
MAX_AUDIO = 6 * 1024 * 1024  # ~3 min a 16 kHz mono
MAX_ESPERA = 25
PREFERENCIA_PANTALLA = "movil_pantalla"
PREFERENCIA_CONTROL = "movil_control"  # "1": se puede pedir el control (desactivado por defecto)
MIRANDO_SEGUNDOS = 4.0  # sin capturas en este tiempo = el celular dejó de mirar
ANCHO_PANTALLA = (480, 2560, 1280)  # mínimo, máximo y por defecto
CALIDAD_PANTALLA = (25, 85, 60)
HERRAMIENTAS_MOVIL = frozenset({"media_control", "set_app_volume", "set_system_volume", "set_output_device"})
ANCHO_VIDEO = (640, 2560, 1920)
KBPS_VIDEO = (1000, 30000, 8000)
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
        pantallas: Callable[[], list[dict]] | None = None,
        captura: Callable[[int, int, int], bytes] | None = None,
        pantalla_permitida: Callable[[], bool] = lambda: True,
        video: Callable[..., object] | None = None,
        carpeta_app: Path | None = None,
        cursor: Callable[[int], tuple[float, float] | None] | None = None,
        audio: Callable[[threading.Event], object] | None = None,
        entrada: Callable[[list], int] | None = None,
        control_permitido: Callable[[], bool] = lambda: False,
        carpetas: Callable[[str], Path] | None = None,
        raices: Callable[[], list[dict]] | None = None,
        abrir_link: Callable[[str], str] | None = None,
        portapapeles: tuple[Callable[[], str], Callable[[str], None]] | None = None,
        encendido: Callable[[], dict] | None = None,
        tts: Callable[[str], bytes] | None = None,
        musica: Callable[[], dict] | None = None,
        portada: Callable[[], bytes | None] | None = None,
        accion: Callable[[str, dict], str] | None = None,
        estado_equipo: Callable[[bool], dict] | None = None,
        tele: Callable[[], dict] | None = None,
        tele_accion: Callable[[str], str] | None = None,
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
        self.pantallas = pantallas
        self.captura = captura  # (monitor, ancho, calidad) → JPEG; corre en el hilo HTTP
        self.pantalla_permitida = pantalla_permitida
        self._captura_lock = threading.Lock()
        self._ultima_captura: float | None = None
        self.video = video  # (monitor, fps, ancho, kbps, detener) → paquetes (bytes, clave)
        self._video_detener: threading.Event | None = None
        self._video_cerrado: threading.Event | None = None  # el anterior ya soltó la captura
        self.cursor = cursor  # monitor → (x, y) en fracción, o None
        self.audio = audio  # detener → bloques PCM (el primero, el formato)
        self.entrada = entrada  # eventos del touchpad → SendInput
        self.control_permitido = control_permitido
        self.carpetas = carpetas  # "descargas"/"documentos"/"disco:D" → carpeta real
        self.raices = raices or (lambda: [{"key": "descargas", "name": "Descargas"},
                                          {"key": "documentos", "name": "Documentos"}])
        self.abrir_link = abrir_link  # url → texto (por tool_registry.execute)
        self.portapapeles = portapapeles  # (leer, escribir)
        self.encendido = encendido  # diagnóstico de Wake-on-LAN
        self._control = False
        self._ultimo_control = 0.0
        self.carpeta_app = carpeta_app  # donde están Jarvis.apk y Jarvis.apk.json
        self._app_cache: tuple[tuple, dict] | None = None
        self.tts = tts  # texto → WAV de Piper (corre en el hilo HTTP)
        self.musica = musica
        self.portada = portada
        self.accion = accion  # (herramienta, argumentos) → texto; pasa por tool_registry.execute
        self.estado_equipo = estado_equipo
        self.tele = tele  # estado de la tele (jarvis_tv)
        self.tele_accion = tele_accion  # botón del control → texto; pasa por tool_registry.execute

    # --- Lo que publica la app (hilo de Tk) -------------------------------------

    def mensaje(self, rol: str, texto: str, aviso: str | None = None) -> None:
        """``aviso`` ("alarm" o "info"): no responde a una orden (recordatorio, alerta)."""
        if rol == "user":
            self.registro.publicar("user", text=texto)
            return
        self._vivo_abierto = False
        extra = {"alert": aviso} if aviso else {}
        self.registro.publicar("assistant", reemplaza=("live",), text=texto, **extra)

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

    def pregunta(self, qid: str, texto: str, si: str, no: str, imagen: bytes | None = None,
                 alto_riesgo: bool = False) -> None:
        """``alto_riesgo``: el celular pide huella antes de enviar el SÍ."""
        self._imagenes.clear()
        if imagen:
            self._imagenes[qid] = imagen
        self._pendiente = qid
        self.registro.publicar("question", qid=qid, text=texto, yes=si, no=no, image=bool(imagen),
                               high=bool(alto_riesgo))

    def pregunta_resuelta(self, qid: str, texto: str) -> None:
        if self._pendiente == qid:
            self._pendiente = None
        self._imagenes.pop(qid, None)
        self.registro.publicar("resolved", qid=qid, text=texto)

    def _app(self) -> tuple[dict, Path]:
        """Datos de la última app compilada (se recalculan si cambia el archivo)."""
        if self.carpeta_app is None:
            raise _Error(404, "No hay ninguna versión de la app en la PC.")
        apk = self.carpeta_app / "Jarvis.apk"
        datos = self.carpeta_app / "Jarvis.apk.json"
        try:
            firma = (apk.stat().st_mtime_ns, apk.stat().st_size, datos.stat().st_mtime_ns)
        except OSError as error:
            raise _Error(404, "No hay ninguna versión de la app en la PC.") from error
        if self._app_cache is None or self._app_cache[0] != firma:
            try:
                version = json.loads(datos.read_text(encoding="utf-8-sig"))  # PowerShell 5 escribe BOM
                info = {
                    "versionCode": int(version["versionCode"]),
                    "versionName": str(version.get("versionName", "")),
                    "size": firma[1],
                    "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
                }
            except (OSError, ValueError, KeyError, TypeError) as error:
                logging.warning("No se pudo leer la versión de la app del celular: %s", error)
                raise _Error(404, "La versión de la app en la PC está incompleta.") from error
            self._app_cache = (firma, info)
        return self._app_cache[1], apk

    # --- Control del mouse y el teclado -----------------------------------------------

    def control_activo(self) -> bool:
        return self._control and bool(self.control_permitido())

    def activar_control(self, activo: bool) -> None:
        """Lo llama la herramienta ``mobile_remote_control`` (o el corte desde la PC)."""
        if activo == self._control:
            return
        self._control = activo
        self._ultimo_control = time.monotonic()
        logging.info("Control de la PC desde el celular %s.", "activado" if activo else "cortado")
        self.registro.publicar("control", active=activo)
        self.eventos_app.put(("mobile_control_changed",))

    def control_inactivo(self, ahora: float | None = None, limite: float = 120.0) -> bool:
        """Sin usar el touchpad ni mirar la pantalla en ``limite`` s: se corta solo."""
        ahora = time.monotonic() if ahora is None else ahora
        return ahora - self._ultimo_control > limite and not self.mirando(ahora)

    # --- Archivos y portapapeles -------------------------------------------------------------

    def _carpeta(self, consulta: dict) -> tuple[str, Path]:
        import jarvis_archivos

        raiz = consulta.get("root", ["descargas"])[0]
        if self.carpetas is None or not self.raiz_valida(raiz):
            raise _Error(400, "Solo se puede usar Descargas, Documentos o los otros discos.")
        return raiz, self.carpetas(raiz)

    def raiz_valida(self, raiz: str) -> bool:
        return any(r["key"] == raiz for r in self.raices())

    def subir(self, url: str, cabeceras, ip: str, origen, largo: int) -> tuple[int, str, bytes]:
        """Archivo del celular a Descargas, leyendo el cuerpo por partes (sin pasar por ``manejar``)."""
        import jarvis_archivos

        try:
            if not es_tailscale(ip):
                raise _Error(403, "Solo se aceptan conexiones por Tailscale.")
            autorizacion = cabeceras.get("Authorization", "") or ""
            token = autorizacion[7:].strip() if autorizacion.lower().startswith("bearer ") else None
            if not self.vinculacion.autorizado(token):
                raise _Error(401, "Este celular no está vinculado con Jarvis.")
            if self.carpetas is None:
                raise _Error(501, "Recibir archivos no está disponible.")
            nombre = parse_qs(urlparse(url).query).get("name", [""])[0]
            try:
                ruta = jarvis_archivos.guardar_subida(origen, largo, nombre, self.carpetas("descargas"))
            except jarvis_archivos.ErrorArchivo as error:
                raise _Error(400, str(error)) from error
            except OSError as error:
                raise _Error(507, f"No pude guardar el archivo: {error}") from error
            logging.info("Archivo recibido del celular (%s).", jarvis_archivos.tamano_legible(largo))
            self.eventos_app.put(("mobile_file_received", ruta.name, largo))
            return self._json({"ok": True, "name": ruta.name})
        except _Error as error:
            return error.estado, "application/json", json.dumps({"error": error.mensaje}).encode("utf-8")

    def mirando(self, ahora: float | None = None) -> bool:
        """Si algún celular está viendo la pantalla ahora mismo."""
        if self._ultima_captura is None:
            return False
        ahora = time.monotonic() if ahora is None else ahora
        return ahora - self._ultima_captura < MIRANDO_SEGUNDOS

    @staticmethod
    def _numero(consulta: dict, nombre: str, limites: tuple[int, int, int]) -> int:
        minimo, maximo, defecto = limites
        try:
            valor = int(consulta.get(nombre, [str(defecto)])[0])
        except ValueError as error:
            raise _Error(400, "Parámetros no válidos.") from error
        return max(minimo, min(maximo, valor))

    def _marcar_mirando(self) -> None:
        empezo = not self.mirando()
        self._ultima_captura = time.monotonic()
        if empezo:
            logging.info("Un celular empezó a ver la pantalla.")
            self.eventos_app.put(("mobile_screen",))

    def _pantalla(self, consulta: dict) -> tuple[int, str, bytes]:
        if self.captura is None or not self.pantalla_permitida():
            raise _Error(403, "Ver la pantalla está desactivado en los Ajustes de Jarvis.")

        def numero(nombre: str, limites: tuple[int, int, int]) -> int:
            return self._numero(consulta, nombre, limites)

        try:
            monitor = max(0, int(consulta.get("n", ["0"])[0]))
        except ValueError as error:
            raise _Error(400, "Parámetros no válidos.") from error
        ancho, calidad = numero("w", ANCHO_PANTALLA), numero("q", CALIDAD_PANTALLA)
        if not self._captura_lock.acquire(timeout=5):
            raise _Error(503, "La PC está ocupada capturando; reintenta.")
        try:
            imagen = self.captura(monitor, ancho, calidad)
        except ValueError as error:  # monitor inexistente
            raise _Error(404, str(error)) from error
        except OSError as error:
            raise _Error(503, f"No pude capturar la pantalla: {error}") from error
        finally:
            self._captura_lock.release()
        self._marcar_mirando()
        posicion = self.cursor(monitor) if self.cursor else None
        cabecera = "none" if posicion is None else f"{posicion[0]},{posicion[1]}"
        return 200, "image/jpeg", imagen, {"X-Cursor": cabecera}

    def _video(self, consulta: dict):
        """Prepara el flujo de vídeo; devuelve la función que lo escribe en la conexión."""
        if self.video is None or not self.pantalla_permitida():
            raise _Error(403, "Ver la pantalla está desactivado en los Ajustes de Jarvis.")
        try:
            monitor = int(consulta.get("n", ["1"])[0])
        except ValueError as error:
            raise _Error(400, "Parámetros no válidos.") from error
        if monitor < 1:
            raise _Error(400, "El vídeo es de un monitor a la vez.")
        con_audio = consulta.get("audio", ["0"])[0] == "1" and self.audio is not None
        fps = self._numero(consulta, "fps", (15, 60, 30))
        ancho = self._numero(consulta, "w", ANCHO_VIDEO)
        kbps = self._numero(consulta, "kbps", KBPS_VIDEO)
        detener = threading.Event()
        cerrado = threading.Event()
        anterior, self._video_detener = self._video_detener, detener
        anterior_cerrado, self._video_cerrado = self._video_cerrado, cerrado
        if anterior is not None:
            anterior.set()  # un solo vídeo a la vez
        # dxcam comparte la captura de cada monitor: si el anterior la suelta después de
        # que el nuevo la tomó, el nuevo se queda congelado. Se espera a que la suelte.
        if anterior_cerrado is not None and not anterior_cerrado.wait(3):
            logging.warning("El vídeo anterior del celular tardó en cerrarse.")
        try:
            paquetes = iter(self.video(monitor, fps, ancho, kbps, detener))
            primero = next(paquetes)  # los errores de arranque salen como respuesta HTTP
        except ValueError as error:
            cerrado.set()
            raise _Error(404, str(error)) from error
        except StopIteration as error:
            cerrado.set()
            raise _Error(503, "El vídeo terminó antes de empezar.") from error
        except OSError as error:
            cerrado.set()
            raise _Error(501, f"No pude iniciar el vídeo: {error}") from error
        except BaseException:
            cerrado.set()
            raise

        def escribir(salida) -> None:
            enviados = 0
            sonido: queue.Queue[bytes] = queue.Queue(maxsize=100)
            if con_audio:
                threading.Thread(target=self._leer_audio, args=(detener, sonido), name="JarvisAudioMovil",
                                 daemon=True).start()
            ultimo_cursor: object = ()
            try:
                for datos, clave in _encadenar(primero, paquetes):
                    if detener.is_set() or not self.pantalla_permitida() or not self.activo():
                        break
                    salida.write(struct.pack(">IB", len(datos), 1 if clave else 0) + datos)
                    if self.cursor is not None:
                        posicion = self.cursor(monitor)
                        if posicion != ultimo_cursor:  # solo si se movió
                            ultimo_cursor = posicion
                            carga = b"" if posicion is None else struct.pack(">ff", *posicion)
                            salida.write(struct.pack(">IB", len(carga), 2) + carga)
                    while not sonido.empty():
                        bloque = sonido.get_nowait()
                        banderas = 8 if len(bloque) == 6 and bloque[:1] == b"\x00" else 4
                        salida.write(struct.pack(">IB", len(bloque), banderas) + bloque)
                    enviados += 1
                    if enviados % 15 == 1:
                        self._marcar_mirando()
            except OSError:
                pass  # el celular cerró la vista
            finally:
                detener.set()
                cerrar = getattr(paquetes, "close", None)
                try:
                    if cerrar is not None:
                        cerrar()  # suelta la captura de dxcam
                finally:
                    cerrado.set()
                logging.info("Vídeo al celular terminado (%s cuadros).", enviados)

        return escribir

    def _leer_audio(self, detener: threading.Event, cola: queue.Queue) -> None:
        try:
            for bloque in self.audio(detener):
                try:
                    cola.put_nowait(bloque)
                except queue.Full:
                    pass  # el video va atrasado: se salta un trozo de audio
        except Exception as error:
            logging.warning("No se pudo capturar el audio para el celular: %s", error)
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
        if metodo == "GET" and ruta == "/api/screens":
            permitido = self.captura is not None and bool(self.pantalla_permitida())
            try:
                lista = self.pantallas() if permitido and self.pantallas else []
            except OSError as error:
                raise _Error(503, f"No pude leer los monitores: {error}") from error
            return self._json({"allowed": permitido, "screens": lista})
        if metodo == "GET" and ruta == "/api/screen":
            return self._pantalla(consulta)
        if ruta == "/api/control" and metodo in ("GET", "POST"):
            if metodo == "POST":
                quiere = bool(self._cuerpo_json(cuerpo).get("on"))
                if not quiere:
                    self.activar_control(False)
                elif not self.control_permitido():
                    raise _Error(403, "El control desde el celular está desactivado en los Ajustes de Jarvis.")
                elif not self.control_activo():
                    if self.ocupado() and self._pendiente is not None:
                        raise _Error(409, "Jarvis está esperando otra confirmación.")
                    self.eventos_app.put(("mobile_control_request",))
                    return self._json({"allowed": True, "active": False, "pending": True})
            return self._json({"allowed": bool(self.control_permitido()), "active": self.control_activo()})
        if metodo == "POST" and ruta == "/api/input":
            if not self.control_activo() or self.entrada is None:
                raise _Error(403, "El control de la PC no está activado.")
            try:
                enviadas = self.entrada(self._cuerpo_json(cuerpo).get("events"))
            except ValueError as error:
                raise _Error(400, str(error)) from error
            except OSError as error:
                raise _Error(409, str(error)) from error
            self._ultimo_control = time.monotonic()
            return self._json({"ok": True, "inputs": enviadas})
        if metodo == "POST" and ruta == "/api/vision":
            if not self.pantalla_permitida():
                raise _Error(403, "Ver la pantalla está desactivado en los Ajustes de Jarvis.")
            datos = self._cuerpo_json(cuerpo)
            try:
                monitor = int(datos.get("n", 1))
                zona = tuple(float(datos[clave]) for clave in ("x", "y", "w", "h"))
            except (KeyError, TypeError, ValueError) as error:
                raise _Error(400, "Zona no válida.") from error
            x, y, w, h = zona
            if monitor < 0 or not (0 <= x < 1 and 0 <= y < 1 and 0.005 <= w <= 1 and 0.005 <= h <= 1):
                raise _Error(400, "Zona no válida.")
            pregunta = " ".join(str(datos.get("question", "")).split())[:MAX_TEXTO] or "¿Qué es esto?"
            if self.ocupado():
                raise _Error(409, "Jarvis está ocupado; espera a que termine.")
            logging.info("«¿Qué es esto?» desde el celular sobre el monitor %s.", monitor)
            self.eventos_app.put(("mobile_vision", monitor, (x, y, min(w, 1 - x), min(h, 1 - y)), pregunta))
            return self._json({"ok": True})
        if metodo == "GET" and ruta == "/api/wol":
            if self.encendido is None:
                raise _Error(501, "El encendido remoto no está disponible.")
            return self._json(self.encendido())
        if metodo == "POST" and ruta == "/api/share":
            texto = str(self._cuerpo_json(cuerpo).get("text", "")).strip()[:MAX_TEXTO]
            if not texto:
                raise _Error(400, "No llegó nada para compartir.")
            enlace = re.search(r"https?://\S+", texto)
            if enlace and self.abrir_link is not None:
                try:
                    resultado = self.abrir_link(enlace.group(0).rstrip(").,;»\"'"))
                except Exception as error:
                    raise _Error(409, str(error) or "No pude abrir el link.") from error
                self.eventos_app.put(("mobile_shared", "link", enlace.group(0)))
                return self._json({"ok": True, "text": resultado})
            if self.portapapeles is None:
                raise _Error(501, "El portapapeles de la PC no está disponible.")
            self.portapapeles[1](texto)
            self.eventos_app.put(("mobile_shared", "text", ""))
            return self._json({"ok": True, "text": "Lo copié en el portapapeles de la PC."})
        if ruta == "/api/clipboard" and metodo in ("GET", "POST"):
            if self.portapapeles is None:
                raise _Error(501, "El portapapeles de la PC no está disponible.")
            try:
                if metodo == "GET":
                    return self._json({"text": self.portapapeles[0]()})
                texto = str(self._cuerpo_json(cuerpo).get("text", ""))
                if not texto:
                    raise _Error(400, "No hay texto para copiar.")
                self.portapapeles[1](texto)
            except OSError as error:
                raise _Error(409, str(error)) from error
            logging.info("Portapapeles %s desde el celular.", "leído" if metodo == "GET" else "escrito")
            return self._json({"ok": True})
        if metodo == "GET" and ruta == "/api/files/roots":
            return self._json({"roots": self.raices() if self.carpetas else []})
        if metodo == "GET" and ruta == "/api/files":
            import jarvis_archivos

            raiz, base = self._carpeta(consulta)
            relativa = consulta.get("path", [""])[0]
            try:
                elementos = jarvis_archivos.listar(base, relativa)
            except jarvis_archivos.ErrorArchivo as error:
                raise _Error(404, str(error)) from error
            return self._json({"root": raiz, "path": relativa.strip("/\\"), "items": elementos})
        if metodo == "GET" and ruta == "/api/files/download":
            import jarvis_archivos

            _raiz, base = self._carpeta(consulta)
            try:
                archivo = jarvis_archivos.resolver(base, consulta.get("path", [""])[0])
            except jarvis_archivos.ErrorArchivo as error:
                raise _Error(404, str(error)) from error
            if not archivo.is_file():
                raise _Error(404, "Ese archivo no existe.")
            tamano = archivo.stat().st_size
            logging.info("El celular descarga un archivo (%s).", jarvis_archivos.tamano_legible(tamano))

            def enviar(salida) -> None:
                try:
                    with open(archivo, "rb") as origen:
                        while trozo := origen.read(jarvis_archivos.BLOQUE):
                            salida.write(trozo)
                except OSError:
                    pass  # el celular canceló

            nombre = urllib.parse.quote(archivo.name)
            return 200, "application/octet-stream", enviar, {
                "Content-Length": str(tamano),
                "Content-Disposition": f"attachment; filename*=UTF-8''{nombre}",
            }
        if metodo == "GET" and ruta == "/api/media":
            if self.musica is None:
                raise _Error(501, "El control multimedia no está disponible.")
            return self._json(self.musica())
        if metodo == "GET" and ruta == "/api/media/cover":
            imagen = self.portada() if self.portada else None
            if not imagen:
                raise _Error(404, "No hay carátula.")
            tipo = "image/png" if imagen.startswith(b"\x89PNG") else "image/jpeg"
            return 200, tipo, imagen
        if metodo == "POST" and ruta == "/api/media":
            datos = self._cuerpo_json(cuerpo)
            herramienta = str(datos.get("tool", ""))
            argumentos = datos.get("args") or {}
            if herramienta not in HERRAMIENTAS_MOVIL or not isinstance(argumentos, dict):
                raise _Error(400, "Esa acción no se puede pedir desde aquí.")
            if self.accion is None:
                raise _Error(501, "El control multimedia no está disponible.")
            try:
                texto = self.accion(herramienta, argumentos)
            except Exception as error:  # la herramienta explica qué falló
                raise _Error(409, str(error) or "No se pudo completar la acción.") from error
            return self._json({"ok": True, "text": texto})
        if ruta == "/api/tv" and metodo in ("GET", "POST"):
            if self.tele is None or self.tele_accion is None:
                raise _Error(501, "El control de la tele no está disponible.")
            if metodo == "GET":
                return self._json(self.tele())
            accion = str(self._cuerpo_json(cuerpo).get("action", ""))
            if not re.fullmatch(r"[a-z0-9_]{2,20}|app:[A-Za-z0-9_.]{2,80}", accion):
                raise _Error(400, "Acción de la tele no válida.")
            try:
                texto = self.tele_accion(accion)
            except Exception as error:  # la herramienta explica qué falló
                raise _Error(409, str(error) or "No se pudo controlar la tele.") from error
            return self._json({"ok": True, "text": texto, "tv": self.tele()})
        if metodo == "GET" and ruta == "/api/status":
            if self.estado_equipo is None:
                raise _Error(501, "El estado del equipo no está disponible.")
            return self._json(self.estado_equipo(consulta.get("fps", ["0"])[0] == "1"))
        if metodo == "POST" and ruta == "/api/tts":
            texto = " ".join(str(self._cuerpo_json(cuerpo).get("text", "")).split())
            if not texto:
                raise _Error(400, "No hay texto para leer.")
            if self.tts is None:
                raise _Error(501, "La voz de la PC no está disponible.")
            try:
                audio = self.tts(texto[:MAX_TEXTO])
            except Exception as error:
                logging.warning("No se pudo sintetizar la voz para el celular: %s", error)
                raise _Error(503, "La voz de la PC no respondió.") from error
            return 200, "audio/wav", audio
        if metodo == "GET" and ruta == "/api/app":
            return self._json(self._app()[0])
        if metodo == "GET" and ruta == "/api/app/apk":
            info, apk = self._app()
            logging.info("El celular descarga la app %s.", info["versionName"] or info["versionCode"])
            return 200, "application/vnd.android.package-archive", apk.read_bytes()
        if metodo == "GET" and ruta == "/api/video":
            return 200, "application/octet-stream", self._video(consulta)
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


def _encadenar(primero, resto):
    yield primero
    yield from resto


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
        if metodo == "POST" and urlparse(self.path).path.rstrip("/") == "/api/upload":
            # Los archivos no pasan por ``manejar``: se escriben a disco por partes.
            estado, tipo, cuerpo = movil.subir(self.path, self.headers, self.client_address[0], self.rfile, largo)
            if estado != 200:
                self.close_connection = True  # el cuerpo quizá no se leyó
            extra = {}
        elif largo < 0 or largo > MAX_AUDIO:
            estado, tipo, cuerpo = 413, "application/json", b'{"error": "Demasiado grande."}'
            extra = {}
            self.close_connection = True
        else:
            datos = self.rfile.read(largo) if largo else b""
            respuesta = movil.manejar(metodo, self.path, self.headers, datos, self.client_address[0])
            estado, tipo, cuerpo = respuesta[:3]
            extra = respuesta[3] if len(respuesta) > 3 else {}
        if callable(cuerpo):  # flujo continuo (vídeo, descargas): hasta cerrar
            self.close_connection = True
            self.send_response(estado)
            for nombre, valor in extra.items():
                self.send_header(nombre, valor)
            self.send_header("Content-Type", tipo)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            cuerpo(self.wfile)
            return
        self.send_response(estado)
        for nombre, valor in extra.items():
            self.send_header(nombre, valor)
        self.send_header("Content-Type", tipo + ("; charset=utf-8" if tipo == "application/json" else ""))
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self) -> None:  # noqa: N802 (nombre de http.server)
        self._atender("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._atender("POST")
