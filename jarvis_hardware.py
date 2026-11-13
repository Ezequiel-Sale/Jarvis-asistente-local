"""Salud del equipo: temperaturas y carga con LibreHardwareMonitor (vía pythonnet).

- La biblioteca (MPL 2.0, NuGet 0.9.6 para .NET Framework 4.7.2) está en
  ``vendor/LibreHardwareMonitor`` con sus dependencias y licencias; se carga con
  ``pythonnet`` en el runtime «netfx» (.NET Framework, ya incluido en Windows).
- Jarvis corre como administrador, que es lo que la biblioteca necesita.
- GPU AMD: temperatura (núcleo, punto caliente, memoria) y carga sin drivers extra.
- CPU: la carga sale siempre; **la temperatura necesita el driver PawnIO**
  (desde la 0.9.x la biblioteca ya no usa WinRing0, que Windows Defender marca
  como vulnerable). Sin PawnIO la biblioteca devuelve 0 °C: se trata como «sin dato».
- Si la biblioteca no carga, todo sigue funcionando sin temperaturas.
- ``VigilanteSalud`` avisa (evento ``health_alert``) si una temperatura supera su
  límite en dos lecturas seguidas, como mucho una vez cada 15 minutos, y (evento
  ``disk_alert``) si un disco pasa del 90 %, como mucho una vez al día por disco.
"""

import logging
import sys
from collections import deque
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

LIMITE_CPU = 90.0
LIMITE_GPU = 90.0
LIMITE_GPU_PUNTO_CALIENTE = 105.0
PAUSA_AVISOS = 15 * 60
LIMITE_DISCO = 90.0
LIMITE_DISCO_CRITICO = 97.0  # se avisa otra vez aunque ya se haya avisado ese día al 90 %
# Avisos antes de llegar al límite: calor o memoria altos durante un rato seguido.
LIMITE_SOSTENIDO_CPU = 85.0
LIMITE_SOSTENIDO_GPU = 85.0
LIMITE_SOSTENIDO_RAM = 92.0
VENTANA_SOSTENIDA = 120.0  # segundos
PAUSA_AVISOS_DISCO = 24 * 60 * 60
INTERVALO_DISCOS = 10 * 60
INTERVALO_SENSORES = 3.0
HISTORIAL = 200  # lecturas: ~10 minutos a 3 s


@dataclass(frozen=True)
class Temperaturas:
    cpu: float | None = None
    cpu_carga: float | None = None
    gpu: float | None = None
    gpu_punto_caliente: float | None = None
    gpu_memoria: float | None = None
    gpu_carga: float | None = None
    gpu_nombre: str = ""
    cpu_nombre: str = ""


def _carpeta() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "vendor" / "LibreHardwareMonitor"


def _valor(sensor) -> float | None:
    try:
        valor = sensor.Value
        if valor is None:
            return None
        valor = float(valor)
    except Exception:
        return None
    return valor if valor > 0 else None  # 0 °C = sin driver (PawnIO) o sin dato


def interpretar(lecturas: list[tuple[str, str, str, str, float | None]]) -> Temperaturas:
    """(tipo de hardware, nombre del hardware, tipo de sensor, nombre del sensor, valor) → resumen."""
    datos: dict[str, object] = {}
    for tipo_hw, nombre_hw, tipo, nombre, valor in lecturas:
        if valor is None:
            continue
        if tipo_hw == "Cpu":
            datos["cpu_nombre"] = nombre_hw
            if tipo == "Temperature" and ("Tctl" in nombre or "Tdie" in nombre or nombre == "CPU Package"):
                datos.setdefault("cpu", valor)
            elif tipo == "Load" and nombre == "CPU Total":
                datos["cpu_carga"] = valor
        elif tipo_hw.startswith("Gpu"):
            datos.setdefault("gpu_nombre", nombre_hw)
            if tipo == "Temperature" and nombre == "GPU Core":
                datos["gpu"] = valor
            elif tipo == "Temperature" and nombre == "GPU Hot Spot":
                datos["gpu_punto_caliente"] = valor
            elif tipo == "Temperature" and nombre == "GPU Memory":
                datos["gpu_memoria"] = valor
            elif tipo == "Load" and nombre == "GPU Core":
                datos["gpu_carga"] = valor
    return Temperaturas(**datos)


class SensoresHardware:
    """Acceso perezoso y seguro entre hilos a la biblioteca."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._computer = None
        self._error: str | None = None

    @property
    def error(self) -> str | None:
        return self._error

    def _abrir(self):
        if self._computer is not None or self._error is not None:
            return self._computer
        carpeta = _carpeta()
        try:
            if not (carpeta / "LibreHardwareMonitorLib.dll").exists():
                raise FileNotFoundError(f"falta {carpeta / 'LibreHardwareMonitorLib.dll'}")
            sys.path.append(str(carpeta))
            from pythonnet import load

            try:
                load("netfx")
            except RuntimeError:
                pass  # ya cargado
            import clr

            clr.AddReference(str(carpeta / "LibreHardwareMonitorLib.dll"))
            from LibreHardwareMonitor.Hardware import Computer

            computer = Computer()
            computer.IsCpuEnabled = True
            computer.IsGpuEnabled = True
            computer.Open()
            self._computer = computer
            logging.info("Sensores de hardware listos (LibreHardwareMonitor).")
        except Exception as error:
            self._error = str(error) or type(error).__name__
            logging.warning("Sensores de hardware no disponibles: %s", self._error)
        return self._computer

    def leer(self) -> Temperaturas:
        with self._lock:
            computer = self._abrir()
            if computer is None:
                return Temperaturas()
            lecturas = []
            try:
                for hardware in computer.Hardware:
                    hardware.Update()
                    for sensor in hardware.Sensors:
                        lecturas.append((str(hardware.HardwareType), str(hardware.Name),
                                         str(sensor.SensorType), str(sensor.Name), _valor(sensor)))
            except Exception:
                logging.exception("No se pudieron leer los sensores de hardware.")
            return interpretar(lecturas)

    def cerrar(self) -> None:
        with self._lock:
            if self._computer is not None:
                try:
                    self._computer.Close()
                except Exception:
                    pass
                self._computer = None


sensores = SensoresHardware()


def _grados(valor: float | None) -> str:
    return f"{valor:.0f} °C" if valor is not None else "sin dato"


def describir(t: Temperaturas) -> list[str]:
    """Líneas para «¿cómo está el equipo?»."""
    lineas = []
    if t.gpu is not None or t.gpu_carga is not None:
        partes = [f"GPU a {_grados(t.gpu)}"]
        if t.gpu_punto_caliente is not None:
            partes.append(f"punto caliente {_grados(t.gpu_punto_caliente)}")
        if t.gpu_carga is not None:
            partes.append(f"carga {t.gpu_carga:.0f} %")
        lineas.append(", ".join(partes) + ".")
    if t.cpu is not None:
        lineas.append(f"CPU a {_grados(t.cpu)}.")
    elif t.cpu_nombre:
        lineas.append("Temperatura de la CPU: sin dato (hace falta instalar el driver PawnIO).")
    return lineas


def alertas(t: Temperaturas) -> list[str]:
    avisos = []
    if t.cpu is not None and t.cpu >= LIMITE_CPU:
        avisos.append(f"la CPU está a {t.cpu:.0f} °C")
    if t.gpu is not None and t.gpu >= LIMITE_GPU:
        avisos.append(f"la GPU está a {t.gpu:.0f} °C")
    if t.gpu_punto_caliente is not None and t.gpu_punto_caliente >= LIMITE_GPU_PUNTO_CALIENTE:
        avisos.append(f"el punto caliente de la GPU está a {t.gpu_punto_caliente:.0f} °C")
    return avisos


def alertas_sostenidas(historial, ahora: float, ventana: float = VENTANA_SOSTENIDA) -> list[str]:
    """Calor o memoria por encima de lo normal durante toda la ``ventana`` (lecturas de
    ``VigilanteSalud.historial``: (momento, Temperaturas, % de RAM)). Hace falta que las
    lecturas cubran la ventana entera, para no avisar por un pico."""
    recientes = [(m, t, ram) for m, t, ram in historial if ahora - m <= ventana]
    if len(recientes) < 3 or ahora - recientes[0][0] < ventana * 0.9:
        return []
    avisos = []
    for nombre, valor, limite in (("la CPU", lambda t: t.cpu, LIMITE_SOSTENIDO_CPU),
                                  ("la GPU", lambda t: t.gpu, LIMITE_SOSTENIDO_GPU)):
        valores = [valor(t) for _m, t, _r in recientes]
        if all(v is not None and v >= limite for v in valores):
            avisos.append(f"{nombre} lleva {ventana / 60:.0f} minutos por encima de {limite:.0f} °C "
                          f"(ahora {valores[-1]:.0f} °C)")
    rams = [ram for _m, _t, ram in recientes]
    if all(r >= LIMITE_SOSTENIDO_RAM for r in rams):
        avisos.append(f"la memoria lleva {ventana / 60:.0f} minutos al {rams[-1]:.0f} % "
                      "(di «qué consume más memoria» para ver qué la ocupa)")
    return avisos


class VigilanteSalud:
    """Lee los sensores cada ``intervalo`` s, guarda la última lectura y avisa si se calienta."""

    def __init__(
        self,
        eventos,
        *,
        intervalo: float = INTERVALO_SENSORES,
        leer: Callable[[], Temperaturas] | None = None,
        reloj: Callable[[], float] = time.monotonic,
        discos: Callable[[], list] | None = None,
        memoria: Callable[[], float] | None = None,
    ) -> None:
        self._eventos = eventos
        self._intervalo = intervalo
        self._leer = leer or sensores.leer
        self._reloj = reloj
        self._parar = threading.Event()
        self._sospecha = False
        self._ultimo_aviso = -float("inf")
        self.ultimo = Temperaturas()
        # Últimos ~10 minutos para los gráficos de la app: (momento, temperaturas, % de RAM).
        self.historial: deque[tuple[float, Temperaturas, float]] = deque(maxlen=HISTORIAL)
        self._memoria = memoria
        self._discos = discos  # jarvis_system_info.listar_discos
        self._avisos_disco: dict[str, float] = {}
        self._discos_revisados = -float("inf")
        self._hilo = threading.Thread(target=self._bucle, name="JarvisSensores", daemon=True)

    def start(self) -> None:
        self._hilo.start()

    def stop(self) -> None:
        self._parar.set()

    def revisar_una_vez(self) -> None:
        try:
            self.ultimo = self._leer()
        except Exception:
            logging.debug("No se pudieron leer los sensores.", exc_info=True)
            return
        try:
            ram = self._memoria() if self._memoria else 0.0
        except Exception:
            ram = 0.0
        self.historial.append((self._reloj(), self.ultimo, ram))
        self._revisar_sostenidas()
        avisos = alertas(self.ultimo)
        if not avisos:
            self._sospecha = False
            return
        if not self._sospecha:
            self._sospecha = True  # se confirma en la próxima lectura
            return
        if self._reloj() - self._ultimo_aviso < PAUSA_AVISOS:
            return
        self._ultimo_aviso = self._reloj()
        self._sospecha = False
        texto = "Atención: " + " y ".join(avisos) + "."
        logging.warning("Aviso de temperatura: %s", texto)
        self._eventos.put(("health_alert", texto))

    def _revisar_sostenidas(self) -> None:
        """Aviso anticipado (no urgente): calor o memoria altos 2 minutos seguidos."""
        ahora = self._reloj()
        if ahora - getattr(self, "_ultimo_aviso_sostenido", -float("inf")) < PAUSA_AVISOS:
            return
        avisos = alertas_sostenidas(self.historial, ahora)
        if not avisos:
            return
        self._ultimo_aviso_sostenido = ahora
        texto = "Ojo: " + " y ".join(avisos) + "."
        logging.warning("Aviso anticipado: %s", texto)
        self._eventos.put(("health_notice", texto))

    def revisar_discos(self) -> None:
        """Avisa de cada disco al 90 % o más, como mucho una vez al día por disco y nivel
        (90 % y 97 %); la app recuerda el día entre reinicios (``disk_alert`` lleva la clave)."""
        if self._discos is None:
            return
        ahora = self._reloj()
        self._discos_revisados = ahora
        try:
            discos = self._discos()
        except Exception:
            logging.debug("No se pudo revisar el espacio de los discos.", exc_info=True)
            return
        for disco in discos:
            if disco.usado_pct < LIMITE_DISCO:
                continue
            nivel = "critico" if disco.usado_pct >= LIMITE_DISCO_CRITICO else "lleno"
            clave = f"{disco.unidad}:{nivel}"
            if ahora - self._avisos_disco.get(clave, -float("inf")) < PAUSA_AVISOS_DISCO:
                continue
            self._avisos_disco[clave] = ahora
            libres = disco.libre / 1024**3
            urgencia = "casi sin espacio: " if nivel == "critico" else ""
            texto = (f"El disco {disco.titulo} está {urgencia}al {disco.usado_pct:.0f} % (quedan {libres:.0f} GB). "
                     "Di «libera espacio» si quieres que haga limpieza.")
            logging.warning("Aviso de disco casi lleno: %s %.0f %%.", disco.unidad, disco.usado_pct)
            self._eventos.put(("disk_alert", texto, clave))

    def _bucle(self) -> None:
        while not self._parar.is_set():
            self.revisar_una_vez()
            if self._reloj() - self._discos_revisados >= INTERVALO_DISCOS:
                self.revisar_discos()
            self._parar.wait(self._intervalo)
        sensores.cerrar()
