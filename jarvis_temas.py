"""Herramientas por tema: al modelo se le ofrecen solo las que pueden hacer falta.

Cada herramienta ocupa contexto y puede distraer al elegir. Las de este catálogo se ofrecen si la
frase (o la anterior del usuario, para «¿y la otra?» o «sí, hacelo») toca su tema; las demás,
siempre. Es una optimización, no un permiso: el registro sigue conociéndolas todas.
"""

import re
from collections.abc import Sequence
from typing import Any

from jarvis_screen_actions import normalizar

PANTALLA = "pantalla"
AJUSTES = "ajustes"
DOCTOR = "doctor"
SCRIPTS = "scripts"
ARCHIVOS = "archivos"
PROGRAMAS = "programas"
DOCUMENTOS = "documentos"
PAGINA = "pagina"
WHATSAPP = "whatsapp"
CORREO = "correo"
SISTEMA = "sistema"

# Medido 2026-11-19 (bonsai-2:27b): ocultar las de tele, GPU, monitores y rutinas empeoró casos ajenos
# («Abrí una pestaña nueva y andá a github.com»: 3/3 bien con todas, 0/3 sin ellas). Por eso solo se
# ocultan las de pantalla, que son nuevas; antes de sumar otro tema, compáralo con el examen.
TEMAS: dict[str, tuple[frozenset[str], re.Pattern[str]]] = {
    PANTALLA: (
        frozenset({"read_window", "read_screen_text", "window_action", "type_text", "press_keys",
                   "replay_recording"}),
        re.compile(r"\b(?:ventanas?|pantalla|botone?s?|casillas?|menu|campos?|formulario|opcion(?:es)?|"
                   r"clic|click|escrib\w+|tipe\w+|teclea\w*|teclas?|atajos?|apret\w+|apriet\w+|presion\w+|"
                   r"puls\w+|marc[aá]\w*|desmarc\w+|complet\w+|rellen\w+|corrig\w+|correg\w+|"
                   r"lee(?:me)?|lei|leer|leeme|que\s+dice|que\s+hay\s+en|repet\w*|grab\w*|tarea)\b"),
    ),
    AJUSTES: (
        frozenset({"windows_setting", "startup_apps", "open_settings", "network_adapter"}),
        re.compile(r"\b(?:luz\s+nocturna|modo\s+(?:oscuro|claro)|tema\s+(?:oscuro|claro)|wi\s*fi|bluetooth|"
                   r"inicio|arranqu\w*|arranca\w*|configuracion|ajustes?|windows|notificaciones|ethernet|cable|placa|red|"
                   r"predeterminad\w*|impresora\w*|actualizacion\w*|energia|bateria)\b"),
    ),
    DOCTOR: (
        frozenset({"pc_doctor"}),
        re.compile(r"\b(?:colg\w*|cuelg\w*|tild\w*|crash\w*|reinici\w*|apago|apagon|se\s+apag\w*|errore?s?|"
                   r"fallas?|discos?|ssd|sano|salud|pantallazo|pantalla\s+azul|problemas?|registros?|eventos?)\b"),
    ),
    SCRIPTS: (
        frozenset({"run_powershell"}),
        re.compile(r"\b(?:scripts?|powershell|comandos?|terminal|consola|cmd)\b"),
    ),
    ARCHIVOS: (
        frozenset({"file_action", "organize_folder", "clipboard_history"}),
        re.compile(r"\b(?:archivos?|carpetas?|mov\w*|renombr\w*|cambia\w*\s+(?:el\s+)?nombre|copi\w*|"
                   r"comprim\w*|zip|descomprim\w*|papelera|descargas|documentos|escritorio|orden\w*|organiz\w*|"
                   r"duplicados|portapapeles|peg\w*|pdf|fotos?|videos?|primero|segundo|tercero)\b"),
    ),
    PROGRAMAS: (
        frozenset({"search_programs", "install_program", "uninstall_program", "list_program_updates", "update_programs"}),
        re.compile(r"\b(?:instal\w*|desinstal\w*|actualiz\w*|winget|programas?|aplicacion(?:es)?|apps?|"
                   r"version(?:es)?)\b"),
    ),
    DOCUMENTOS: (
        frozenset({"ask_document", "search_documents"}),
        re.compile(r"\b(?:pdf|documentos?|word|docx|resum\w*|informe|contrato|apuntes|archivos?|"
                   r"que\s+dice|de\s+que\s+(?:trata|habla)|primero|segundo|tercero)\b"),
    ),
    PAGINA: (
        frozenset({"browser_read_page", "browser_page_action"}),
        re.compile(r"\b(?:pagina|paginas|web|sitio|formulario|enlaces?|links?|botone?s?|brave|pestana|"
                   r"navegador|campos?|complet\w*|rellen\w*|articulo|noticia|precio|resultados?)\b"),
    ),
    WHATSAPP: (
        frozenset({"whatsapp_unread", "whatsapp_read", "whatsapp_send"}),
        re.compile(r"\b(?:whats\s*app|wasap|guasap|mensajes?|chats?|escribio|escribieron|mandale|enviale|escribile|"
                   r"decile|contesta\w*|responde\w*)\b"),
    ),
    SISTEMA: (
        frozenset({"screenshot", "screen_record", "service_status", "service_control", "process_control"}),
        re.compile(r"\b(?:captur\w*|screenshot|pantallazo|grab\w*|servicios?|procesos?|colgad\w*|"
                   r"responde\w*|prioridad|forz\w*|mata\w*)\b"),
    ),
    CORREO: (
        frozenset({"email_unread", "email_read", "email_send", "calendar_events", "calendar_create", "connect_account"}),
        re.compile(r"\b(?:correos?|mails?|emails?|gmail|outlook|hotmail|google|calendario|agenda\w*|eventos?|"
                   r"reunion(?:es)?|turnos?|citas?|cumpleanos|que\s+tengo)\b"),
    ),
}


def temas_de(texto: str, previo: str = "") -> set[str]:
    """Temas que toca la frase o la anterior del usuario."""
    consulta = normalizar(f"{texto} {previo}")
    return {nombre for nombre, (_, patron) in TEMAS.items() if patron.search(consulta)}


def elegir(tools: Sequence[dict[str, Any]], texto: str, previo: str = "") -> list[dict[str, Any]]:
    """Las herramientas para esta frase, en el mismo orden del registro."""
    activos = temas_de(texto, previo)
    ocultas: set[str] = set()
    for nombre, (herramientas, _) in TEMAS.items():
        if nombre not in activos:
            ocultas |= herramientas
    return [t for t in tools if t.get("function", {}).get("name") not in ocultas]
