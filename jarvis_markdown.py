"""Markdown sencillo para las burbujas del chat (sin dependencias).

``analizar(texto)`` devuelve una lista de tramos ``(texto, etiquetas, url)`` que
la interfaz inserta en un ``tk.Text`` con etiquetas ya configuradas:

- ``h``: encabezado (``# …``); ``b``: negrita (``**…**``); ``i``: cursiva (``*…*`` o ``_…_``)
- ``code``: código en línea (`` `…` ``); ``pre``: bloque de código (```` ``` ````)
- ``li``: elemento de lista (``-``, ``*``, ``•`` o ``1.``), con viñeta
- ``table``: tabla con ``|``, alineada en columnas monoespaciadas
- ``link``: enlace (``[texto](https://…)`` o una URL suelta); solo http/https
- ``quote``: cita (``> …``)

Es tolerante: lo que no entiende se muestra tal cual.
"""

import re

Tramo = tuple[str, tuple[str, ...], str | None]

_EN_LINEA = re.compile(
    r"(?P<code>`[^`\n]+`)"
    r"|(?P<link>\[[^\]\n]+\]\((?:https?://)[^)\s]+\))"
    r"|(?P<url>https?://[^\s<>()]+[^\s<>().,;:!?¿¡\"'»])"
    r"|(?P<b>\*\*[^*\n]+\*\*|__[^_\n]+__)"
    r"|(?P<i>(?<![\w*])\*[^*\s][^*\n]*\*(?![\w*])|(?<![\w_])_[^_\s][^_\n]*_(?![\w_]))"
)
_LISTA = re.compile(r"^(\s*)(?:[-*•]|\d{1,2}[.)])\s+(.*)$")
_ENCABEZADO = re.compile(r"^#{1,4}\s+(.*)$")
_SEPARADOR_TABLA = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _en_linea(texto: str, base: tuple[str, ...]) -> list[Tramo]:
    tramos: list[Tramo] = []
    pos = 0
    for m in _EN_LINEA.finditer(texto):
        if m.start() > pos:
            tramos.append((texto[pos:m.start()], base, None))
        tipo = m.lastgroup
        valor = m.group()
        if tipo == "code":
            tramos.append((valor[1:-1], base + ("code",), None))
        elif tipo == "link":
            etiqueta, url = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", valor).groups()
            tramos.append((etiqueta, base + ("link",), url))
        elif tipo == "url":
            tramos.append((valor, base + ("link",), valor))
        elif tipo == "b":
            tramos.extend(_en_linea(valor[2:-2], base + ("b",)))
        else:
            tramos.extend(_en_linea(valor[1:-1], base + ("i",)))
        pos = m.end()
    if pos < len(texto):
        tramos.append((texto[pos:], base, None))
    return tramos


def _celdas(linea: str) -> list[str]:
    linea = linea.strip()
    if linea.startswith("|"):
        linea = linea[1:]
    if linea.endswith("|"):
        linea = linea[:-1]
    return [c.strip().replace("**", "") for c in linea.split("|")]


def _es_fila_tabla(linea: str) -> bool:
    return linea.strip().startswith("|") and linea.count("|") >= 2


def _tabla(lineas: list[str]) -> str:
    filas = [_celdas(l) for l in lineas if not _SEPARADOR_TABLA.match(l)]
    columnas = max(len(f) for f in filas)
    filas = [f + [""] * (columnas - len(f)) for f in filas]
    anchos = [max(len(f[c]) for f in filas) for c in range(columnas)]
    salida = []
    for n, fila in enumerate(filas):
        salida.append("  ".join(celda.ljust(anchos[c]) for c, celda in enumerate(fila)).rstrip())
        if n == 0 and len(filas) > 1:
            salida.append("  ".join("─" * a for a in anchos))
    return "\n".join(salida)


def analizar(texto: str) -> list[Tramo]:
    """Tramos listos para insertar; el último no termina en salto de línea."""
    lineas = texto.replace("\r\n", "\n").strip("\n").split("\n")
    tramos: list[Tramo] = []
    i = 0
    while i < len(lineas):
        linea = lineas[i]
        if linea.strip().startswith("```"):
            fin = i + 1
            while fin < len(lineas) and not lineas[fin].strip().startswith("```"):
                fin += 1
            tramos.append(("\n".join(lineas[i + 1:fin]), ("pre",), None))
            i = fin + 1
        elif _es_fila_tabla(linea):
            fin = i
            while fin < len(lineas) and (_es_fila_tabla(lineas[fin]) or _SEPARADOR_TABLA.match(lineas[fin])):
                fin += 1
            tramos.append((_tabla(lineas[i:fin]), ("table",), None))
            i = fin
        elif (m := _ENCABEZADO.match(linea)) is not None:
            tramos.extend(_en_linea(m.group(1), ("h",)))
            i += 1
        elif (m := _LISTA.match(linea)) is not None:
            numero = re.match(r"\s*(\d{1,2})[.)]", linea)
            vineta = f"{numero.group(1)}. " if numero else "•  "
            tramos.append((vineta, ("li",), None))
            tramos.extend(_en_linea(m.group(2), ("li",)))
            i += 1
        elif linea.startswith(">"):
            tramos.extend(_en_linea(linea.lstrip("> "), ("quote",)))
            i += 1
        else:
            tramos.extend(_en_linea(linea, ()))
            i += 1
        if i < len(lineas):
            tramos.append(("\n", (), None))
    return [t for t in tramos if t[0]]


def texto_plano(texto: str) -> str:
    """Lo que se verá, sin marcas (para medir el ancho de la burbuja)."""
    return "".join(t for t, _e, _u in analizar(texto))
