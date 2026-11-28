"""«Mi música favorita» se resuelve al momento de buscar (2026-11-28, pedido del usuario).

Una rutina (o una orden) puede buscar en YouTube «mi música favorita»: al ejecutarse, ``resolver`` cambia
esas palabras por lo que diga tu recuerdo («recordá que mi música favorita es lo-fi para trabajar»); si no
hay recuerdo, por lo que más buscaste en YouTube con Jarvis (historial de acciones, últimos
``DIAS_HISTORIAL`` días). Si tampoco, avisa cómo decírselo. Así, si cambiás el recuerdo, la rutina cambia.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter

DIAS_HISTORIAL = 60
# Lo que se dice en vez del tema: «mi música favorita», «mis canciones favoritas», «lo que más escucho».
_FAVORITA = re.compile(
    r"\b(?:(?:mi|mis|la|las)\s+)?(?:musica|canciones?|temas?|playlist|lista)\s+(?:favorit[oa]s?|preferid[oa]s?)\b"
    r"|\blo\s+que\s+mas\s+(?:escucho|me\s+gusta)\b")
# En el recuerdo: «Mi música favorita es lo-fi para trabajar», «mis canciones favoritas son de Soda».
_EN_RECUERDO = re.compile(
    r"(?:musica|canciones?|temas?|artistas?|bandas?|cantantes?|generos?|playlist)\s+"
    r"(?:favorit[oa]s?|preferid[oa]s?)\s*(?:es|son|:|=|serian?|seria)\s+(?:de\s+)?(?P<valor>.+)$")


def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto).casefold())
    return "".join(c for c in texto if not unicodedata.combining(c))


def pide_favorita(consulta: str) -> bool:
    return _FAVORITA.search(_normal(consulta)) is not None


def de_los_recuerdos(recuerdos: list[str]) -> str | None:
    """El tema que dice el recuerdo más reciente que habla de tu música favorita."""
    for recuerdo in reversed(recuerdos):
        normal = _normal(recuerdo).strip().rstrip(".!")
        m = _EN_RECUERDO.search(normal)
        if m is not None:
            # El valor tal como lo escribiste (con tildes y mayúsculas): misma posición en el texto original.
            inicio = len(normal) - len(m.group("valor"))
            valor = recuerdo.strip().rstrip(".!")[inicio:].strip(" «»\"'")
            if valor:
                return valor
    return None


def mas_buscado(busquedas: list[str]) -> str | None:
    """Lo que más se buscó (sin contar las búsquedas de «mi música favorita»)."""
    contador = Counter(b.strip() for b in busquedas if b and b.strip() and not pide_favorita(b))
    if not contador:
        return None
    return contador.most_common(1)[0][0]


def _recuerdos_reales() -> list[str]:
    import jarvis_memory

    return [contenido for _id, contenido in jarvis_memory.buscar_recuerdos("música favorita canciones favoritas", 10)]


def _busquedas_reales() -> list[str]:
    import jarvis_core as core

    with core.conexion_db() as conexion:
        filas = conexion.execute(
            "SELECT argumentos FROM acciones WHERE herramienta = 'search_youtube' AND estado = 'exito' "
            "AND creado >= datetime('now', ?)", (f"-{DIAS_HISTORIAL} days",)).fetchall()
    busquedas = []
    for (argumentos,) in filas:
        try:
            consulta = json.loads(argumentos or "{}").get("query")
        except (TypeError, ValueError):
            continue
        if isinstance(consulta, str):
            busquedas.append(consulta)
    return busquedas


def resolver(consulta: str, recuerdos=None, busquedas=None) -> tuple[str, str]:
    """(consulta para YouTube, de dónde salió: «tus recuerdos» / «lo que más buscaste» / «»)."""
    if not pide_favorita(consulta):
        return consulta, ""
    tema = de_los_recuerdos(recuerdos if recuerdos is not None else _recuerdos_reales())
    origen = "tus recuerdos"
    if tema is None:
        tema = mas_buscado(busquedas if busquedas is not None else _busquedas_reales())
        origen = "lo que más buscaste"
    if tema is None:
        raise LookupError("No sé cuál es tu música favorita. Decime «recordá que mi música favorita es …» "
                          "(por ejemplo, lo-fi para trabajar) y la uso desde entonces.")
    return tema, origen
