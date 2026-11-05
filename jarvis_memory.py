import re
import unicodedata

import jarvis_core as core


_TOKEN_PATTERN = re.compile(r"[a-z0-9]{2,}")
_STOP_WORDS = {
    "ahora",
    "algo",
    "como",
    "con",
    "cual",
    "cuando",
    "de",
    "del",
    "donde",
    "el",
    "ella",
    "en",
    "es",
    "esta",
    "esto",
    "la",
    "las",
    "lo",
    "los",
    "me",
    "mi",
    "mis",
    "para",
    "por",
    "que",
    "recuerdo",
    "recuerdos",
    "sobre",
    "tu",
    "un",
    "una",
    "y",
}
_SAVE_PATTERNS = (
    re.compile(
        r"^(?:jarvis[,:\s]+)?recuerda(?:\s+que)?\s+(.+?)\s*[.!?]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:jarvis[,:\s]+)?recuerdo\s+que\s+"
        r"(?=(?:prefiero|uso|utilizo|me\s+gusta|mi\s+\w+)\b)"
        r"(.+?)\s*[.!?]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:jarvis[,:\s]+)?guarda\s+en\s+tu\s+memoria(?:\s+que)?\s+"
        r"(.+?)\s*[.!?]*$",
        re.IGNORECASE,
    ),
)
_RECOGNIZED_BRAVE_ALIAS = re.compile(r"\bbraiding\b", re.IGNORECASE)
_PREFERENCE_CONTEXT = re.compile(
    r"\b(?:prefiero|uso|utilizo|me\s+gusta|navegador)\b",
    re.IGNORECASE,
)
_LIST_PATTERN = re.compile(
    r"^¿?\s*(?:jarvis[,:\s]+)?(?:qué\s+recuerdas|que\s+recuerdas|mostrar\s+recuerdos|"
    r"listar\s+(?:mis\s+)?recuerdos|lista\s+(?:mis\s+)?recuerdos)\s*[?.!]*$",
    re.IGNORECASE,
)
_RECALL_PATTERN = re.compile(
    r"^¿?\s*(?:jarvis[,:\s]+)?(?:qué|que)\s+recuerdas\s+(?:sobre|de)\s+(.+?)\s*[?.!]*$",
    re.IGNORECASE,
)
_FORGET_PATTERN = re.compile(
    r"^(?:jarvis[,:\s]+)?olvida\s+(?:el\s+)?recuerdo\s+(\d+)\s*[.!]*$",
    re.IGNORECASE,
)
_UPDATE_PATTERN = re.compile(
    r"^(?:jarvis[,:\s]+)?actualiza\s+(?:el\s+)?recuerdo\s+(\d+)\s+"
    r"(?:a|por)\s+(.+)$",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_PATTERN.findall(_normalize(text))
        if token not in _STOP_WORDS
    }


def normalizar_transcripcion_memoria_voz(texto: str) -> str:
    if not re.match(
        r"^\s*(?:jarvis[,:\s]+)?recuerd[ao]\s+que\b",
        texto,
        re.IGNORECASE,
    ):
        return texto
    if not _PREFERENCE_CONTEXT.search(texto):
        return texto
    return _RECOGNIZED_BRAVE_ALIAS.sub("Brave", texto)


def _validar_contenido(contenido: str) -> str:
    contenido = " ".join(contenido.split())
    if len(contenido) < 3:
        raise ValueError("El recuerdo debe tener al menos 3 caracteres.")
    if len(contenido) > 500:
        raise ValueError("El recuerdo no puede superar los 500 caracteres.")
    return contenido


def guardar_recuerdo(contenido: str) -> tuple[int, bool]:
    contenido = _validar_contenido(contenido)
    with core.conexion_db() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO memorias (contenido) VALUES (?)",
            (contenido,),
        )
        inserted = cursor.rowcount == 1
        row = connection.execute(
            "SELECT id FROM memorias WHERE contenido = ? COLLATE NOCASE",
            (contenido,),
        ).fetchone()
    if row is None:
        raise RuntimeError("No se pudo guardar ni recuperar el recuerdo.")
    return int(row[0]), inserted


def actualizar_recuerdo(identifier: int, contenido: str) -> bool:
    if identifier < 1:
        return False
    contenido = _validar_contenido(contenido)
    with core.conexion_db() as connection:
        existing = connection.execute(
            "SELECT id FROM memorias WHERE contenido = ? COLLATE NOCASE",
            (contenido,),
        ).fetchone()
        if existing is not None and int(existing[0]) != identifier:
            raise ValueError("Ya existe otro recuerdo con ese contenido.")
        cursor = connection.execute(
            "UPDATE memorias SET contenido = ? WHERE id = ?",
            (contenido, identifier),
        )
    return cursor.rowcount == 1


def listar_recuerdos(limite: int = 100) -> list[tuple[int, str]]:
    if limite < 1:
        raise ValueError("El límite debe ser positivo.")
    with core.conexion_db() as connection:
        rows = connection.execute(
            "SELECT id, contenido FROM memorias ORDER BY id DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return [(int(identifier), str(content)) for identifier, content in rows]


def eliminar_recuerdo(identifier: int) -> bool:
    if identifier < 1:
        return False
    with core.conexion_db() as connection:
        cursor = connection.execute("DELETE FROM memorias WHERE id = ?", (identifier,))
    return cursor.rowcount == 1


def buscar_recuerdos(consulta: str, limite: int = 5) -> list[tuple[int, str]]:
    if limite < 1:
        raise ValueError("El límite debe ser positivo.")
    query_tokens = _tokens(consulta)
    if not query_tokens:
        return []

    recuerdos = listar_recuerdos(limite=1000)
    ranked: list[tuple[int, int, str]] = []
    for identifier, content in recuerdos:
        overlap = query_tokens.intersection(_tokens(content))
        if overlap:
            ranked.append((len(overlap), identifier, content))
    ranked.sort(key=lambda result: (-result[0], -result[1]))
    return [(identifier, content) for _, identifier, content in ranked[:limite]]


def _format_recuerdos(recuerdos: list[tuple[int, str]]) -> str:
    if not recuerdos:
        return "Todavía no guardé recuerdos."
    return "Estos son tus recuerdos guardados:\n" + "\n".join(
        f"{identifier}. {content}" for identifier, content in recuerdos
    )


def procesar_comando(texto: str) -> str | None:
    command = texto.strip()
    for pattern in _SAVE_PATTERNS:
        match = pattern.fullmatch(command)
        if match is not None:
            contenido = match.group(1).strip(" \t.,!?¿¡")
            identifier, inserted = guardar_recuerdo(contenido)
            if not inserted:
                return f"Ya tenía guardado ese recuerdo (número {identifier})."
            return f"Lo recordaré. Guardé el recuerdo número {identifier}."

    match = _FORGET_PATTERN.fullmatch(command)
    if match is not None:
        identifier = int(match.group(1))
        if eliminar_recuerdo(identifier):
            return f"Olvidé el recuerdo número {identifier}."
        return f"No encontré un recuerdo con el número {identifier}."

    match = _UPDATE_PATTERN.fullmatch(command)
    if match is not None:
        identifier = int(match.group(1))
        if actualizar_recuerdo(identifier, match.group(2)):
            return f"Actualicé el recuerdo número {identifier}."
        return f"No encontré un recuerdo con el número {identifier}."

    match = _RECALL_PATTERN.fullmatch(command)
    if match is not None:
        return _format_recuerdos(buscar_recuerdos(match.group(1)))

    if _LIST_PATTERN.fullmatch(command) is not None:
        return _format_recuerdos(listar_recuerdos())
    return None
