import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
import re
import shutil
import socket
import requests
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import wave
from pathlib import Path
from collections import deque
from collections.abc import Callable, Generator
from prometheus_client import start_http_server, Counter, Histogram
from audio_capture import grabar_audio, limpiar_audio

# 🔧 CONFIGURACIÓN
# Usar 127.0.0.1 y no "localhost": Ollama solo escucha por IPv4 y Windows intenta
# antes IPv6, lo que añadía ~2 s a cada petición (medido el 2026-11-05).
STT_URL = "http://127.0.0.1:9000/v1/audio/transcriptions"
STT_MODEL = "Systran/faster-whisper-small"
# Vocabulario de pista para Whisper: acierta más las palabras de las órdenes.
# Medido el 2026-11-07 con frases sintetizadas: «Desmutea» (antes «deshutea») y
# «Spotify al 30 %» (antes «spoticia el 30 %»), sin más demora; con silencio no
# devuelve texto. Que sea corto: una pista larga se cuela a veces en la transcripción.
STT_PROMPT = (
    "Órdenes para Jarvis: pausa, reanuda, silencia, mutea, desmutea la pestaña de YouTube "
    "en Brave, monitor 1, monitor 2, haz clic, volumen, Spotify. Minimiza, maximiza, restaura, "
    "recarga la página."
)
LLM_URL = "http://127.0.0.1:11434/api/generate"
TTS_HOST = "127.0.0.1"
TTS_PORT = 5555
APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
DB_PATH = APP_DIR / "jarvis_memory.db"
RESPONSES_DIR = APP_DIR / "responses"
VOZ_PIPER = "es_ES-davefx-medium"  # se cambia en Ajustes (jarvis_voices); este es el valor por defecto
VOZ_HABLANTE: str | None = None  # para voces con varias personas (sharvard: «M» / «F»)


def _voz_wyoming(voz: str | None = None, hablante: str | None = None) -> dict:
    """Wyoming Piper carga la voz pedida desde /data (piper-voices) en cada petición."""
    datos = {"name": voz or VOZ_PIPER}
    elegido = hablante if voz else (hablante or VOZ_HABLANTE)
    if elegido:
        datos["speaker"] = elegido
    return datos
MODEL_LLM = "qwen2.5-coder:14b"  # Modelo instalado en Ollama
# Modelo con visión para analizar capturas y ubicar dónde hacer clic. Qwen3-VL 8B
# (~10 GB) cabe entero en la GPU de 16 GB. Comparado el 2026-11-07 en los monitores
# del usuario contra qwen3.6:27b (18 GB, no cabía): ubicó 7/9 textos frente a 6/9,
# ~0,7 s por búsqueda frente a ~11 s, y describió en ~4 s frente a ~23 s (y sin
# inventar). Sigue desplazando a MODEL_LLM mientras está cargado (no caben juntos).
MODEL_VISION = "qwen3-vl:8b-instruct-q8_0"
MAX_CONTEXT_MESSAGES = 12
STT_TIMEOUT = 90  # segundos de espera de la transcripción
TTS_TIMEOUT = 60  # segundos de espera de la síntesis de voz

# 📊 MÉTRICAS (Prometheus)
REQUEST_COUNTER = Counter('jarvis_requests_total', 'Total de interacciones', ['tipo'])
LATENCY_HIST = Histogram('jarvis_latency_seconds', 'Latencia end-to-end', ['estado'])
ERROR_COUNTER = Counter('jarvis_errors_total', 'Fallos detectados', ['modulo'])
STAGE_HIST = Histogram(
    'jarvis_stage_seconds', 'Duración de cada etapa (stt, llm, tts, total)', ['etapa', 'estado']
)
MAX_LATENCIAS_RECIENTES = 50
_latencias_recientes: dict[str, deque[tuple[float, bool]]] = {}
_latencias_lock = threading.Lock()


def registrar_latencia(etapa: str, segundos: float, ok: bool = True) -> None:
    """Guarda la duración de una etapa en Prometheus y en memoria (para el diagnóstico)."""
    STAGE_HIST.labels(etapa=etapa, estado="ok" if ok else "error").observe(segundos)
    with _latencias_lock:
        _latencias_recientes.setdefault(
            etapa, deque(maxlen=MAX_LATENCIAS_RECIENTES)
        ).append((segundos, ok))


def resumen_latencias() -> dict[str, tuple[int, float, float, int]]:
    """Por etapa: (cantidad, media, máximo, fallos) de las últimas mediciones."""
    with _latencias_lock:
        copia = {etapa: list(valores) for etapa, valores in _latencias_recientes.items()}
    resumen = {}
    for etapa, valores in copia.items():
        if not valores:
            continue
        duraciones = [segundos for segundos, _ in valores]
        resumen[etapa] = (
            len(valores),
            sum(duraciones) / len(duraciones),
            max(duraciones),
            sum(1 for _, ok in valores if not ok),
        )
    return resumen
_metrics_server_started = False
_database_initialized = False
_database_init_lock = threading.Lock()

logging.basicConfig(level=logging.INFO, format='🕒 %(asctime)s | %(levelname)s | %(message)s')


def iniciar_servicios_docker(espera: int = 120) -> None:
    compose_file = APP_DIR / "docker-compose.yml"
    if not compose_file.is_file():
        raise FileNotFoundError(f"No encuentro la configuración de Docker: {compose_file}")

    docker = shutil.which("docker")
    if docker is None:
        raise FileNotFoundError(
            "No encuentro Docker en PATH. Inicia Docker Desktop y verifica su instalación."
        )

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    deadline = time.monotonic() + espera
    last_error = "Docker Desktop todavía no está listo."
    while True:
        try:
            result = subprocess.run(
                [docker, "info"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=creation_flags,
                check=False,
            )
        except subprocess.TimeoutExpired:
            result = None

        if result is not None and result.returncode == 0:
            break
        if result is not None:
            last_error = (result.stderr or result.stdout).strip() or last_error
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Docker Desktop no estuvo listo después de {espera} segundos: {last_error}"
            )
        time.sleep(3)

    compose_command = [docker, "compose", "-f", str(compose_file)]

    def run_compose(arguments: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [*compose_command, *arguments],
                cwd=APP_DIR,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=creation_flags,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"Docker Compose excedió el tiempo de espera al ejecutar "
                f"{' '.join(arguments)}."
            ) from error

    status = run_compose(["ps", "--format", "json", "stt", "tts"], timeout=30)
    if status.returncode != 0:
        details = (status.stderr or status.stdout).strip()
        raise RuntimeError(f"No pude consultar los servicios Docker: {details}")

    paused_services: set[str] = set()
    for line in status.stdout.splitlines():
        if not line.strip():
            continue
        try:
            container = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError("Docker devolvió un estado de contenedores inválido.") from error
        if str(container.get("State", "")).lower() == "paused":
            service = container.get("Service")
            if service in {"stt", "tts"}:
                paused_services.add(service)

    for service in sorted(paused_services):
        result = run_compose(["unpause", service], timeout=30)
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"No pude reactivar el contenedor {service}: {details}")

    result = run_compose(["up", "-d", "stt", "tts"], timeout=600)
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"No pude iniciar STT y TTS con Docker Compose: {details}")

    logging.info("Servicios Docker de STT y TTS iniciados.")


def detener_servicios_docker() -> None:
    compose_file = APP_DIR / "docker-compose.yml"
    if not compose_file.is_file():
        raise FileNotFoundError(f"No encuentro la configuración de Docker: {compose_file}")

    docker = shutil.which("docker")
    if docker is None:
        raise FileNotFoundError(
            "No encuentro Docker en PATH; no pude detener los servicios de voz."
        )

    try:
        result = subprocess.run(
            [docker, "compose", "-f", str(compose_file), "stop", "stt", "tts"],
            cwd=APP_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Docker Compose tardó demasiado al detener STT y TTS.") from error

    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"No pude detener STT y TTS: {details}")

    logging.info("Servicios Docker de STT y TTS detenidos.")


def inicializar_base_datos() -> None:
    global _database_initialized
    with _database_init_lock:
        if _database_initialized:
            return

        # En el .exe se usa siempre el esquema empaquetado: es el que coincide
        # con el código. Un init_db.sql suelto junto al .exe puede ser antiguo.
        schema_path = APP_DIR / "init_db.sql"
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if isinstance(bundle_dir, str) and (Path(bundle_dir) / "init_db.sql").is_file():
            schema_path = Path(bundle_dir) / "init_db.sql"
        connection = sqlite3.connect(DB_PATH, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            routine_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(rutinas)").fetchall()
            }
            if "dias_semana" not in routine_columns:
                connection.execute(
                    "ALTER TABLE rutinas ADD COLUMN dias_semana TEXT NOT NULL "
                    "DEFAULT '1,2,3,4,5,6,7'"
                )
            if "ultima_ejecucion" not in routine_columns:
                connection.execute(
                    "ALTER TABLE rutinas ADD COLUMN ultima_ejecucion TEXT"
                )
            if "disparador" not in routine_columns:
                connection.execute(
                    "ALTER TABLE rutinas ADD COLUMN disparador TEXT NOT NULL DEFAULT 'hora'"
                )
            memory_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(memorias)").fetchall()
            }
            if "origen" not in memory_columns:
                connection.execute(
                    "ALTER TABLE memorias ADD COLUMN origen TEXT NOT NULL DEFAULT 'usuario'"
                )
            if "frases" not in routine_columns:
                connection.execute(
                    "ALTER TABLE rutinas ADD COLUMN frases TEXT NOT NULL DEFAULT '[]'"
                )
            if "una_vez" not in routine_columns:
                connection.execute(
                    "ALTER TABLE rutinas ADD COLUMN una_vez INTEGER NOT NULL DEFAULT 0"
                )
            if "modo_permiso" not in routine_columns:
                connection.execute(
                    "ALTER TABLE rutinas ADD COLUMN modo_permiso TEXT NOT NULL "
                    "DEFAULT 'preguntar'"
                )
            connection.execute(
                "UPDATE rutinas SET activa = 0, accion = '[]' "
                "WHERE nombre = 'buenos_dias' AND accion = 'notificar_clima'"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_rutinas_programacion "
                "ON rutinas(activa, hora)"
            )
            conversation = connection.execute(
                "SELECT id FROM conversaciones ORDER BY actualizado DESC, id DESC LIMIT 1"
            ).fetchone()
            if conversation is None:
                connection.execute(
                    "INSERT INTO conversaciones (id, creado, actualizado) "
                    "VALUES (?, strftime('%Y-%m-%d %H:%M:%f', 'now'), "
                    "strftime('%Y-%m-%d %H:%M:%f', 'now'))",
                    (str(uuid.uuid4()),),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        _database_initialized = True


@contextmanager
def conexion_db() -> Generator[sqlite3.Connection, None, None]:
    inicializar_base_datos()
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def iniciar_metricas() -> None:
    global _metrics_server_started
    if not _metrics_server_started:
        start_http_server(8000)
        _metrics_server_started = True


def obtener_preferencia(clave: str) -> str | None:
    with conexion_db() as connection:
        row = connection.execute(
            "SELECT valor FROM preferencias WHERE clave = ?", (clave,)
        ).fetchone()
    return str(row[0]) if row is not None else None


def guardar_preferencia(clave: str, valor: str) -> None:
    with conexion_db() as connection:
        connection.execute(
            "INSERT INTO preferencias (clave, valor) VALUES (?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, "
            "actualizado = datetime('now')",
            (clave, valor),
        )


def cargar_contexto_db() -> str:
    """Consulta preferencias y rutinas para enriquecer el prompt"""
    with conexion_db() as connection:
        prefs = dict(
            connection.execute(
                "SELECT clave, valor FROM preferencias "
                "WHERE clave IN ('idioma', 'tono', 'unidad_temperatura')"
            ).fetchall()
        )
    prefs.setdefault("idioma", "español")
    prefs.setdefault("tono", "directo y útil")
    prefs.setdefault("unidad_temperatura", "°C")
    return (
        f"Sistema: Responde en {prefs['idioma']}. Tono: {prefs['tono']}. "
        f"Usa {prefs['unidad_temperatura']} si aplica."
    )


def crear_conversacion() -> str:
    conversacion_id = str(uuid.uuid4())
    with conexion_db() as connection:
        connection.execute(
            "INSERT INTO conversaciones (id, creado, actualizado) "
            "VALUES (?, strftime('%Y-%m-%d %H:%M:%f', 'now'), "
            "strftime('%Y-%m-%d %H:%M:%f', 'now'))",
            (conversacion_id,),
        )
    return conversacion_id


def obtener_conversacion_actual() -> str:
    with conexion_db() as connection:
        conversation = connection.execute(
            "SELECT id FROM conversaciones ORDER BY actualizado DESC, id DESC LIMIT 1"
        ).fetchone()
    if conversation is None:
        return crear_conversacion()
    return str(conversation[0])


def obtener_historial(
    conversacion_id: str,
    limite: int = 50,
) -> list[tuple[str, str]]:
    with conexion_db() as connection:
        rows = connection.execute(
            "SELECT rol, contenido FROM mensajes "
            "WHERE conversacion_id = ? ORDER BY id DESC LIMIT ?",
            (conversacion_id, limite),
        ).fetchall()
    return [(str(role), str(content)) for role, content in reversed(rows)]


def obtener_historial_con_fecha(
    conversacion_id: str,
    limite: int = 50,
) -> list[tuple[str, str, datetime | None]]:
    """Como ``obtener_historial`` pero con la hora local de cada mensaje (para el chat)."""
    with conexion_db() as connection:
        rows = connection.execute(
            "SELECT rol, contenido, creado FROM mensajes "
            "WHERE conversacion_id = ? ORDER BY id DESC LIMIT ?",
            (conversacion_id, limite),
        ).fetchall()
    resultado = []
    for role, content, creado in reversed(rows):
        try:
            # SQLite guarda la hora en UTC («2026-11-12 17:22:40.123»).
            momento = datetime.fromisoformat(str(creado)).replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        except (TypeError, ValueError):
            momento = None
        resultado.append((str(role), str(content), momento))
    return resultado


def guardar_turno(conversacion_id: str, usuario: str, asistente: str) -> None:
    with conexion_db() as connection:
        conversation = connection.execute(
            "SELECT titulo FROM conversaciones WHERE id = ?",
            (conversacion_id,),
        ).fetchone()
        if conversation is None:
            raise ValueError(f"No existe la conversación {conversacion_id}.")

        connection.executemany(
            "INSERT INTO mensajes (conversacion_id, rol, contenido) VALUES (?, ?, ?)",
            (
                (conversacion_id, "user", usuario),
                (conversacion_id, "assistant", asistente),
            ),
        )
        titulo = str(conversation[0])
        if titulo == "Nueva conversación":
            titulo = usuario.strip().replace("\n", " ")[:60] or titulo
        connection.execute(
            "UPDATE conversaciones SET titulo = ?, actualizado = "
            "strftime('%Y-%m-%d %H:%M:%f', 'now') WHERE id = ?",
            (titulo, conversacion_id),
        )

def _wav_silencio(segundos: float = 1.0, frecuencia: int = 16000) -> bytes:
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(frecuencia)
        wav.writeframes(b"\x00\x00" * int(segundos * frecuencia))
    return buffer.getvalue()


def precalentar_stt(intentos: int = 30, espera: float = 2.0) -> bool:
    """Carga el modelo de Whisper con un segundo de silencio.

    La primera transcripción tras arrancar el contenedor tarda varios segundos
    más porque carga el modelo; así ese coste no lo paga la primera frase del
    usuario. Reintenta mientras el contenedor termina de arrancar.
    """
    audio = _wav_silencio()
    for _ in range(max(1, intentos)):
        inicio = time.perf_counter()
        try:
            res = requests.post(
                STT_URL,
                data={"model": STT_MODEL, "language": "es"},
                files={"file": ("silencio.wav", audio, "audio/wav")},
                timeout=(5, STT_TIMEOUT),
            )
            res.raise_for_status()
        except requests.RequestException:
            time.sleep(espera)
            continue
        logging.info("Modelo de voz a texto cargado en %.2fs.", time.perf_counter() - inicio)
        return True
    logging.warning("No pude precargar el modelo de voz a texto.")
    return False


_vocabulario_stt: "Callable[[], list[str]] | None" = None
# Una pista larga se cuela a veces en la transcripción: pocos nombres y tope de largo.
MAX_VOCABULARIO_STT = 12
MAX_PISTA_STT = 300


def configurar_vocabulario_stt(funcion: "Callable[[], list[str]] | None") -> None:
    """Nombres que Whisper debe conocer (programas abiertos, rutinas…): los pone la app."""
    global _vocabulario_stt
    _vocabulario_stt = funcion


def prompt_stt() -> str:
    """Pista para Whisper: la fija más los nombres del momento.

    Whisper escribe lo que le suena en español cuando no conoce una palabra
    («Claude» salía como «Cloudy» o «clau de»); ver los nombres en la pista basta
    para que los escriba bien.
    """
    palabras: list[str] = []
    if _vocabulario_stt is not None:
        try:
            palabras = [p for p in dict.fromkeys(_vocabulario_stt()) if p]
        except Exception:
            logging.debug("No se pudo armar el vocabulario de la transcripción.", exc_info=True)
    if not palabras:
        return STT_PROMPT
    pista = STT_PROMPT + " Nombres:"
    for palabra in palabras[:MAX_VOCABULARIO_STT]:
        if len(pista) + len(palabra) + 3 > MAX_PISTA_STT:
            break
        pista += (" " if pista.endswith(":") else ", ") + palabra
    return pista + "."


def procesar_stt(ruta_audio: str) -> str:
    inicio = time.perf_counter()
    try:
        with open(ruta_audio, 'rb') as f:
            res = requests.post(
                STT_URL,
                data={"model": STT_MODEL, "language": "es", "prompt": prompt_stt()},
                files={"file": (Path(ruta_audio).name, f, "audio/wav")},
                timeout=(10, STT_TIMEOUT),
            )
        res.raise_for_status()
        texto = res.json().get("text", "")
        logging.info("⏱️ STT: %.2fs", time.perf_counter() - inicio)
        registrar_latencia("stt", time.perf_counter() - inicio)
        return texto
    except (OSError, requests.RequestException, ValueError) as e:
        ERROR_COUNTER.labels(modulo="STT").inc()
        logging.error("STT falló tras %.2fs: %s", time.perf_counter() - inicio, e)
        registrar_latencia("stt", time.perf_counter() - inicio, ok=False)
        return ""

def procesar_llm(
    texto: str,
    historial: list[tuple[str, str]] | None = None,
) -> str | None:
    contexto = cargar_contexto_db()
    lineas_historial = []
    for role, contenido in historial or []:
        etiqueta = "Usuario" if role == "user" else "Asistente"
        lineas_historial.append(f"{etiqueta}: {contenido}")
    contexto_conversacional = "\n".join(lineas_historial)
    if contexto_conversacional:
        contexto_conversacional = (
            f"\nHistorial reciente de esta conversación:\n{contexto_conversacional}\n"
        )
    payload = {
        "model": MODEL_LLM,
        "prompt": (
            f"{contexto}\nMantén el hilo usando el historial proporcionado. "
            "Responde de forma concisa en una o dos frases, salvo que el usuario "
            f"pida detalles.{contexto_conversacional}\nUsuario: {texto}\nAsistente:"
        ),
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "num_ctx": 2048,
            "num_predict": 96,
            "temperature": 0.2,
        },
    }
    inicio = time.perf_counter()
    try:
        res = requests.post(LLM_URL, json=payload, timeout=120)
        res.raise_for_status()
        respuesta = res.json().get("response", "No pude procesar eso.")
        logging.info("⏱️ LLM: %.2fs", time.perf_counter() - inicio)
        return respuesta
    except (requests.RequestException, ValueError, KeyError) as e:
        ERROR_COUNTER.labels(modulo="LLM").inc()
        logging.error("LLM falló tras %.2fs: %s", time.perf_counter() - inicio, e)
        return None

MAX_AUDIOS_RESPUESTA = 30
_PATRON_ARCHIVO_RESPUESTA = re.compile(r"^respuesta_[0-9_]+\.(?:wav|txt)$")


def guardar_respuesta(respuesta: str) -> str:
    """Devuelve el identificador del audio de la respuesta.

    Ya no se escribe un .txt con el texto: está en el historial de la base de
    datos y duplicarlo en disco guardaba información privada innecesaria.
    """
    del respuesta
    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    return datetime.now().strftime("respuesta_%Y%m%d_%H%M%S_%f")


def _archivos_respuesta() -> list[Path]:
    if not RESPONSES_DIR.is_dir():
        return []
    return [
        ruta
        for ruta in RESPONSES_DIR.iterdir()
        if ruta.is_file() and _PATRON_ARCHIVO_RESPUESTA.match(ruta.name)
    ]


def limpiar_respuestas(max_audios: int = MAX_AUDIOS_RESPUESTA) -> int:
    """Borra los .txt antiguos y conserva solo los ``max_audios`` audios más recientes.

    Solo toca archivos ``respuesta_*.wav``/``.txt`` de RESPONSES_DIR.
    """
    archivos = _archivos_respuesta()
    audios = sorted(
        (ruta for ruta in archivos if ruta.suffix == ".wav"),
        key=lambda ruta: ruta.stat().st_mtime,
        reverse=True,
    )
    sobrantes = [ruta for ruta in archivos if ruta.suffix == ".txt"] + audios[max_audios:]
    borrados = 0
    for ruta in sobrantes:
        try:
            ruta.unlink()
            borrados += 1
        except OSError:
            logging.warning("No se pudo borrar la respuesta antigua %s.", ruta.name)
    if borrados:
        logging.info("Se borraron %s archivos de respuestas antiguas.", borrados)
    return borrados


def borrar_respuestas() -> int:
    return limpiar_respuestas(max_audios=0)


def borrar_historial_conversaciones() -> str:
    """Elimina todas las conversaciones y mensajes (no toca recuerdos, rutinas ni acciones).

    Devuelve el identificador de una conversación nueva y vacía.
    """
    with conexion_db() as connection:
        connection.execute("DELETE FROM mensajes")
        connection.execute("DELETE FROM conversaciones")
    conversacion_id = crear_conversacion()
    borrar_respuestas()
    logging.info("Se borró el historial de conversaciones a pedido del usuario.")
    return conversacion_id


def generar_audio_tts(
    respuesta: str,
    identificador: str | None = None,
    *,
    voz: str | None = None,
    hablante: str | None = None,
) -> str:
    """Sintetiza con Piper. Sin ``voz`` usa la elegida (``VOZ_PIPER``/``VOZ_HABLANTE``)."""
    if identificador is None:
        identificador = guardar_respuesta(respuesta)
    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    ruta_salida = RESPONSES_DIR / f"{identificador}.wav"

    def leer_bytes(reader, cantidad: int) -> bytes:
        partes = []
        restante = cantidad
        while restante:
            parte = reader.read(restante)
            if not parte:
                raise ConnectionError("El servidor TTS cortó la respuesta de audio.")
            partes.append(parte)
            restante -= len(parte)
        return b"".join(partes)

    inicio = time.perf_counter()
    try:
        with socket.create_connection((TTS_HOST, TTS_PORT), timeout=TTS_TIMEOUT) as client:
            client.settimeout(TTS_TIMEOUT)
            reader = client.makefile("rb")
            data = json.dumps(
                {"text": respuesta, "voice": _voz_wyoming(voz, hablante)},
                ensure_ascii=False,
            ).encode("utf-8")
            header = json.dumps(
                {"type": "synthesize", "data_length": len(data)}
            ).encode("utf-8")
            client.sendall(header + b"\n" + data)

            audio_start = None
            audio_chunks = []
            while True:
                event_header = reader.readline()
                if not event_header:
                    raise ConnectionError("El servidor TTS cerró la conexión sin completar el audio.")

                event = json.loads(event_header)
                event_data = event.get("data", {})
                data_length = event.get("data_length", 0)
                if data_length:
                    event_data.update(json.loads(leer_bytes(reader, data_length)))

                payload_length = event.get("payload_length", 0)
                payload = leer_bytes(reader, payload_length) if payload_length else b""

                if event["type"] == "error":
                    raise RuntimeError(event_data.get("text", "Error del servidor Piper."))
                if event["type"] == "audio-start":
                    audio_start = event_data
                elif event["type"] == "audio-chunk":
                    audio_chunks.append(payload)
                elif event["type"] == "audio-stop":
                    break

            reader.close()

        if not audio_start or not audio_chunks:
            raise RuntimeError("Piper no devolvió audio.")

        with wave.open(str(ruta_salida), "wb") as wav_file:
            wav_file.setnchannels(audio_start["channels"])
            wav_file.setsampwidth(audio_start["width"])
            wav_file.setframerate(audio_start["rate"])
            wav_file.writeframes(b"".join(audio_chunks))

        logging.info("⏱️ TTS: %.2fs", time.perf_counter() - inicio)
        registrar_latencia("tts", time.perf_counter() - inicio)
        return str(ruta_salida)
    except (OSError, ValueError, KeyError, RuntimeError) as e:
        ERROR_COUNTER.labels(modulo="TTS").inc()
        logging.error("TTS falló tras %.2fs: %s", time.perf_counter() - inicio, e)
        registrar_latencia("tts", time.perf_counter() - inicio, ok=False)
        raise

def reproducir_audio(ruta: str):
    if not ruta: return
    # Cross-platform playback
    if os.name == "posix":
        os.system(f"aplay '{ruta}'")  # Linux
    elif os.name == "nt":
        os.startfile(os.path.abspath(ruta))  # Windows
    else:
        os.system(f"afplay '{ruta}'")  # macOS

def procesar_entrada(
    texto: str,
    tipo: str,
    inicio_ciclo: float | None = None,
    conversacion_id: str | None = None,
) -> None:
    t_inicio = inicio_ciclo if inicio_ciclo is not None else time.perf_counter()
    _, ruta_audio = procesar_consulta(texto, tipo, t_inicio, conversacion_id)
    if ruta_audio:
        reproducir_audio(ruta_audio)


def procesar_consulta(
    texto: str,
    tipo: str = "chat",
    inicio_ciclo: float | None = None,
    conversacion_id: str | None = None,
) -> tuple[str | None, str | None]:
    t_inicio = inicio_ciclo if inicio_ciclo is not None else time.perf_counter()
    conversacion_id = conversacion_id or obtener_conversacion_actual()
    historial = obtener_historial(conversacion_id, MAX_CONTEXT_MESSAGES)
    REQUEST_COUNTER.labels(tipo=tipo).inc()
    respuesta = procesar_llm(texto, historial)
    if respuesta is None:
        LATENCY_HIST.labels(estado="error").observe(time.perf_counter() - t_inicio)
        return None, None

    guardar_turno(conversacion_id, texto, respuesta)
    try:
        identificador = guardar_respuesta(respuesta)
        ruta_audio = generar_audio_tts(respuesta, identificador)
        LATENCY_HIST.labels(estado="success").observe(time.perf_counter() - t_inicio)
        logging.info(
            "⏱️ Latencia tras la entrada: %.2fs | ✅ Ciclo completado",
            time.perf_counter() - t_inicio,
        )
        return respuesta, ruta_audio
    except (OSError, RuntimeError, ValueError) as e:
        LATENCY_HIST.labels(estado="error").observe(time.perf_counter() - t_inicio)
        logging.error(f"No se pudo generar o reproducir la respuesta: {e}")
        return respuesta, None


def ciclo_interactivo(modo_texto: bool = False):
    iniciar_metricas()
    conversacion_id = obtener_conversacion_actual()
    if modo_texto:
        logging.info("🟢 Jarvis Local listo. Escribe 'salir' para terminar.")
    else:
        logging.info("🟢 Jarvis Local listo. ENTER inicia y detiene la grabación; escribe 'salir' para terminar.")

    while True:
        if modo_texto:
            entrada = input("\n🎤 Tu mensaje: ").strip()
            if entrada.lower() == "salir":
                break
            if entrada.lower() == "/nuevo":
                conversacion_id = crear_conversacion()
                logging.info("Se inició una conversación nueva.")
                continue
            if entrada:
                procesar_entrada(
                    entrada.removeprefix("@"),
                    "chat",
                    conversacion_id=conversacion_id,
                )
            continue

        comando = input("\n⏸️ Presiona ENTER para grabar (o escribe 'salir'): ").strip()
        if comando.lower() == "salir":
            break

        ruta_audio = grabar_audio()
        if not ruta_audio:
            logging.warning("No se obtuvo una grabación; inténtalo de nuevo.")
            continue

        try:
            inicio_ciclo = time.perf_counter()
            texto_voz = procesar_stt(ruta_audio)
            if not texto_voz.strip():
                logging.warning("No se detectó voz clara; inténtalo de nuevo.")
                continue
            logging.info("📝 Texto detectado: %s", texto_voz)
            if texto_voz.strip().lower() == "/nuevo":
                conversacion_id = crear_conversacion()
                logging.info("Se inició una conversación nueva.")
                continue
            procesar_entrada(texto_voz, "voz", inicio_ciclo, conversacion_id)
        finally:
            limpiar_audio(ruta_audio)

if __name__ == "__main__":
    iniciar_metricas()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tts-test",
        metavar="TEXTO",
        help="Genera y reproduce un WAV directamente con Piper, sin llamar al LLM.",
    )
    parser.add_argument(
        "--text-mode",
        action="store_true",
        help="Usa texto escrito en la terminal en lugar del micrófono.",
    )
    args = parser.parse_args()

    if args.tts_test:
        identificador = guardar_respuesta(args.tts_test)
        archivo = generar_audio_tts(args.tts_test, identificador)
        logging.info(f"Audio de prueba guardado en {archivo}")
        reproducir_audio(archivo)
    else:
        ciclo_interactivo(modo_texto=args.text_mode)
