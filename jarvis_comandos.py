"""Guía de comandos de voz (ventana «? COMANDOS» de la cabecera).

Lógica sin Tk: el catálogo de órdenes que Jarvis entiende directamente, las frases de
las rutinas del usuario y el filtro del buscador. La ventana la dibuja
``JarvisUIMixin._show_commands_help``.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Comando:
    frase: str        # lo que se dice, p. ej. «Sube el volumen al 30 %»
    para_que: str     # qué hace, en palabras de cualquiera


@dataclass(frozen=True, slots=True)
class Seccion:
    icono: str
    titulo: str
    comandos: tuple[Comando, ...]


def _s(icono: str, titulo: str, *pares: tuple[str, str]) -> Seccion:
    return Seccion(icono, titulo, tuple(Comando(f, p) for f, p in pares))


CATALOGO: tuple[Seccion, ...] = (
    _s("🎙", "Hablar con Jarvis",
       ("Hey Jarvis… (y tu orden)", "Despierta a Jarvis sin tocar nada; también interrumpe lo que esté diciendo."),
       ("F9 o el botón 🎙 MIC", "Graba lo que dices sin usar «Hey Jarvis»."),
       ("Sí / No", "Responde cualquier confirmación, sin decir «Hey Jarvis»."),
       ("Cancela", "Detiene lo que esté haciendo (también con Esc)."),
       ("Deja de escucharme / Activa la palabra de activación", "Apaga o enciende la escucha de «Hey Jarvis»."),
       ("Cambia tu voz a la argentina", "Elige otra voz (España, México o Argentina)."),
       ("Silencia el sonido de Jarvis / Activa el sonido", "Jarvis responde solo por escrito o vuelve a hablar.")),
    _s("🔊", "Volumen y sonido",
       ("Sube el volumen al 30 %", "Pone el volumen del equipo en ese porcentaje."),
       ("Pon el volumen de Spotify al 30", "Cambia el volumen de un solo programa."),
       ("Silencia Discord", "Quita el sonido de un programa."),
       ("Cambia la salida a auriculares", "Saca el sonido por los auriculares o por los parlantes."),
       ("¿Cuánta batería tienen los auriculares?", "Dice la batería de los auriculares G533.")),
    _s("⏯", "Vídeos y música",
       ("Pausa el vídeo del monitor 1", "Pausa lo que se reproduce en esa pantalla."),
       ("Reanuda el vídeo del monitor 2", "Vuelve a reproducir lo que estaba pausado."),
       ("Pausa el del monitor 1 y reanuda el del monitor 2", "Varias órdenes de vídeo en una sola frase."),
       ("Siguiente canción / La anterior", "Pasa de canción o de vídeo."),
       ("Pon el vídeo en pantalla completa", "Agranda el vídeo a toda la pantalla (YouTube, Twitch y otras webs)."),
       ("Sal de pantalla completa del monitor 3", "Vuelve el vídeo a su tamaño normal."),
       ("Modo teatro / Sal del modo cine", "Pone o quita el modo teatro del reproductor."),
       ("Silencia la pestaña que suena / Mutea el monitor 3", "Quita el sonido de una pestaña del navegador."),
       ("Devuélvele el sonido al vídeo", "Le devuelve el sonido a la pestaña silenciada."),
       ("¿Qué está sonando?", "Dice qué canción o vídeo se está reproduciendo."),
       ("Busca en YouTube música de los 80", "Busca en YouTube y puede reproducir un resultado."),
       ("Pon el segundo resultado", "Reproduce otro resultado de la última búsqueda.")),
    _s("🖥", "Monitores",
       ("Desactiva el monitor 3", "Apaga esa pantalla para Windows."),
       ("Extiende el monitor 3", "Vuelve a usar esa pantalla como escritorio extendido."),
       ("Duplica los monitores 1 y 3", "Muestra lo mismo en las dos pantallas."),
       ("Pon el monitor 3 como principal", "Mueve la barra de tareas y los iconos a esa pantalla."),
       ("Arregla el escalado del monitor 3", "Corrige la barra de tareas mal ubicada en la TV."),
       ("Manda Brave a la TV / Pon Discord en el monitor 2", "Mueve la ventana de un programa a otra pantalla."),
       ("Trae todo al monitor 1", "Junta todas las ventanas en una pantalla.")),
    _s("🗔", "Programas, juegos y ventanas",
       ("Abre WhatsApp / Abre la calculadora", "Abre cualquier aplicación por su nombre."),
       ("Abre Elden Ring", "Abre un juego de tu biblioteca."),
       ("Minimiza Discord / Maximiza Spotify", "Achica o agranda la ventana de un programa."),
       ("Trae WhatsApp al frente", "Pone esa ventana delante de las demás."),
       ("Muestra el escritorio", "Minimiza todas las ventanas.")),
    _s("🌐", "Navegador",
       ("Abre una pestaña", "Abre una pestaña nueva en Brave."),
       ("Abre youtube.com", "Va a esa página web."),
       ("Página anterior / Página siguiente", "Navega hacia atrás o adelante."),
       ("Recarga la página / F5", "Vuelve a cargar la página.")),
    _s("🖱", "Pantalla",
       ("Haz clic en Aceptar en el monitor 2", "Jarvis busca el botón, te muestra dónde y hace clic si dices «sí»."),
       ("Escribe \"hola\" en el buscador del monitor 3", "Hace clic en el buscador y escribe el texto."),
       ("Mira el monitor 2 y dime qué error sale", "Mira la pantalla y te explica lo que ve.")),
    _s("⏰", "Recordatorios y temporizadores",
       ("Pon un temporizador de 10 minutos", "Avisa cuando pasa ese tiempo."),
       ("Recuérdame en 20 minutos sacar la ropa", "Te avisa a esa hora con el texto que digas."),
       ("Recuérdame mañana a las 9 pagar la luz", "Recordatorio para otro día."),
       ("Pon una alarma a las 7", "Alarma a esa hora."),
       ("¿Qué recordatorios tengo?", "Lista los avisos pendientes."),
       ("Cancela el temporizador", "Borra un temporizador o recordatorio.")),
    _s("⏱", "Rutinas",
       ("Programa una rutina llamada Inicio a las 09:00 y pon el volumen al 30 %",
        "Crea una rutina que se ejecuta sola a esa hora."),
       ("Cuando conecte los auriculares, cambia la salida a auriculares", "Rutina que se activa al conectar los auriculares."),
       ("Cuando abra un juego, cambia la salida a auriculares", "Rutina que se activa al abrir o cerrar un juego."),
       ("Desactiva (nombre de tu rutina)", "Si la rutina cambió los monitores, los deja como estaban antes.")),
    _s("⏻", "Energía",
       ("Apaga el equipo / Reinicia la PC", "Apaga o reinicia (siempre pregunta antes)."),
       ("Suspende el equipo", "Pone la PC en suspensión."),
       ("Suspende el equipo dentro de 10 minutos / Apaga la PC a las 2",
        "Lo programa para más tarde; al llegar la hora avisa 60 s antes y se puede cancelar."),
       ("Bloquea la pantalla", "Bloquea la sesión de Windows."),
       ("Cancela el apagado", "Frena un apagado o reinicio en curso.")),
    _s("📊", "Consultas",
       ("¿Qué hora es? / ¿Qué día es hoy?", "Dice la hora o la fecha."),
       ("¿Cómo está el equipo?", "Uso de CPU, memoria, discos y temperaturas."),
       ("¿Qué aplicaciones tengo abiertas?", "Lista los programas abiertos."),
       ("¿Qué consume más memoria?", "Muestra los programas que más gastan."),
       ("¿Qué tiempo hace? / Clima en Córdoba", "Dice el clima de tu ciudad o de otra."),
       ("Mi ciudad es Córdoba", "Guarda tu ciudad para el clima."),
       ("Diagnóstico", "Estado de Jarvis, sus servicios y sus últimas acciones.")),
    _s("🧹", "Limpieza",
       ("Libera espacio", "Muestra cuánto se puede liberar y borra lo que marques."),
       ("Vacía la papelera / Borra los archivos temporales", "Limpia solo esa parte.")),
    _s("🧠", "Memoria y conversación",
       ("Recuerda que prefiero Brave", "Guarda un dato sobre ti."),
       ("¿Qué recuerdas sobre mi navegador?", "Busca entre lo que recuerda."),
       ("¿Qué aprendiste de mí?", "Lista lo que aprendió solo en las conversaciones."),
       ("Desactiva el aprendizaje automático", "Deja de aprender solo."),
       ("Borra el historial de conversaciones", "Elimina los chats guardados (pregunta antes).")),
    _s("🎨", "Apariencia y sistema",
       ("Cambia al tema rojo / Pon el modo claro", "Cambia los colores de Jarvis."),
       ("Modo compacto", "Deja solo el orbe en una ventanita siempre visible."),
       ("Desactiva el inicio con Windows", "Jarvis deja de abrirse al encender la PC."),
       ("Aplica el undervolt / ¿Está aplicado el undervolt?", "Reaplica o revisa el perfil de la GPU.")),
)

AYUDA_IA = ("Si dices algo que no está en la lista, Jarvis lo interpreta con la IA y te propone "
            "un plan antes de hacerlo. Puedes pedir varias cosas en una sola frase.")


def tus_comandos(rutinas: Iterable, describir=None) -> Seccion | None:
    """Sección «Tus comandos» con las frases de las rutinas activas por frase.

    ``rutinas`` son ``RoutineInfo`` (o algo con ``name``, ``phrases``, ``steps``,
    ``active`` y ``trigger``); ``describir(herramienta, argumentos)`` pasa cada paso a
    lenguaje llano (por defecto, ``jarvis_cards.describir_paso``).
    """
    from jarvis_routines import DISPARADOR_FRASE

    if describir is None:
        from jarvis_cards import describir_paso as describir
    comandos = []
    for rutina in rutinas:
        if not getattr(rutina, "active", True) or rutina.trigger != DISPARADOR_FRASE or not rutina.phrases:
            continue
        pasos = []
        for paso in rutina.steps:
            texto = describir(paso.tool, dict(paso.arguments)) or paso.tool
            pasos.append(texto)
        if any(paso.tool == "configure_display" for paso in rutina.steps):
            pasos.append(f"Para deshacerlo: «Desactiva {rutina.phrases[0]}»")
        detalle = f"Rutina «{rutina.name}»: " + ("; ".join(pasos) if pasos else "sin pasos")
        comandos.append(Comando(" / ".join(rutina.phrases), detalle))
    if not comandos:
        return None
    return Seccion("⭐", "Tus comandos (rutinas)", tuple(comandos))


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in sin_tildes if not unicodedata.combining(c))


def filtrar(secciones: Iterable[Seccion], busqueda: str) -> list[Seccion]:
    """Secciones con solo los comandos que contienen todas las palabras buscadas
    (sin distinguir tildes ni mayúsculas). Si el título coincide, la sección entera."""
    palabras = _normalizar(busqueda).split()
    if not palabras:
        return list(secciones)
    resultado = []
    for seccion in secciones:
        if all(p in _normalizar(seccion.titulo) for p in palabras):
            resultado.append(seccion)
            continue
        quedan = tuple(c for c in seccion.comandos
                       if all(p in _normalizar(f"{c.frase} {c.para_que}") for p in palabras))
        if quedan:
            resultado.append(Seccion(seccion.icono, seccion.titulo, quedan))
    return resultado
