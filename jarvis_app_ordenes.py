"""Órdenes: enrutado de lo escrito (``_send_text``) y lo dicho (``_voice_worker``), respuestas de
la IA y acciones directas. Un comando nuevo se enruta en los dos métodos.

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Los métodos usan
los atributos de la app (``self.root``, ``self.events``, ``self.tool_registry``…).
"""

import audio_capture
import jarvis_actions
import jarvis_aprendizaje
import jarvis_buenos_dias
import jarvis_buscar_archivos
import jarvis_comandos
import jarvis_core as core
import jarvis_deshacer
import jarvis_memory
import jarvis_reminders
import jarvis_routines
import jarvis_screen_actions
import jarvis_system
import jarvis_voz_stream
import logging
import queue
import threading
import time
import tkinter as tk
from jarvis_actions import (
    CancellationToken,
    EstadoAccion,
    OperacionCancelada,
    ResultadoAccion,
    TiempoAgotado,
    ejecutar_con_limites,
)
from jarvis_agent import procesar_con_herramientas
from jarvis_commands import (
    detectar_accion_ventana,
    detectar_borrado_historial,
    detectar_busqueda_youtube,
    detectar_comando_audio,
    detectar_comando_compacto,
    detectar_comando_dispositivo_salida,
    detectar_comando_energia,
    detectar_comando_escucha,
    detectar_comando_gpu,
    detectar_comando_inicio_windows,
    detectar_comando_juego,
    detectar_comando_limpieza,
    detectar_comando_monitores,
    detectar_comando_multimedia,
    detectar_comando_navegacion_web,
    detectar_comando_nueva_pestana,
    detectar_comando_tema,
    detectar_comando_ventanas,
    detectar_comando_volumen,
    detectar_comando_voz,
    detectar_consulta_bateria,
    detectar_consulta_fecha_hora,
    detectar_consulta_sistema,
    detectar_reproduccion_ordinal_youtube,
    detectar_vision,
    limpiar_transcripcion_voz,
)
from jarvis_plugins import GestorPlugins
from jarvis_system import cambiar_estado_ventana_brave, encontrar_brave, localizar_ejecutable_juego
from jarvis_theme import CYAN, ERROR, MUTED
from jarvis_tool_registry import ToolCancelledError, ToolError
from jarvis_app_confirmaciones import ConfirmacionesMixin


class OrdenesMixin:
    def _send_text(self) -> None:
        if self.busy or self.recorder is not None:
            return
        text = self.input.get("1.0", "end-1c").strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self._add_message("user", text)
        if detectar_borrado_historial(text):
            self._confirm_clear_history()
            return
        rutina = self._find_phrase_routine(text)
        if rutina is not None:
            self._start_phrase_routine(rutina, text)
            return
        pedido_pantalla = jarvis_screen_actions.detectar_comando(text)
        if pedido_pantalla is not None:
            self._start_screen_action(pedido_pantalla, text)
            return
        vision = detectar_vision(text)
        if vision is not None:
            self._start_vision_capture(*vision, text)
            return
        audio_enabled = detectar_comando_audio(text)
        if audio_enabled is not None:
            self._execute_audio_toggle(audio_enabled, text)
            return
        escucha = detectar_comando_escucha(text)
        if escucha is not None:
            self._execute_wakeword_toggle(escucha, text)
            return
        inicio_windows = detectar_comando_inicio_windows(text)
        if inicio_windows is not None:
            self._execute_autostart_toggle(inicio_windows, text)
            return
        dispositivo = detectar_comando_dispositivo_salida(text)
        if dispositivo is not None:
            self._execute_output_device_action(dispositivo, text)
            return
        porcentaje = detectar_comando_volumen(text)
        if porcentaje is not None:
            self._execute_volume_action(porcentaje, text)
            return
        limpieza = detectar_comando_limpieza(text)
        if limpieza is not None:
            self._start_cleanup(text, limpieza["solo"])
            return
        system_query = (
            jarvis_buenos_dias.detectar(text)
            or jarvis_deshacer.detectar(text)
            or self._file_choice(text)
            or jarvis_buscar_archivos.detectar(text)
            or jarvis_comandos.detectar_ayuda(text)
            or detectar_consulta_fecha_hora(text)
            or jarvis_reminders.detectar_comando(text)
            or detectar_consulta_bateria(text)
            or detectar_comando_multimedia(text)
            or detectar_consulta_sistema(text)
            or detectar_comando_monitores(text)
            or detectar_comando_tema(text)
            or detectar_comando_compacto(text)
            or detectar_comando_voz(text)
            or detectar_comando_gpu(text)
            or detectar_comando_energia(text)
            or detectar_comando_ventanas(text)
            or self.plugins.detectar_comando(text)
        )
        if system_query is not None:
            self._start_direct_tool_action(*system_query, text)
            return
        youtube_index = detectar_reproduccion_ordinal_youtube(text)
        if youtube_index is not None:
            if self._last_youtube_query is None:
                self._add_message(
                    "assistant",
                    "Primero busca una canción en YouTube y luego dime qué resultado quieres reproducir.",
                )
                return
            self._start_youtube_search(
                self._last_youtube_query,
                youtube_index,
                text,
                self.conversation_id,
            )
            return
        search_action = detectar_busqueda_youtube(text)
        if search_action is not None:
            query, play_index = search_action
            self._start_youtube_search(query, play_index, text, self.conversation_id)
            return
        if detectar_comando_nueva_pestana(text):
            self._start_browser_action("new_tab", "", text)
            return
        destination = detectar_comando_navegacion_web(text)
        if destination is not None:
            self._start_browser_action("navigate", destination, text)
            return
        action = detectar_accion_ventana(text)
        if action is not None:
            self._execute_window_action(action, text)
            return
        game_name = detectar_comando_juego(text)
        if game_name is not None:
            self._execute_game_action(game_name, text)
            return
        self._start_busy("PROCESANDO CONSULTA…")
        started = time.perf_counter()
        threading.Thread(
            target=self._answer_worker,
            args=(text, "chat", started, self.conversation_id, self.cancel_token),
            daemon=True,
        ).start()

    def _voice_worker(
        self,
        audio_path: str,
        started: float,
        conversation_id: str,
        plan_approval: bool = False,
        approval_kind: str = "plan",
        cancel_token: CancellationToken | None = None,
    ) -> None:
        try:
            transcript = limpiar_transcripcion_voz(
                ejecutar_con_limites(
                    lambda: core.procesar_stt(audio_path),
                    timeout=core.STT_TIMEOUT + 10,
                    descripcion="transcripción de voz",
                    cancel_token=cancel_token,
                )
            )
            transcript = jarvis_memory.normalizar_transcripcion_memoria_voz(transcript)
            if plan_approval:
                if transcript:
                    decision = ConfirmacionesMixin._approval_decision(self, approval_kind, transcript)
                    logging.info(
                        "Decisión de confirmación %s por voz: %r (%s caracteres reconocidos).",
                        approval_kind,
                        decision,
                        len(transcript),
                    )
                    self.events.put(("plan_voice_decision", decision, transcript))
                else:
                    self.events.put(("plan_voice_unrecognized",))
                return
            if not transcript:
                self.events.put(("empty_transcript",))
                return
            self.events.put(("transcript", transcript))
            if detectar_borrado_historial(transcript):
                self.events.put(("clear_history_request",))
                return
            rutina = self._find_phrase_routine(transcript)
            if rutina is not None:
                self.events.put(("phrase_routine", rutina, transcript))
                return
            pedido_pantalla = jarvis_screen_actions.detectar_comando(transcript)
            if pedido_pantalla is not None:
                self.events.put(("screen_action", pedido_pantalla, transcript))
                return
            vision = detectar_vision(transcript)
            if vision is not None:
                self.events.put(("vision_request", *vision, transcript))
                return
            audio_enabled = detectar_comando_audio(transcript)
            if audio_enabled is not None:
                self.events.put(("audio_toggle", audio_enabled, transcript))
                return
            escucha = detectar_comando_escucha(transcript)
            if escucha is not None:
                self.events.put(("wakeword_toggle", escucha, transcript))
                return
            inicio_windows = detectar_comando_inicio_windows(transcript)
            if inicio_windows is not None:
                self.events.put(("autostart_toggle", inicio_windows, transcript, True))
                return
            dispositivo = detectar_comando_dispositivo_salida(transcript)
            if dispositivo is not None:
                self.events.put(("output_device_action", dispositivo, transcript))
                return
            porcentaje = detectar_comando_volumen(transcript)
            if porcentaje is not None:
                self.events.put(("volume_action", porcentaje, transcript))
                return
            limpieza = detectar_comando_limpieza(transcript)
            if limpieza is not None:
                self.events.put(("cleanup_start", transcript, limpieza["solo"]))
                return
            system_query = (
                jarvis_buenos_dias.detectar(transcript)
                or jarvis_deshacer.detectar(transcript)
                or self._file_choice(transcript)
                or jarvis_buscar_archivos.detectar(transcript)
                or jarvis_comandos.detectar_ayuda(transcript)
                or detectar_consulta_fecha_hora(transcript)
                or jarvis_reminders.detectar_comando(transcript)
                or detectar_consulta_bateria(transcript)
                or detectar_comando_multimedia(transcript)
                or detectar_consulta_sistema(transcript)
                or detectar_comando_monitores(transcript)
                or detectar_comando_tema(transcript)
                or detectar_comando_compacto(transcript)
                or detectar_comando_voz(transcript)
                or detectar_comando_gpu(transcript)
                or detectar_comando_energia(transcript)
                or detectar_comando_ventanas(transcript)
            )
            plugins = getattr(self, "plugins", None)
            if system_query is None and isinstance(plugins, GestorPlugins):
                system_query = plugins.detectar_comando(transcript)
            if system_query is not None:
                self.events.put(("system_query", *system_query, transcript))
                return
            youtube_index = detectar_reproduccion_ordinal_youtube(transcript)
            if youtube_index is not None:
                self.events.put(("youtube_play_result", youtube_index, transcript))
                return
            search_action = detectar_busqueda_youtube(transcript)
            if search_action is not None:
                query, play_index = search_action
                self.events.put(("youtube_search", query, play_index, transcript))
                return
            if detectar_comando_nueva_pestana(transcript):
                self.events.put(("browser_action", "new_tab", "", transcript))
                return
            destination = detectar_comando_navegacion_web(transcript)
            if destination is not None:
                self.events.put(
                    ("browser_action", "navigate", destination, transcript)
                )
                return
            action = detectar_accion_ventana(transcript)
            if action is not None:
                self.events.put(("window_action", action, transcript))
                return
            game_name = detectar_comando_juego(transcript)
            if game_name is not None:
                self.events.put(("game_action", game_name, transcript))
                return
            answer, audio = self._process_agent_query(
                transcript,
                "voz",
                started,
                conversation_id,
                cancel_token,
            )
            self.events.put(("answer", answer, audio, time.perf_counter() - started))
        except OperacionCancelada:
            self.events.put(("cancelled", "Cancelé el procesamiento de la voz."))
        except TiempoAgotado:
            logging.warning("La transcripción de voz superó el tiempo máximo.")
            self.events.put(
                (
                    "plan_voice_error" if plan_approval else "error",
                    f"La transcripción tardó más de {core.STT_TIMEOUT} s. "
                    "Revisa que Docker esté funcionando e inténtalo de nuevo.",
                )
            )
        except Exception as error:
            logging.exception("Falló el procesamiento de voz.")
            if plan_approval:
                self.events.put(("plan_voice_error", str(error)))
            else:
                self.events.put(("error", f"Error al procesar el audio: {error}"))
        finally:
            audio_capture.limpiar_audio(audio_path)

    def _answer_worker(
        self,
        text: str,
        kind: str,
        started: float,
        conversation_id: str,
        cancel_token: CancellationToken | None = None,
    ) -> None:
        try:
            answer, audio = self._process_agent_query(
                text,
                kind,
                started,
                conversation_id,
                cancel_token,
            )
            self.events.put(("answer", answer, audio, time.perf_counter() - started))
        except Exception as error:
            logging.exception("Falló el procesamiento de la consulta.")
            self.events.put(("error", f"Error al procesar la consulta: {error}"))

    def _process_agent_query(
        self,
        text: str,
        kind: str,
        started: float,
        conversation_id: str,
        cancel_token: CancellationToken | None = None,
    ) -> tuple[str | None, str | None]:
        core.REQUEST_COUNTER.labels(tipo=kind).inc()
        hablador: jarvis_voz_stream.HabladorEnVivo | None = None
        try:
            answer = jarvis_memory.procesar_comando(text)
            if answer is None:
                answer = jarvis_aprendizaje.procesar_comando(text)
            if answer is None:
                answer = jarvis_actions.procesar_comando(text)
            if answer is None:
                answer = jarvis_routines.procesar_comando(text)
            plugins = getattr(self, "plugins", None)
            if answer is None and isinstance(plugins, GestorPlugins):
                try:
                    answer = plugins.procesar_comando(text)
                except (ValueError, OSError) as error:
                    answer = f"No pude completar la configuración: {error}"
            if answer is None:
                try:
                    routine_request = jarvis_routines.parsear_solicitud_creacion(
                        text,
                        self.tool_registry,
                    )
                except ValueError as error:
                    answer = str(error)
                else:
                    if routine_request is not None:
                        arguments = self.tool_registry.validate_arguments(
                            "schedule_routine",
                            routine_request.tool_arguments(),
                        )
                        definition = self.tool_registry.get("schedule_routine")
                        if self._request_routine_save_confirmation(
                            routine_request,
                            definition,
                            arguments,
                        ):
                            answer = self.tool_registry.execute(
                                definition.name,
                                arguments,
                                confirmed=True,
                                origin="comando",
                                cancel_token=cancel_token,
                            )
                        else:
                            answer = (
                                f"No guardé la rutina «{routine_request.name}»."
                            )
                    else:
                        hablador = self._new_live_speaker()
                        self._live_speaker = hablador  # para poder interrumpirlo con «Hey Jarvis»
                        answer = procesar_con_herramientas(
                            text,
                            conversation_id,
                            self.tool_registry,
                            self._request_tool_confirmation,
                            approve_plan=self._request_plan_confirmation,
                            plan_scope=self._plan_display_scope,
                            cancel_token=cancel_token,
                            on_text=self._live_text_callback(hablador),
                        )
            core.guardar_turno(conversation_id, text, answer)
        except Exception:
            core.LATENCY_HIST.labels(estado="error").observe(
                time.perf_counter() - started
            )
            raise

        audio_path = None
        if hablador is not None and hablador.empezo:
            # La respuesta ya se está diciendo en vivo: terminar y unir el audio.
            response_id = core.guardar_respuesta(answer)
            audio_path = hablador.terminar(
                answer,
                str(core.RESPONSES_DIR / f"{response_id}.wav"),
                cancelar_si=(lambda: cancel_token.cancelled) if cancel_token is not None else None,
            )
            if audio_path:
                self._spoken_audio.add(audio_path)
            if hablador.primera_voz is not None:
                logging.info("⏱️ Primera voz a los %.2fs.", hablador.primera_voz - started)
            core.registrar_latencia("total", time.perf_counter() - started)
            return answer, audio_path
        if hablador is not None:
            hablador.cancelar()
        try:
            response_id = core.guardar_respuesta(answer)
            audio_path = ejecutar_con_limites(
                lambda: core.generar_audio_tts(answer, response_id),
                timeout=core.TTS_TIMEOUT + 5,
                descripcion="texto a voz",
                cancel_token=cancel_token,
            )
        except (OperacionCancelada, TiempoAgotado) as error:
            logging.warning("Se omitió el audio de la respuesta: %s", error)
            core.LATENCY_HIST.labels(estado="error").observe(time.perf_counter() - started)
            core.registrar_latencia("total", time.perf_counter() - started, ok=False)
        except (OSError, RuntimeError, ValueError):
            core.LATENCY_HIST.labels(estado="error").observe(
                time.perf_counter() - started
            )
            core.registrar_latencia("total", time.perf_counter() - started, ok=False)
            logging.exception("No se pudo generar el audio de la respuesta del agente.")
        else:
            core.LATENCY_HIST.labels(estado="success").observe(
                time.perf_counter() - started
            )
            core.registrar_latencia("total", time.perf_counter() - started)
            try:
                core.limpiar_respuestas()
            except Exception:
                logging.exception("No se pudieron limpiar las respuestas antiguas.")
        return answer, audio_path

    def _record_action(self, resultado: ResultadoAccion) -> None:
        """Recibe cada ejecución de herramienta (se llama desde hilos de trabajo)."""
        self.events.put(("action_result", resultado))
        try:
            jarvis_actions.registrar_accion(resultado)
        except Exception:
            logging.exception("No se pudo guardar el registro de la acción.")

    def _cancel_current_operation(self, _event=None) -> str | None:
        if not self.busy:
            return None
        self.cancel_token.cancel()
        self.cancel_button.configure(state="disabled")
        self._set_status("CANCELANDO…", MUTED)
        if self._pending_plan_reply is not None:
            self._resolve_plan_approval(False)
        return "break"

    def _request_tool_ui_action(self, action: str, value: object = None) -> str:
        if self._closing:
            raise RuntimeError("Jarvis se está cerrando; se canceló la acción.")
        reply: queue.Queue[tuple[bool, str]] = queue.Queue(maxsize=1)
        self.events.put(("tool_ui_request", action, value, reply))
        try:
            succeeded, result = reply.get(timeout=15)
        except queue.Empty as error:
            raise TimeoutError("La interfaz no respondió a la acción solicitada.") from error
        if not succeeded:
            raise RuntimeError(result)
        return result

    def _show_direct_response(self, herramienta: str, texto: str) -> None:
        """La respuesta de un comando directo va dentro de su tarjeta de acción
        (si se dibujó); si no, como mensaje normal."""
        tarjeta = self._last_action_cards.pop(herramienta, None)
        if tarjeta is not None and tarjeta.winfo_exists():
            tarjeta.jarvis_poner_cuerpo(texto)
        else:
            self._add_message("assistant", texto)

    def _show_action_result(self, resultado: ResultadoAccion) -> None:
        etiqueta = resultado.etiqueta.upper()
        if resultado.exitosa:
            detalle = f"{etiqueta}  ·  {resultado.duracion:.1f} S"
            if resultado.verificacion is not None and resultado.verificacion.ok:
                detalle += "  ·  VERIFICADA"
            self._set_status(detalle, CYAN)
            return
        estados = {
            EstadoAccion.ERROR: "FALLÓ",
            EstadoAccion.TIEMPO_AGOTADO: "TIEMPO AGOTADO",
            EstadoAccion.CANCELADA: "CANCELADA",
            EstadoAccion.NO_VERIFICADA: "NO VERIFICADA",
        }
        self._set_status(
            f"{etiqueta}: {estados.get(resultado.estado, resultado.estado.value)}",
            MUTED if resultado.estado is EstadoAccion.CANCELADA else ERROR,
        )

    _DIRECT_SPOKEN_TOOLS = frozenset(
        {
            "get_current_datetime",
            "morning_briefing",
            "show_commands_help",
            "undo_last_action",
            "get_headset_battery",
            "get_now_playing",
            "set_reminder",
            "list_reminders",
            "cancel_reminder",
        }
    )

    _DIRECT_ACTION_BUSY = {
        "set_system_volume": "AJUSTANDO VOLUMEN…",
        "morning_briefing": "PREPARANDO EL RESUMEN DEL DÍA…",
        "find_files": "BUSCANDO ARCHIVOS…",
        "undo_last_action": "DESHACIENDO…",
        "set_output_device": "CAMBIANDO SALIDA DE AUDIO…",
        "launch_game": "BUSCANDO EL JUEGO…",
        "open_new_browser_tab": "ABRIENDO PESTAÑA EN BRAVE…",
        "navigate_browser": "ABRIENDO PÁGINA EN BRAVE…",
        "search_youtube": "ABRIENDO YOUTUBE…",
        "list_open_applications": "REVISANDO APLICACIONES ABIERTAS…",
        "list_top_processes": "MIDIENDO PROCESOS…",
        "get_system_status": "REVISANDO EL EQUIPO…",
        "configure_display": "CAMBIANDO LA CONFIGURACIÓN DE MONITORES…",
        "restore_display_config": "VOLVIENDO A LA CONFIGURACIÓN DE MONITORES…",
        "fix_display_scaling": "REAJUSTANDO EL ESCALADO…",
        "set_theme": "CAMBIANDO EL TEMA…",
        "set_compact_mode": "CAMBIANDO EL MODO DE VENTANA…",
        "set_voice": "CAMBIANDO LA VOZ…",
        "apply_gpu_profile": "APLICANDO EL UNDERVOLT…",
        "get_gpu_profile_status": "REVISANDO LA GPU…",
        "video_fullscreen": "CAMBIANDO LA PANTALLA COMPLETA…",
        "video_theater": "CAMBIANDO EL MODO TEATRO…",
        "clean_disk": "LIBERANDO ESPACIO…",
        "move_window": "MOVIENDO VENTANAS…",
        "power_control": "ESPERANDO CONFIRMACIÓN…",
        "cancel_power_action": "CANCELANDO EL APAGADO…",
        "lock_computer": "BLOQUEANDO EL EQUIPO…",
        "media_control": "CONTROLANDO LA REPRODUCCIÓN…",
        "mute_browser_tab": "SILENCIANDO LA PESTAÑA…",
        "media_sequence": "CONTROLANDO LA REPRODUCCIÓN…",
        "set_app_volume": "AJUSTANDO VOLUMEN DE LA APLICACIÓN…",
    }

    def _execute_volume_action(self, porcentaje: int, command: str) -> None:
        self._start_direct_tool_action(
            "set_system_volume", {"percentage": porcentaje}, command
        )

    def _execute_output_device_action(self, dispositivo: str, command: str) -> None:
        self._start_direct_tool_action(
            "set_output_device", {"device": dispositivo}, command
        )

    def _execute_game_action(self, game_name: str, command: str) -> None:
        """«abre X»: primero los juegos; si no es un juego, la app instalada (WhatsApp…).

        Antes solo buscaba juegos y «abre WhatsApp» fallaba aunque esté instalada.
        Se decide en otro hilo: buscar juegos y leer el menú Inicio toma un momento.
        """

        def decidir() -> None:
            try:
                localizar_ejecutable_juego(game_name)
                herramienta, argumentos = "launch_game", {"name": game_name}
            except Exception:
                es_app = jarvis_system.buscar_app(game_name, jarvis_system.apps_instaladas()) is not None
                herramienta, argumentos = ("open_app", {"name": game_name}) if es_app else (
                    "launch_game", {"name": game_name})
            self.events.put(("system_query", herramienta, argumentos, command))

        threading.Thread(target=decidir, name="JarvisAbrir", daemon=True).start()

    def _execute_window_action(self, action: str, command: str) -> None:
        respuestas = {
            "brave_minimize": "Minimizando la ventana de Brave.",
            "brave_maximize": "Maximizando la ventana de Brave.",
            "minimize": "Minimizando la aplicación.",
            "maximize": "Maximizando la ventana.",
            "close": "Cerrando Jarvis.",
        }
        respuesta = respuestas[action]
        if action in {"brave_minimize", "brave_maximize"}:
            brave = encontrar_brave()
            if brave is None:
                self._set_status("BRAVE NO ENCONTRADO", ERROR)
                self._add_message(
                    "assistant",
                    "No encontré Brave instalado para cambiar el estado de su ventana.",
                )
                return
            estado = "minimize" if action == "brave_minimize" else "maximize"
            try:
                if not cambiar_estado_ventana_brave(estado, brave):
                    self._set_status("BRAVE NO ESTÁ ABIERTO", ERROR)
                    self._add_message(
                        "assistant",
                        "No encontré una ventana abierta de Brave para realizar esa acción.",
                    )
                    return
            except OSError as error:
                logging.exception("No se pudo cambiar el estado de la ventana de Brave.")
                self._set_status("ERROR DE VENTANA", ERROR)
                self._add_message(
                    "assistant",
                    f"No pude cambiar el estado de la ventana de Brave: {error}",
                )
                return

        try:
            core.guardar_turno(self.conversation_id, command, respuesta)
        except Exception as error:
            logging.exception("No se pudo guardar la acción de ventana.")
            self._set_status("ERROR DE MEMORIA", ERROR)
            self._add_message(
                "assistant",
                f"No pude guardar la acción en la memoria, así que no la ejecuté: {error}",
            )
            return

        self._add_message("assistant", respuesta)
        try:
            if action == "minimize":
                self._hide_to_tray()
                self._set_status("APLICACIÓN MINIMIZADA", CYAN)
            elif action == "maximize":
                self.root.state("zoomed")
                self._set_status("VENTANA MAXIMIZADA", CYAN)
            elif action == "brave_minimize":
                self._set_status("BRAVE MINIMIZADO", CYAN)
            elif action == "brave_maximize":
                self._set_status("BRAVE MAXIMIZADO", CYAN)
            else:
                self.root.after(250, self._close)
        except tk.TclError as error:
            logging.exception("No se pudo ejecutar la acción de ventana.")
            self._set_status("ERROR DE VENTANA", ERROR)
            self._add_message("assistant", f"No pude realizar la acción: {error}")

    def _start_youtube_search(
        self,
        query: str,
        play_index: int | None,
        command: str,
        conversation_id: str,
    ) -> None:
        self._last_youtube_query = query
        arguments: dict[str, object] = {"query": query}
        if play_index is not None:
            arguments["play_index"] = play_index
        self._start_direct_tool_action(
            "search_youtube",
            arguments,
            command,
            conversation_id=conversation_id,
            busy_message=(
                f"BUSCANDO EL RESULTADO {play_index}…"
                if play_index is not None
                else None
            ),
        )

    def _start_browser_action(self, action: str, value: str, command: str) -> None:
        if action == "new_tab":
            self._start_direct_tool_action("open_new_browser_tab", {}, command)
        elif action == "navigate":
            self._start_direct_tool_action("navigate_browser", {"url": value}, command)
        else:
            self._add_message("assistant", f"Acción de Brave desconocida: {action}")

    def _start_direct_tool_action(
        self,
        tool_name: str,
        arguments: dict[str, object],
        command: str,
        *,
        conversation_id: str | None = None,
        busy_message: str | None = None,
    ) -> None:
        """Ejecuta un comando directo (sin LLM) con el ejecutor supervisado.

        Mantiene el comportamiento previo: el usuario lo pidió explícitamente,
        así que se ejecuta con ``confirmed=True``.
        """
        self._last_action_cards.pop(tool_name, None)  # su respuesta irá a la tarjeta nueva
        self._start_busy(
            busy_message or self._DIRECT_ACTION_BUSY.get(tool_name, "EJECUTANDO ACCIÓN…")
        )
        threading.Thread(
            target=self._direct_tool_worker,
            args=(
                tool_name,
                arguments,
                command,
                conversation_id or self.conversation_id,
                self.cancel_token,
            ),
            name=f"JarvisDirecto-{tool_name}",
            daemon=True,
        ).start()

    def _direct_tool_worker(
        self,
        tool_name: str,
        arguments: dict[str, object],
        command: str,
        conversation_id: str,
        cancel_token: CancellationToken | None,
    ) -> None:
        started = time.perf_counter()
        try:
            label = self.tool_registry.get(tool_name).display_name
        except ToolError:
            label = tool_name
        if tool_name == "power_control" and not self._request_power_confirmation(
            str(arguments.get("action"))
        ):
            # Riesgo alto: aunque sea un comando directo, siempre se confirma.
            self.events.put(("direct_action_error", tool_name, "Cancelé la acción; el equipo sigue igual."))
            return
        try:
            result = self.tool_registry.execute(
                tool_name,
                arguments,
                confirmed=True,
                origin="directo",
                cancel_token=cancel_token,
            )
        except ToolCancelledError:
            self.events.put(("direct_action_error", tool_name, "Cancelé la acción."))
            return
        except Exception as error:
            if not isinstance(error, ToolError):
                logging.exception("Falló el comando directo %s.", tool_name)
            self.events.put(
                (
                    "direct_action_error",
                    tool_name,
                    f"No pude completar «{label}»: {error}",
                )
            )
            return

        response = str(result)
        try:
            core.guardar_turno(conversation_id, command, response)
        except Exception as error:
            logging.exception("La acción se ejecutó, pero no se pudo guardar en memoria.")
            response += f" No pude guardar la acción en la memoria: {error}"
        if tool_name in OrdenesMixin._DIRECT_SPOKEN_TOOLS:
            # Respuestas cortas que se esperan habladas («¿qué hora es?»).
            try:
                audio = core.generar_audio_tts(response)
            except Exception:
                logging.exception("No se pudo generar el audio de %s.", tool_name)
                audio = None
            self.events.put(("answer", response, audio, time.perf_counter() - started))
            return
        self.events.put(("direct_action_done", tool_name, response))

    def _new_conversation(self) -> None:
        if self.busy or self.recorder is not None:
            return
        try:
            self.conversation_id = core.crear_conversacion()
        except Exception as error:
            logging.exception("No se pudo crear una conversación nueva.")
            self._set_status("ERROR DE MEMORIA", ERROR)
            self._add_message("assistant", f"No pude iniciar otra conversación: {error}")
            return

        for child in self.messages.winfo_children():
            child.destroy()
        self._add_message(
            "assistant",
            "Conversación nueva. El contexto anterior se conserva en la memoria, pero no se mezclará con este hilo.",
        )
        self._set_status("NUEVA CONVERSACIÓN", CYAN)
