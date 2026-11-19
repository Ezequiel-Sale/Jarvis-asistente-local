"""Doctor de la PC: lee los registros de Windows y responde «¿por qué se colgó anoche?»,
«¿está sano el disco?» o «¿hubo errores?», sin tocar nada.

Los eventos se leen con ``Get-WinEvent`` por PowerShell (sin dependencias) y se interpretan
aquí, en Python, para poder probarlo sin el sistema. Ojo: el número de evento depende de quién
lo escribe (el 55 de Kernel-Processor-Power es otra cosa que el 55 de Ntfs), así que cada
evento que importa se identifica por proveedor y número.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

TIEMPO = 45
MAX_EVENTOS = 400


@dataclass(frozen=True)
class Evento:
    momento: datetime
    proveedor: str
    id: int
    mensaje: str = ""
    dato: str = ""      # primer dato del evento (p. ej. el programa que se cerró)
    nivel: int = 0      # 1 crítico, 2 error, 3 advertencia


# (proveedor en minúsculas, id) → qué significa, en palabras.
APAGADO_INESPERADO = {("microsoft-windows-kernel-power", 41), ("eventlog", 6008)}
PANTALLA_AZUL = {("microsoft-windows-wer-systemerrorreporting", 1001), ("bugcheck", 1001)}
APAGADO_PEDIDO = {("user32", 1074)}
VIDEO = {("display", 4101)}
HARDWARE = {("microsoft-windows-whea-logger", i) for i in (1, 17, 18, 19, 20, 46, 47)}
DISCO = ({("disk", i) for i in (7, 11, 51, 52, 153, 154)} | {("ntfs", 55), ("ntfs", 98), ("microsoft-windows-ntfs", 55),
         ("microsoft-windows-ntfs", 98), ("stornvme", 11), ("storahci", 129)})
PROGRAMA_CERRADO = {("application error", 1000)}
PROGRAMA_COLGADO = {("application hang", 1002)}

_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$desde = (Get-Date).AddHours(-[int]$env:JARVIS_HORAS)
$salida = @()
foreach ($log in @('System', 'Application')) {
    $eventos = Get-WinEvent -FilterHashtable @{LogName=$log; StartTime=$desde; Level=1,2,3} -MaxEvents ([int]$env:JARVIS_MAX)
    $eventos += Get-WinEvent -FilterHashtable @{LogName=$log; StartTime=$desde; Id=1074,6005,6006} -MaxEvents 50
    foreach ($e in $eventos) {
        $dato = ''
        if ($e.Properties.Count -gt 0) { $dato = [string]$e.Properties[0].Value }
        $mensaje = ''
        if ($e.Message) { $mensaje = ($e.Message -split "`r?`n")[0] }
        $salida += [pscustomobject]@{ t = $e.TimeCreated.ToString('s'); p = $e.ProviderName; i = $e.Id;
                                      n = [int]$e.Level; m = $mensaje; d = $dato }
    }
}
$salida | ConvertTo-Json -Compress -Depth 2
"""

_SCRIPT_DISCOS = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$salida = @()
foreach ($d in Get-PhysicalDisk) {
    $c = $d | Get-StorageReliabilityCounter
    $salida += [pscustomobject]@{ nombre = $d.FriendlyName; tipo = [string]$d.MediaType; salud = [string]$d.HealthStatus;
        estado = [string]($d.OperationalStatus -join ', '); temp = $c.Temperature; desgaste = $c.Wear;
        lectura = $c.ReadErrorsUncorrected; escritura = $c.WriteErrorsUncorrected; horas = $c.PowerOnHours }
}
$salida | ConvertTo-Json -Compress -Depth 2
"""


def _powershell(script: str, entorno: dict[str, str] | None = None) -> object:
    comando = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    salida = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", comando],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIEMPO,
        env={**os.environ, **(entorno or {})}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    texto = (salida.stdout or "").strip()
    if not texto:
        return []
    datos = json.loads(texto)
    return datos if isinstance(datos, list) else [datos]


def leer_eventos(horas: int) -> list[Evento]:
    crudos = _powershell(_SCRIPT, {"JARVIS_HORAS": str(int(horas)), "JARVIS_MAX": str(MAX_EVENTOS)})
    eventos = {}
    for c in crudos:
        try:
            e = Evento(datetime.fromisoformat(c["t"]), str(c.get("p") or ""), int(c["i"]),
                       str(c.get("m") or "")[:200], str(c.get("d") or "")[:120], int(c.get("n") or 0))
        except (KeyError, TypeError, ValueError):
            continue
        eventos[(e.momento, e.proveedor, e.id)] = e
    return sorted(eventos.values(), key=lambda e: e.momento)


def _clave(e: Evento) -> tuple[str, int]:
    return (e.proveedor.casefold(), e.id)


def _hora(momento: datetime, ahora: datetime) -> str:
    if momento.date() == ahora.date():
        dia = "hoy"
    elif momento.date() == (ahora - timedelta(days=1)).date():
        dia = "ayer"
    else:
        dia = f"el {momento.day}/{momento.month}"
    return f"{dia} a las {momento:%H:%M}"


def _programa(dato: str) -> str:
    return os.path.basename(dato.strip()) or "un programa"


def diagnosticar_cuelgues(eventos: list[Evento], ahora: datetime | None = None) -> str:
    """¿Se apagó o reinició sin querer? ¿Por qué? Mira lo que pasó en los 10 min anteriores."""
    ahora = ahora or datetime.now()
    caidas = [e for e in eventos if _clave(e) in APAGADO_INESPERADO | PANTALLA_AZUL]
    pedidos = [e for e in eventos if _clave(e) in APAGADO_PEDIDO]
    if not caidas:
        texto = "No encontré apagados ni reinicios inesperados en ese período."
        if pedidos:
            ultimo = pedidos[-1]
            texto += f" El último apagado o reinicio fue pedido ({_hora(ultimo.momento, ahora)}"
            texto += f", por {_programa(ultimo.dato)})." if ultimo.dato else ")."
        colgados = [e for e in eventos if _clave(e) in PROGRAMA_COLGADO | PROGRAMA_CERRADO]
        if colgados:
            texto += " " + resumir_programas(colgados)
        return texto
    # Agrupa caídas a menos de 15 min (Kernel-Power 41 y EventLog 6008 son el mismo episodio).
    episodios: list[list[Evento]] = []
    for e in caidas:
        if episodios and e.momento - episodios[-1][-1].momento <= timedelta(minutes=15):
            episodios[-1].append(e)
        else:
            episodios.append([e])
    partes = []
    for episodio in episodios[-3:]:
        inicio = episodio[0].momento
        antes = [e for e in eventos if inicio - timedelta(minutes=10) <= e.momento <= inicio + timedelta(minutes=1)]
        azul = any(_clave(e) in PANTALLA_AZUL for e in episodio)
        frase = f"{_hora(inicio, ahora).capitalize()} el equipo "
        frase += "tuvo una pantalla azul" if azul else "se apagó o reinició sin cerrar bien"
        causas = []
        if any(_clave(e) in VIDEO for e in antes):
            causas.append("antes el controlador de video dejó de responder (posible GPU o driver)")
        if any(_clave(e) in HARDWARE for e in antes):
            causas.append("Windows registró un error de hardware (WHEA)")
        if any(_clave(e) in DISCO for e in antes):
            causas.append("hubo errores de disco")
        colgados = [e for e in antes if _clave(e) in PROGRAMA_COLGADO | PROGRAMA_CERRADO]
        if colgados:
            causas.append(f"se había colgado {_programa(colgados[-1].dato)}")
        if causas:
            frase += ": " + "; ".join(causas) + "."
        elif not azul:
            frase += ". No hay errores justo antes: suele ser un corte de luz, la fuente o un apagado forzado con el botón."
        else:
            frase += "."
        partes.append(frase)
    total = len(episodios)
    cabecera = f"Encontré {total} {'caída' if total == 1 else 'caídas'} inesperada{'s' if total != 1 else ''}. "
    return cabecera + " ".join(partes)


def resumir_programas(eventos: list[Evento]) -> str:
    cerrados = Counter(_programa(e.dato) for e in eventos if _clave(e) in PROGRAMA_CERRADO)
    colgados = Counter(_programa(e.dato) for e in eventos if _clave(e) in PROGRAMA_COLGADO)
    partes = []
    if cerrados:
        partes.append("Se cerraron con error: " + ", ".join(f"{n} ({c})" if c > 1 else n for n, c in cerrados.most_common(5)))
    if colgados:
        partes.append("Dejaron de responder: " + ", ".join(f"{n} ({c})" if c > 1 else n for n, c in colgados.most_common(5)))
    return ". ".join(partes) + "." if partes else ""


def resumir_errores(eventos: list[Evento]) -> str:
    """Los errores que importan (no el ruido de siempre), agrupados."""
    importantes = []
    for nombre, claves in (("errores de hardware (WHEA)", HARDWARE), ("errores de disco", DISCO),
                           ("cuelgues del controlador de video", VIDEO), ("pantallas azules", PANTALLA_AZUL),
                           ("apagados inesperados", APAGADO_INESPERADO)):
        cuantos = sum(1 for e in eventos if _clave(e) in claves)
        if claves is APAGADO_INESPERADO:  # Kernel-Power 41 y EventLog 6008 son la misma caída
            cuantos = max(sum(1 for e in eventos if _clave(e) == c) for c in claves)
        if cuantos:
            importantes.append(f"{cuantos} {nombre}")
    texto = ("Lo importante: " + ", ".join(importantes) + ".") if importantes else "No hay errores de hardware, disco, video ni caídas."
    programas = resumir_programas(eventos)
    if programas:
        texto += " " + programas
    ruido = Counter(e.proveedor for e in eventos if e.nivel in (1, 2)
                    and _clave(e) not in HARDWARE | DISCO | VIDEO | PANTALLA_AZUL | APAGADO_INESPERADO
                    | PROGRAMA_CERRADO | PROGRAMA_COLGADO)
    if ruido:
        texto += " Otros errores frecuentes: " + ", ".join(f"{p} ({c})" for p, c in ruido.most_common(3)) + "."
    return texto


def diagnosticar_discos(discos: list[dict], eventos: list[Evento]) -> str:
    if not discos:
        return "No pude leer el estado de los discos."
    partes = []
    alertas = 0
    for d in discos:
        nombre = str(d.get("nombre") or "disco")
        problemas = []
        if str(d.get("salud")) not in ("Healthy", "0"):
            problemas.append(f"Windows lo marca como {d.get('salud')}")
        for campo, texto in (("lectura", "errores de lectura sin corregir"), ("escritura", "errores de escritura sin corregir")):
            if isinstance(d.get(campo), int) and d[campo] > 0:
                problemas.append(f"{d[campo]} {texto}")
        if isinstance(d.get("desgaste"), int) and d["desgaste"] >= 80:
            problemas.append(f"desgaste del {d['desgaste']} %")
        if isinstance(d.get("temp"), int) and d["temp"] >= 60:
            problemas.append(f"está a {d['temp']} °C")
        extra = []
        if isinstance(d.get("temp"), int) and 0 < d["temp"] < 60:
            extra.append(f"{d['temp']} °C")
        if isinstance(d.get("desgaste"), int) and 0 < d["desgaste"] < 80:
            extra.append(f"desgaste {d['desgaste']} %")
        if problemas:
            alertas += 1
            partes.append(f"{nombre}: ojo, {', '.join(problemas)}.")
        else:
            partes.append(f"{nombre}: sano" + (f" ({', '.join(extra)})" if extra else "") + ".")
    errores_disco = [e for e in eventos if _clave(e) in DISCO]
    if errores_disco:
        partes.append(f"Además, Windows registró {len(errores_disco)} errores de disco en ese período.")
    cabecera = "Los discos están bien. " if not alertas and not errores_disco else "Revisá los discos. "
    return cabecera + " ".join(partes)


def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    return " ".join(re.sub(r"[^\w\s]", " ", "".join(c for c in texto if not unicodedata.combining(c))).split())


def consultar(question: str, hours: int = 48) -> str:
    """Handler de ``pc_doctor``: crashes | disks | errors."""
    horas = max(1, min(int(hours or 48), 24 * 30))
    if question == "disks":
        return diagnosticar_discos(_powershell(_SCRIPT_DISCOS), leer_eventos(max(horas, 24 * 7)))
    eventos = leer_eventos(horas)
    if question == "crashes":
        return diagnosticar_cuelgues(eventos)
    if question == "errors":
        return resumir_errores(eventos)
    raise ValueError("question debe ser crashes, disks o errors.")


# --- Frases directas ----------------------------------------------------------------------

_PREFIJO = r"^(?:(?:hey|oye)\s+)?(?:jarvis\s+)?(?:(?:me\s+)?(?:podes|puedes|podrias)\s+(?:decir|fijarte|revisar)\s+)?"
_CUELGUE = re.compile(_PREFIJO + r"(?:por\s*que|que\s+paso\s+que)\s+(?:se\s+)?(?:colgo|tildo|apago|reinicio|trabo|cayo|"
                      r"crasheo)\s*(?:la\s+pc|la\s+compu\w*|el\s+equipo|windows)?"
                      r"(?:\s+(?:anoche|ayer|hoy|recien|esta\s+manana|esta\s+noche|antes))?$")
_DISCOS = re.compile(_PREFIJO + r"(?:esta|estan|como\s+esta|como\s+estan)\s+(?:sano|sanos|bien)?\s*"
                     r"(?:el\s+disco|los\s+discos|el\s+ssd|el\s+disco\s+duro)(?:\s+(?:sano|sanos|bien))?$")
_ERRORES = re.compile(_PREFIJO + r"(?:hubo|hay|tuve|tuvo)\s+(?:algun\s+)?(?:errores?|problemas?|fallas?)"
                      r"(?:\s+en\s+(?:la\s+pc|la\s+compu\w*|el\s+equipo|windows))?"
                      r"(?:\s+(?:hoy|ayer|anoche|esta\s+semana|ultimamente))?$")


def detectar(texto: str) -> tuple[str, dict[str, object]] | None:
    consulta = _normal(texto or "")
    if _CUELGUE.match(consulta):
        return ("pc_doctor", {"question": "crashes", "hours": 24 * 7 if "semana" in consulta else 48})
    if _DISCOS.match(consulta):
        return ("pc_doctor", {"question": "disks"})
    if _ERRORES.match(consulta):
        return ("pc_doctor", {"question": "errors", "hours": 24 * 7 if "semana" in consulta else 24})
    return None
