"""Mantener aplicado el perfil de ajuste de la GPU AMD (undervolt).

Adrenalin restaura los valores de fábrica tras un pantallazo negro o algunos
reinicios, y hay que volver a cargar el perfil a mano. Este módulo lee el perfil
que el usuario exportó desde Adrenalin (``undervolt.xml``), comprueba cada minuto
si la GPU lo tiene aplicado y, si no, lo reaplica.

Cómo: la biblioteca oficial de AMD ``atiadlxx.dll`` (ADL, interfaz Overdrive 8),
la misma que usa Adrenalin. Estructuras y números según el SDK público
(GPUOpen display-library: ``adl_defines.h`` / ``Sample/Overdrive8``).

Correspondencia perfil de Adrenalin → ajuste ADL (comprobada el 2026-11-10 con el
perfil aplicado: los cuatro grupos coincidían con lo que informaba el driver):
- FEATURE 12, estado 0  → OD8_OD_VOLTAGE (37): desfase de voltaje en mV (−80)
- FEATURE 5,  estado 0  → OD8_UCLK_FMAX (8): frecuencia máxima de la VRAM (2718)
- FEATURE 3,  estado 0  → OD8_POWER_PERCENTAGE (9): límite de energía en % (−10)
- FEATURE 22, estados 0–9 → OD8_FAN_CURVE_* (19–28): curva de ventiladores
Lo demás del XML no se toca. El «cero RPM» de los ventiladores (15) no está
identificado en el XML: se recuerda el valor visto con el perfil aplicado.

Seguridad:
- Solo se escriben valores del perfil del usuario y dentro de los límites que
  informa el driver; el identificador de la GPU debe coincidir con el del XML.
- Un cambio debe verse en dos revisiones seguidas antes de reaplicar.
- Si el perfil se pierde 3 veces en una hora se deja de reaplicar y se avisa:
  un undervolt inestable provoca justamente pantallazos negros, y reaplicarlo
  sin fin dejaría el equipo en un bucle de cuelgues.
"""

import ctypes
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from ctypes import POINTER, byref, c_char, c_int, c_void_p
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

OD8_COUNT = 77  # entradas de ADLOD8SettingId en adl_defines.h
OD8_UCLK_FMAX, OD8_POWER_PERCENTAGE, OD8_FAN_ZERORPM = 8, 9, 15
OD8_FAN_CURVE_INICIO, OD8_FAN_CURVE_FIN = 19, 28
OD8_UCLK_FMIN, OD8_OD_VOLTAGE = 34, 37
NOMBRES = {
    OD8_OD_VOLTAGE: "voltaje (mV)", OD8_UCLK_FMAX: "VRAM (MHz)", OD8_POWER_PERCENTAGE: "límite de energía (%)",
    OD8_FAN_ZERORPM: "ventiladores en parado",
}
PREFERENCIA_ACTIVO = "gpu_perfil"
PREFERENCIA_RUTA = "gpu_perfil_ruta"
PREFERENCIA_EXTRAS = "gpu_perfil_extras"
INTERVALO = 60.0
ESPERA_INICIAL = 90.0  # tras arrancar, dar tiempo a que Adrenalin aplique lo suyo
MAX_REAPLICACIONES = 3
VENTANA_REAPLICACIONES = 3600.0


class ErrorGPU(Exception):
    pass


@dataclass(frozen=True)
class Ajuste:
    minimo: int
    maximo: int
    defecto: int
    actual: int


def ruta_por_defecto() -> Path:
    return Path.home() / "Documents" / "undervolt.xml"


# --- Perfil de Adrenalin (XML) ---------------------------------------------------

def perfil_desde_xml(ruta: Path) -> tuple[dict[int, int], str]:
    """({ajuste ADL: valor}, DevID de la GPU) a partir del perfil exportado."""
    try:
        raiz = ElementTree.parse(ruta).getroot()
    except (OSError, ElementTree.ParseError) as error:
        raise ErrorGPU(f"No pude leer el perfil {ruta}: {error}") from error
    gpu = raiz.find("GPU")
    if gpu is None:
        raise ErrorGPU("El archivo no parece un perfil de ajuste de Adrenalin (falta <GPU>).")
    estados: dict[int, list[int]] = {}
    for feature in gpu.findall("FEATURE"):
        try:
            estados[int(feature.get("ID", ""))] = [
                int(estado.get("Value", "")) for estado in feature.findall("./STATES/STATE")
            ]
        except ValueError:
            continue
    perfil: dict[int, int] = {}
    if estados.get(12):
        perfil[OD8_OD_VOLTAGE] = estados[12][0]
    if estados.get(5):
        perfil[OD8_UCLK_FMAX] = estados[5][0]
    if estados.get(3):
        perfil[OD8_POWER_PERCENTAGE] = estados[3][0]
    curva = estados.get(22, [])
    if len(curva) >= 10:
        for indice in range(10):
            perfil[OD8_FAN_CURVE_INICIO + indice] = curva[indice]
    if OD8_OD_VOLTAGE not in perfil:
        raise ErrorGPU("El perfil no tiene el desfase de voltaje (FEATURE 12).")
    return perfil, (gpu.get("DevID") or "").upper()


def diferencias(perfil: dict[int, int], estado: dict[int, Ajuste]) -> dict[int, tuple[int, int]]:
    """{ajuste: (valor actual, valor del perfil)} de lo que no coincide."""
    return {
        clave: (estado[clave].actual, valor)
        for clave, valor in perfil.items()
        if clave in estado and estado[clave].actual != valor
    }


def validar(perfil: dict[int, int], estado: dict[int, Ajuste]) -> None:
    for clave, valor in perfil.items():
        ajuste = estado.get(clave)
        if ajuste is None:
            raise ErrorGPU(f"La GPU no informa el ajuste {clave}; no aplico nada.")
        if not ajuste.minimo <= valor <= ajuste.maximo:
            raise ErrorGPU(
                f"El valor {valor} de {NOMBRES.get(clave, f'ajuste {clave}')} está fuera de los "
                f"límites del driver ({ajuste.minimo} a {ajuste.maximo}); no aplico nada."
            )


def describir(perfil: dict[int, int]) -> str:
    partes = []
    if OD8_OD_VOLTAGE in perfil:
        partes.append(f"voltaje {perfil[OD8_OD_VOLTAGE]:+d} mV")
    if OD8_UCLK_FMAX in perfil:
        partes.append(f"VRAM {perfil[OD8_UCLK_FMAX]} MHz")
    if OD8_POWER_PERCENTAGE in perfil:
        partes.append(f"energía {perfil[OD8_POWER_PERCENTAGE]:+d} %")
    if OD8_FAN_CURVE_INICIO in perfil:
        partes.append("curva de ventiladores")
    return ", ".join(partes)


# --- ADL (driver de AMD) -------------------------------------------------------------

class _AdapterInfo(ctypes.Structure):
    _fields_ = [("iSize", c_int), ("iAdapterIndex", c_int), ("strUDID", c_char * 256), ("iBusNumber", c_int),
                ("iDeviceNumber", c_int), ("iFunctionNumber", c_int), ("iVendorID", c_int),
                ("strAdapterName", c_char * 256), ("strDisplayName", c_char * 256), ("iPresent", c_int),
                ("iExist", c_int), ("strDriverPath", c_char * 256), ("strDriverPathExt", c_char * 256),
                ("strPNPString", c_char * 256), ("iOSDisplayIndex", c_int)]


class _Inicial(ctypes.Structure):
    _fields_ = [("featureID", c_int), ("minValue", c_int), ("maxValue", c_int), ("defaultValue", c_int)]


class _Pedido(ctypes.Structure):
    _fields_ = [("value", c_int), ("requested", c_int), ("reset", c_int)]


class _Pedidos(ctypes.Structure):
    _fields_ = [("count", c_int), ("tabla", _Pedido * OD8_COUNT)]


class _Actuales(ctypes.Structure):
    _fields_ = [("count", c_int), ("tabla", c_int * OD8_COUNT)]


class _Sesion:
    """Contexto ADL abierto sobre la primera GPU AMD con Overdrive 8."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise ErrorGPU("El ajuste de la GPU solo está disponible en Windows.")
        try:
            self.adl = ctypes.WinDLL("atiadlxx.dll")
        except OSError as error:
            raise ErrorGPU("No encontré el driver de AMD (atiadlxx.dll).") from error
        self._bloques: list = []

        @ctypes.CFUNCTYPE(c_void_p, c_int)
        def reservar(cantidad):
            bloque = ctypes.create_string_buffer(cantidad)
            self._bloques.append(bloque)
            return ctypes.addressof(bloque)

        self._reservar = reservar
        self.ctx = c_void_p()
        if self.adl.ADL2_Main_Control_Create(reservar, 1, byref(self.ctx)) != 0:
            raise ErrorGPU("El driver de AMD no respondió (ADL).")
        self.indice, self.dev_id, self.nombre = self._buscar_gpu()

    def _buscar_gpu(self) -> tuple[int, str, str]:
        cantidad = c_int()
        self.adl.ADL2_Adapter_NumberOfAdapters_Get(self.ctx, byref(cantidad))
        adaptadores = (_AdapterInfo * max(1, cantidad.value))()
        self.adl.ADL2_Adapter_AdapterInfo_Get(self.ctx, adaptadores, ctypes.sizeof(adaptadores))
        for info in adaptadores[: cantidad.value]:
            if not info.iPresent:
                continue
            soportado, activo, version = c_int(), c_int(), c_int()
            if self.adl.ADL2_Overdrive_Caps(self.ctx, info.iAdapterIndex, byref(soportado), byref(activo),
                                            byref(version)) == 0 and soportado.value and version.value == 8:
                pnp = (info.strPNPString + b" " + info.strUDID).decode(errors="ignore").upper()
                dev = pnp.split("DEV_")[1][:4] if "DEV_" in pnp else ""
                return info.iAdapterIndex, dev, info.strAdapterName.decode(errors="ignore")
        raise ErrorGPU("No encontré una GPU AMD con ajuste manual (Overdrive 8).")

    def leer(self) -> dict[int, Ajuste]:
        capacidades, cantidad = c_int(), c_int(OD8_COUNT)
        iniciales = POINTER(_Inicial)()
        self.adl.ADL2_Overdrive8_Init_SettingX2_Get.argtypes = [
            c_void_p, c_int, POINTER(c_int), POINTER(c_int), POINTER(POINTER(_Inicial))]
        if self.adl.ADL2_Overdrive8_Init_SettingX2_Get(self.ctx, self.indice, byref(capacidades),
                                                       byref(cantidad), byref(iniciales)) != 0:
            raise ErrorGPU("No pude leer los límites de ajuste de la GPU.")
        cantidad_actual = c_int(OD8_COUNT)
        actuales = POINTER(c_int)()
        self.adl.ADL2_Overdrive8_Current_SettingX2_Get.argtypes = [
            c_void_p, c_int, POINTER(c_int), POINTER(POINTER(c_int))]
        if self.adl.ADL2_Overdrive8_Current_SettingX2_Get(self.ctx, self.indice, byref(cantidad_actual),
                                                          byref(actuales)) != 0:
            raise ErrorGPU("No pude leer el ajuste actual de la GPU.")
        total = min(OD8_COUNT, cantidad.value, cantidad_actual.value)
        return {
            i: Ajuste(iniciales[i].minValue, iniciales[i].maxValue, iniciales[i].defaultValue, actuales[i])
            for i in range(total)
        }

    def escribir(self, valores: dict[int, int]) -> None:
        pedidos, respuesta = _Pedidos(), _Actuales()
        pedidos.count = respuesta.count = OD8_COUNT
        for clave, valor in valores.items():
            pedidos.tabla[clave].requested = 1
            pedidos.tabla[clave].value = valor
        self.adl.ADL2_Overdrive8_Setting_Set.argtypes = [c_void_p, c_int, POINTER(_Pedidos), POINTER(_Actuales)]
        codigo = self.adl.ADL2_Overdrive8_Setting_Set(self.ctx, self.indice, byref(pedidos), byref(respuesta))
        if codigo != 0:
            raise ErrorGPU(f"El driver de AMD rechazó el ajuste (código {codigo}).")

    def cerrar(self) -> None:
        try:
            self.adl.ADL2_Main_Control_Destroy(self.ctx)
        except Exception:
            pass
        self._bloques.clear()


_lock = threading.Lock()


def leer_estado() -> tuple[dict[int, Ajuste], str, str]:
    """(ajustes, DevID, nombre de la GPU). Solo lectura."""
    with _lock:
        sesion = _Sesion()
        try:
            return sesion.leer(), sesion.dev_id, sesion.nombre
        finally:
            sesion.cerrar()


def pedido_completo(perfil: dict[int, int], estado: dict[int, Ajuste]) -> dict[int, int]:
    """Lo que se envía al driver: el perfil más lo que el SDK pide enviar junto."""
    valores = dict(perfil)
    fmin = estado.get(OD8_UCLK_FMIN)
    if OD8_UCLK_FMAX in valores and fmin is not None and fmin.minimo != fmin.maximo:
        valores[OD8_UCLK_FMIN] = fmin.actual  # FMAX y FMIN van juntos (si la GPU tiene FMIN)
    return valores


def aplicar(perfil: dict[int, int], dev_id_perfil: str = "") -> dict[int, tuple[int, int]]:
    """Escribe el perfil en la GPU. Devuelve lo que siga sin coincidir (vacío = aplicado)."""
    with _lock:
        sesion = _Sesion()
        try:
            if dev_id_perfil and sesion.dev_id and dev_id_perfil != sesion.dev_id:
                raise ErrorGPU(
                    f"El perfil es de otra GPU (DevID {dev_id_perfil}; esta es {sesion.dev_id}); no aplico nada."
                )
            estado = sesion.leer()
            validar(perfil, estado)
            sesion.escribir(pedido_completo(perfil, estado))
            time.sleep(0.5)
            return diferencias(perfil, sesion.leer())
        finally:
            sesion.cerrar()


# --- Vigilante ---------------------------------------------------------------------------

class VigilanteGPU:
    """Revisa cada minuto si el perfil sigue aplicado y lo reaplica si se perdió."""

    def __init__(
        self,
        eventos,
        activo: Callable[[], bool],
        obtener: Callable[[str], str | None],
        guardar: Callable[[str, str], None],
        *,
        intervalo: float = INTERVALO,
        espera_inicial: float = ESPERA_INICIAL,
        leer: Callable[[], tuple[dict[int, Ajuste], str, str]] = leer_estado,
        escribir: Callable[[dict[int, int], str], dict] = aplicar,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._eventos, self._activo = eventos, activo
        self._obtener, self._guardar = obtener, guardar
        self._intervalo, self._espera_inicial = intervalo, espera_inicial
        self._leer, self._escribir, self._reloj = leer, escribir, reloj
        self._parar = threading.Event()
        self._sospecha = False
        self._aplicaciones: list[float] = []
        self.bloqueado = False
        self.ultimo_estado = "sin revisar"
        self._hilo = threading.Thread(target=self._bucle, name="JarvisGPU", daemon=True)

    def start(self) -> None:
        self._hilo.start()

    def stop(self) -> None:
        self._parar.set()

    def ruta(self) -> Path:
        guardada = self._obtener(PREFERENCIA_RUTA)
        return Path(guardada) if guardada else ruta_por_defecto()

    def perfil(self) -> tuple[dict[int, int], str]:
        """Perfil del XML más los extras recordados (ventiladores en parado)."""
        perfil, dev = perfil_desde_xml(self.ruta())
        try:
            extras = json.loads(self._obtener(PREFERENCIA_EXTRAS) or "{}")
            perfil.update({int(k): int(v) for k, v in extras.items() if int(k) == OD8_FAN_ZERORPM})
        except (ValueError, TypeError, AttributeError):
            pass
        return perfil, dev

    def _recordar_extras(self, estado: dict[int, Ajuste]) -> None:
        if OD8_FAN_ZERORPM not in estado:
            return
        nuevo = json.dumps({str(OD8_FAN_ZERORPM): estado[OD8_FAN_ZERORPM].actual})
        if self._obtener(PREFERENCIA_EXTRAS) != nuevo:
            self._guardar(PREFERENCIA_EXTRAS, nuevo)

    def desbloquear(self) -> None:
        self.bloqueado = False
        self._aplicaciones.clear()

    def revisar_una_vez(self) -> None:
        if not self._activo():
            self.ultimo_estado = "desactivado"
            self._sospecha = False
            return
        try:
            base, dev = perfil_desde_xml(self.ruta())
            estado, _dev_gpu, _nombre = self._leer()
        except ErrorGPU as error:
            self.ultimo_estado = str(error)
            return
        if not diferencias(base, estado):
            # El perfil del XML está aplicado: se recuerda cómo están los ventiladores en parado.
            self._recordar_extras(estado)
            self._sospecha = False
            self.ultimo_estado = "aplicado"
            return
        if self.bloqueado:
            self.ultimo_estado = "perdido; no lo reaplico (se perdió demasiadas veces)"
            return
        if not self._sospecha:
            self._sospecha = True  # se confirma en la próxima revisión
            self.ultimo_estado = "parece perdido; lo confirmo en la próxima revisión"
            return
        self._sospecha = False
        ahora = self._reloj()
        self._aplicaciones = [t for t in self._aplicaciones if ahora - t < VENTANA_REAPLICACIONES]
        if len(self._aplicaciones) >= MAX_REAPLICACIONES:
            self.bloqueado = True
            self.ultimo_estado = "perdido; no lo reaplico (se perdió demasiadas veces)"
            logging.warning("El perfil de la GPU se perdió %s veces en una hora; dejo de reaplicarlo.",
                            MAX_REAPLICACIONES)
            self._eventos.put(("gpu_profile_warning",
                               f"El undervolt de la GPU se perdió {MAX_REAPLICACIONES} veces en una hora, así que "
                               "dejé de reaplicarlo: puede ser inestable y estar causando los pantallazos. "
                               "Revísalo en Adrenalin; para volver a aplicarlo di «aplica el undervolt»."))
            return
        try:
            perfil, dev = self.perfil()
            pendientes = self._escribir(perfil, dev)
        except ErrorGPU as error:
            self.ultimo_estado = str(error)
            logging.warning("No se pudo reaplicar el perfil de la GPU: %s", error)
            self._eventos.put(("gpu_profile_warning", f"No pude reaplicar el undervolt de la GPU: {error}"))
            self.bloqueado = True
            return
        self._aplicaciones.append(ahora)
        if pendientes:
            self.ultimo_estado = "reaplicado, pero el driver no aceptó todos los valores"
            logging.warning("Perfil de la GPU reaplicado con %s valores sin aceptar.", len(pendientes))
        else:
            self.ultimo_estado = "aplicado"
            logging.info("Perfil de la GPU reaplicado (%s).", describir(perfil))
        self._eventos.put(("gpu_profile_applied",
                           f"La GPU había perdido el undervolt; lo volví a aplicar ({describir(perfil)})."))

    def _bucle(self) -> None:
        if self._parar.wait(self._espera_inicial):
            return
        while not self._parar.is_set():
            try:
                self.revisar_una_vez()
            except Exception:
                logging.exception("Falló la revisión del perfil de la GPU.")
            self._parar.wait(self._intervalo)
