"""Ajustes de Windows con herramientas propias: luz nocturna, modo oscuro, Wi-Fi, Bluetooth,
programas que arrancan con Windows y páginas de Configuración.

Antes (2026-11-19) estos pedidos iban al piloto, que tardaba 7–15 s manejando Configuración y a
veces erraba el botón. Aquí cada uno tarda menos de un segundo, se verifica y se puede deshacer.
«No molestar», modo avión, fondo de pantalla y ahorro de batería no tienen una vía confiable:
siguen con el piloto (``jarvis_piloto._AJUSTE``). Las apps predeterminadas tampoco: Windows protege
esa elección con una firma, así que solo se abre su página.
"""

from __future__ import annotations

import base64
import ctypes
import logging
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

AJUSTES = ("night_light", "dark_mode", "wifi", "bluetooth")
NOMBRES = {"night_light": "la luz nocturna", "dark_mode": "el modo oscuro", "wifi": "el Wi-Fi",
           "bluetooth": "el Bluetooth"}
TIEMPO_RADIO = 20

# --- Luz nocturna ---------------------------------------------------------------------------
# Windows la guarda en un blob de CloudStore. Formato observado (41 bytes apagada, 43 encendida):
# el byte 18 es 0x13 (apagada) o 0x15 (encendida) y, encendida, lleva «10 00» tras «43 42 01 00»
# (posición 23). Los bytes 10–14 son una marca de tiempo: si no cambia, Windows ignora el cambio.
_CLAVE_LUZ = (r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
              r"\default$windows.data.bluelightreduction.bluelightreductionstate"
              r"\windows.data.bluelightreduction.bluelightreductionstate")
_APAGADA, _ENCENDIDA = 0x13, 0x15


def luz_activa(datos: bytes) -> bool:
    if len(datos) < 25 or datos[18] not in (_APAGADA, _ENCENDIDA):
        raise ValueError("No reconozco el formato de la luz nocturna de esta versión de Windows.")
    return datos[18] == _ENCENDIDA


def blob_luz(datos: bytes, activar: bool) -> bytes:
    """El blob con la luz nocturna en ``activar`` (y la marca de tiempo adelantada)."""
    if luz_activa(datos) == activar:
        return bytes(datos)
    nuevo = bytearray(datos)
    if activar:
        nuevo[23:23] = b"\x10\x00"
        nuevo[18] = _ENCENDIDA
    else:
        if nuevo[23:25] == b"\x10\x00":
            del nuevo[23:25]
        nuevo[18] = _APAGADA
    for i in range(10, 15):  # varint: se suma 1 al primer byte que no esté al tope
        if nuevo[i] & 0x7F != 0x7F:
            nuevo[i] += 1
            break
    return bytes(nuevo)


def _leer_valor(clave: str, nombre: str, raiz=None):
    import winreg

    with winreg.OpenKey(raiz or winreg.HKEY_CURRENT_USER, clave) as k:
        return winreg.QueryValueEx(k, nombre)[0]


def _escribir_valor(clave: str, nombre: str, valor, tipo, raiz=None) -> None:
    import winreg

    with winreg.CreateKeyEx(raiz or winreg.HKEY_CURRENT_USER, clave, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, nombre, 0, tipo, valor)


def _luz(activar: bool | None) -> bool:
    import winreg

    datos = bytes(_leer_valor(_CLAVE_LUZ, "Data"))
    if activar is None:
        return luz_activa(datos)
    _escribir_valor(_CLAVE_LUZ, "Data", blob_luz(datos, activar), winreg.REG_BINARY)
    return activar


# --- Modo oscuro ----------------------------------------------------------------------------
_CLAVE_TEMA = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def _avisar_cambio_de_tema() -> None:
    """WM_SETTINGCHANGE «ImmersiveColorSet»: las ventanas abiertas toman el tema sin reiniciar."""
    resultado = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 1000,
                                             ctypes.byref(resultado))


def _oscuro(activar: bool | None) -> bool:
    import winreg

    if activar is None:
        return int(_leer_valor(_CLAVE_TEMA, "AppsUseLightTheme")) == 0
    claro = 0 if activar else 1
    for nombre in ("AppsUseLightTheme", "SystemUsesLightTheme"):
        _escribir_valor(_CLAVE_TEMA, nombre, claro, winreg.REG_DWORD)
    _avisar_cambio_de_tema()
    return activar


# --- Wi-Fi y Bluetooth (Windows.Devices.Radios por PowerShell, sin dependencias) -------------
_SCRIPT_RADIO = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Wait-Op($op, $tipo) { $t = $asTask.MakeGenericMethod($tipo).Invoke($null, @($op)); $t.Wait(); $t.Result }
$null = [Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime]
$null = Wait-Op ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
$radios = Wait-Op ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$radio = $radios | Where-Object { $_.Kind -eq $env:JARVIS_RADIO } | Select-Object -First 1
if ($null -eq $radio) { Write-Output 'SIN_RADIO'; exit 0 }
if ($env:JARVIS_ESTADO -ne 'leer') {
    $r = Wait-Op ($radio.SetStateAsync($env:JARVIS_ESTADO)) ([Windows.Devices.Radios.RadioAccessStatus])
    if ($r -ne 'Allowed') { Write-Output "DENEGADO $r"; exit 0 }
}
Write-Output $radio.State
"""


def _correr_radio(tipo: str, estado: str) -> str:
    comando = base64.b64encode(_SCRIPT_RADIO.encode("utf-16-le")).decode("ascii")
    entorno = {**os.environ, "JARVIS_RADIO": tipo, "JARVIS_ESTADO": estado}
    salida = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", comando],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIEMPO_RADIO,
        env=entorno, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    texto = (salida.stdout or "").strip().splitlines()
    return texto[-1].strip() if texto else ""


def _radio(tipo: str, activar: bool | None) -> bool:
    estado = _correr_radio(tipo, "leer" if activar is None else ("On" if activar else "Off"))
    if estado == "SIN_RADIO":
        raise RuntimeError(f"Esta PC no tiene {'Wi-Fi' if tipo == 'WiFi' else 'Bluetooth'}.")
    if estado.startswith("DENEGADO"):
        raise PermissionError("Windows no dejó cambiar la radio (revisá Configuración › Privacidad › Radios).")
    if estado not in ("On", "Off"):
        raise RuntimeError("No pude leer el estado de la radio.")
    return estado == "On"


_LECTORES = {
    "night_light": _luz,
    "dark_mode": _oscuro,
    "wifi": lambda activar: _radio("WiFi", activar),
    "bluetooth": lambda activar: _radio("Bluetooth", activar),
}


def estado(setting: str) -> bool:
    """¿Está activado? (para deshacer y verificar)."""
    if setting not in _LECTORES:
        raise ValueError(f"Ajuste desconocido: {setting}.")
    return bool(_LECTORES[setting](None))


def cambiar(setting: str, state: str) -> str:
    """Handler de ``windows_setting``: state = on | off."""
    if setting not in _LECTORES:
        raise ValueError(f"Ajuste desconocido: {setting}.")
    if state not in ("on", "off"):
        raise ValueError("state debe ser on u off.")
    activar = state == "on"
    nombre = NOMBRES[setting]
    if estado(setting) == activar:
        return f"{nombre[0].upper()}{nombre[1:]} ya estaba {'activado' if activar else 'desactivado'}."
    _LECTORES[setting](activar)
    return f"Listo: {'activé' if activar else 'desactivé'} {nombre}."


# --- Programas que arrancan con Windows ----------------------------------------------------

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
_APROBADOS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"


@dataclass(frozen=True)
class Inicio:
    nombre: str
    comando: str
    activo: bool
    raiz: str          # "HKCU" | "HKLM": dónde está su marca de aprobado
    aprobado: str      # subclave de StartupApproved (Run, Run32, StartupFolder)
    valor: str         # nombre del valor en esa subclave


def activo_segun(marca: bytes | None) -> bool:
    """StartupApproved: sin marca = activo; primer byte par (02, 06) = activo, impar (03, 07) = no."""
    return marca is None or len(marca) == 0 or marca[0] % 2 == 0


def _raiz(nombre: str):
    import winreg

    return winreg.HKEY_CURRENT_USER if nombre == "HKCU" else winreg.HKEY_LOCAL_MACHINE


def _valores(raiz: str, clave: str) -> dict[str, object]:
    import winreg

    try:
        with winreg.OpenKey(_raiz(raiz), clave) as k:
            salida = {}
            i = 0
            while True:
                try:
                    nombre, valor, _tipo = winreg.EnumValue(k, i)
                except OSError:
                    return salida
                salida[nombre] = valor
                i += 1
    except OSError:
        return {}


def _carpetas_inicio() -> list[tuple[str, Path]]:
    appdata, comun = os.environ.get("APPDATA", ""), os.environ.get("PROGRAMDATA", "")
    menu = Path("Microsoft/Windows/Start Menu/Programs/Startup")
    return [("HKCU", Path(appdata) / menu), ("HKLM", Path(comun) / menu)]


def listar_inicio() -> list[Inicio]:
    programas: list[Inicio] = []
    for raiz, clave, aprobado in (("HKCU", _RUN, "Run"), ("HKLM", _RUN, "Run"), ("HKLM", _RUN32, "Run32")):
        marcas = _valores(raiz, f"{_APROBADOS}\\{aprobado}")
        for nombre, comando in _valores(raiz, clave).items():
            marca = marcas.get(nombre)
            programas.append(Inicio(nombre, str(comando), activo_segun(marca if isinstance(marca, bytes) else None),
                                    raiz, aprobado, nombre))
    for raiz, carpeta in _carpetas_inicio():
        marcas = _valores(raiz, f"{_APROBADOS}\\StartupFolder")
        try:
            archivos = sorted(p for p in carpeta.iterdir() if p.suffix.lower() in {".lnk", ".url", ".exe", ".bat"})
        except OSError:
            continue
        for archivo in archivos:
            marca = marcas.get(archivo.name)
            programas.append(Inicio(archivo.stem, str(archivo), activo_segun(marca if isinstance(marca, bytes) else None),
                                    raiz, "StartupFolder", archivo.name))
    return programas


def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    return " ".join(re.sub(r"[^\w\s]", " ", "".join(c for c in texto if not unicodedata.combining(c))).split())


def elegir_inicio(programas: list[Inicio], nombre: str) -> Inicio:
    clave = _normal(nombre)
    if not clave:
        raise ValueError("Decime qué programa.")
    exactos = [p for p in programas if _normal(p.nombre) == clave]
    parecidos = exactos or [p for p in programas if clave in _normal(p.nombre) or clave in _normal(p.comando)]
    if not parecidos:
        raise LookupError(f"«{nombre}» no está entre los programas que arrancan con Windows.")
    if len({p.nombre for p in parecidos}) > 1:
        raise LookupError(f"Hay varios parecidos a «{nombre}»: {', '.join(sorted({p.nombre for p in parecidos}))}.")
    return parecidos[0]


def marcar_inicio(programa: Inicio, activar: bool) -> None:
    import winreg

    marca = bytes([2 if activar else 3]) + bytes(11)
    try:
        _escribir_valor(f"{_APROBADOS}\\{programa.aprobado}", programa.valor, marca, winreg.REG_BINARY,
                        _raiz(programa.raiz))
    except PermissionError as error:
        raise PermissionError(f"Para cambiar «{programa.nombre}» Windows pide permisos de administrador.") from error


def programas_de_inicio(action: str, name: str = "") -> str:
    """Handler de ``startup_apps``: list | enable | disable."""
    programas = listar_inicio()
    if action == "list":
        if not programas:
            return "No hay programas configurados para arrancar con Windows."
        activos = [p.nombre for p in programas if p.activo]
        apagados = [p.nombre for p in programas if not p.activo]
        texto = f"Arrancan con Windows: {', '.join(activos) or 'ninguno'}."
        if apagados:
            texto += f" Desactivados: {', '.join(apagados)}."
        return texto
    if action not in ("enable", "disable"):
        raise ValueError("action debe ser list, enable o disable.")
    programa = elegir_inicio(programas, name)
    activar = action == "enable"
    if programa.activo == activar:
        return f"{programa.nombre} ya {'arrancaba' if activar else 'no arrancaba'} con Windows."
    marcar_inicio(programa, activar)
    return f"Listo: {programa.nombre} {'vuelve a arrancar' if activar else 'ya no arranca'} con Windows."


def inicio_activo(name: str) -> bool:
    return elegir_inicio(listar_inicio(), name).activo


# --- Páginas de Configuración -------------------------------------------------------------

PAGINAS = {
    "display": "ms-settings:display", "night_light": "ms-settings:nightlight", "sound": "ms-settings:sound",
    "bluetooth": "ms-settings:bluetooth", "wifi": "ms-settings:network-wifi", "network": "ms-settings:network",
    "notifications": "ms-settings:notifications", "focus": "ms-settings:quiethours",
    "default_apps": "ms-settings:defaultapps", "startup_apps": "ms-settings:startupapps",
    "apps": "ms-settings:appsfeatures", "background": "ms-settings:personalization-background",
    "colors": "ms-settings:personalization-colors", "windows_update": "ms-settings:windowsupdate",
    "power": "ms-settings:powersleep", "battery": "ms-settings:batterysaver", "storage": "ms-settings:storagesense",
    "mouse": "ms-settings:mousetouchpad", "keyboard": "ms-settings:typing", "privacy": "ms-settings:privacy",
    "accounts": "ms-settings:yourinfo", "time": "ms-settings:dateandtime", "language": "ms-settings:regionlanguage",
    "printers": "ms-settings:printers", "airplane_mode": "ms-settings:network-airplanemode",
    "vpn": "ms-settings:network-vpn", "gaming": "ms-settings:gaming-gamemode", "about": "ms-settings:about",
}
NOMBRES_PAGINAS = {
    "display": "Pantalla", "night_light": "Luz nocturna", "sound": "Sonido", "bluetooth": "Bluetooth y dispositivos",
    "wifi": "Wi-Fi", "network": "Red e internet", "notifications": "Notificaciones", "focus": "No molestar",
    "default_apps": "Aplicaciones predeterminadas", "startup_apps": "Aplicaciones de inicio",
    "apps": "Aplicaciones instaladas", "background": "Fondo de pantalla", "colors": "Colores",
    "windows_update": "Windows Update", "power": "Energía", "battery": "Ahorro de batería",
    "storage": "Almacenamiento", "mouse": "Mouse", "keyboard": "Escritura", "privacy": "Privacidad",
    "accounts": "Cuentas", "time": "Fecha y hora", "language": "Idioma y región", "printers": "Impresoras",
    "airplane_mode": "Modo avión", "vpn": "VPN", "gaming": "Modo de juego", "about": "Información del sistema",
}


def abrir_pagina(page: str) -> str:
    """Handler de ``open_settings``."""
    if page not in PAGINAS:
        raise ValueError(f"Página desconocida: {page}.")
    os.startfile(PAGINAS[page])  # noqa: S606 — URI fija de la tabla, nunca texto del usuario
    return f"Abrí Configuración › {NOMBRES_PAGINAS[page]}."


# --- Frases directas ----------------------------------------------------------------------

_ON = r"(?:activa|prende|enciende|pone|pon|habilita|conecta)"
_OFF = r"(?:desactiva|apaga|saca|quita|deshabilita|desconecta)"
_OBJETOS = {
    "night_light": r"(?:la\s+)?luz\s+nocturna",
    "dark_mode": r"(?:el\s+)?(?:modo|tema)\s+oscuro",
    "wifi": r"(?:el\s+|la\s+)?wi\s*fi",
    "bluetooth": r"(?:el\s+)?bluetooth",
}
_CLARO = re.compile(r"^(?:activa|pone|pon)\w*\s+(?:el\s+)?(?:modo|tema)\s+claro(?:\s+de\s+windows)?$")
_PREFIJO = r"^(?:(?:hey|oye)\s+)?(?:jarvis\s+)?(?:por\s+favor\s+)?"
_SUFIJO = r"(?:\s+(?:de|del|en)\s+(?:windows|la\s+pc|la\s+compu\w*|el\s+equipo))?(?:\s+por\s+favor)?(?:\s+jarvis)?$"
_LISTA_INICIO = re.compile(
    _PREFIJO + r"(?:que|cuales)\s+programas\s+(?:se\s+abren|arrancan|se\s+inician|inician|se\s+ejecutan)\s+"
    r"(?:al\s+(?:iniciar|prender|encender|arrancar)(?:\s+(?:windows|la\s+pc|la\s+compu\w*))?|con\s+windows)$")
_SACAR_INICIO = re.compile(_PREFIJO + r"(?:saca|quita|desactiva)\w*\s+(?:a\s+)?(?P<n>.+?)\s+del\s+inicio(?:\s+de\s+windows)?$")
_PONER_INICIO = re.compile(_PREFIJO + r"(?:agrega|activa|pone|pon)\w*\s+(?:a\s+)?(?P<n>.+?)\s+(?:al|en\s+el)\s+inicio(?:\s+de\s+windows)?$")
_NO_ARRANQUE = re.compile(_PREFIJO + r"que\s+(?P<n>.+?)\s+no\s+(?:arranque|se\s+abra|inicie|se\s+inicie)\s+(?:con|al\s+(?:iniciar|prender))\s+(?:windows|la\s+pc)$")
_SI_ARRANQUE = re.compile(_PREFIJO + r"que\s+(?P<n>.+?)\s+(?:arranque|se\s+abra|inicie|se\s+inicie)\s+(?:con|al\s+(?:iniciar|prender))\s+(?:windows|la\s+pc)$")


def detectar(texto: str) -> tuple[str, dict[str, object]] | None:
    """Frase directa → (herramienta, argumentos). Las de más de un ajuste van al modelo."""
    original = (texto or "").strip().rstrip(".!")
    consulta = _normal(original)
    for setting, objeto in _OBJETOS.items():
        for verbo, state in ((_ON, "on"), (_OFF, "off")):
            if re.match(_PREFIJO + verbo + r"\w*\s+" + objeto + _SUFIJO, consulta):
                return ("windows_setting", {"setting": setting, "state": state})
    if _CLARO.match(consulta):
        return ("windows_setting", {"setting": "dark_mode", "state": "off"})
    if _LISTA_INICIO.match(consulta):
        return ("startup_apps", {"action": "list"})
    for patron, action in ((_SACAR_INICIO, "disable"), (_NO_ARRANQUE, "disable"),
                           (_PONER_INICIO, "enable"), (_SI_ARRANQUE, "enable")):
        if (m := patron.match(consulta)) is not None:
            return ("startup_apps", {"action": action, "name": m.group("n")[:80]})
    return None
