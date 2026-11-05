"""Detectores de eventos que disparan automatizaciones (roadmap punto 3).

Por ahora: conexión y desconexión de los auriculares. Cada detector corre en
su propio hilo y solo publica eventos en la cola de la app; nunca ejecuta
acciones por sí mismo.
"""

import logging
import queue
import threading
from collections.abc import Callable

from jarvis_audio_devices import dispositivo_presente
from jarvis_routines import (
    DISPARADOR_AURICULARES_CONECTADOS,
    DISPARADOR_AURICULARES_DESCONECTADOS,
)

INTERVALO_SONDEO = 2.0
LECTURAS_ESTABLES = 2


class HeadphonesWatcher(threading.Thread):
    """Sondea si los auriculares están activos y avisa de los cambios.

    - La primera lectura solo fija el estado inicial: al abrir Jarvis no se
      dispara nada.
    - Un cambio cuenta solo si se mantiene ``lecturas_estables`` lecturas
      seguidas, para ignorar parpadeos del dispositivo.
    - Publica ``("device_event", disparador)`` en ``events``.
    """

    def __init__(
        self,
        events: queue.Queue[tuple],
        *,
        intervalo: float = INTERVALO_SONDEO,
        lecturas_estables: int = LECTURAS_ESTABLES,
        leer_estado: Callable[[], bool] = lambda: dispositivo_presente("headphones"),
    ) -> None:
        super().__init__(name="JarvisHeadphonesWatcher", daemon=True)
        self.events = events
        self.intervalo = intervalo
        self.lecturas_estables = max(1, lecturas_estables)
        self.leer_estado = leer_estado
        self._stop_event = threading.Event()
        self.estado: bool | None = None

    def run(self) -> None:
        candidato: bool | None = None
        repeticiones = 0
        errores_seguidos = 0
        while not self._stop_event.is_set():
            try:
                actual = self.leer_estado()
            except Exception:
                errores_seguidos += 1
                if errores_seguidos == 1:
                    logging.exception("No se pudo comprobar si los auriculares están conectados.")
                self._stop_event.wait(self.intervalo * 5)
                continue
            errores_seguidos = 0

            if self.estado is None:
                self.estado = actual
                logging.info(
                    "Auriculares %s al iniciar el detector.",
                    "conectados" if actual else "desconectados",
                )
            elif actual == self.estado:
                candidato, repeticiones = None, 0
            else:
                if actual == candidato:
                    repeticiones += 1
                else:
                    candidato, repeticiones = actual, 1
                if repeticiones >= self.lecturas_estables:
                    self.estado = actual
                    candidato, repeticiones = None, 0
                    disparador = (
                        DISPARADOR_AURICULARES_CONECTADOS
                        if actual
                        else DISPARADOR_AURICULARES_DESCONECTADOS
                    )
                    logging.info("Evento de audio: %s", disparador)
                    self.events.put(("device_event", disparador))
            self._stop_event.wait(self.intervalo)

    def stop(self) -> None:
        self._stop_event.set()
