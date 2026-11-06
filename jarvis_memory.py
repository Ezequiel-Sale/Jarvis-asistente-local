import logging
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


def guardar_recuerdo(contenido: str, origen: str = "usuario") -> tuple[int, bool]:
    contenido = _validar_contenido(contenido)
    if origen not in {"usuario", "automatica"}:
        raise ValueError("Origen de recuerdo no válido.")
    with core.conexion_db() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO memorias (contenido, origen) VALUES (?, ?)",
            (contenido, origen),
        )
        inserted = cursor.rowcount == 1
        row = connection.execute(
            "SELECT id FROM memorias WHERE contenido = ? COLLATE NOCASE",
            (contenido,),
        ).fetchone()
    if row is None:
        raise RuntimeError("No se pudo guardar ni recuperar el recuerdo.")
    if inserted:
        _guardar_vectores([(int(row[0]), contenido)])
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
    if cursor.rowcount == 1:
        _guardar_vectores([(identifier, contenido)])
    return cursor.rowcount == 1


def listar_recuerdos(limite: int = 100, origen: str | None = None) -> list[tuple[int, str]]:
    if limite < 1:
        raise ValueError("El límite debe ser positivo.")
    with core.conexion_db() as connection:
        if origen is None:
            rows = connection.execute(
                "SELECT id, contenido FROM memorias ORDER BY id DESC LIMIT ?",
                (limite,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id, contenido FROM memorias WHERE origen = ? ORDER BY id DESC LIMIT ?",
                (origen, limite),
            ).fetchall()
    return [(int(identifier), str(content)) for identifier, content in rows]


def eliminar_recuerdo(identifier: int) -> bool:
    if identifier < 1:
        return False
    with core.conexion_db() as connection:
        cursor = connection.execute("DELETE FROM memorias WHERE id = ?", (identifier,))
    return cursor.rowcount == 1


# Similitud mínima consulta→recuerdo para considerarlo relevante (medido con
# embeddinggemma: relevantes ~0,5; sin relación < 0,27).
UMBRAL_RELEVANCIA = 0.35


def buscar_recuerdos(consulta: str, limite: int = 5) -> list[tuple[int, str]]:
    """Búsqueda mixta: por significado (embeddings) y por palabras en común.

    Sin el modelo de embeddings funciona como antes, solo por palabras.
    """
    if limite < 1:
        raise ValueError("El límite debe ser positivo.")
    query_tokens = _tokens(consulta)
    recuerdos = listar_recuerdos(limite=1000)
    similitudes = _similitudes(consulta, recuerdos, consulta_usuario=True)
    if not query_tokens and similitudes is None:
        return []

    ranked: list[tuple[float, int, str]] = []
    for identifier, content in recuerdos:
        overlap = len(query_tokens.intersection(_tokens(content)))
        similitud = similitudes.get(identifier, 0.0) if similitudes is not None else 0.0
        if overlap == 0 and similitud < UMBRAL_RELEVANCIA:
            continue
        ranked.append((similitud + 0.05 * overlap, identifier, content))
    ranked.sort(key=lambda result: (-result[0], -result[1]))
    return [(identifier, content) for _, identifier, content in ranked[:limite]]


def buscar_parecidos(texto: str, limite: int = 5, minimo: float = 0.5) -> list[tuple[int, str, float]]:
    """Recuerdos de significado parecido a un texto (para no duplicar al aprender)."""
    recuerdos = listar_recuerdos(limite=1000)
    similitudes = _similitudes(texto, recuerdos, consulta_usuario=False)
    if not similitudes:
        return []
    parecidos = [
        (identifier, content, similitudes[identifier])
        for identifier, content in recuerdos
        if similitudes.get(identifier, 0.0) >= minimo
    ]
    parecidos.sort(key=lambda r: -r[2])
    return parecidos[:limite]


def _guardar_vectores(recuerdos: list[tuple[int, str]]) -> int:
    """Calcula y guarda los vectores; si el modelo no responde, se hará luego."""
    import jarvis_embeddings

    if not recuerdos:
        return 0
    matriz = jarvis_embeddings.vectorizar([content for _, content in recuerdos])
    if matriz is None:
        return 0
    with core.conexion_db() as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO memorias_vectores (memoria_id, modelo, vector) VALUES (?, ?, ?)",
            [
                (identifier, jarvis_embeddings.MODELO, jarvis_embeddings.a_bytes(vector))
                for (identifier, _), vector in zip(recuerdos, matriz)
            ],
        )
    return len(recuerdos)


def sincronizar_vectores() -> int:
    """Vectoriza los recuerdos que no tienen vector (o lo tienen de otro modelo)."""
    import jarvis_embeddings

    with core.conexion_db() as connection:
        faltan = connection.execute(
            "SELECT m.id, m.contenido FROM memorias m LEFT JOIN memorias_vectores v "
            "ON v.memoria_id = m.id WHERE v.memoria_id IS NULL OR v.modelo != ?",
            (jarvis_embeddings.MODELO,),
        ).fetchall()
    total = 0
    for inicio in range(0, len(faltan), 64):
        lote = [(int(i), str(c)) for i, c in faltan[inicio : inicio + 64]]
        guardados = _guardar_vectores(lote)
        if guardados == 0:
            break
        total += guardados
    if total:
        logging.info("Se vectorizaron %s recuerdos.", total)
    return total


def _similitudes(
    texto: str, recuerdos: list[tuple[int, str]], *, consulta_usuario: bool
) -> dict[int, float] | None:
    import jarvis_embeddings

    if not recuerdos:
        return {}
    consulta = jarvis_embeddings.vectorizar([texto], consulta=consulta_usuario)
    if consulta is None:
        return None
    sincronizar_vectores()
    with core.conexion_db() as connection:
        filas = connection.execute(
            "SELECT memoria_id, vector FROM memorias_vectores WHERE modelo = ?",
            (jarvis_embeddings.MODELO,),
        ).fetchall()
    if not filas:
        return {}
    ids = [int(f[0]) for f in filas]
    matriz = jarvis_embeddings.np.vstack([jarvis_embeddings.de_bytes(f[1]) for f in filas])
    valores = matriz @ consulta[0]
    return {identifier: float(valor) for identifier, valor in zip(ids, valores)}


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
