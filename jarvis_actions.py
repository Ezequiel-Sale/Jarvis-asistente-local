"""Ejecución supervisada de acciones.

Aporta límites de tiempo, cancelación cooperativa, verificación posterior y
registro (log, métricas Prometheus y tabla SQLite ``acciones``) de cada
herramienta que ejecuta Jarvis.
"""

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import TypeVar

from prometheus_client import Counter, Histogram

import jarvis_core as core


MAX_ACCIONES_GUARDADAS = 500
MAX_TEXTO_GUARDADO = 300
MAX_ARGUMENTOS_GUARDADOS = 1000
TIEMPO_MAXIMO_VERIFICACION = 10.0

ACTION_COUNTER = Counter(
    "jarvis_tool_executions_total",
    "Ejecuciones de herramientas por resultado",
    ["herramienta", "estado", "origen"],
)
ACTION_DURATION = Histogram(
    "jarvis_tool_duration_seconds",
    "Duración de la ejecución de cada herramienta",
    ["herramienta"],
)

T = TypeVar("T")


class OperacionCancelada(Exception):
    """El usuario canceló la operación en curso."""


class TiempoAgotado(Exception):
    """Una operación superó su tiempo máximo de espera."""


class CancellationToken:
    """Bandera de cancelación compartida entre la interfaz y los hilos de trabajo."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self, mensaje: str = "La operación fue cancelada.") -> None:
        if self._event.is_set():
            raise OperacionCancelada(mensaje)


def ejecutar_con_limites(
    func: Callable[[], T],
    *,
    timeout: float,
    descripcion: str,
    cancel_token: CancellationToken | None = None,
) -> T:
    """Ejecuta ``func`` en un hilo y espera hasta ``timeout`` segundos.

    Python no puede interrumpir un hilo: si se agota el tiempo o se cancela,
    se deja de esperar y el hilo termina por su cuenta en segundo plano.
    """
    if timeout <= 0:
        raise ValueError("El tiempo máximo debe ser positivo.")
    if cancel_token is not None:
        cancel_token.raise_if_cancelled(f"Se canceló «{descripcion}» antes de empezar.")

    finished = threading.Event()
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            outcome["value"] = func()
        except BaseException as error:  # se relanza en el hilo que espera
            outcome["error"] = error
        finally:
            finished.set()

    threading.Thread(target=target, name=f"JarvisAccion-{descripcion}", daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if finished.wait(min(0.1, max(remaining, 0.0))):
            break
        if cancel_token is not None and cancel_token.cancelled:
            raise OperacionCancelada(
                f"Se canceló «{descripcion}» mientras se ejecutaba; "
                "podría completarse igualmente."
            )
        if time.monotonic() >= deadline:
            raise TiempoAgotado(f"«{descripcion}» superó el tiempo máximo de {timeout:g} s.")

    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome["value"]  # type: ignore[return-value]


class EstadoAccion(StrEnum):
    EXITO = "exito"
    ERROR = "error"
    TIEMPO_AGOTADO = "tiempo_agotado"
    CANCELADA = "cancelada"
    NO_VERIFICADA = "no_verificada"


_ESTADO_TEXTO = {
    EstadoAccion.EXITO: "completada",
    EstadoAccion.ERROR: "falló",
    EstadoAccion.TIEMPO_AGOTADO: "superó el tiempo máximo",
    EstadoAccion.CANCELADA: "se canceló",
    EstadoAccion.NO_VERIFICADA: "no superó la verificación",
}


@dataclass(frozen=True, slots=True)
class Verificacion:
    """Resultado de comprobar el efecto de una acción.

    ``ok`` es True (comprobado), False (la comprobación falló) o None (no se
    pudo comprobar).
    """

    ok: bool | None
    detalle: str


Verificador = Callable[[Mapping[str, object], object], Verificacion]


@dataclass(frozen=True, slots=True)
class ResultadoAccion:
    herramienta: str
    etiqueta: str
    origen: str
    estado: EstadoAccion
    duracion: float
    argumentos: dict[str, object] = field(default_factory=dict)
    resultado: object = None
    error: str | None = None
    verificacion: Verificacion | None = None

    @property
    def exitosa(self) -> bool:
        return self.estado is EstadoAccion.EXITO

    def resumen(self) -> str:
        texto = f"«{self.etiqueta}» {_ESTADO_TEXTO[self.estado]}"
        if self.exitosa:
            texto += f" en {self.duracion:.1f} s"
            if self.verificacion is not None and self.verificacion.ok:
                texto += " y verificada"
            return texto + "."
        detalle = self.error or (self.verificacion.detalle if self.verificacion else "")
        return f"{texto}: {detalle}" if detalle else f"{texto}."


def _recortar(valor: object, limite: int = MAX_TEXTO_GUARDADO) -> str | None:
    if valor is None:
        return None
    texto = " ".join(str(valor).split())
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def guardar_accion(resultado: ResultadoAccion) -> None:
    argumentos = json.dumps(resultado.argumentos, ensure_ascii=False, default=str)
    verificacion = None
    if resultado.verificacion is not None:
        estado = {True: "ok", False: "fallo", None: "sin_comprobar"}[resultado.verificacion.ok]
        verificacion = _recortar(f"{estado}: {resultado.verificacion.detalle}")
    with core.conexion_db() as connection:
        connection.execute(
            "INSERT INTO acciones (herramienta, etiqueta, origen, estado, argumentos, "
            "resultado, error, verificacion, duracion_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                resultado.herramienta,
                resultado.etiqueta,
                resultado.origen,
                resultado.estado.value,
                _recortar(argumentos, MAX_ARGUMENTOS_GUARDADOS),
                _recortar(resultado.resultado),
                _recortar(resultado.error),
                verificacion,
                round(resultado.duracion * 1000),
            ),
        )
        connection.execute(
            "DELETE FROM acciones WHERE id <= (SELECT MAX(id) FROM acciones) - ?",
            (MAX_ACCIONES_GUARDADAS,),
        )


def registrar_accion(resultado: ResultadoAccion) -> None:
    """Deja constancia de una acción en el log, las métricas y la base de datos."""
    log = logging.info if resultado.exitosa else logging.warning
    log(
        "Acción %s [%s] %s en %.2fs%s",
        resultado.herramienta,
        resultado.origen,
        resultado.estado.value,
        resultado.duracion,
        f" | {resultado.error}" if resultado.error else "",
    )
    ACTION_COUNTER.labels(
        herramienta=resultado.herramienta,
        estado=resultado.estado.value,
        origen=resultado.origen,
    ).inc()
    ACTION_DURATION.labels(herramienta=resultado.herramienta).observe(resultado.duracion)
    guardar_accion(resultado)


def listar_acciones(limite: int = 10) -> list[dict[str, object]]:
    if limite < 1:
        raise ValueError("El límite debe ser positivo.")
    with core.conexion_db() as connection:
        rows = connection.execute(
            "SELECT id, herramienta, etiqueta, origen, estado, error, verificacion, "
            "duracion_ms, creado FROM acciones ORDER BY id DESC LIMIT ?",
            (limite,),
        ).fetchall()
    keys = (
        "id", "herramienta", "etiqueta", "origen", "estado",
        "error", "verificacion", "duracion_ms", "creado",
    )
    return [dict(zip(keys, row)) for row in rows]


_HISTORIAL_PATTERN = re.compile(
    r"^¿?\s*(?:jarvis[,:\s]+)?¿?\s*(?:"
    r"(?:qu[eé]|cu[aá]les)\s+(?:son\s+las\s+)?(?:[uú]ltimas\s+)?acciones\s+"
    r"(?:hiciste|ejecutaste|realizaste|has\s+hecho|has\s+ejecutado)"
    r"|(?:muestra|mostrar|lista|listar)\s+(?:el\s+)?(?:historial\s+de\s+)?"
    r"(?:las\s+)?(?:[uú]ltimas\s+)?acciones"
    r"|historial\s+de\s+acciones"
    r")\s*[?.!]*$",
    re.IGNORECASE,
)
_ULTIMA_PATTERN = re.compile(
    r"^¿?\s*(?:jarvis[,:\s]+)?¿?\s*(?:(?:cu[aá]l|qu[eé])\s+fue\s+)?(?:la\s+)?"
    r"[uú]ltima\s+acci[oó]n\s*[?.!]*$",
    re.IGNORECASE,
)
_ESTADO_ETIQUETA = {
    "exito": "correcta",
    "error": "falló",
    "tiempo_agotado": "tiempo agotado",
    "cancelada": "cancelada",
    "no_verificada": "no verificada",
}


def _hora_local(creado: object) -> str:
    try:
        fecha = datetime.fromisoformat(str(creado)).replace(tzinfo=timezone.utc)
    except ValueError:
        return str(creado)
    return fecha.astimezone().strftime("%d/%m %H:%M")


def _formatear_accion(accion: Mapping[str, object]) -> str:
    estado = str(accion["estado"])
    partes = [
        _ESTADO_ETIQUETA.get(estado, estado),
        f"{int(accion['duracion_ms']) / 1000:.1f} s",
    ]
    verificacion = accion.get("verificacion")
    if isinstance(verificacion, str) and verificacion.startswith("ok"):
        partes.append("verificada")
    linea = (
        f"{accion['id']}. {accion['etiqueta']} — {', '.join(partes)} "
        f"({_hora_local(accion['creado'])}, {accion['origen']})"
    )
    if accion.get("error"):
        linea += f"\n   Motivo: {accion['error']}"
    return linea


def procesar_comando(texto: str) -> str | None:
    comando = texto.strip()
    if _ULTIMA_PATTERN.fullmatch(comando):
        acciones = listar_acciones(1)
        if not acciones:
            return "Todavía no registré ninguna acción."
        return "Última acción:\n" + _formatear_accion(acciones[0])
    if _HISTORIAL_PATTERN.fullmatch(comando):
        acciones = listar_acciones(10)
        if not acciones:
            return "Todavía no registré ninguna acción."
        return "Últimas acciones:\n" + "\n".join(_formatear_accion(a) for a in acciones)
    return None
