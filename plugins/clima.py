"""Plugin de clima con Open-Meteo (gratuito, sin cuenta ni clave).

Frases directas: «¿qué tiempo hace?», «clima en Córdoba», «¿va a llover
mañana?», «¿qué temperatura hace?». Ciudad por defecto: «mi ciudad es …».
Privacidad: solo se envía a Open-Meteo el nombre de la ciudad consultada.
"""

import json
import re
import unicodedata

import requests

NOMBRE = "clima"
DESCRIPCION = "Clima actual y pronóstico de hoy y mañana (Open-Meteo)."
PREFERENCIA_CIUDAD = "ciudad_clima"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_HTTP = 8

_CODIGOS_WMO = {
    0: "despejado",
    1: "mayormente despejado",
    2: "parcialmente nublado",
    3: "nublado",
    45: "niebla",
    48: "niebla con escarcha",
    51: "llovizna débil",
    53: "llovizna",
    55: "llovizna intensa",
    56: "llovizna helada",
    57: "llovizna helada intensa",
    61: "lluvia débil",
    63: "lluvia",
    65: "lluvia intensa",
    66: "lluvia helada",
    67: "lluvia helada intensa",
    71: "nevada débil",
    73: "nevada",
    75: "nevada intensa",
    77: "granizo fino",
    80: "chubascos débiles",
    81: "chubascos",
    82: "chubascos fuertes",
    85: "chubascos de nieve",
    86: "chubascos de nieve fuertes",
    95: "tormenta",
    96: "tormenta con granizo",
    99: "tormenta fuerte con granizo",
}

_preferencias: dict[str, object] = {}


def _normalizar(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto.casefold())
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    limpio = re.sub(r"[^a-z0-9 ]+", " ", sin_tildes)
    limpio = re.sub(r"\s+", " ", limpio).strip()
    return re.sub(r"^(?:jarvis|oye jarvis|por favor)\s+", "", limpio)


_DIA = r"(?:\s+(?:de\s+|para\s+)?(?P<dia>hoy|manana|ahora))?"
_CIUDAD = r"(?:\s+(?:en|de|para)\s+(?P<ciudad>[a-z0-9 ]+?))?"
_DIA2 = r"(?:\s+(?P<dia2>hoy|manana|ahora))?"
_PATRONES_CLIMA = (
    re.compile(
        r"^(?:que|como)\s+(?:tiempo|clima)\s+(?:hace|hara|va a hacer|tenemos|esta|hay)"
        + _DIA + _CIUDAD + _DIA2 + r"$"
    ),
    re.compile(r"^(?:el\s+|como\s+esta\s+el\s+)?(?:clima|tiempo|pronostico)" + _DIA + _CIUDAD + _DIA2 + r"$"),
    re.compile(r"^(?:va|vaya)\s+a\s+llover" + _DIA + _CIUDAD + _DIA2 + r"$"),
    re.compile(r"^(?:llueve|llovera)" + _DIA + _CIUDAD + _DIA2 + r"$"),
    re.compile(r"^(?:cuanta|que)\s+temperatura\s+(?:hace|hay|hara)" + _DIA + _CIUDAD + _DIA2 + r"$"),
)
_PATRON_CIUDAD = re.compile(
    r"^(?:mi\s+ciudad\s+es|vivo\s+en|"
    r"(?:configura|cambia|establece|pon)\s+(?:mi\s+|la\s+)?ciudad\s+(?:del\s+clima\s+)?"
    r"(?:en|a|como)?)\s*(?P<ciudad>[a-z0-9 ]+)$"
)


def detectar_comando(texto: str) -> tuple[str, dict[str, object]] | None:
    consulta = _normalizar(texto)
    for patron in _PATRONES_CLIMA:
        match = patron.fullmatch(consulta)
        if match is None:
            continue
        argumentos: dict[str, object] = {}
        dia = match.group("dia") or match.group("dia2")
        if dia == "manana":
            argumentos["day"] = "tomorrow"
        ciudad = (match.group("ciudad") or "").strip()
        if ciudad:
            argumentos["city"] = ciudad
        return "get_weather", argumentos
    return None


_PALABRAS_DE_UNION = {"de", "del", "en", "la", "el", "provincia", "departamento", "estado"}
_PREFIJOS_REGION = re.compile(r"^(?:provincia|departamento|estado|region|municipio)\s+de\s+", re.I)
MAX_PALABRAS_NOMBRE = 5


def _geocodificar(nombre: str) -> list[dict]:
    respuesta = requests.get(
        GEOCODING_URL,
        params={"name": nombre, "count": 10, "language": "es"},
        timeout=TIMEOUT_HTTP,
    )
    respuesta.raise_for_status()
    return respuesta.json().get("results") or []


def _texto_region(lugar: dict) -> str:
    claves = ("admin1", "admin2", "admin3", "admin4", "country")
    return _normalizar(" ".join(str(lugar[c]) for c in claves if lugar.get(c)))


def buscar_ciudad_con_alternativas(texto: str, pais_preferido: str | None = None) -> tuple[dict, list[dict]]:
    """Busca «ciudad [provincia] [país]» y devuelve (elegida, otras candidatas).

    Open-Meteo solo busca por nombre, así que se prueba con las primeras
    palabras como nombre y el resto como pistas que deben coincidir con la
    provincia/país del resultado.
    """
    palabras = _normalizar(texto).split()
    if not palabras:
        raise ValueError("Indica el nombre de una ciudad.")
    for cantidad in range(min(len(palabras), MAX_PALABRAS_NOMBRE), 0, -1):
        nombre = " ".join(palabras[:cantidad])
        pistas = [p for p in palabras[cantidad:] if p not in _PALABRAS_DE_UNION]
        candidatos = [
            lugar
            for lugar in _geocodificar(nombre)
            if all(re.search(rf"\b{re.escape(p)}", _texto_region(lugar)) for p in pistas)
        ]
        if not candidatos:
            continue
        if not pistas and pais_preferido:
            preferidos = [c for c in candidatos if c.get("country") == pais_preferido]
            if preferidos:
                candidatos = preferidos + [c for c in candidatos if c not in preferidos]
        return candidatos[0], candidatos[1:]
    raise ValueError(
        f"No encontré la ciudad «{texto}». Prueba con «ciudad, provincia, país», "
        "por ejemplo «Santa Rosa, La Pampa, Argentina»."
    )


def buscar_ciudad(texto: str, pais_preferido: str | None = None) -> dict:
    return buscar_ciudad_con_alternativas(texto, pais_preferido)[0]


def _nombre_lugar(lugar: dict) -> str:
    partes = [lugar.get("name", "")]
    region = _PREFIJOS_REGION.sub("", str(lugar.get("admin1") or ""))
    if region and region != lugar.get("name"):
        partes.append(region)
    if lugar.get("country"):
        partes.append(lugar["country"])
    return ", ".join(p for p in partes if p)


def _lugar_guardado(valor: str | None) -> dict | None:
    """La ciudad guardada es un JSON con coordenadas; versiones previas guardaban solo el nombre."""
    if not valor:
        return None
    try:
        lugar = json.loads(valor)
    except json.JSONDecodeError:
        return {"legacy_name": valor}
    if isinstance(lugar, dict) and {"latitude", "longitude"} <= lugar.keys():
        return lugar
    return None


def _temp(valor: float, unidad: str) -> str:
    return f"{valor:.0f} {unidad}"


def formatear_clima(lugar: dict, datos: dict, dia: str, unidad: str = "°C") -> str:
    diario = datos["daily"]
    indice = 1 if dia == "tomorrow" else 0
    descripcion = _CODIGOS_WMO.get(int(diario["weather_code"][indice]), "condiciones variables")
    minima = _temp(diario["temperature_2m_min"][indice], unidad)
    maxima = _temp(diario["temperature_2m_max"][indice], unidad)
    lluvia = diario.get("precipitation_probability_max", [None, None])[indice]
    lluvia_texto = f", probabilidad de lluvia {lluvia:.0f} %" if lluvia is not None else ""
    lugar_texto = _nombre_lugar(lugar)
    if dia == "tomorrow":
        return (
            f"Mañana en {lugar_texto}: {descripcion}, mínima {minima} y máxima {maxima}"
            f"{lluvia_texto}."
        )
    actual = datos["current"]
    ahora = _CODIGOS_WMO.get(int(actual["weather_code"]), "condiciones variables")
    return (
        f"Ahora en {lugar_texto}: {_temp(actual['temperature_2m'], unidad)} "
        f"(sensación {_temp(actual['apparent_temperature'], unidad)}), {ahora}, "
        f"humedad {actual['relative_humidity_2m']:.0f} %, viento "
        f"{actual['wind_speed_10m']:.0f} km/h. Hoy: mínima {minima} y máxima {maxima}"
        f"{lluvia_texto}."
    )


def consultar_clima(city: str = "", day: str = "today") -> str:
    obtener = _preferencias.get("obtener")
    guardado = _lugar_guardado(obtener(PREFERENCIA_CIUDAD) if callable(obtener) else None)
    if city.strip():
        pais = guardado.get("country") if guardado else None
        lugar = buscar_ciudad(city, pais_preferido=pais)
    elif guardado and "legacy_name" in guardado:
        lugar = buscar_ciudad(guardado["legacy_name"])
    elif guardado:
        lugar = guardado
    else:
        raise ValueError(
            "No sé en qué ciudad estás. Dime «mi ciudad es …» o pregunta «clima en …»."
        )
    unidad = (obtener("unidad_temperatura") if callable(obtener) else None) or "°C"
    fahrenheit = "F" in unidad.upper()
    respuesta = requests.get(
        FORECAST_URL,
        params={
            "latitude": lugar["latitude"],
            "longitude": lugar["longitude"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 2,
            "temperature_unit": "fahrenheit" if fahrenheit else "celsius",
        },
        timeout=TIMEOUT_HTTP,
    )
    respuesta.raise_for_status()
    texto = formatear_clima(lugar, respuesta.json(), day, "°F" if fahrenheit else "°C")
    if not city.strip() and day == "today":
        # El mismo dato que muestra el panel de inicio: al preguntar, el panel se actualiza también.
        try:
            import jarvis_dashboard

            jarvis_dashboard.guardar_clima(texto)
        except Exception:
            pass
    return texto


def procesar_comando(texto: str, contexto) -> str | None:
    match = _PATRON_CIUDAD.fullmatch(_normalizar(texto))
    if match is None:
        return None
    lugar, otras = buscar_ciudad_con_alternativas(match.group("ciudad").strip())
    nombre = _nombre_lugar(lugar)
    guardado = {
        clave: lugar[clave]
        for clave in ("name", "admin1", "country", "latitude", "longitude")
        if lugar.get(clave) is not None
    }
    contexto.guardar_preferencia(PREFERENCIA_CIUDAD, json.dumps(guardado, ensure_ascii=False))
    respuesta = f"Listo, usaré {nombre} como tu ciudad para el clima."
    if otras:
        alternativas = "; ".join(_nombre_lugar(o) for o in otras[:3])
        respuesta += (
            f" También existe: {alternativas}. Si no es la tuya, dime la provincia y el "
            "país, por ejemplo «mi ciudad es Santa Rosa, La Pampa, Argentina»."
        )
    return respuesta


def registrar(registry, contexto) -> None:
    _preferencias["obtener"] = contexto.obtener_preferencia
    registry.register(
        "get_weather",
        (
            "Solo lectura. Clima actual y pronóstico. city es opcional (si falta se usa "
            "la ciudad configurada por el usuario); day es today o tomorrow."
        ),
        {
            "type": "object",
            "properties": {
                "city": {"type": "string", "maxLength": 100},
                "day": {"type": "string", "enum": ["today", "tomorrow"]},
            },
            "required": [],
            "additionalProperties": False,
        },
        consultar_clima,
        label="Consultar el clima",
        timeout_seconds=20,
    )
