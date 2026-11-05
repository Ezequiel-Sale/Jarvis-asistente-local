"""Configuración de plugins y servidores MCP (roadmap punto 5).

Se guarda en ``integraciones.json`` junto a la app. Si no existe se crea con
valores por defecto; el usuario puede editarlo (con Jarvis cerrado) para
activar/desactivar integraciones o cambiar qué herramientas se permiten.
"""

import copy
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import jarvis_core as core

NOMBRE_ARCHIVO = "integraciones.json"

# Herramientas de lectura del servidor de archivos que se exponen por defecto.
# Las de escritura (write_file, edit_file, move_file…) nunca se listan aquí.
HERRAMIENTAS_ARCHIVOS_LECTURA = [
    "list_allowed_directories",
    "list_directory",
    "read_text_file",
    "search_files",
    "get_file_info",
]


def configuracion_por_defecto() -> dict:
    documentos = str(Path.home() / "Documents")
    return {
        "plugins": {
            "clima": {"activo": True},
        },
        "mcp_servers": {
            "archivos": {
                "activo": True,
                "descripcion": f"Archivos de {documentos} (solo lectura)",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", documentos],
                "herramientas_permitidas": list(HERRAMIENTAS_ARCHIVOS_LECTURA),
                "lecturas_sin_preguntar": True,
            }
        },
    }


@dataclass(frozen=True, slots=True)
class ServidorMCPConfig:
    nombre: str
    command: str
    args: tuple[str, ...]
    descripcion: str = ""
    env: dict[str, str] = field(default_factory=dict)
    herramientas_permitidas: tuple[str, ...] = ()
    lecturas_sin_preguntar: bool = False


@dataclass(frozen=True, slots=True)
class Integraciones:
    plugins: dict[str, dict]
    servidores_mcp: tuple[ServidorMCPConfig, ...]
    ruta: Path
    error: str | None = None


def _parsear(datos: dict, ruta: Path) -> Integraciones:
    if not isinstance(datos, dict):
        raise ValueError("el archivo debe contener un objeto JSON.")
    plugins = datos.get("plugins", {})
    servidores = datos.get("mcp_servers", {})
    if not isinstance(plugins, dict) or not isinstance(servidores, dict):
        raise ValueError("«plugins» y «mcp_servers» deben ser objetos.")
    activos = {
        nombre: config
        for nombre, config in plugins.items()
        if isinstance(config, dict) and config.get("activo", True)
    }
    configs: list[ServidorMCPConfig] = []
    for nombre, config in servidores.items():
        if not isinstance(config, dict) or not config.get("activo", True):
            continue
        command = config.get("command")
        args = config.get("args", [])
        permitidas = config.get("herramientas_permitidas", [])
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"el servidor MCP «{nombre}» no tiene «command».")
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError(f"«args» del servidor «{nombre}» debe ser una lista de textos.")
        if not isinstance(permitidas, list) or not all(isinstance(h, str) for h in permitidas):
            raise ValueError(
                f"«herramientas_permitidas» del servidor «{nombre}» debe ser una lista de textos."
            )
        env = config.get("env", {})
        if not isinstance(env, dict):
            raise ValueError(f"«env» del servidor «{nombre}» debe ser un objeto.")
        configs.append(
            ServidorMCPConfig(
                nombre=str(nombre),
                command=command,
                args=tuple(args),
                descripcion=str(config.get("descripcion", "")),
                env={str(k): str(v) for k, v in env.items()},
                herramientas_permitidas=tuple(permitidas),
                lecturas_sin_preguntar=bool(config.get("lecturas_sin_preguntar", False)),
            )
        )
    return Integraciones(activos, tuple(configs), ruta)


def cargar_integraciones(app_dir: Path | None = None) -> Integraciones:
    """Lee (o crea) integraciones.json. Si es inválido, no activa ninguna integración."""
    ruta = (app_dir or core.APP_DIR) / NOMBRE_ARCHIVO
    if not ruta.exists():
        datos = configuracion_por_defecto()
        try:
            ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
            logging.info("Se creó %s con la configuración por defecto.", ruta)
        except OSError:
            logging.exception("No se pudo crear %s; se usa la configuración por defecto.", ruta)
        return _parsear(copy.deepcopy(datos), ruta)
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        return _parsear(datos, ruta)
    except (OSError, ValueError) as error:
        logging.error("Configuración de integraciones inválida en %s: %s", ruta, error)
        return Integraciones({}, (), ruta, error=f"{NOMBRE_ARCHIVO} inválido: {error}")
