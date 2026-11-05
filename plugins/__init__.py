"""Plugins integrados de Jarvis.

Cada plugin es un módulo con:
- ``NOMBRE`` y ``DESCRIPCION``.
- ``registrar(registry, contexto)``: registra sus herramientas.
- Opcional ``detectar_comando(texto) -> (herramienta, argumentos) | None``:
  frases que se ejecutan directamente, sin pasar por el LLM.
- Opcional ``procesar_comando(texto, contexto) -> str | None``: comandos de
  texto que responden directamente (p. ej. configurar una preferencia).

Para añadir uno: crear el módulo aquí, añadirlo a ``PLUGINS_DISPONIBLES`` en
``jarvis_plugins.py`` y activarlo en ``integraciones.json``.
"""
