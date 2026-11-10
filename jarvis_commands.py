import ipaddress
import json
import re
import unicodedata
from urllib.parse import quote_plus, urlsplit

import requests

VOLUME_COMMAND_PATTERN = re.compile(
    r"^\s*(?:(?:jarvis|por favor|puedes|podr[ií]as|quiero)\s+)*"
    r"(?:sube|subir|aumenta|aumentar|baja|bajar|reduce|reducir|"
    r"ajusta|ajustar|establece|establecer|configura|configurar|pon|"
    r"coloca|colocar)\s+"
    r"(?:el\s+)?volumen(?:\s+(?:de\s+windows|del\s+sistema|del\s+equipo))?\s+"
    r"(?:a|al|en)\s+(\d{1,3})\s*(?:%|por\s+ciento|porciento)\s*[.!?]*\s*$",
    re.IGNORECASE,
)

OUTPUT_DEVICE_COMMAND_PATTERN = re.compile(
    r"^(?:(?:jarvis|por favor|puedes|podrias|quiero)\s+)*(?:que\s+)?"
    r"(?:(?:cambia|cambie|cambies|cambiar|pon|ponme|poner|usa|usar|"
    r"selecciona|seleccione|seleccionar)\s+"
    r"(?:(?:la|el)\s+)?"
    r"(?:(?:(?:salida|dispositivo|audio|sonido)"
    r"(?:\s+(?:de\s+(?:salida|audio|sonido)))?\s+(?:a|por|en)\s+)|a\s+)?"
    r")?(?:los\s+)?(altavoces|parlantes|auriculares|auris)"
    r"(?:\s+por favor)?$",
    re.IGNORECASE,
)

AUDIO_TOGGLE_COMMAND_PATTERN = re.compile(
    r"^(?:(?:jarvis|por favor|puedes|podr[ií]as|quiero)\s+)*"
    r"(?P<action>pausa(?:r)?|silencia(?:r)?|desactiva(?:r)?|"
    r"activa(?:r)?|enciende(?:r)?|habilita(?:r)?|reanuda(?:r)?)\s+"
    r"(?:el\s+)?(?:sonido|audio)(?:\s+de\s+jarvis)?[.!?]*$",
    re.IGNORECASE,
)

GAME_LAUNCH_COMMAND_PATTERN = re.compile(
    r"^\s*(?:(?:jarvis(?:\s*[,;:]\s*|\s+)|"
    r"(?:por favor|puedes|podrias|quiero)(?:\s*[,;:]\s*|\s+)))*"
    r"(?:abre|abrir|inicia|iniciar)\s+"
    r"(?:(?:el|la)\s+)?(?:juego\s+)?(.+?)\s*[.!?]*\s*$",
    re.IGNORECASE,
)

WINDOW_ACTION_PATTERNS = {
    "brave_minimize": re.compile(
        r"(?:(?:jarvis|por favor|puedes|podrias|quiero)\s+)*"
        r"(?:minimiza(?:r)?|minimice)"
        r"(?:\s+(?:(?:la|el|esta)\s+)?(?:ventana(?:\s+(?:de|del))?\s+)?)"
        r"(?:brave|navegador)(?:\s+por favor)?"
    ),
    "brave_maximize": re.compile(
        r"(?:(?:jarvis|por favor|puedes|podrias|quiero)\s+)*"
        r"(?:maximiza(?:r)?|maximice)"
        r"(?:\s+(?:(?:la|el|esta)\s+)?(?:ventana(?:\s+(?:de|del))?\s+)?)"
        r"(?:brave|navegador)(?:\s+por favor)?"
    ),
    "minimize": re.compile(
        r"(?:(?:jarvis|por favor|puedes|podrias|quiero)\s+)*"
        r"minimiza(?:r)?(?:\s+(?:la|esta)\s+)?(?:app|aplicacion|ventana)?"
        r"(?:\s+por favor)?"
    ),
    "maximize": re.compile(
        r"(?:(?:jarvis|por favor|puedes|podrias|quiero)\s+)*"
        r"maximiza(?:r)?(?:\s+(?:la|esta)\s+)?(?:app|aplicacion|ventana)?"
        r"(?:\s+por favor)?"
    ),
    "close": re.compile(
        r"(?:(?:jarvis|por favor|puedes|podrias|quiero)\s+)*"
        r"(?:cierra|cerrar|cierre|salir|termina|terminar)"
        r"(?:\s+(?:(?:la|esta)\s+)?(?:app|aplicacion|ventana|jarvis))?"
        r"(?:\s+por favor)?"
    ),
}

BROWSER_NEW_TAB_PATTERN = re.compile(
    r"^(?:a[\s,]+)?(?:(?:jarvis|por favor|puedes|podrias|quiero)[,\s]+)*"
    r"(?:(?:abre|abrir|crea|crear)\s+)?"
    r"(?:(?:una|la)\s+)?(?:nueva\s+)?pesta(?:na|ña)"
    r"(?:\s+nueva)?(?:\s+(?:en\s+)?brave)?(?:\s+por favor)?$",
    re.IGNORECASE,
)

BROWSER_NAVIGATION_PATTERN = re.compile(
    r"^(?:(?:jarvis|por favor|puedes|podr[ií]as|quiero)[,\s]+)*"
    r"(?:ir|ve|anda|navega|dir[ií]gete|dirigirme)\s+a\s+(.+?)\s*$",
    re.IGNORECASE,
)

PLAN_APPROVAL_DECISION_PATTERNS = {
    True: re.compile(
        r"^(?:jarvis\s+)?(?:ejecutar|ejecuta|aprobar|aprueba|aceptar|acepta)"
        r"\s+(?:el\s+)?plan$",
        re.IGNORECASE,
    ),
    False: re.compile(
        r"^(?:jarvis\s+)?(?:no\s+(?:ejecutar|ejecutes|aprobar|apruebes|"
        r"aceptar|aceptes)\s+(?:el\s+)?plan|cancelar\s+(?:el\s+)?plan|"
        r"cancela\s+(?:el\s+)?plan)$",
        re.IGNORECASE,
    ),
}

YOUTUBE_ORDINAL_PATTERN = (
    r"(?:n[uú]mero\s+)?(?:\d{1,2}(?:\s*(?:º|°|er|ra|ro|do|da|to|ta|vo|va|mo|ma))?|"
    r"primer(?:o|a)?|segund[oa]|tercer(?:o|a)?|cuart[oa]|quint[oa]|"
    r"sext[oa]|s[eé]ptim[oa]|octav[oa]|noven[oa]|d[eé]cim[oa])"
)

YOUTUBE_PLAY_RESULT_SUFFIX = re.compile(
    r"\s+(?:y\s+)?(?:reproduce|reproduzca|reproducir|reproduceme|"
    r"pon|ponga|poner|dale\s+play(?:\s+a|\s+al)?)\s+"
    r"(?:(?:el|la)\s+)?(?P<ordinal>" + YOUTUBE_ORDINAL_PATTERN + r")\s+"
    r"(?:canci[oó]n|tema|resultado|video|v[ií]deo)"
    r"(?:\s+por favor)?\s*[.!?]*$",
    re.IGNORECASE,
)

YOUTUBE_PLAY_RESULT_COMMAND = re.compile(
    r"^\s*(?:(?:jarvis|por favor|puedes|podr[ií]as|quiero)[,\s]+)*"
    r"(?:reproduce|reproduzca|reproducir|reproduceme|pon|ponga|poner)\s+"
    r"(?:(?:el|la)\s+)?(?P<ordinal>" + YOUTUBE_ORDINAL_PATTERN + r")\s+"
    r"(?:canci[oó]n|tema|resultado|video|v[ií]deo)"
    r"(?:\s+(?:de\s+)?youtube)?(?:\s+por favor)?\s*[.!?]*$",
    re.IGNORECASE,
)

YOUTUBE_SEARCH_PREFIXES = (
    re.compile(
        r"^(?:abre|abrir)\s+brave\s+(?:y\s+)?(?:en\s+)?youtube\s+"
        r"(?:busca|busque|buscar)\s*(?:[,;:]\s*|\s+)(.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:busca|busque|buscar)\s+(?:en\s+)?youtube"
        r"(?:\s*[,;:]\s*|\s+)(.+)$",
        re.IGNORECASE,
    ),
    re.compile(r"^youtube\s+(?:busca|busque|buscar)\s+(.+)$", re.IGNORECASE),
)

YOUTUBE_PLAY_PREFIXES = (
    re.compile(
        r"^(?:reproduce|reproduzca|reproducir|reproduceme|"
        r"pon|ponga|poner)\s+(.+?)(?:\s+en\s+youtube)?\s*[.!?]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:youtube\s+)?(?:reproduce|reproduzca|reproducir|"
        r"reproduceme|pon|ponga|poner)\s+(.+?)\s+en\s+youtube\s*[.!?]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^youtube\s+(?:reproduce|reproduzca|reproducir|"
        r"reproduceme|pon|ponga|poner)\s+(.+?)\s*[.!?]*$",
        re.IGNORECASE,
    ),
)

def _convertir_ordinal_youtube(ordinal: str) -> int | None:
    normalizado = unicodedata.normalize("NFKD", ordinal)
    normalizado = normalizado.encode("ascii", "ignore").decode("ascii").lower()
    numero = re.match(r"\d+", normalizado)
    if numero is not None:
        indice = int(numero.group())
        return indice if indice > 0 else None
    ordinales = {
        "primer": 1,
        "primero": 1,
        "primera": 1,
        "segundo": 2,
        "segunda": 2,
        "tercer": 3,
        "tercero": 3,
        "tercera": 3,
        "cuarto": 4,
        "cuarta": 4,
        "quinto": 5,
        "quinta": 5,
        "sexto": 6,
        "sexta": 6,
        "septimo": 7,
        "septima": 7,
        "octavo": 8,
        "octava": 8,
        "noveno": 9,
        "novena": 9,
        "decimo": 10,
        "decima": 10,
    }
    return ordinales.get(normalizado.removeprefix("numero "))

def detectar_reproduccion_ordinal_youtube(texto: str) -> int | None:
    coincidencia = YOUTUBE_PLAY_RESULT_COMMAND.fullmatch(texto.strip())
    if coincidencia is None:
        return None
    return _convertir_ordinal_youtube(coincidencia.group("ordinal"))

def detectar_busqueda_youtube(texto: str) -> tuple[str, int | None] | None:
    comando = texto.strip()
    comando = re.sub(
        r"^(?:(?:jarvis|por favor|puedes|podr[ií]as|quiero)[,\s]+)+",
        "",
        comando,
        flags=re.IGNORECASE,
    )
    misma_ventana = re.match(
        r"^en\s+la\s+misma\s+ventana[,\s]+",
        comando,
        flags=re.IGNORECASE,
    ) is not None
    comando = re.sub(
        r"^en\s+la\s+misma\s+ventana[,\s]+",
        "",
        comando,
        flags=re.IGNORECASE,
    )
    comando = comando.strip(" \t.,!?¿¡")

    reproducir_indice = None
    coincidencia_ordinal = YOUTUBE_PLAY_RESULT_SUFFIX.search(comando)
    if coincidencia_ordinal is not None:
        reproducir_indice = _convertir_ordinal_youtube(
            coincidencia_ordinal.group("ordinal")
        )
        comando = YOUTUBE_PLAY_RESULT_SUFFIX.sub("", comando).strip(" \t.,!?¿¡")

    coincidencia = None
    if reproducir_indice is None or coincidencia_ordinal is not None:
        patrones_reproduccion = (
            YOUTUBE_PLAY_PREFIXES
            if misma_ventana
            else YOUTUBE_PLAY_PREFIXES[1:]
        )
        for patron in patrones_reproduccion:
            coincidencia = patron.fullmatch(comando)
            if coincidencia is not None:
                if reproducir_indice is None:
                    reproducir_indice = 1
                break

    if coincidencia is None:
        for patron in YOUTUBE_SEARCH_PREFIXES:
            coincidencia = patron.fullmatch(comando)
            if coincidencia is not None:
                break

    if coincidencia is None:
        return None

    consulta = coincidencia.group(1).strip(" \t.,!?¿¡")
    consulta = re.sub(
        r"^(?:(?:la|el)\s+)?(?:canci[oó]n|tema)\s+",
        "",
        consulta,
        flags=re.IGNORECASE,
    )
    return (consulta, reproducir_indice) if consulta else None

def obtener_primer_video_youtube(consulta: str, indice: int = 1) -> str:
    if indice < 1:
        raise ValueError("El número de resultado debe ser mayor que cero.")
    url = f"https://www.youtube.com/results?search_query={quote_plus(consulta)}"
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    response.raise_for_status()

    match = re.search(r"(?:var\s+)?ytInitialData\s*=\s*", response.text)
    if match is None:
        raise ValueError("YouTube no devolvió los datos de resultados esperados.")
    initial_data, _ = json.JSONDecoder().raw_decode(response.text, match.end())

    pending = [initial_data]
    videos_encontrados = 0
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            renderer = item.get("videoRenderer")
            if isinstance(renderer, dict):
                video_id = renderer.get("videoId")
                if isinstance(video_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                    videos_encontrados += 1
                    if videos_encontrados == indice:
                        return video_id
            pending.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item))
    if videos_encontrados == 0:
        raise ValueError(f"YouTube no encontró videos para «{consulta}».")
    raise ValueError(
        f"YouTube solo devolvió {videos_encontrados} resultados para «{consulta}»; "
        f"no existe el resultado número {indice}."
    )

def detectar_accion_ventana(texto: str) -> str | None:
    normalizado = unicodedata.normalize("NFKD", texto)
    normalizado = normalizado.encode("ascii", "ignore").decode("ascii").lower()
    normalizado = re.sub(r"[¿?¡!,.;:]", " ", normalizado)
    normalizado = " ".join(normalizado.split())
    for accion, patron in WINDOW_ACTION_PATTERNS.items():
        if patron.fullmatch(normalizado):
            return accion
    return None

def detectar_comando_nueva_pestana(texto: str) -> bool:
    normalizado = unicodedata.normalize("NFKD", texto.strip())
    normalizado = normalizado.encode("ascii", "ignore").decode("ascii").lower()
    normalizado = re.sub(r"[.!?¿¡]+$", "", normalizado).strip()
    return BROWSER_NEW_TAB_PATTERN.fullmatch(normalizado) is not None

def detectar_comando_navegacion_web(texto: str) -> str | None:
    coincidencia = BROWSER_NAVIGATION_PATTERN.fullmatch(texto.strip())
    if coincidencia is None:
        return None
    destino = coincidencia.group(1).strip(" \t,;!?¿¡")
    return destino or None

def normalizar_url_web(destino: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in destino):
        raise ValueError("La dirección contiene caracteres no válidos.")
    if any(character.isspace() for character in destino):
        raise ValueError("La dirección web no puede contener espacios.")
    url = destino if "://" in destino else f"https://{destino}"
    partes = urlsplit(url)
    if partes.scheme.lower() not in {"http", "https"} or not partes.hostname:
        raise ValueError("Usa una dirección web válida, como twitch.tv o google.com.ar.")
    if partes.username is not None or partes.password is not None:
        raise ValueError("No se permiten credenciales dentro de la dirección web.")
    try:
        puerto = partes.port
    except ValueError as error:
        raise ValueError("La dirección web contiene un puerto no válido.") from error
    if puerto is not None and not 1 <= puerto <= 65535:
        raise ValueError("La dirección web contiene un puerto no válido.")
    if "\\" in partes.netloc:
        raise ValueError("La dirección web contiene caracteres no válidos.")
    hostname = partes.hostname
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            hostname_ascii = hostname.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise ValueError("La dirección web contiene un nombre de host no válido.") from error
        labels = hostname_ascii.rstrip(".").split(".")
        if (
            len(hostname_ascii) > 253
            or not labels
            or any(
                not re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                    label,
                    re.IGNORECASE,
                )
                for label in labels
            )
        ):
            raise ValueError("La dirección web contiene un nombre de host no válido.")
    return url

def normalizar_nombre_juego(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto)
    normalizado = normalizado.encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalizado).split())


def detectar_decision_aprobacion_plan(texto: str) -> bool | None:
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    normalizado = "".join(
        caracter
        for caracter in normalizado
        if not unicodedata.combining(caracter)
    )
    palabras = re.findall(r"[a-z0-9]+", normalizado)
    while palabras and palabras[0] in {
        "jarvis",
        "si",
        "claro",
        "confirmo",
        "confirmacion",
        "por",
        "favor",
    }:
        if palabras[0] == "por":
            if len(palabras) < 2 or palabras[1] not in {"favor", "voz"}:
                break
            del palabras[:2]
        else:
            palabras.pop(0)
    while palabras and palabras[-1] in {"ahora", "por", "favor"}:
        if palabras[-1] == "favor" and len(palabras) > 1 and palabras[-2] == "por":
            del palabras[-2:]
        else:
            palabras.pop()

    rechazo = {
        ("no", "ejecutar", "plan"),
        ("no", "ejecutar", "el", "plan"),
        ("no", "no", "ejecutar", "plan"),
        ("no", "no", "ejecutar", "el", "plan"),
        ("no", "ejecutes", "el", "plan"),
        ("no", "aprobar", "el", "plan"),
        ("no", "aceptar", "el", "plan"),
        ("cancelar", "plan"),
        ("cancelar", "el", "plan"),
        ("cancela", "el", "plan"),
    }
    if tuple(palabras) in rechazo:
        return False
    aprobacion = {
        ("ejecutar", "plan"),
        ("ejecutar", "el", "plan"),
        ("ejecuta", "plan"),
        ("ejecuta", "el", "plan"),
        ("aprobar", "plan"),
        ("aprobar", "el", "plan"),
        ("aprueba", "el", "plan"),
        ("aceptar", "plan"),
        ("aceptar", "el", "plan"),
        ("acepta", "el", "plan"),
    }
    if tuple(palabras) in aprobacion:
        return True
    return None


def detectar_decision_guardar_rutina(texto: str) -> bool | None:
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    normalizado = "".join(
        caracter
        for caracter in normalizado
        if not unicodedata.combining(caracter)
    )
    palabras = re.findall(r"[a-z0-9]+", normalizado)
    while palabras and palabras[0] in {
        "jarvis",
        "si",
        "claro",
        "confirmo",
        "confirmacion",
        "por",
        "favor",
    }:
        if palabras[0] == "por":
            if len(palabras) < 2 or palabras[1] not in {"favor", "voz"}:
                break
            del palabras[:2]
        else:
            palabras.pop(0)
    while palabras and palabras[-1] in {"ahora", "por", "favor"}:
        if palabras[-1] == "favor" and len(palabras) > 1 and palabras[-2] == "por":
            del palabras[-2:]
        else:
            palabras.pop()

    rechazo = {
        ("no", "guardar", "rutina"),
        ("no", "guardar", "la", "rutina"),
        ("no", "no", "guardar", "rutina"),
        ("no", "no", "guardar", "la", "rutina"),
        ("no", "guardes", "la", "rutina"),
        ("cancelar", "rutina"),
        ("cancelar", "la", "rutina"),
        ("cancela", "la", "rutina"),
    }
    if tuple(palabras) in rechazo:
        return False
    aprobacion = {
        ("guardar", "rutina"),
        ("guardar", "la", "rutina"),
        ("guarda", "rutina"),
        ("guarda", "la", "rutina"),
    }
    if tuple(palabras) in aprobacion:
        return True
    return None


def detectar_comando_juego(texto: str) -> str | None:
    coincidencia = GAME_LAUNCH_COMMAND_PATTERN.fullmatch(texto)
    if coincidencia is None:
        return None
    nombre = coincidencia.group(1).strip(" \t,;:")
    return nombre or None

def detectar_comando_volumen(texto: str) -> int | None:
    texto = limpiar_transcripcion_voz(texto)
    coincidencia = VOLUME_COMMAND_PATTERN.fullmatch(texto)
    if coincidencia is None:
        return None
    porcentaje = int(coincidencia.group(1))
    if not 0 <= porcentaje <= 100:
        return None
    return porcentaje

def detectar_comando_audio(texto: str) -> bool | None:
    normalizado = unicodedata.normalize("NFKD", texto.strip())
    normalizado = normalizado.encode("ascii", "ignore").decode("ascii").lower()
    coincidencia = AUDIO_TOGGLE_COMMAND_PATTERN.fullmatch(normalizado)
    if coincidencia is None:
        return None
    accion = coincidencia.group("action")
    return accion.startswith(("activa", "enciende", "habilita", "reanuda"))

def detectar_comando_dispositivo_salida(texto: str) -> str | None:
    normalizado = unicodedata.normalize("NFKD", texto)
    normalizado = normalizado.encode("ascii", "ignore").decode("ascii").lower()
    normalizado = re.sub(r"[^\w\s]", " ", normalizado)
    normalizado = " ".join(normalizado.split())
    coincidencia = OUTPUT_DEVICE_COMMAND_PATTERN.fullmatch(normalizado)
    if coincidencia is None:
        return None
    palabra = coincidencia.group(1)
    return "speakers" if palabra in {"altavoces", "parlantes"} else "headphones"

# Errores de Whisper vistos con las órdenes de Jarvis (palabras enteras, sin
# distinguir mayúsculas). Solo frases que no tienen otro sentido en una orden.
_CORRECCIONES_VOZ = (
    (r"\by\s+ya\s+anuda\b", "y reanuda"),  # «pausa X y ya anuda Y» (2026-11-06)
    (r"\b(?:ya|re|rea)\s+anuda\b", "reanuda"),
    (r"\brea\s+nuda\b", "reanuda"),
    (r"\bre\s+anudar?\b", "reanuda"),
    (r"\bvaos\s+al\s+(v[ií]deo)\b", r"pausa el \1"),
    (r"\bdes(?:hutea|\s+mutea|mutia|mute\s+a)\b", "desmutea"),
    (r"\bmute\s+a\s+(la|el)\b", r"mutea \1"),
    (r"\bmutia\b", "mutea"),
    (r"\bspot(?:icia|ifai|ifay|ifi)\b", "Spotify"),
    (r"\b(?:jarbis|yarvis|harvis|yarbis)\b", "Jarvis"),
    (r"\bpesta[nñ]a\s+de\s+you\s+tube\b", "pestaña de YouTube"),
    (r"\byou\s+tube\b", "YouTube"),
    (r"\b(volumen\s+de\s+\w+)\s+el\s+(\d)", r"\1 al \2"),  # «… de Spotify el 30 %»
    (r"(\d)\s*%", r"\1 por ciento"),  # Whisper escribe «30%»
)


def limpiar_transcripcion_voz(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto.replace(",", " ")).strip()
    for patron, reemplazo in _CORRECCIONES_VOZ:
        texto = re.sub(patron, reemplazo, texto, flags=re.IGNORECASE)
    return texto


# --- Consultas de sistema de solo lectura (roadmap punto 2) ---------------------

_SYSTEM_QUERY_START = re.compile(
    r"^(?:que|cuales|cual|lista|listar|listame|muestra|muestrame|mostrar|dime|"
    r"ensename|como|cuanta|cuanto|quien|hay)\b"
)
_SYSTEM_QUERY_FORBIDDEN = re.compile(
    r"\b(?:cierra|cerrar|cierres|mata|matar|finaliza|finalizar|termina|terminar|"
    r"detén|deten|detener|elimina|eliminar|abre|abrir|inicia|iniciar)\b"
)
_APPS_WORD = re.compile(r"\b(?:aplicaciones|apps|programas|ventanas)\b")
_OPEN_WORD = re.compile(r"\babiert[oa]s?\b")
_TITLES_WORD = re.compile(r"\btitulos?\b")
_PROCESS_RESOURCE = re.compile(r"\b(?:memoria|ram|cpu|procesador)\b")
_PROCESS_CONSUME = re.compile(
    r"\b(?:consume|consumen|consumiendo|usa|usan|usando|gasta|gastan|gastando|"
    r"ocupa|ocupan|ocupando)\b"
)
_STATUS_PATTERNS = (
    re.compile(
        r"^(?:como|que tal)\s+(?:esta|va|anda)\s+(?:el|mi)\s+"
        r"(?:equipo|pc|ordenador|computador|computadora|sistema)$"
    ),
    re.compile(r"\bestado\s+del?\s+(?:equipo|pc|ordenador|sistema|computador|computadora)\b"),
    re.compile(r"\b(?:cuanta|cuanto)\s+(?:ram|memoria)\b"),
    re.compile(r"\buso\s+de\s+(?:la\s+)?(?:cpu|ram|memoria|disco|procesador)\b"),
    re.compile(r"\b(?:cuanto|que)\s+espacio\b.*\bdisco\b"),
    re.compile(r"\bespacio\s+libre\b"),
    re.compile(r"\b(?:cuanta|que)\s+bateria\b"),
    re.compile(r"\bcuanto\s+(?:tiempo\s+)?lleva\s+encendid[oa]\b"),
)


_DIAGNOSTIC_PATTERNS = (
    re.compile(r"^(?:haz\s+(?:un\s+)?)?diagnostico(?:\s+de\s+jarvis)?$"),
    re.compile(r"\bdiagnostico\s+de\s+jarvis\b"),
    re.compile(r"\bestado\s+de\s+(?:jarvis|los\s+servicios)\b"),
    re.compile(r"^(?:funciona|esta\s+funcionando|anda|responde)\s+ollama$"),
)


def _normalizar_consulta(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto.casefold())
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    limpio = re.sub(r"[^a-z0-9%]+", " ", sin_tildes).strip()
    return re.sub(r"^(?:jarvis|oye jarvis|por favor)\s+", "", limpio)


def detectar_consulta_sistema(texto: str) -> tuple[str, dict[str, object]] | None:
    """Reconoce preguntas de solo lectura sobre el equipo.

    Devuelve ``(herramienta, argumentos)`` o None. Las frases que piden cerrar,
    abrir o detener algo se dejan al agente: estas consultas nunca actúan.
    """
    consulta = _normalizar_consulta(texto)
    if not consulta or _SYSTEM_QUERY_FORBIDDEN.search(consulta):
        return None
    pregunta = _SYSTEM_QUERY_START.match(consulta) is not None

    if pregunta and (
        re.search(r"\bprocesos\b", consulta)
        or (re.search(r"\bmas\b", consulta) and _PROCESS_RESOURCE.search(consulta)
            and _PROCESS_CONSUME.search(consulta))
    ):
        criterio = "cpu" if re.search(r"\b(?:cpu|procesador)\b", consulta) else "memory"
        return "list_top_processes", {"sort_by": criterio}

    if pregunta and _APPS_WORD.search(consulta) and (
        _OPEN_WORD.search(consulta) or re.search(r"\bventanas\b", consulta)
    ):
        return "list_open_applications", {
            "include_titles": _TITLES_WORD.search(consulta) is not None
        }

    if any(pattern.search(consulta) for pattern in _DIAGNOSTIC_PATTERNS):
        return "get_jarvis_diagnostics", {}
    if any(pattern.search(consulta) for pattern in _STATUS_PATTERNS):
        return "get_system_status", {}
    return None


_CLEAR_HISTORY_PATTERN = re.compile(
    r"^(?:borra|borrar|elimina|eliminar|vacia|vaciar|limpia|limpiar)\s+"
    r"(?:todo\s+)?(?:el\s+|mi\s+)?historial"
    r"(?:\s+de\s+(?:las\s+)?(?:conversaciones|conversacion|chats?|mensajes))?$"
)


def detectar_borrado_historial(texto: str) -> bool:
    """«Borra el historial de conversaciones» (siempre se pide confirmación)."""
    return _CLEAR_HISTORY_PATTERN.fullmatch(_normalizar_consulta(texto)) is not None


# --- Visión de pantalla (roadmap punto 6) ----------------------------------------

_NUMEROS_MONITOR = {
    "uno": 1, "primero": 1, "primer": 1, "principal": None,
    "dos": 2, "segundo": 2, "tres": 3, "tercero": 3, "tercer": 3,
    "cuatro": 4, "cuarto": 4,
}
_VISION_PATTERN = re.compile(
    r"^(?:(?:puedes|podrias)\s+)?"
    r"(?:mira|mirar|miras|analiza|analizar|revisa|revisar|captura|capturar|observa|"
    r"fijate\s+en|que\s+ves\s+en|que\s+hay\s+en|que\s+se\s+ve\s+en|que\s+sale\s+en|"
    r"que\s+dice)\s+"
    r"(?:a\s+)?(?:mi\s+|la\s+|el\s+|esta\s+|este\s+)?(?P<objeto>pantalla|monitor)"
    r"(?:\s+(?:numero\s+)?(?P<numero>\d|uno|dos|tres|cuatro|principal|primero|primer|"
    r"segundo|tercero|tercer|cuarto))?"
    r"(?:\s+(?:y\s+)?(?P<pregunta>.+))?$"
)


def detectar_vision(texto: str) -> tuple[int | None, str] | None:
    """«Mira el monitor 2 y dime qué error sale» → (2, «dime qué error sale»).

    Devuelve (número de monitor o None para el principal, pregunta) o None.
    La captura siempre pasa por la vista previa antes de analizarse.
    """
    consulta = _normalizar_consulta(texto)
    match = _VISION_PATTERN.fullmatch(consulta)
    if match is None:
        return None
    valor = match.group("numero")
    numero = None
    if valor is not None:
        numero = int(valor) if valor.isdigit() else _NUMEROS_MONITOR[valor]
    return numero, _pregunta_original(texto, match.group("pregunta") or "")


def _pregunta_original(texto: str, pregunta_normalizada: str) -> str:
    """Recupera la pregunta con sus tildes y mayúsculas a partir del texto original."""
    objetivo = pregunta_normalizada.split()
    if not objetivo:
        return ""
    palabras = texto.split()
    normalizadas = [_normalizar_consulta(p) for p in palabras]
    for inicio in range(len(palabras)):
        cola = " ".join(n for n in normalizadas[inicio:] if n).split()
        if cola == objetivo:
            return " ".join(palabras[inicio:]).strip(" ¿?¡!.,")
    return pregunta_normalizada


def detectar_decision_vision(texto: str) -> bool | None:
    """Decisión por voz en la vista previa de la captura: analizar o cancelar."""
    palabras = re.findall(r"[a-z0-9]+", _normalizar_consulta(texto))
    if not palabras:
        return None
    frase = " ".join(palabras)
    if re.fullmatch(
        r"(?:si\s+)?(?:no|cancela|cancelar|descarta|descartar|no\s+analices|no\s+analizar|"
        r"no\s+la\s+envies|no\s+enviar)(?:\s+la\s+captura)?",
        frase,
    ):
        return False
    if re.fullmatch(
        r"(?:si\s+)?(?:analiza|analizar|analizala|envia|enviar|enviala|adelante|"
        r"si\s+analiza|si)(?:\s+la\s+captura)?(?:\s+por\s+favor)?",
        frase,
    ):
        return True
    return None


# --- Configuración de monitores ----------------------------------------------------

_NUM_MONITOR = r"(\d|uno|dos|tres|cuatro|cinco|seis)"
_MONITOR = r"(?:el\s+|la\s+)?(?:monitor|pantalla)\s+(?:numero\s+)?"
_DISABLE_MONITOR = re.compile(
    r"^(?:desactiva|desactivar|apaga|apagar|deshabilita|deshabilitar|desconecta|desconectar)\s+"
    + _MONITOR + _NUM_MONITOR + r"$"
)
_EXTEND_MONITOR = re.compile(
    r"^(?:extiende|extender|activa|activar|enciende|encender|habilita|habilitar|reactiva|reactivar)"
    r"(?:\s+(?:el\s+escritorio|la\s+pantalla))?\s+(?:a\s+|al\s+|en\s+)?"
    + r"(?:el\s+|la\s+)?(?:monitor|pantalla)\s+(?:numero\s+)?" + _NUM_MONITOR + r"$"
)
_DUPLICATE_MONITORS = re.compile(
    r"^(?:duplica|duplicar|clona|clonar|espeja|espejar)\s+"
    r"(?:(?:los|las)\s+)?(?:(?:monitores|pantallas|el\s+monitor|la\s+pantalla|monitor|pantalla)\s+)?"
    + _NUM_MONITOR
    + r"\s+(?:y|con|en)\s+(?:el\s+|la\s+)?(?:(?:monitor|pantalla)\s+)?" + _NUM_MONITOR + r"$"
)


_PRIMARY_MONITOR = (
    re.compile(
        r"^(?:pon|poner|establece|establecer|configura|configurar|haz|hacer|define|definir|"
        r"usa|usar|deja|dejar)\s+(?:a\s+)?" + _MONITOR + _NUM_MONITOR
        + r"\s+(?:como\s+)?(?:el\s+|la\s+)?(?:(?:monitor|pantalla)\s+)?principal$"
    ),
    re.compile(
        r"^(?:haz|hacer|pon|poner)\s+(?:como\s+)?principal\s+(?:al\s+|a\s+la\s+|el\s+|la\s+)?"
        r"(?:monitor|pantalla)\s+(?:numero\s+)?" + _NUM_MONITOR + r"$"
    ),
    re.compile(
        r"^(?:cambia|cambiar|pasa|pasar)\s+(?:el\s+|la\s+)?(?:monitor|pantalla)\s+principal\s+"
        r"(?:al|a\s+la|a)\s+(?:(?:monitor|pantalla)\s+)?(?:numero\s+)?" + _NUM_MONITOR + r"$"
    ),
    re.compile(
        r"^(?:que\s+)?" + _MONITOR + _NUM_MONITOR
        + r"\s+(?:sea|pase\s+a\s+ser)\s+(?:el\s+|la\s+)?(?:(?:monitor|pantalla)\s+)?principal$"
    ),
)


def _numero_monitor(valor: str) -> int:
    return int(valor) if valor.isdigit() else {
        "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    }[valor]


_FIX_ESCALADO = re.compile(
    r"^(?:arregla|arreglar|arreglame|corrige|corregir|repara|reparar|reajusta|reajustar|"
    r"resetea|resetear|reinicia|reiniciar|soluciona|solucionar|acomoda|acomodar)\s+"
    r"(?:el\s+|la\s+)?(?:escalado|escala|barra(?:\s+de\s+tareas)?)"
    r"(?:\s+(?:del|de\s+la|en\s+el|en\s+la)\s+(?:monitor|pantalla)\s+(?:numero\s+)?"
    + _NUM_MONITOR + r")?$"
)


_PANTALLA_TV = r"(?:la\s+|el\s+)?(?:smart\s+)?(?:tv|tele|televisor|television)"
_MOVER_VENTANA = re.compile(
    r"^(?:manda|mandame|mueve|muevelo|movela|pasa|pasame|lleva|llevame|pon|ponme|envia|tira|trae|traeme)\s+"
    r"(?:a\s+)?(?:(?:el|la|los|las)\s+)?(?:ventanas?\s+(?:de\s+(?:la\s+|el\s+)?)?)?(?P<app>.+?)\s+"
    r"(?:al|a\s+la|a\s+el|en\s+el|en\s+la|hacia\s+el|hacia\s+la|a|para\s+el|para\s+la)\s+"
    r"(?:(?P<tv>" + _PANTALLA_TV + r")|(?:monitor|pantalla)\s+(?:numero\s+)?" + _NUM_MONITOR + r")$"
)


def detectar_mover_ventana(texto: str) -> tuple[str, dict[str, object]] | None:
    """«manda Brave a la TV», «pon Discord en el monitor 2», «trae todo al monitor 1»."""
    match = _MOVER_VENTANA.fullmatch(_normalizar_consulta(texto))
    if match is None:
        return None
    app = re.sub(r"^(?:ventanas?\s+(?:de\s+)?)", "", match.group("app")).strip()
    if not app or re.fullmatch(r"(?:el\s+|la\s+)?(?:monitor|pantalla|escritorio)(?:\s+.*)?", app):
        return None
    if match.group("tv"):
        return "move_window", {"app": app, "screen_name": "tv"}
    return "move_window", {"app": app, "monitor": _numero_monitor(match.group(3))}


def detectar_comando_monitores(texto: str) -> tuple[str, dict[str, object]] | None:
    """«desactiva el monitor 3», «extiende el monitor 3», «duplica los monitores 1 y 3»,
    «arregla el escalado del monitor 3»."""
    consulta = _normalizar_consulta(texto)
    if (match := _FIX_ESCALADO.fullmatch(consulta)) is not None:
        return "fix_display_scaling", (
            {"monitor": _numero_monitor(match.group(1))} if match.group(1) else {}
        )
    for patron in _PRIMARY_MONITOR:
        if (match := patron.fullmatch(consulta)) is not None:
            return "configure_display", {
                "action": "set_primary",
                "monitor": _numero_monitor(match.group(1)),
            }
    if (match := _DISABLE_MONITOR.fullmatch(consulta)) is not None:
        return "configure_display", {"action": "disable", "monitor": _numero_monitor(match.group(1))}
    if (match := _EXTEND_MONITOR.fullmatch(consulta)) is not None:
        return "configure_display", {"action": "extend", "monitor": _numero_monitor(match.group(1))}
    if (movimiento := detectar_mover_ventana(texto)) is not None:
        return movimiento
    if (match := _DUPLICATE_MONITORS.fullmatch(consulta)) is not None:
        return "configure_display", {
            "action": "duplicate",
            "monitor": _numero_monitor(match.group(1)),
            "second_monitor": _numero_monitor(match.group(2)),
        }
    return None


_EQUIPO = (
    r"(?:el\s+|la\s+|mi\s+|este\s+|esta\s+)?"
    r"(?:equipo|pc|ordenador|computadora|computador|compu|maquina|sistema|windows)"
)
_ACCIONES_ENERGIA_DIRECTAS = (
    (
        re.compile(r"^(?:apaga|apagar|apagame|apagues)\s+" + _EQUIPO + r"(?:\s+ahora)?$"),
        "power_control",
        {"action": "shutdown"},
    ),
    (
        re.compile(
            r"^(?:reinicia|reiniciar|reiniciame|reinicies|resetea|resetear)\s+"
            + _EQUIPO + r"(?:\s+ahora)?$"
        ),
        "power_control",
        {"action": "restart"},
    ),
    (
        re.compile(
            r"^(?:(?:suspende|suspender|suspendeme|duerme|dormir)\s+" + _EQUIPO
            + r"|(?:pon|poner|manda|mandar|deja|dejar)\s+" + _EQUIPO
            + r"\s+(?:en|a)\s+(?:suspension|suspender|reposo|modo\s+(?:suspension|reposo)))$"
        ),
        "power_control",
        {"action": "sleep"},
    ),
    (
        re.compile(
            r"^(?:(?:bloquea|bloquear|bloqueame)\s+(?:" + _EQUIPO
            + r"|(?:la\s+)?(?:sesion|pantalla))"
            + r"|(?:bloqueo|bloqueo\s+de)\s+(?:la\s+)?(?:sesion|pantalla))$"
        ),
        "lock_computer",
        {},
    ),
    (
        re.compile(
            r"^(?:cancela|cancelar|anula|anular|deten|detener|para|parar|aborta|abortar)\s+"
            r"(?:el\s+)?(?:apagado|reinicio)(?:\s+(?:del?\s+)?" + _EQUIPO + r")?$"
            r"|^no\s+(?:lo\s+)?(?:apagues|reinicies)(?:\s+" + _EQUIPO + r")?$"
        ),
        "cancel_power_action",
        {},
    ),
)


def detectar_comando_energia(texto: str) -> tuple[str, dict[str, object]] | None:
    """«apaga el equipo», «reinicia la pc», «suspende el equipo», «bloquea la pc»,
    «cancela el apagado». Solo frases completas, para no confundir «apaga el
    monitor 3» con apagar el equipo."""
    consulta = _normalizar_consulta(texto)
    for patron, herramienta, argumentos in _ACCIONES_ENERGIA_DIRECTAS:
        if patron.fullmatch(consulta):
            return herramienta, dict(argumentos)
    return None


_VERBOS_ENERGIA = {
    "shutdown": r"apaga|apagalo|apagala|apagar|apagarlo",
    "restart": r"reinicia|reinicialo|reiniciala|reiniciar|reiniciarlo",
    "sleep": r"suspende|suspendelo|suspendela|suspender|suspenderlo",
}


def detectar_decision_monitores(texto: str) -> bool | None:
    """Respuesta por voz a «¿Mantener esta configuración de pantallas?».

    Además del «sí»/«no» de siempre acepta «mantener», «mantenla», «déjala así»,
    «revierte» o «vuelve atrás».
    """
    frase = " ".join(re.findall(r"[a-z0-9]+", _normalizar_consulta(texto)))
    if re.fullmatch(
        r"(?:si\s+)?(?:mantener|mantenla|mantenlo|mantenerla|manten(?:la|lo)?|"
        r"dejala(?:\s+asi)?|dejalo(?:\s+asi)?|asi\s+esta\s+bien|esta\s+bien|perfecto)"
        r"(?:\s+(?:la\s+)?configuracion)?(?:\s+por\s+favor)?",
        frase,
    ):
        return True
    if re.fullmatch(
        r"(?:no\s+)?(?:revierte|revertir|revertila|revertilo|deshaz|deshazlo|"
        r"vuelve\s+(?:atras|como\s+estaba)|como\s+estaba)(?:\s+.*)?",
        frase,
    ):
        return False
    return detectar_decision_energia(texto, "")


def detectar_decision_energia(texto: str, accion: str) -> bool | None:
    """Respuesta por voz a «¿Apago/Reinicio/Suspendo el equipo?».

    Acepta «sí», «confirmo», «adelante», «hazlo» o el verbo de la acción pedida
    («apágalo» si se preguntó por apagar; «reinícialo» no aprueba un apagado).
    Cualquier respuesta que empiece por «no», «cancela» o «mejor no» rechaza.
    """
    frase = " ".join(re.findall(r"[a-z0-9]+", _normalizar_consulta(texto)))
    if not frase:
        return None
    if re.fullmatch(
        r"(?:mejor\s+)?(?:no|nop|cancela|cancelar|cancelalo|deja|dejalo|para|espera|olvidalo)"
        r"(?:\s+.*)?",
        frase,
    ):
        return False
    verbos = _VERBOS_ENERGIA.get(accion)
    nucleo = r"si|confirmo|confirmado|adelante|hazlo|dale|de\s+acuerdo|ok|okay|vale"
    if verbos:
        nucleo += "|" + verbos
    if re.fullmatch(
        r"(?:si\s+)?(?:" + nucleo + r")"
        r"(?:\s+(?:" + (verbos or "si") + r"))?"
        r"(?:\s+(?:el\s+|la\s+)?(?:equipo|pc|ordenador|computadora))?"
        r"(?:\s+(?:ya|ahora))?(?:\s+por\s+favor)?",
        frase,
    ):
        return True
    return None


_ESCUCHA = (
    r"(?:la\s+)?(?:palabra\s+(?:de\s+)?(?:activacion|clave)|escucha(?:\s+continua)?"
    r"|activacion\s+por\s+voz|hey\s+jarvis|oye\s+jarvis)"
)
_ESCUCHA_ON = re.compile(
    r"^(?:activa|activar|enciende|encender|habilita|habilitar|reactiva|reactivar)\s+"
    + _ESCUCHA + r"$|^(?:escuchame|escucha)\s+(?:siempre|todo\s+el\s+tiempo)$"
)
_ESCUCHA_OFF = re.compile(
    r"^(?:desactiva|desactivar|apaga|apagar|deshabilita|deshabilitar|quita|quitar)\s+"
    + _ESCUCHA + r"$|^(?:deja|dejar)\s+de\s+escuchar(?:me)?(?:\s+siempre)?$"
)


def detectar_comando_escucha(texto: str) -> bool | None:
    """«activa la palabra de activación» → True; «deja de escucharme» → False."""
    consulta = _normalizar_consulta(texto)
    if _ESCUCHA_ON.fullmatch(consulta):
        return True
    if _ESCUCHA_OFF.fullmatch(consulta):
        return False
    return None


_CON_WINDOWS = (
    r"(?:con|al\s+(?:iniciar|arrancar|encender|prender)|junto\s+con)\s+"
    r"(?:windows|la\s+pc|el\s+pc|el\s+equipo|la\s+computadora|el\s+ordenador)"
)
_INICIO_WINDOWS = rf"(?:el\s+)?(?:inicio|arranque)(?:\s+automatico)?(?:\s+{_CON_WINDOWS}|\s+de\s+windows)"
_INICIO_ON = re.compile(
    rf"^(?:activa|activar|habilita|habilitar|enciende|encender|pon|poner)\s+{_INICIO_WINDOWS}$"
    rf"|^(?:iniciate|inicia|arranca|arrancate|abrete|ejecutate)\s+(?:siempre\s+)?{_CON_WINDOWS}$"
    rf"|^(?:quiero\s+)?que\s+(?:jarvis\s+)?(?:se\s+)?(?:inicie|arranque|abra)\s+(?:siempre\s+)?{_CON_WINDOWS}$"
)
_INICIO_OFF = re.compile(
    rf"^(?:desactiva|desactivar|deshabilita|deshabilitar|quita|quitar|apaga|apagar)\s+{_INICIO_WINDOWS}$"
    rf"|^no\s+(?:te\s+)?(?:inicies|arranques|abras|ejecutes)\s+(?:mas\s+)?{_CON_WINDOWS}$"
    rf"|^(?:quiero\s+)?que\s+(?:jarvis\s+)?no\s+(?:se\s+)?(?:inicie|arranque|abra)\s+(?:mas\s+)?{_CON_WINDOWS}$"
)


def detectar_comando_inicio_windows(texto: str) -> bool | None:
    """«inicia con Windows» → True; «desactiva el inicio con Windows» → False."""
    consulta = _normalizar_consulta(texto)
    if _INICIO_ON.fullmatch(consulta):
        return True
    if _INICIO_OFF.fullmatch(consulta):
        return False
    return None


_PIDE = r"(?:(?:me\s+)?(?:dices|decis|das|puedes\s+decir|podes\s+decir)\s+|dime\s+|decime\s+|sabes\s+)?"
_FECHA_HORA = (
    (re.compile(
        r"^" + _PIDE + r"(?:que\s+(?:dia|fecha)\s+y\s+(?:que\s+)?hora\s+es(?:\s+hoy)?"
        r"|la\s+fecha\s+y\s+(?:la\s+)?hora)$"
    ), "both"),
    (re.compile(
        r"^" + _PIDE + r"(?:que\s+hora\s+(?:es|tenemos|son)(?:\s+ahora)?|la\s+hora)$"
    ), "time"),
    (re.compile(
        r"^" + _PIDE + r"(?:(?:que|en\s+que|a\s+que)\s+(?:dia|fecha)\s+(?:es|estamos)(?:\s+hoy)?"
        r"|que\s+dia\s+de\s+la\s+semana\s+es(?:\s+hoy)?|la\s+fecha(?:\s+de\s+hoy)?)$"
    ), "date"),
)


def detectar_consulta_fecha_hora(texto: str) -> tuple[str, dict[str, object]] | None:
    """«¿qué hora es?», «¿qué día es hoy?», «dime la fecha y la hora»."""
    consulta = _normalizar_consulta(texto)
    for patron, que in _FECHA_HORA:
        if patron.fullmatch(consulta):
            return "get_current_datetime", {"what": que}
    return None


_BATERIA_AURICULARES = re.compile(
    r"^(?:(?:cuanta|que|cual\s+es\s+la|como\s+esta\s+la|como\s+anda\s+la|dime\s+la|decime\s+la)\s+)?"
    r"bateria\s+(?:(?:les\s+queda\s+a|tienen|tiene|queda\s+en|hay\s+en|del|de)\s+)?"
    r"(?:los\s+|mis\s+|el\s+)?(?:auriculares|cascos|audifonos|headset|g533)(?:\s+g533)?$"
    r"|^(?:cuanta|que)\s+bateria\s+(?:les\s+)?queda\s+(?:a\s+)?(?:los\s+|mis\s+)?"
    r"(?:auriculares|cascos|audifonos)$"
)


def detectar_consulta_bateria(texto: str) -> tuple[str, dict[str, object]] | None:
    """«¿cuánta batería tienen los auriculares?», «batería de los auriculares»."""
    if _BATERIA_AURICULARES.fullmatch(_normalizar_consulta(texto)):
        return "get_headset_battery", {}
    return None


def detectar_comando_multimedia(texto: str) -> tuple[str, dict[str, object]] | None:
    """«pausa la música», «siguiente canción», «¿qué está sonando?»,
    «pon el volumen de Spotify al 30», «silencia Discord» (ver jarvis_media)."""
    import jarvis_media

    return jarvis_media.detectar_comando(_normalizar_consulta(texto))


_NOMBRES_TEMA = {
    "cian": "cian", "celeste": "cian", "azul": "cian", "oscuro": "cian", "normal": "cian",
    "original": "cian", "por defecto": "cian",
    "mark": "mark", "rojo": "mark", "dorado": "mark", "rojo y dorado": "mark",
    "iron man": "mark", "ironman": "mark",
    "claro": "claro", "blanco": "claro",
}
_TEMA = "(" + "|".join(sorted(map(re.escape, _NOMBRES_TEMA), key=len, reverse=True)) + ")"
_CAMBIA_TEMA = re.compile(
    r"^(?:cambia|cambiar|pon|poner|ponme|usa|usar|activa|activar|pasa|pasar|elige)\s+"
    r"(?:(?:el\s+)?(?:tema|modo|estilo|color(?:es)?)\s+(?:a\s+|al\s+|en\s+)?(?:(?:el\s+)?(?:tema|modo)\s+)?"
    r"|(?:a|al)\s+(?:(?:el\s+)?(?:tema|modo)\s+)?)" + _TEMA + r"$"
)


def detectar_comando_tema(texto: str) -> tuple[str, dict[str, object]] | None:
    """«cambia al tema rojo», «pon el modo claro», «usa el tema cian»."""
    if (match := _CAMBIA_TEMA.fullmatch(_normalizar_consulta(texto))) is not None:
        return "set_theme", {"theme": _NOMBRES_TEMA[match.group(1)]}
    return None


_COMPACTO_ON = re.compile(
    r"^(?:(?:activa|activar|pon|ponte|ponme|pasa|pasate|cambia|cambiate|usa)\s+(?:a(?:l)?\s+|en\s+)?)?"
    r"(?:el\s+)?modo\s+(?:compacto|mini|minimo|pequeno)$"
    r"|^(?:hazte|ponte)\s+(?:pequeno|chiquito|compacto)$"
)
_COMPACTO_OFF = re.compile(
    r"^(?:(?:quita|quitar|desactiva|desactivar|sal|salir)\s+(?:del?\s+)?(?:el\s+)?modo\s+(?:compacto|mini)"
    r"|(?:vuelve|volve|pasa|cambia)\s+(?:a(?:l)?\s+)?(?:la\s+)?(?:ventana|modo)\s+(?:completa|completo|normal|grande)"
    r"|(?:abre|muestra)\s+(?:la\s+)?ventana\s+(?:completa|grande))$"
)


def detectar_comando_compacto(texto: str) -> tuple[str, dict[str, object]] | None:
    """«modo compacto», «ponte en modo mini» / «vuelve a la ventana completa»."""
    consulta = _normalizar_consulta(texto)
    if _COMPACTO_ON.fullmatch(consulta):
        return "set_compact_mode", {"enabled": True}
    if _COMPACTO_OFF.fullmatch(consulta):
        return "set_compact_mode", {"enabled": False}
    return None


_LIMPIEZA = re.compile(
    r"^(?:libera|liberar|liberame|haz|hace|hacer|haceme|limpia|limpiar|limpiame)\s+"
    r"(?:(?:un\s+poco\s+de\s+|algo\s+de\s+)?espacio(?:\s+(?:en|del)\s+(?:el\s+)?disco)?"
    r"|(?:una\s+)?limpieza(?:\s+(?:del|de)\s+(?:el\s+)?(?:disco|equipo|pc))?"
    r"|(?:el\s+)?(?:disco|equipo|pc))$"
)
_LIMPIEZA_SOLO = (
    (re.compile(r"^(?:vacia|vaciar|vaciame|limpia|limpiar)\s+(?:la\s+)?papelera(?:\s+de\s+reciclaje)?$"), "papelera"),
    (re.compile(r"^(?:borra|borrar|limpia|limpiar|elimina)\s+(?:los\s+)?(?:archivos\s+)?temporales$"), "temporales"),
    (re.compile(r"^(?:borra|borrar|limpia|limpiar)\s+(?:la\s+)?(?:cache|cach)\s+(?:de|del)\s+(?:brave|navegador)$"),
     "cache_brave"),
)


def detectar_comando_limpieza(texto: str) -> dict[str, object] | None:
    """«libera espacio», «haz una limpieza del disco», «vacía la papelera»… → {"solo": clave|None}."""
    consulta = _normalizar_consulta(texto)
    if _LIMPIEZA.fullmatch(consulta):
        return {"solo": None}
    for patron, clave in _LIMPIEZA_SOLO:
        if patron.fullmatch(consulta):
            return {"solo": clave}
    return None


_CAMBIA_VOZ = re.compile(
    r"^(?:cambia|cambiar|cambiame|pon|poner|ponme|usa|usar|elige|habla\s+con)\s+"
    r"(?:(?:tu|la|de)\s+)?voz\s+(?:a|por|de|en)?\s*(?:la\s+|el\s+)?(?:voz\s+)?(?:de\s+)?(?P<voz>.+)$"
    r"|^(?:habla|hablame)\s+(?:con\s+)?(?:la\s+)?voz\s+(?:de\s+)?(?P<voz2>.+)$"
)


def detectar_comando_voz(texto: str) -> tuple[str, dict[str, object]] | None:
    """«cambia tu voz a la argentina», «usa la voz de Daniela», «pon la voz sharvard mujer»."""
    import jarvis_voices

    match = _CAMBIA_VOZ.fullmatch(_normalizar_consulta(texto))
    if match is None:
        return None
    voz = jarvis_voices.buscar(match.group("voz") or match.group("voz2") or "")
    return ("set_voice", {"voice": voz.clave}) if voz is not None else None


_UNDERVOLT = r"(?:el\s+|mi\s+)?(?:undervolt|under\s+volt|perfil\s+de\s+la\s+(?:gpu|grafica|placa(?:\s+de\s+video)?))"
_APLICA_UNDERVOLT = re.compile(
    rf"^(?:aplica|aplicar|aplicame|pon|poner|carga|cargar|activa|activar|reaplica|reaplicar|restaura|restaurar)\s+"
    rf"{_UNDERVOLT}(?:\s+(?:de|a|en)\s+la\s+(?:gpu|grafica|placa(?:\s+de\s+video)?))?$"
)
_ESTADO_UNDERVOLT = re.compile(
    rf"^(?:(?:esta|sigue)\s+(?:aplicado|puesto|activo|cargado)\s+{_UNDERVOLT}"
    rf"|(?:como\s+esta|estado\s+de(?:l)?|revisa|revisar|comprueba|comprobar|verifica|verificar)\s+{_UNDERVOLT}"
    rf"|{_UNDERVOLT}\s+(?:esta|sigue)\s+(?:aplicado|puesto|activo|cargado))$"
)


def detectar_comando_gpu(texto: str) -> tuple[str, dict[str, object]] | None:
    """«aplica el undervolt» / «¿está aplicado el undervolt?»."""
    consulta = _normalizar_consulta(texto)
    if _APLICA_UNDERVOLT.fullmatch(consulta):
        return "apply_gpu_profile", {}
    if _ESTADO_UNDERVOLT.fullmatch(consulta):
        return "get_gpu_profile_status", {}
    return None
