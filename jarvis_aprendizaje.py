"""Aprendizaje automático: Jarvis extrae datos duraderos de tus conversaciones.

Cuando una conversación lleva ``INACTIVIDAD`` sin mensajes nuevos, el modelo
lee los mensajes que aún no revisó y devuelve, en JSON, los hechos duraderos
que **dijo el usuario** (preferencias, costumbres, personas, equipo…) y las
correcciones de recuerdos que ya existían. Se guardan como recuerdos con
``origen='automatica'`` y la app avisa en el chat («Aprendí: …»).

Salvaguardas:
- nunca guarda contraseñas, datos bancarios, documentos ni salud (instrucción
  al modelo + filtro ``_SENSIBLE``);
- no duplica: si ya hay un recuerdo casi igual (similitud ≥ ``UMBRAL_DUPLICADO``)
  lo ignora; el modelo ve los recuerdos parecidos y puede actualizarlos;
- solo actualiza recuerdos que se le mostraron;
- se apaga con «desactiva el aprendizaje automático» (preferencia).

Los registros solo anotan cuántos recuerdos se aprendieron, nunca su texto.
"""

import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

import jarvis_core as core
import jarvis_memory

INACTIVIDAD = timedelta(minutes=10)
UMBRAL_DUPLICADO = 0.85
MINIMO_TEXTO_USUARIO = 20
MAX_MENSAJES = 40
PREF_ACTIVO = "aprendizaje_automatico"
PREF_PROGRESO = "aprendizaje_progreso"

_SENSIBLE = re.compile(
    r"contrase|password|clave\s+de|pin\b|tarjeta|cbu|cvu|alias\s+banc|cuenta\s+banc|iban"
    r"|\bdni\b|pasaporte|cuil|cuit|seguro\s+social|diagnost|enfermedad|medicaci|tratamiento"
    r"|psicolog|psiquiatr|embaraz",
    re.IGNORECASE,
)

_SISTEMA = """Eres el módulo de memoria de JARVIS, el asistente personal del usuario.
Lee la conversación y extrae TODOS los hechos duraderos sobre el usuario que el
propio usuario haya dicho: preferencias, gustos, costumbres y horarios, su trabajo,
su equipo y dispositivos, nombres de personas o mascotas cercanas, planes a largo plazo.

Reglas:
- Nada que solo haya dicho JARVIS, ni suposiciones.
- Nada momentáneo (lo que hace ahora, el clima, una búsqueda puntual, órdenes como
  subir el volumen, poner un temporizador o abrir algo).
- Nunca contraseñas, datos bancarios, números de documento ni datos de salud.
- Un hecho por elemento, en una frase corta en español, en tercera persona sin
  sujeto. Ejemplo: «Prefiere Brave como navegador», «Cena a las 21», «Su hermana
  se llama Lucía».
- Si no hay nada que valga la pena recordar, devuelve una lista vacía.
"""

_ESQUEMA = {
    "type": "object",
    "properties": {"hechos": {"type": "array", "items": {"type": "string"}}},
    "required": ["hechos"],
}

_SISTEMA_REEMPLAZO = """Decide si un DATO NUEVO sobre el usuario corrige o reemplaza a
alguno de sus RECUERDOS (mismo tema con un valor distinto o más actual, p. ej.
«Cena a las 22:30» reemplaza a «Cena a las 21»). Si solo tratan temas parecidos
pero ambos pueden ser ciertos a la vez, no reemplaza ninguno.
Responde con el id del recuerdo reemplazado o null."""

_ESQUEMA_REEMPLAZO = {
    "type": "object",
    "properties": {"reemplaza_id": {"type": ["integer", "null"]}},
    "required": ["reemplaza_id"],
}
# Parecido mínimo para preguntar si un dato reemplaza a un recuerdo existente.
UMBRAL_MISMO_TEMA = 0.55


# --- Preferencias ------------------------------------------------------------------


def activo() -> bool:
    return core.obtener_preferencia(PREF_ACTIVO) != "0"


def _progreso() -> dict[str, int]:
    try:
        datos = json.loads(core.obtener_preferencia(PREF_PROGRESO) or "{}")
        return {str(k): int(v) for k, v in datos.items()}
    except (ValueError, TypeError, AttributeError):
        return {}


def _guardar_progreso(progreso: dict[str, int]) -> None:
    core.guardar_preferencia(PREF_PROGRESO, json.dumps(progreso))


# --- Conversaciones ------------------------------------------------------------------


def conversaciones_pendientes(ahora_utc: datetime | None = None) -> list[str]:
    """Conversaciones con mensajes sin revisar y sin actividad desde hace un rato."""
    ahora_utc = ahora_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    limite = (ahora_utc - INACTIVIDAD).strftime("%Y-%m-%d %H:%M:%f")
    progreso = _progreso()
    with core.conexion_db() as conexion:
        filas = conexion.execute(
            "SELECT conversacion_id, MAX(id), MAX(creado) FROM mensajes GROUP BY conversacion_id"
        ).fetchall()
    return [
        str(conv)
        for conv, ultimo_id, ultimo in filas
        if int(ultimo_id) > progreso.get(str(conv), 0) and str(ultimo) <= limite
    ]


def mensajes_nuevos(conversacion_id: str) -> list[tuple[int, str, str]]:
    desde = _progreso().get(conversacion_id, 0)
    with core.conexion_db() as conexion:
        filas = conexion.execute(
            "SELECT id, rol, contenido FROM mensajes WHERE conversacion_id = ? AND id > ? "
            "ORDER BY id DESC LIMIT ?",
            (conversacion_id, desde, MAX_MENSAJES),
        ).fetchall()
    return [(int(i), str(r), str(c)) for i, r, c in reversed(filas)]


# --- Extracción --------------------------------------------------------------------------


def _chat_json(sistema: str, usuario: str, esquema: dict, num_predict: int) -> dict:
    respuesta = requests.post(
        core.LLM_URL.rsplit("/", 1)[0] + "/chat",
        json={
            "model": core.MODEL_LLM,
            "stream": False,
            "format": esquema,
            "keep_alive": "2h",  # como jarvis_agent.KEEP_ALIVE (si no, acorta el de la charla)
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": num_predict},
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
        },
        timeout=120,
    )
    respuesta.raise_for_status()
    return json.loads(respuesta.json()["message"]["content"])


def extraer_hechos(conversacion: str) -> list[str]:
    datos = _chat_json(_SISTEMA, f"CONVERSACIÓN:\n{conversacion}", _ESQUEMA, 400)
    hechos = datos.get("hechos") if isinstance(datos, dict) else None
    return [h for h in hechos if isinstance(h, str)] if isinstance(hechos, list) else []


def decidir_reemplazo(hecho: str, candidatos: list[tuple[int, str]]) -> int | None:
    lista = "\n".join(f"[{i}] {c}" for i, c in candidatos)
    datos = _chat_json(
        _SISTEMA_REEMPLAZO,
        f"DATO NUEVO: {hecho}\n\nRECUERDOS:\n{lista}",
        _ESQUEMA_REEMPLAZO,
        30,
    )
    valor = datos.get("reemplaza_id") if isinstance(datos, dict) else None
    return valor if isinstance(valor, int) else None


def _limpio(texto: object) -> str | None:
    if not isinstance(texto, str):
        return None
    texto = " ".join(texto.split()).strip(" -•.")
    if not 3 <= len(texto) <= 200 or _SENSIBLE.search(texto):
        return None
    return texto[0].upper() + texto[1:]


def aprender_de(
    conversacion_id: str,
    *,
    extraer=extraer_hechos,
    decidir=decidir_reemplazo,
) -> list[str]:
    """Revisa los mensajes nuevos de una conversación. Devuelve los avisos."""
    mensajes = mensajes_nuevos(conversacion_id)
    if not mensajes:
        return []
    ultimo_id = mensajes[-1][0]
    texto_usuario = " ".join(c for _, rol, c in mensajes if rol == "user")
    avisos: list[str] = []
    if len(texto_usuario) >= MINIMO_TEXTO_USUARIO:
        conversacion = "\n".join(
            f"{'USUARIO' if rol == 'user' else 'JARVIS'}: {contenido[:600]}"
            for _, rol, contenido in mensajes
        )
        for hecho in extraer(conversacion):
            aviso = _guardar_hecho(hecho, decidir)
            if aviso:
                avisos.append(aviso)
    progreso = _progreso()
    progreso[conversacion_id] = ultimo_id
    _guardar_progreso(progreso)
    if avisos:
        logging.info("Aprendizaje automático: %s cambio(s) en la memoria.", len(avisos))
    return avisos


def _guardar_hecho(hecho: str, decidir) -> str | None:
    texto = _limpio(hecho)
    if texto is None:
        return None
    parecidos = jarvis_memory.buscar_parecidos(texto, limite=3, minimo=UMBRAL_MISMO_TEMA)
    if parecidos and parecidos[0][2] >= UMBRAL_DUPLICADO:
        return None  # ya lo sabía
    if parecidos:
        candidatos = {i: c for i, c, _ in parecidos}
        reemplazado = decidir(texto, list(candidatos.items()))
        if reemplazado in candidatos:  # solo recuerdos que se le mostraron
            if _normalizar(texto) == _normalizar(candidatos[reemplazado]):
                return None
            try:
                if jarvis_memory.actualizar_recuerdo(reemplazado, texto):
                    return f"Actualicé el recuerdo {reemplazado}: {texto}"
            except ValueError:
                return None
    identificador, insertado = jarvis_memory.guardar_recuerdo(texto, origen="automatica")
    return f"Aprendí ({identificador}): {texto}" if insertado else None


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in texto if c.isalnum())


_reintentar_desde = 0.0
ESPERA_TRAS_FALLO = 15 * 60


def procesar_pendientes() -> list[str]:
    """Revisa todas las conversaciones inactivas. Lo llama la app cada minuto.

    Si Ollama falla, espera ``ESPERA_TRAS_FALLO`` antes de reintentar, para no
    insistir cada minuto (un error del servidor puede reiniciar el modelo).
    """
    global _reintentar_desde
    if not activo() or time.monotonic() < _reintentar_desde:
        return []
    avisos: list[str] = []
    for conversacion_id in conversaciones_pendientes():
        try:
            avisos.extend(aprender_de(conversacion_id))
        except (requests.RequestException, ValueError, KeyError) as error:
            logging.warning(
                "No se pudo revisar una conversación para aprender (%s); reintento en %s min.",
                error,
                ESPERA_TRAS_FALLO // 60,
            )
            _reintentar_desde = time.monotonic() + ESPERA_TRAS_FALLO
            break
    return avisos


# --- Comandos ------------------------------------------------------------------------------

_ACTIVAR = re.compile(
    r"^(?:activa|activar|enciende|habilita|reactiva)\s+(?:el\s+)?aprendizaje(?:\s+automatico)?$"
)
_DESACTIVAR = re.compile(
    r"^(?:desactiva|desactivar|apaga|deshabilita|para|deten)\s+(?:el\s+)?aprendizaje(?:\s+automatico)?$"
    r"|^(?:deja|dejar)\s+de\s+aprender(?:\s+(?:de\s+mi|sobre\s+mi|cosas\s+de\s+mi))?$"
)
_LISTAR = re.compile(
    r"^(?:que|cosas)\s+(?:has\s+)?(?:aprendiste|aprendido)\s+(?:de|sobre)\s+mi$"
    r"|^que\s+sabes\s+de\s+mi\s+(?:que\s+aprendiste\s+)?solo$"
    r"|^(?:mostrar|lista|listar)\s+(?:lo\s+)?aprendido$"
)


def _normalizar_comando(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    return re.sub(r"^(?:jarvis|oye jarvis|por favor)\s+", "", texto)


def procesar_comando(texto: str) -> str | None:
    consulta = _normalizar_comando(texto)
    if _ACTIVAR.fullmatch(consulta):
        core.guardar_preferencia(PREF_ACTIVO, "1")
        return (
            "Aprendizaje automático activado. Cuando una conversación quede inactiva "
            "unos minutos, guardaré lo que valga la pena recordar y te avisaré."
        )
    if _DESACTIVAR.fullmatch(consulta):
        core.guardar_preferencia(PREF_ACTIVO, "0")
        return (
            "Aprendizaje automático desactivado. Solo guardaré lo que me pidas con "
            "«recuerda que…»."
        )
    if _LISTAR.fullmatch(consulta):
        aprendidos = jarvis_memory.listar_recuerdos(limite=50, origen="automatica")
        estado = "" if activo() else " (el aprendizaje automático está desactivado)"
        if not aprendidos:
            return f"Todavía no aprendí nada por mi cuenta{estado}."
        return (
            f"Esto aprendí de nuestras conversaciones{estado}:\n"
            + "\n".join(f"{i}. {c}" for i, c in aprendidos)
            + "\nSi algo no es correcto, di «olvida el recuerdo N»."
        )
    return None
