"""Diagnóstico de Jarvis (roadmap punto 4): servicios, rendimiento y errores.

Solo lectura y con tiempos de espera cortos, para poder ejecutarse en
cualquier momento sin bloquear nada. No incluye contenido de conversaciones.
"""

import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import requests

import jarvis_core as core
from jarvis_logging import ruta_registro

TIMEOUT_COMPROBACION = 3.0
STT_PORT = 9000
_ETAPAS = {"stt": "Voz a texto", "llm": "Ollama", "tts": "Texto a voz", "total": "Respuesta completa"}


@dataclass(frozen=True, slots=True)
class Comprobacion:
    nombre: str
    ok: bool
    detalle: str


def _ollama_base() -> str:
    return core.LLM_URL.split("/api/", 1)[0]


def comprobar_ollama(timeout: float = TIMEOUT_COMPROBACION) -> Comprobacion:
    base = _ollama_base()
    try:
        tags = requests.get(f"{base}/api/tags", timeout=timeout)
        tags.raise_for_status()
        modelos = {m.get("name", "") for m in tags.json().get("models", [])}
    except (requests.RequestException, ValueError, AttributeError):
        return Comprobacion(
            "Ollama", False, "no responde; abre la aplicación de Ollama."
        )
    if core.MODEL_LLM not in modelos:
        return Comprobacion(
            "Ollama", False, f"responde, pero no tiene descargado el modelo {core.MODEL_LLM}."
        )
    try:
        cargados = requests.get(f"{base}/api/ps", timeout=timeout).json().get("models", [])
        en_memoria = any(m.get("name") == core.MODEL_LLM for m in cargados)
    except (requests.RequestException, ValueError, AttributeError):
        en_memoria = False
    estado = "cargado en memoria" if en_memoria else "se cargará en la próxima consulta"
    return Comprobacion("Ollama", True, f"funciona; {core.MODEL_LLM} {estado}.")


def comprobar_puerto(nombre: str, host: str, puerto: int, timeout: float = 1.5) -> Comprobacion:
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            pass
    except OSError:
        return Comprobacion(nombre, False, f"no responde en el puerto {puerto} (¿Docker iniciado?).")
    return Comprobacion(nombre, True, f"responde en el puerto {puerto}.")


_proveedores: list[Callable[[], list[Comprobacion]]] = []


def registrar_proveedor(proveedor: Callable[[], list[Comprobacion]]) -> None:
    """Añade comprobaciones extra al diagnóstico (p. ej. plugins y servidores MCP)."""
    if proveedor not in _proveedores:
        _proveedores.append(proveedor)


def comprobar_servicios() -> list[Comprobacion]:
    checks = [
        comprobar_ollama(),
        comprobar_puerto("Voz a texto (STT)", "127.0.0.1", STT_PORT),
        comprobar_puerto("Texto a voz (TTS)", core.TTS_HOST, core.TTS_PORT),
    ]
    for proveedor in list(_proveedores):
        try:
            checks.extend(proveedor())
        except Exception as error:
            checks.append(Comprobacion("Integraciones", False, f"no se pudo comprobar: {error}"))
    return checks


def fallos_recientes() -> dict[str, int]:
    """Acciones no exitosas de las últimas 24 horas, por estado."""
    with core.conexion_db() as connection:
        rows = connection.execute(
            "SELECT estado, COUNT(*) FROM acciones WHERE estado <> 'exito' "
            "AND creado >= strftime('%Y-%m-%d %H:%M:%f', 'now', '-1 day') GROUP BY estado"
        ).fetchall()
    return {str(estado): int(cantidad) for estado, cantidad in rows}


def formatear_diagnostico(
    servicios: Sequence[Comprobacion],
    latencias: dict[str, tuple[int, float, float, int]],
    fallos: dict[str, int],
    tamano_bd: int | None,
    registro: str | None,
) -> str:
    lineas = ["Diagnóstico de Jarvis:", "Servicios:"]
    for servicio in servicios:
        lineas.append(f"- {'✓' if servicio.ok else '✗'} {servicio.nombre}: {servicio.detalle}")
    lineas.append("Rendimiento (últimas mediciones desde que se abrió Jarvis):")
    if latencias:
        for etapa, nombre in _ETAPAS.items():
            if etapa in latencias:
                cantidad, media, maximo, errores = latencias[etapa]
                texto = f"- {nombre}: media {media:.1f} s, máximo {maximo:.1f} s ({cantidad} mediciones"
                texto += f", {errores} con error)" if errores else ")"
                lineas.append(texto)
    else:
        lineas.append("- Todavía no hay mediciones.")
    if fallos:
        detalle = ", ".join(f"{cantidad} {estado.replace('_', ' ')}" for estado, cantidad in fallos.items())
        lineas.append(f"Acciones con problemas en las últimas 24 h: {detalle}.")
    else:
        lineas.append("Acciones con problemas en las últimas 24 h: ninguna.")
    if tamano_bd is not None:
        lineas.append(f"Base de datos: {tamano_bd / 1024:.0f} KB.")
    if registro:
        lineas.append(f"Registro de errores: {registro}")
    return "\n".join(lineas)


def consultar_diagnostico() -> str:
    try:
        tamano = core.DB_PATH.stat().st_size
    except OSError:
        tamano = None
    try:
        fallos = fallos_recientes()
    except Exception:
        fallos = {}
    registro = ruta_registro()
    return formatear_diagnostico(
        comprobar_servicios(),
        core.resumen_latencias(),
        fallos,
        tamano,
        str(registro) if registro else None,
    )
