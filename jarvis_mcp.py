"""Cliente MCP (Model Context Protocol) con permisos limitados (roadmap punto 5).

- Cada servidor de ``integraciones.json`` se lanza por stdio en un hilo con su
  propio bucle asyncio y la sesión se mantiene abierta.
- Solo se registran las herramientas de ``herramientas_permitidas``.
- Permisos: solo lectura (según el servidor) + ``lecturas_sin_preguntar`` →
  riesgo bajo sin confirmación; destructivas → alto; resto → medio (ambas con
  confirmación). Nunca se permiten en rutinas automáticas.
- Los resultados no se guardan en el historial de acciones (pueden contener
  contenido de archivos) y se recortan para no desbordar el contexto del LLM.
"""

import asyncio
import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path

from jarvis_integraciones import ServidorMCPConfig
from jarvis_tool_registry import ToolRegistry, ToolRisk

TIEMPO_INICIO = 120.0
TIEMPO_LLAMADA = 30.0
MAX_RESULTADO = 4000
MAX_DESCRIPCION = 300
# Etiquetas en español para herramientas conocidas (se muestran al usuario).
TITULOS_ES = {
    "list_allowed_directories": "carpetas permitidas",
    "list_directory": "listar carpeta",
    "read_text_file": "leer archivo",
    "read_file": "leer archivo",
    "search_files": "buscar archivos",
    "get_file_info": "información de archivo",
}


@dataclass(frozen=True, slots=True)
class HerramientaMCP:
    servidor: str
    nombre: str
    titulo: str
    descripcion: str
    esquema: dict
    solo_lectura: bool
    destructiva: bool


def nombre_registro(servidor: str, herramienta: str) -> str:
    base = f"mcp_{servidor}_{herramienta}"
    return re.sub(r"[^A-Za-z0-9_]", "_", base)


def normalizar_esquema(esquema: dict | None) -> dict:
    """Esquema de objeto válido para ToolRegistry (properties y required presentes)."""
    esquema = dict(esquema or {})
    esquema.pop("$schema", None)
    esquema["type"] = "object"
    propiedades = esquema.get("properties")
    if not isinstance(propiedades, dict):
        propiedades = {}
    esquema["properties"] = {k: v for k, v in propiedades.items() if isinstance(v, dict)}
    requeridos = esquema.get("required")
    esquema["required"] = [
        r for r in (requeridos if isinstance(requeridos, list) else []) if r in esquema["properties"]
    ]
    return esquema


def riesgo_de(herramienta: HerramientaMCP, lecturas_sin_preguntar: bool) -> tuple[ToolRisk, bool]:
    """(riesgo, requiere_confirmación) según las anotaciones y la configuración."""
    if herramienta.destructiva:
        return ToolRisk.HIGH, True
    if herramienta.solo_lectura and lecturas_sin_preguntar:
        return ToolRisk.LOW, False
    return ToolRisk.MEDIUM, True


def formatear_resultado(resultado) -> str:
    textos: list[str] = []
    for contenido in getattr(resultado, "content", []) or []:
        texto = getattr(contenido, "text", None)
        textos.append(texto if isinstance(texto, str) else "[contenido no textual omitido]")
    salida = "\n".join(textos).strip() or "(sin contenido)"
    if getattr(resultado, "is_error", False):
        raise RuntimeError(salida[:500])
    if len(salida) > MAX_RESULTADO:
        salida = (
            salida[:MAX_RESULTADO]
            + f"\n[… recortado: se muestran {MAX_RESULTADO} de {len(salida)} caracteres]"
        )
    return salida


class GestorMCP:
    """Mantiene las sesiones MCP en un hilo propio y ofrece llamadas síncronas."""

    def __init__(self, configs: tuple[ServidorMCPConfig, ...], log_dir: Path) -> None:
        self.configs = {config.nombre: config for config in configs}
        self.log_dir = log_dir
        self.estado: dict[str, str] = {nombre: "iniciando" for nombre in self.configs}
        self._sesiones: dict[str, object] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None

    def iniciar(self, al_conectar: Callable[[str, list[HerramientaMCP]], None]) -> None:
        if not self.configs:
            return
        listo = threading.Event()

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._stop = asyncio.Event()
            listo.set()
            try:
                self._loop.run_until_complete(self._ejecutar_todos(al_conectar))
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=run, name="JarvisMCP", daemon=True)
        self._thread.start()
        listo.wait(5)

    async def _ejecutar_todos(self, al_conectar) -> None:
        await asyncio.gather(
            *(self._ejecutar_servidor(config, al_conectar) for config in self.configs.values()),
            return_exceptions=True,
        )

    async def _ejecutar_servidor(self, config: ServidorMCPConfig, al_conectar) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self.log_dir.mkdir(parents=True, exist_ok=True)
        errlog_path = self.log_dir / f"mcp_{config.nombre}.log"
        params = StdioServerParameters(
            command=config.command,
            args=list(config.args),
            env=config.env or None,
        )
        try:
            with open(errlog_path, "a", encoding="utf-8") as errlog:
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write) as session:
                        await asyncio.wait_for(session.initialize(), TIEMPO_INICIO)
                        listado = await asyncio.wait_for(session.list_tools(), TIEMPO_INICIO)
                        herramientas = self._filtrar(config, listado.tools)
                        self._sesiones[config.nombre] = session
                        self.estado[config.nombre] = (
                            f"conectado ({len(herramientas)} herramientas permitidas)"
                        )
                        logging.info(
                            "MCP %s conectado: %s herramientas permitidas.",
                            config.nombre,
                            len(herramientas),
                        )
                        al_conectar(config.nombre, herramientas)
                        await self._stop.wait()
        except Exception as error:
            mensaje = str(error) or type(error).__name__
            self.estado[config.nombre] = f"error: {mensaje[:160]}"
            logging.exception("El servidor MCP %s falló.", config.nombre)
        finally:
            self._sesiones.pop(config.nombre, None)
            if not self.estado[config.nombre].startswith("error"):
                self.estado[config.nombre] = "detenido"

    @staticmethod
    def _filtrar(config: ServidorMCPConfig, herramientas) -> list[HerramientaMCP]:
        permitidas = set(config.herramientas_permitidas)
        resultado: list[HerramientaMCP] = []
        for herramienta in herramientas:
            if herramienta.name not in permitidas:
                continue
            anotaciones = getattr(herramienta, "annotations", None)
            resultado.append(
                HerramientaMCP(
                    servidor=config.nombre,
                    nombre=herramienta.name,
                    titulo=str(
                        TITULOS_ES.get(herramienta.name)
                        or getattr(anotaciones, "title", None)
                        or getattr(herramienta, "title", None)
                        or herramienta.name
                    ),
                    descripcion=(herramienta.description or herramienta.name)[:MAX_DESCRIPCION],
                    esquema=normalizar_esquema(getattr(herramienta, "input_schema", None)),
                    solo_lectura=getattr(anotaciones, "read_only_hint", None) is True,
                    destructiva=getattr(anotaciones, "destructive_hint", None) is True,
                )
            )
        faltan = permitidas - {h.nombre for h in resultado}
        if faltan:
            logging.warning(
                "MCP %s: herramientas permitidas que el servidor no ofrece: %s",
                config.nombre,
                ", ".join(sorted(faltan)),
            )
        return resultado

    def llamar(self, servidor: str, herramienta: str, argumentos: dict, timeout: float = TIEMPO_LLAMADA) -> str:
        session = self._sesiones.get(servidor)
        if session is None or self._loop is None:
            raise RuntimeError(f"El servidor MCP «{servidor}» no está conectado.")
        futuro = asyncio.run_coroutine_threadsafe(
            session.call_tool(herramienta, argumentos), self._loop
        )
        try:
            resultado = futuro.result(timeout)
        except FutureTimeout as error:
            futuro.cancel()
            raise TimeoutError(
                f"El servidor MCP «{servidor}» no respondió en {timeout:g} s."
            ) from error
        return formatear_resultado(resultado)

    def registrar_herramientas(
        self,
        registry: ToolRegistry,
        servidor: str,
        herramientas: list[HerramientaMCP],
    ) -> list[str]:
        config = self.configs[servidor]
        registradas: list[str] = []
        for herramienta in herramientas:
            riesgo, confirmar = riesgo_de(herramienta, config.lecturas_sin_preguntar)
            nombre = nombre_registro(servidor, herramienta.nombre)
            prefijo = f"[{config.descripcion or servidor}] "
            try:
                registry.register(
                    nombre,
                    prefijo + herramienta.descripcion,
                    herramienta.esquema,
                    self._handler(servidor, herramienta.nombre),
                    risk=riesgo,
                    requires_confirmation=confirmar,
                    label=f"{servidor.capitalize()}: {herramienta.titulo}",
                    timeout_seconds=TIEMPO_LLAMADA + 5,
                    record_result=False,
                )
            except ValueError as error:
                logging.warning("No se pudo registrar %s: %s", nombre, error)
                continue
            registradas.append(nombre)
        return registradas

    def _handler(self, servidor: str, herramienta: str):
        def handler(**argumentos) -> str:
            return self.llamar(servidor, herramienta, argumentos)

        return handler

    def detener(self) -> None:
        if self._loop is None or self._stop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._stop.set)
        except RuntimeError:
            return
        if self._thread is not None:
            self._thread.join(timeout=5)
