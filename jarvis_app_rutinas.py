"""Rutinas: programadas, por frase y por evento (auriculares, juegos), y su ejecución.

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Los métodos usan
los atributos de la app (``self.root``, ``self.events``, ``self.tool_registry``…).
"""

import jarvis_core as core
import jarvis_displays
import jarvis_power
import jarvis_routines
import json
import logging
import queue
import threading
import time
from jarvis_actions import CancellationToken
from jarvis_agent import PlannedAction
from jarvis_routines_panel import RoutinesPanel
from jarvis_theme import CYAN
from jarvis_tool_registry import (
    ToolCancelledError,
    ToolConfirmationRequired,
    ToolDefinition,
    ToolError,
    ToolRisk,
)


class RutinasMixin:
    def _request_routine_save_confirmation(
        self,
        request: jarvis_routines.RoutineCreationRequest,
        tool: ToolDefinition,
        arguments: dict[str, object],
    ) -> bool:
        if self._closing:
            return False
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        action = PlannedAction(tool=tool, arguments=arguments)
        trigger = jarvis_routines._validate_trigger(request.trigger)
        days = jarvis_routines._parse_days(request.days)
        cuando = jarvis_routines.describir_disparador(trigger, request.time, days)
        if request.once:
            cuando += ", una sola vez"
        goal = (
            f"{request.name} — {cuando} — "
            + self._routine_permission_text(request.steps_json, request.automatic).lower()
        )
        self.events.put(("routine_save_confirmation", goal, (action,), reply))
        try:
            return reply.get(timeout=120)
        except queue.Empty:
            logging.warning("Expiró la confirmación para guardar la rutina %s.", request.name)
            self.events.put(("plan_confirmation_timeout", reply))
            return False

    def _tool_schedule_routine(
        self,
        name: str,
        time: str = "",
        days: str = "1,2,3,4,5,6,7",
        steps_json: str = "",
        trigger: str = "time",
        automatic: bool = False,
        once: bool = False,
    ) -> str:
        jarvis_routines.crear_rutina(
            name,
            time,
            days,
            steps_json,
            self.tool_registry,
            trigger=trigger,
            automatic=automatic,
            once=once,
        )
        disparador = jarvis_routines._validate_trigger(trigger)
        cuando = jarvis_routines.describir_disparador(
            disparador,
            time,
            jarvis_routines._parse_days(days or "1,2,3,4,5,6,7"),
        )
        permiso = self._routine_permission_text(steps_json, automatic)
        if once:
            cuando = f"{cuando}, una sola vez"
        return (
            f"Guardé la rutina «{name}» ({cuando}). {permiso}, "
            "mientras Jarvis esté abierto. Puedes pausarla desde el panel de rutinas."
        )

    @staticmethod
    def _routine_permission_text(steps_json: str, automatic: bool) -> str:
        try:
            pasos = json.loads(steps_json)
        except (TypeError, ValueError):
            pasos = []
        if jarvis_routines.requiere_cuenta_atras(pasos):
            return (
                f"Al llegar la hora avisará con {jarvis_power.CUENTA_ATRAS_RUTINA} s de "
                "cuenta atrás y lo hará si nadie la cancela"
            )
        return "Se ejecutará automáticamente" if automatic else "Pedirá aprobación cada vez que se active"

    _ESTADOS_EVENTO = {
        "auriculares_conectados": "AURICULARES CONECTADOS",
        "auriculares_desconectados": "AURICULARES DESCONECTADOS",
        "juego_abierto": "MODO JUEGO: {detalle}",
        "juego_cerrado": "JUEGO CERRADO: {detalle}",
    }

    def _handle_device_event(self, trigger: str, detalle: str = "") -> None:
        try:
            routines = jarvis_routines.rutinas_para_evento(trigger)
        except Exception:
            logging.exception("No se pudieron cargar las rutinas del evento %s.", trigger)
            return
        for routine in routines:
            self._pending_routines.put(routine)
        if routines and not self.busy:
            texto = self._ESTADOS_EVENTO.get(trigger, trigger.upper())
            self._set_status(texto.format(detalle=detalle.upper()).rstrip(": "), CYAN)

    def _find_phrase_routine(self, texto: str) -> "jarvis_routines.Routine | None":
        try:
            rutina = jarvis_routines.buscar_rutina_por_frase(texto)
            if rutina is None:
                rutina = self._find_undo_routine(texto)
            return rutina
        except Exception:
            logging.exception("No se pudieron buscar los comandos personalizados.")
            return None

    def _find_undo_routine(self, texto: str) -> "jarvis_routines.Routine | None":
        """«Desactiva el modo gaming 2K»: si esa rutina cambió los monitores y hay una
        configuración guardada de antes, se vuelve a ella (comando directo, sin la IA)."""
        resto = jarvis_routines.frase_a_deshacer(texto)
        if resto is None:
            return None
        rutina = jarvis_routines.buscar_rutina_por_frase(resto)
        if rutina is None or not any(p.tool == "configure_display" for p in rutina.steps):
            return None
        if jarvis_displays.base_desde_texto(core.obtener_preferencia(jarvis_displays.PREFERENCIA_BASE)) is None:
            return None
        return jarvis_routines.Routine(
            -rutina.identifier, f"Desactivar {rutina.name}", "", (),
            (jarvis_routines.RoutineStep("restore_display_config", {"routine": rutina.name}),),
            jarvis_routines.DISPARADOR_FRASE,
        )

    def _start_phrase_routine(self, rutina: "jarvis_routines.Routine", command: str) -> None:
        self._start_busy(f"COMANDO: {rutina.name.upper()}")
        threading.Thread(
            target=self._phrase_routine_worker,
            args=(rutina, command, self.conversation_id, self.cancel_token),
            name="JarvisComandoPersonalizado",
            daemon=True,
        ).start()

    def _phrase_routine_worker(
        self,
        rutina: "jarvis_routines.Routine",
        command: str,
        conversation_id: str,
        cancel_token: CancellationToken | None,
    ) -> None:
        """Lo pediste con tu frase: se ejecuta como un comando directo, sin
        aprobar el plan; solo apagar/reiniciar/suspender pide confirmación."""
        started = time.perf_counter()
        resultados: list[str] = []
        lote = self._start_display_batch()
        for paso in rutina.steps:
            nombre = paso.tool
            try:
                nombre = self.tool_registry.get(paso.tool).display_name
                if paso.tool == "power_control" and not self._request_power_confirmation(
                    str(paso.arguments.get("action"))
                ):
                    resultados.append("No apagué, reinicié ni suspendí el equipo.")
                    break
                resultado = self.tool_registry.execute(
                    paso.tool,
                    paso.arguments,
                    confirmed=True,
                    origin="frase",
                    cancel_token=cancel_token,
                )
                resultados.append(str(resultado))
            except ToolCancelledError:
                resultados.append("Cancelé el resto del comando.")
                break
            except Exception as error:
                if not isinstance(error, ToolError):
                    logging.exception("Falló un paso del comando %s.", rutina.name)
                resultados.append(f"«{nombre}» falló: {error}")
                break
        try:
            if (revertido := self._confirm_display_batch(lote, rutina.name)) is not None:
                resultados.append(revertido)
        except Exception:
            logging.exception("No se pudo confirmar o restaurar la configuración de monitores.")
            resultados.append("No pude restaurar la configuración de monitores anterior.")
        respuesta = f"{rutina.name}: " + " ".join(r for r in resultados if r) if resultados else rutina.name
        try:
            core.guardar_turno(conversation_id, command, respuesta)
        except Exception:
            logging.exception("No se pudo guardar el comando personalizado en memoria.")
        audio = None
        if self.audio_enabled:
            try:
                audio = core.generar_audio_tts(respuesta)
            except Exception:
                logging.exception("No se pudo generar el audio del comando personalizado.")
        self.events.put(("answer", respuesta, audio, time.perf_counter() - started))

    def _show_routines_panel(self) -> None:
        """Muestra la sección Rutinas de la ventana principal."""
        self.root.deiconify()
        self.root.lift()
        self._show_page("rutinas")

    def _load_routines_page(self) -> None:
        """La sección Rutinas se crea la primera vez y se refresca al volver."""
        if self._routines_panel is not None and self._routines_panel.is_open():
            self._routines_panel.refresh()
            return
        self._routines_panel = RoutinesPanel(
            self._pages["rutinas"], self.tool_registry, incrustado=True
        )

    def _check_scheduled_routines(self) -> None:
        if self._closing:
            return
        try:
            for routine in jarvis_routines.reclamar_rutinas_vencidas():
                self.events.put(("routine_due", routine))
        except Exception as error:
            logging.exception("No se pudieron comprobar las rutinas programadas.")
            if not self._routine_scheduler_error_reported:
                self.events.put(("routine_scheduler_error", str(error)))
                self._routine_scheduler_error_reported = True
        finally:
            if not self._closing and self.root.winfo_exists():
                # Cada 2 s: lo programado al segundo («dentro de 10 minutos») sale a tiempo.
                self.root.after(2000, self._check_scheduled_routines)

    def _start_next_pending_routine(self) -> None:
        if (
            self._closing
            or self.busy
            or self._routine_active
            or self._pending_plan_reply is not None
        ):
            return
        try:
            routine = self._pending_routines.get_nowait()
        except queue.Empty:
            return
        self._routine_active = True
        self._start_busy(f"RUTINA PROGRAMADA: {routine.name.upper()}")
        threading.Thread(
            target=self._routine_worker,
            args=(routine, self.cancel_token),
            name=f"JarvisRoutine-{routine.identifier}",
            daemon=True,
        ).start()

    def _routine_worker(
        self,
        routine: jarvis_routines.Routine,
        cancel_token: CancellationToken | None = None,
    ) -> None:
        try:
            actions = tuple(
                PlannedAction(
                    tool=self.tool_registry.get(step.tool),
                    arguments=self.tool_registry.validate_arguments(
                        step.tool,
                        step.arguments,
                    ),
                )
                for step in routine.steps
            )
            goal = f"Ejecutar la rutina programada «{routine.name}»"
            automatic = jarvis_routines.puede_ejecutarse_sin_preguntar(
                routine,
                self.tool_registry,
            )
            cuenta_atras = jarvis_routines.requiere_cuenta_atras(routine.steps)
            if cuenta_atras:
                accion = next(
                    str(s.arguments.get("action"))
                    for s in routine.steps
                    if s.tool == "power_control"
                )
                if not self._request_power_countdown(routine.name, accion):
                    self.events.put(
                        ("routine_result", routine, False, "Cancelaste la rutina a tiempo.")
                    )
                    return
                logging.info("Rutina %s: nadie canceló la cuenta atrás.", routine.name)
            elif automatic:
                logging.info("Rutina %s ejecutada sin preguntar (permiso automático).", routine.name)
            elif not self._request_plan_confirmation(goal, actions):
                self.events.put(
                    ("routine_result", routine, False, "No aprobaste esta ejecución.")
                )
                return

            results: list[str] = []
            lote = self._start_display_batch()
            try:
                for action in actions:
                    try:
                        result = self.tool_registry.execute(
                            action.tool.name,
                            action.arguments,
                            # La cuenta atrás ya fue el aviso de la acción de energía.
                            confirmed=action.tool.risk is ToolRisk.MEDIUM
                            or (cuenta_atras and action.tool.name == "power_control"),
                            origin="rutina",
                            cancel_token=cancel_token,
                        )
                    except ToolConfirmationRequired:
                        if not self._request_tool_confirmation(
                            action.tool,
                            action.arguments,
                        ):
                            self.events.put(
                                (
                                    "routine_result",
                                    routine,
                                    False,
                                    "La rutina se detuvo porque rechazaste una acción de riesgo alto.",
                                )
                            )
                            return
                        result = self.tool_registry.execute(
                            action.tool.name,
                            action.arguments,
                            confirmed=True,
                            origin="rutina",
                            cancel_token=cancel_token,
                        )
                    results.append(str(result))
            finally:
                # Una sola confirmación para todos los cambios de monitores de la rutina.
                revertido = self._confirm_display_batch(lote, routine.name)
            if revertido is not None:
                self.events.put(("routine_result", routine, False, "\n".join([*results, revertido])))
                return
        except ToolError as error:
            logging.exception("No se pudo ejecutar la rutina %s.", routine.name)
            self.events.put(("routine_result", routine, False, str(error)))
        except Exception as error:
            logging.exception("Falló la ejecución de la rutina %s.", routine.name)
            self.events.put(
                (
                    "routine_result",
                    routine,
                    False,
                    f"{type(error).__name__}: {error}",
                )
            )
        else:
            self.events.put(
                ("routine_result", routine, True, "\n".join(results))
            )
