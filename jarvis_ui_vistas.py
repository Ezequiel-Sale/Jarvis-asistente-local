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

from jarvis_theme import BG, BORDER, CYAN, ERROR, MUTED, PANEL, PANEL_LIGHT, TEXT

VISTAS = ("sistema", "programas", "mensajeria", "tareas")
TITULOS = {"sistema": "SISTEMA", "programas": "PROGRAMAS", "mensajeria": "MENSAJES", "tareas": "TAREAS"}
SUBTITULOS = {
    "sistema": "Ajustes de Windows al instante, la red y la salud de la PC. Todo también por voz y con «deshacé».",
    "programas": "Actualizaciones con winget y programas que arrancan con Windows.",
    "mensajeria": "Correos sin leer, tu agenda de la semana y WhatsApp. También: «¿tengo correos?», «leé el segundo».",
    "tareas": "Lo que le enseñaste con «mirá cómo lo hago» y lo último que copiaste.",
}
DIAS_CORTOS = ("LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM")
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
        self._vista_datos: dict[str, dict] = {}  # lo último leído (los filtros redibujan sin releer)
        for nombre in VISTAS:
            cuerpo = self._vista_desplazable(paginas[nombre], nombre)
            self._vista_cuerpos[nombre] = cuerpo

    def _vista_desplazable(self, page: tk.Frame, nombre: str) -> tk.Frame:
        import jarvis_ui_kit as kit

        cabecera = tk.Frame(page, bg=BG, padx=28, pady=14)
        cabecera.pack(fill="x")
        cabecera.grid_columnconfigure(0, weight=1)
        # El título ya está en la cabecera de la página: acá, qué hay en la vista.
        subtitulo = kit.nota(cabecera, SUBTITULOS[nombre], fondo="BG", fg="MUTED")
        subtitulo.configure(font=("Segoe UI", 10))
        subtitulo.grid(row=0, column=0, sticky="we")
        derecha = tk.Frame(cabecera, bg=BG)
        derecha.grid(row=0, column=1, sticky="e", padx=(16, 0))
        estado = tk.Label(derecha, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="e")
        estado.pack(side="left", padx=(0, 10))
        self._vista_estado[nombre] = estado
        self._ui_button(derecha, "⟳  ACTUALIZAR", lambda n=nombre: self._cargar_vista(n, forzar=True),
                        fg=MUTED).pack(side="left")
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

    def _seccion(self, padre: tk.Frame, titulo: str, nota: str = "", *, empaquetar: bool = True) -> tk.Frame:
        """Una tarjeta con su título (en violeta) y una nota opcional."""
        import jarvis_ui_kit as kit

        marco = kit.tarjeta(padre, padx=20, pady=16)
        if empaquetar:
            marco.pack(fill="x", pady=(0, 16))
        marco.jarvis_scroll = getattr(padre, "jarvis_scroll", None)
        kit.titulo_seccion(marco, titulo).pack(fill="x")
        if nota:
            kit.nota(marco, nota).pack(fill="x", pady=(2, 8))
        else:
            tk.Frame(marco, bg=PANEL, height=6).pack(fill="x")
        return marco

    def _fila(self, padre: tk.Frame, texto: str, *, detalle: str = "", color: str = TEXT,
              botones: tuple = (), fondo: str = "PANEL_LIGHT") -> tk.Frame:
        """Un renglón en caja: texto, detalle y botones a la derecha."""
        import jarvis_ui_kit as kit

        fila = tk.Frame(padre, bg=kit.color(fondo), padx=12, pady=9, highlightthickness=1,
                        highlightbackground=kit.color("BORDER"))
        fila.pack(fill="x", pady=(0, 8))
        for texto_boton, comando, color_boton in reversed(botones):
            kit.boton(fila, texto_boton, comando, fondo="PANEL", fg=None).pack(side="right", padx=(6, 0))
            if color_boton:
                fila.winfo_children()[-1].configure(fg=color_boton)
        textos = tk.Frame(fila, bg=kit.color(fondo))
        textos.pack(side="left", fill="x", expand=True)
        if texto:
            tk.Label(textos, text=texto, bg=kit.color(fondo), fg=color, font=("Segoe UI", 10, "bold"),
                     anchor="w").pack(fill="x")
        if detalle:
            tk.Label(textos, text=detalle, bg=kit.color(fondo), fg=MUTED, font=("Segoe UI", 9),
                     anchor="w").pack(fill="x")
        return fila

    def _texto(self, padre: tk.Frame, texto: str, color: str = TEXT) -> tk.Label:
        import jarvis_ui_kit as kit

        etiqueta = tk.Label(padre, text=texto, bg=padre.cget("bg"), fg=color, font=("Segoe UI", 10), anchor="w",
                            justify="left", wraplength=760)
        etiqueta.pack(fill="x", pady=2)
        # El ancho real del panel (2026-11-20: con la ventana angosta el texto se cortaba a la derecha).
        etiqueta.bind("<Configure>", lambda e: e.width > 50 and kit.postergar(
            etiqueta, lambda: etiqueta.configure(wraplength=e.width - 8), clave="ancho"))
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
        self.__dict__.setdefault("_vista_datos", {})[nombre] = datos
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
        import jarvis_ui_kit as kit

        kit.titulo_seccion(cuerpo, "AJUSTES DE WINDOWS", fondo="BG").pack(fill="x", pady=(0, 10))
        tiles = kit.Rejilla(cuerpo, 200, separacion=14, max_columnas=4)
        tiles.pack(fill="x", pady=(0, 6))
        for ajuste, activo in datos["ajustes"].items():
            nombre = NOMBRES_AJUSTES[ajuste]
            fondo = "CYAN_DARK" if activo else "PANEL"
            tile = kit.tarjeta(tiles, fondo=fondo, borde="BORDER_STRONG" if activo else "BORDER")
            arriba = tk.Frame(tile, bg=kit.color(fondo))
            arriba.pack(fill="x")
            tk.Label(arriba, text=nombre[0], width=3, bg=kit.color("CYAN") if activo else kit.color("PANEL_LIGHT"),
                     fg=kit.color("BG") if activo else kit.color("ORB_BRIGHT"), font=("Segoe UI", 12, "bold"),
                     pady=4).pack(side="left")
            estado = "off" if activo else "on"
            kit.interruptor(arriba, activo, None if activo is None else (
                lambda a=ajuste, s=estado, n=nombre: self._accion_vista(
                    "windows_setting", {"setting": a, "state": s}, f"{n}: {s}")), fondo=fondo).pack(side="right")
            tk.Label(tile, text=nombre, bg=kit.color(fondo), fg=TEXT, font=("Segoe UI", 11, "bold"),
                     anchor="w").pack(fill="x", pady=(14, 0))
            tk.Label(tile, text="no se pudo leer" if activo is None else ("activado" if activo else "desactivado"),
                     bg=kit.color(fondo), fg=kit.color("ORB_BRIGHT") if activo else MUTED, font=("Segoe UI", 9),
                     anchor="w").pack(fill="x")
            tiles.agregar(tile)

        dos = kit.Rejilla(cuerpo, 420, separacion=16, max_columnas=2)
        dos.pack(fill="x", pady=(10, 0))
        marco = self._seccion(dos, "RED Y ENCENDIDO DESDE EL CELULAR",
                              "Antes de apagar o suspender, si estás por Wi-Fi y el cable está enchufado, Jarvis pasa "
                              "al cable.", empaquetar=False)
        dos.agregar(marco)
        for nombre, habilitada, conectada in datos["red"]:
            estado = "conectada" if conectada else ("deshabilitada" if not habilitada else "sin conexión")
            self._fila(marco, f"●  {nombre}", detalle=estado, color=CYAN if conectada else TEXT,
                       botones=(("DESHABILITAR" if habilitada else "HABILITAR",
                                 lambda n=nombre, h=habilitada: self._accion_vista(
                                     "network_adapter", {"name": n, "state": "off" if h else "on"},
                                     f"Placa {n}"), None),))
        for consejo in datos["encendido"]:
            self._texto(marco, f"•  {consejo}", MUTED)

        marco = self._seccion(dos, "SALUD DE LA PC", "Lee los registros de Windows (no cambia nada).",
                              empaquetar=False)
        dos.agregar(marco)
        opciones = tk.Frame(marco, bg=PANEL)
        opciones.pack(fill="x")
        opciones.grid_columnconfigure((0, 1, 2), weight=1, uniform="salud")
        resultado = tk.Frame(marco, bg=PANEL_LIGHT, padx=12, pady=10, highlightthickness=1,
                             highlightbackground=BORDER)
        salida = tk.Label(resultado, text="Elegí qué revisar.", bg=PANEL_LIGHT, fg=MUTED, font=("Segoe UI", 10),
                          anchor="nw", justify="left", wraplength=400)
        salida.pack(fill="both", expand=True)
        salida.bind("<Configure>", lambda e: e.width > 50 and kit.postergar(
            salida, lambda: salida.configure(wraplength=e.width - 8), clave="ancho"))
        for columna, (texto, periodo, pregunta) in enumerate((("CAÍDAS", "Últimos 7 días", "crashes"),
                                                              ("DISCOS", "Estado y espacio", "disks"),
                                                              ("ERRORES", "Últimos 7 días", "errors"))):
            kit.boton(opciones, f"{texto}\n{periodo}", lambda p=pregunta, s=salida: self._revisar_salud(p, s),
                      justify="left", anchor="w", pady=8).grid(
                row=0, column=columna, sticky="nsew", padx=(0, 8) if columna < 2 else 0)
        resultado.pack(fill="both", expand=True, pady=(10, 0))
        for error in datos["errores"]:
            self._texto(marco, error, ERROR)

    def _revisar_salud(self, pregunta: str, etiqueta: tk.Label) -> None:
        etiqueta.configure(text="Revisando…", fg=MUTED)

        def trabajar() -> None:
            texto = datos_salud(pregunta)
            self.root.after(0, lambda: etiqueta.winfo_exists() and etiqueta.configure(text=texto, fg=TEXT))

        threading.Thread(target=trabajar, name="JarvisVistaSalud", daemon=True).start()

    # PROGRAMAS ----------------------------------------------------------------------------------
    FILTROS_INICIO = ("Todos", "Arrancan", "Desactivados")

    def _render_programas(self, cuerpo: tk.Frame, datos: dict) -> None:
        import jarvis_ui_kit as kit

        actualizaciones, inicio = datos["actualizaciones"], datos["inicio"]
        activos = sum(1 for _, a in inicio if a)
        resumen = tk.Frame(cuerpo, bg=BG)
        resumen.pack(fill="x", pady=(0, 14))
        for numero, texto, token in ((str(len(actualizaciones)), "por actualizar", "CYAN"),
                                     (f"{activos} de {len(inicio)}", "arrancan con Windows", "VIOLET")):
            caja = kit.tarjeta(resumen, padx=16, pady=8)
            caja.pack(side="left", padx=(0, 10))
            tk.Label(caja, text=numero, bg=PANEL, fg=kit.color(token), font=("Segoe UI", 16, "bold"),
                     anchor="w").pack(anchor="w")
            tk.Label(caja, text=texto, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(anchor="w")

        marco = self._seccion(cuerpo, f"ACTUALIZACIONES DISPONIBLES ({len(actualizaciones)})",
                              "Con winget. Cada actualización te pide aprobación; cerrá el programa antes.")
        if datos["error_winget"]:
            self._texto(marco, f"No pude consultar winget: {datos['error_winget']}", ERROR)
        elif not actualizaciones:
            self._texto(marco, "Tus programas están al día.", CYAN)
        else:
            kit.boton(marco, "ACTUALIZAR TODO", lambda: self._accion_vista(
                "update_programs", {"name": "all"}, "Actualizar todos los programas"), primario=True).pack(
                anchor="e", pady=(0, 10))
            tarjetas = kit.Rejilla(marco, 260, max_columnas=3, fondo="PANEL")
            tarjetas.pack(fill="x")
            for nombre, identificador, version, disponible in actualizaciones:
                caja = kit.tarjeta(tarjetas, fondo="PANEL_LIGHT", padx=12, pady=12)
                arriba = tk.Frame(caja, bg=PANEL_LIGHT)
                arriba.pack(fill="x")
                tk.Label(arriba, text=(nombre[:1] or "?").upper(), width=3, bg=BORDER, fg=kit.color("ORB_BRIGHT"),
                         font=("Segoe UI", 12, "bold"), pady=4).pack(side="left")
                textos = tk.Frame(arriba, bg=PANEL_LIGHT)
                textos.pack(side="left", fill="x", expand=True, padx=(10, 0))
                tk.Label(textos, text=nombre[:34], bg=PANEL_LIGHT, fg=TEXT, font=("Segoe UI", 10, "bold"),
                         anchor="w").pack(fill="x")
                tk.Label(textos, text=f"{version} → {disponible}", bg=PANEL_LIGHT, fg=CYAN, font=("Consolas", 9),
                         anchor="w").pack(fill="x")
                kit.boton(caja, "ACTUALIZAR", lambda i=identificador, n=nombre: self._accion_vista(
                    "update_programs", {"name": i}, f"Actualizar {n}"), fondo="PANEL").pack(fill="x", pady=(10, 0))
                tarjetas.agregar(caja)

        marco = self._seccion(cuerpo, f"ARRANCAN CON WINDOWS ({activos} de {len(inicio)})",
                              "Desactivar uno no lo desinstala: solo deja de abrirse al prender la PC.")
        filtro = self.__dict__.get("_filtro_inicio", "Todos")
        botones = tk.Frame(marco, bg=PANEL_LIGHT, padx=3, pady=3)
        botones.pack(anchor="w", pady=(0, 10))
        for texto in self.FILTROS_INICIO:
            elegido = texto == filtro
            kit.boton(botones, texto.upper(), lambda t=texto: self._filtrar_inicio(t), primario=elegido,
                      pady=3).pack(side="left", padx=(0, 3))
        visibles = [p for p in inicio if filtro == "Todos" or (p[1] if filtro == "Arrancan" else not p[1])]
        filas = kit.Rejilla(marco, 240, separacion=10, max_columnas=3, fondo="PANEL")
        filas.pack(fill="x")
        for nombre, activo in sorted(visibles, key=lambda p: (not p[1], p[0].casefold())):
            fondo = "PANEL_LIGHT" if activo else "PANEL"
            caja = kit.tarjeta(filas, fondo=fondo, padx=12, pady=10)
            tk.Label(caja, text=nombre[:30], bg=kit.color(fondo), fg=TEXT if activo else MUTED,
                     font=("Segoe UI", 10, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
            kit.interruptor(caja, activo, lambda n=nombre, a=activo: self._accion_vista(
                "startup_apps", {"action": "disable" if a else "enable", "name": n}, f"Inicio de {n}"),
                fondo=fondo).pack(side="right")
            tk.Label(caja, text="arranca" if activo else "desactivado", bg=kit.color(fondo), fg=MUTED,
                     font=("Segoe UI", 8)).pack(side="right", padx=(0, 8))
            filas.agregar(caja)
        if not visibles:
            self._texto(marco, "Ninguno con ese filtro.", MUTED)

    def _filtrar_inicio(self, filtro: str) -> None:
        self._filtro_inicio = filtro
        datos = self.__dict__.get("_vista_datos", {}).get("programas")
        if datos is not None:
            self._render_vista("programas", datos)

    # MENSAJES -----------------------------------------------------------------------------------
    def _render_mensajeria(self, cuerpo: tk.Frame, datos: dict) -> None:
        import jarvis_ui_kit as kit

        cuentas = kit.Rejilla(cuerpo, 280, separacion=14, max_columnas=3)
        cuentas.pack(fill="x", pady=(0, 6))
        for cuenta, titulo, letra in (("google", "Google", "G"), ("outlook", "Outlook", "O")):
            conectada = cuenta in datos["cuentas"]
            fondo = "CYAN_DARK" if conectada else "PANEL"
            caja = kit.tarjeta(cuentas, fondo=fondo, borde="BORDER_STRONG" if conectada else "BORDER")
            tk.Label(caja, text=letra, width=3, bg=kit.color("CYAN") if conectada else kit.color("PANEL_LIGHT"),
                     fg=kit.color("BG") if conectada else kit.color("ORB_BRIGHT"), font=("Segoe UI", 13, "bold"),
                     pady=5).pack(side="left")
            if not conectada:
                kit.boton(caja, "CONECTAR", lambda c=cuenta: self._accion_vista(
                    "connect_account", {"account": c}, f"Conectar {c}")).pack(side="right", padx=(8, 0))
            textos = tk.Frame(caja, bg=kit.color(fondo))
            textos.pack(side="left", fill="x", expand=True, padx=(12, 0))
            tk.Label(textos, text=titulo, bg=kit.color(fondo), fg=TEXT, font=("Segoe UI", 11, "bold"),
                     anchor="w").pack(fill="x")
            tk.Label(textos, text=("Gmail y Calendar · " if cuenta == "google" else "") +
                     ("conectada" if conectada else "sin conectar"), bg=kit.color(fondo),
                     fg=kit.color("ORB_BRIGHT") if conectada else MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x")
            cuentas.agregar(caja)
        caja = kit.tarjeta(cuentas)
        tk.Label(caja, text="W", width=3, bg=kit.color("PANEL_LIGHT"), fg=kit.color("VIOLET"),
                 font=("Segoe UI", 13, "bold"), pady=5).pack(side="left")
        kit.boton(caja, "REVISAR", lambda: self._accion_vista("whatsapp_unread", {}, "Revisar WhatsApp"),
                  fg="VIOLET").pack(side="right", padx=(8, 0))
        textos = tk.Frame(caja, bg=PANEL)
        textos.pack(side="left", fill="x", expand=True, padx=(12, 0))
        tk.Label(textos, text="WhatsApp", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x")
        kit.nota(textos, "Lo trae un momento y lo vuelve a ocultar").pack(fill="x")
        cuentas.agregar(caja)
        kit.nota(cuerpo, "Las cuentas se conectan una sola vez; los pasos para crear las credenciales están en el "
                         "README («Conectar Gmail, Google Calendar y Outlook»).", fondo="BG").pack(fill="x", pady=(0, 14))

        if datos["cuentas"]:
            dos = kit.Rejilla(cuerpo, 420, separacion=16, max_columnas=2)
            dos.pack(fill="x")
            marco = self._seccion(dos, f"CORREOS SIN LEER ({len(datos['correos'])})",
                                  "«Leé el segundo» usa estos números.", empaquetar=False)
            dos.agregar(marco)
            if not datos["correos"]:
                self._texto(marco, "No tenés correos sin leer.", CYAN)
            for numero, (de, asunto) in enumerate(datos["correos"], 1):
                self._fila(marco, f"{numero}.  {de[:40]}", detalle=(asunto or "(sin asunto)")[:70],
                           botones=(("LEER", lambda n=numero: self._accion_vista(
                               "email_read", {"number": n}, f"Leer el correo {n}"), None),))
            marco = self._seccion(dos, "AGENDA DE LOS PRÓXIMOS 7 DÍAS", empaquetar=False)
            dos.agregar(marco)
            if not datos["eventos"]:
                self._texto(marco, "No tenés nada agendado.", MUTED)
            dias: dict = {}
            for inicio, titulo, todo_el_dia in datos["eventos"]:
                dias.setdefault(inicio.date(), []).append((inicio, titulo, todo_el_dia))
            hoy = time.localtime()
            for dia, eventos in dias.items():
                renglon = tk.Frame(marco, bg=PANEL)
                renglon.pack(fill="x", pady=(0, 10))
                fecha = tk.Frame(renglon, bg=PANEL, width=56)
                fecha.pack(side="left", anchor="n")
                es_hoy = (dia.year, dia.month, dia.day) == (hoy.tm_year, hoy.tm_mon, hoy.tm_mday)
                tk.Label(fecha, text="HOY" if es_hoy else DIAS_CORTOS[dia.weekday()], bg=PANEL, fg=MUTED,
                         font=("Segoe UI", 8, "bold")).pack()
                tk.Label(fecha, text=str(dia.day), bg=PANEL, fg=CYAN if es_hoy else TEXT,
                         font=("Segoe UI", 16, "bold")).pack()
                lista = tk.Frame(renglon, bg=PANEL)
                lista.pack(side="left", fill="x", expand=True, padx=(10, 0))
                for inicio, titulo, todo_el_dia in eventos:
                    caja = tk.Frame(lista, bg=PANEL_LIGHT, padx=10, pady=7, highlightthickness=1,
                                    highlightbackground=BORDER)
                    caja.pack(fill="x", pady=(0, 6))
                    tk.Label(caja, text="Todo el día" if todo_el_dia else inicio.strftime("%H:%M"), width=12,
                             bg=PANEL_LIGHT, fg=CYAN, font=("Consolas", 9), anchor="w").pack(side="left")
                    tk.Label(caja, text=titulo[:70], bg=PANEL_LIGHT, fg=TEXT, font=("Segoe UI", 10),
                             anchor="w").pack(side="left", fill="x", expand=True)
        for error in datos["errores"]:
            self._texto(cuerpo, error, ERROR)

    # TAREAS -------------------------------------------------------------------------------------
    def _render_tareas(self, cuerpo: tk.Frame, datos: dict) -> None:
        import jarvis_grabador
        import jarvis_ui_kit as kit

        arriba = tk.Frame(cuerpo, bg=BG)
        arriba.pack(fill="x", pady=(0, 10))
        textos = tk.Frame(arriba, bg=BG)
        textos.pack(side="left", fill="x", expand=True)
        kit.titulo_seccion(textos, f"TAREAS GRABADAS ({len(datos['grabaciones'])})", fondo="BG").pack(fill="x")
        kit.nota(textos, "Grabá una con «mirá cómo lo hago» y guardala con «listo, guardalo como …»; después decí su "
                         "nombre para repetirla.", fondo="BG").pack(fill="x")
        kit.boton(arriba, "●  GRABAR UNA NUEVA", lambda: self._manejar_grabacion("mirá cómo lo hago"),
                  fondo="PANEL", fg="ERROR", pady=8).pack(side="right", padx=(12, 0))
        if not datos["grabaciones"]:
            self._texto(self._seccion(cuerpo, "TODAVÍA NO GRABASTE NINGUNA"),
                        "Decí «mirá cómo lo hago» y hacé la tarea: Jarvis anota cada clic y cada tecla.", MUTED)
        tarjetas = kit.Rejilla(cuerpo, 280, separacion=14, max_columnas=3)
        tarjetas.pack(fill="x", pady=(0, 6))
        for nombre, pasos, archivo in datos["grabaciones"]:
            caja = kit.tarjeta(tarjetas)
            cabeza = tk.Frame(caja, bg=PANEL)
            cabeza.pack(fill="x")
            tk.Label(cabeza, text=nombre[:32], bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"),
                     anchor="w").pack(side="left", fill="x", expand=True)
            kit.ficha(cabeza, f"{len(pasos)} pasos").pack(side="right")
            if pasos:
                kit.nota(caja, jarvis_grabador.describir(pasos[:3])[:220] +
                         (f"  (+{len(pasos) - 3} más)" if len(pasos) > 3 else "")).pack(fill="x", pady=(8, 0))
            botones = tk.Frame(caja, bg=PANEL)
            botones.pack(fill="x", pady=(12, 0))
            kit.boton(botones, "▶ REPETIR", lambda n=nombre: self._accion_vista(
                "replay_recording", {"name": n}, n), primario=True).pack(side="left", fill="x", expand=True)
            kit.boton(botones, "BORRAR", lambda n=nombre, a=archivo: self._borrar_grabacion(n, a),
                      fg="MUTED").pack(side="left", padx=(8, 0))
            tarjetas.agregar(caja)

        marco = self._seccion(cuerpo, "LO ÚLTIMO QUE COPIASTE",
                              "Solo mientras Jarvis está abierto; no guarda lo copiado de bancos ni contraseñas.")
        if not datos["copiados"]:
            self._texto(marco, "Todavía no copiaste nada.", MUTED)
        filas = kit.Rejilla(marco, 360, separacion=10, max_columnas=2, fondo="PANEL")
        filas.pack(fill="x")
        for numero, texto in enumerate(datos["copiados"], 1):
            linea = " ".join(texto.split())
            caja = tk.Frame(filas, bg=PANEL_LIGHT, padx=10, pady=7, highlightthickness=1, highlightbackground=BORDER)
            tk.Label(caja, text=str(numero), width=2, bg=PANEL_LIGHT, fg=CYAN, font=("Segoe UI", 10, "bold")).pack(
                side="left")
            tk.Label(caja, text=linea[:60] + ("…" if len(linea) > 60 else ""), bg=PANEL_LIGHT, fg=TEXT,
                     font=("Consolas", 9), anchor="w").pack(side="left", fill="x", expand=True, padx=(6, 6))
            kit.boton(caja, "COPIAR", lambda n=numero: self._accion_vista(
                "clipboard_history", {"action": "copy", "number": n}, "Copiar del historial"), fondo="PANEL").pack(
                side="right")
            filas.agregar(caja)

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
