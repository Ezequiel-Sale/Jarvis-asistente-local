import logging
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
            # Guía de comandos de voz: visible en todas las secciones.
            self.commands_button = self._ui_button(self.header_actions, "?  COMANDOS", self._show_commands_help,
                                                   fg=MUTED)
            self.commands_button.pack(side="right", padx=(8, 0))
            tk.Frame(content, bg=BORDER, height=1).grid(row=1, column=0, sticky="ew", padx=28)

            pages = tk.Frame(content, bg=BG)
            pages.grid(row=2, column=0, sticky="nsew")
            pages.grid_rowconfigure(0, weight=1)
            pages.grid_columnconfigure(0, weight=1)
            self._pages: dict[str, tk.Frame] = {}
            for nombre in ("inicio", "chat", "rutinas", "juegos", "tele", "ajustes"):
                pagina = tk.Frame(pages, bg=BG)
                pagina.grid(row=0, column=0, sticky="nsew")
                self._pages[nombre] = pagina
            self._build_chat_page(self._pages["chat"])
            self._build_settings_page(self._pages["ajustes"])
            self._build_home_page(self._pages["inicio"])
            self._build_games_page(self._pages["juegos"])
            self._build_tv_page(self._pages["tele"])
            self._current_page = ""
            self._show_page("inicio")  # el panel de inicio es la primera página
            self._draw_orb()
            self.root.after(40, self._animate_orb)

        def _ui_button(self, parent: tk.Misc, text: str, command, *, fg: str | None = None) -> tk.Button:
            return tk.Button(
                parent, text=text, command=command, bg=PANEL_LIGHT, fg=fg or CYAN,
                activebackground=HOVER, activeforeground=TEXT, relief="flat",
                padx=12, pady=8, font=("Consolas", 8, "bold"), cursor="hand2",
            )

        def _show_commands_help(self, buscar: str = "") -> None:
            """Ventana con todos los comandos de voz (los de Jarvis y las frases de tus rutinas).
            ``buscar``: texto inicial del buscador («Monitores» al preguntar por voz)."""
            import jarvis_comandos
            import jarvis_routines

            ventana = getattr(self, "_commands_window", None)
            if ventana is not None and ventana.winfo_exists():
                if buscar:
                    self._commands_search.set(buscar)
                ventana.deiconify()
                ventana.lift()
                ventana.focus_force()
                return
            secciones = list(jarvis_comandos.CATALOGO)
            try:
                propias = jarvis_comandos.tus_comandos(jarvis_routines.listar_automatizaciones())
            except Exception:
                logging.exception("No se pudieron leer las rutinas para la guía de comandos.")
                propias = None
            if propias is not None:
                secciones.insert(0, propias)

            ventana = tk.Toplevel(self.root)
            self._commands_window = ventana
            ventana.title("Comandos de voz — J.A.R.V.I.S.")
            ventana.configure(bg=BG)
            ventana.geometry("780x680")
            ventana.minsize(560, 420)
            ventana.transient(self.root)
            ventana.bind("<Escape>", lambda _e: ventana.destroy())

            arriba = tk.Frame(ventana, bg=BG, padx=24, pady=16)
            arriba.pack(fill="x")
            tk.Label(arriba, text="COMANDOS DE VOZ", bg=BG, fg=CYAN,
                     font=("Segoe UI", 15, "bold")).pack(anchor="w")
            tk.Label(arriba, text="Di «Hey Jarvis» y una de estas frases (o escríbela en el chat). "
                     "No hace falta decirlas exactas.", bg=BG, fg=MUTED,
                     font=("Segoe UI", 9), wraplength=700, justify="left").pack(anchor="w", pady=(2, 10))
            fila = tk.Frame(arriba, bg=BG)
            fila.pack(fill="x")
            tk.Label(fila, text="🔎", bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(side="left", padx=(0, 6))
            busqueda = tk.StringVar(value=buscar)
            self._commands_search = busqueda
            entrada = tk.Entry(fila, textvariable=busqueda, bg=PANEL_LIGHT, fg=TEXT, insertbackground=CYAN,
                               relief="flat", font=("Segoe UI", 10),
                               highlightthickness=1, highlightbackground=BORDER, highlightcolor=CYAN)
            entrada.pack(side="left", fill="x", expand=True, ipady=5)
            tk.Frame(ventana, bg=BORDER, height=1).pack(fill="x", padx=24)

            marco = tk.Frame(ventana, bg=BG)
            marco.pack(fill="both", expand=True)
            lienzo = tk.Canvas(marco, bg=BG, highlightthickness=0, bd=0)
            barra = ttk.Scrollbar(marco, orient="vertical", command=lienzo.yview,
                                  style="Jarvis.Vertical.TScrollbar")
            lienzo.configure(yscrollcommand=barra.set)
            barra.pack(side="right", fill="y", padx=(0, 6), pady=8)
            lienzo.pack(side="left", fill="both", expand=True)
            cuerpo = tk.Frame(lienzo, bg=BG, padx=24, pady=14)
            item = lienzo.create_window((0, 0), window=cuerpo, anchor="nw")
            cuerpo.bind("<Configure>", lambda _e: lienzo.configure(scrollregion=lienzo.bbox("all")))
            lienzo.bind("<Configure>", lambda e: lienzo.itemconfigure(item, width=e.width))
            cuerpo.jarvis_scroll = lienzo  # rueda del ratón (ver _on_mousewheel)
            lienzo.jarvis_scroll = lienzo

            def dibujar(*_args) -> None:
                for hijo in cuerpo.winfo_children():
                    hijo.destroy()
                visibles = jarvis_comandos.filtrar(secciones, busqueda.get())
                if not visibles:
                    tk.Label(cuerpo, text="No hay comandos con esas palabras. Igual puedes pedírselo a Jarvis: "
                             "lo que no está en la lista lo resuelve la IA.", bg=BG, fg=MUTED,
                             font=("Segoe UI", 10), wraplength=680, justify="left").pack(anchor="w", pady=10)
                for seccion in visibles:
                    tarjeta = tk.Frame(cuerpo, bg=PANEL, padx=16, pady=12,
                                       highlightthickness=1, highlightbackground=BORDER)
                    tarjeta.pack(fill="x", pady=(0, 10))
                    tk.Label(tarjeta, text=f"{seccion.icono}  {seccion.titulo}", bg=PANEL, fg=CYAN,
                             font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 6))
                    for comando in seccion.comandos:
                        linea = tk.Frame(tarjeta, bg=PANEL)
                        linea.pack(fill="x", pady=3)
                        tk.Label(linea, text=f"«{comando.frase}»", bg=PANEL, fg=TEXT, anchor="w",
                                 font=("Segoe UI", 10, "bold"), wraplength=680, justify="left").pack(anchor="w")
                        tk.Label(linea, text=comando.para_que, bg=PANEL, fg=MUTED, anchor="w",
                                 font=("Segoe UI", 9), wraplength=680, justify="left").pack(anchor="w")
                if not busqueda.get().strip():
                    tk.Label(cuerpo, text="💡  " + jarvis_comandos.AYUDA_IA, bg=BG, fg=MUTED,
                             font=("Segoe UI", 9, "italic"), wraplength=680, justify="left").pack(anchor="w", pady=(4, 8))
                lienzo.yview_moveto(0)

            busqueda.trace_add("write", dibujar)
            dibujar()
            entrada.focus_set()

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
            for nombre, texto in (("inicio", "⌂   INICIO"), ("chat", "▤   CHAT"), ("rutinas", "⏱   RUTINAS"),
                                  ("juegos", "🎮   JUEGOS"), ("tele", "📺   TELE"), ("ajustes", "⚙   AJUSTES")):
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

        _PAGE_TITLES = {"inicio": "INICIO", "chat": "CHAT", "rutinas": "RUTINAS Y AUTOMATIZACIONES",
                        "juegos": "JUEGOS", "tele": "TELEVISOR", "ajustes": "AJUSTES"}

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
                self._refresh_search_settings()
                self._refresh_morning_brief_button()  # pudo cambiar por voz
            if nombre == "inicio":
                self._refresh_home_now()
            if nombre in ("tele", "ajustes"):
                self._refresh_tv_views()
            if nombre == "juegos" and getattr(self, "_games", None) is None:
                self._load_games_page()

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
            # Precarga el modelo de lenguaje mientras escribes (como al decir «Hey Jarvis»).
            self.input.bind("<KeyPress>", lambda _e: self._preload_llm("escribiendo"), add="+")
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
                         wraplength=540, font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(2, 0))
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
            self._build_voice_settings(cuerpo)
            fila("Interrumpir con «Hey Jarvis»",
                 "Mientras Jarvis habla, decir «Hey Jarvis» lo calla y te escucha. Solo con la salida en "
                 "los auriculares: por los altavoces el micrófono oiría su propia voz.",
                 "interrupt_button", "✋  INTERRUMPIR …", self._toggle_interrupt)
            self._build_mobile_settings(cuerpo)
            self._build_search_settings(cuerpo)
            fila("Aviso flotante",
                 "Con Jarvis en la bandeja, una burbuja en la esquina muestra lo que entendió y la "
                 "respuesta, y las confirmaciones con SÍ / NO, sin quitarte el foco.",
                 "floating_button", "▤  AVISO FLOTANTE …", self._toggle_floating)
            fila("Mantener el undervolt de la GPU",
                 "Cada minuto comprueba que la GPU tenga aplicado tu perfil de Adrenalin (undervolt.xml en "
                 "Documentos) y lo reaplica si se perdió tras un reinicio o un pantallazo. Si se pierde 3 veces "
                 "en una hora deja de hacerlo y te avisa.",
                 "gpu_profile_button", "⚡  UNDERVOLT …", self._toggle_gpu_profile)
            self._build_game_mode_settings(cuerpo)
            self._build_tv_settings(cuerpo)
            fila("Resumen de la mañana",
                 "La primera vez que usas la PC por la mañana, Jarvis te dice el clima, tus avisos y rutinas "
                 "de hoy y cómo está el equipo. Se pide cuando quieras con «buenos días».",
                 "morning_brief_button", "☀  RESUMEN …", self._toggle_morning_brief)
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

        # --- Panel de inicio ------------------------------------------------------

        def _home_card(self, parent: tk.Misc, titulo: str) -> tk.Frame:
            marco = tk.Frame(parent, bg=PANEL, padx=18, pady=14, highlightthickness=1, highlightbackground=BORDER)
            tk.Label(marco, text=titulo, bg=PANEL, fg=MUTED, font=("Consolas", 9, "bold")).pack(anchor="w")
            cuerpo = tk.Frame(marco, bg=PANEL)
            cuerpo.pack(fill="both", expand=True, pady=(8, 0))
            marco.jarvis_cuerpo = cuerpo
            return marco

        # --- Juegos que reconoce Jarvis --------------------------------------------------------

        def _build_games_page(self, page: tk.Frame) -> None:
            """Lista de juegos (los de ``jarvis_system.listar_juegos``) con ▶ JUGAR."""
            self._games = None
            cabecera = tk.Frame(page, bg=BG, padx=28, pady=(14))
            cabecera.pack(fill="x")
            self.games_summary = tk.Label(cabecera, text="Buscando juegos…", bg=BG, fg=MUTED,
                                          font=("Segoe UI", 10), anchor="w", justify="left")
            self.games_summary.pack(side="left")
            self._ui_button(cabecera, "⟳  ACTUALIZAR", self._load_games_page, fg=MUTED).pack(side="right")
            self.games_filter = tk.StringVar()
            buscador = tk.Entry(cabecera, textvariable=self.games_filter, bg=PANEL_LIGHT, fg=TEXT,
                                insertbackground=TEXT, relief="flat", font=("Segoe UI", 10), width=26)
            buscador.pack(side="right", padx=(0, 10), ipady=6)
            self.games_filter.trace_add("write", lambda *_: self._render_games())
            tk.Label(cabecera, text="Buscar:", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="right", padx=(0, 6))

            lienzo = tk.Canvas(page, bg=BG, highlightthickness=0, bd=0)
            barra = ttk.Scrollbar(page, orient="vertical", command=lienzo.yview,
                                  style="Jarvis.Vertical.TScrollbar")
            lienzo.configure(yscrollcommand=barra.set)
            barra.pack(side="right", fill="y", padx=(0, 6), pady=8)
            lienzo.pack(side="left", fill="both", expand=True)
            self.games_list = tk.Frame(lienzo, bg=BG, padx=28, pady=6)
            ventana = lienzo.create_window((0, 0), window=self.games_list, anchor="nw")
            self.games_list.bind("<Configure>", lambda _e: lienzo.configure(scrollregion=lienzo.bbox("all")))
            lienzo.bind("<Configure>", lambda e: lienzo.itemconfigure(ventana, width=e.width))
            self.games_list.jarvis_scroll = lienzo
            lienzo.jarvis_scroll = lienzo

        def _load_games_page(self) -> None:
            """Busca los juegos en otro hilo (un disco dormido puede tardar en responder)."""
            import logging
            import threading

            import jarvis_system

            self.games_summary.configure(text="Buscando juegos…")

            def buscar() -> None:
                try:
                    juegos = jarvis_system.listar_juegos()
                except Exception:
                    logging.exception("No se pudieron listar los juegos.")
                    juegos = []
                self.events.put(("games_list", juegos))

            threading.Thread(target=buscar, name="JarvisJuegosLista", daemon=True).start()

        def _render_games(self, juegos=None) -> None:
            if juegos is not None:
                self._games = juegos
            juegos = self._games or []
            for fila in self.games_list.winfo_children():
                fila.destroy()
            filtro = self.games_filter.get().strip().casefold()
            visibles = [j for j in juegos if not filtro or filtro in j.nombre.casefold()]
            listos = [j for j in visibles if j.ejecutable is not None]
            sin_exe = [j for j in visibles if j.ejecutable is None]
            total = sum(1 for j in juegos if j.ejecutable is not None)
            self.games_summary.configure(
                text=f"{total} juegos reconocidos. Ábrelos con «abre …» o con ▶ JUGAR. "
                     "Busca en «Games», «Epic Games» y «SteamLibrary» de cada disco, en la carpeta "
                     "«Games» de tu usuario y en Steam.")
            for juego in listos:
                fila = tk.Frame(self.games_list, bg=PANEL, padx=16, pady=10,
                                highlightthickness=1, highlightbackground=BORDER)
                fila.pack(fill="x", pady=4)
                textos = tk.Frame(fila, bg=PANEL)
                textos.pack(side="left", fill="x", expand=True)
                tk.Label(textos, text=juego.nombre, bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"),
                         anchor="w").pack(anchor="w")
                tk.Label(textos, text=f"{juego.carpeta.parent}   ·   {juego.ejecutable.name}", bg=PANEL, fg=MUTED,
                         font=("Consolas", 8), anchor="w").pack(anchor="w")
                self._ui_button(fila, "▶  JUGAR", lambda n=juego.nombre: self._play_game(n)).pack(side="right")
            if sin_exe:
                tk.Label(self.games_list, text="CARPETAS SIN UN JUEGO CLARO (no se pueden abrir por nombre)",
                         bg=BG, fg=MUTED, font=("Consolas", 9, "bold")).pack(anchor="w", pady=(16, 4))
                for juego in sin_exe:
                    tk.Label(self.games_list, text=f"•  {juego.nombre}  —  {juego.problema}", bg=BG, fg=MUTED,
                             font=("Segoe UI", 9), anchor="w", justify="left", wraplength=900).pack(anchor="w")
            if not visibles:
                tk.Label(self.games_list, text="Ningún juego coincide con la búsqueda." if filtro else
                         "No encontré juegos en las carpetas de juegos.", bg=BG, fg=MUTED,
                         font=("Segoe UI", 10)).pack(anchor="w", pady=10)

        def _play_game(self, nombre: str) -> None:
            if self.busy:
                self._set_status("ESPERA A QUE TERMINE LA ACCIÓN ACTUAL", ERROR)
                return
            self._start_direct_tool_action("launch_game", {"name": nombre}, f"abre {nombre}")

        def _build_home_page(self, page: tk.Frame) -> None:
            import jarvis_dashboard

            lienzo = tk.Canvas(page, bg=BG, highlightthickness=0, bd=0)
            lienzo.pack(fill="both", expand=True)
            cuerpo = tk.Frame(lienzo, bg=BG, padx=28, pady=18)
            ventana = lienzo.create_window((0, 0), window=cuerpo, anchor="nw")
            cuerpo.bind("<Configure>", lambda _e: lienzo.configure(scrollregion=lienzo.bbox("all")))
            lienzo.bind("<Configure>", lambda e: lienzo.itemconfigure(ventana, width=e.width))
            cuerpo.jarvis_scroll = lienzo
            cuerpo.grid_columnconfigure((0, 1), weight=1, uniform="inicio")
            self._home_layout = None  # «ancho» o «angosto»: se reacomoda al cambiar el tamaño

            # Encabezado: hora, fecha, saludo, clima y accesos rápidos.
            hero = tk.Frame(cuerpo, bg=PANEL, padx=22, pady=18, highlightthickness=1, highlightbackground=BORDER)
            hero.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
            hero.grid_columnconfigure(1, weight=1)
            izquierda = tk.Frame(hero, bg=PANEL)
            izquierda.grid(row=0, column=0, sticky="nw")
            self.home_greet = tk.Label(izquierda, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 11))
            self.home_greet.pack(anchor="w")
            self.home_clock = tk.Label(izquierda, text="--:--", bg=PANEL, fg=CYAN, font=("Segoe UI", 40, "bold"))
            self.home_clock.pack(anchor="w")
            self.home_date = tk.Label(izquierda, text="", bg=PANEL, fg=TEXT, font=("Segoe UI", 12))
            self.home_date.pack(anchor="w")
            derecha = tk.Frame(hero, bg=PANEL)
            derecha.grid(row=0, column=1, sticky="nsew", padx=(28, 0))
            self._home_hero = (izquierda, derecha)
            tk.Label(derecha, text="CLIMA", bg=PANEL, fg=MUTED, font=("Consolas", 9, "bold")).pack(anchor="w")
            self.home_weather = tk.Label(derecha, text="Consultando…", bg=PANEL, fg=TEXT, font=("Segoe UI", 10),
                                         justify="left", anchor="w", wraplength=300)
            self.home_weather.pack(anchor="w", fill="x", pady=(4, 10))
            accesos = tk.Frame(derecha, bg=PANEL)
            accesos.pack(anchor="w")
            for texto, comando in (("🎙  HABLAR", self._on_orb_click), ("🧹  LIBERAR ESPACIO",
                                   lambda: self._start_cleanup("Panel de inicio: liberar espacio", None)),
                                   ("▣  MODO COMPACTO", self._enter_compact_mode)):
                self._ui_button(accesos, texto, comando).pack(side="left", padx=(0, 8))

            # Salud del equipo.
            salud = self._home_card(cuerpo, "SALUD DEL EQUIPO")
            salud.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(0, 12))
            self._home_bars = {}
            for clave, nombre in (("gpu", "GPU"), ("gpu_hot", "GPU punto caliente"), ("gpu_load", "Carga GPU"),
                                  ("gpu_mem", "Memoria GPU"), ("cpu", "CPU"), ("cpu_load", "Carga CPU"), ("ram", "Memoria RAM")):
                self._home_bars[clave] = self._home_bar_row(salud.jarvis_cuerpo, nombre)
            # Una barra por disco instalado; se rehacen si cambian (p. ej. al conectar uno).
            self._home_disks = tk.Frame(salud.jarvis_cuerpo, bg=PANEL)
            self._home_disks.pack(fill="x")
            self._home_disk_keys: list[str] | None = None
            self.home_health_note = tk.Label(salud.jarvis_cuerpo, text="", bg=PANEL, fg=MUTED,
                                             font=("Segoe UI", 8), justify="left", wraplength=260)
            self.home_health_note.pack(anchor="w", fill="x", pady=(6, 0))

            proximo = self._home_card(cuerpo, "PRÓXIMO")
            proximo.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(0, 12))
            self.home_upcoming = tk.Label(proximo.jarvis_cuerpo, text="…", bg=PANEL, fg=TEXT, font=("Segoe UI", 10),
                                          justify="left", anchor="nw", wraplength=260)
            self.home_upcoming.pack(anchor="w", fill="x")

            sonando = self._home_card(cuerpo, "SONANDO AHORA · AURICULARES")
            sonando.grid(row=2, column=0, sticky="nsew", padx=(0, 6))
            self.home_media = tk.Label(sonando.jarvis_cuerpo, text="…", bg=PANEL, fg=TEXT, font=("Segoe UI", 10),
                                       justify="left", anchor="w", wraplength=260)
            self.home_media.pack(anchor="w", fill="x")
            self.home_battery = tk.Label(sonando.jarvis_cuerpo, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 10),
                                         anchor="w")
            self.home_battery.pack(anchor="w", pady=(8, 0))

            servicios = self._home_card(cuerpo, "SERVICIOS")
            servicios.grid(row=2, column=1, sticky="nsew", padx=(6, 0))
            self.home_services = tk.Label(servicios.jarvis_cuerpo, text="…", bg=PANEL, fg=TEXT,
                                          font=("Consolas", 9), justify="left", anchor="w")
            self.home_services.pack(anchor="w", fill="x")
            self._home_cards = (salud, proximo, sonando, servicios)
            # Los textos se cortan al ancho real de su tarjeta (antes, a un ancho fijo que no entraba).
            for etiqueta in (self.home_weather, self.home_upcoming, self.home_media, self.home_health_note):
                etiqueta.bind("<Configure>", lambda e, l=etiqueta: l.configure(wraplength=max(120, e.width - 4)))
            cuerpo.bind("<Configure>", lambda e: self._layout_home(e.width), add="+")
            self._saludo = jarvis_dashboard.saludo
            self._tick_home_clock()

        HOME_ANCHO_DOS_COLUMNAS = 760  # px de la página: menos que eso, una tarjeta debajo de la otra

        def _layout_home(self, ancho: int) -> None:
            """Dos columnas de tarjetas (y el clima al lado de la hora) si entran; si no, una sola."""
            modo = "ancho" if ancho >= self.HOME_ANCHO_DOS_COLUMNAS else "angosto"
            if modo == self._home_layout:
                return
            self._home_layout = modo
            salud, proximo, sonando, servicios = self._home_cards
            _izquierda, derecha = self._home_hero
            if modo == "ancho":
                derecha.grid_configure(row=0, column=1, padx=(28, 0), pady=0)
                for tarjeta, (fila, columna) in zip(self._home_cards, ((1, 0), (1, 1), (2, 0), (2, 1))):
                    tarjeta.grid_configure(row=fila, column=columna, columnspan=1,
                                           padx=(0, 6) if columna == 0 else (6, 0))
            else:
                derecha.grid_configure(row=1, column=0, padx=0, pady=(14, 0))
                for fila, tarjeta in enumerate(self._home_cards, start=1):
                    tarjeta.grid_configure(row=fila, column=0, columnspan=2, padx=0)
            for tarjeta in self._home_cards:
                tarjeta.grid_configure(pady=(0, 12))

        def _tick_home_clock(self) -> None:
            from datetime import datetime

            reloj = getattr(self, "home_clock", None)
            if reloj is None or not reloj.winfo_exists():
                return
            ahora = datetime.now()
            dias = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
            meses = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                     "septiembre", "octubre", "noviembre", "diciembre")
            reloj.configure(text=ahora.strftime("%H:%M"))
            self.home_date.configure(text=f"{dias[ahora.weekday()].capitalize()} {ahora.day} de "
                                          f"{meses[ahora.month - 1]} de {ahora.year}")
            self.home_greet.configure(text=self._saludo(ahora.hour))
            reloj.after(1000, self._tick_home_clock)

        def _home_bar_row(self, padre: tk.Frame, nombre: str) -> tuple:
            """Nombre a la izquierda y valor a la derecha; debajo, la barra a todo el ancho (así el
            valor se ve aunque la ventana sea angosta)."""
            fila = tk.Frame(padre, bg=PANEL)
            fila.pack(fill="x", pady=(2, 5))
            arriba = tk.Frame(fila, bg=PANEL)
            arriba.pack(fill="x")
            tk.Label(arriba, text=nombre, bg=PANEL, fg=TEXT, font=("Segoe UI", 9), anchor="w").pack(side="left")
            valor = tk.Label(arriba, text="…", bg=PANEL, fg=MUTED, font=("Consolas", 9), anchor="e")
            valor.pack(side="right")
            barra = tk.Canvas(fila, height=6, bg=PANEL_LIGHT, highlightthickness=0, bd=0)
            barra.pack(fill="x", pady=(3, 0))
            barra.jarvis_barra = (None, CYAN)
            barra.bind("<Configure>", lambda _e: self._draw_home_bar(barra))
            return barra, valor

        def _draw_home_bar(self, barra: tk.Canvas) -> None:
            fraccion, color = barra.jarvis_barra
            barra.delete("all")
            if fraccion is not None:
                ancho = max(0.0, min(1.0, fraccion)) * max(1, barra.winfo_width())
                barra.create_rectangle(0, 0, ancho, 6, fill=color, outline="")

        def _render_home_disks(self, discos) -> None:
            claves = [d.unidad for d in discos]
            if claves != self._home_disk_keys:
                for fila in self._home_disks.winfo_children():
                    fila.destroy()
                for clave in [c for c in self._home_bars if c.startswith("disco:")]:
                    del self._home_bars[clave]
                for disco in discos:
                    self._home_bars["disco:" + disco.unidad] = self._home_bar_row(self._home_disks,
                                                                                  "Disco " + disco.titulo)
                self._home_disk_keys = claves
            for disco in discos:
                pct = disco.usado_pct
                self._set_home_bar("disco:" + disco.unidad, pct / 100,
                                   f"{pct:.0f} % · {disco.libre / 1024**3:.0f} GB libres de "
                                   f"{disco.total / 1024**3:.0f}", alerta=pct >= 90)

        def _set_home_bar(self, clave: str, fraccion: float | None, texto: str, alerta: bool = False) -> None:
            barra, valor = self._home_bars[clave]
            barra.jarvis_barra = (fraccion, ERROR if alerta else CYAN)
            self._draw_home_bar(barra)
            valor.configure(text=texto, fg=ERROR if alerta else (TEXT if fraccion is not None else MUTED))

        def _render_home(self, datos) -> None:
            import jarvis_hardware as hw

            if not hasattr(self, "home_weather") or not self.home_weather.winfo_exists():
                return
            self.home_weather.configure(text=datos.clima or "Sin datos del clima (¿sin internet o sin ciudad? "
                                                            "Dime «mi ciudad es …»).")
            s = datos.salud

            def temp(clave, valor, limite):
                if valor is None:
                    self._set_home_bar(clave, None, "sin dato")
                else:
                    self._set_home_bar(clave, valor / 110, f"{valor:.0f} °C", alerta=valor >= limite)

            temp("gpu", s.gpu, hw.LIMITE_GPU)
            temp("gpu_hot", s.gpu_punto_caliente, hw.LIMITE_GPU_PUNTO_CALIENTE)
            temp("cpu", s.cpu, hw.LIMITE_CPU)
            for clave, valor in (("gpu_load", s.gpu_carga), ("cpu_load", s.cpu_carga)):
                if valor is None:
                    self._set_home_bar(clave, None, "sin dato")
                else:
                    self._set_home_bar(clave, valor / 100, f"{valor:.0f} %")
            vram = hw.vram_pct(s)
            if vram is None:
                self._set_home_bar("gpu_mem", None, "sin dato")
            else:
                self._set_home_bar("gpu_mem", vram / 100, f"{vram:.0f} % · {s.gpu_mem_usada / 1024:.1f}/"
                                   f"{s.gpu_mem_total / 1024:.0f} GB", alerta=vram >= 95)
            self._set_home_bar("ram", datos.ram_pct / 100, f"{datos.ram_pct:.0f} % · {datos.ram_texto}",
                               alerta=datos.ram_pct >= 90)
            self._render_home_disks(datos.discos)
            nota = []
            if s.cpu is None and s.cpu_nombre:
                nota.append("La temperatura de la CPU necesita el driver PawnIO (pawnio.eu).")
            if hw.sensores.error:
                nota.append("Sensores no disponibles: " + hw.sensores.error)
            self.home_health_note.configure(text="\n".join(nota))
            lineas = []
            if datos.recordatorios:
                lineas += ["⏰  " + r for r in datos.recordatorios]
            if datos.rutinas:
                lineas += ["⏱  " + r for r in datos.rutinas]
            self.home_upcoming.configure(text="\n".join(lineas) or "Nada programado. Prueba «recuérdame en 20 "
                                                                   "minutos…» o crea una rutina.")
            self.home_media.configure(text=datos.sonando or "No suena nada.")
            if datos.auriculares is None:
                self.home_battery.configure(text="🎧  G533: no detectado")
            elif not datos.auriculares:
                self.home_battery.configure(text="🎧  G533: apagado")
            else:
                carga = f"{datos.bateria} %" if datos.bateria is not None else "sin dato"
                self.home_battery.configure(text=f"🎧  G533: batería {carga}",
                                            fg=ERROR if datos.bateria is not None and datos.bateria <= 15 else MUTED)
            modelos = ", ".join(datos.modelos_cargados) if datos.modelos_cargados else "ninguno cargado"
            voz = getattr(self, "_voice_services_ready", None)
            self.home_services.configure(text=(
                f"Ollama     {'en línea' if datos.ollama_ok else 'sin conexión'}\n"
                f"Modelos    {modelos}\n"
                f"Voz        {'lista' if voz else ('iniciando…' if voz is None else 'sin conexión')}\n"
                f"Sensores   {'LibreHardwareMonitor' if not hw.sensores.error else 'no disponibles'}"
            ))

        def _build_tv_page(self, page: tk.Frame) -> None:
            """Control remoto de la tele (Google TV): tarjeta de estado, control (encendido, cruz,
            navegación, volumen y reproducción), entradas y apps. Los botones van por ``_tv_button``."""
            import jarvis_tv

            cuerpo = tk.Frame(page, bg=BG, padx=20, pady=18)
            cuerpo.pack(fill="both", expand=True)
            columnas = tk.Frame(cuerpo, bg=BG)
            columnas.pack(anchor="nw")
            izquierda = tk.Frame(columnas, bg=BG)
            izquierda.grid(row=0, column=0, sticky="n")
            derecha = tk.Frame(columnas, bg=BG)
            derecha.grid(row=0, column=1, sticky="n", padx=(16, 0))

            def acomodar(evento):
                """Ventana angosta: las entradas y apps pasan debajo del control (antes se cortaban)."""
                ancho = izquierda.winfo_reqwidth() + derecha.winfo_reqwidth() + 16 + 40  # + márgenes
                if evento.width < ancho:
                    derecha.grid_configure(row=1, column=0, padx=0, sticky="nw")
                else:
                    derecha.grid_configure(row=0, column=1, padx=(16, 0), sticky="n")

            cuerpo.bind("<Configure>", acomodar, add="+")

            estilos = {  # fondo, texto, fondo al pasar el mouse
                "normal": (PANEL_LIGHT, TEXT, HOVER), "acento": (CYAN, BG, CYAN),
                "prender": (CYAN_DARK, CYAN, ACCENT_ACTIVE), "apagar": (PANEL_LIGHT, ERROR, HOVER),
                "suave": (PANEL_LIGHT, CYAN, HOVER),
            }

            def tecla(padre, texto, accion, estilo="normal", ancho=6, alto=1, tam=11):
                fondo, color, encima = estilos[estilo]
                boton = tk.Button(padre, text=texto, command=lambda: self._tv_button(accion), bg=fondo, fg=color,
                                  activebackground=encima, activeforeground=color, relief="flat", bd=0,
                                  width=ancho, height=alto, cursor="hand2", highlightthickness=0,
                                  font=("Segoe UI", tam, "bold" if estilo == "acento" else "normal"))
                boton.bind("<Enter>", lambda _e: boton.configure(bg=encima))
                boton.bind("<Leave>", lambda _e: boton.configure(bg=fondo))
                return boton

            def tarjeta(padre, titulo=None):
                marco = tk.Frame(padre, bg=PANEL, padx=20, pady=16, highlightthickness=1, highlightbackground=BORDER)
                marco.pack(fill="x", pady=(0, 12))
                if titulo:
                    tk.Label(marco, text=titulo, bg=PANEL, fg=MUTED,
                             font=("Consolas", 8, "bold")).pack(anchor="w", pady=(0, 10))
                return marco

            def fila(padre, pady=(0, 8)):
                marco = tk.Frame(padre, bg=PANEL)
                marco.pack(pady=pady)
                return marco

            # Estado
            estado = tarjeta(izquierda)
            arriba = tk.Frame(estado, bg=PANEL)
            arriba.pack(fill="x")
            tk.Label(arriba, text="📺", bg=PANEL, fg=TEXT, font=("Segoe UI Emoji", 20)).pack(side="left", padx=(0, 12))
            textos = tk.Frame(arriba, bg=PANEL)
            textos.pack(side="left", fill="x", expand=True)
            linea = tk.Frame(textos, bg=PANEL)
            linea.pack(anchor="w")
            self.tv_page_name = tk.Label(linea, text="La tele", bg=PANEL, fg=TEXT, font=("Segoe UI", 13, "bold"))
            self.tv_page_name.pack(side="left")
            self.tv_page_pill = tk.Label(linea, text="…", bg=PANEL_LIGHT, fg=MUTED, font=("Segoe UI", 8, "bold"),
                                         padx=8, pady=1)
            self.tv_page_pill.pack(side="left", padx=(10, 0))
            self.tv_page_detail = tk.Label(textos, text="Conectando con la tele…", bg=PANEL, fg=MUTED,
                                           font=("Segoe UI", 10), anchor="w")
            self.tv_page_detail.pack(anchor="w", pady=(2, 0))

            # Control
            control = tarjeta(izquierda, "CONTROL")
            energia = fila(control, pady=(0, 14))
            tecla(energia, "⏻  Prender", "power_on", "prender", ancho=14).pack(side="left", padx=4)
            tecla(energia, "⏻  Apagar", "power_off", "apagar", ancho=14).pack(side="left", padx=4)

            cruz = tk.Frame(control, bg=PANEL)
            cruz.pack(pady=(0, 14))
            for texto, accion, f, c, estilo in (("▲", "up", 0, 1, "normal"), ("◀", "left", 1, 0, "normal"),
                                                ("OK", "ok", 1, 1, "acento"), ("▶", "right", 1, 2, "normal"),
                                                ("▼", "down", 2, 1, "normal")):
                tecla(cruz, texto, accion, estilo, ancho=5, alto=2, tam=12).grid(row=f, column=c, padx=3, pady=3)

            navegar = fila(control, pady=(0, 14))
            for texto, accion in (("↩  Atrás", "back"), ("⌂  Inicio", "home"), ("⚙  Panel", "settings")):
                tecla(navegar, texto, accion, ancho=10).pack(side="left", padx=4)

            abajo = tk.Frame(control, bg=PANEL)
            abajo.pack()
            for f, (titulo, botones) in enumerate((
                    ("VOLUMEN", (("−", "volume_down"), ("🔇", "mute"), ("+", "volume_up"))),
                    ("REPRODUCCIÓN", (("◀◀", "rewind"), ("▶ ❚❚", "play_pause"), ("▶▶", "forward"))))):
                tk.Label(abajo, text=titulo, bg=PANEL, fg=MUTED, font=("Consolas", 8, "bold"),
                         anchor="w").grid(row=f, column=0, sticky="w", padx=(0, 12))
                for c, (texto, accion) in enumerate(botones, start=1):
                    tecla(abajo, texto, accion, ancho=5, tam=10).grid(row=f, column=c, padx=3, pady=3)

            # Entradas, apps y frases
            entradas = tarjeta(derecha, "ENTRADAS")
            marco = tk.Frame(entradas, bg=PANEL)
            marco.pack(anchor="w")
            for n in range(1, 5):  # 2×2: la columna derecha queda angosta y entra al lado del control
                tecla(marco, f"HDMI {n}", f"hdmi{n}", "suave", ancho=10).grid(row=(n - 1) // 2, column=(n - 1) % 2,
                                                                             padx=(0, 6), pady=(0, 6))
            apps = tarjeta(derecha, "APPS")
            rejilla = tk.Frame(apps, bg=PANEL)
            rejilla.pack(anchor="w")
            tk.Label(apps, text="Las apps que abras con el control de la tele aparecen acá solas.", bg=PANEL,
                     fg=MUTED, font=("Segoe UI", 8), wraplength=240, justify="left").pack(anchor="w", pady=(4, 0))
            self._tv_apps_grid, self._tv_apps_key = rejilla, None

            def dibujar_apps(lista):
                """Rehace la rejilla si cambió la lista (fijas + aprendidas)."""
                if lista == self._tv_apps_key or not rejilla.winfo_exists():
                    return
                self._tv_apps_key = list(lista)
                for hijo in rejilla.winfo_children():
                    hijo.destroy()
                for i, (clave, nombre) in enumerate(lista):
                    tecla(rejilla, nombre, f"app:{clave}", ancho=12, tam=10).grid(row=i // 2, column=i % 2,
                                                                                  padx=(0, 6), pady=(0, 6))

            self._render_tv_apps = dibujar_apps
            dibujar_apps([(clave, nombre) for clave, (nombre, _enlace) in jarvis_tv.APPS.items()])
            ayuda = tarjeta(derecha, "POR VOZ")
            tk.Label(ayuda, text="«prende la tele» · «sube el volumen de la tele» · «abre Netflix en la tele» · "
                     "«pon la tele en HDMI 1» · «pausa la tele»", bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                     wraplength=240, justify="left").pack(anchor="w")

        def _paint_tv_status(self, nombre: str, corto: str, detalle: str, tono: str) -> None:
            """Tarjeta de estado de la página 📺 TELE (la llama ``_refresh_tv_views``)."""
            if getattr(self, "tv_page_name", None) is None:
                return
            fondo, color = {"on": (CYAN_DARK, CYAN), "error": (PANEL_LIGHT, ERROR)}.get(tono, (PANEL_LIGHT, MUTED))
            self.tv_page_name.configure(text=nombre)
            self.tv_page_pill.configure(text=corto.upper(), bg=fondo, fg=color)
            self.tv_page_detail.configure(text=detalle or " ")

        def _build_tv_settings(self, cuerpo: tk.Frame) -> None:
            """Tarjeta «Televisor»: buscar la tele en la red, vincularla con su código y olvidarla."""
            marco = tk.Frame(cuerpo, bg=PANEL, padx=18, pady=14, highlightthickness=1, highlightbackground=BORDER)
            marco.pack(fill="x", pady=(0, 10))
            tk.Label(marco, text="Televisor", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w")
            self.tv_settings_status = tk.Label(marco, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                                               wraplength=640, justify="left")
            self.tv_settings_status.pack(anchor="w", pady=(2, 2))
            self.tv_settings_hint = tk.Label(marco, text="Google TV / Android TV en la misma red. Se vincula una "
                                             "vez con el código que muestra la tele.", bg=PANEL, fg=MUTED,
                                             font=("Segoe UI", 9), wraplength=640, justify="left")
            self.tv_settings_hint.pack(anchor="w", pady=(0, 8))
            botones = tk.Frame(marco, bg=PANEL)
            botones.pack(anchor="w")
            self._ui_button(botones, "🔎  BUSCAR", self._tv_search, fg=MUTED).pack(side="left")
            self._ui_button(botones, "🔗  VINCULAR", self._tv_pair_start).pack(side="left", padx=(8, 0))
            self.tv_code_entry = tk.Entry(botones, width=10, bg=PANEL_LIGHT, fg=TEXT, insertbackground=CYAN,
                                          relief="flat", font=("Consolas", 12), highlightthickness=1,
                                          highlightbackground=BORDER, highlightcolor=CYAN)
            self.tv_code_entry.pack(side="left", padx=(16, 0), ipady=4)
            self._ui_button(botones, "CONFIRMAR", self._tv_pair_finish).pack(side="left", padx=(8, 0))
            self._ui_button(botones, "OLVIDAR", self._tv_forget, fg=MUTED).pack(side="left", padx=(16, 0))

        def _build_game_mode_settings(self, cuerpo: tk.Frame) -> None:
            """Modo juego: silencio de Jarvis y programas que se cierran mientras juegas."""
            marco = tk.Frame(cuerpo, bg=PANEL, padx=18, pady=14,
                             highlightthickness=1, highlightbackground=BORDER)
            marco.pack(fill="x", pady=(0, 10))
            marco.grid_columnconfigure(0, weight=1)
            tk.Label(marco, text="Modo juego", bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
            tk.Label(marco, text="Al abrir un juego, Jarvis libera la memoria de la tarjeta (la recupera al "
                     "cerrarlo). Con el silencio activado, los avisos no urgentes solo quedan en el chat y "
                     "te los resume al terminar; las alarmas y los de temperatura suenan igual.",
                     bg=PANEL, fg=MUTED, justify="left", wraplength=540,
                     font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(2, 0))
            self.game_quiet_button = self._ui_button(marco, "🔕  SILENCIO AL JUGAR …", self._toggle_game_quiet,
                                                     fg=MUTED)
            self.game_quiet_button.configure(width=30)
            self.game_quiet_button.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
            tk.Label(marco, text="Programas que se cierran al jugar y se vuelven a abrir al terminar "
                     "(separados por comas, p. ej. «Discord, Brave»). Primero se cierran como con la X; "
                     "si a los 5 s siguen abiertos, a la fuerza.",
                     bg=PANEL, fg=MUTED, justify="left", wraplength=540,
                     font=("Segoe UI", 9)).grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 4))
            fila = tk.Frame(marco, bg=PANEL)
            fila.grid(row=3, column=0, columnspan=2, sticky="ew")
            self.game_close_entry = tk.Entry(fila, bg=PANEL_LIGHT, fg=TEXT, insertbackground=CYAN, relief="flat",
                                             font=("Segoe UI", 10), highlightthickness=1,
                                             highlightbackground=BORDER, highlightcolor=CYAN)
            self.game_close_entry.pack(side="left", fill="x", expand=True, ipady=5)
            self._ui_button(fila, "GUARDAR", self._save_game_close_list).pack(side="left", padx=(8, 0))
            self._refresh_game_mode_settings()

        def _build_mobile_settings(self, cuerpo: tk.Frame) -> None:
            marco = tk.Frame(cuerpo, bg=PANEL, padx=18, pady=14, highlightthickness=1, highlightbackground=BORDER)
            marco.pack(fill="x", pady=(0, 10))
            marco.grid_columnconfigure(0, weight=1)
            tk.Label(marco, text="App del celular", bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
            tk.Label(marco, text="Chatea, da órdenes y mira las pantallas de esta PC desde la app de Android, en casa o fuera. "
                     "Solo funciona por Tailscale (red privada y cifrada entre tus dispositivos) y con "
                     "celulares que hayas vinculado con un código.", bg=PANEL, fg=MUTED, justify="left",
                     wraplength=540, font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(2, 0))
            botones = tk.Frame(marco, bg=PANEL)
            botones.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
            self.mobile_button = self._ui_button(botones, "▯  CELULAR …", self._toggle_mobile, fg=MUTED)
            self.mobile_button.configure(width=30)
            self.mobile_button.pack(anchor="e")
            fila_botones = tk.Frame(botones, bg=PANEL)
            fila_botones.pack(anchor="e", pady=(6, 0))
            self.mobile_pair_button = self._ui_button(fila_botones, "VINCULAR CELULAR", self._pair_mobile)
            self.mobile_pair_button.pack(side="left")
            self._ui_button(fila_botones, "DESVINCULAR", self._unpair_mobile, fg=MUTED).pack(side="left", padx=(6, 0))
            self.mobile_screen_button = self._ui_button(botones, "▣  VER PANTALLA …", self._toggle_mobile_screen,
                                                        fg=MUTED)
            self.mobile_screen_button.configure(width=30)
            self.mobile_screen_button.pack(anchor="e", pady=(6, 0))
            self.mobile_control_button = self._ui_button(botones, "🖱  CONTROL DESDE EL CELULAR …",
                                                         self._toggle_mobile_control, fg=MUTED)
            self.mobile_control_button.configure(width=30)
            self.mobile_control_button.pack(anchor="e", pady=(6, 0))
            self.mobile_info = tk.Label(marco, text="", bg=PANEL, fg=MUTED, justify="left", anchor="w",
                                        font=("Consolas", 9))
            self.mobile_info.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
            self.mobile_code = tk.Label(marco, text="", bg=PANEL, fg=CYAN, justify="left", anchor="w",
                                        font=("Consolas", 16, "bold"))
            self.mobile_code.grid(row=3, column=0, columnspan=2, sticky="w")

        def _build_search_settings(self, cuerpo: tk.Frame) -> None:
            marco = tk.Frame(cuerpo, bg=PANEL, padx=18, pady=14, highlightthickness=1, highlightbackground=BORDER)
            marco.pack(fill="x", pady=(0, 10))
            tk.Label(marco, text="Búsqueda en internet", bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 11, "bold")).pack(anchor="w")
            tk.Label(marco, text="Cuando le preguntas algo reciente o que no conoce, Jarvis busca en internet "
                     "y responde citando las fuentes. Solo sale a internet el texto buscado. Usa Tavily "
                     "(clave TAVILY_API_KEY en el .env junto a Jarvis.exe) y DuckDuckGo de respaldo.",
                     bg=PANEL, fg=MUTED, justify="left", wraplength=620,
                     font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))
            self.search_info = tk.Label(marco, text="", bg=PANEL, fg=MUTED, justify="left", anchor="w",
                                        font=("Consolas", 9))
            self.search_info.pack(anchor="w", pady=(8, 0))
            self._refresh_search_settings()

        def _refresh_search_settings(self) -> None:
            etiqueta = getattr(self, "search_info", None)
            if etiqueta is None:
                return
            import logging

            import jarvis_core as core
            from plugins import busqueda

            try:
                texto = busqueda.resumen_uso(core.obtener_preferencia, busqueda.leer_clave())
            except Exception:  # la BD ocupada no debe romper Ajustes
                logging.exception("No se pudo leer el uso de la búsqueda en internet.")
                texto = "No pude leer el uso de este mes."
            etiqueta.configure(text=texto)

        def _build_voice_settings(self, cuerpo: tk.Frame) -> None:
            import jarvis_voices

            marco = tk.Frame(cuerpo, bg=PANEL, padx=18, pady=14, highlightthickness=1, highlightbackground=BORDER)
            marco.pack(fill="x", pady=(0, 10))
            tk.Label(marco, text="Voz de Jarvis", bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 11, "bold")).pack(anchor="w")
            tk.Label(marco, text="Elige con qué voz te responde. ▶ dice una frase de prueba. "
                     "También: «cambia tu voz a la argentina», «usa la voz de Daniela».",
                     bg=PANEL, fg=MUTED, font=("Segoe UI", 9), justify="left", wraplength=620).pack(anchor="w", pady=(2, 8))
            self._voice_buttons: dict[str, tk.Button] = {}
            for voz in jarvis_voices.VOCES:
                fila = tk.Frame(marco, bg=PANEL)
                fila.pack(fill="x", pady=2)
                instalada = jarvis_voices.instalada(voz)
                boton = self._ui_button(fila, voz.nombre.upper(), lambda c=voz.clave: self._set_voice(c), fg=MUTED)
                boton.configure(width=20, anchor="w", state="normal" if instalada else "disabled")
                boton.pack(side="left")
                self._voice_buttons[voz.clave] = boton
                probar = self._ui_button(fila, "▶", lambda c=voz.clave: self._test_voice(c), fg=CYAN)
                probar.configure(state="normal" if instalada else "disabled")
                probar.pack(side="left", padx=(6, 0))
                tk.Label(fila, text=voz.descripcion + ("" if instalada else " · no instalada"), bg=PANEL,
                         fg=MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(12, 0))
            self._refresh_voice_buttons()

        def _refresh_voice_buttons(self) -> None:
            actual = getattr(self, "_voice_key", None)
            for clave, boton in getattr(self, "_voice_buttons", {}).items():
                boton.configure(bg=CYAN_DARK if clave == actual else PANEL_LIGHT,
                                fg=CYAN if clave == actual else MUTED)

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
                movil = getattr(self, "mobile", None)
                if movil is not None and movil.activo():
                    movil.estado(self._orb_mode)
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

        # --- App del celular (jarvis_mobile) ---------------------------------------

        def _notify_mobile(self, role: str, text: str, aviso: str | None = None) -> None:
            movil = getattr(self, "mobile", None)
            if movil is None or not movil.activo():
                return
            if role != "user":
                import jarvis_markdown

                text = jarvis_markdown.texto_plano(text)
            movil.mensaje(role, text, aviso)

        def _notify_mobile_live(self, text: str | None) -> None:
            """Respuesta en vivo para el celular; ``None`` = se descartó."""
            movil = getattr(self, "mobile", None)
            if movil is None or not movil.activo():
                return
            if text is None:
                movil.fin_en_vivo()
                return
            import jarvis_markdown

            movil.en_vivo(jarvis_markdown.texto_plano(text).strip())

        def _mobile_question(self, texto, si, no, aprobar_cb, rechazar_cb, imagen=None,
                             alto_riesgo: bool = False) -> str | None:
            """Publica una confirmación para responderla desde el celular. Devuelve su id."""
            movil = getattr(self, "mobile", None)
            if movil is None or not movil.activo():
                return None
            import io
            import secrets

            datos = None
            if imagen is not None:
                try:
                    copia = imagen.convert("RGB")
                    copia.thumbnail((1280, 1280))
                    memoria = io.BytesIO()
                    copia.save(memoria, "JPEG", quality=80)
                    datos = memoria.getvalue()
                except Exception:
                    datos = None
            qid = "q" + secrets.token_hex(4)
            self._mobile_callbacks = {qid: (aprobar_cb, rechazar_cb)}
            movil.pregunta(qid, texto, si, no, datos, alto_riesgo=alto_riesgo)
            return qid

        def _notify_floating(self, role: str, text: str) -> None:
            if not self._floating_active() or getattr(self, "_floating_muted", False) is True:
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
            aviso: str | None = None,
            momento=None,
        ) -> None:
            """``aviso`` ("alarm"/"info"): recordatorio o alerta; el celular lo notifica.
            ``momento``: hora del mensaje (los del historial); por defecto, ahora."""
            import jarvis_cards

            should_follow = self._chat_following
            is_user = role == "user"
            if is_user and momento is None:
                import jarvis_core

                # El turno se guarda al terminar la respuesta: así queda con la hora de ahora.
                jarvis_core.marcar_mensaje_usuario(getattr(self, "conversation_id", ""), text)
            row = tk.Frame(self.messages, bg=PANEL)
            row.pack(fill="x", padx=14, pady=8)

            bubble = tk.Frame(
                row,
                bg=CYAN_DARK if is_user else PANEL_LIGHT,
                padx=14,
                pady=10,
            )
            bubble.pack(side="right" if is_user else "left", anchor="e" if is_user else "w")

            self._bubble_header(bubble, "TÚ" if is_user else "JARVIS",
                                CYAN_DARK if is_user else PANEL_LIGHT, CYAN if is_user else VIOLET,
                                jarvis_cards.hora_mensaje(momento))
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
            self._notify_mobile(role, text, aviso)
            if should_follow:
                self.chat_canvas.after_idle(self._scroll_chat_to_bottom)

        def _bubble_header(self, bubble: tk.Frame, quien: str, bg: str, fg: str, hora: str) -> tk.Frame:
            """«TÚ   12/11 14:22» encima del texto de una burbuja."""
            cabecera = tk.Frame(bubble, bg=bg)
            cabecera.pack(anchor="w", fill="x", pady=(0, 5))
            tk.Label(cabecera, text=quien, bg=bg, fg=fg, font=("Consolas", 8, "bold")).pack(side="left")
            tk.Label(cabecera, text=hora, bg=bg, fg=MUTED, font=("Consolas", 8)).pack(side="left", padx=(10, 0))
            return cabecera

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
                (f"{jarvis_cards.resumen(resultado)} · {jarvis_cards.hora_mensaje()}", MUTED, ("Consolas", 8)),
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
                self._notify_mobile("assistant", texto)
                if self._chat_following:
                    self.chat_canvas.after_idle(self._scroll_chat_to_bottom)

            tarjeta.jarvis_poner_cuerpo = poner_cuerpo
            self._fade_in(fila)
            if resultado.origen != "directo":  # la respuesta de los directos llega con poner_cuerpo
                resumen_accion = f"{jarvis_cards.icono(resultado.herramienta)} {resultado.etiqueta}: {texto_estado}"
                self._notify_floating("assistant", resumen_accion)
                self._notify_mobile("assistant", resumen_accion)
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
            imagen=None,
            alto_riesgo: bool | None = None,
        ) -> tuple[tk.Frame, tk.Frame]:
            """Confirmación como tarjeta dentro del chat (antes eran ventanas aparte).

            Devuelve (tarjeta, cuerpo): el cuerpo lo rellena quien la crea. Los botones
            llaman a ``_resolve_plan_approval`` salvo que se pasen ``al_aprobar`` /
            ``al_rechazar``. Al resolverse, la tarjeta se queda en el historial con el
            resultado en lugar de los botones (``tarjeta.jarvis_resolver``).

            ``alto_riesgo`` (por defecto: las tarjetas rojas) hace que el celular pida
            huella antes de aprobar.
            """
            if alto_riesgo is None:
                alto_riesgo = color == ERROR
            aprobar_cb = al_aprobar or (lambda: self._resolve_plan_approval(True))
            rechazar_cb = al_rechazar or (lambda: self._resolve_plan_approval(False))
            qid_movil = self._mobile_question(
                resumen or titulo.capitalize(), aprobar, rechazar, aprobar_cb, rechazar_cb, imagen,
                alto_riesgo=alto_riesgo,
            )
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
                if isinstance(qid_movil, str):
                    getattr(self, "_mobile_callbacks", {}).pop(qid_movil, None)
                    self.mobile.pregunta_resuelta(
                        qid_movil, texto or (f"✓ {aprobar}" if aprobado else f"✕ {rechazar}")
                    )
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
            self._live_speaker = None
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
