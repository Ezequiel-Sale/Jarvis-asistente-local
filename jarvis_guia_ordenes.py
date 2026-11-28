"""«Pensá y…» y la guía de frases para la IA (2026-11-27, pedido del usuario).

- ``separar_pensar``: «pensá y decime cómo sería la orden para…» → (True, «decime cómo sería…»). Esa frase
  va directo a la IA (sin órdenes directas ni el creador de rutinas, que la interceptaba) y con
  razonamiento (``think``), que tarda más y ayuda a deducir.
- ``guia``: la herramienta ``command_guide``. Busca en el catálogo de «? COMANDOS» las frases exactas que
  entiende Jarvis, para que la IA no invente una orden que después no se reconoce.
"""

from __future__ import annotations

import re
import unicodedata

_PENSAR = re.compile(
    r"^\s*(?:(?:jarvis|oye\s+jarvis)\s*[,:]?\s*)?"
    r"(?:pens[aá]|piensa|pens[aá]lo|pi[eé]nsalo|razon[aá]|razona)(?:\s+(?:bien|un\s+poco|despacio))?"
    r"\s*(?:,|:|\s+y\b)\s*(?P<resto>\S.*)$",
    re.IGNORECASE | re.DOTALL)


def separar_pensar(texto: str) -> tuple[bool, str]:
    """(True, el pedido sin «pensá y») si empieza así; si no, (False, texto)."""
    m = _PENSAR.match(texto or "")
    if m is None:
        return False, texto
    resto = m.group("resto").strip()
    return (True, resto) if resto else (False, texto)


# --- Guía de frases ----------------------------------------------------------------------------

# Cómo armar una rutina: el creador entiende este formato exacto (jarvis_routines.parsear_solicitud_creacion).
FORMATOS_RUTINA = (
    "Formato para crear una rutina por hora: «Crea una rutina llamada NOMBRE a las HH:MM [días] PASOS». Días: "
    "«todos los días», «de lunes a viernes» o «los miércoles y los viernes» (también antes de «a las»). PASOS, "
    "unidos con «y»: «pon el volumen al 30 %», «abrir una nueva pestaña», «navegar a youtube.com», "
    "«reanuda la música» / «pausa la música», «cambia la salida a auriculares», «bloquea el equipo», "
    "«apaga / suspende / reinicia el equipo». Ejemplo: «Crea una rutina llamada Despejarme a las 18:30 de lunes "
    "a viernes abrir una nueva pestaña y navegar a youtube.com y reanuda la música». Por evento: «Cuando abra "
    "Spotify, poné el volumen al 20». Para pasos que no están en la lista (abrir un programa, buscar un tema en "
    "YouTube, activarla con una frase), la sección Rutinas de Jarvis tiene un formulario."
)

_VACIAS = frozenset("""a al como con cual cuales de del el en es esta este hace hacer la las le lo los me mi mis
para por que se si sin su te tu un una y o orden ordenes frase frases decir pedir pido correcta jarvis puedo
quiero dime decime""".split())


def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto).casefold())
    return "".join(c for c in texto if not unicodedata.combining(c))


def _palabras(texto: str) -> set[str]:
    return {p for p in re.findall(r"[a-z0-9]+", _normal(texto)) if p not in _VACIAS and len(p) > 2}


def _coincide(buscada: str, palabras: set[str]) -> bool:
    """La palabra o su raíz («mover» ~ «muevo» no, «pestanas» ~ «pestana» sí: 5 letras en común)."""
    return buscada in palabras or any(
        len(p) >= 5 and len(buscada) >= 5 and (p.startswith(buscada[:5]) or buscada.startswith(p[:5]))
        for p in palabras)


def guia(query: str = "", catalogo=None, maximo: int = 12) -> str:
    """Las frases del catálogo que más se parecen a lo pedido (por palabras en común, también la raíz)."""
    import jarvis_comandos

    catalogo = catalogo if catalogo is not None else jarvis_comandos.CATALOGO
    buscadas = _palabras(query)
    puntajes = []
    for seccion in catalogo:
        titulo = _palabras(seccion.titulo)
        for comando in seccion.comandos:
            # Lo que coincide con la frase misma pesa el doble que la descripción o el título.
            frase = _palabras(comando.frase)
            resto = _palabras(comando.para_que) | titulo
            puntos = sum(2 if _coincide(b, frase) else 1 if _coincide(b, resto) else 0 for b in buscadas)
            if puntos:
                puntajes.append((puntos, seccion.titulo, comando))
    puntajes.sort(key=lambda t: -t[0])
    lineas = [f"- «{c.frase}» ({titulo}): {c.para_que}" for _p, titulo, c in puntajes[:maximo]]
    partes = []
    if re.search(r"\brutin", _normal(query)):
        partes.append(FORMATOS_RUTINA)
    if lineas:
        partes.append("Frases que Jarvis entiende tal cual (usá estas, no inventes otras):\n" + "\n".join(lineas))
    if not partes:
        return ("No hay una frase directa para eso en el catálogo: lo que no está ahí lo resuelve la IA. "
                "Secciones: " + ", ".join(s.titulo for s in catalogo) + ".")
    return "\n\n".join(partes)


def registrar(registry) -> None:
    registry.register(
        "command_guide",
        "Busca las frases exactas que entiende Jarvis (el catálogo de «? COMANDOS» y el formato para crear "
        "rutinas). Usala cuando el usuario pregunte cómo pedirle algo, cuál es la orden correcta o cómo armar "
        "una rutina; respondé con esas frases, no inventes otras. query: lo que quiere hacer.",
        {"type": "object", "properties": {"query": {"type": "string", "maxLength": 200}},
         "required": ["query"], "additionalProperties": False},
        lambda query="": guia(query),
        label="Guía de órdenes", timeout_seconds=10,
    )
