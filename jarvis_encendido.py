"""Encender la PC desde el celular (Wake-on-LAN), solo en casa (decisión del usuario).

La PC le da al celular lo que necesita para despertarla —las MAC de sus placas de
red físicas, su IP y la dirección de difusión de la red de casa— y un diagnóstico
de lo que falta configurar. El celular guarda esos datos y, con la PC apagada o
suspendida, manda el «paquete mágico» por la red local (Jarvis no puede hacerlo:
está dormido).

Jarvis no cambia la configuración de Windows ni de la BIOS: solo la lee
(``powercfg``, registro) y explica los pasos.
"""

import ipaddress
import logging
import os
import socket
import subprocess
import time

import psutil

CACHE = 60.0  # al conectar o desconectar el cable, el celular lo ve en un minuto
CREATE_NO_WINDOW = 0x08000000
_VIRTUALES = ("vethernet", "virtual", "hyper-v", "vpn", "zerotier", "tailscale", "radmin", "loopback",
              "vmware", "virtualbox", "bluetooth", "wsl", "tap", "tun", "área local*", "local area connection*")
_cache: tuple[float, dict] | None = None


def _ip_local() -> str | None:
    """IP de la placa que sale a la red de casa (sin enviar nada: UDP no conecta)."""
    prueba = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        prueba.connect(("192.168.255.255", 9))
        ip = prueba.getsockname()[0]
    except OSError:
        return None
    finally:
        prueba.close()
    return ip if ipaddress.ip_address(ip).is_private and not ip.startswith("100.") else None


def _es_virtual(nombre: str) -> bool:
    return any(v in nombre.lower() for v in _VIRTUALES)


def placas(direcciones=None, ip: str | None = None) -> dict:
    """{ip, broadcast, adapter, wifi, macs} de la red de casa (psutil, sin red real en tests)."""
    direcciones = direcciones if direcciones is not None else psutil.net_if_addrs()
    ip = ip if ip is not None else _ip_local()
    datos: dict = {"ip": None, "broadcast": None, "adapter": None, "wifi": False, "macs": []}
    for nombre, lista in direcciones.items():
        if _es_virtual(nombre):
            continue
        mac = next((d.address for d in lista if d.family == psutil.AF_LINK), None)
        if mac and mac.replace("-", "").replace(":", "") not in ("", "000000000000"):
            datos["macs"].append(mac.replace("-", ":").upper())
        for d in lista:
            if d.family == socket.AF_INET and d.address == ip and d.netmask:
                red = ipaddress.ip_network(f"{d.address}/{d.netmask}", strict=False)
                datos.update(ip=d.address, broadcast=str(red.broadcast_address), adapter=nombre,
                             wifi="wi-fi" in nombre.lower() or "wifi" in nombre.lower()
                             or "wlan" in nombre.lower() or "inalámbrica" in nombre.lower())
    return datos


def _powercfg(*argumentos: str) -> str:
    try:
        salida = subprocess.run(["powercfg", *argumentos], capture_output=True, text=True, timeout=15,
                                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return salida.stdout or ""


def _inicio_rapido() -> bool | None:
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager\Power") as clave:
            return winreg.QueryValueEx(clave, "HiberbootEnabled")[0] == 1
    except OSError:
        return None


def consejos(datos: dict) -> list[str]:
    """Qué falta para que funcione, en el orden en que conviene hacerlo."""
    lista = []
    if not datos.get("macs") or not datos.get("broadcast"):
        lista.append("No encontré la placa de red de casa: conecta la PC por Wi-Fi o cable y abre la app de nuevo.")
        return lista
    if not datos.get("armed"):
        lista.append("Windows no deja que la placa de red despierte la PC. En PowerShell como administrador: "
                     f"powercfg /deviceenablewake \"{datos.get('device') or 'nombre de la placa'}\" "
                     "(o Administrador de dispositivos → la placa → Administración de energía → "
                     "«Permitir que este dispositivo reactive el equipo»).")
    if datos.get("wifi"):
        lista.append("Por Wi-Fi solo despierta desde «suspender», no desde apagado: dile a Jarvis «suspende el "
                     "equipo» en vez de «apágalo». Para encender desde apagado hace falta el cable de red.")
    else:
        lista.append("Con cable enciende desde apagado si en la BIOS está activado «Wake on LAN» (a veces "
                     "«Power On by PCI-E») y desactivado «ErP» (si está activado, la placa de red se queda sin "
                     "energía al apagar). Esto no se puede comprobar desde Windows.")
        if datos.get("fastStartup"):
            lista.append("El «inicio rápido» de Windows suele impedir encender desde apagado: desactívalo en "
                         "Panel de control → Opciones de energía → Elegir el comportamiento de los botones.")
    return lista


def diagnostico(*, ahora: float | None = None) -> dict:
    """Datos para el celular y estado de la configuración (se guarda un minuto)."""
    global _cache
    ahora = time.monotonic() if ahora is None else ahora
    if _cache is not None and ahora - _cache[0] < CACHE:
        return _cache[1]
    datos = placas()
    try:
        fisicas = _adaptadores()
    except Exception:
        fisicas = []
    descripcion = next((a.description for a in fisicas if a.name == datos["adapter"]), None)
    if fisicas:  # Wi-Fi y cable (aunque no esté conectado); sin las virtuales de Wi-Fi Direct
        datos["macs"] = [a.mac.replace("-", ":").upper() for a in fisicas if a.mac]
    armados = _powercfg("/devicequery", "wake_armed")
    datos["device"] = descripcion
    datos["armed"] = bool(descripcion and descripcion.lower() in armados.lower())
    datos["fastStartup"] = _inicio_rapido()
    datos["tips"] = consejos(datos)
    _cache = (ahora, datos)
    logging.info("Diagnóstico de encendido remoto: %s, armada=%s.", "Wi-Fi" if datos["wifi"] else "cable",
                 datos["armed"])
    return datos


def _adaptadores():
    """Nombre y descripción de las placas (para ``powercfg``)."""
    from collections import namedtuple

    Placa = namedtuple("Placa", "name description mac")
    salida = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-NetAdapter -Physical | ForEach-Object { $_.Name + '|' + $_.InterfaceDescription + '|' + $_.MacAddress }"],
        capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    ).stdout
    return [Placa(*linea.split("|", 2)) for linea in salida.splitlines() if linea.count("|") == 2]
