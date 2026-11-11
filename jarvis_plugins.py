"""Carga de plugins integrados (roadmap punto 5).

Los plugins se importan de forma estática (``plugins.*``) para que PyInstaller
los incluya en el .exe. Cuáles se activan lo decide ``integraciones.json``.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import ModuleType

import jarvis_core as core
from jarvis_tool_registry import ToolRegistry
from plugins import busqueda, clima

PLUGINS_DISPONIBLES: dict[str, ModuleType] = {
    clima.NOMBRE: clima,
    busqueda.NOMBRE: busqueda,
}


@dataclass
class ContextoPlugin:
    """Lo que un plugin puede usar del núcleo, sin depender de la app."""

    config: Mapping[str, object] = field(default_factory=dict)
    obtener_preferencia: Callable[[str], str | None] = core.obtener_preferencia
    guardar_preferencia: Callable[[str, str], None] = core.guardar_preferencia


@dataclass
class PluginCargado:
    nombre: str
    descripcion: str
    modulo: ModuleType
    contexto: ContextoPlugin


class GestorPlugins:
    def __init__(self) -> None:
        self.cargados: list[PluginCargado] = []
        self.errores: dict[str, str] = {}

    def cargar(self, configs: Mapping[str, Mapping[str, object]], registry: ToolRegistry) -> None:
        for nombre, config in configs.items():
            modulo = PLUGINS_DISPONIBLES.get(nombre)
            if modulo is None:
                self.errores[nombre] = "plugin desconocido"
                logging.warning("Plugin desconocido en la configuración: %s", nombre)
                continue
            contexto = ContextoPlugin(config=dict(config))
            try:
                modulo.registrar(registry, contexto)
            except Exception as error:
                self.errores[nombre] = str(error)
                logging.exception("No se pudo cargar el plugin %s.", nombre)
                continue
            self.cargados.append(
                PluginCargado(nombre, str(getattr(modulo, "DESCRIPCION", "")), modulo, contexto)
            )
            logging.info("Plugin cargado: %s", nombre)

    def detectar_comando(self, texto: str) -> tuple[str, dict[str, object]] | None:
        for plugin in self.cargados:
            detectar = getattr(plugin.modulo, "detectar_comando", None)
            if detectar is None:
                continue
            try:
                resultado = detectar(texto)
            except Exception:
                logging.exception("Falló la detección de comandos del plugin %s.", plugin.nombre)
                continue
            if resultado is not None:
                return resultado
        return None

    def procesar_comando(self, texto: str) -> str | None:
        for plugin in self.cargados:
            procesar = getattr(plugin.modulo, "procesar_comando", None)
            if procesar is None:
                continue
            respuesta = procesar(texto, plugin.contexto)
            if respuesta is not None:
                return respuesta
        return None
