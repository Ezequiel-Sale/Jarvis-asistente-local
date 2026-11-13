"""Confirmaciones: planes, herramientas de riesgo, energía y su respuesta por voz o con botones.

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Los métodos usan
los atributos de la app (``self.root``, ``self.events``, ``self.tool_registry``…).
"""

import audio_capture
import jarvis_cards
import jarvis_core as core
import jarvis_power
import jarvis_routines
import json
import logging
import queue
import re
import threading
import tkinter as tk
from collections.abc import Sequence
from jarvis_agent import PlannedAction
from jarvis_commands import (
    detectar_decision_aprobacion_plan,
    detectar_decision_energia,
    detectar_decision_guardar_rutina,
    detectar_decision_monitores,
    detectar_decision_vision,
    limpiar_transcripcion_voz,
)
from jarvis_theme import BG, CYAN, ERROR, MUTED, PANEL, PANEL_LIGHT, TEXT, VIOLET
from jarvis_tool_registry import ToolDefinition, ToolError
from tkinter import messagebox


class ConfirmacionesMixin:
    def _approval_decision(self, kind: str, transcript: str) -> bool | None:
        """«sí»/«no» (o las frases de cada tipo de confirmación) en lo dicho."""
        texto = re.sub(r"^[\s¡¿,.!?-]*(?:(?:hey|oye|ey|ei)[\s,.!-]+)?jarvis[\s,.:;!?-]*", "",
                       transcript, flags=re.IGNORECASE)
        detector = {
            "routine_save": detectar_decision_guardar_rutina,
            "vision": detectar_decision_vision,
            "power": lambda frase: detectar_decision_energia(frase, self._pending_power_action or ""),
            "screen": lambda frase: detectar_decision_energia(frase, ""),
            "display": detectar_decision_monitores,
        }.get(kind, detectar_decision_aprobacion_plan)
        decision = detector(texto)
        if decision is None:
            # Como en el apagado: también vale un «sí», «dale», «no» o «cancela» a secas.
            decision = detectar_decision_energia(texto, "")
        return decision

    def _free_approval_listening(self) -> bool:
        """El detector lo consulta desde su hilo: con una confirmación abierta,
        escucha respuestas sin «Hey Jarvis» (solo lee atributos)."""
        return (
            self._pending_plan_reply is not None
            and self.recorder is None
            and not self._free_approval_busy
        )

    def _on_approval_utterance(self, audio_path: str) -> None:
        reply = self._pending_plan_reply
        if reply is None or self.recorder is not None or self._free_approval_busy:
            audio_capture.limpiar_audio(audio_path)
            return
        self._free_approval_busy = True
        kind = self._pending_approval_kind or "plan"
        self._set_status("ESCUCHANDO TU RESPUESTA…", CYAN)
        threading.Thread(
            target=self._free_approval_worker,
            args=(audio_path, kind, reply),
            name="JarvisRespuestaLibre",
            daemon=True,
        ).start()

    def _free_approval_worker(self, audio_path: str, kind: str, reply) -> None:
        try:
            transcript = limpiar_transcripcion_voz(core.procesar_stt(audio_path))
            decision = self._approval_decision(kind, transcript) if transcript else None
            # No se registra lo dicho: puede ser una conversación de fondo.
            logging.info("Respuesta sin «Hey Jarvis» a la confirmación %s: %r.", kind, decision)
            self.events.put(("free_approval_result", decision, transcript, reply))
        except Exception:
            logging.exception("No se pudo interpretar la respuesta a la confirmación.")
            self.events.put(("free_approval_result", None, "", reply))
        finally:
            audio_capture.limpiar_audio(audio_path)

    def _request_tool_confirmation(
        self,
        tool: ToolDefinition,
        arguments: dict[str, object],
    ) -> bool:
        if self._closing:
            return False
        if tool.name == "power_control":
            # Mismo diálogo (con voz) que el comando directo.
            return self._request_power_confirmation(str(arguments.get("action")))
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        self.events.put(
            ("tool_confirmation", tool.name, tool.description, tool.risk.value, arguments, reply)
        )
        try:
            return reply.get(timeout=120)
        except queue.Empty:
            logging.warning("Expiró la confirmación de la herramienta %s.", tool.name)
            return False

    def _request_power_countdown(self, routine_name: str, action: str) -> bool:
        """Cuenta atrás de una rutina de energía. True si nadie la canceló."""
        if self._closing:
            return False
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        self.events.put(("power_countdown", routine_name, action, reply))
        try:
            return reply.get(timeout=jarvis_power.CUENTA_ATRAS_RUTINA + 60)
        except queue.Empty:
            logging.warning("La cuenta atrás de la rutina %s no respondió.", routine_name)
            self.events.put(("plan_confirmation_timeout", reply))
            return False

    def _request_power_confirmation(self, action: str) -> bool:
        """Pide «sí/no» (botones, teclado o voz con F9) para apagar, reiniciar o suspender.

        Se llama desde un hilo secundario; False si se rechaza o expira.
        """
        if self._closing:
            return False
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        self.events.put(("power_confirmation", action, reply))
        try:
            return reply.get(timeout=120)
        except queue.Empty:
            logging.warning("Expiró la confirmación de energía (%s).", action)
            self.events.put(("plan_confirmation_timeout", reply))
            return False

    def _request_plan_confirmation(
        self,
        goal: str,
        actions: Sequence[PlannedAction],
    ) -> bool:
        if self._closing:
            return False
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        self.events.put(("plan_confirmation", goal, actions, reply))
        try:
            return reply.get(timeout=120)
        except queue.Empty:
            logging.warning("Expiró la aprobación de un plan (%s acciones).", len(actions))
            self.events.put(("plan_confirmation_timeout", reply))
            return False

    def _resolve_plan_approval(self, approved: bool) -> None:
        reply = self._pending_plan_reply
        if reply is None:
            return
        self._pending_plan_reply = None
        self._pending_approval_kind = None
        self._pending_power_action = None
        if self._recording_for_plan_approval and self.recorder is not None:
            try:
                self.recorder.cancelar()
            except Exception:
                logging.exception("No se pudo cancelar la grabación de aprobación.")
            self.recorder = None
            self._recording_for_plan_approval = False
            self.mic_button.configure(state="normal", text="🎙  MIC", fg=CYAN)
        dialog = self._plan_dialog
        self._plan_dialog = None
        if dialog is not None and dialog.winfo_exists():
            resolver = getattr(dialog, "jarvis_resolver", None)
            if resolver is not None:
                resolver(approved)  # tarjeta del chat: se queda con el resultado
            else:
                if dialog.grab_current() is dialog:
                    dialog.grab_release()
                dialog.destroy()
        self._plan_voice_button = None
        self._plan_approve_button = None
        self._plan_reject_button = None
        try:
            reply.put_nowait(approved)
        except queue.Full:
            logging.warning("La confirmación del plan ya había sido resuelta.")

    def _set_plan_voice_controls(
        self,
        *,
        recording: bool,
        recognizing: bool = False,
    ) -> None:
        if self._plan_voice_button is None:
            return
        if recognizing:
            self._plan_voice_button.configure(
                text="RECONOCIENDO…",
                state="disabled",
            )
            return
        self._plan_voice_button.configure(
            text="■  DETENER (F9)" if recording else "🎙  HABLAR (F9)",
            state="normal",
        )
        state = "disabled" if recording else "normal"
        if self._plan_approve_button is not None:
            self._plan_approve_button.configure(state=state)
        if self._plan_reject_button is not None:
            self._plan_reject_button.configure(state=state)

    def _show_plan_confirmation(
        self,
        goal: str,
        actions: Sequence[PlannedAction],
        reply: queue.Queue[bool],
        *,
        kind: str = "plan",
    ) -> None:
        if self._closing:
            reply.put(False)
            return
        if self._pending_plan_reply is not None:
            logging.error("Se recibió una segunda aprobación mientras había un plan pendiente.")
            reply.put(False)
            return

        self._pending_plan_reply = reply
        self._pending_approval_kind = kind
        is_routine_save = kind == "routine_save"
        tarjeta, cuerpo = self._confirmation_card(
            titulo="J.A.R.V.I.S. PROPONE GUARDAR UNA RUTINA" if is_routine_save else "J.A.R.V.I.S. PROPONE UN PLAN",
            resumen=(f"¿Guardo la rutina? {goal}" if is_routine_save else f"¿Ejecuto este plan? {goal}"),
            color=CYAN,
            aprobar="GUARDAR RUTINA" if is_routine_save else "EJECUTAR PLAN",
            rechazar="NO GUARDAR" if is_routine_save else "NO EJECUTAR",
            pista=(
                "Di «sí» / «guardar rutina» o «no», sin «Hey Jarvis». Guardar la rutina no ejecuta sus pasos."
                if is_routine_save
                else "Di «sí» / «ejecutar plan» o «no», sin «Hey Jarvis». "
                "Las acciones de riesgo alto pueden pedir confirmación adicional."
            ),
        )
        self._plan_dialog = tarjeta
        tk.Label(cuerpo, text=f"Objetivo: {goal}", bg=PANEL_LIGHT, fg=TEXT, font=("Segoe UI", 10),
                 wraplength=680, justify="left").pack(anchor="w", pady=(0, 8))

        for index, action in enumerate(actions, start=1):
            if is_routine_save:
                try:
                    routine_steps = json.loads(str(action.arguments["steps_json"]))
                except (KeyError, json.JSONDecodeError):
                    routine_steps = []
                step_details = "\n".join(
                    f"   {step_index}. {self.tool_registry.get(step['tool']).display_name}"
                    f" — {json.dumps(step['arguments'], ensure_ascii=False)}"
                    for step_index, step in enumerate(routine_steps, start=1)
                )
                trigger = str(action.arguments.get("trigger", "time"))
                if trigger == "time":
                    cuando = (
                        f"Horario: {action.arguments.get('time', '--:--')} | "
                        f"Días: {action.arguments.get('days', '1,2,3,4,5,6,7')}"
                    )
                else:
                    cuando = "Cuándo: " + jarvis_routines.describir_disparador(
                        jarvis_routines._validate_trigger(trigger), "", ()
                    )
                permiso = (
                    "Automática: se ejecutará sin preguntar"
                    if action.arguments.get("automatic")
                    else "Pedirá aprobación cada vez que se active"
                )
                step = (
                    f"Rutina: {action.arguments['name']}\n"
                    f"   {cuando}\n"
                    f"   Permiso: {permiso}\n"
                    f"   Pasos:\n{step_details}"
                )
            else:
                self._plan_step_row(cuerpo, index, action)
                continue
            tk.Label(
                cuerpo, text=step, bg=PANEL, fg=TEXT, padx=12, pady=9, font=("Consolas", 9),
                justify="left", wraplength=660, anchor="w",
            ).pack(fill="x", pady=3)
        self._set_plan_voice_controls(recording=False)

    def _plan_step_row(self, cuerpo: tk.Widget, index: int | None, action: PlannedAction) -> None:
        """Un paso del plan en lenguaje llano: «2  🖥 Configurar monitores · RIESGO ALTO»
        y debajo «Desactivar el monitor 3» (sin JSON ni la descripción para el modelo)."""
        colores = {"MUTED": MUTED, "VIOLET": VIOLET, "ERROR": ERROR, "CYAN": CYAN}
        fila = tk.Frame(cuerpo, bg=PANEL, padx=12, pady=8)
        fila.pack(fill="x", pady=3)
        cabecera = tk.Frame(fila, bg=PANEL)
        cabecera.pack(fill="x")
        if index is not None:
            tk.Label(cabecera, text=f"{index}", bg=PANEL, fg=CYAN, font=("Consolas", 11, "bold"),
                     width=2, anchor="w").pack(side="left")
        tk.Label(
            cabecera, text=f"{jarvis_cards.icono(action.tool.name)}  {action.tool.display_name}",
            bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold"), anchor="w",
        ).pack(side="left")
        texto_riesgo, color = jarvis_cards.riesgo(action.tool.risk.value)
        tk.Label(cabecera, text=texto_riesgo, bg=PANEL, fg=colores.get(color, MUTED),
                 font=("Consolas", 8, "bold")).pack(side="right")
        detalle = jarvis_cards.describir_paso(action.tool.name, dict(action.arguments))
        if detalle:
            tk.Label(fila, text=detalle, bg=PANEL, fg=TEXT, font=("Segoe UI", 10), justify="left",
                     anchor="w", wraplength=620).pack(fill="x", padx=(26, 0), pady=(3, 0))

    def _show_power_countdown(self, routine_name: str, action: str, reply: queue.Queue[bool]) -> None:
        """«Apagaré el equipo en 60 s» con CANCELAR; si nadie responde, se ejecuta."""
        if self._closing:
            reply.put(False)
            return
        if self._pending_plan_reply is not None:
            # Hay otra aprobación abierta: no se apaga sin poder avisar bien.
            logging.warning("Cuenta atrás de energía omitida: había otra aprobación pendiente.")
            reply.put(False)
            return
        self._pending_plan_reply = reply
        self._pending_approval_kind = "power"
        self._pending_power_action = action
        restante = {"s": jarvis_power.CUENTA_ATRAS_RUTINA}
        tarjeta, cuerpo = self._confirmation_card(
            titulo=f"RUTINA «{routine_name.upper()}»", color=ERROR,
            aprobar="HACERLO YA", rechazar="CANCELAR", aprobar_bg=PANEL, aprobar_fg=ERROR,
            resumen=f"Rutina «{routine_name}»: {jarvis_power.describir_cuenta_atras(action, restante['s'])}",
            pista="Di «cancela» o pulsa CANCELAR. ESC cancela · ENTER lo hace ya.",
            al_aprobar=lambda: self._resolve_plan_approval(True),
            al_rechazar=lambda: self._resolve_plan_approval(False),
        )
        self._plan_dialog = tarjeta
        # ENTER sin querer no debe apagar: el botón con el foco es CANCELAR.
        self._plan_reject_button.configure(bg=CYAN, fg=BG)
        etiqueta = tk.Label(cuerpo, text="", bg=PANEL_LIGHT, fg=TEXT, font=("Segoe UI", 14), justify="left")
        etiqueta.pack(anchor="w")
        try:
            self.root.attributes("-topmost", True)
            self.root.after(1500, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass

        def tic() -> None:
            if self._pending_plan_reply is not reply or not tarjeta.winfo_exists():
                return
            if restante["s"] <= 0:
                self._resolve_plan_approval(True)  # nadie canceló
                return
            etiqueta.configure(text=jarvis_power.describir_cuenta_atras(action, restante["s"]))
            restante["s"] -= 1
            tarjeta.after(1000, tic)

        tic()
        self._set_plan_voice_controls(recording=False)
        verbo = jarvis_power.VERBOS_RUTINA.get(action, "apagaré")
        self._announce(
            f"Rutina {routine_name}: {verbo} el equipo en "
            f"{jarvis_power.CUENTA_ATRAS_RUTINA} segundos. Di «cancela» si no quieres.",
            alarm=True,
        )

    def _show_tool_confirmation(self, name, description, risk, arguments, reply) -> None:
        """«¿Autorizas esta acción?» de una herramienta que pidió el modelo."""
        self._pending_plan_reply = reply
        self._pending_approval_kind = "tool"
        tarjeta, cuerpo = self._confirmation_card(
            titulo="¿AUTORIZAS ESTA ACCIÓN?", color=ERROR if risk == "high" else CYAN,
            aprobar="AUTORIZAR", rechazar="NO",
            pista="Di «sí» o «no», sin «Hey Jarvis». ENTER autoriza · ESC rechaza.",
        )
        self._plan_dialog = tarjeta
        # En lenguaje llano: qué se va a hacer y qué implica (sin la descripción
        # pensada para el modelo ni los parámetros en JSON).
        try:
            definicion = self.tool_registry.get(name)
        except ToolError:
            definicion = None
        if definicion is not None:
            self._plan_step_row(cuerpo, None, PlannedAction(tool=definicion, arguments=arguments))
        else:
            tk.Label(cuerpo, text=jarvis_cards.describir_paso(name, dict(arguments)) or name, bg=PANEL,
                     fg=TEXT, font=("Segoe UI", 10), wraplength=620, justify="left").pack(fill="x")
        tk.Label(cuerpo, text=jarvis_cards.aviso(name, risk), bg=PANEL_LIGHT, fg=MUTED,
                 font=("Segoe UI", 9), wraplength=620, justify="left").pack(anchor="w", pady=(6, 0))
        self._set_plan_voice_controls(recording=False)
        self._set_status(self._approval_hint("tool"), CYAN)

    def _show_power_confirmation(self, action: str, reply: queue.Queue[bool]) -> None:
        """Tarjeta «¿Apago el equipo?» que acepta botones, teclado o voz."""
        if self._closing or self._pending_plan_reply is not None:
            if self._pending_plan_reply is not None:
                logging.error("Se pidió confirmar energía con otra aprobación pendiente.")
            reply.put(False)
            return
        self._pending_plan_reply = reply
        self._pending_approval_kind = "power"
        self._pending_power_action = action
        pregunta = jarvis_power.PREGUNTAS_CONFIRMACION.get(action, "¿Confirmas la acción?")
        tarjeta, cuerpo = self._confirmation_card(
            titulo="CONFIRMAR", color=ERROR, aprobar="SÍ", rechazar="NO",
            aprobar_bg=ERROR, aprobar_fg=BG, resumen=pregunta,
            pista="Di «sí» o «no», sin «Hey Jarvis». ENTER confirma · ESC cancela.",
        )
        self._plan_dialog = tarjeta
        tk.Label(cuerpo, text=pregunta, bg=PANEL_LIGHT, fg=TEXT, font=("Segoe UI", 12),
                 wraplength=680, justify="left").pack(anchor="w")
        self._set_plan_voice_controls(recording=False)
        self._set_status(self._approval_hint("power"), CYAN)

    _APPROVAL_PHRASES = {
        "plan": ("SÍ", "NO"),
        "routine_save": ("SÍ", "NO"),
        "vision": ("SÍ", "NO"),
        "power": ("SÍ", "NO"),
        "screen": ("SÍ", "NO"),
        "display": ("SÍ", "NO"),
        "tool": ("SÍ", "NO"),
        "cleanup": ("SÍ", "NO"),
    }

    def _approval_hint(self, kind: str) -> str:
        approve, reject = self._APPROVAL_PHRASES.get(kind, self._APPROVAL_PHRASES["plan"])
        return f"DI «{approve}» O «{reject}»"

    def _confirm_clear_history(self) -> None:
        if self.root.state() == "withdrawn":
            self._show_from_tray()
        if not messagebox.askyesno(
            "Borrar historial de conversaciones",
            "¿Borrar todas las conversaciones guardadas y los audios de respuestas?\n\n"
            "Se conservan tus recuerdos, rutinas e historial de acciones. "
            "Esta acción no se puede deshacer.",
            parent=self.chat_window if self.chat_window.state() != "withdrawn" else self.root,
            icon="warning",
        ):
            self._add_message("assistant", "No borré el historial.")
            return
        try:
            self.conversation_id = core.borrar_historial_conversaciones()
        except Exception as error:
            logging.exception("No se pudo borrar el historial de conversaciones.")
            self._set_status("ERROR AL BORRAR EL HISTORIAL", ERROR)
            self._add_message("assistant", f"No pude borrar el historial: {error}")
            return
        self._stop_player()
        for child in self.messages.winfo_children():
            child.destroy()
        self._add_message(
            "assistant",
            "Borré el historial de conversaciones y los audios de respuestas. "
            "Tus recuerdos, rutinas e historial de acciones se conservan.",
        )
        self._set_status("HISTORIAL BORRADO", CYAN)
