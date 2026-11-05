import math
import time
import tkinter as tk
from tkinter import ttk

from jarvis_actions import CancellationToken
from jarvis_theme import (BG, CYAN, CYAN_DARK, ERROR, MUTED, PANEL, PANEL_LIGHT, TEXT, VIOLET)


class JarvisUIMixin:
        def _configure_styles(self) -> None:
            style = ttk.Style()
            style.theme_use("clam")
            style.configure(
                "Jarvis.Vertical.TScrollbar",
                background=PANEL,
                troughcolor=BG,
                bordercolor=BG,
                arrowcolor=CYAN,
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
            self.root.configure(bg=BG)
            self.root.geometry("820x820")
            self.root.minsize(680, 700)
            self.root.grid_rowconfigure(2, weight=1)
            self.root.grid_columnconfigure(0, weight=1)

            header = tk.Frame(self.root, bg=BG, padx=30, pady=22)
            header.grid(row=0, column=0, sticky="ew")
            header.grid_columnconfigure(1, weight=1)
            self.chat_button = tk.Button(
                header,
                text="▤  CHAT",
                command=self._show_chat_window,
                bg=PANEL_LIGHT,
                fg=CYAN,
                activebackground="#1D374B",
                activeforeground=TEXT,
                relief="flat",
                padx=14,
                pady=10,
                font=("Consolas", 9, "bold"),
                cursor="hand2",
            )
            self.chat_button.grid(row=0, column=0, sticky="w")

            brand = tk.Frame(header, bg=BG)
            brand.grid(row=0, column=1, sticky="w", padx=(20, 0))
            tk.Label(
                brand,
                text="J.A.R.V.I.S.",
                bg=BG,
                fg=CYAN,
                font=("Segoe UI", 18, "bold"),
            ).pack(anchor="w")
            tk.Label(
                brand,
                text="VOICE INTERFACE",
                bg=BG,
                fg=MUTED,
                font=("Consolas", 8),
            ).pack(anchor="w")

            self.status = tk.Label(
                header,
                text="●  SISTEMA LISTO",
                bg=BG,
                fg=CYAN,
                font=("Consolas", 9, "bold"),
            )
            self.status.grid(row=0, column=2, sticky="e")

            tk.Frame(self.root, bg="#1C3446", height=1).grid(
                row=1, column=0, sticky="ew", padx=30
            )

            center = tk.Frame(self.root, bg=BG, padx=24, pady=12)
            center.grid(row=2, column=0, sticky="nsew")
            center.grid_rowconfigure(0, weight=1)
            center.grid_columnconfigure(0, weight=1)
            self.orb_canvas = tk.Canvas(
                center,
                bg=BG,
                highlightthickness=0,
                bd=0,
                cursor="hand2",
            )
            self.orb_canvas.grid(row=0, column=0, sticky="nsew")
            self.orb_canvas.bind("<Button-1>", self._on_orb_click)
            self.orb_canvas.bind("<Configure>", self._draw_orb)

            self.orb_status = tk.Label(
                center,
                text="PULSA EL NÚCLEO O F9 PARA HABLAR",
                bg=BG,
                fg=MUTED,
                font=("Consolas", 9, "bold"),
            )
            self.orb_status.grid(row=1, column=0, pady=(0, 18))

            footer = tk.Label(
                self.root,
                text="F9  ·  ACTIVAR / DETENER MICRÓFONO     ─     CIERRA CON X PARA DEJAR JARVIS EN SEGUNDO PLANO",
                bg=BG,
                fg=MUTED,
                font=("Consolas", 8),
            )
            footer.grid(row=3, column=0, pady=(0, 18))

            self._build_chat_window()
            self._draw_orb()
            self.root.after(40, self._animate_orb)

        def _build_chat_window(self) -> None:
            self.chat_window = tk.Toplevel(self.root)
            self.chat_window.title("J.A.R.V.I.S. | Chat")
            self.chat_window.geometry("920x720")
            self.chat_window.minsize(680, 520)
            self.chat_window.configure(bg=BG)
            self.chat_window.withdraw()
            self.chat_window.protocol("WM_DELETE_WINDOW", self._hide_chat_window)
            self.chat_window.bind("<Escape>", self._cancel_current_operation)
            self.root.bind("<Escape>", self._cancel_current_operation)
            self.chat_window.grid_rowconfigure(1, weight=1)
            self.chat_window.grid_columnconfigure(0, weight=1)

            header = tk.Frame(self.chat_window, bg=BG, padx=30, pady=18)
            header.grid(row=0, column=0, sticky="ew")
            brand = tk.Frame(header, bg=BG)
            brand.pack(side="left")
            tk.Label(
                brand,
                text="J.A.R.V.I.S.",
                bg=BG,
                fg=CYAN,
                font=("Segoe UI", 20, "bold"),
            ).pack(anchor="w")
            tk.Label(
                brand,
                text="CONVERSACIÓN LOCAL",
                bg=BG,
                fg=MUTED,
                font=("Consolas", 8),
            ).pack(anchor="w", pady=(2, 0))
            self.new_chat_button = tk.Button(
                header,
                text="＋  NUEVA CONVERSACIÓN",
                command=self._new_conversation,
                bg=PANEL_LIGHT,
                fg=CYAN,
                activebackground="#1D374B",
                activeforeground=TEXT,
                relief="flat",
                padx=12,
                pady=8,
                font=("Consolas", 8, "bold"),
                cursor="hand2",
            )
            self.new_chat_button.pack(side="right", anchor="center")
            self.routines_button = tk.Button(
                header,
                text="⏱  RUTINAS",
                command=self._show_routines_panel,
                bg=PANEL_LIGHT,
                fg=CYAN,
                activebackground="#1D374B",
                activeforeground=TEXT,
                relief="flat",
                padx=12,
                pady=8,
                font=("Consolas", 8, "bold"),
                cursor="hand2",
            )
            self.routines_button.pack(side="right", anchor="center", padx=(0, 8))

            tk.Frame(self.chat_window, bg="#1C3446", height=1).grid(
                row=0, column=0, sticky="sew", padx=30
            )

            content = tk.Frame(self.chat_window, bg=BG, padx=30, pady=18)
            content.grid(row=1, column=0, sticky="nsew")
            content.grid_rowconfigure(0, weight=1)
            content.grid_columnconfigure(0, weight=1)

            self.chat_panel = tk.Frame(
                content,
                bg=PANEL,
                highlightthickness=1,
                highlightbackground="#193246",
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
                highlightbackground="#193246",
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
                activebackground="#1D374B",
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
                activebackground="#1D374B",
                activeforeground=TEXT,
                relief="flat",
                width=3,
                font=("Consolas", 10, "bold"),
                cursor="hand2",
            )
            self.player_stop_button.pack(side="left")
            self.audio_toggle_button = tk.Button(
                player_buttons,
                text="AUDIO ON",
                command=self._toggle_audio_enabled,
                bg=PANEL_LIGHT,
                fg=CYAN,
                activebackground="#1D374B",
                activeforeground=TEXT,
                relief="flat",
                padx=8,
                font=("Consolas", 8, "bold"),
                cursor="hand2",
            )
            self.audio_toggle_button.pack(side="left", padx=(6, 0))

            composer = tk.Frame(content, bg=BG)
            composer.grid(row=2, column=0, sticky="ew", pady=(16, 0))
            composer.grid_columnconfigure(0, weight=1)
            input_panel = tk.Frame(
                composer,
                bg=PANEL,
                highlightthickness=1,
                highlightbackground="#24445A",
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
                activebackground="#1D374B",
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
                activebackground="#1D374B",
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
                activebackground="#1A545B",
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
                text=(
                    "ENTER enviar  ·  SHIFT+ENTER nueva línea  ·  "
                    "F9 o el núcleo para hablar/detener  ·  ESC cancelar"
                ),
                bg=BG,
                fg=MUTED,
                font=("Consolas", 8),
            ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        def _show_chat_window(self) -> None:
            if self._closing:
                return
            self.chat_window.deiconify()
            self.chat_window.lift()
            self.input.focus_set()

        def _hide_chat_window(self) -> None:
            self.chat_window.withdraw()

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
            if not hasattr(self, "orb_canvas") or not self.orb_canvas.winfo_exists():
                return
            canvas = self.orb_canvas
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
            glow = CYAN if active else VIOLET if processing else "#1F6873"
            bright = "#8EFFF2" if active else "#D0BCFF" if processing else CYAN

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
                (0, "#102B38", 1),
                (0.055, "#173B49", 1),
                (0.22, "#0E2633", 1),
                (0.38, "#102A38", 1),
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
                color = glow if active and index % 3 == 0 else "#28505D"
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
                (0.68, 42, 24, -1, "#3CA9B7"),
                (0.56, 76, 44, 1, VIOLET if processing else "#45D9E8"),
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
                        outline=color if active or processing else "#34818B",
                        width=3 if ring_index == 0 else 2,
                        tags="orb",
                    )

            core_radius = radius * 0.43
            canvas.create_oval(
                center_x - core_radius,
                center_y - core_radius,
                center_x + core_radius,
                center_y + core_radius,
                fill="#0A1B27",
                outline=glow,
                width=2,
                tags="orb",
            )
            canvas.create_oval(
                center_x - core_radius * 0.83,
                center_y - core_radius * 0.83,
                center_x + core_radius * 0.83,
                center_y + core_radius * 0.83,
                outline="#193B49",
                width=1,
                tags="orb",
            )
            center_label = "J.A.R.V.I.S."
            action_label = "TOCA PARA DETENER" if active else "TOCA PARA HABLAR"
            canvas.create_text(
                center_x,
                center_y - core_radius * 0.2,
                text=center_label,
                fill=TEXT,
                font=("Segoe UI", max(12, int(radius * 0.075)), "bold"),
                tags="orb",
            )
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
            self._draw_orb()

        def _update_scroll_region(self, event) -> None:
            self.chat_canvas.configure(scrollregion=event.widget.master.bbox("all"))
            if self._chat_following:
                self.chat_canvas.after_idle(self._scroll_chat_to_bottom)

        def _resize_messages(self, event) -> None:
            self.chat_canvas.itemconfigure(self.messages_window, width=event.width)

        def _on_mousewheel(self, event) -> None:
            widget = event.widget
            while widget is not None and widget is not self.chat_panel:
                widget = getattr(widget, "master", None)
            if widget is None or not self.chat_canvas.winfo_exists():
                return

            units = int(-event.delta / 120)
            if units == 0 and event.delta:
                units = -1 if event.delta > 0 else 1
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
            tk.Label(
                bubble,
                text=text,
                bg=CYAN_DARK if is_user else PANEL_LIGHT,
                fg=TEXT,
                justify="left",
                anchor="w",
                wraplength=650,
                font=("Segoe UI", 11),
            ).pack(anchor="w")

            if audio_path:
                tk.Button(
                    bubble,
                    text="▶  REPRODUCIR EN JARVIS",
                    command=lambda path=audio_path: self._play_audio(path),
                    bg=PANEL,
                    fg=CYAN,
                    activebackground="#1D374B",
                    activeforeground=TEXT,
                    relief="flat",
                    padx=10,
                    pady=6,
                    font=("Consolas", 8, "bold"),
                    cursor="hand2",
                ).pack(anchor="w", pady=(8, 0))

            if should_follow:
                self.chat_canvas.after_idle(self._scroll_chat_to_bottom)

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
