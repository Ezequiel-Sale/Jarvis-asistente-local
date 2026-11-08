import math
import time
import tkinter as tk
from tkinter import ttk

import jarvis_theme
from jarvis_actions import CancellationToken
from jarvis_theme import (
    ACCENT_ACTIVE, BG, BORDER, BORDER_STRONG, CYAN, CYAN_DARK, ERROR, HOVER, MUTED, ORB_ARC_B, ORB_ARC_C, ORB_ARC_IDLE, ORB_BRIGHT, ORB_BRIGHT_ALT, ORB_CORE, ORB_CORE_LINE, ORB_IDLE, ORB_RING_A, ORB_RING_B, ORB_RING_C, ORB_RING_D, ORB_TICK, PANEL, PANEL_LIGHT, TEXT, VIOLET,
)


class JarvisUIMixin:
        def _configure_styles(self) -> None:
            style = ttk.Style()
            style.theme_use("clam")
            style.configure(
                "Jarvis.Vertical.TScrollbar",
                background=PANEL_LIGHT,
                troughcolor=PANEL,
                bordercolor=PANEL,
                lightcolor=PANEL_LIGHT,
                darkcolor=PANEL_LIGHT,
                arrowcolor=CYAN,
                gripcount=0,
            )
            style.map(
                "Jarvis.Vertical.TScrollbar",
                background=[("active", HOVER), ("pressed", HOVER)],
            )
            style.configure(
                "Jarvis.Horizontal.TProgressbar",
                troughcolor=BG,
                background=CYAN,
                bordercolor=BG,
                lightcolor=CYAN,
                darkcolor=CYAN,
            )

        def _build_layout(self) -> None:
            """Una sola ventana: barra lateral (orbe, estado, secciones) y a la
            derecha la sección elegida (chat, rutinas o ajustes)."""
            self.root.configure(bg=BG)
            self.root.geometry("1180x800")
            self.root.minsize(940, 640)
            self.root.grid_rowconfigure(0, weight=1)
            self.root.grid_columnconfigure(1, weight=1)
            # Antes el chat era otra ventana; ahora es una sección de la principal.
            self.chat_window = self.root
            self.root.bind("<Escape>", self._cancel_current_operation)

            self._build_sidebar()

            content = tk.Frame(self.root, bg=BG)
            content.grid(row=0, column=1, sticky="nsew")
            content.grid_rowconfigure(2, weight=1)
            content.grid_columnconfigure(0, weight=1)

            header = tk.Frame(content, bg=BG, padx=28, pady=16)
            header.grid(row=0, column=0, sticky="ew")
            header.grid_columnconfigure(1, weight=1)
            self.page_title = tk.Label(
                header, text="CHAT", bg=BG, fg=CYAN, font=("Segoe UI", 17, "bold"),
            )
            self.page_title.grid(row=0, column=0, sticky="w")
            self.status = tk.Label(
                header, text="●  SISTEMA LISTO", bg=BG, fg=CYAN, font=("Consolas", 9, "bold"),
            )
            self.status.grid(row=0, column=1, sticky="e", padx=(16, 16))
            self.header_actions = tk.Frame(header, bg=BG)
            self.header_actions.grid(row=0, column=2, sticky="e")
            self.new_chat_button = self._ui_button(
                self.header_actions, "＋  NUEVA CONVERSACIÓN", self._new_conversation
            )
            self.compact_button = self._ui_button(self.header_actions, "▣  COMPACTO", self._enter_compact_mode)
            self.compact_button.pack(side="right", padx=(8, 0))
            tk.Frame(content, bg=BORDER, height=1).grid(row=1, column=0, sticky="ew", padx=28)

            pages = tk.Frame(content, bg=BG)
            pages.grid(row=2, column=0, sticky="nsew")
            pages.grid_rowconfigure(0, weight=1)
            pages.grid_columnconfigure(0, weight=1)
            self._pages: dict[str, tk.Frame] = {}
            for nombre in ("chat", "rutinas", "ajustes"):
                pagina = tk.Frame(pages, bg=BG)
                pagina.grid(row=0, column=0, sticky="nsew")
                self._pages[nombre] = pagina
            self._build_chat_page(self._pages["chat"])
            self._build_settings_page(self._pages["ajustes"])
            self._current_page = ""
            self._show_page("chat")
            self._draw_orb()
            self.root.after(40, self._animate_orb)

        def _ui_button(self, parent: tk.Misc, text: str, command, *, fg: str | None = None) -> tk.Button:
            return tk.Button(
                parent, text=text, command=command, bg=PANEL_LIGHT, fg=fg or CYAN,
                activebackground=HOVER, activeforeground=TEXT, relief="flat",
                padx=12, pady=8, font=("Consolas", 8, "bold"), cursor="hand2",
            )

        def _build_sidebar(self) -> None:
            sidebar = tk.Frame(self.root, bg=PANEL, width=250)
            sidebar.grid(row=0, column=0, sticky="ns")
            sidebar.grid_propagate(False)
            sidebar.pack_propagate(False)
            tk.Frame(self.root, bg=BORDER, width=1).grid(row=0, column=0, sticky="nse")

            marca = tk.Frame(sidebar, bg=PANEL, padx=22, pady=18)
            marca.pack(fill="x")
            tk.Label(marca, text="J.A.R.V.I.S.", bg=PANEL, fg=CYAN,
                     font=("Segoe UI", 17, "bold")).pack(anchor="w")
            tk.Label(marca, text="ASISTENTE LOCAL", bg=PANEL, fg=MUTED,
                     font=("Consolas", 8)).pack(anchor="w")

            self.orb_canvas = tk.Canvas(
                sidebar, bg=PANEL, highlightthickness=0, bd=0, cursor="hand2", height=210,
            )
            self.orb_canvas.pack(fill="x", padx=12)
            self.orb_canvas.bind("<Button-1>", self._on_orb_click)
            self.orb_canvas.bind("<Configure>", self._draw_orb)
            self.orb_status = tk.Label(
                sidebar, text="PULSA EL NÚCLEO O F9 PARA HABLAR", bg=PANEL, fg=MUTED,
                font=("Consolas", 8, "bold"), wraplength=210, justify="center",
            )
            self.orb_status.pack(fill="x", padx=16, pady=(4, 14))
            tk.Frame(sidebar, bg=BORDER, height=1).pack(fill="x", padx=18, pady=(0, 10))

            self._nav_buttons: dict[str, tk.Button] = {}
            for nombre, texto in (("chat", "▤   CHAT"), ("rutinas", "⏱   RUTINAS"), ("ajustes", "⚙   AJUSTES")):
                boton = tk.Button(
                    sidebar, text=texto, anchor="w", command=lambda n=nombre: self._show_page(n),
                    bg=PANEL, fg=MUTED, activebackground=HOVER, activeforeground=TEXT,
                    relief="flat", padx=16, pady=9, font=("Consolas", 10, "bold"), cursor="hand2",
                )
                boton.pack(fill="x", padx=12, pady=2)
                self._nav_buttons[nombre] = boton

            tk.Label(
                sidebar,
                text="F9  hablar / detener\nESC  cancelar\nX  seguir en la bandeja",
                bg=PANEL, fg=MUTED, justify="left", font=("Consolas", 8),
            ).pack(side="bottom", anchor="w", padx=22, pady=18)

        _PAGE_TITLES = {"chat": "CHAT", "rutinas": "RUTINAS Y AUTOMATIZACIONES", "ajustes": "AJUSTES"}

        def _show_page(self, nombre: str) -> None:
            if nombre not in self._pages:
                return
            self._pages[nombre].tkraise()
            self._current_page = nombre
            cambia = getattr(self, "_current_page", "") != nombre
            self.page_title.configure(text=self._PAGE_TITLES[nombre], fg=CYAN)
            if cambia:
                self._fade_in(self.page_title)
            for clave, boton in self._nav_buttons.items():
                activo = clave == nombre
                boton.configure(bg=PANEL_LIGHT if activo else PANEL, fg=CYAN if activo else MUTED)
            if nombre == "chat":
                self.new_chat_button.pack(side="right")
                self.input.focus_set()
            else:
                self.new_chat_button.pack_forget()
            if nombre == "rutinas":
                self._load_routines_page()
            if nombre == "ajustes":
                self._refresh_monitor_map()

        def _build_chat_page(self, page: tk.Frame) -> None:
            content = tk.Frame(page, bg=BG, padx=28, pady=16)
            content.pack(fill="both", expand=True)
            content.grid_rowconfigure(0, weight=1)
            content.grid_columnconfigure(0, weight=1)

            self.chat_panel = tk.Frame(
                content,
                bg=PANEL,
                highlightthickness=1,
                highlightbackground=BORDER,
            )
            self.chat_panel.grid(row=0, column=0, sticky="nsew")
            self.chat_panel.grid_rowconfigure(0, weight=1)
            self.chat_panel.grid_columnconfigure(0, weight=1)

            self.chat_canvas = tk.Canvas(
                self.chat_panel,
                bg=PANEL,
                highlightthickness=0,
                bd=0,
            )
            self.chat_canvas.grid(row=0, column=0, sticky="nsew", padx=(12, 0), pady=12)
            scrollbar = ttk.Scrollbar(
                self.chat_panel,
                orient="vertical",
                command=self._scroll_chat,
                style="Jarvis.Vertical.TScrollbar",
            )
            scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=12)
            self.chat_canvas.configure(yscrollcommand=scrollbar.set)

            self.messages = tk.Frame(self.chat_canvas, bg=PANEL)
            self.messages_window = self.chat_canvas.create_window(
                (0, 0), window=self.messages, anchor="nw"
            )
            self.messages.bind("<Configure>", self._update_scroll_region)
            self.chat_canvas.bind("<Configure>", self._resize_messages)
            self.chat_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

            player = tk.Frame(
                content,
                bg=PANEL,
                padx=14,
                pady=10,
                highlightthickness=1,
                highlightbackground=BORDER,
            )
            player.grid(row=1, column=0, sticky="ew", pady=(12, 0))
            player.grid_columnconfigure(1, weight=1)
            tk.Label(
                player,
                text="AUDIO",
                bg=PANEL,
                fg=CYAN,
                font=("Consolas", 9, "bold"),
            ).grid(row=0, column=0, padx=(0, 12))
            self.player_track = tk.Label(
                player,
                text="Sin audio seleccionado",
                bg=PANEL,
                fg=MUTED,
                anchor="w",
                font=("Segoe UI", 9),
            )
            self.player_track.grid(row=0, column=1, sticky="ew")
            self.player_time = tk.Label(
                player,
                text="00:00 / 00:00",
                bg=PANEL,
                fg=MUTED,
                font=("Consolas", 8),
            )
            self.player_time.grid(row=0, column=2, padx=(10, 0))
            self.player_progress = ttk.Progressbar(
                player,
                style="Jarvis.Horizontal.TProgressbar",
                mode="determinate",
                maximum=100,
                value=0,
            )
            self.player_progress.grid(
                row=1, column=1, columnspan=2, sticky="ew", pady=(8, 0)
            )
            player_buttons = tk.Frame(player, bg=PANEL)
            player_buttons.grid(row=0, column=3, rowspan=2, padx=(14, 0))
            self.player_toggle = tk.Button(
                player_buttons,
                text="▶",
                command=self._toggle_player,
                state="disabled",
                bg=PANEL_LIGHT,
                fg=CYAN,
                activebackground=HOVER,
                activeforeground=TEXT,
                relief="flat",
                width=3,
                font=("Consolas", 10, "bold"),
                cursor="hand2",
            )
            self.player_toggle.pack(side="left", padx=(0, 6))
            self.player_stop_button = tk.Button(
                player_buttons,
                text="■",
                command=self._stop_player,
                state="disabled",
                bg=PANEL_LIGHT,
                fg=ERROR,
                activebackground=HOVER,
                activeforeground=TEXT,
                relief="flat",
                width=3,
                font=("Consolas", 10, "bold"),
                cursor="hand2",
            )
            self.player_stop_button.pack(side="left")

            composer = tk.Frame(content, bg=BG)
            composer.grid(row=2, column=0, sticky="ew", pady=(16, 0))
            composer.grid_columnconfigure(0, weight=1)
            input_panel = tk.Frame(
                composer,
                bg=PANEL,
                highlightthickness=1,
                highlightbackground=BORDER_STRONG,
                highlightcolor=BORDER_STRONG,
            )
            input_panel.grid(row=0, column=0, sticky="ew")
            input_panel.grid_columnconfigure(0, weight=1)
            self.input = tk.Text(
                input_panel,
                height=3,
                wrap="word",
                bg=PANEL,
                fg=TEXT,
                insertbackground=CYAN,
                selectbackground=CYAN_DARK,
                highlightthickness=0,
                relief="flat",
                padx=14,
                pady=12,
                font=("Segoe UI", 11),
                undo=True,
            )
            self.input.grid(row=0, column=0, sticky="nsew")
            self.input.bind("<Return>", self._on_enter)
            self.input.bind("<Shift-Return>", self._on_shift_enter)
            actions = tk.Frame(input_panel, bg=PANEL, padx=10, pady=8)
            actions.grid(row=0, column=1, sticky="se")
            self.cancel_button = tk.Button(
                actions,
                text="✕  CANCELAR",
                command=self._cancel_current_operation,
                bg=PANEL_LIGHT,
                fg=ERROR,
                activebackground=HOVER,
                activeforeground=TEXT,
                relief="flat",
                padx=14,
                pady=10,
                font=("Consolas", 10, "bold"),
                cursor="hand2",
            )
            self.mic_button = tk.Button(
                actions,
                text="🎙  MIC",
                command=self._on_orb_click,
                bg=PANEL_LIGHT,
                fg=CYAN,
                activebackground=HOVER,
                activeforeground=TEXT,
                relief="flat",
                padx=14,
                pady=10,
                font=("Consolas", 10, "bold"),
                cursor="hand2",
            )
            self.mic_button.pack(side="left", padx=(0, 8))
            self.send_button = tk.Button(
                actions,
                text="ENVIAR  ➤",
                command=self._send_text,
                bg=CYAN_DARK,
                fg=CYAN,
                activebackground=ACCENT_ACTIVE,
                activeforeground=TEXT,
                relief="flat",
                padx=18,
                pady=10,
                font=("Consolas", 10, "bold"),
                cursor="hand2",
            )
            self.send_button.pack(side="left")
            tk.Label(
                composer,
                text="ENTER enviar  ·  SHIFT+ENTER nueva línea",
                bg=BG,
                fg=MUTED,
                font=("Consolas", 8),
            ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        def _build_settings_page(self, page: tk.Frame) -> None:
            """Vista de monitores e interruptores; desplazable con la rueda."""
            lienzo = tk.Canvas(page, bg=BG, highlightthickness=0, bd=0)
            barra = ttk.Scrollbar(page, orient="vertical", command=lienzo.yview,
                                  style="Jarvis.Vertical.TScrollbar")
            lienzo.configure(yscrollcommand=barra.set)
            barra.pack(side="right", fill="y", padx=(0, 6), pady=8)
            lienzo.pack(side="left", fill="both", expand=True)
            cuerpo = tk.Frame(lienzo, bg=BG, padx=28, pady=18)
            ventana = lienzo.create_window((0, 0), window=cuerpo, anchor="nw")
            cuerpo.bind("<Configure>", lambda _e: lienzo.configure(scrollregion=lienzo.bbox("all")))
            lienzo.bind("<Configure>", lambda e: lienzo.itemconfigure(ventana, width=e.width))
            cuerpo.jarvis_scroll = lienzo
            lienzo.jarvis_scroll = lienzo

            # Vista de monitores: a escala y en su posición real; clic para acciones.
            mapa_marco = tk.Frame(cuerpo, bg=PANEL, padx=18, pady=14,
                                  highlightthickness=1, highlightbackground=BORDER)
            mapa_marco.pack(fill="x", pady=(0, 10))
            cabecera = tk.Frame(mapa_marco, bg=PANEL)
            cabecera.pack(fill="x")
            tk.Label(cabecera, text="Monitores", bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 11, "bold")).pack(side="left")
            self._ui_button(cabecera, "⟳  ACTUALIZAR", self._refresh_monitor_map, fg=MUTED).pack(side="right")
            tk.Label(mapa_marco, text="Clic en una pantalla para desactivarla, extenderla, "
                     "hacerla principal o arreglar su escalado.", bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 8))
            self.monitor_canvas = tk.Canvas(mapa_marco, bg=PANEL, height=230,
                                            highlightthickness=0, bd=0)
            self.monitor_canvas.pack(fill="x")
            self.monitor_canvas.bind("<Configure>", lambda _e: self._draw_monitor_map())
            self._monitor_views: list = []

            def fila(titulo: str, descripcion: str, atributo: str, texto: str, comando) -> None:
                marco = tk.Frame(cuerpo, bg=PANEL, padx=18, pady=14,
                                 highlightthickness=1, highlightbackground=BORDER)
                marco.pack(fill="x", pady=(0, 10))
                marco.grid_columnconfigure(0, weight=1)
                tk.Label(marco, text=titulo, bg=PANEL, fg=TEXT,
                         font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
                tk.Label(marco, text=descripcion, bg=PANEL, fg=MUTED, justify="left",
                         wraplength=620, font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(2, 0))
                boton = self._ui_button(marco, texto, comando, fg=MUTED)
                boton.configure(width=30)
                boton.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
                setattr(self, atributo, boton)

            fila("Respuestas habladas",
                 "Jarvis lee en voz alta sus respuestas. También: «silencia tu voz» / «activa tu voz».",
                 "audio_toggle_button", "AUDIO ON", self._toggle_audio_enabled)
            fila("Palabra de activación",
                 "Escucha «Hey Jarvis» en segundo plano. También: «deja de escucharme».",
                 "wakeword_button", "HEY JARVIS …", self._toggle_wakeword_button)
            fila("Iniciar con Windows",
                 "Al encender la PC, Jarvis arranca oculto en la bandeja. "
                 "También: «desactiva el inicio con Windows».",
                 "autostart_button", "⏻  INICIO CON WINDOWS …", self._toggle_autostart_button)
            fila("Arreglar la barra de tareas sola",
                 "Si tras cambiar monitores la barra de tareas de la TV queda fuera de su sitio, "
                 "Jarvis baja y sube su escala automáticamente (igual que «arregla el escalado»).",
                 "taskbar_button", "▭  ARREGLO AUTOMÁTICO …", self._toggle_taskbar_button)
            fila("Aviso flotante",
                 "Con Jarvis en la bandeja, una burbuja en la esquina muestra lo que entendió y la "
                 "respuesta, y las confirmaciones con SÍ / NO, sin quitarte el foco.",
                 "floating_button", "▤  AVISO FLOTANTE …", self._toggle_floating)
            fila("Animaciones",
                 "Los mensajes, tarjetas y secciones aparecen con un fundido corto.",
                 "animations_button", "✦  ANIMACIONES …", self._toggle_animations)

            marco = tk.Frame(cuerpo, bg=PANEL, padx=18, pady=14,
                             highlightthickness=1, highlightbackground=BORDER)
            marco.pack(fill="x", pady=(0, 10))
            marco.grid_columnconfigure(0, weight=1)
            tk.Label(marco, text="Tema", bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
            tk.Label(marco, text="Colores de Jarvis. También: «cambia al tema rojo», «pon el modo claro».",
                     bg=PANEL, fg=MUTED, justify="left", wraplength=620,
                     font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(2, 0))
            botones = tk.Frame(marco, bg=PANEL)
            botones.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
            self._theme_buttons: dict[str, tk.Button] = {}
            for nombre, etiqueta in (("cian", "CIAN"), ("mark", "MARK"), ("claro", "CLARO")):
                boton = self._ui_button(botones, etiqueta, lambda n=nombre: self._set_theme(n), fg=MUTED)
                boton.configure(width=9)
                boton.pack(side="left", padx=(6, 0))
                self._theme_buttons[nombre] = boton
            self._refresh_theme_buttons()

            self.settings_info = tk.Label(
                cuerpo, text="", bg=BG, fg=MUTED, justify="left", anchor="w",
                font=("Consolas", 8),
            )
            self.settings_info.pack(fill="x", pady=(14, 0))

        def _draw_monitor_map(self) -> None:
            lienzo = getattr(self, "monitor_canvas", None)
            if lienzo is None or not lienzo.winfo_exists():
                return
            import jarvis_monitor_view as vista_mod

            lienzo.delete("all")
            ancho, alto = lienzo.winfo_width(), lienzo.winfo_height()
            if ancho < 60:
                return
            if not self._monitor_views:
                lienzo.create_text(ancho / 2, alto / 2, text="Sin datos de monitores todavía.",
                                   fill=MUTED, font=("Segoe UI", 10))
                return
            cajas = vista_mod.distribuir(self._monitor_views, ancho, alto)
            for v in self._monitor_views:
                if v.numero not in cajas:
                    continue
                x0, y0, x1, y1 = cajas[v.numero]
                etiqueta = f"mon_{v.numero}"
                lienzo.create_rectangle(
                    x0, y0, x1, y1, tags=(etiqueta, "monitor"),
                    fill=PANEL_LIGHT if v.activa else PANEL,
                    outline=CYAN if v.principal else (BORDER_STRONG if v.activa else MUTED),
                    width=2 if v.principal else 1, dash=() if v.activa else (4, 3),
                    activeoutline=CYAN, activewidth=2,
                )
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                grande = min(28, max(12, (y1 - y0) / 3))
                lienzo.create_text(cx, cy - grande * 0.55, text=str(v.numero), tags=(etiqueta,),
                                   fill=CYAN if v.activa else MUTED, font=("Segoe UI", int(grande), "bold"))
                lienzo.create_text(cx, cy + grande * 0.35, text=v.nombre, tags=(etiqueta,),
                                   fill=TEXT if v.activa else MUTED, width=max(40, x1 - x0 - 8),
                                   font=("Segoe UI", 8, "bold"))
                detalle = vista_mod.describir(v, compacto=(x1 - x0) < 190)
                lienzo.create_text(cx, cy + grande * 0.35 + 14, text=detalle, tags=(etiqueta,),
                                   fill=MUTED, font=("Consolas", 7), width=max(40, x1 - x0 - 8),
                                   justify="center")
                lienzo.tag_bind(etiqueta, "<Button-1>", lambda e, vv=v: self._monitor_menu(vv, e))
                lienzo.tag_bind(etiqueta, "<Enter>", lambda _e: lienzo.configure(cursor="hand2"))
                lienzo.tag_bind(etiqueta, "<Leave>", lambda _e: lienzo.configure(cursor=""))

        def _monitor_menu(self, vista, event) -> None:
            import jarvis_monitor_view as vista_mod

            menu = tk.Menu(self.root, tearoff=0, bg=PANEL_LIGHT, fg=TEXT, activebackground=HOVER,
                           activeforeground=CYAN, bd=0, font=("Segoe UI", 10))
            menu.add_command(label=f"Monitor {vista.numero} · {vista.nombre}", state="disabled")
            menu.add_separator()
            for texto, herramienta, argumentos in vista_mod.acciones(vista):
                menu.add_command(
                    label=texto,
                    command=lambda h=herramienta, a=argumentos, t=texto: self._run_monitor_action(h, a, t),
                )
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        def _refresh_theme_buttons(self) -> None:
            actual = jarvis_theme.tema_actual()
            for nombre, boton in getattr(self, "_theme_buttons", {}).items():
                boton.configure(
                    bg=CYAN_DARK if nombre == actual else PANEL_LIGHT,
                    fg=CYAN if nombre == actual else MUTED,
                )

        def _show_chat_window(self) -> None:
            if self._closing:
                return
            self.root.deiconify()
            self.root.lift()
            self._show_page("chat")

        def _hide_chat_window(self) -> None:
            self._hide_to_tray()

        def _on_orb_click(self, _event=None) -> str:
            self._orb_pulse_started = time.perf_counter()
            self._toggle_recording()
            return "break"

        def _animate_orb(self) -> None:
            if not self.root.winfo_exists():
                return
            self._orb_phase = (self._orb_phase + (5 if self._orb_mode == "listening" else 1.2)) % 360
            self._draw_orb()
            self.root.after(40, self._animate_orb)

        def _draw_orb(self, _event=None) -> None:
            for lienzo in (getattr(self, "orb_canvas", None), getattr(self, "compact_canvas", None)):
                try:
                    if lienzo is not None and lienzo.winfo_exists() and lienzo.winfo_ismapped():
                        self._draw_orb_on(lienzo)
                except tk.TclError:
                    continue

        def _draw_orb_on(self, canvas: tk.Canvas) -> None:
            width = canvas.winfo_width()
            height = canvas.winfo_height()
            if width < 40 or height < 40:
                return

            canvas.delete("orb")
            center_x = width / 2
            center_y = height / 2
            base_radius = min(width, height) * 0.43
            active = self._orb_mode == "listening"
            processing = self._orb_mode == "processing"
            glow = CYAN if active else VIOLET if processing else ORB_IDLE
            bright = ORB_BRIGHT if active else ORB_BRIGHT_ALT if processing else CYAN

            pulse = 0.0
            if self._orb_pulse_started:
                elapsed = time.perf_counter() - self._orb_pulse_started
                if elapsed < 0.5:
                    pulse = math.sin(elapsed * math.pi * 2) * (1 - elapsed / 0.5)
                else:
                    self._orb_pulse_started = 0.0
            breathe = 0.018 * math.sin(math.radians(self._orb_phase * 2))
            scale = 1 + pulse * 0.09 + (breathe if active else breathe * 0.25)
            radius = base_radius * scale

            for inset, color, line_width in (
                (0, ORB_RING_A, 1),
                (0.055, ORB_RING_B, 1),
                (0.22, ORB_RING_C, 1),
                (0.38, ORB_RING_D, 1),
            ):
                ring_radius = radius * (1 - inset)
                canvas.create_oval(
                    center_x - ring_radius,
                    center_y - ring_radius,
                    center_x + ring_radius,
                    center_y + ring_radius,
                    outline=color,
                    width=line_width,
                    tags="orb",
                )

            for index in range(72):
                angle = math.radians(index * 5 + self._orb_phase * (0.22 if active else 0.04))
                outer = radius * (0.93 if index % 6 else 0.96)
                inner = radius * (0.88 if index % 6 else 0.84)
                color = glow if active and index % 3 == 0 else ORB_TICK
                canvas.create_line(
                    center_x + math.cos(angle) * inner,
                    center_y + math.sin(angle) * inner,
                    center_x + math.cos(angle) * outer,
                    center_y + math.sin(angle) * outer,
                    fill=color,
                    width=2 if index % 6 == 0 else 1,
                    tags="orb",
                )

            rings = (
                (0.78, 64, 34, 1, CYAN),
                (0.68, 42, 24, -1, ORB_ARC_B),
                (0.56, 76, 44, 1, VIOLET if processing else ORB_ARC_C),
            )
            for ring_index, (ratio, extent, gap, direction, color) in enumerate(rings):
                ring_radius = radius * ratio
                rotation = self._orb_phase * direction * (1 + ring_index * 0.35)
                for segment in range(4):
                    start = rotation + segment * (extent + gap)
                    canvas.create_arc(
                        center_x - ring_radius,
                        center_y - ring_radius,
                        center_x + ring_radius,
                        center_y + ring_radius,
                        start=start,
                        extent=extent,
                        style=tk.ARC,
                        outline=color if active or processing else ORB_ARC_IDLE,
                        width=3 if ring_index == 0 else 2,
                        tags="orb",
                    )

            core_radius = radius * 0.43
            canvas.create_oval(
                center_x - core_radius,
                center_y - core_radius,
                center_x + core_radius,
                center_y + core_radius,
                fill=ORB_CORE,
                outline=glow,
                width=2,
                tags="orb",
            )
            canvas.create_oval(
                center_x - core_radius * 0.83,
                center_y - core_radius * 0.83,
                center_x + core_radius * 0.83,
                center_y + core_radius * 0.83,
                outline=ORB_CORE_LINE,
                width=1,
                tags="orb",
            )
            center_label = "J.A.R.V.I.S."
            action_label = "TOCA PARA DETENER" if active else "TOCA PARA HABLAR"
            # Orbe compacto de la barra lateral: solo el nombre (el estado va debajo).
            compacto = radius < 130
            canvas.create_text(
                center_x,
                center_y if compacto else center_y - core_radius * 0.2,
                text=center_label,
                fill=TEXT,
                font=("Segoe UI", 8 if compacto else max(12, int(radius * 0.075)), "bold"),
                tags="orb",
            )
            if compacto:
                return
            canvas.create_text(
                center_x,
                center_y + core_radius * 0.34,
                text=action_label,
                fill=bright,
                font=("Consolas", max(7, int(radius * 0.028)), "bold"),
                tags="orb",
            )

        def _update_orb_mode(self) -> None:
            if self.recorder is not None:
                self._orb_mode = "listening"
                self.orb_status.configure(
                    text="ESCUCHANDO  ·  PULSA EL NÚCLEO O F9 PARA DETENER",
                    fg=CYAN,
                )
            elif self.busy:
                self._orb_mode = "processing"
                self.orb_status.configure(text="PROCESANDO SOLICITUD", fg=VIOLET)
            else:
                self._orb_mode = "idle"
                self.orb_status.configure(
                    text="PULSA EL NÚCLEO O F9 PARA HABLAR",
                    fg=MUTED,
                )
            estado_compacto = getattr(self, "compact_status", None)
            if estado_compacto is not None and estado_compacto.winfo_exists():
                estado_compacto.configure(
                    text={"listening": "TE ESCUCHO…", "processing": "PROCESANDO…"}.get(self._orb_mode, "LISTO"),
                    fg={"listening": CYAN, "processing": VIOLET}.get(self._orb_mode, MUTED),
                )
            if self._orb_mode != getattr(self, "_floating_mode", "idle"):
                self._floating_mode = self._orb_mode
                if self._floating_active():
                    self.floating.estado(
                        {"listening": "TE ESCUCHO…", "processing": "PROCESANDO…"}.get(self._orb_mode, "")
                    )
            self._draw_orb()

        # --- Aviso flotante -------------------------------------------------------

        def _floating_active(self) -> bool:
            """El aviso se usa cuando la ventana principal no está a la vista."""
            aviso = getattr(self, "floating", None)
            if aviso is None or not getattr(self, "_floating_enabled", True):
                return False
            try:
                return self.root.state() in ("withdrawn", "iconic")
            except tk.TclError:
                return False

        def _notify_floating(self, role: str, text: str) -> None:
            if not self._floating_active():
                return
            if role == "user":
                self.floating.usuario(text)
            else:
                import jarvis_markdown

                self.floating.respuesta(jarvis_markdown.texto_plano(text))

        def _update_scroll_region(self, event) -> None:
            self.chat_canvas.configure(scrollregion=event.widget.master.bbox("all"))
            if self._chat_following:
                self.chat_canvas.after_idle(self._scroll_chat_to_bottom)

        def _resize_messages(self, event) -> None:
            self.chat_canvas.itemconfigure(self.messages_window, width=event.width)

        def _on_mousewheel(self, event) -> None:
            units = int(-event.delta / 120)
            if units == 0 and event.delta:
                units = -1 if event.delta > 0 else 1
            widget = event.widget
            while widget is not None and widget is not self.chat_panel:
                # Secciones incrustadas (rutinas, formulario) marcan su lienzo así.
                lienzo = getattr(widget, "jarvis_scroll", None)
                if lienzo is not None:
                    lienzo.yview_scroll(units, "units")
                    return "break"
                widget = getattr(widget, "master", None)
            if widget is None or not self.chat_canvas.winfo_exists():
                return

            self.chat_canvas.yview_scroll(units, "units")
            self._update_chat_following()
            return "break"

        def _scroll_chat(self, *args: str) -> None:
            self.chat_canvas.yview(*args)
            self._update_chat_following()

        def _update_chat_following(self) -> None:
            self._chat_following = self.chat_canvas.yview()[1] >= 0.99

        def _scroll_chat_to_bottom(self) -> None:
            if self._chat_following and self.chat_canvas.winfo_exists():
                self.chat_canvas.yview_moveto(1.0)

        def _add_message(
            self,
            role: str,
            text: str,
            audio_path: str | None = None,
        ) -> None:
            should_follow = self._chat_following
            is_user = role == "user"
            row = tk.Frame(self.messages, bg=PANEL)
            row.pack(fill="x", padx=14, pady=8)

            bubble = tk.Frame(
                row,
                bg=CYAN_DARK if is_user else PANEL_LIGHT,
                padx=14,
                pady=10,
            )
            bubble.pack(side="right" if is_user else "left", anchor="e" if is_user else "w")

            tk.Label(
                bubble,
                text="TÚ" if is_user else "JARVIS",
                bg=CYAN_DARK if is_user else PANEL_LIGHT,
                fg=CYAN if is_user else VIOLET,
                font=("Consolas", 8, "bold"),
            ).pack(anchor="w", pady=(0, 5))
            if is_user:
                tk.Label(
                    bubble,
                    text=text,
                    bg=CYAN_DARK,
                    fg=TEXT,
                    justify="left",
                    anchor="w",
                    wraplength=650,
                    font=("Segoe UI", 11),
                ).pack(anchor="w")
            else:
                self._rich_text(bubble, text, PANEL_LIGHT).pack(anchor="w")

            if audio_path:
                tk.Button(
                    bubble,
                    text="▶  REPRODUCIR EN JARVIS",
                    command=lambda path=audio_path: self._play_audio(path),
                    bg=PANEL,
                    fg=CYAN,
                    activebackground=HOVER,
                    activeforeground=TEXT,
                    relief="flat",
                    padx=10,
                    pady=6,
                    font=("Consolas", 8, "bold"),
                    cursor="hand2",
                ).pack(anchor="w", pady=(8, 0))

            self._fade_in(row)
            self._notify_floating(role, text)
            if should_follow:
                self.chat_canvas.after_idle(self._scroll_chat_to_bottom)

        # --- Animaciones suaves --------------------------------------------------

        _ANIM_PASOS = 6
        _ANIM_MS = 25  # 6 × 25 ms ≈ 150 ms: se nota sin hacer esperar

        def _animations_enabled(self) -> bool:
            return getattr(self, "_animations", True)

        def _fade_in(self, raiz: tk.Misc) -> None:
            """El texto aparece fundiéndose desde el color de su fondo."""
            if not self._animations_enabled():
                return
            objetivos = []
            pendientes = [raiz]
            while pendientes:
                w = pendientes.pop()
                pendientes.extend(w.winfo_children())
                if isinstance(w, (tk.Label, tk.Text)):
                    try:
                        fg, bg = str(w.cget("foreground")), str(w.cget("background"))
                    except tk.TclError:
                        continue
                    if fg.startswith("#") and bg.startswith("#") and len(fg) == len(bg) == 7:
                        objetivos.append((w, bg, fg))
            if not objetivos:
                return

            tema = jarvis_theme.tema_actual()

            def paso(n: int) -> None:
                t = n / self._ANIM_PASOS
                for w, bg, fg in objetivos:
                    # Si cambiaron el tema a mitad del fundido, se termina con el color nuevo.
                    bg, fg = jarvis_theme.traducir(bg, tema), jarvis_theme.traducir(fg, tema)
                    try:
                        if w.winfo_exists():
                            w.configure(foreground=fg if n >= self._ANIM_PASOS else jarvis_theme.mezclar(bg, fg, t))
                    except tk.TclError:
                        pass
                if n < self._ANIM_PASOS:
                    raiz.after(self._ANIM_MS, paso, n + 1)

            paso(1)

        def _add_action_card(self, resultado) -> tk.Frame:
            """Tarjeta compacta de una acción: icono, nombre, estado, duración y origen.
            Clic en la cabecera despliega parámetros, verificación y error."""
            import jarvis_cards

            should_follow = self._chat_following
            texto_estado, token = jarvis_cards.estado(resultado)
            color = getattr(jarvis_theme, token)
            fila = tk.Frame(self.messages, bg=PANEL)
            fila.pack(fill="x", padx=14, pady=4)
            tarjeta = tk.Frame(fila, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
            tarjeta.pack(side="left", anchor="w")
            tk.Frame(tarjeta, bg=color, width=3).pack(side="left", fill="y")
            interior = tk.Frame(tarjeta, bg=PANEL, padx=12, pady=7)
            interior.pack(side="left", fill="both")
            cabecera = tk.Frame(interior, bg=PANEL, cursor="hand2")
            cabecera.pack(anchor="w")
            partes = (
                (jarvis_cards.icono(resultado.herramienta), TEXT, ("Segoe UI Emoji", 11)),
                (resultado.etiqueta, TEXT, ("Segoe UI", 10, "bold")),
                (texto_estado, color, ("Consolas", 8, "bold")),
                (jarvis_cards.resumen(resultado), MUTED, ("Consolas", 8)),
            )
            etiquetas = []
            for n, (texto, fg, fuente) in enumerate(partes):
                etiqueta = tk.Label(cabecera, text=texto, bg=PANEL, fg=fg, font=fuente, cursor="hand2")
                etiqueta.pack(side="left", padx=(0 if n == 0 else 8, 0))
                etiquetas.append(etiqueta)
            flecha = tk.Label(cabecera, text="▸", bg=PANEL, fg=MUTED, font=("Consolas", 9), cursor="hand2")
            flecha.pack(side="left", padx=(8, 0))
            cuerpo = tk.Frame(interior, bg=PANEL)
            cuerpo.pack(anchor="w", fill="x")
            detalle = tk.Label(
                interior, text=jarvis_cards.detalle(resultado), bg=PANEL, fg=MUTED,
                font=("Consolas", 8), justify="left", wraplength=620, anchor="w",
            )

            def alternar(_event=None) -> None:
                if detalle.winfo_ismapped():
                    detalle.pack_forget()
                    flecha.configure(text="▸")
                else:
                    detalle.pack(anchor="w", pady=(6, 0))
                    flecha.configure(text="▾")
                    self._fade_in(detalle)

            for widget in (cabecera, flecha, *etiquetas):
                widget.bind("<Button-1>", alternar)

            def poner_cuerpo(texto: str) -> None:
                for hijo in cuerpo.winfo_children():
                    hijo.destroy()
                self._rich_text(cuerpo, texto, PANEL).pack(anchor="w", pady=(6, 0))
                self._fade_in(cuerpo)
                self._notify_floating("assistant", texto)
                if self._chat_following:
                    self.chat_canvas.after_idle(self._scroll_chat_to_bottom)

            tarjeta.jarvis_poner_cuerpo = poner_cuerpo
            self._fade_in(fila)
            if resultado.origen != "directo":  # la respuesta de los directos llega con poner_cuerpo
                self._notify_floating(
                    "assistant", f"{jarvis_cards.icono(resultado.herramienta)} {resultado.etiqueta}: {texto_estado}"
                )
            if should_follow:
                self.chat_canvas.after_idle(self._scroll_chat_to_bottom)
            return tarjeta

        def _confirmation_card(
            self,
            *,
            titulo: str,
            color: str,
            aprobar: str,
            rechazar: str,
            pista: str,
            aprobar_bg: str | None = None,
            aprobar_fg: str | None = None,
            voz: bool = True,
            al_aprobar=None,
            al_rechazar=None,
            resumen: str | None = None,
            necesita_ventana: bool = False,
        ) -> tuple[tk.Frame, tk.Frame]:
            """Confirmación como tarjeta dentro del chat (antes eran ventanas aparte).

            Devuelve (tarjeta, cuerpo): el cuerpo lo rellena quien la crea. Los botones
            llaman a ``_resolve_plan_approval`` salvo que se pasen ``al_aprobar`` /
            ``al_rechazar``. Al resolverse, la tarjeta se queda en el historial con el
            resultado en lugar de los botones (``tarjeta.jarvis_resolver``).
            """
            aprobar_cb = al_aprobar or (lambda: self._resolve_plan_approval(True))
            rechazar_cb = al_rechazar or (lambda: self._resolve_plan_approval(False))
            en_aviso = self._floating_active() and not necesita_ventana
            if en_aviso:
                # En la bandeja: la pregunta va al aviso flotante y no se roba el foco.
                self.floating.pregunta(resumen or titulo.capitalize(), aprobar_cb, rechazar_cb)
            else:
                self._bring_chat_forward()
            fila = tk.Frame(self.messages, bg=PANEL)
            fila.pack(fill="x", padx=14, pady=8)
            tarjeta = tk.Frame(fila, bg=PANEL_LIGHT, highlightthickness=1, highlightbackground=color,
                               takefocus=1)
            tarjeta.pack(side="left", anchor="w")
            tk.Frame(tarjeta, bg=color, width=4).pack(side="left", fill="y")
            interior = tk.Frame(tarjeta, bg=PANEL_LIGHT, padx=16, pady=12)
            interior.pack(side="left", fill="both")
            tk.Label(interior, text=titulo, bg=PANEL_LIGHT, fg=color,
                     font=("Consolas", 11, "bold"), justify="left", wraplength=680).pack(anchor="w")
            cuerpo = tk.Frame(interior, bg=PANEL_LIGHT)
            cuerpo.pack(anchor="w", fill="x", pady=(6, 0))
            pie = tk.Frame(interior, bg=PANEL_LIGHT)
            pie.pack(anchor="w", fill="x", pady=(10, 0))
            tk.Label(pie, text=pista, bg=PANEL_LIGHT, fg=MUTED, font=("Segoe UI", 9),
                     justify="left", wraplength=680).pack(anchor="w", pady=(0, 8))
            botones = tk.Frame(pie, bg=PANEL_LIGHT)
            botones.pack(fill="x")
            estilo = dict(relief="flat", padx=12, pady=8, font=("Consolas", 9, "bold"), cursor="hand2",
                          activebackground=HOVER, activeforeground=TEXT)
            if voz:
                self._plan_voice_button = tk.Button(
                    botones, text="🎙  HABLAR (F9)", command=self._toggle_recording,
                    bg=PANEL, fg=CYAN, **estilo,
                )
                self._plan_voice_button.pack(side="left", padx=(0, 24))
            self._plan_approve_button = tk.Button(
                botones, text=aprobar, command=aprobar_cb,
                bg=aprobar_bg or CYAN, fg=aprobar_fg or BG, **estilo,
            )
            self._plan_approve_button.pack(side="left", padx=(0, 8))
            self._plan_reject_button = tk.Button(
                botones, text=rechazar, command=rechazar_cb, bg=PANEL, fg=TEXT, **estilo,
            )
            self._plan_reject_button.pack(side="left")

            def resolver(aprobado: bool, texto: str | None = None) -> None:
                aviso = getattr(self, "floating", None)
                if aviso is not None:
                    aviso.quitar_pregunta(texto or (f"✓ {aprobar}" if aprobado else f"✕ {rechazar}"))
                if not tarjeta.winfo_exists():
                    return
                for hijo in pie.winfo_children():
                    hijo.destroy()
                tk.Label(
                    pie, text=texto or (f"✓  {aprobar}" if aprobado else f"✕  {rechazar}"),
                    bg=PANEL_LIGHT, fg=CYAN if aprobado else MUTED, font=("Consolas", 9, "bold"),
                ).pack(anchor="w")
                tarjeta.configure(highlightbackground=BORDER)

            tarjeta.jarvis_resolver = resolver
            tarjeta.after_idle(lambda: tarjeta.winfo_exists() and self._fade_in(fila))
            for widget in (tarjeta, self._plan_approve_button, self._plan_reject_button):
                widget.bind("<Return>", lambda _e: (aprobar_cb(), "break")[1])
                widget.bind("<Escape>", lambda _e: (rechazar_cb(), "break")[1])
            tarjeta.after_idle(tarjeta.focus_set)
            self.chat_canvas.after_idle(self._force_scroll_bottom)
            return tarjeta, cuerpo

        def _enter_compact_mode(self) -> None:
            """Ventanita con el orbe, siempre encima, en lugar de la ventana completa."""
            if getattr(self, "compact_window", None) is not None and self.compact_window.winfo_exists():
                self.compact_window.lift()
                return
            ventana = tk.Toplevel(self.root)
            ventana.overrideredirect(True)
            ventana.attributes("-topmost", True)
            ventana.configure(bg=BORDER)
            marco = tk.Frame(ventana, bg=PANEL)
            marco.pack(fill="both", expand=True, padx=1, pady=1)
            barra = tk.Frame(marco, bg=PANEL, cursor="fleur")
            barra.pack(fill="x", padx=6, pady=(4, 0))
            titulo = tk.Label(barra, text="J.A.R.V.I.S.", bg=PANEL, fg=CYAN, font=("Consolas", 8, "bold"),
                              cursor="fleur")
            titulo.pack(side="left")
            for texto, comando in (("✕", self._exit_compact_to_tray), ("⤢", self._exit_compact_mode)):
                boton = tk.Label(barra, text=texto, bg=PANEL, fg=MUTED, font=("Segoe UI", 10), cursor="hand2")
                boton.pack(side="right", padx=(6, 0))
                boton.bind("<Button-1>", lambda _e, c=comando: c())
            self.compact_canvas = tk.Canvas(marco, width=150, height=150, bg=PANEL, highlightthickness=0,
                                            bd=0, cursor="hand2")
            self.compact_canvas.pack(padx=8)
            self.compact_canvas.bind("<Button-1>", self._on_orb_click)
            self.compact_canvas.bind("<Double-Button-1>", lambda _e: self._exit_compact_mode())
            self.compact_status = tk.Label(marco, text="LISTO", bg=PANEL, fg=MUTED,
                                           font=("Consolas", 8, "bold"), cursor="fleur")
            self.compact_status.pack(pady=(0, 8))
            # Arrastrar desde la barra o el estado.
            inicio = {"x": 0, "y": 0}

            def agarrar(e) -> None:
                inicio["x"], inicio["y"] = e.x_root - ventana.winfo_x(), e.y_root - ventana.winfo_y()

            def mover(e) -> None:
                ventana.geometry(f"+{e.x_root - inicio['x']}+{e.y_root - inicio['y']}")

            def soltar(_e) -> None:
                self._save_compact_position(ventana.winfo_x(), ventana.winfo_y())

            for widget in (barra, titulo, self.compact_status):
                widget.bind("<Button-1>", agarrar)
                widget.bind("<B1-Motion>", mover)
                widget.bind("<ButtonRelease-1>", soltar)
            ventana.update_idletasks()
            x, y = self._compact_position(ventana.winfo_reqwidth(), ventana.winfo_reqheight())
            ventana.geometry(f"+{x}+{y}")
            self.compact_window = ventana
            self.root.withdraw()
            ventana.after(60, self._update_orb_mode)

        def _exit_compact_mode(self) -> None:
            self._close_compact_window()
            self._show_from_tray()

        def _exit_compact_to_tray(self) -> None:
            self._close_compact_window()

        def _close_compact_window(self) -> None:
            ventana = getattr(self, "compact_window", None)
            self.compact_window = None
            self.compact_canvas = None
            self.compact_status = None
            if ventana is not None and ventana.winfo_exists():
                ventana.destroy()

        def _compact_mode_active(self) -> bool:
            ventana = getattr(self, "compact_window", None)
            return bool(ventana is not None and ventana.winfo_exists())

        def _bring_chat_forward(self) -> None:
            """Muestra la ventana en el chat para que se vea la confirmación."""
            try:
                if self.root.state() in ("withdrawn", "iconic"):
                    self.root.deiconify()
                self._show_page("chat")
                self.root.lift()
            except tk.TclError:
                pass

        def _force_scroll_bottom(self) -> None:
            self._chat_following = True
            self._scroll_chat_to_bottom()

        _ANCHO_BURBUJA = 650

        def _rich_text(self, parent: tk.Misc, texto: str, bg: str) -> tk.Text:
            """Texto con formato (negritas, listas, código, tablas, enlaces) del tamaño justo."""
            import webbrowser
            from tkinter import font as tkfont

            import jarvis_markdown

            normal = tkfont.Font(family="Segoe UI", size=11)
            mono = tkfont.Font(family="Consolas", size=10)
            tramos = jarvis_markdown.analizar(texto)
            # Ancho: la línea visible más larga (con su fuente), hasta el máximo de la burbuja.
            lineas: list[tuple[int, int]] = [(0, 0)]  # (px proporcional, px monoespaciado)
            for contenido, etiquetas, _url in tramos:
                fuente = mono if ("pre" in etiquetas or "table" in etiquetas or "code" in etiquetas) else normal
                partes = contenido.split("\n")
                for n, parte in enumerate(partes):
                    if n:
                        lineas.append((0, 0))
                    prop, fija = lineas[-1]
                    if fuente is mono:
                        lineas[-1] = (prop, fija + mono.measure(parte))
                    else:
                        lineas[-1] = (prop + normal.measure(parte), fija)
            ancho_px = min(self._ANCHO_BURBUJA, max(24, max(p + f for p, f in lineas) + 6))
            caja = tk.Text(
                parent, bg=bg, fg=TEXT, font=normal, relief="flat", bd=0, highlightthickness=0,
                wrap="word", padx=0, pady=0, cursor="arrow", insertwidth=0,
                width=max(2, -(-ancho_px // normal.measure("0"))), height=1,
                selectbackground=CYAN_DARK, spacing1=1, spacing3=1,
            )
            caja.tag_configure("b", font=("Segoe UI", 11, "bold"))
            caja.tag_configure("i", font=("Segoe UI", 11, "italic"))
            caja.tag_configure("h", font=("Segoe UI", 12, "bold"), foreground=CYAN, spacing1=4, spacing3=2)
            caja.tag_configure("code", font=mono, foreground=CYAN, background=PANEL)
            caja.tag_configure("pre", font=mono, foreground=TEXT, background=PANEL,
                               lmargin1=8, lmargin2=8, rmargin=8, spacing1=4, spacing3=4, wrap="none")
            caja.tag_configure("table", font=mono, foreground=TEXT, wrap="none", spacing1=2)
            caja.tag_configure("li", lmargin1=6, lmargin2=24)
            caja.tag_configure("quote", foreground=MUTED, lmargin1=10, lmargin2=10)
            caja.tag_configure("link", foreground=CYAN, underline=True)
            caja.tag_raise("b")
            caja.tag_raise("link")
            for n, (contenido, etiquetas, url) in enumerate(tramos):
                if url and url.startswith(("http://", "https://")):
                    propia = f"url{n}"
                    caja.insert("end", contenido, etiquetas + (propia,))
                    caja.tag_bind(propia, "<Button-1>", lambda _e, u=url: webbrowser.open(u))
                    caja.tag_bind(propia, "<Enter>", lambda _e: caja.configure(cursor="hand2"))
                    caja.tag_bind(propia, "<Leave>", lambda _e: caja.configure(cursor="arrow"))
                else:
                    caja.insert("end", contenido, etiquetas)
            caja.configure(state="disabled", height=max(1, len(lineas)))

            def ajustar_alto(_event=None) -> None:
                # Solo con el ancho real se sabe cuántas líneas ocupa al ajustar palabras.
                try:
                    vistas = caja.count("1.0", "end-1c", "update", "displaylines")
                except tk.TclError:
                    return
                alto = (vistas[0] if isinstance(vistas, tuple) else vistas) or 0
                alto = max(1, alto + 1 if caja.get("end-2c") != "\n" else alto)
                if int(caja.cget("height")) != alto:
                    caja.configure(height=alto)

            caja.bind("<Configure>", ajustar_alto, add="+")
            return caja

        def _on_enter(self, event):
            if event.keysym != "Return":
                return
            self._send_text()
            return "break"

        def _on_shift_enter(self, event):
            event.widget.insert("insert", "\n")
            return "break"

        def _start_busy(self, message: str) -> None:
            self.busy = True
            self.cancel_token = CancellationToken()
            self.send_button.configure(state="disabled")
            self.mic_button.configure(state="disabled")
            self.input.configure(state="disabled")
            self.cancel_button.configure(state="normal")
            if not self.cancel_button.winfo_ismapped():
                self.cancel_button.pack(side="left", padx=(0, 8), before=self.mic_button)
            self._set_status(message, CYAN)

        def _set_idle(self) -> None:
            self.busy = False
            self.cancel_button.pack_forget()
            self.send_button.configure(state="normal")
            self.mic_button.configure(state="normal")
            self.input.configure(state="normal")
            if self.chat_window.state() != "withdrawn":
                self.input.focus_set()
            self._update_orb_mode()

        def _set_status(self, message: str, color: str) -> None:
            self.status.configure(text=f"●  {message}", fg=color)
            self._update_orb_mode()
