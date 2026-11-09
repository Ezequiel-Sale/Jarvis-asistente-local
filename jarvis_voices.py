"""Voces de Piper en español para Jarvis (elegibles en Ajustes o por voz).

Los modelos (``.onnx`` + ``.onnx.json``, de rhasspy/piper-voices en Hugging Face)
van en ``piper-voices/``, que el contenedor Wyoming Piper monta como /data y
carga en cada petición según ``voice.name`` (y ``speaker`` en las de varias personas).
"""

import unicodedata
from dataclasses import dataclass
from pathlib import Path

import jarvis_core as core

PREFERENCIA = "voz"
FRASE_PRUEBA = "Hola, soy Jarvis. ¿En qué te ayudo hoy?"


@dataclass(frozen=True)
class Voz:
    clave: str
    nombre: str
    modelo: str
    hablante: str | None
    descripcion: str
    alias: tuple[str, ...] = ()


VOCES = (
    Voz("davefx", "Davefx", "es_ES-davefx-medium", None, "España · masculina · la original",
        ("davefx", "original", "espanola", "espana", "por defecto")),
    Voz("sharvard_m", "Sharvard (hombre)", "es_ES-sharvard-medium", "M", "España · masculina",
        ("sharvard", "sharvard hombre")),
    Voz("sharvard_f", "Sharvard (mujer)", "es_ES-sharvard-medium", "F", "España · femenina",
        ("sharvard mujer", "femenina espanola")),
    Voz("ald", "Ald", "es_MX-ald-medium", None, "México · masculina", ("ald", "mexicana", "mexico")),
    Voz("claude", "Claude", "es_MX-claude-high", None, "México · alta calidad", ("claude",)),
    Voz("daniela", "Daniela", "es_AR-daniela-high", None, "Argentina · femenina · alta calidad",
        ("daniela", "argentina", "femenina")),
)
POR_DEFECTO = "davefx"


def por_clave(clave: str | None) -> Voz:
    return next((v for v in VOCES if v.clave == clave), next(v for v in VOCES if v.clave == POR_DEFECTO))


def carpeta() -> Path:
    return core.APP_DIR / "piper-voices"


def instalada(voz: Voz, base: Path | None = None) -> bool:
    base = base or carpeta()
    return (base / f"{voz.modelo}.onnx").is_file() and (base / f"{voz.modelo}.onnx.json").is_file()


def disponibles(base: Path | None = None) -> list[Voz]:
    return [v for v in VOCES if instalada(v, base)]


def aplicar(voz: Voz) -> None:
    """A partir de ahora Jarvis habla con esta voz."""
    core.VOZ_PIPER = voz.modelo
    core.VOZ_HABLANTE = voz.hablante


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.lower())
    return " ".join("".join(c for c in texto if not unicodedata.combining(c) and (c.isalnum() or c == " ")).split())


def buscar(texto: str) -> Voz | None:
    """«la argentina», «Daniela», «sharvard mujer» → Voz (el alias más largo gana)."""
    consulta = _normalizar(texto)
    mejor: tuple[int, Voz] | None = None
    for voz in VOCES:
        for alias in (voz.clave.replace("_", " "), _normalizar(voz.nombre), *voz.alias):
            if alias and (consulta == alias or f" {alias} " in f" {consulta} "):
                if mejor is None or len(alias) > mejor[0]:
                    mejor = (len(alias), voz)
    return mejor[1] if mejor else None
