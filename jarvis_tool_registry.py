import logging
import threading
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from jarvis_actions import (
    TIEMPO_MAXIMO_VERIFICACION,
    CancellationToken,
    EstadoAccion,
    OperacionCancelada,
    ResultadoAccion,
    TiempoAgotado,
    Verificacion,
    Verificador,
    ejecutar_con_limites,
)


class ToolError(Exception):
    """Base exception for tool registry failures."""


class ToolNotFoundError(ToolError):
    pass


class ToolValidationError(ToolError):
    pass


class ToolConfirmationRequired(ToolError):
    pass


class ToolTimeoutError(ToolError):
    """La herramienta superó su tiempo máximo de ejecución."""


class ToolCancelledError(ToolError):
    """El usuario canceló la operación antes o durante la ejecución."""


class ToolVerificationError(ToolError):
    """La herramienta terminó, pero la comprobación posterior falló."""


class ToolRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


ToolHandler = Callable[..., object]
ArgumentValidator = Callable[[object], object]
ActionRecorder = Callable[[ResultadoAccion], None]

DEFAULT_TOOL_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: ToolHandler = field(repr=False, compare=False)
    risk: ToolRisk = ToolRisk.LOW
    requires_confirmation: bool = False
    argument_validators: Mapping[str, ArgumentValidator] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    label: str = ""
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT
    verifier: Verificador | None = field(default=None, repr=False, compare=False)
    # False: el historial anota la ejecución pero no guarda su resultado (privacidad).
    record_result: bool = True
    # False: no se ofrece al modelo de lenguaje; solo la usa la app tras una
    # confirmación propia (p. ej. el clic en pantalla con vista previa).
    exposed: bool = True

    @property
    def display_name(self) -> str:
        return self.label or self.name


class ToolRegistry:
    def __init__(self, recorder: ActionRecorder | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._recorder = recorder
        # Las integraciones (MCP) registran herramientas desde otro hilo.
        self._lock = threading.Lock()
        # «Deshaz lo último» (jarvis_deshacer): antes de ejecutar se anota cómo volver atrás
        # (antes_de_ejecutar(nombre, argumentos, origen) → contexto) y, si salió bien, se
        # guarda (tras_exito(contexto)). Un fallo de los ganchos nunca impide la acción.
        self.antes_de_ejecutar: Callable[[str, dict[str, object], str], object] | None = None
        self.tras_exito: Callable[[object], None] | None = None

    def register(
        self,
        name: str,
        description: str,
        parameters: Mapping[str, Any],
        handler: ToolHandler,
        *,
        risk: ToolRisk = ToolRisk.LOW,
        requires_confirmation: bool = False,
        argument_validators: Mapping[str, ArgumentValidator] | None = None,
        label: str | None = None,
        timeout_seconds: float = DEFAULT_TOOL_TIMEOUT,
        verifier: Verificador | None = None,
        record_result: bool = True,
        exposed: bool = True,
    ) -> None:
        if not name or not name.replace("_", "").isalnum():
            raise ValueError("El nombre de la herramienta debe ser un identificador.")
        if not description.strip():
            raise ValueError("La herramienta debe tener una descripción.")
        if not callable(handler):
            raise TypeError("El handler de la herramienta debe ser invocable.")
        if risk is ToolRisk.HIGH and not requires_confirmation:
            raise ValueError("Las herramientas de riesgo alto requieren confirmación.")
        if not timeout_seconds > 0:
            raise ValueError("El tiempo máximo de la herramienta debe ser positivo.")
        if verifier is not None and not callable(verifier):
            raise TypeError("El verificador de la herramienta debe ser invocable.")
        self._validate_schema(parameters)
        validators = dict(argument_validators or {})
        unknown_validators = validators.keys() - parameters["properties"].keys()
        if unknown_validators:
            raise ValueError(
                "Hay validadores para parámetros no declarados: "
                + ", ".join(sorted(unknown_validators))
            )
        if any(not callable(validator) for validator in validators.values()):
            raise TypeError("Cada validador de parámetros debe ser invocable.")
        definition = ToolDefinition(
            name=name,
            description=description.strip(),
            parameters=deepcopy(dict(parameters)),
            handler=handler,
            risk=risk,
            requires_confirmation=requires_confirmation,
            argument_validators=validators,
            label=(label or "").strip(),
            timeout_seconds=float(timeout_seconds),
            verifier=verifier,
            record_result=record_result,
            exposed=exposed,
        )
        with self._lock:
            if name in self._tools:
                raise ValueError(f"La herramienta {name!r} ya está registrada.")
            self._tools[name] = definition

    @staticmethod
    def _validate_schema(parameters: Mapping[str, Any]) -> None:
        if parameters.get("type") != "object":
            raise ValueError("Los parámetros deben describir un objeto JSON.")
        properties = parameters.get("properties")
        required = parameters.get("required", [])
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            raise ValueError("El esquema debe declarar properties y required válidos.")
        if any(not isinstance(key, str) or not isinstance(value, Mapping) for key, value in properties.items()):
            raise ValueError("Cada parámetro debe tener un nombre y un esquema.")
        if any(key not in properties for key in required):
            raise ValueError("required contiene un parámetro no declarado en properties.")

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as error:
            raise ToolNotFoundError(f"La herramienta {name!r} no está registrada.") from error

    def definitions(self) -> tuple[ToolDefinition, ...]:
        with self._lock:
            return tuple(self._tools.values())

    def ollama_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": deepcopy(dict(tool.parameters)),
                },
            }
            for tool in self.definitions()
            if tool.exposed
        ]

    def validate_arguments(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        tool = self.get(name)
        if not isinstance(arguments, Mapping):
            raise ToolValidationError("Los argumentos deben ser un objeto JSON.")

        properties = tool.parameters["properties"]
        required = tool.parameters.get("required", [])
        missing = [key for key in required if key not in arguments]
        if missing:
            raise ToolValidationError(
                "Faltan parámetros obligatorios: " + ", ".join(missing)
            )
        additional_properties = tool.parameters.get("additionalProperties", False)
        unknown = arguments.keys() - properties.keys()
        if unknown and additional_properties is False:
            raise ToolValidationError(
                "Parámetros no permitidos: " + ", ".join(sorted(unknown))
            )

        validated = dict(arguments)
        for key, value in arguments.items():
            if key not in properties:
                continue
            schema = properties[key]
            self._validate_value(key, value, schema)
            validator = tool.argument_validators.get(key)
            if validator is not None:
                try:
                    value = validator(value)
                except (TypeError, ValueError) as error:
                    raise ToolValidationError(f"{key}: {error}") from error
                self._validate_value(key, value, schema)
            validated[key] = value
        return validated

    @staticmethod
    def _validate_value(
        name: str,
        value: object,
        schema: Mapping[str, Any],
    ) -> None:
        expected_type = schema.get("type")
        validators = {
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "array": lambda item: isinstance(item, list),
            "object": lambda item: isinstance(item, dict),
            "null": lambda item: item is None,
        }
        if expected_type is None:
            # Esquemas externos (MCP) pueden usar anyOf/oneOf sin "type": el
            # servidor valida el detalle.
            return
        if isinstance(expected_type, list):
            if any(t not in validators for t in expected_type):
                raise ToolValidationError(
                    f"El esquema del parámetro {name!r} tiene un tipo no soportado."
                )
            if not any(validators[t](value) for t in expected_type):
                raise ToolValidationError(
                    f"El parámetro {name!r} debe ser de tipo {' o '.join(expected_type)}."
                )
            return
        type_valid = validators.get(expected_type)
        if type_valid is None:
            raise ToolValidationError(
                f"El esquema del parámetro {name!r} tiene un tipo no soportado."
            )
        if not type_valid(value):
            raise ToolValidationError(
                f"El parámetro {name!r} debe ser de tipo {expected_type}."
            )
        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                raise ToolValidationError(f"El parámetro {name!r} es demasiado corto.")
            if len(value) > schema.get("maxLength", float("inf")):
                raise ToolValidationError(f"El parámetro {name!r} es demasiado largo.")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value < schema.get("minimum", float("-inf")):
                raise ToolValidationError(
                    f"El parámetro {name!r} es menor que el mínimo permitido."
                )
            if value > schema.get("maximum", float("inf")):
                raise ToolValidationError(
                    f"El parámetro {name!r} excede el máximo permitido."
                )
        if "enum" in schema and value not in schema["enum"]:
            raise ToolValidationError(
                f"El valor de {name!r} debe ser uno de: "
                + ", ".join(map(str, schema["enum"]))
            )

    def execute(
        self,
        name: str,
        arguments: Mapping[str, object],
        *,
        confirmed: bool = False,
        origin: str = "agente",
        cancel_token: CancellationToken | None = None,
    ) -> object:
        """Valida, ejecuta con tiempo máximo, verifica y registra la herramienta.

        Los errores del handler se relanzan con su tipo original; el tiempo
        agotado, la cancelación y la verificación fallida se traducen a
        subclases de ToolError.
        """
        tool = self.get(name)
        validated = self.validate_arguments(name, arguments)
        if cancel_token is not None and cancel_token.cancelled:
            message = f"Se canceló «{tool.display_name}» antes de ejecutarla."
            self._record(tool, origin, EstadoAccion.CANCELADA, validated, 0.0, error=message)
            raise ToolCancelledError(message)
        if tool.requires_confirmation and not confirmed:
            raise ToolConfirmationRequired(
                f"La herramienta {name!r} requiere confirmación explícita."
            )

        contexto = None
        if self.antes_de_ejecutar is not None:
            try:
                contexto = self.antes_de_ejecutar(name, dict(validated), origin)
            except Exception:
                logging.debug("Falló el gancho previo de %s.", name, exc_info=True)
        started = time.perf_counter()
        try:
            result = ejecutar_con_limites(
                lambda: tool.handler(**validated),
                timeout=tool.timeout_seconds,
                descripcion=tool.display_name,
                cancel_token=cancel_token,
            )
        except TiempoAgotado as error:
            self._record(
                tool, origin, EstadoAccion.TIEMPO_AGOTADO, validated,
                time.perf_counter() - started, error=str(error),
            )
            raise ToolTimeoutError(str(error)) from error
        except OperacionCancelada as error:
            self._record(
                tool, origin, EstadoAccion.CANCELADA, validated,
                time.perf_counter() - started, error=str(error),
            )
            raise ToolCancelledError(str(error)) from error
        except Exception as error:
            self._record(
                tool, origin, EstadoAccion.ERROR, validated,
                time.perf_counter() - started,
                error=str(error) or type(error).__name__,
            )
            raise
        duration = time.perf_counter() - started

        verification = self._verify(tool, validated, result)
        if verification is not None and verification.ok is False:
            message = (
                f"«{tool.display_name}» se ejecutó, pero la comprobación falló: "
                f"{verification.detalle}"
            )
            self._record(
                tool, origin, EstadoAccion.NO_VERIFICADA, validated, duration,
                result=result, error=message, verification=verification,
            )
            raise ToolVerificationError(message)
        self._record(
            tool, origin, EstadoAccion.EXITO, validated, duration,
            result=result, verification=verification,
        )
        if self.tras_exito is not None and contexto is not None:
            try:
                self.tras_exito(contexto)
            except Exception:
                logging.debug("Falló el gancho posterior de %s.", name, exc_info=True)
        return result

    @staticmethod
    def _verify(
        tool: ToolDefinition,
        arguments: dict[str, object],
        result: object,
    ) -> Verificacion | None:
        if tool.verifier is None:
            return None
        verifier = tool.verifier
        try:
            verification = ejecutar_con_limites(
                lambda: verifier(arguments, result),
                timeout=TIEMPO_MAXIMO_VERIFICACION,
                descripcion=f"verificar {tool.display_name}",
            )
        except Exception as error:
            logging.warning("No se pudo verificar %s: %s", tool.name, error)
            return Verificacion(None, f"No se pudo comprobar: {error}")
        if not isinstance(verification, Verificacion):
            logging.warning("El verificador de %s devolvió un valor inválido.", tool.name)
            return None
        return verification

    def _record(
        self,
        tool: ToolDefinition,
        origin: str,
        status: EstadoAccion,
        arguments: dict[str, object],
        duration: float,
        *,
        result: object = None,
        error: str | None = None,
        verification: Verificacion | None = None,
    ) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder(
                ResultadoAccion(
                    herramienta=tool.name,
                    etiqueta=tool.display_name,
                    origen=origin,
                    estado=status,
                    duracion=duration,
                    argumentos=dict(arguments),
                    resultado=result if tool.record_result else None,
                    error=error,
                    verificacion=verification,
                )
            )
        except Exception:
            logging.exception("No se pudo registrar la acción %s.", tool.name)
