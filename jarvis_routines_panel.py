"""Panel de rutinas y automatizaciones (roadmap punto 3).

Permite ver cada rutina con su disparador y pasos, pausarla o reanudarla,
cambiar su permiso (solo puede ser automática si todas sus acciones son de
riesgo bajo) y eliminarla. Opera en el hilo de Tk con consultas SQLite locales.
"""

import logging
import tkinter as tk
from tkinter import messagebox

import jarvis_routines
from jarvis_routine_form import RoutineForm
from jarvis_theme import (
    BG, CYAN, ERROR, HOVER, MUTED, PANEL, PANEL_LIGHT, TEXT, VIOLET,
)
from jarvis_tool_registry import ToolError, ToolRegistry

AYUDA = (
    "Pulsa «+ NUEVA» para crear una rutina o un comando personalizado con el formulario,\n"
    "o díselo a Jarvis. Ejemplos:\n"
    "«Cuando conecte los auriculares, cambia la salida a auriculares automáticamente»\n"
    "«Cuando desconecte los auriculares, cambia la salida a parlantes automáticamente»\n"
    "«Programa una rutina llamada Inicio a las 09:00 de lunes a viernes y pon el volumen al 30%»"
)


_VALORES_LEGIBLES = {
    "headphones": "auriculares",
    "speakers": "parlantes",
    "True": "sí",
    "False": "no",
    "shutdown": "apagar",
    "restart": "reiniciar",
    "sleep": "suspender",
    "pause": "pausar",
    "play": "reanudar",
    "next": "siguiente",
    "previous": "anterior",
}


class RoutinesPanel:
    def __init__(self, parent: tk.Misc, registry: ToolRegistry, *, incrustado: bool = False) -> None:
        """``incrustado``: sección de la ventana principal (un Frame que ocupa
        ``parent``) en vez de una ventana aparte."""
        self.registry = registry
        self.incrustado = incrustado
        if incrustado:
            self.window = tk.Frame(parent, bg=BG)
            self.window.pack(fill="both", expand=True)
        else:
            self.window = tk.Toplevel(parent)
            self.window.title("J.A.R.V.I.S. | Rutinas y automatizaciones")
            self.window.geometry("860x560")
            self.window.minsize(640, 400)
            self.window.configure(bg=BG)
            self.window.bind("<Escape>", lambda _event: self.close())
        self.window.grid_rowconfigure(1, weight=1)
        self.window.grid_columnconfigure(0, weight=1)

        header = tk.Frame(self.window, bg=BG, padx=24, pady=16)
        header.grid(row=0, column=0, sticky="ew")
        if not incrustado:  # incrustado, el título ya está en la cabecera principal
            tk.Label(
                header, text="RUTINAS Y AUTOMATIZACIONES", bg=BG, fg=CYAN,
                font=("Segoe UI", 16, "bold"),
            ).pack(side="left")
        else:
            tk.Label(
                header, text="Lo que Jarvis hace solo: a una hora, al conectar algo o al oír una frase.",
                bg=BG, fg=MUTED, font=("Segoe UI", 10),
            ).pack(side="left")
        self._button(header, "⟳  ACTUALIZAR", self.refresh).pack(side="right")
        self._button(header, "+  NUEVA", self._new_routine).pack(side="right", padx=(0, 8))

        body = tk.Frame(self.window, bg=PANEL)
        body.grid(row=1, column=0, sticky="nsew", padx=24)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg=PANEL, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = tk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        body.jarvis_scroll = self.canvas  # rueda del ratón (ver JarvisUIMixin._on_mousewheel)
        self.rows = tk.Frame(self.canvas, bg=PANEL)
        self._rows_window = self.canvas.create_window((0, 0), window=self.rows, anchor="nw")
        self.rows.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self._rows_window, width=event.width),
        )

        tk.Label(
            self.window, text=AYUDA, bg=BG, fg=MUTED, justify="left", anchor="w",
            font=("Consolas", 8),
        ).grid(row=2, column=0, sticky="ew", padx=24, pady=(10, 16))

        self.refresh()
        self.focus()

    # --- Utilidades -------------------------------------------------------------

    def _button(self, parent: tk.Misc, text: str, command, *, fg: str | None = None,
                state: str = "normal") -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, bg=PANEL_LIGHT, fg=fg or CYAN,
            activebackground=HOVER, activeforeground=TEXT, relief="flat",
            padx=10, pady=6, font=("Consolas", 8, "bold"), cursor="hand2",
            state=state, disabledforeground=MUTED,
        )

    def is_open(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def focus(self) -> None:
        if not self.incrustado:
            self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self) -> None:
        if self.is_open():
            self.window.destroy()

    def _step_labels(self, routine: jarvis_routines.RoutineInfo) -> str:
        labels = []
        for step in routine.steps:
            try:
                label = self.registry.get(step.tool).display_name
            except ToolError:
                label = step.tool
            detalle = ", ".join(
                _VALORES_LEGIBLES.get(str(value), f"{value} %" if key == "percentage" else str(value))
                for key, value in step.arguments.items()
            )
            labels.append(f"{label} ({detalle})" if detalle else label)
        return " → ".join(labels) or "sin pasos válidos"

    # --- Contenido --------------------------------------------------------------

    def refresh(self) -> None:
        for child in self.rows.winfo_children():
            child.destroy()
        try:
            routines = jarvis_routines.listar_automatizaciones()
        except Exception as error:
            logging.exception("No se pudieron cargar las rutinas.")
            tk.Label(
                self.rows, text=f"No pude cargar las rutinas: {error}", bg=PANEL, fg=ERROR,
                font=("Segoe UI", 10),
            ).pack(anchor="w", padx=16, pady=16)
            return
        if not routines:
            tk.Label(
                self.rows, text="Todavía no hay rutinas ni automatizaciones.", bg=PANEL,
                fg=MUTED, font=("Segoe UI", 11),
            ).pack(anchor="w", padx=16, pady=16)
            return
        for routine in routines:
            self._add_row(routine)

    def _add_row(self, routine: jarvis_routines.RoutineInfo) -> None:
        row = tk.Frame(self.rows, bg=PANEL_LIGHT, padx=14, pady=10)
        row.pack(fill="x", padx=12, pady=6)
        row.grid_columnconfigure(0, weight=1)

        info = tk.Frame(row, bg=PANEL_LIGHT)
        info.grid(row=0, column=0, sticky="ew")
        estado = "ACTIVA" if routine.active else "PAUSADA"
        tk.Label(
            info, text=f"{routine.identifier}. {routine.name}", bg=PANEL_LIGHT,
            fg=TEXT if routine.active else MUTED, font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")
        cuando = jarvis_routines.describir_disparador(
            routine.trigger, routine.time, routine.days, routine.phrases
        )
        if routine.once:
            cuando += ", una sola vez"
        tk.Label(
            info, text=f"{cuando[:1].upper()}{cuando[1:]}  ·  {estado}", bg=PANEL_LIGHT,
            fg=CYAN if routine.active else MUTED, font=("Consolas", 8, "bold"),
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            info, text=self._step_labels(routine), bg=PANEL_LIGHT, fg=MUTED,
            justify="left", wraplength=480, font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(4, 0))

        buttons = tk.Frame(row, bg=PANEL_LIGHT)
        buttons.grid(row=0, column=1, sticky="e", padx=(12, 0))

        try:
            bloqueantes = jarvis_routines.acciones_con_permiso(
                (step.tool for step in routine.steps), self.registry
            )
        except ToolError:
            bloqueantes = ["herramienta desconocida"]
        automatica = routine.permission == jarvis_routines.PERMISO_AUTOMATICA
        if jarvis_routines.requiere_cuenta_atras(routine.steps):
            permiso = self._button(buttons, "⏳ CUENTA ATRÁS", None, state="disabled")
        elif bloqueantes:
            permiso = self._button(buttons, "PREGUNTA (OBLIGATORIO)", None, state="disabled")
        else:
            permiso = self._button(
                buttons,
                "⚡ AUTOMÁTICA" if automatica else "✋ PREGUNTA",
                lambda: self._toggle_permission(routine, not automatica),
                fg=VIOLET if automatica else CYAN,
            )
        permiso.pack(side="left", padx=(0, 6))
        self._button(
            buttons,
            "⏸ PAUSAR" if routine.active else "▶ REANUDAR",
            lambda: self._toggle_active(routine),
        ).pack(side="left", padx=(0, 6))
        self._button(buttons, "✎ EDITAR", lambda: self._edit_routine(routine)).pack(
            side="left", padx=(0, 6)
        )
        self._button(buttons, "✕ ELIMINAR", lambda: self._delete(routine), fg=ERROR).pack(
            side="left"
        )
        if bloqueantes:
            tk.Label(
                row,
                text="Siempre pide aprobación porque incluye: " + ", ".join(bloqueantes),
                bg=PANEL_LIGHT, fg=MUTED, font=("Consolas", 7),
            ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

    # --- Acciones ---------------------------------------------------------------

    def _new_routine(self) -> None:
        RoutineForm(self.window, self.registry, self.refresh, incrustado=self.incrustado)

    def _edit_routine(self, routine: jarvis_routines.RoutineInfo) -> None:
        RoutineForm(self.window, self.registry, self.refresh, routine, incrustado=self.incrustado)

    def _toggle_permission(self, routine: jarvis_routines.RoutineInfo, automatica: bool) -> None:
        if automatica and not messagebox.askyesno(
            "Ejecutar sin preguntar",
            f"«{routine.name}» se ejecutará automáticamente, sin pedir aprobación, "
            "cada vez que se active.\n\n¿Quieres continuar?",
            parent=self.window,
        ):
            return
        try:
            jarvis_routines.cambiar_permiso_rutina(routine.identifier, automatica, self.registry)
        except (ValueError, ToolError) as error:
            messagebox.showerror("No se pudo cambiar el permiso", str(error), parent=self.window)
        self.refresh()

    def _toggle_active(self, routine: jarvis_routines.RoutineInfo) -> None:
        jarvis_routines.cambiar_estado_rutina(routine.identifier, not routine.active)
        self.refresh()

    def _delete(self, routine: jarvis_routines.RoutineInfo) -> None:
        if messagebox.askyesno(
            "Eliminar rutina",
            f"¿Eliminar «{routine.name}»? Esta acción no se puede deshacer.",
            parent=self.window,
            icon="warning",
        ):
            jarvis_routines.eliminar_rutina(routine.identifier)
            self.refresh()


__all__ = ["RoutinesPanel"]
