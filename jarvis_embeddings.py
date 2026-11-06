"""Vectores de significado (embeddings) con Ollama, para buscar recuerdos por sentido.

Modelo: ``embeddinggemma`` (elegido el 2026-11-06: 14/15 aciertos con preguntas
en español dichas con otras palabras, frente a 4/15 de la búsqueda por
palabras; ~17 ms por consulta en la GPU). Usa prefijos distintos para consultas
y para documentos, como recomienda el modelo.

Los vectores se guardan normalizados (float32) en la tabla
``memorias_vectores``; con cientos o miles de recuerdos la similitud se calcula
en memoria con numpy en milisegundos. Si Ollama o el modelo no responden,
``vectorizar`` devuelve None y quien llama usa la búsqueda por palabras.
"""

import logging
import os
import sys
import time

import numpy as np
import requests

import jarvis_core as core

MODELO = "embeddinggemma"
PREFIJO_CONSULTA = "task: search result | query: "
PREFIJO_DOCUMENTO = "title: none | text: "
URL = core.LLM_URL.rsplit("/", 1)[0] + "/embed"
TIMEOUT = 15
ESPERA_TRAS_FALLO = 60.0

_no_disponible_hasta = 0.0


def vectorizar(textos: list[str], *, consulta: bool = False) -> np.ndarray | None:
    """Matriz (n, d) normalizada, o None si el modelo no está disponible."""
    global _no_disponible_hasta
    if _en_tests():
        return None
    if not textos:
        return np.zeros((0, 0), dtype=np.float32)
    if time.monotonic() < _no_disponible_hasta:
        return None
    prefijo = PREFIJO_CONSULTA if consulta else PREFIJO_DOCUMENTO
    try:
        respuesta = requests.post(
            URL,
            json={"model": MODELO, "input": [prefijo + t for t in textos], "keep_alive": "30m"},
            timeout=TIMEOUT,
        )
        respuesta.raise_for_status()
        matriz = np.asarray(respuesta.json()["embeddings"], dtype=np.float32)
    except (requests.RequestException, KeyError, ValueError) as error:
        _no_disponible_hasta = time.monotonic() + ESPERA_TRAS_FALLO
        logging.warning(
            "No se pudieron calcular embeddings con %s (%s); se usa la búsqueda por palabras.",
            MODELO,
            error,
        )
        return None
    normas = np.linalg.norm(matriz, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    return matriz / normas


def _en_tests() -> bool:
    """Los tests no deben depender de Ollama: con ``python -m unittest`` los
    embeddings están desactivados (los tests que los necesitan los simulan).
    No se mira ``sys.modules``: algunas librerías de la app importan unittest."""
    principal = sys.argv[0] if sys.argv else ""
    return "unittest" in principal and not os.environ.get("JARVIS_EMBEDDINGS_EN_TESTS")


def a_bytes(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def de_bytes(datos: bytes) -> np.ndarray:
    return np.frombuffer(datos, dtype=np.float32)
