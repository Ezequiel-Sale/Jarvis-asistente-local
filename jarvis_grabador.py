"""«Mirá cómo lo hago»: Jarvis observa una tarea que hacés una vez y la repite cuando se la pedís.

Grabar: con ganchos de bajo nivel de mouse y teclado (solo mientras dura la grabación, con aviso
en la barra) se anota QUÉ tocás, no dónde: el botón o campo que está bajo el mouse según la
accesibilidad de Windows (tipo y nombre) y su programa. Al escribir se guarda el valor final del
campo (o, si el campo no lo expone, lo tecleado). Abrir un programa se anota como «abrir» con la
ruta de su ejecutable (los clics en la barra de tareas o el menú Inicio no se guardan).
Nunca se graba: campos de contraseña, ventanas protegidas (bancos, pagos, gestores de
contraseñas) ni Jarvis. Nada de lo grabado va al registro; se guarda en ``grabaciones/`` (fuera
de git) y se crea un comando por frase con su nombre.

Repetir: cada paso por ``tool_registry.execute`` (open_program, window_action, type_text,
press_keys), buscando el control por tipo y nombre. Si alguno no aparece, lo que falta lo
termina el piloto con la descripción de los pasos.
"""

from __future__ import annotations

import ctypes
import json
import logging
import queue
import re
import threading
import time
import unicodedata
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_PASOS = 60
NAVEGADORES = frozenset({"brave"})  # browser_tab maneja las pestañas de Brave
MAX_SEGUNDOS = 600
SHELL = frozenset({"explorer", "searchhost", "startmenuexperiencehost", "shellexperiencehost", "searchapp",
                   "textinputhost", "lockapp", "jarvis"})
TIPOS_CAMPO = frozenset({"EditControl", "ComboBoxControl", "DocumentControl"})
_MODIFICADORES = {0x10: "shift", 0xA0: "shift", 0xA1: "shift", 0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
                  0x12: "alt", 0xA4: "alt", 0xA5: "alt", 0x5B: "win", 0x5C: "win"}
_ESPECIALES = {0x0D: "enter", 0x09: "tab", 0x1B: "esc", 0x08: "backspace", 0x2E: "supr", 0x26: "arriba",
               0x28: "abajo", 0x25: "izquierda", 0x27: "derecha", 0x24: "inicio", 0x23: "fin", 0x21: "repag",
               0x22: "avpag", **{0x6F + n: f"f{n}" for n in range(1, 13)}}


@dataclass(frozen=True)
class Paso:
    tipo: str              # abrir | click | completar | escribir | teclas
    programa: str = ""
    control: str = ""      # tipo de control UIA (ButtonControl…)
    nombre: str = ""       # nombre del control
    valor: str = ""        # texto, atajo o ruta del ejecutable
    orden: int = 0         # entre los de igual tipo y nombre
    etiqueta: str = ""     # cómo se llama para el usuario («Calculadora»), del título de la ventana


def etiqueta_de(titulo: str, programa: str) -> str:
    """«Sin título: Bloc de notas» → «Bloc de notas»; «Calculadora» → «Calculadora»."""
    partes = [p.strip() for p in re.split(r"\s+[-–—]\s+|:\s+", titulo or "") if p.strip()]
    return (partes[-1] if partes else programa)[:40]


# Lo que Brave/Chrome le agregan al nombre de una pestaña y cambia solo (2026-11-19: «IlloJuan - Twitch:
# Reproducción de audio: Uso de memoria: 772 MB» no se volvía a encontrar al repetir).
_SUFIJOS = re.compile(
    r"(?::\s*|\s+-\s+)(?:reproducci[oó]n de audio|audio silenciado|silenciad[ao]|uso de memoria:?\s*[\d.,]+\s*[kmg]?b|"
    r"fijad[ao]|cargando|grabando[^:]*|compartiendo[^:]*|usando (?:la )?(?:c[aá]mara|micr[oó]fono)[^:]*|"
    r"playing audio|muted|memory usage:?\s*[\d.,]+\s*[kmg]?b|pinned|loading)\s*$", re.IGNORECASE)


def nombre_estable(nombre: str) -> str:
    """El nombre sin lo que cambia solo (audio, memoria, fijada…), para grabar y para buscar."""
    anterior = None
    nombre = " ".join((nombre or "").split())
    while anterior != nombre:
        anterior, nombre = nombre, _SUFIJOS.sub("", nombre).strip()
    return nombre


def _clave(nombre: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFKD", nombre_estable(nombre).casefold())).split())


def parecido(buscado: str, candidatos: list[str]) -> int | None:
    """Índice del candidato que es el mismo control aunque su nombre haya cambiado un poco:
    igual sin los sufijos, uno empieza como el otro (si es largo), o muy parecido."""
    from difflib import SequenceMatcher

    clave = _clave(buscado)
    if not clave:
        return None
    claves = [_clave(c) for c in candidatos]
    for i, c in enumerate(claves):
        if c == clave:
            return i
    for i, c in enumerate(claves):
        if len(clave) >= 8 and c and (c.startswith(clave) or clave.startswith(c) and len(c) >= 8):
            return i
    puntajes = [(SequenceMatcher(None, clave, c).ratio(), i) for i, c in enumerate(claves) if c]
    mejor = max(puntajes, default=(0.0, None))
    return mejor[1] if mejor[0] >= 0.85 else None


def describir_paso(paso: Paso) -> str:
    donde = paso.etiqueta or paso.programa
    if paso.tipo == "abrir":
        return f"Abrir {donde}"
    if paso.tipo == "click":
        return f"Pulsar «{paso.nombre}» en {donde}"
    if paso.tipo == "click_rueda":
        return f"Abrir «{paso.nombre}» en una pestaña nueva (clic con la rueda) en {donde}"
    if paso.tipo == "completar":
        return f"Escribir «{paso.valor}» en «{paso.nombre or 'el campo'}» de {donde}"
    if paso.tipo == "escribir":
        return f"Escribir «{paso.valor}» en {donde}"
    return f"Apretar {paso.valor} en {donde}"


def describir(pasos: list[Paso]) -> str:
    return "; ".join(f"{i}. {describir_paso(p)}" for i, p in enumerate(pasos, 1))


def limpiar(pasos: list[Paso]) -> list[Paso]:
    """Junta lo repetido: dos «completar» seguidos del mismo campo, «abrir» duplicado…"""
    salida: list[Paso] = []
    for paso in pasos:
        if salida:
            ultimo = salida[-1]
            if paso.tipo == "completar" and ultimo.tipo == "completar" and (ultimo.programa, ultimo.nombre) == (
                    paso.programa, paso.nombre):
                salida[-1] = paso
                continue
            if paso.tipo == "abrir" and ultimo.tipo == "abrir" and ultimo.programa == paso.programa:
                continue
            if paso.tipo == "escribir" and ultimo.tipo == "escribir" and ultimo.programa == paso.programa:
                salida[-1] = Paso("escribir", paso.programa, valor=ultimo.valor + paso.valor, etiqueta=paso.etiqueta)
                continue
        salida.append(paso)
    return salida[:MAX_PASOS]


# --- Guardar y cargar -----------------------------------------------------------------------

def carpeta() -> Path:
    import jarvis_core as core

    destino = core.APP_DIR / "grabaciones"
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _archivo(nombre: str) -> Path:
    normal = unicodedata.normalize("NFKD", nombre.casefold())
    normal = re.sub(r"[^a-z0-9]+", "-", "".join(c for c in normal if not unicodedata.combining(c))).strip("-")
    if not normal:
        raise ValueError("Ese nombre no sirve para guardar la grabación.")
    return carpeta() / f"{normal[:60]}.json"


def guardar(nombre: str, pasos: list[Paso]) -> Path:
    ruta = _archivo(nombre)
    ruta.write_text(json.dumps({"nombre": nombre, "pasos": [asdict(p) for p in pasos]}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return ruta


def cargar(nombre: str) -> list[Paso]:
    ruta = _archivo(nombre)
    if not ruta.exists():
        raise FileNotFoundError(f"No tengo ninguna grabación llamada «{nombre}».")
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return [Paso(**p) for p in datos.get("pasos", [])]


# --- Grabar ---------------------------------------------------------------------------------

class _MSLL(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KBDLL(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


_PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class Grabador:
    """Graba entre ``iniciar`` y ``detener``. Los ganchos solo encolan; un hilo aparte consulta
    la accesibilidad (que tarda) para no trabar el mouse ni el teclado."""

    def __init__(self, reloj=time.monotonic) -> None:
        self._reloj = reloj
        self._eventos: queue.Queue = queue.Queue()
        self.pasos: list[Paso] = []
        self._campo: tuple | None = None     # (programa, control, nombre, orden, objeto UIA) donde se escribe
        self._tecleado = ""
        self._modificadores: set[str] = set()
        self._programas_vistos: set[str] = set()
        self._hilo_ganchos: threading.Thread | None = None
        self._hilo_trabajo: threading.Thread | None = None
        self._id_hilo_ganchos = 0
        self.inicio = 0.0
        self.activo = False
        self.pausado_por = ""  # «ventana protegida» mientras se está en una

    # --- ciclo ---
    def iniciar(self) -> None:
        if self.activo:
            return
        self.activo, self.inicio, self.pasos = True, self._reloj(), []
        self._programas_vistos = set(self._programas_abiertos())
        self._hilo_trabajo = threading.Thread(target=self._trabajar, name="JarvisGrabadorTrabajo", daemon=True)
        self._hilo_trabajo.start()
        self._hilo_ganchos = threading.Thread(target=self._enganchar, name="JarvisGrabadorGanchos", daemon=True)
        self._hilo_ganchos.start()
        logging.info("Grabación de una tarea: empezó.")

    def detener(self) -> list[Paso]:
        if not self.activo:
            return []
        self.activo = False
        if self._id_hilo_ganchos:
            ctypes.windll.user32.PostThreadMessageW(self._id_hilo_ganchos, 0x0012, 0, 0)  # WM_QUIT
        self._eventos.put(("fin",))
        if self._hilo_trabajo is not None:
            self._hilo_trabajo.join(5)
        pasos = limpiar(self.pasos)
        logging.info("Grabación de una tarea: terminó con %s pasos.", len(pasos))
        return pasos

    def vencida(self) -> bool:
        return self.activo and (self._reloj() - self.inicio > MAX_SEGUNDOS or len(self.pasos) >= MAX_PASOS)

    # --- ganchos (hilo con bucle de mensajes) ---
    def _enganchar(self) -> None:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _PROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        self._id_hilo_ganchos = kernel32.GetCurrentThreadId()

        @_PROC
        def raton(codigo, mensaje, datos):
            if codigo == 0 and mensaje in (0x0201, 0x0207):  # WM_LBUTTONDOWN, WM_MBUTTONDOWN (rueda)
                info = ctypes.cast(datos, ctypes.POINTER(_MSLL)).contents
                self._eventos.put(("clic", info.pt.x, info.pt.y, mensaje == 0x0207))
            return user32.CallNextHookEx(None, codigo, mensaje, datos)

        @_PROC
        def teclado(codigo, mensaje, datos):
            if codigo == 0:
                info = ctypes.cast(datos, ctypes.POINTER(_KBDLL)).contents
                if not info.flags & 0x10:  # sin las teclas que inyecta otro programa (Jarvis incluido)
                    self._eventos.put(("tecla", int(info.vkCode), mensaje in (0x0100, 0x0104)))
            return user32.CallNextHookEx(None, codigo, mensaje, datos)

        kernel32.GetModuleHandleW.restype = wintypes.HMODULE  # sin esto el puntero se trunca a 32 bits
        modulo = kernel32.GetModuleHandleW(None)
        gancho_raton = user32.SetWindowsHookExW(14, raton, modulo, 0)    # WH_MOUSE_LL
        gancho_teclado = user32.SetWindowsHookExW(13, teclado, modulo, 0)  # WH_KEYBOARD_LL
        if not gancho_raton or not gancho_teclado:
            logging.warning("No se pudieron poner los ganchos de la grabación (error %s).", ctypes.get_last_error())
        try:
            mensaje = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(mensaje), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(mensaje))
                user32.DispatchMessageW(ctypes.byref(mensaje))
        finally:
            user32.UnhookWindowsHookEx(gancho_raton)
            user32.UnhookWindowsHookEx(gancho_teclado)

    # --- trabajo (accesibilidad) ---
    def _trabajar(self) -> None:
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread():
            while True:
                try:
                    evento = self._eventos.get(timeout=0.5)
                except queue.Empty:
                    self._ver_programa_nuevo()
                    continue
                if evento[0] == "fin":
                    self._cerrar_campo(auto)
                    return
                try:
                    if evento[0] == "clic":
                        self._clic(auto, evento[1], evento[2], rueda=evento[3] if len(evento) > 3 else False)
                    else:
                        self._tecla(auto, evento[1], evento[2])
                except Exception:
                    logging.debug("No se pudo anotar un evento de la grabación.", exc_info=True)

    def _ventana(self, hwnd: int):
        import jarvis_control

        raiz = ctypes.windll.user32.GetAncestor(hwnd, 2)  # GA_ROOT
        return jarvis_control._ventana_de(raiz or hwnd)

    def _programas_abiertos(self) -> list[str]:
        import jarvis_windows

        try:
            return [v.programa for v in jarvis_windows.listar_ventanas() if v.programa]
        except Exception:
            return []

    def _ver_programa_nuevo(self) -> None:
        """Una ventana de un programa que no estaba: se anota «abrir» con su ejecutable."""
        import psutil

        import jarvis_control

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        ventana = jarvis_control._ventana_de(hwnd) if hwnd else None
        if ventana is None or not ventana.programa or ventana.programa in SHELL:
            return
        if ventana.programa in self._programas_vistos:
            return
        self._programas_vistos.add(ventana.programa)
        if jarvis_control.protegida(ventana):
            return
        try:
            ruta = psutil.Process(ventana.pid).exe()
        except Exception:
            ruta = ""
        self.pasos.append(Paso("abrir", ventana.programa, valor=ruta, etiqueta=etiqueta_de(ventana.titulo, ventana.programa)))

    def _protegida_o_propia(self, ventana) -> bool:
        import jarvis_control

        if ventana is None or not ventana.programa or ventana.programa in SHELL:
            return True
        if jarvis_control.protegida(ventana):
            self.pausado_por = "ventana protegida"
            return True
        self.pausado_por = ""
        return False

    def _clic(self, auto, x: int, y: int, rueda: bool = False) -> None:
        self._ver_programa_nuevo()
        self._cerrar_campo(auto)
        control = auto.ControlFromPoint(x, y)
        if control is None:
            return
        ventana = self._ventana(control.NativeWindowHandle or ctypes.windll.user32.WindowFromPoint(wintypes.POINT(x, y)))
        if self._protegida_o_propia(ventana):
            return
        # Del elemento bajo el mouse al primero con nombre que se pueda pulsar o completar.
        for _ in range(4):
            if control is None:
                return
            if control.ControlTypeName in TIPOS_CAMPO or (control.Name and control.ControlTypeName in
                                                          {"ButtonControl", "MenuItemControl", "HyperlinkControl",
                                                           "ListItemControl", "TabItemControl", "CheckBoxControl",
                                                           "RadioButtonControl", "TreeItemControl", "SplitButtonControl"}):
                break
            control = control.GetParentControl()
        else:
            return
        if control is None:
            return
        tipo, nombre = control.ControlTypeName, nombre_estable(control.Name or "")[:80]
        if tipo in TIPOS_CAMPO:
            if getattr(control, "IsPassword", False):
                self._campo = None
                return
            self._campo = (ventana.programa, tipo, nombre, control, etiqueta_de(ventana.titulo, ventana.programa))
            self._tecleado = ""
            return
        # 2026-11-20: el usuario abre favoritos con la rueda (pestaña nueva); antes solo se anotaba el izquierdo.
        self.pasos.append(Paso("click_rueda" if rueda else "click", ventana.programa, tipo, nombre,
                               etiqueta=etiqueta_de(ventana.titulo, ventana.programa)))

    def _tecla(self, auto, vk: int, abajo: bool) -> None:
        if vk in _MODIFICADORES:
            (self._modificadores.add if abajo else self._modificadores.discard)(_MODIFICADORES[vk])
            return
        if not abajo:
            return
        import jarvis_control

        ventana = jarvis_control._ventana_de(ctypes.windll.user32.GetForegroundWindow())
        if self._protegida_o_propia(ventana):
            return
        atajo = self._modificadores - {"shift"}
        if atajo or vk in _ESPECIALES:
            self._cerrar_campo(auto)
            nombre = _ESPECIALES.get(vk) or self._caracter(vk) or ""
            if nombre and not (vk == 0x08 and not atajo):  # el retroceso suelto es parte de escribir
                partes = sorted(self._modificadores, key=["ctrl", "alt", "shift", "win"].index) + [nombre.lower()]
                self.pasos.append(Paso("teclas", ventana.programa, valor="+".join(partes),
                                       etiqueta=etiqueta_de(ventana.titulo, ventana.programa)))
            return
        caracter = self._caracter(vk)
        if not caracter:
            return
        if self._campo is None:
            foco = auto.GetFocusedControl()
            if foco is not None and getattr(foco, "IsPassword", False):
                return
            etiqueta = etiqueta_de(ventana.titulo, ventana.programa)
            if foco is not None and foco.ControlTypeName in TIPOS_CAMPO:
                self._campo = (ventana.programa, foco.ControlTypeName, " ".join((foco.Name or "").split())[:80], foco,
                               etiqueta)
            else:
                self._campo = (ventana.programa, "", "", None, etiqueta)
        self._tecleado += caracter.upper() if "shift" in self._modificadores else caracter

    def _caracter(self, vk: int) -> str:
        if vk == 0x20:
            return " "
        valor = ctypes.windll.user32.MapVirtualKeyW(vk, 2)  # MAPVK_VK_TO_CHAR
        return chr(valor & 0xFFFF).lower() if valor and not valor & 0x80000000 else ""

    def _cerrar_campo(self, auto) -> None:
        """Anota lo escrito en el último campo: su valor final si se puede leer; si no, lo tecleado."""
        if self._campo is None:
            return
        programa, tipo, nombre, control, etiqueta = self._campo
        self._campo = None
        valor = ""
        if control is not None:
            try:
                if getattr(control, "IsPassword", False):
                    return
                patron = control.GetValuePattern()
                valor = (patron.Value or "").strip() if patron else ""
            except Exception:
                valor = ""
        tecleado, self._tecleado = self._tecleado.strip(), ""
        if valor and tipo:
            self.pasos.append(Paso("completar", programa, tipo, nombre, valor[:500], etiqueta=etiqueta))
        elif tecleado:
            self.pasos.append(Paso("escribir", programa, valor=tecleado[:500], etiqueta=etiqueta))


# --- Repetir --------------------------------------------------------------------------------

class Reproductor:
    """Repite pasos grabados con las herramientas de siempre; si uno no se encuentra, lo que
    falta lo termina el piloto (que pregunta aparte lo delicado)."""

    def __init__(self, registry, confirmar, *, piloto=None, dormir=time.sleep, intentos: int = 6) -> None:
        self.registry = registry
        self.confirmar = confirmar
        self.piloto = piloto            # función(objetivo) → texto
        self.dormir = dormir
        self.intentos = intentos
        self._pestanas_nuevas: list[int] = []  # abiertas con la rueda al repetir, en orden

    def _ejecutar(self, herramienta: str, argumentos: dict) -> str:
        return str(self.registry.execute(herramienta, argumentos, confirmed=True, origin="grabacion"))

    def _ventana_de(self, programa: str):
        import jarvis_control
        import jarvis_windows

        for _ in range(self.intentos):
            candidatas = [v for v in jarvis_windows.listar_ventanas() if v.programa == programa]
            if candidatas:
                jarvis_control.traer_al_frente(candidatas[0].hwnd)
                jarvis_control.fijar(candidatas[0])
                return candidatas[0]
            self.dormir(0.5)
        return None

    def _numero(self, paso: Paso) -> int | None:
        import jarvis_control

        for _ in range(self.intentos):
            foto = jarvis_control.fotografiar()
            iguales = [e for e in foto.elementos if e.tipo == paso.control and e.nombre == paso.nombre]
            if iguales:
                return iguales[min(paso.orden, len(iguales) - 1)].n
            mismos_tipo = [e for e in foto.elementos if e.tipo == paso.control]
            indice = parecido(paso.nombre, [e.nombre for e in mismos_tipo])
            if indice is not None:
                return mismos_tipo[indice].n
            self.dormir(0.5)
        return None

    def repetir(self, pasos: list[Paso], nombre: str = "la tarea") -> str:
        import jarvis_control

        hechos = 0
        try:
            for i, paso in enumerate(pasos):
                if paso.tipo == "abrir":
                    if self._ventana_de(paso.programa) is None:
                        if not paso.valor:
                            return self._seguir_con_piloto(pasos[i:], nombre, hechos)
                        self._ejecutar("open_program", {"target": paso.valor})
                        if self._ventana_de(paso.programa) is None:
                            return self._seguir_con_piloto(pasos[i:], nombre, hechos)
                    hechos += 1
                    continue
                if self._ventana_de(paso.programa) is None:
                    return self._seguir_con_piloto(pasos[i:], nombre, hechos)
                if paso.tipo == "click" and paso.programa in NAVEGADORES and paso.control == "TabItemControl":
                    # 2026-11-20: las pestañas de un grupo (o con muchas abiertas) miden 2 px y la lectura
                    # las descarta; la herramienta de pestañas las encuentra por título aunque no se vean.
                    # Si se acaba de abrir (clic con la rueda), espera a que cargue y muestre su título.
                    if not self._ir_a_pestana(paso):
                        return self._seguir_con_piloto(pasos[i:], nombre, hechos)
                    hechos += 1
                    self.dormir(0.3)
                    continue
                if paso.tipo == "click_rueda":
                    numero = self._numero(paso)
                    if numero is None:
                        return self._seguir_con_piloto(pasos[i:], nombre, hechos)
                    antes = self._titulos() if paso.programa in NAVEGADORES else []
                    self._ejecutar("window_action", {"n": numero, "action": "middle_click"})
                    if paso.programa in NAVEGADORES:
                        self._recordar_pestana_nueva(antes)
                    hechos += 1
                    self.dormir(0.5)
                    continue
                if paso.tipo in ("click", "completar"):
                    numero = self._numero(paso)
                    if numero is None:
                        return self._seguir_con_piloto(pasos[i:], nombre, hechos)
                    if paso.tipo == "click":
                        if jarvis_control.sensible(paso.nombre) and not self.confirmar(
                                self.registry.get("window_action"), {"n": numero, "action": "click"}):
                            return f"Paré antes de pulsar «{paso.nombre}»: no lo aprobaste. Hice {hechos} pasos."
                        self._ejecutar("window_action", {"n": numero, "action": "click"})
                    else:
                        self._ejecutar("window_action", {"n": numero, "action": "set_text", "text": paso.valor})
                elif paso.tipo == "escribir":
                    self._ejecutar("type_text", {"text": paso.valor})
                elif paso.tipo == "teclas":
                    if jarvis_control.atajo_delicado(paso.valor, paso.programa) and not self.confirmar(
                            self.registry.get("press_keys"), {"keys": paso.valor}):
                        return f"Paré antes de apretar {paso.valor}: no lo aprobaste. Hice {hechos} pasos."
                    self._ejecutar("press_keys", {"keys": paso.valor})
                hechos += 1
                self.dormir(0.3)
        finally:
            jarvis_control.fijar(None)
        return f"Listo: repetí «{nombre}» ({hechos} pasos)."

    def _titulos(self) -> list[str]:
        import jarvis_pestanas

        try:
            return jarvis_pestanas.titulos_actuales()
        except Exception:
            logging.debug("No se pudieron leer las pestañas de Brave.", exc_info=True)
            return []

    def _recordar_pestana_nueva(self, antes: list[str]) -> None:
        """Tras abrir algo con la rueda: qué número tiene la pestaña nueva. 2026-11-20: ZonaLeRoS muestra
        «Just a moment…» (Cloudflare) hasta que alguien mira la pestaña, así que por título no aparece."""
        import jarvis_pestanas

        for _ in range(10):
            self.dormir(0.5)
            nueva = jarvis_pestanas.pestana_nueva(antes, self._titulos())
            if nueva is not None:
                self._pestanas_nuevas.append(nueva)
                return

    def _ir_a_pestana(self, paso: Paso) -> bool:
        from jarvis_tool_registry import ToolCancelledError

        titulo = nombre_estable(paso.nombre)[:100]
        for intento in range(max(1, self.intentos)):
            try:
                self._ejecutar("browser_tab", {"action": "go", "title": titulo})
                return True
            except ToolCancelledError:
                raise
            except Exception:  # el registro deja pasar el error del handler («No encontré ninguna pestaña…»)
                if self._pestanas_nuevas:  # la que se acaba de abrir, aunque todavía no tenga su título
                    numero = self._pestanas_nuevas.pop(0)
                    self._ejecutar("browser_tab", {"action": "number", "number": numero})
                    return True
                self.dormir(1.0)
        return False

    def _seguir_con_piloto(self, faltan: list[Paso], nombre: str, hechos: int) -> str:
        if self.piloto is None:
            return f"No encontré lo que sigue de «{nombre}» ({describir_paso(faltan[0])}). Hice {hechos} pasos."
        logging.info("Grabación «%s»: el paso %s no apareció; sigue el piloto.", nombre, hechos + 1)
        objetivo = f"Terminá de repetir «{nombre}» haciendo estos pasos: {describir(faltan)}"[:500]
        return f"Hice {hechos} pasos y el resto lo terminó el piloto: {self.piloto(objetivo)}"


# --- Frases -------------------------------------------------------------------------------

def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", (texto or "").casefold())
    return " ".join(re.sub(r"[^\w\s]", " ", "".join(c for c in texto if not unicodedata.combining(c))).split())


_PREFIJO = r"^(?:(?:hey|oye)\s+)?(?:jarvis\s+)?"
_EMPEZAR = re.compile(_PREFIJO + r"(?:mira|fijate|observa|aprende)\w*\s+(?:como|lo\s+que)\s+(?:lo\s+)?(?:hago|hare|voy\s+a\s+hacer)"
                      r"|^(?:(?:hey|oye)\s+)?(?:jarvis\s+)?(?:graba\w*|aprende\w*)\s+(?:lo\s+que\s+hago|como\s+lo\s+hago|esta\s+tarea)$")
_TERMINAR = re.compile(_PREFIJO + r"(?:listo|ya\s+esta|termine|eso\s+es\s+todo|ya\s+termine)\s*"
                       r"(?:(?:,\s*)?(?:guarda\w*|llama\w*|ponele\s+de\s+nombre|nombra\w*)\s+(?:como\s+|lo\s+)?(?P<nombre>.+))?$")
_CANCELAR = re.compile(_PREFIJO + r"(?:cancela|olvida|descarta|borra)\w*\s+(?:la\s+)?grabacion$|" + _PREFIJO.lstrip("^")
                       + r"no\s+(?:la\s+)?guardes$")


def detectar_inicio(texto: str) -> bool:
    return _EMPEZAR.match(_normal(texto)) is not None


def detectar_fin(texto: str) -> tuple[bool, str] | None:
    """(guardar, nombre) mientras se graba: «listo, guardalo como pedir turno» → (True, «pedir turno»)."""
    consulta = _normal(texto)
    if _CANCELAR.match(consulta):
        return False, ""
    m = _TERMINAR.match(consulta)
    if m is None:
        return None
    return True, (m.group("nombre") or "").strip()
