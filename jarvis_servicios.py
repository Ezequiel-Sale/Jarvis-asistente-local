"""Vigila que los servicios de Jarvis sigan funcionando y avisa antes de que te des cuenta.

Cada ``INTERVALO`` s comprueba la voz a texto, la voz de Jarvis (texto a voz) y Ollama. Si
uno falla ``FALLOS_PARA_AVISAR`` veces seguidas, intenta arreglarlo (reiniciar los
contenedores de Docker, como mucho una vez cada ``PAUSA_REPARAR``) y, si sigue caído,
publica ``("service_alert", texto)``; cuando vuelve, ``("service_recovered", texto)``.
Los primeros ``ESPERA_INICIAL`` s no comprueba nada: Docker y Ollama aún están arrancando.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

INTERVALO = 60.0
ESPERA_INICIAL = 180.0
FALLOS_PARA_AVISAR = 2
PAUSA_REPARAR = 30 * 60


@dataclass
class Servicio:
    nombre: str                                  # «la voz a texto»
    funciona: Callable[[], bool]
    consejo: str = ""                            # qué puede hacer el usuario si no se arregla solo
    reparar: Callable[[], None] | None = None    # reiniciar Docker…
    fallos: int = 0
    avisado: bool = False
    ultima_reparacion: float = -float("inf")


class VigilanteServicios:
    def __init__(self, eventos, servicios: list[Servicio], *, intervalo: float = INTERVALO,
                 espera_inicial: float = ESPERA_INICIAL, reloj: Callable[[], float] = time.monotonic) -> None:
        self._eventos = eventos
        self.servicios = servicios
        self._intervalo = intervalo
        self._espera_inicial = espera_inicial
        self._reloj = reloj
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._bucle, name="JarvisServicios", daemon=True)

    def start(self) -> None:
        self._hilo.start()

    def stop(self) -> None:
        self._parar.set()

    @staticmethod
    def _comprobar(servicio: Servicio) -> bool:
        try:
            return bool(servicio.funciona())
        except Exception:
            logging.debug("Falló la comprobación de %s.", servicio.nombre, exc_info=True)
            return False

    def revisar_una_vez(self) -> None:
        for servicio in self.servicios:
            if self._comprobar(servicio):
                if servicio.avisado:
                    self._eventos.put(("service_recovered", f"{servicio.nombre.capitalize()} volvió a funcionar."))
                    logging.info("Servicio recuperado: %s.", servicio.nombre)
                servicio.fallos, servicio.avisado = 0, False
                continue
            servicio.fallos += 1
            if servicio.avisado or servicio.fallos < FALLOS_PARA_AVISAR:
                continue
            logging.warning("Servicio caído: %s (%d comprobaciones seguidas).", servicio.nombre, servicio.fallos)
            if servicio.reparar is not None and self._reloj() - servicio.ultima_reparacion >= PAUSA_REPARAR:
                servicio.ultima_reparacion = self._reloj()
                try:
                    servicio.reparar()
                except Exception:
                    logging.exception("No se pudo reiniciar %s.", servicio.nombre)
                if self._comprobar(servicio):
                    servicio.fallos = 0
                    self._eventos.put(("service_recovered",
                                       f"{servicio.nombre.capitalize()} se había caído; lo reinicié y ya funciona."))
                    continue
            servicio.avisado = True
            texto = f"Ojo: {servicio.nombre} no responde desde hace unos minutos."
            if servicio.consejo:
                texto += f" {servicio.consejo}"
            self._eventos.put(("service_alert", texto))

    def _bucle(self) -> None:
        if self._parar.wait(self._espera_inicial):
            return
        while not self._parar.is_set():
            self.revisar_una_vez()
            self._parar.wait(self._intervalo)
