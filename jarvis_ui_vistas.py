"""Vistas de la barra lateral para lo nuevo (2026-11-20, pedido del usuario):

- SISTEMA: ajustes de Windows (luz nocturna, modo oscuro, Wi-Fi, Bluetooth), red y encendido
  remoto, y la salud de la PC (doctor).
- PROGRAMAS: actualizaciones disponibles (winget) y programas que arrancan con Windows.
- MENSAJES: cuentas de Google y Outlook, correos sin leer, la agenda de la semana y WhatsApp.
- TAREAS: lo grabado con «mirá cómo lo hago» y lo último que copiaste.

Los datos se leen en hilos aparte y llegan por ``("vista", nombre, datos)``; el dibujo es en el
hilo de Tk. Cada botón usa las mismas herramientas que la voz (``_start_direct_tool_action``):
lo que es de riesgo alto sigue pidiendo su tarjeta de aprobación.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from jarvis_theme import BG, BORDER, CYAN, ERROR, HOVER, MUTED, PANEL, PANEL_LIGHT, TEXT

VISTAS = ("sistema", "programas", "mensajeria", "tareas")
TITULOS = {"sistema": "SISTEMA", "programas": "PROGRAMAS Y ACTUALIZACIONES", "mensajeria": "CORREO, AGENDA Y WHATSAPP",
           "tareas": "TAREAS GRABADAS Y PORTAPAPELES"}
BOTONES = (("sistema", "🖥   SISTEMA"), ("programas", "📦   PROGRAMAS"), ("mensajeria", "💬   MENSAJES"),
           ("tareas", "🎬   TAREAS"))
NOMBRES_AJUSTES = {"night_light": "Luz nocturna", "dark_mode": "Modo oscuro de Windows", "wifi": "Wi-Fi",
                   "bluetooth": "Bluetooth"}


# --- Lectura de datos (hilos aparte, sin Tk) ---------------------------------------------------

def datos_sistema() -> dict:
    import jarvis_ajustes
    import jarvis_encendido
    import jarvis_red

    datos: dict = {"ajustes": {}, "red": [], "encendido": [], "errores": []}
    for ajuste in jarvis_ajustes.AJUSTES:
        try:
            datos["ajustes"][ajuste] = jarvis_ajustes.estado(ajuste)
        except Exception as error:
            datos["ajustes"][ajuste] = None
            datos["errores"].append(f"{NOMBRES_AJUSTES[ajuste]}: {error}")
    try:
        datos["red"] = [(i.nombre, i.habilitada, i.conectada) for i in jarvis_red.interfaces()]
    except Exception:
        logging.debug("No se pudo leer la red para la vista.", exc_info=True)
    try:
        datos["encendido"] = list(jarvis_encendido.diagnostico().get("tips") or [])
    except Exception:
        logging.debug("No se pudo leer el encendido remoto para la vista.", exc_info=True)
    return datos


def datos_salud(pregunta: str) -> str:
    import jarvis_doctor

    try:
        return jarvis_doctor.consultar(pregunta, 24 * 7 if pregunta != "disks" else 48)
    except Exception as error:
        return f"No pude revisar: {error}"


def datos_programas() -> dict:
    import jarvis_ajustes
    import jarvis_winget

    datos: dict = {"actualizaciones": [], "inicio": [], "error_winget": ""}
    try:
        datos["actualizaciones"] = [(p.nombre, p.id, p.version, p.disponible) for p in jarvis_winget.actualizaciones()]
    except Exception as error:
        datos["error_winget"] = str(error)
    try:
        datos["inicio"] = [(p.nombre, p.activo) for p in jarvis_ajustes.listar_inicio()]
    except Exception:
        logging.debug("No se pudieron leer los programas de inicio para la vista.", exc_info=True)
    return datos


def datos_mensajeria() -> dict:
    from datetime import datetime, timedelta

    import jarvis_correo

    datos: dict = {"cuentas": [], "correos": [], "eventos": [], "errores": []}
    try:
        datos["cuentas"] = jarvis_correo.conectadas()
    except Exception:
        return datos
    correos = []
    for cuenta in datos["cuentas"]:
        try:
            correos += (jarvis_correo.google_no_leidos if cuenta == "google" else jarvis_correo.outlook_no_leidos)(10)
        except Exception as error:
            datos["errores"].append(f"Correo de {cuenta}: {error}")
    jarvis_correo._ultimos = correos  # «LEER» usa la misma numeración que «leé el segundo»
    datos["correos"] = [(jarvis_correo._nombre(c.de), c.asunto) for c in correos]
    desde = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    for cuenta in datos["cuentas"]:
        try:
            leer = jarvis_correo.google_eventos if cuenta == "google" else jarvis_correo.outlook_eventos
            datos["eventos"] += [(e.inicio, e.titulo, e.todo_el_dia) for e in leer(desde, desde + timedelta(days=7))]
        except Exception as error:
            datos["errores"].append(f"Calendario de {cuenta}: {error}")
    datos["eventos"].sort(key=lambda e: e[0])
    return datos


def datos_tareas(portapapeles=None) -> dict:
    import jarvis_grabador

    grabaciones = []
    try:
        for archivo in sorted(jarvis_grabador.carpeta().glob("*.json"), key=lambda a: a.stat().st_mtime, reverse=True):
            try:
                contenido = json.loads(archivo.read_text(encoding="utf-8"))
                pasos = [jarvis_grabador.Paso(**p) for p in contenido.get("pasos", [])]
                grabaciones.append((contenido.get("nombre") or archivo.stem, pasos, archivo))
            except (ValueError, TypeError):
                continue
    except OSError:
        logging.debug("No se pudieron leer las grabaciones para la vista.", exc_info=True)
    copiados = [c.texto for c in portapapeles.recientes()[:10]] if portapapeles is not None else []
    return {"grabaciones": grabaciones, "copiados": copiados}


# --- Mixin de la interfaz ------------------------------------------------------------------------

class VistasMixin:
    # Construcción ------------------------------------------------------------------------------
    def _build_vistas(self, paginas: dict) -> None:
        self._vista_cuerpos: dict[str, tk.Frame] = {}
        self._vista_estado: dict[str, tk.Label] = {}
        self._vista_cargada: dict[str, float] = {}
        for nombre in VISTAS:
            cuerpo = self._vista_desplazable(paginas[nombre], nombre)
            self._vista_cuerpos[nombre] = cuerpo

    def _vista_desplazable(self, page: tk.Frame, nombre: str) -> tk.Frame:
        cabecera = tk.Frame(page, bg=BG, padx=28, pady=12)
        cabecera.pack(fill="x")
        estado = tk.Label(cabecera, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10), anchor="w")
        estado.pack(side="left")
        self._vista_estado[nombre] = estado
        self._ui_button(cabecera, "⟳  ACTUALIZAR", lambda n=nombre: self._cargar_vista(n, forzar=True),
                        fg=MUTED).pack(side="right")
        lienzo = tk.Canvas(page, bg=BG, highlightthickness=0, bd=0)
        barra = ttk.Scrollbar(page, orient="vertical", command=lienzo.yview, style="Jarvis.Vertical.TScrollbar")
        lienzo.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y", padx=(0, 6), pady=8)
        lienzo.pack(side="left", fill="both", expand=True)
        if nombre == "programas":  # barras de las descargas en curso, fuera de lo que se redibuja
            self._progreso_marco = tk.Frame(page, bg=BG, padx=28)
            self._progreso_marco.pack(fill="x", before=lienzo)
            self._progresos: dict[str, tuple] = {}
        cuerpo = tk.Frame(lienzo, bg=BG, padx=28, pady=4)
        ventana = lienzo.create_window((0, 0), window=cuerpo, anchor="nw")
        cuerpo.bind("<Configure>", lambda _e: lienzo.configure(scrollregion=lienzo.bbox("all")))
        lienzo.bind("<Configure>", lambda e: lienzo.itemconfigure(ventana, width=e.width))
        cuerpo.jarvis_scroll = lienzo
        lienzo.jarvis_scroll = lienzo
        return cuerpo

    def _seccion(self, padre: tk.Frame, titulo: str, nota: str = "") -> tk.Frame:
        marco = tk.Frame(padre, bg=PANEL, padx=18, pady=14, highlightthickness=1, highlightbackground=BORDER)
        marco.pack(fill="x", pady=(0, 14))
        tk.Label(marco, text=titulo, bg=PANEL, fg=CYAN, font=("Consolas", 10, "bold"), anchor="w").pack(fill="x")
        if nota:
            etiqueta = tk.Label(marco, text=nota, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w",
                                justify="left", wraplength=760)
            etiqueta.pack(fill="x", pady=(2, 6))
            etiqueta.bind("<Configure>", lambda e: e.width > 50 and etiqueta.configure(wraplength=e.width - 8))
        return marco

    def _fila(self, padre: tk.Frame, texto: str, *, detalle: str = "", color: str = TEXT,
              botones: tuple = ()) -> tk.Frame:
        fila = tk.Frame(padre, bg=PANEL)
        fila.pack(fill="x", pady=3)
        for texto_boton, comando, color_boton in reversed(botones):
            tk.Button(fila, text=texto_boton, command=comando, bg=PANEL_LIGHT, fg=color_boton or CYAN,
                      activebackground=HOVER, activeforeground=TEXT, relief="flat", padx=10, pady=4,
                      font=("Consolas", 8, "bold"), cursor="hand2").pack(side="right", padx=(6, 0))
        tk.Label(fila, text=texto, bg=PANEL, fg=color, font=("Segoe UI", 10), anchor="w").pack(side="left")
        if detalle:
            tk.Label(fila, text=detalle, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(side="left", padx=(10, 0))
        return fila

    def _texto(self, padre: tk.Frame, texto: str, color: str = TEXT) -> tk.Label:
        etiqueta = tk.Label(padre, text=texto, bg=PANEL, fg=color, font=("Segoe UI", 10), anchor="w",
                            justify="left", wraplength=760)
        etiqueta.pack(fill="x", pady=2)
        # El ancho real del panel (2026-11-20: con la ventana angosta el texto se cortaba a la derecha).
        etiqueta.bind("<Configure>", lambda e: e.width > 50 and etiqueta.configure(wraplength=e.width - 8))
        return etiqueta

    # Carga ---------------------------------------------------------------------------------------
    def _cargar_vista(self, nombre: str, *, forzar: bool = False) -> None:
        """Lee los datos en otro hilo. Sin «forzar», no relee si se leyó hace menos de 1 minuto."""
        if not forzar and time.monotonic() - self._vista_cargada.get(nombre, -1e9) < 60:
            return
        self._vista_cargada[nombre] = time.monotonic()
        self._vista_estado[nombre].configure(text="Leyendo…")
        portapapeles = getattr(self, "portapapeles", None)
        lectores = {"sistema": datos_sistema, "programas": datos_programas, "mensajeria": datos_mensajeria,
                    "tareas": lambda: datos_tareas(portapapeles)}

        def trabajar() -> None:
            try:
                datos = lectores[nombre]()
            except Exception as error:
                logging.exception("No se pudo leer la vista %s.", nombre)
                datos = {"error": str(error)}
            self.events.put(("vista", nombre, datos))

        threading.Thread(target=trabajar, name=f"JarvisVista-{nombre}", daemon=True).start()

    def _render_vista(self, nombre: str, datos: dict) -> None:
        cuerpo = self._vista_cuerpos[nombre]
        for hijo in cuerpo.winfo_children():
            hijo.destroy()
        self._vista_estado[nombre].configure(text=f"Actualizado a las {time.strftime('%H:%M')}.")
        if "error" in datos:
            self._texto(self._seccion(cuerpo, "NO PUDE LEER ESTA VISTA"), datos["error"], ERROR)
            return
        {"sistema": self._render_sistema, "programas": self._render_programas, "mensajeria": self._render_mensajeria,
         "tareas": self._render_tareas}[nombre](cuerpo, datos)

    def _accion_vista(self, herramienta: str, argumentos: dict, comando: str) -> None:
        """Un botón: la misma herramienta que por voz (con su tarjeta si es de riesgo alto)."""
        if self.busy:
            self._set_status("ESPERÁ A QUE TERMINE LO ANTERIOR", MUTED)
            return
        self._vista_pendiente = self._current_page
        self._start_direct_tool_action(herramienta, argumentos, comando)

    def _refrescar_tras_accion(self) -> None:
        """Tras una acción lanzada desde una vista, se vuelve a leer esa vista."""
        nombre = getattr(self, "_vista_pendiente", None)
        self._vista_pendiente = None
        if nombre in VISTAS:
            self.root.after(800, lambda: self._cargar_vista(nombre, forzar=True))

    # SISTEMA ------------------------------------------------------------------------------------
    def _render_sistema(self, cuerpo: tk.Frame, datos: dict) -> None:
        marco = self._seccion(cuerpo, "AJUSTES DE WINDOWS", "Al instante; también por voz («desactivá la luz nocturna») "
                                                             "y con «deshacé».")
        for ajuste, activo in datos["ajustes"].items():
            nombre = NOMBRES_AJUSTES[ajuste]
            if activo is None:
                self._fila(marco, nombre, detalle="no se pudo leer", color=MUTED)
                continue
            estado = "off" if activo else "on"
            self._fila(marco, nombre, detalle="activado" if activo else "desactivado",
                       botones=(("DESACTIVAR" if activo else "ACTIVAR",
                                 lambda a=ajuste, s=estado, n=nombre: self._accion_vista(
                                     "windows_setting", {"setting": a, "state": s}, f"{n}: {s}"), None),))
        marco = self._seccion(cuerpo, "RED Y ENCENDIDO DESDE EL CELULAR",
                              "Antes de apagar o suspender, si estás por Wi-Fi y el cable está enchufado, Jarvis pasa al cable.")
        for nombre, habilitada, conectada in datos["red"]:
            estado = "conectada" if conectada else ("deshabilitada" if not habilitada else "sin conexión")
            self._fila(marco, nombre, detalle=estado, color=CYAN if conectada else MUTED,
                       botones=(("DESHABILITAR" if habilitada else "HABILITAR",
                                 lambda n=nombre, h=habilitada: self._accion_vista(
                                     "network_adapter", {"name": n, "state": "off" if h else "on"},
                                     f"Placa {n}"), None),))
        for consejo in datos["encendido"]:
            self._texto(marco, f"• {consejo}", MUTED)
        marco = self._seccion(cuerpo, "SALUD DE LA PC", "Lee los registros de Windows (no cambia nada).")
        salida = self._texto(marco, "Elegí qué revisar.", MUTED)
        botones = tk.Frame(marco, bg=PANEL)
        botones.pack(fill="x", pady=(6, 0))
        for texto, pregunta in (("CAÍDAS (7 DÍAS)", "crashes"), ("DISCOS", "disks"), ("ERRORES (7 DÍAS)", "errors")):
            self._ui_button(botones, texto, lambda p=pregunta, s=salida: self._revisar_salud(p, s)).pack(
                side="left", padx=(0, 8))
        for error in datos["errores"]:
            self._texto(marco, error, ERROR)

    def _revisar_salud(self, pregunta: str, etiqueta: tk.Label) -> None:
        etiqueta.configure(text="Revisando…", fg=MUTED)

        def trabajar() -> None:
            texto = datos_salud(pregunta)
            self.root.after(0, lambda: etiqueta.winfo_exists() and etiqueta.configure(text=texto, fg=TEXT))

        threading.Thread(target=trabajar, name="JarvisVistaSalud", daemon=True).start()

    # PROGRAMAS ----------------------------------------------------------------------------------
    def _render_programas(self, cuerpo: tk.Frame, datos: dict) -> None:
        actualizaciones = datos["actualizaciones"]
        marco = self._seccion(cuerpo, f"ACTUALIZACIONES DISPONIBLES ({len(actualizaciones)})",
                              "Con winget. Cada actualización te pide aprobación; cerrá el programa antes.")
        if datos["error_winget"]:
            self._texto(marco, f"No pude consultar winget: {datos['error_winget']}", ERROR)
        elif not actualizaciones:
            self._texto(marco, "Tus programas están al día.", CYAN)
        else:
            self._fila(marco, "", botones=(("ACTUALIZAR TODO", lambda: self._accion_vista(
                "update_programs", {"name": "all"}, "Actualizar todos los programas"), None),))
            for nombre, identificador, version, disponible in actualizaciones:
                self._fila(marco, nombre, detalle=f"{version} → {disponible}",
                           botones=(("ACTUALIZAR", lambda i=identificador, n=nombre: self._accion_vista(
                               "update_programs", {"name": i}, f"Actualizar {n}"), None),))
        inicio = datos["inicio"]
        marco = self._seccion(cuerpo, f"ARRANCAN CON WINDOWS ({sum(1 for _, a in inicio if a)} de {len(inicio)})",
                              "Desactivar uno no lo desinstala: solo deja de abrirse al prender la PC.")
        for nombre, activo in sorted(inicio, key=lambda p: (not p[1], p[0].casefold())):
            self._fila(marco, nombre, detalle="arranca" if activo else "desactivado", color=TEXT if activo else MUTED,
                       botones=(("DESACTIVAR" if activo else "ACTIVAR",
                                 lambda n=nombre, a=activo: self._accion_vista(
                                     "startup_apps", {"action": "disable" if a else "enable", "name": n},
                                     f"Inicio de {n}"), None),))

    def _progreso_winget(self, evento: dict) -> None:
        """Hilo de Tk: una barra por programa que se descarga o instala (``jarvis_winget.al_progreso``)."""
        marco = getattr(self, "_progreso_marco", None)
        if marco is None:
            return
        paquete = evento.get("paquete") or "programa"
        if paquete not in self._progresos:
            fila = tk.Frame(marco, bg=PANEL, padx=14, pady=10, highlightthickness=1, highlightbackground=BORDER)
            fila.pack(fill="x", pady=(0, 8))
            texto = tk.Label(fila, text="", bg=PANEL, fg=TEXT, font=("Segoe UI", 10), anchor="w")
            texto.pack(fill="x")
            barra = ttk.Progressbar(fila, style="Jarvis.Horizontal.TProgressbar", mode="determinate", maximum=100)
            barra.pack(fill="x", pady=(6, 0))
            self._progresos[paquete] = (fila, texto, barra)
        fila, texto, barra = self._progresos[paquete]
        cantidad = evento.get("cantidad") or 1
        prefijo = f"[{evento.get('indice')} de {cantidad}] " if cantidad > 1 else ""
        fase = {"preparando": "preparando", "descargando": "descargando", "verificando": "verificando",
                "instalando": "instalando", "terminado": "listo", "error": "no se pudo"}.get(evento.get("fase"), "")
        texto.configure(text=f"{prefijo}{paquete} — {fase}: {evento.get('detalle', '')}",
                        fg=ERROR if evento.get("fase") == "error" else (CYAN if evento.get("fase") == "terminado" else TEXT))
        fraccion = evento.get("fraccion")
        if fraccion is None and evento.get("fase") != "error":
            if str(barra.cget("mode")) != "indeterminate":
                barra.configure(mode="indeterminate")
                barra.start(12)
        else:
            if str(barra.cget("mode")) != "determinate":
                barra.stop()
                barra.configure(mode="determinate")
            barra["value"] = round((fraccion or 0) * 100)
        if evento.get("fase") in ("terminado", "error"):
            self.root.after(6000, lambda p=paquete: self._quitar_progreso(p))

    def _quitar_progreso(self, paquete: str) -> None:
        fila = self._progresos.pop(paquete, (None,))[0]
        if fila is not None and fila.winfo_exists():
            fila.destroy()

    # MENSAJES -----------------------------------------------------------------------------------
    def _render_mensajeria(self, cuerpo: tk.Frame, datos: dict) -> None:
        marco = self._seccion(cuerpo, "CUENTAS", "Se conectan una sola vez; los pasos para crear las credenciales "
                                                 "están en el README («Conectar Gmail, Google Calendar y Outlook»).")
        for cuenta, titulo in (("google", "Google (Gmail y Calendar)"), ("outlook", "Outlook")):
            conectada = cuenta in datos["cuentas"]
            self._fila(marco, titulo, detalle="conectada" if conectada else "sin conectar",
                       color=CYAN if conectada else MUTED,
                       botones=(() if conectada else (("CONECTAR", lambda c=cuenta: self._accion_vista(
                           "connect_account", {"account": c}, f"Conectar {c}"), None),)))
        if datos["cuentas"]:
            marco = self._seccion(cuerpo, f"CORREOS SIN LEER ({len(datos['correos'])})")
            if not datos["correos"]:
                self._texto(marco, "No tenés correos sin leer.", CYAN)
            for numero, (de, asunto) in enumerate(datos["correos"], 1):
                self._fila(marco, de[:40], detalle=(asunto or "(sin asunto)")[:70],
                           botones=(("LEER", lambda n=numero: self._accion_vista(
                               "email_read", {"number": n}, f"Leer el correo {n}"), None),))
            marco = self._seccion(cuerpo, "AGENDA DE LOS PRÓXIMOS 7 DÍAS")
            if not datos["eventos"]:
                self._texto(marco, "No tenés nada agendado.", MUTED)
            for inicio, titulo, todo_el_dia in datos["eventos"]:
                cuando = inicio.strftime("%a %d/%m") + ("" if todo_el_dia else inicio.strftime(" %H:%M"))
                self._fila(marco, titulo[:70], detalle=cuando)
        marco = self._seccion(cuerpo, "WHATSAPP", "Revisarlo trae WhatsApp al frente un momento y después lo vuelve a ocultar en la bandeja.")
        self._fila(marco, "Mensajes sin leer", botones=(("REVISAR", lambda: self._accion_vista(
            "whatsapp_unread", {}, "Revisar WhatsApp"), None),))
        for error in datos["errores"]:
            self._texto(marco, error, ERROR)

    # TAREAS -------------------------------------------------------------------------------------
    def _render_tareas(self, cuerpo: tk.Frame, datos: dict) -> None:
        import jarvis_grabador

        marco = self._seccion(cuerpo, f"TAREAS GRABADAS ({len(datos['grabaciones'])})",
                              "Grabá una con «mirá cómo lo hago» y guardala con «listo, guardalo como …»; "
                              "después decí su nombre para repetirla.")
        self._fila(marco, "", botones=(("●  GRABAR UNA NUEVA", lambda: self._manejar_grabacion("mirá cómo lo hago"), ERROR),))
        for nombre, pasos, archivo in datos["grabaciones"]:
            self._fila(marco, nombre, detalle=f"{len(pasos)} pasos",
                       botones=(("▶ REPETIR", lambda n=nombre: self._accion_vista(
                                     "replay_recording", {"name": n}, n), None),
                                ("BORRAR", lambda n=nombre, a=archivo: self._borrar_grabacion(n, a), MUTED)))
            if pasos:
                self._texto(marco, "    " + jarvis_grabador.describir(pasos)[:400], MUTED)
        marco = self._seccion(cuerpo, "LO ÚLTIMO QUE COPIASTE",
                              "Solo mientras Jarvis está abierto; no guarda lo copiado de bancos ni contraseñas.")
        if not datos["copiados"]:
            self._texto(marco, "Todavía no copiaste nada.", MUTED)
        for numero, texto in enumerate(datos["copiados"], 1):
            linea = " ".join(texto.split())
            self._fila(marco, linea[:80] + ("…" if len(linea) > 80 else ""),
                       botones=(("COPIAR", lambda n=numero: self._accion_vista(
                           "clipboard_history", {"action": "copy", "number": n}, "Copiar del historial"), None),))

    def _borrar_grabacion(self, nombre: str, archivo) -> None:
        import jarvis_routines

        if not messagebox.askyesno("Borrar tarea grabada", f"¿Borro «{nombre}»? Se borra también su comando por frase.",
                                   parent=self.root):
            return
        try:
            archivo.unlink(missing_ok=True)
            for identificador, nombre_rutina, *_ in jarvis_routines.listar_rutinas():
                if nombre_rutina.casefold() == nombre.casefold():
                    jarvis_routines.eliminar_rutina(identificador)
        except Exception:
            logging.exception("No se pudo borrar la grabación.")
        self._cargar_vista("tareas", forzar=True)
