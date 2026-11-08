"""Configuración de monitores: desactivar, extender y duplicar.

Usa la API CCD de Windows (QueryDisplayConfig / SetDisplayConfig), la misma que
Configuración > Pantalla. Las topologías se aplican con SDC_TOPOLOGY_SUPPLIED:
Jarvis indica qué monitores se encienden y cuáles comparten imagen, y Windows
elige las resoluciones guardadas para esa combinación.

Seguridad:
- ``validar`` usa los mismos flags que la aplicación real con SDC_VALIDATE: comprueba sin aplicar nada.
- Antes de aplicar se guarda la configuración actual (``ConfiguracionGuardada``)
  para poder revertirla exactamente.
- Nunca se deja el equipo sin ningún monitor activo.
- Numeración fija por monitor físico: la primera vez que se ve un monitor toma su
  número de Windows (\\\\.\\DISPLAYN) y lo conserva para siempre, aunque Windows
  le cambie el DISPLAYN al desactivar y volver a extender monitores. La visión,
  el clic y el control de vídeo usan la misma numeración (``numeros_por_gdi``).
"""

import ctypes
import json
import logging
import os
import re
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

QDC_ALL_PATHS = 0x1
QDC_ONLY_ACTIVE_PATHS = 0x2
SDC_TOPOLOGY_SUPPLIED = 0x10
SDC_USE_SUPPLIED_DISPLAY_CONFIG = 0x20
SDC_VALIDATE = 0x40
SDC_APPLY = 0x80
SDC_SAVE_TO_DATABASE = 0x200
SDC_ALLOW_CHANGES = 0x400
SDC_ALLOW_PATH_ORDER_CHANGES = 0x2000
DISPLAYCONFIG_PATH_ACTIVE = 0x1
MODE_IDX_INVALID = 0xFFFFFFFF
GET_SOURCE_NAME = 1
GET_TARGET_NAME = 2
ERROR_SUCCESS = 0
ERROR_INSUFFICIENT_BUFFER = 122

PREFERENCIA_MAPA = "mapa_monitores"
# Decisión del usuario (2026-11-05): solo los monitores 1 (KG271U) y 3 (Smart TV)
# pueden ser el principal.
MONITORES_PRINCIPALES_PERMITIDOS = (1, 3)

_ERRORES = {
    87: "Windows rechazó la configuración (parámetro no válido)",
    50: "el controlador de video no admite esa configuración",
    31: "el dispositivo no respondió",
    5: "acceso denegado",
    1610: "la configuración de pantalla está bloqueada",
}


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", ctypes.c_uint32), ("Denominator", ctypes.c_uint32)]


class PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", ctypes.c_uint32),
        ("modeInfoIdx", ctypes.c_uint32),
        ("statusFlags", ctypes.c_uint32),
    ]


class PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", ctypes.c_uint32),
        ("modeInfoIdx", ctypes.c_uint32),
        ("outputTechnology", ctypes.c_uint32),
        ("rotation", ctypes.c_uint32),
        ("scaling", ctypes.c_uint32),
        ("refreshRate", RATIONAL),
        ("scanLineOrdering", ctypes.c_uint32),
        ("targetAvailable", wintypes.BOOL),
        ("statusFlags", ctypes.c_uint32),
    ]


class PATH_INFO(ctypes.Structure):
    _fields_ = [
        ("sourceInfo", PATH_SOURCE_INFO),
        ("targetInfo", PATH_TARGET_INFO),
        ("flags", ctypes.c_uint32),
    ]


class MODE_INFO(ctypes.Structure):
    _fields_ = [
        ("infoType", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
        ("adapterId", LUID),
        ("data", ctypes.c_byte * 48),
    ]


class DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("size", ctypes.c_uint32),
        ("adapterId", LUID),
        ("id", ctypes.c_uint32),
    ]


class SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", DEVICE_INFO_HEADER), ("viewGdiDeviceName", wintypes.WCHAR * 32)]


class TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DEVICE_INFO_HEADER),
        ("flags", ctypes.c_uint32),
        ("outputTechnology", ctypes.c_uint32),
        ("edidManufactureId", ctypes.c_uint16),
        ("edidProductCodeId", ctypes.c_uint16),
        ("connectorInstance", ctypes.c_uint32),
        ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
        ("monitorDevicePath", wintypes.WCHAR * 128),
    ]


assert ctypes.sizeof(PATH_INFO) == 72 and ctypes.sizeof(MODE_INFO) == 64


class ErrorPantalla(Exception):
    """Error al leer o cambiar la configuración de monitores."""


@dataclass(frozen=True)
class Pantalla:
    clave: str  # identificador estable del monitor físico (ruta del dispositivo)
    nombre: str  # nombre comercial (EDID), p. ej. «KG271U N3»
    adapter: tuple[int, int]
    target_id: int
    activa: bool
    numero: int | None  # número fijo del monitor físico (ver ``asignar_numeros``)
    source_id: int | None  # fuente actual si está activa
    gdi: int | None = None  # N de \\.\DISPLAYN mientras está activo (cambia al reconfigurar)

    @property
    def descripcion(self) -> str:
        etiqueta = f"monitor {self.numero}" if self.numero is not None else "monitor sin número"
        return f"{etiqueta} ({self.nombre})"


@dataclass(frozen=True)
class ConfiguracionGuardada:
    """Copia exacta de la configuración activa para poder revertirla."""

    paths: bytes
    modes: bytes
    num_paths: int
    num_modes: int


def _user32():
    if os.name != "nt":
        raise ErrorPantalla("La configuración de monitores solo está disponible en Windows.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetDisplayConfigBufferSizes.argtypes = [
        ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
    ]
    user32.GetDisplayConfigBufferSizes.restype = ctypes.c_long
    user32.QueryDisplayConfig.argtypes = [
        ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(PATH_INFO),
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(MODE_INFO), ctypes.c_void_p,
    ]
    user32.QueryDisplayConfig.restype = ctypes.c_long
    user32.SetDisplayConfig.argtypes = [
        ctypes.c_uint32, ctypes.POINTER(PATH_INFO), ctypes.c_uint32,
        ctypes.POINTER(MODE_INFO), ctypes.c_uint32,
    ]
    user32.SetDisplayConfig.restype = ctypes.c_long
    user32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.POINTER(DEVICE_INFO_HEADER)]
    user32.DisplayConfigGetDeviceInfo.restype = ctypes.c_long
    return user32


def _consultar(flags: int) -> tuple[list[PATH_INFO], list[MODE_INFO]]:
    user32 = _user32()
    for _ in range(3):  # la configuración puede cambiar entre llamadas
        num_paths, num_modes = ctypes.c_uint32(), ctypes.c_uint32()
        resultado = user32.GetDisplayConfigBufferSizes(flags, ctypes.byref(num_paths), ctypes.byref(num_modes))
        if resultado != ERROR_SUCCESS:
            raise ErrorPantalla(f"No pude leer la configuración de pantallas (error {resultado}).")
        paths = (PATH_INFO * num_paths.value)()
        modes = (MODE_INFO * num_modes.value)()
        resultado = user32.QueryDisplayConfig(
            flags, ctypes.byref(num_paths), paths, ctypes.byref(num_modes), modes, None
        )
        if resultado == ERROR_INSUFFICIENT_BUFFER:
            continue
        if resultado != ERROR_SUCCESS:
            raise ErrorPantalla(f"No pude leer la configuración de pantallas (error {resultado}).")
        return list(paths[: num_paths.value]), list(modes[: num_modes.value])
    raise ErrorPantalla("La configuración de pantallas cambió mientras se leía; inténtalo de nuevo.")


def _nombre_fuente(adapter: LUID, source_id: int) -> str:
    info = SOURCE_DEVICE_NAME()
    info.header.type = GET_SOURCE_NAME
    info.header.size = ctypes.sizeof(info)
    info.header.adapterId = adapter
    info.header.id = source_id
    if _user32().DisplayConfigGetDeviceInfo(ctypes.byref(info.header)) != ERROR_SUCCESS:
        return ""
    return info.viewGdiDeviceName


def _nombre_destino(adapter: LUID, target_id: int) -> tuple[str, str]:
    info = TARGET_DEVICE_NAME()
    info.header.type = GET_TARGET_NAME
    info.header.size = ctypes.sizeof(info)
    info.header.adapterId = adapter
    info.header.id = target_id
    if _user32().DisplayConfigGetDeviceInfo(ctypes.byref(info.header)) != ERROR_SUCCESS:
        return "", ""
    return info.monitorFriendlyDeviceName, info.monitorDevicePath


def _clave_adapter(luid: LUID) -> tuple[int, int]:
    return (int(luid.LowPart), int(luid.HighPart))


def listar_pantallas(
    mapa: dict[str, int] | None = None,
) -> tuple[list[Pantalla], dict[str, int]]:
    """Monitores conectados (activos o no) y el mapa número↔monitor actualizado."""
    mapa = dict(mapa or {})
    activos, _ = _consultar(QDC_ONLY_ACTIVE_PATHS)
    todos, _ = _consultar(QDC_ALL_PATHS)
    activos_por_destino = {
        (_clave_adapter(p.targetInfo.adapterId), p.targetInfo.id): p for p in activos
    }
    datos: dict[tuple[tuple[int, int], int], dict] = {}
    for path in todos:
        if not path.targetInfo.targetAvailable:
            continue
        clave_destino = (_clave_adapter(path.targetInfo.adapterId), path.targetInfo.id)
        if clave_destino in datos:
            continue
        nombre, ruta = _nombre_destino(path.targetInfo.adapterId, path.targetInfo.id)
        if not ruta:
            continue
        activo = activos_por_destino.get(clave_destino)
        gdi = None
        source_id = None
        if activo is not None:
            source_id = int(activo.sourceInfo.id)
            match = re.search(r"(\d+)$", _nombre_fuente(activo.sourceInfo.adapterId, activo.sourceInfo.id))
            gdi = int(match.group(1)) if match else None
        datos[clave_destino] = {
            "clave": ruta, "nombre": nombre or "monitor", "adapter": clave_destino[0],
            "target_id": clave_destino[1], "activa": activo is not None,
            "source_id": source_id, "gdi": gdi,
        }
    mapa = asignar_numeros([(d["clave"], d["gdi"]) for d in datos.values()], mapa)
    pantallas = [Pantalla(numero=mapa.get(d["clave"]), **d) for d in datos.values()]
    pantallas.sort(key=lambda p: (p.numero is None, p.numero or 0, p.nombre))
    return pantallas, mapa


def asignar_numeros(
    monitores: list[tuple[str, int | None]], mapa: dict[str, int]
) -> dict[str, int]:
    """Número fijo para cada monitor físico ``(ruta, DISPLAYN o None)``.

    Un monitor ya recordado conserva siempre su número: Windows reasigna los
    DISPLAYN al desactivar y volver a extender, y antes eso hacía que la Smart TV
    «robara» el número 2 al monitor que estaba apagado. Un monitor nuevo toma su
    DISPLAYN si está libre o, si no, el número libre más bajo.
    """
    resultado: dict[str, int] = {}
    usados: set[int] = set()
    for ruta, numero in mapa.items():  # descarta duplicados de mapas antiguos
        if numero not in usados and numero > 0:
            resultado[ruta] = numero
            usados.add(numero)
    nuevos = [(ruta, gdi) for ruta, gdi in monitores if ruta not in resultado]
    nuevos.sort(key=lambda m: (m[1] is None, m[1] or 0))
    for ruta, gdi in nuevos:
        numero = gdi if gdi and gdi not in usados else next(n for n in range(1, 100) if n not in usados)
        resultado[ruta] = numero
        usados.add(numero)
    return resultado


def numeros_por_gdi(mapa: dict[str, int] | None = None) -> tuple[dict[int, int], dict[str, int]]:
    """{DISPLAYN: número fijo} de los monitores activos, y el mapa actualizado."""
    pantallas, mapa = listar_pantallas(mapa)
    equivalencias: dict[int, int] = {}
    for pantalla in pantallas:  # ordenadas por número: en un duplicado gana el menor
        if pantalla.activa and pantalla.gdi is not None and pantalla.numero is not None:
            equivalencias.setdefault(pantalla.gdi, pantalla.numero)
    return equivalencias, mapa


def guardar_configuracion() -> ConfiguracionGuardada:
    paths, modes = _consultar(QDC_ONLY_ACTIVE_PATHS)
    return ConfiguracionGuardada(
        paths=b"".join(bytes(p) for p in paths),
        modes=b"".join(bytes(m) for m in modes),
        num_paths=len(paths),
        num_modes=len(modes),
    )


def restaurar_configuracion(guardada: ConfiguracionGuardada, solo_validar: bool = False) -> None:
    paths = (PATH_INFO * guardada.num_paths).from_buffer_copy(guardada.paths)
    modes = (MODE_INFO * guardada.num_modes).from_buffer_copy(guardada.modes) if guardada.num_modes else None
    resultado = _user32().SetDisplayConfig(
        guardada.num_paths, paths, guardada.num_modes, modes,
        flags_para(FLAGS_EXPLICITO, solo_validar),
    )
    if resultado != ERROR_SUCCESS:
        # Último recurso: dejar que Windows restaure la última configuración extendida conocida.
        logging.error("No se pudo restaurar la configuración exacta (error %s).", resultado)
        raise ErrorPantalla(_mensaje_error(resultado))


def _mensaje_error(codigo: int) -> str:
    return f"{_ERRORES.get(codigo, 'Windows no aceptó la configuración')} (código {codigo})."


# --- Construcción de topologías ---------------------------------------------------

def _buscar(pantallas: list[Pantalla], numero: int) -> Pantalla:
    for pantalla in pantallas:
        if pantalla.numero == numero:
            return pantalla
    conocidos = ", ".join(
        f"{p.numero}{'' if p.activa else ' (desactivado)'}" for p in pantallas if p.numero is not None
    )
    raise ErrorPantalla(f"No conozco el monitor {numero}. Monitores conocidos: {conocidos or 'ninguno'}.")


def _topologia(
    activos_deseados: dict[tuple[tuple[int, int], int], int | None],
    todos: list[PATH_INFO],
) -> list[PATH_INFO]:
    """Construye los paths para {destino: source_id fijo o None (cualquiera libre)}."""
    usados: dict[tuple[int, int], set[int]] = {}
    for (adapter, _target), source in activos_deseados.items():
        if source is not None:
            usados.setdefault(adapter, set()).add(source)
    resultado: list[PATH_INFO] = []
    for (adapter, target), source in activos_deseados.items():
        candidatos = [
            p for p in todos
            if _clave_adapter(p.targetInfo.adapterId) == adapter and p.targetInfo.id == target
            and _clave_adapter(p.sourceInfo.adapterId) == adapter
        ]
        if source is None:
            libres = [p for p in candidatos if p.sourceInfo.id not in usados.get(adapter, set())]
            if not libres:
                raise ErrorPantalla("La tarjeta de video no tiene salidas libres para otro monitor.")
            elegido = libres[0]
            usados.setdefault(adapter, set()).add(int(elegido.sourceInfo.id))
        else:
            coincidentes = [p for p in candidatos if p.sourceInfo.id == source]
            if not coincidentes:
                raise ErrorPantalla("Esos monitores no se pueden duplicar en esta tarjeta de video.")
            elegido = coincidentes[0]
        path = PATH_INFO.from_buffer_copy(bytes(elegido))
        path.flags = DISPLAYCONFIG_PATH_ACTIVE
        path.sourceInfo.modeInfoIdx = MODE_IDX_INVALID
        path.targetInfo.modeInfoIdx = MODE_IDX_INVALID
        path.sourceInfo.statusFlags = 0
        path.targetInfo.statusFlags = 0
        resultado.append(path)
    return resultado


def planificar(accion: str, numeros: tuple[int, ...], pantallas: list[Pantalla]) -> tuple[
    dict[tuple[tuple[int, int], int], int | None], str
]:
    """Devuelve la topología deseada {destino: fuente} y una descripción en español."""
    deseado: dict[tuple[tuple[int, int], int], int | None] = {
        (p.adapter, p.target_id): p.source_id for p in pantallas if p.activa
    }
    if accion == "disable":
        objetivo = _buscar(pantallas, numeros[0])
        if not objetivo.activa:
            raise ErrorPantalla(f"El {objetivo.descripcion} ya está desactivado.")
        if len(deseado) <= 1:
            raise ErrorPantalla("No puedo desactivar el único monitor activo.")
        # Si estaba duplicado, el otro monitor sigue mostrando esa misma imagen.
        del deseado[(objetivo.adapter, objetivo.target_id)]
        return deseado, f"Desactivé el {objetivo.descripcion}."
    if accion == "extend":
        objetivo = _buscar(pantallas, numeros[0])
        clave = (objetivo.adapter, objetivo.target_id)
        fuente = objetivo.source_id
        duplicado = fuente is not None and sum(1 for s in deseado.values() if s == fuente) > 1
        if objetivo.activa and not duplicado:
            raise ErrorPantalla(f"El {objetivo.descripcion} ya está extendido.")
        deseado[clave] = None  # se le asigna una fuente propia
        return deseado, f"Extendí el escritorio al {objetivo.descripcion}."
    if accion == "duplicate":
        if len(numeros) != 2 or numeros[0] == numeros[1]:
            raise ErrorPantalla("Para duplicar indica dos monitores distintos, por ejemplo «duplica 1 y 3».")
        primero, segundo = (_buscar(pantallas, n) for n in numeros)
        if primero.adapter != segundo.adapter:
            raise ErrorPantalla("Solo puedo duplicar monitores conectados a la misma tarjeta de video.")
        if primero.activa and segundo.activa and primero.source_id == segundo.source_id:
            raise ErrorPantalla(
                f"El {primero.descripcion} y el {segundo.descripcion} ya están duplicados."
            )
        # El duplicado se arma con modos explícitos (ver _config_duplicado); aquí solo
        # se indica qué monitores deben estar activos antes de duplicar.
        for pantalla in (primero, segundo):
            deseado.setdefault((pantalla.adapter, pantalla.target_id), None)
        return deseado, f"Dupliqué el {primero.descripcion} y el {segundo.descripcion}."
    if accion == "set_primary":
        if numeros[0] not in MONITORES_PRINCIPALES_PERMITIDOS:
            permitidos = " y ".join(str(n) for n in MONITORES_PRINCIPALES_PERMITIDOS)
            raise ErrorPantalla(f"Solo puedo poner como principal los monitores {permitidos}.")
        objetivo = _buscar(pantallas, numeros[0])
        if not objetivo.activa:
            raise ErrorPantalla(
                f"El {objetivo.descripcion} está desactivado; actívalo antes de hacerlo principal."
            )
        return deseado, f"El {objetivo.descripcion} ahora es el monitor principal."
    raise ErrorPantalla(f"Acción de pantalla desconocida: {accion}")


class SOURCE_MODE(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("pixelFormat", ctypes.c_uint32),
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


def _config_duplicado(base: Pantalla, copia: Pantalla) -> tuple[list[PATH_INFO], list[MODE_INFO]]:
    """Configuración activa con ``copia`` compartiendo la imagen de ``base``.

    Con SDC_TOPOLOGY_SUPPLIED el driver rechaza duplicar monitores sin un modo
    común guardado (p. ej. 2560×1440 con 1920×1080, error 31). Por eso la imagen
    compartida usa la resolución del monitor más pequeño del par y la tarjeta la
    escala en el otro, como hace Windows al duplicar a mano.
    """
    paths, modes = _consultar(QDC_ONLY_ACTIVE_PATHS)
    paths = [PATH_INFO.from_buffer_copy(bytes(p)) for p in paths]
    modes = [MODE_INFO.from_buffer_copy(bytes(m)) for m in modes]

    def path_de(pantalla: Pantalla) -> PATH_INFO:
        for path in paths:
            if (_clave_adapter(path.targetInfo.adapterId) == pantalla.adapter
                    and path.targetInfo.id == pantalla.target_id):
                return path
        raise ErrorPantalla(f"El {pantalla.descripcion} no está activo.")

    path_base, path_copia = path_de(base), path_de(copia)
    modo_base = SOURCE_MODE.from_buffer(modes[path_base.sourceInfo.modeInfoIdx].data)
    modo_copia = SOURCE_MODE.from_buffer(modes[path_copia.sourceInfo.modeInfoIdx].data)
    ancho, alto = min(
        ((modo_base.width, modo_base.height), (modo_copia.width, modo_copia.height)),
        key=lambda tam: tam[0] * tam[1],
    )
    modo_base.width, modo_base.height = ancho, alto
    path_copia.sourceInfo.id = path_base.sourceInfo.id
    path_copia.sourceInfo.modeInfoIdx = path_base.sourceInfo.modeInfoIdx
    return paths, modes


# Flags reales de cada forma de aplicar. La validación usa EXACTAMENTE los mismos
# cambiando SDC_APPLY por SDC_VALIDATE: antes se validaba sin SDC_SAVE_TO_DATABASE
# y al aplicar Windows devolvía 87, porque ese flag no se admite con
# SDC_TOPOLOGY_SUPPLIED (en ese modo Windows ya guarda la topología por su cuenta).
FLAGS_TOPOLOGIA = SDC_APPLY | SDC_TOPOLOGY_SUPPLIED | SDC_ALLOW_PATH_ORDER_CHANGES
FLAGS_EXPLICITO = (
    SDC_APPLY | SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_ALLOW_CHANGES | SDC_SAVE_TO_DATABASE
)


def flags_para(flags_reales: int, solo_validar: bool) -> int:
    return (flags_reales & ~SDC_APPLY) | SDC_VALIDATE if solo_validar else flags_reales


def _aplicar_explicito(
    paths: list[PATH_INFO], modes: list[MODE_INFO], solo_validar: bool = False
) -> None:
    arreglo_paths = (PATH_INFO * len(paths))(*paths)
    arreglo_modes = (MODE_INFO * len(modes))(*modes)
    resultado = _user32().SetDisplayConfig(
        len(paths), arreglo_paths, len(modes), arreglo_modes,
        flags_para(FLAGS_EXPLICITO, solo_validar),
    )
    if resultado != ERROR_SUCCESS:
        raise ErrorPantalla(_mensaje_error(resultado))


def _aplicar(deseado, solo_validar: bool = False) -> None:
    todos, _ = _consultar(QDC_ALL_PATHS)
    paths = _topologia(deseado, todos)
    arreglo = (PATH_INFO * len(paths))(*paths)
    resultado = _user32().SetDisplayConfig(
        len(paths), arreglo, 0, None, flags_para(FLAGS_TOPOLOGIA, solo_validar)
    )
    if resultado != ERROR_SUCCESS:
        raise ErrorPantalla(_mensaje_error(resultado))


MODE_INFO_TYPE_SOURCE = 1


def _config_principal(nuevo: Pantalla) -> tuple[list[PATH_INFO], list[MODE_INFO]]:
    """Configuración activa desplazada para que ``nuevo`` quede en (0, 0).

    En Windows el monitor principal es el que empieza en el origen del
    escritorio; se mueven todas las imágenes lo mismo, así la disposición
    relativa de los monitores no cambia.
    """
    paths, modes = _consultar(QDC_ONLY_ACTIVE_PATHS)
    paths = [PATH_INFO.from_buffer_copy(bytes(p)) for p in paths]
    modes = [MODE_INFO.from_buffer_copy(bytes(m)) for m in modes]
    path = next(
        (p for p in paths
         if _clave_adapter(p.targetInfo.adapterId) == nuevo.adapter
         and p.targetInfo.id == nuevo.target_id),
        None,
    )
    if path is None:
        raise ErrorPantalla(f"El {nuevo.descripcion} no está activo.")
    origen = SOURCE_MODE.from_buffer(modes[path.sourceInfo.modeInfoIdx].data)
    dx, dy = origen.x, origen.y
    if dx == 0 and dy == 0:
        raise ErrorPantalla(f"El {nuevo.descripcion} ya es el monitor principal.")
    for modo in modes:
        if modo.infoType == MODE_INFO_TYPE_SOURCE:
            fuente = SOURCE_MODE.from_buffer(modo.data)
            fuente.x -= dx
            fuente.y -= dy
    return paths, modes


def _par(numeros: tuple[int, ...], pantallas: list[Pantalla]) -> tuple[Pantalla, Pantalla]:
    """Para duplicar: la base es el primer monitor mencionado que esté activo."""
    primero, segundo = (_buscar(pantallas, n) for n in numeros)
    return (primero, segundo) if primero.activa or not segundo.activa else (segundo, primero)


def validar(accion: str, numeros: tuple[int, ...], mapa: dict[str, int] | None = None) -> str:
    """Comprueba con SDC_VALIDATE que Windows acepta el cambio, sin aplicarlo.

    Si para duplicar hay que activar antes un monitor, solo se valida ese
    primer paso (el segundo depende de la configuración resultante).
    """
    pantallas, _ = listar_pantallas(mapa)
    deseado, descripcion = planificar(accion, numeros, pantallas)
    if accion == "set_primary":
        _aplicar_explicito(*_config_principal(_buscar(pantallas, numeros[0])), solo_validar=True)
        return descripcion
    if accion == "duplicate":
        base, copia = _par(numeros, pantallas)
        if base.activa and copia.activa:
            _aplicar_explicito(*_config_duplicado(base, copia), solo_validar=True)
            return descripcion
    _aplicar(deseado, solo_validar=True)
    return descripcion


def aplicar(
    accion: str,
    numeros: tuple[int, ...],
    mapa: dict[str, int] | None = None,
) -> tuple[str, ConfiguracionGuardada, dict[str, int]]:
    """Aplica el cambio y devuelve (descripción, configuración previa, mapa actualizado)."""
    pantallas, mapa = listar_pantallas(mapa)
    deseado, descripcion = planificar(accion, numeros, pantallas)
    validar(accion, numeros, mapa)
    previa = guardar_configuracion()
    # La red de seguridad debe funcionar antes de tocar nada.
    restaurar_configuracion(previa, solo_validar=True)
    if accion == "set_primary":
        _aplicar_explicito(*_config_principal(_buscar(pantallas, numeros[0])))
    elif accion == "duplicate":
        base, copia = _par(numeros, pantallas)
        if not (base.activa and copia.activa):
            # Paso 1: activar el monitor que faltaba; paso 2: duplicar.
            _aplicar(deseado)
            pantallas, mapa = listar_pantallas(mapa)
            base, copia = _par(numeros, pantallas)
        _aplicar_explicito(*_config_duplicado(base, copia))
    else:
        _aplicar(deseado)
    logging.info("Configuración de pantallas aplicada: %s %s", accion, numeros)
    return descripcion, previa, mapa


def cargar_mapa(obtener: Callable[[str], str | None]) -> dict[str, int]:
    try:
        datos = json.loads(obtener(PREFERENCIA_MAPA) or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return {str(k): int(v) for k, v in datos.items() if isinstance(v, int)} if isinstance(datos, dict) else {}


def guardar_mapa(guardar: Callable[[str, str], None], mapa: dict[str, int]) -> None:
    guardar(PREFERENCIA_MAPA, json.dumps(mapa, ensure_ascii=False))


# --- Escalado por monitor (arreglo de la barra de tareas) ----------------------------
#
# Windows 11 a veces dibuja mal la barra de tareas de un monitor con escala alta
# (la Smart TV al 300 %) tras cambiar la configuración: queda a media pantalla o
# cortada. No hay arreglo definitivo conocido; lo que funciona es lo mismo que
# hacerlo a mano en Configuración > Pantalla: bajar la escala y volver a subirla.
# La API es la que usa Configuración (DisplayConfig{Get,Set}DeviceInfo con los
# tipos no documentados -3/-4): la escala va relativa a la recomendada.

ESCALAS = (100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500)
GET_DPI_SCALE = 0xFFFFFFFD  # DISPLAYCONFIG_DEVICE_INFO_GET_DPI_SCALE (-3)
SET_DPI_SCALE = 0xFFFFFFFC  # DISPLAYCONFIG_DEVICE_INFO_SET_DPI_SCALE (-4)
ESCALA_INTERMEDIA = 200
ESPERA_ESCALADO = 2.0


class SOURCE_DPI_SCALE_GET(ctypes.Structure):
    _fields_ = [
        ("header", DEVICE_INFO_HEADER),
        ("minScaleRel", ctypes.c_int32),
        ("curScaleRel", ctypes.c_int32),
        ("maxScaleRel", ctypes.c_int32),
    ]


class SOURCE_DPI_SCALE_SET(ctypes.Structure):
    _fields_ = [("header", DEVICE_INFO_HEADER), ("scaleRel", ctypes.c_int32)]


@dataclass(frozen=True)
class Escala:
    actual: int
    recomendada: int
    posibles: tuple[int, ...]


def escala_desde_relativa(minimo: int, actual: int, maximo: int) -> Escala:
    """Convierte los valores relativos de Windows (índices respecto a la recomendada)."""
    recomendada = abs(minimo)
    ultimo = min(len(ESCALAS) - 1, recomendada + maximo)
    indice = min(max(recomendada + actual, 0), ultimo)
    return Escala(ESCALAS[indice], ESCALAS[recomendada], ESCALAS[: ultimo + 1])


def _cabecera_fuente(estructura, tipo: int, pantalla: Pantalla) -> None:
    estructura.header.type = tipo
    estructura.header.size = ctypes.sizeof(estructura)
    estructura.header.adapterId = LUID(pantalla.adapter[0], pantalla.adapter[1])
    estructura.header.id = pantalla.source_id


def leer_escala(pantalla: Pantalla) -> Escala:
    if not pantalla.activa or pantalla.source_id is None:
        raise ErrorPantalla(f"El {pantalla.descripcion} está desactivado.")
    info = SOURCE_DPI_SCALE_GET()
    _cabecera_fuente(info, GET_DPI_SCALE, pantalla)
    if _user32().DisplayConfigGetDeviceInfo(ctypes.byref(info.header)) != ERROR_SUCCESS:
        raise ErrorPantalla(f"No pude leer la escala del {pantalla.descripcion}.")
    return escala_desde_relativa(info.minScaleRel, info.curScaleRel, info.maxScaleRel)


def poner_escala(pantalla: Pantalla, porcentaje: int, escala: Escala) -> None:
    if porcentaje not in escala.posibles:
        raise ErrorPantalla(f"El {pantalla.descripcion} no admite una escala del {porcentaje} %.")
    info = SOURCE_DPI_SCALE_SET()
    _cabecera_fuente(info, SET_DPI_SCALE, pantalla)
    info.scaleRel = ESCALAS.index(porcentaje) - ESCALAS.index(escala.recomendada)
    user32 = _user32()
    user32.DisplayConfigSetDeviceInfo.argtypes = [ctypes.POINTER(DEVICE_INFO_HEADER)]
    user32.DisplayConfigSetDeviceInfo.restype = ctypes.c_long
    resultado = user32.DisplayConfigSetDeviceInfo(ctypes.byref(info.header))
    if resultado != ERROR_SUCCESS:
        raise ErrorPantalla(f"Windows no aceptó la escala del {porcentaje} % (código {resultado}).")


def escala_intermedia(escala: Escala) -> int:
    """200 % (lo que pidió el usuario) o, si ya está ahí, la escala anterior disponible."""
    if escala.actual != ESCALA_INTERMEDIA and ESCALA_INTERMEDIA in escala.posibles:
        return ESCALA_INTERMEDIA
    menores = [e for e in escala.posibles if e < escala.actual]
    if menores:
        return menores[-1]
    mayores = [e for e in escala.posibles if e > escala.actual]
    if mayores:
        return mayores[0]
    raise ErrorPantalla("Ese monitor solo admite una escala; no hay nada que reajustar.")


def elegir_monitor_escalado(pantallas: list[Pantalla], numero: int | None, escalas: Callable[[Pantalla], Escala]) -> Pantalla:
    """El monitor pedido o, sin número, el activo con más escala (la TV)."""
    if numero is not None:
        return _buscar(pantallas, numero)
    activas = [p for p in pantallas if p.activa]
    if not activas:
        raise ErrorPantalla("No hay monitores activos.")
    return max(activas, key=lambda p: (escalas(p).actual, -(p.numero or 0)))


def arreglar_escalado(
    numero: int | None,
    mapa: dict[str, int] | None = None,
    *,
    dormir: Callable[[float], None] | None = None,
) -> str:
    """Baja la escala del monitor (a 200 %) y la devuelve a la que tenía."""
    import time

    pantallas, _ = listar_pantallas(mapa)
    pantalla = elegir_monitor_escalado(pantallas, numero, leer_escala)
    escala = leer_escala(pantalla)
    intermedia = escala_intermedia(escala)
    poner_escala(pantalla, intermedia, escala)
    try:
        (dormir or time.sleep)(ESPERA_ESCALADO)
    finally:
        poner_escala(pantalla, escala.actual, escala)  # siempre se vuelve a la original
    logging.info("Escalado reajustado en el %s: %s %% → %s %%.", pantalla.descripcion, intermedia, escala.actual)
    return (
        f"Reajusté el escalado del {pantalla.descripcion}: lo puse al {intermedia} % "
        f"y lo devolví al {escala.actual} %."
    )


def barra_bien_ubicada(barra: tuple[int, int, int, int], monitores: list[tuple[int, int, int, int]]) -> bool:
    """La barra (izq, arr, der, abj) está dentro de un monitor y pegada a uno de sus bordes."""
    l, t, r, b = barra
    for ml, mt, mr, mb in monitores:
        if ml - 2 <= l and r <= mr + 2 and mt - 2 <= t and b <= mb + 2:
            if r - l >= b - t:  # horizontal: abajo o arriba
                return min(abs(b - mb), abs(t - mt)) <= 2
            return min(abs(l - ml), abs(r - mr)) <= 2  # vertical: a un lado
    return False


def barras_de_tareas() -> list[tuple[int, int, int, int]]:
    """Rectángulos físicos de las barras de tareas (principal y secundarias)."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    anterior = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    rects = []
    try:
        for clase in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            hwnd = None
            while hwnd := user32.FindWindowExW(None, hwnd, clase, None):
                rect = wintypes.RECT()
                if user32.IsWindowVisible(hwnd) and user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    rects.append((rect.left, rect.top, rect.right, rect.bottom))
    finally:
        if anterior:
            user32.SetThreadDpiAwarenessContext(anterior)
    return rects
