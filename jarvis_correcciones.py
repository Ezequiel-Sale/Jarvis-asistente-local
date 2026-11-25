"""Palabras mal entendidas (2026-11-24, pedido del usuario: Whisper escribió «desmuntea» y la IA lo tomó por
«desmontar» y cerró una pestaña).

Si una frase no es una orden conocida, se busca una palabra que, cambiada por una parecida del
``VOCABULARIO`` de las órdenes, la vuelva una orden (``sugerir``). Entonces Jarvis pregunta «¿Quisiste decir
"desmutea"?» con SÍ / NO / «¿Cuál es la palabra?»; lo confirmado queda en la preferencia
``correcciones_voz`` ({escuchada: correcta}) y desde ahí se corrige solo (``aplicar``), sin preguntar.
Nada de esto pasa por la IA: es comparar letras (``difflib``) y probar con los detectores de siempre.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from difflib import SequenceMatcher, get_close_matches

PREFERENCIA = "correcciones_voz"
PARECIDO_MINIMO = 0.8
LARGO_MINIMO = 4  # palabras cortas («de», «la», «sí»…) nunca se corrigen
MAX_APRENDIDAS = 200

# Palabras de las órdenes (verbos en tú y vos sin tilde, y lo que suelen nombrar). Lo que esté acá puede
# ser la «palabra correcta» de una sugerencia; la frase corregida igual tiene que ser una orden.
VOCABULARIO = frozenset("""
pausa pausar reanuda reanudar continua sigue segui reproduce reproduci mutea mutear desmutea desmutear silencia
silenciar desilencia quita quitar activa activar desactiva desactivar enciende encende prende apaga apagar cierra
cerra cerrar abre abri abrir maximiza maximizar minimiza minimizar restaura restaurar agranda achica sube subi
subir baja baja bajar pon pone poner cambia cambiar mueve move mover manda mandar trae trae traer muestra mostra
mostrar oculta ocultar esconde adelanta retrocede siguiente anterior repite busca buscar escribe escribi
apreta presiona graba grabar guarda guardalo recarga recargar actualiza actualizar instala desinstala
volumen sonido audio silencio pestana pestanas ventana ventanas monitor pantalla completa tele televisor juego
juegos gestos escucha continua tema modo compacto auriculares parlantes salida microfono brave whatsapp discord
spotify steam twitch youtube netflix crunchyroll widget calculadora navegador escritorio teatro
""".split())


# Pronombres que se pegan al verbo («muévelo», «ponela», «traémelo»). Una palabra que es una del vocabulario
# más uno de estos NO está mal entendida: es la misma orden sobre algo. 2026-11-24: se aprendió «muévelo →
# mueve» y la frase de un gesto («… y muévelo al monitor 1») perdió el «lo» y movió Brave en vez de Jarvis.
CLITICOS = ("melo", "mela", "selo", "sela", "telo", "tela", "lo", "la", "los", "las", "le", "les", "me", "te",
            "se", "nos")


def es_variante(palabra: str, base: str) -> bool:
    """¿``palabra`` es ``base`` con pronombres pegados (sin importar tildes)?"""
    palabra, base = normalizar(palabra), normalizar(base)
    if not palabra.startswith(base) or palabra == base:
        return False
    resto = palabra[len(base):]
    return any(resto == c or (resto.startswith(c) and resto[len(c):] in CLITICOS) for c in CLITICOS)


def normalizar(palabra: str) -> str:
    texto = unicodedata.normalize("NFKD", palabra.casefold())
    return "".join(c for c in texto if not unicodedata.combining(c))


_PALABRA = re.compile(r"[^\W\d_]+", re.UNICODE)


# --- Guardadas ---------------------------------------------------------------------------------

def cargar(obtener: Callable[[str], str | None]) -> dict[str, str]:
    try:
        datos = json.loads(obtener(PREFERENCIA) or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(datos, dict):
        return {}
    # Lo aprendido que en realidad era el verbo con un pronombre («muévelo → mueve») no se usa.
    return {normalizar(k): str(v) for k, v in datos.items()
            if isinstance(k, str) and isinstance(v, str) and not es_variante(k, v)}


def guardar(obtener, guardar_pref, escuchada: str, correcta: str) -> dict[str, str]:
    """Anota «escuchada → correcta» (o la borra si son la misma palabra)."""
    datos = cargar(obtener)
    clave = normalizar(escuchada.strip())
    correcta = correcta.strip()
    if not clave or not correcta or clave == normalizar(correcta) or es_variante(clave, correcta):
        datos.pop(clave, None)
    else:
        datos[clave] = correcta
        if len(datos) > MAX_APRENDIDAS:
            datos.pop(next(iter(datos)))
    guardar_pref(PREFERENCIA, json.dumps(datos, ensure_ascii=False))
    return datos


def olvidar(obtener, guardar_pref, escuchada: str) -> dict[str, str]:
    datos = cargar(obtener)
    datos.pop(normalizar(escuchada), None)
    guardar_pref(PREFERENCIA, json.dumps(datos, ensure_ascii=False))
    return datos


def aplicar(texto: str, aprendidas: dict[str, str]) -> str:
    """Cambia cada palabra aprendida por la correcta (sin importar tildes ni mayúsculas)."""
    if not aprendidas or not texto:
        return texto
    return _PALABRA.sub(lambda m: aprendidas.get(normalizar(m.group(0)), m.group(0)), texto)


def reemplazar(texto: str, escuchada: str, correcta: str) -> str:
    """El texto con ``escuchada`` (palabra entera, sin importar tildes) cambiada por ``correcta``."""
    clave = normalizar(escuchada)
    return _PALABRA.sub(lambda m: correcta if normalizar(m.group(0)) == clave else m.group(0), texto)


# --- Sugerir --------------------------------------------------------------------------------------

def candidatas(palabra: str, vocabulario=VOCABULARIO, n: int = 3) -> list[str]:
    clave = normalizar(palabra)
    if len(clave) < LARGO_MINIMO or clave in vocabulario:
        return []
    return [c for c in get_close_matches(clave, sorted(vocabulario), n=n, cutoff=PARECIDO_MINIMO)
            if not es_variante(clave, c)]


def sugerir(texto: str, es_orden: Callable[[str], bool], vocabulario=VOCABULARIO) -> tuple[str, str, str] | None:
    """(escuchada, correcta, texto corregido) si cambiar UNA palabra por una parecida vuelve la frase una orden.

    Entre varias, la más parecida. None si la frase ya es una orden o no hay ninguna que sirva."""
    if not texto or es_orden(texto):
        return None
    opciones = []
    for m in _PALABRA.finditer(texto):
        palabra = m.group(0)
        for candidata in candidatas(palabra, vocabulario):
            opciones.append((SequenceMatcher(None, normalizar(palabra), candidata).ratio(), palabra, candidata))
    for _parecido, palabra, candidata in sorted(opciones, key=lambda o: -o[0]):
        corregido = reemplazar(texto, palabra, candidata)
        if es_orden(corregido):
            return palabra, candidata, corregido
    return None


_SI = re.compile(r"^(?:si|sip|dale|claro|correcto|exacto|eso|eso\s+mismo|afirmativo|ok|okay)(?:\s+jarvis)?[.!]*$")
_NO = re.compile(r"^(?:no|nop|nada|negativo|no\s+era\s+eso|ninguna)(?:\s+jarvis)?[.!]*$")
_PALABRA_DICHA = re.compile(
    r"^(?:(?:no\s*,?\s*)?(?:la\s+palabra\s+(?:es|era)|quise\s+decir|quería\s+decir|queria\s+decir|era|es|dije))\s+"
    r"[«\"']?(?P<palabra>[^\W\d_]+)[»\"']?[.!]*$")


def respuesta(texto: str) -> tuple[str, str | None] | None:
    """Respuesta a «¿Quisiste decir…?»: ("si", None), ("no", None), ("palabra", «desmutea») o None."""
    limpio = " ".join(normalizar(texto).replace(",", " ").split()).strip(" ¿?¡!")
    if _SI.match(limpio):
        return "si", None
    if _NO.match(limpio):
        return "no", None
    m = _PALABRA_DICHA.match(limpio)
    if m:
        return "palabra", m.group("palabra")
    return None
