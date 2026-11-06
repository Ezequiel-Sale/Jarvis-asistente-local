"""Estado del enlace inalámbrico de los auriculares Logitech G533.

El receptor USB del G533 sigue presente en Windows aunque el auricular esté
apagado, así que la salida de audio no basta para saber si están en uso. El
receptor responde al protocolo HID++ de Logitech: al pedirle la batería
devuelve el voltaje si el auricular está encendido y enlazado, o un error
HID++ si está apagado o fuera de alcance.

Solo se hace una lectura (consulta de batería); no se cambia ningún ajuste.
Si `hidapi` no está instalado o no se encuentra el receptor, las funciones
devuelven ``None`` («no se sabe») para que quien llame use otro método.
"""

import logging
import time
from dataclasses import dataclass

VENDOR_LOGITECH = 0x046D
PRODUCTO_G533 = 0x0A66
# Interfaz HID++ del receptor (la misma que usa HeadsetControl para la batería).
USAGE_PAGE_HIDPP = 0xFF43
USAGE_HIDPP = 0x0202

HIDPP_LARGO = 0x11
HIDPP_LONGITUD = 20
HIDPP_RECEPTOR = 0xFF
HIDPP_ERROR = 0xFF
# Índice de la función de batería del G533 y su consulta.
COMANDO_BATERIA = (0x07, 0x01)
CARGANDO = 0x03

TIEMPO_ESPERA_MS = 1000

_aviso_sin_hidapi = False


@dataclass(frozen=True)
class EstadoAuricular:
    encendido: bool
    voltaje_mv: int | None = None
    cargando: bool = False


def construir_consulta_bateria() -> list[int]:
    paquete = [HIDPP_LARGO, HIDPP_RECEPTOR, *COMANDO_BATERIA]
    return paquete + [0] * (HIDPP_LONGITUD - len(paquete))


def interpretar_respuesta(respuesta: list[int] | bytes) -> EstadoAuricular | None:
    """Traduce un informe HID++ del receptor.

    Devuelve ``None`` si el informe no responde a la consulta de batería
    (p. ej. una notificación ajena), para seguir leyendo.
    """
    datos = list(respuesta)
    if len(datos) < 4 or datos[0] != HIDPP_LARGO or datos[1] != HIDPP_RECEPTOR:
        return None
    # Error HID++: 11 FF FF <índice> <función> <código>.
    if datos[2] == HIDPP_ERROR:
        if tuple(datos[3:5]) == COMANDO_BATERIA:
            return EstadoAuricular(encendido=False)
        return None
    if tuple(datos[2:4]) != COMANDO_BATERIA or len(datos) < 7:
        return None
    voltaje = (datos[4] << 8) | datos[5]
    if voltaje == 0:
        return EstadoAuricular(encendido=False)
    return EstadoAuricular(encendido=True, voltaje_mv=voltaje, cargando=datos[6] == CARGANDO)


def consultar_g533(tiempo_espera_ms: int = TIEMPO_ESPERA_MS) -> EstadoAuricular | None:
    """Pregunta al receptor si el auricular está encendido.

    ``None`` si no se puede saber (sin `hidapi`, sin receptor o sin respuesta).
    """
    global _aviso_sin_hidapi
    try:
        import hid
    except ImportError:
        if not _aviso_sin_hidapi:
            _aviso_sin_hidapi = True
            logging.warning(
                "No está instalado hidapi: no se puede saber si el G533 está encendido."
            )
        return None

    interfaz = next(
        (
            d
            for d in hid.enumerate(VENDOR_LOGITECH, PRODUCTO_G533)
            if d.get("usage_page") == USAGE_PAGE_HIDPP and d.get("usage") == USAGE_HIDPP
        ),
        None,
    )
    if interfaz is None:
        return None

    dispositivo = hid.device()
    try:
        dispositivo.open_path(interfaz["path"])
    except OSError:
        logging.debug("No se pudo abrir la interfaz HID++ del G533.", exc_info=True)
        return None
    try:
        dispositivo.write(construir_consulta_bateria())
        limite = time.monotonic() + tiempo_espera_ms / 1000
        while (restante := limite - time.monotonic()) > 0:
            respuesta = dispositivo.read(HIDPP_LONGITUD, max(1, int(restante * 1000)))
            if not respuesta:
                break
            estado = interpretar_respuesta(respuesta)
            if estado is not None:
                return estado
        return None
    except OSError:
        logging.debug("Falló la consulta HID++ al G533.", exc_info=True)
        return None
    finally:
        dispositivo.close()


def auricular_encendido() -> bool | None:
    estado = consultar_g533()
    return None if estado is None else estado.encendido
