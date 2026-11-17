"""Corrección del examen: compara las llamadas que pidió el modelo con las esperadas.

Sin red ni ventanas: solo datos. Lo usa ``correr_examen.py`` y lo prueba ``tests/test_examen.py``.

Un caso espera una lista de pasos ``[{"tool": ..., "args": {...}}]`` (en orden), o ``null`` si la
respuesta correcta es contestar con texto sin usar herramientas. ``alternativas`` suma otras listas
que también valen. En ``args`` solo se revisan las claves escritas:

- texto: lo esperado tiene que aparecer dentro de lo que puso el modelo (sin mayúsculas ni tildes);
  ``"a|b"`` acepta cualquiera de las dos;
- números y verdadero/falso: iguales;
- listas (``arrange_windows``): cada elemento esperado, en orden, con la misma regla;
- ``_pasos`` (``schedule_routine``): las herramientas que tiene que tener ``steps_json``, en orden.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any

# Veredictos, del mejor al peor (el orden importa para elegir la alternativa más cercana).
CORRECTO = "correcto"
ARGUMENTOS = "argumentos mal"
PASOS_DE_MAS = "pasos de más"
FALTAN_PASOS = "faltan pasos"
ORDEN = "orden equivocado"
HERRAMIENTA = "herramienta equivocada"
NO_ACTUO = "no actuó"
ILEGIBLE = "llamada escrita ilegible"  # intentó la herramienta, pero en un JSON que Jarvis no puede leer
ACTUO_SIN_PEDIDO = "actuó sin que se lo pidieran"
VEREDICTOS = (CORRECTO, ARGUMENTOS, PASOS_DE_MAS, FALTAN_PASOS, ORDEN, HERRAMIENTA, ILEGIBLE, NO_ACTUO,
              ACTUO_SIN_PEDIDO)


def _normal(texto: Any) -> str:
    texto = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn").strip()


def _argumentos(llamada: dict) -> dict:
    args = llamada.get("args")
    if args is None:
        args = (llamada.get("function") or {}).get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return {}
    return args if isinstance(args, dict) else {}


def nombre(llamada: dict) -> str:
    return str(llamada.get("tool") or (llamada.get("function") or {}).get("name") or "")


def _valor_coincide(esperado: Any, real: Any) -> bool:
    if isinstance(esperado, bool) or isinstance(real, bool):
        return esperado is real or (isinstance(real, str) and _normal(real) == _normal(esperado))
    if isinstance(esperado, (int, float)):
        try:
            return float(real) == float(esperado)
        except (TypeError, ValueError):
            return False
    if isinstance(esperado, list):
        if not isinstance(real, list) or len(real) < len(esperado):
            return False
        return all(_args_coinciden(e, r if isinstance(r, dict) else {}) for e, r in zip(esperado, real))
    if isinstance(esperado, dict):
        return isinstance(real, dict) and _args_coinciden(esperado, real)
    if real is None:
        return False
    return any(_normal(opcion) in _normal(real) for opcion in str(esperado).split("|"))


def _pasos_de_rutina(real: Any) -> list[str]:
    try:
        pasos = json.loads(real) if isinstance(real, str) else real
    except ValueError:
        return []
    if not isinstance(pasos, list):
        return []
    return [str(p.get("tool") or p.get("name") or "") for p in pasos if isinstance(p, dict)]


def _args_coinciden(esperados: dict, reales: dict) -> bool:
    for clave, valor in esperados.items():
        if clave == "_pasos":
            if _pasos_de_rutina(reales.get("steps_json")) != list(valor):
                return False
        elif not _valor_coincide(valor, reales.get(clave)):
            return False
    return True


def comparar(esperado: list[dict] | None, llamadas: list[dict]) -> tuple[str, str]:
    """(veredicto, detalle) para una sola lista esperada."""
    if esperado is None:
        if llamadas:
            return ACTUO_SIN_PEDIDO, "llamó " + ", ".join(nombre(c) for c in llamadas)
        return CORRECTO, ""
    if not llamadas:
        return NO_ACTUO, "respondió con texto"
    nombres_esperados = [p["tool"] for p in esperado]
    nombres_reales = [nombre(c) for c in llamadas]
    if nombres_reales == nombres_esperados:
        malos = [f"{p['tool']} {_argumentos(c)}" for p, c in zip(esperado, llamadas)
                 if not _args_coinciden(p.get("args", {}), _argumentos(c))]
        return (ARGUMENTOS, "; ".join(malos)) if malos else (CORRECTO, "")
    detalle = " → ".join(nombres_reales)
    if sorted(nombres_reales) == sorted(nombres_esperados):
        return ORDEN, detalle
    if len(nombres_reales) > len(nombres_esperados) and _contiene_en_orden(nombres_reales, nombres_esperados):
        return PASOS_DE_MAS, detalle
    if len(nombres_reales) < len(nombres_esperados) and _contiene_en_orden(nombres_esperados, nombres_reales):
        return FALTAN_PASOS, detalle
    return HERRAMIENTA, detalle


def _contiene_en_orden(largo: list[str], corto: list[str]) -> bool:
    restante = iter(largo)
    return all(any(x == y for y in restante) for x in corto)


def corregir(caso: dict, llamadas: list[dict]) -> tuple[str, str]:
    """El mejor veredicto entre ``esperado`` y sus ``alternativas``."""
    opciones = [caso.get("esperado")] + list(caso.get("alternativas", []))
    resultados = [comparar(op, llamadas) for op in opciones]
    return min(resultados, key=lambda r: VEREDICTOS.index(r[0]))
