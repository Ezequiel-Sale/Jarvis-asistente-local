"""Acciones al abrir un juego: pasos como los de las rutinas que se hacen después de lanzarlo.

Para emuladores y juegos que piden un paso más (pulsar «Jugar» en el launcher, esperar a que
cargue, acomodar la ventana…). Se editan en la página 🎮 JUEGOS (botón «⚙ ACCIONES», con el mismo
editor de acciones que las rutinas) y se guardan en la preferencia ``acciones_juegos``:
{nombre del juego en minúsculas: [{"tool", "arguments"}…]}.

Las acciones permitidas son las de las rutinas salvo abrir otro juego (podría encadenarse sin fin)
y la energía (apagar o reiniciar al abrir un juego no tiene sentido y pediría confirmación).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import jarvis_routines

PREFERENCIA = "acciones_juegos"
NO_PERMITIDAS = frozenset({"launch_game", "power_control", "cancel_power_action"})


def _clave(nombre: str) -> str:
    return " ".join(nombre.casefold().split())


def cargar(obtener: Callable[[str], str | None]) -> dict[str, list[dict]]:
    try:
        datos = json.loads(obtener(PREFERENCIA) or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(datos, dict):
        return {}
    return {str(k): v for k, v in datos.items() if isinstance(v, list)}


def pasos_de(obtener: Callable[[str], str | None], juego: str) -> list[dict]:
    return list(cargar(obtener).get(_clave(juego), []))


def herramientas_permitidas() -> frozenset[str]:
    return frozenset(jarvis_routines._ROUTINE_ALLOWED_TOOLS) - NO_PERMITIDAS


def validar(pasos: list[dict], registry) -> list[dict]:
    """Los pasos con sus argumentos ya validados; ValueError con un mensaje claro si algo no va."""
    if len(pasos) > jarvis_routines.MAX_PASOS:
        raise ValueError(f"Como máximo {jarvis_routines.MAX_PASOS} acciones.")
    validados = []
    for paso in pasos:
        herramienta = str(paso.get("tool", ""))
        if herramienta not in herramientas_permitidas():
            raise ValueError(f"«{herramienta}» no se puede usar al abrir un juego.")
        argumentos = registry.validate_arguments(herramienta, dict(paso.get("arguments") or {}))
        validados.append({"tool": herramienta, "arguments": argumentos})
    return validados


def guardar(obtener: Callable[[str], str | None], guardar_pref: Callable[[str, str], None],
            juego: str, pasos: list[dict]) -> None:
    """Guarda (o borra, con ``pasos`` vacío) las acciones de ``juego``."""
    datos = cargar(obtener)
    if pasos:
        datos[_clave(juego)] = pasos
    else:
        datos.pop(_clave(juego), None)
    guardar_pref(PREFERENCIA, json.dumps(datos, ensure_ascii=False))


def ejecutar(pasos: list[dict], hacer: Callable[[str, dict], object],
             nombre_de: Callable[[str], str] = str) -> tuple[list[str], str | None]:
    """Hace los pasos en orden; corta en el primero que falla. Devuelve (resultados, error)."""
    resultados: list[str] = []
    for paso in pasos:
        try:
            resultados.append(str(hacer(paso["tool"], dict(paso.get("arguments") or {}))))
        except Exception as error:
            return resultados, f"«{nombre_de(paso['tool'])}» falló: {error}"
    return resultados, None
