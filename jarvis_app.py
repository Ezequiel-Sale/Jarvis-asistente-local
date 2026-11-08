import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from urllib.parse import quote_plus

import jarvis_actions
import jarvis_core as core
import jarvis_health
import jarvis_displays
import jarvis_ollama_gpu
import jarvis_power
import jarvis_aprendizaje
import jarvis_autostart
import jarvis_reminders
import jarvis_screen_actions
import jarvis_voz_stream
import jarvis_wakeword
import jarvis_vision
import jarvis_memory
import jarvis_monitor_view
import jarvis_floating
import jarvis_routines
import jarvis_windows
import jarvis_theme
from jarvis_actions import (
    CancellationToken,
    EstadoAccion,
    OperacionCancelada,
    ResultadoAccion,
    TiempoAgotado,
    ejecutar_con_limites,
)
from audio_capture import GrabadorAudio, limpiar_audio
import pystray
from PIL import Image, ImageTk

from jarvis_agent import PlannedAction, procesar_con_herramientas
from jarvis_commands import (
    detectar_accion_ventana,
    detectar_busqueda_youtube,
    detectar_comando_audio,
    detectar_comando_dispositivo_salida,
    detectar_comando_juego,
    detectar_comando_navegacion_web,
    detectar_comando_nueva_pestana,
    detectar_comando_volumen,
    detectar_comando_energia,
    detectar_comando_multimedia,
    detectar_consulta_bateria,
    detectar_consulta_fecha_hora,
    detectar_comando_escucha,
    detectar_comando_inicio_windows,
    detectar_decision_energia,
    detectar_decision_monitores,
    detectar_comando_monitores,
    detectar_comando_tema,
    detectar_comando_compacto,
    detectar_consulta_sistema,
    detectar_borrado_historial,
    detectar_decision_vision,
    detectar_vision,
    detectar_decision_aprobacion_plan,
    detectar_decision_guardar_rutina,
    detectar_reproduccion_ordinal_youtube,
    limpiar_transcripcion_voz,
    normalizar_url_web,
    obtener_primer_video_youtube,
)
from jarvis_events import HeadphonesWatcher, HeadsetBatteryWatcher
from jarvis_integraciones import cargar_integraciones
from jarvis_mcp import GestorMCP, HerramientaMCP
from jarvis_plugins import GestorPlugins
from jarvis_hotkeys import GlobalHotkeyListener
from jarvis_routines_panel import RoutinesPanel
from jarvis_logging import capturar_errores_tk, configurar_registro
from jarvis_single_instance import SingleInstance
from jarvis_audio_devices import cambiar_dispositivo_salida, cambiar_volumen_sistema
from jarvis_system import (
    abrir_nueva_pestana_brave,
    cambiar_estado_ventana_brave,
    encontrar_brave,
    iniciar_ejecutable_juego,
    localizar_ejecutable_juego,
    navegar_en_ventana_brave,
)
from jarvis_theme import (
    BG, CYAN, ERROR, HOVER, MUTED, PANEL, PANEL_LIGHT, TEXT, VIOLET,
)
from jarvis_ui import JarvisUIMixin
from jarvis_player import AudioPlayerMixin
from jarvis_tool_registry import (
    ToolCancelledError,
    ToolConfirmationRequired,
    ToolDefinition,
    ToolError,
    ToolRisk,
)
from jarvis_tools import crear_registro_herramientas
from jarvis_verifiers import crear_verificadores


def _numeracion_fija_monitores() -> dict[int, int]:
    """{DISPLAYN: número fijo} para que visión, clic y vídeo numeren como los monitores."""
    mapa = jarvis_displays.cargar_mapa(core.obtener_preferencia)
    equivalencias, nuevo = jarvis_displays.numeros_por_gdi(mapa)
    if nuevo != mapa:
        jarvis_displays.guardar_mapa(core.guardar_preferencia, nuevo)
    return equivalencias


class JarvisApp(JarvisUIMixin, AudioPlayerMixin):
    def __init__(self, root: tk.Tk, icon_path: Path) -> None:
        self.root = root
        self.root.title("J.A.R.V.I.S. | Asistente local")
        self.root.geometry("1020x760")
        self.root.minsize(760, 580)
        self.root.configure(bg=BG)

        self.events: queue.Queue[tuple] = queue.Queue()
        self._pending_routines: queue.Queue[jarvis_routines.Routine] = queue.Queue()
        self._routine_active = False
        self._routine_scheduler_error_reported = False
        self.recorder: GrabadorAudio | None = None
        self._recording_for_plan_approval = False
        self._pending_plan_reply: queue.Queue[bool] | None = None
        self._pending_approval_kind: str | None = None
        self._pending_power_action: str | None = None
        self._free_approval_busy = False  # transcribiendo una respuesta sin «Hey Jarvis»
        self._spoken_audio: set[str] = set()  # respuestas que ya se dijeron en vivo
        self._live_row = None
        self._live_label = None
        self._live_text = ""
        self._plan_dialog: tk.Toplevel | None = None
        self._last_action_cards: dict[str, tk.Frame] = {}
        self._plan_voice_button: tk.Button | None = None
        self._plan_approve_button: tk.Button | None = None
        self._plan_reject_button: tk.Button | None = None
        self.busy = False
        self.cancel_token = CancellationToken()
        self.player_thread: threading.Thread | None = None
        self.player_stop: threading.Event | None = None
        self.player_resume: threading.Event | None = None
        self.player_token = 0
        self.player_path: str | None = None
        self.player_duration = 0.0
        self.player_paused = False
        self.audio_enabled = True
        self._chat_following = True
        self._orb_mode = "idle"
        self._orb_phase = 0.0
        self._orb_pulse_started = 0.0
        self._last_youtube_query: str | None = None
        self._closing = False
        self._closing_finished = False
        self._docker_start_finished = threading.Event()
        with Image.open(icon_path) as icon_image:
            tray_image = icon_image.convert("RGBA")
        self.tray_icon = pystray.Icon(
            "jarvis",
            tray_image,
            "J.A.R.V.I.S. | Asistente local",
            menu=pystray.Menu(
                pystray.MenuItem("Abrir Jarvis", self._request_tray_show, default=True),
                pystray.MenuItem(
                    "Iniciar con Windows",
                    self._request_tray_autostart,
                    checked=lambda _item: self._autostart_wanted,
                ),
                pystray.MenuItem("Modo compacto", self._request_tray_compact),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir de Jarvis", self._request_tray_exit),
            ),
        )
        core.inicializar_base_datos()
        self._autostart_wanted = self._autostart_preferido()
        try:
            jarvis_autostart.sincronizar(self._autostart_wanted)
        except OSError:
            logging.exception("No se pudo actualizar el inicio con Windows.")
        self.conversation_id = core.obtener_conversacion_actual()
        historial = core.obtener_historial(self.conversation_id)

        try:
            jarvis_theme.activar(jarvis_theme.nombre_valido(core.obtener_preferencia(jarvis_theme.PREFERENCIA)))
        except Exception:
            logging.exception("No se pudo cargar el tema guardado.")
        self.root.configure(bg=BG)
        try:
            self._animations = core.obtener_preferencia(self._PREFERENCIA_ANIMACIONES) != "0"
        except Exception:
            self._animations = True
        try:
            self._floating_enabled = core.obtener_preferencia(self._PREFERENCIA_AVISO) != "0"
        except Exception:
            self._floating_enabled = True
        self._configure_styles()
        self._build_layout()
        self._refresh_animations_button()
        self.floating = jarvis_floating.AvisoFlotante(self.root, self._show_from_tray)
        self.compact_window = None
        self._refresh_floating_button()
        self.tool_registry = crear_registro_herramientas(
            self,
            recorder=self._record_action,
            verificadores=crear_verificadores(self),
        )
        self.integraciones = cargar_integraciones()
        self.plugins = GestorPlugins()
        self.plugins.cargar(self.integraciones.plugins, self.tool_registry)
        self.mcp = GestorMCP(self.integraciones.servidores_mcp, core.APP_DIR / "logs")
        jarvis_health.registrar_proveedor(self._integration_checks)
        if historial:
            for role, content in historial:
                self._add_message(role, content)
        else:
            self._add_message(
                "assistant",
                "Sistema en línea. Escribe una consulta o usa el botón de micrófono para hablar.",
            )
        self.root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.root.bind("<Unmap>", self._on_window_unmapped, add="+")
        try:
            self.tray_icon.run_detached()
        except Exception as error:
            logging.exception("No se pudo iniciar el icono de bandeja.")
            self.events.put(("tray_error", str(error)))
        self.global_hotkey_listener = GlobalHotkeyListener(self.events)
        self.global_hotkey_listener.start()
        self.headphones_watcher = HeadphonesWatcher(self.events)
        self.headphones_watcher.start()
        self.battery_watcher = HeadsetBatteryWatcher(self.events)
        self.battery_watcher.start()
        self.reminder_watcher = jarvis_reminders.VigilanteRecordatorios(self.events)
        self.reminder_watcher.start()
        self._barra_auto = self._barra_auto_preferida()
        self.taskbar_watcher = jarvis_displays.VigilanteBarras(self.events, lambda: self._barra_auto)
        self.taskbar_watcher.start()
        self._refresh_taskbar_button()
        self.wakeword_detector: jarvis_wakeword.DetectorPalabraActivacion | None = None
        if self._wakeword_preferida():
            self._start_wakeword()
        self._refresh_wakeword_indicator()
        self._refresh_autostart_button()
        self._refresh_settings_info()
        if jarvis_autostart.arrancado_con_windows():
            # Al encender la PC: directo a la bandeja, sin abrir ventanas.
            self._hide_to_tray()
        self._routines_panel: RoutinesPanel | None = None
        self._vision: dict | None = None
        self.root.after(100, self._poll_events)
        self.root.after(5000, self._check_scheduled_routines)
        self._learning_running = False
        self._vectors_synced = False
        self.root.after(30000, self._check_learning)
        self.root.after(2000, self._check_close_request)
        self._set_status("INICIANDO SERVICIOS DOCKER…", CYAN)
        threading.Thread(
            target=self._docker_start_worker,
            name="JarvisDockerStartup",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._ollama_gpu_worker,
            name="JarvisOllamaGPU",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._startup_checks_worker,
            name="JarvisStartupChecks",
            daemon=True,
        ).start()
        if self.integraciones.error:
            self.events.put(("integrations_error", self.integraciones.error))
        try:
            self.mcp.iniciar(self._on_mcp_connected)
        except Exception:
            logging.exception("No se pudieron iniciar los servidores MCP.")

        try:
            core.iniciar_metricas()
        except OSError as error:
            logging.warning("Métricas no disponibles en el puerto 8000: %s", error)

    def _request_tray_show(self, _icon, _item) -> None:
        self.events.put(("tray_show",))

    def _request_tray_exit(self, _icon, _item) -> None:
        self.events.put(("tray_exit",))

    def _request_tray_compact(self, _icon, _item) -> None:
        self.events.put(("compact_toggle",))

    # Jarvis corre como administrador y no se puede cerrar desde fuera sin
    # elevar. Para instalar una versión nueva basta con crear este archivo en
    # su carpeta: Jarvis lo borra y se cierra limpiamente (como «Salir»).
    SOLICITUD_CIERRE = "cerrar.solicitud"

    def _check_close_request(self) -> None:
        if self._closing:
            return
        solicitud = core.APP_DIR / self.SOLICITUD_CIERRE
        try:
            if solicitud.exists():
                solicitud.unlink()
                logging.info("Cierre solicitado con %s; Jarvis se cierra.", self.SOLICITUD_CIERRE)
                self._close()
                return
        except OSError:
            logging.exception("No se pudo leer la solicitud de cierre.")
        self.root.after(2000, self._check_close_request)

    def _request_tray_autostart(self, _icon, _item) -> None:
        activar = not self._autostart_wanted
        self.events.put((
            "autostart_toggle",
            activar,
            "Bandeja: activar el inicio con Windows" if activar else "Bandeja: desactivar el inicio con Windows",
            False,
        ))

    def _hide_to_tray(self) -> None:
        if self._closing:
            return
        self.chat_window.withdraw()
        self.root.withdraw()
        self._set_status("EJECUTÁNDOSE EN SEGUNDO PLANO", CYAN)

    def _on_window_unmapped(self, _event) -> None:
        if not self._closing and self.root.state() == "iconic":
            self.root.after_idle(self._hide_to_tray)

    def _show_from_tray(self) -> None:
        if self._closing:
            return
        aviso = getattr(self, "floating", None)
        if aviso is not None:
            aviso.ocultar()
        if getattr(self, "compact_window", None) is not None:
            self._close_compact_window()
        self.root.deiconify()
        if self.root.state() == "iconic":
            self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def _announce(self, text: str, *, alarm: bool = False) -> None:
        """Aviso que no responde a una orden: chat, notificación de Windows,
        sonido opcional y voz. No cambia el estado ocupado/libre de la app."""
        self._add_message("assistant", text)
        self._set_status("⏰ " + text[:60].upper() if alarm else text[:60].upper(), CYAN)
        try:
            self.tray_icon.notify(text, "J.A.R.V.I.S.")
        except Exception:
            logging.debug("No se pudo mostrar la notificación de Windows.", exc_info=True)

        def sonar_y_hablar() -> None:
            if alarm:
                try:
                    jarvis_reminders.reproducir_alarma()
                except Exception:
                    logging.exception("No se pudo reproducir la alarma.")
            if not self.audio_enabled:
                return
            try:
                audio = core.generar_audio_tts(text)
            except Exception:
                logging.exception("No se pudo generar el audio del aviso.")
                return
            self.events.put(("announce_audio", audio))

        threading.Thread(target=sonar_y_hablar, name="JarvisAviso", daemon=True).start()

    # --- Aprendizaje automático ---------------------------------------------

    _LEARNING_INTERVAL_MS = 60000

    def _check_learning(self) -> None:
        """Cada minuto: si hay conversaciones inactivas sin revisar, aprende de ellas."""
        if self._closing:
            return
        self.root.after(self._LEARNING_INTERVAL_MS, self._check_learning)
        if self._learning_running or self.busy:
            return
        self._learning_running = True
        threading.Thread(target=self._learning_worker, name="JarvisAprendizaje", daemon=True).start()

    def _learning_worker(self) -> None:
        try:
            if not self._vectors_synced:
                jarvis_memory.sincronizar_vectores()
                self._vectors_synced = True
            avisos = jarvis_aprendizaje.procesar_pendientes()
        except Exception:
            logging.exception("Falló el aprendizaje automático.")
            avisos = []
        finally:
            self._learning_running = False
        if avisos:
            self.events.put(("learned", avisos))

    def _ollama_gpu_worker(self) -> None:
        """Si Ollama arrancó sin la tarjeta gráfica, lo reinicia (ver jarvis_ollama_gpu)."""
        try:
            aviso = jarvis_ollama_gpu.asegurar_gpu()
        except Exception:
            logging.exception("No se pudo comprobar si Ollama usa la tarjeta gráfica.")
            return
        if aviso:
            self.events.put(("ollama_gpu_notice", aviso))

    def _docker_start_worker(self) -> None:
        try:
            # Al encender la PC, Docker Desktop también está arrancando: más margen.
            core.iniciar_servicios_docker(
                espera=300 if jarvis_autostart.arrancado_con_windows() else 120
            )
        except (OSError, RuntimeError, ValueError) as error:
            logging.exception("No se pudieron iniciar los servicios Docker.")
            self.events.put(("docker_services_error", str(error)))
        else:
            self.events.put(("docker_services_ready",))
            core.precalentar_stt()
        finally:
            self._docker_start_finished.set()

    def _startup_checks_worker(self) -> None:
        """Comprueba Ollama y limpia respuestas antiguas sin bloquear el arranque."""
        try:
            ollama = jarvis_health.comprobar_ollama()
            if not ollama.ok:
                logging.warning("Ollama no disponible al iniciar: %s", ollama.detalle)
                self.events.put(("ollama_unavailable", ollama.detalle))
        except Exception:
            logging.exception("No se pudo comprobar Ollama al iniciar.")
        try:
            core.limpiar_respuestas()
        except Exception:
            logging.exception("No se pudieron limpiar las respuestas antiguas.")
        try:
            mapa = jarvis_displays.cargar_mapa(core.obtener_preferencia)
            _pantallas, mapa = jarvis_displays.listar_pantallas(mapa)
            jarvis_displays.guardar_mapa(core.guardar_preferencia, mapa)
        except Exception:
            logging.exception("No se pudo actualizar el mapa de monitores.")
        jarvis_vision.configurar_numeracion(_numeracion_fija_monitores)

    def _on_mcp_connected(self, servidor: str, herramientas: list[HerramientaMCP]) -> None:
        """Se llama desde el hilo MCP; el registro de herramientas es seguro entre hilos."""
        nombres = self.mcp.registrar_herramientas(self.tool_registry, servidor, herramientas)
        self.events.put(("mcp_connected", servidor, len(nombres)))

    def _integration_checks(self) -> list[jarvis_health.Comprobacion]:
        checks = [
            jarvis_health.Comprobacion(f"Plugin {plugin.nombre}", True, plugin.descripcion)
            for plugin in self.plugins.cargados
        ]
        checks += [
            jarvis_health.Comprobacion(f"Plugin {nombre}", False, error)
            for nombre, error in self.plugins.errores.items()
        ]
        for servidor, estado in self.mcp.estado.items():
            checks.append(
                jarvis_health.Comprobacion(
                    f"MCP {servidor}", estado.startswith("conectado"), estado
                )
            )
        if self.integraciones.error:
            checks.append(
                jarvis_health.Comprobacion("Integraciones", False, self.integraciones.error)
            )
        return checks

    def _docker_shutdown_worker(self) -> None:
        try:
            self.mcp.detener()
        except Exception:
            logging.exception("No se pudieron detener los servidores MCP.")
        self._docker_start_finished.wait()
        try:
            core.detener_servicios_docker()
        except (OSError, RuntimeError, ValueError) as error:
            logging.exception("No se pudieron detener los servicios Docker.")
            self.events.put(("docker_shutdown_error", str(error)))
            return
        self.events.put(("docker_shutdown_done",))


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
        system_query = (
            detectar_consulta_fecha_hora(text)
            or jarvis_reminders.detectar_comando(text)
            or detectar_consulta_bateria(text)
            or detectar_comando_multimedia(text)
            or detectar_consulta_sistema(text)
            or detectar_comando_monitores(text)
            or detectar_comando_tema(text)
            or detectar_comando_compacto(text)
            or detectar_comando_energia(text)
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

    def _toggle_recording(self) -> None:
        if self.recorder is not None:
            self._stop_recording()
            return
        if self.busy and self._pending_plan_reply is None:
            return
        try:
            self.recorder = GrabadorAudio()
            self.recorder.iniciar()
        except Exception as error:
            logging.exception("No se pudo iniciar el micrófono.")
            if self.recorder is not None:
                self.recorder.cancelar()
            self.recorder = None
            self._set_status("MICRÓFONO NO DISPONIBLE", ERROR)
            self._add_message("assistant", f"No pude iniciar el micrófono: {error}")
            return

        self._recording_for_plan_approval = self._pending_plan_reply is not None
        if self._recording_for_plan_approval:
            self._set_plan_voice_controls(recording=True)
        self.mic_button.configure(text="■  DETENER", fg=ERROR)
        self.send_button.configure(state="disabled")
        self.input.configure(state="disabled")
        self._set_status(
            "GRABANDO CONFIRMACIÓN… PULSA DETENER"
            if self._recording_for_plan_approval
            else "GRABANDO… PULSA DETENER",
            ERROR,
        )

    def _on_microphone_hotkey(self, _event=None) -> str:
        del _event
        self._toggle_recording()
        return "break"

    def _stop_recording(self) -> None:
        recorder = self.recorder
        self.recorder = None
        plan_approval = self._recording_for_plan_approval
        approval_kind = self._pending_approval_kind or "plan"
        self._recording_for_plan_approval = False
        self.mic_button.configure(text="🎙  MIC", fg=CYAN)
        try:
            audio_path = recorder.detener()
        except Exception as error:
            logging.exception("Falló la captura del micrófono.")
            if plan_approval and self._pending_plan_reply is not None:
                self._set_plan_voice_controls(recording=False)
                self.mic_button.configure(state="normal", text="🎙  MIC", fg=CYAN)
                self._set_status("ERROR DE CAPTURA; USA LOS BOTONES", ERROR)
                self._add_message("assistant", f"No pude capturar la confirmación: {error}")
            else:
                self._set_status("ERROR DE CAPTURA", ERROR)
                self._add_message("assistant", f"No pude capturar el audio: {error}")
                self._set_idle()
            return

        if not audio_path:
            if plan_approval and self._pending_plan_reply is not None:
                self._set_plan_voice_controls(recording=False)
                self.mic_button.configure(state="normal")
                self._set_status(self._approval_hint(approval_kind), CYAN)
            else:
                self._set_idle()
            return

        if not plan_approval:
            self._start_busy("TRANSCRIBIENDO AUDIO…")
        else:
            self._set_plan_voice_controls(recording=False, recognizing=True)
            self.mic_button.configure(state="disabled")
            self._set_status("RECONOCIENDO CONFIRMACIÓN…", CYAN)
        threading.Thread(
            target=self._voice_worker,
            args=(
                audio_path,
                time.perf_counter(),
                self.conversation_id,
                plan_approval,
                approval_kind,
                self.cancel_token,
            ),
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
                    decision = JarvisApp._approval_decision(self, approval_kind, transcript)
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
            system_query = (
                detectar_consulta_fecha_hora(transcript)
                or jarvis_reminders.detectar_comando(transcript)
                or detectar_consulta_bateria(transcript)
                or detectar_comando_multimedia(transcript)
                or detectar_consulta_sistema(transcript)
                or detectar_comando_monitores(transcript)
                or detectar_comando_tema(transcript)
                or detectar_comando_compacto(transcript)
                or detectar_comando_energia(transcript)
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
            limpiar_audio(audio_path)

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
                        answer = procesar_con_herramientas(
                            text,
                            conversation_id,
                            self.tool_registry,
                            self._request_tool_confirmation,
                            approve_plan=self._request_plan_confirmation,
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

    # --- Respuesta en vivo (texto y voz mientras el modelo escribe) -------------

    def _new_live_speaker(self) -> "jarvis_voz_stream.HabladorEnVivo | None":
        if not self.audio_enabled:
            return None
        return jarvis_voz_stream.HabladorEnVivo(
            lambda frase, identificador: core.generar_audio_tts(frase, identificador)
        )

    def _live_text_callback(self, hablador):
        def recibir(trozo: str | None) -> None:
            if hablador is not None:
                hablador.alimentar(trozo)
            self.events.put(("answer_delta", trozo))

        return recibir

    def _show_live_answer(self, trozo: str | None) -> None:
        """Burbuja provisional que crece mientras el modelo escribe."""
        if trozo is None:  # nueva ronda del modelo: empezar de cero
            self._live_text = ""
            if self._live_label is not None:
                self._live_label.configure(text="…")
            return
        self._live_text += trozo
        if (
            self._live_row is None
            or self._live_label is None
            or not self._live_row.winfo_exists()
        ):
            self._clear_live_answer(keep_text=True)
            fila = tk.Frame(self.messages, bg=PANEL)
            fila.pack(fill="x", padx=14, pady=8)
            self._live_row = fila
            burbuja = tk.Frame(fila, bg=PANEL_LIGHT, padx=14, pady=10)
            burbuja.pack(side="left", anchor="w")
            tk.Label(
                burbuja, text="JARVIS", bg=PANEL_LIGHT, fg=VIOLET, font=("Consolas", 8, "bold")
            ).pack(anchor="w", pady=(0, 5))
            self._live_label = tk.Label(
                burbuja, text="", bg=PANEL_LIGHT, fg=TEXT, justify="left", anchor="w",
                wraplength=650, font=("Segoe UI", 11),
            )
            self._live_label.pack(anchor="w")
        self._live_label.configure(text=self._live_text.strip() or "…")
        self._scroll_chat_to_bottom()

    def _clear_live_answer(self, *, keep_text: bool = False) -> None:
        if self._live_row is not None and self._live_row.winfo_exists():
            self._live_row.destroy()
        self._live_row = None
        self._live_label = None
        if not keep_text:
            self._live_text = ""

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
            limpiar_audio(audio_path)
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
            limpiar_audio(audio_path)

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

    def _handle_device_event(self, trigger: str) -> None:
        try:
            routines = jarvis_routines.rutinas_para_evento(trigger)
        except Exception:
            logging.exception("No se pudieron cargar las rutinas del evento %s.", trigger)
            return
        for routine in routines:
            self._pending_routines.put(routine)
        if routines and not self.busy:
            self._set_status(
                "AURICULARES CONECTADOS"
                if trigger == jarvis_routines.DISPARADOR_AURICULARES_CONECTADOS
                else "AURICULARES DESCONECTADOS",
                CYAN,
            )

    # --- Clic y escritura en pantalla ------------------------------------------

    def _start_screen_action(self, pedido: dict, command: str) -> None:
        """«Haz clic en … del monitor N»: captura (sin las ventanas de Jarvis),
        busca el elemento y pide confirmación con la marca antes de actuar."""
        if self.busy or self._pending_plan_reply is not None:
            return
        try:
            monitor = jarvis_vision.elegir_monitor(jarvis_vision.listar_monitores(), pedido["monitor"])
        except (OSError, ValueError) as error:
            self._add_message("assistant", str(error))
            return
        self._start_busy(f"BUSCANDO EN EL {monitor.descripcion.upper()}…")
        conversation_id = self.conversation_id

        def capturado(imagen, error) -> None:
            if error is not None or imagen is None:
                self._finish_screen_action(f"No pude capturar la pantalla: {error}", command, conversation_id)
                return
            threading.Thread(
                target=self._screen_action_worker,
                args=(pedido, monitor, imagen, command, conversation_id),
                name="JarvisPantalla",
                daemon=True,
            ).start()

        self._capture_hidden(monitor, capturado)

    def _screen_action_worker(self, pedido, monitor, imagen, command, conversation_id) -> None:
        escribir = pedido["accion"] == "type"

        def terminar(mensaje: str) -> None:
            self.events.put(("screen_finished", mensaje, command, conversation_id))

        try:
            objetivo = jarvis_screen_actions.buscar_por_accesibilidad(
                monitor, pedido["consulta"], escribir=escribir, excluir_pid=os.getpid()
            )
            if objetivo is None and pedido.get("monitor") is None:
                # No dijo en qué monitor: probar en los demás antes de recurrir a la visión.
                for otro in jarvis_vision.listar_monitores():
                    if otro.numero == monitor.numero:
                        continue
                    objetivo = jarvis_screen_actions.buscar_por_accesibilidad(
                        otro, pedido["consulta"], escribir=escribir, excluir_pid=os.getpid()
                    )
                    if objetivo is not None:
                        monitor, imagen = otro, jarvis_vision.capturar_monitor(otro)
                        break
            if objetivo is None:
                if not self._request_screen_approval("vision", imagen, monitor, None, pedido):
                    terminar(f"No encontré «{pedido['consulta']}» en el monitor {monitor.numero}.")
                    return
                objetivo = jarvis_screen_actions.buscar_por_vision(
                    imagen, monitor, pedido["consulta"], escribir=escribir
                )
                if objetivo is None:
                    terminar(f"Tampoco encontré «{pedido['consulta']}» con el modelo de visión.")
                    return
            if not self._request_screen_approval("click", imagen, monitor, objetivo, pedido):
                terminar("De acuerdo, no hice nada.")
                return
            self.events.put(("screen_execute", objetivo, pedido.get("texto", ""), command, conversation_id))
        except Exception as error:
            logging.exception("Falló la acción en pantalla.")
            terminar(f"No pude completar la acción en pantalla: {error}")

    def _request_screen_approval(self, tipo, imagen, monitor, objetivo, pedido) -> bool:
        if self._closing:
            return False
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        self.events.put(("screen_approval", tipo, imagen, monitor, objetivo, pedido, reply))
        try:
            return reply.get(timeout=120)
        except queue.Empty:
            self.events.put(("plan_confirmation_timeout", reply))
            return False

    def _show_screen_approval(self, tipo, imagen, monitor, objetivo, pedido, reply) -> None:
        """Captura con una marca roja donde se hará clic (o propuesta de usar visión)."""
        if self._closing or self._pending_plan_reply is not None:
            reply.put(False)
            return
        from PIL import ImageDraw

        self._pending_plan_reply = reply
        self._pending_approval_kind = "screen"
        miniatura = imagen.copy().convert("RGB")
        escala = min(680 / miniatura.width, 380 / miniatura.height, 1.0)
        miniatura = miniatura.resize((round(miniatura.width * escala), round(miniatura.height * escala)))
        if objetivo is not None:
            dibujo = ImageDraw.Draw(miniatura)
            mx = (objetivo.x - monitor.x) * escala
            my = (objetivo.y - monitor.y) * escala
            if objetivo.caja:
                l, a, r, b = objetivo.caja
                dibujo.rectangle(
                    ((l - monitor.x) * escala, (a - monitor.y) * escala,
                     (r - monitor.x) * escala, (b - monitor.y) * escala),
                    outline=(255, 60, 60), width=2,
                )
            for radio in (14, 9):
                dibujo.ellipse((mx - radio, my - radio, mx + radio, my + radio), outline=(255, 40, 40), width=3)
            dibujo.line((mx - 22, my, mx + 22, my), fill=(255, 40, 40), width=2)
            dibujo.line((mx, my - 22, mx, my + 22), fill=(255, 40, 40), width=2)

        if tipo == "vision":
            titulo = f"NO ENCONTRÉ «{pedido['consulta'].upper()}» POR ACCESIBILIDAD"
            detalle = (
                "¿Lo busco con el modelo de visión local? Se le enviará esta captura "
                f"({core.MODEL_VISION}); no sale de tu equipo ni se guarda."
            )
            aprobar = "BUSCAR CON VISIÓN"
        else:
            accion = (
                f"Escribiré «{pedido.get('texto', '')}» en «{objetivo.nombre}»"
                if pedido["accion"] == "type"
                else f"Haré clic en «{objetivo.nombre}»"
            )
            titulo = "¿HAGO ESTO?"
            detalle = f"{accion}\n(encontrado por {objetivo.origen}; marca roja en la captura)"
            aprobar = "SÍ, HAZLO"
        tarjeta, cuerpo = self._confirmation_card(
            titulo=titulo, color=CYAN, aprobar=aprobar, rechazar="NO",
            pista="Di «sí» o «no», sin «Hey Jarvis». ENTER confirma · ESC cancela.",
            necesita_ventana=True,
        )
        self._plan_dialog = tarjeta
        tk.Label(cuerpo, text=detalle, bg=PANEL_LIGHT, fg=TEXT, font=("Segoe UI", 10),
                 justify="left", wraplength=680).pack(anchor="w", pady=(0, 8))
        foto = ImageTk.PhotoImage(miniatura)
        vista = tk.Label(cuerpo, image=foto, bg=PANEL)
        vista.image = foto
        vista.pack(anchor="w")
        self._set_plan_voice_controls(recording=False)
        self._set_status(self._approval_hint("screen"), CYAN)

    def _execute_screen_action(self, objetivo, texto, command, conversation_id) -> None:
        """Oculta Jarvis (para que el clic no caiga sobre su ventana), actúa y vuelve."""
        ocultas = []
        candidatas = [self.root, self.chat_window]
        if self._routines_panel is not None and self._routines_panel.is_open():
            # Incrustado en la ventana principal: se oculta con ella.
            if isinstance(self._routines_panel.window, tk.Toplevel):
                candidatas.append(self._routines_panel.window)
        for ventana in candidatas:
            try:
                if ventana.winfo_exists() and ventana.state() != "withdrawn":
                    ocultas.append(ventana)
                    ventana.withdraw()
            except tk.TclError:
                continue

        def ejecutar() -> None:
            argumentos = {"x": objetivo.x, "y": objetivo.y}
            if texto:
                argumentos["text"] = texto
            try:
                self.tool_registry.execute(
                    "screen_click", argumentos, confirmed=True, origin="pantalla"
                )
                mensaje = (
                    f"Escribí «{texto}» en «{objetivo.nombre}»." if texto else f"Hice clic en «{objetivo.nombre}»."
                )
            except Exception as error:
                if not isinstance(error, ToolError):
                    logging.exception("Falló el clic en pantalla.")
                mensaje = f"No pude hacer clic: {error}"
            self.events.put(("screen_finished", mensaje, command, conversation_id))
            self.root.after(0, lambda: [v.deiconify() for v in ocultas if v.winfo_exists()])

        # Esperar a que Windows quite las ventanas antes de hacer clic.
        self.root.after(350, lambda: threading.Thread(target=ejecutar, daemon=True).start())

    def _finish_screen_action(self, mensaje: str, command: str, conversation_id: str) -> None:
        self._add_message("assistant", mensaje)
        try:
            core.guardar_turno(conversation_id, command, mensaje)
        except Exception:
            logging.exception("No se pudo guardar la acción en pantalla en memoria.")
        self._set_idle()

    # --- Comandos personalizados (rutinas con disparador «frase») -----------

    def _find_phrase_routine(self, texto: str) -> "jarvis_routines.Routine | None":
        try:
            return jarvis_routines.buscar_rutina_por_frase(texto)
        except Exception:
            logging.exception("No se pudieron buscar los comandos personalizados.")
            return None

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
                self.root.after(15000, self._check_scheduled_routines)

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
                step = (
                    f"{index}. {action.tool.description}\n"
                    f"   Herramienta: {action.tool.name} | Riesgo: {action.tool.risk.value}\n"
                    f"   Parámetros: {json.dumps(action.arguments, ensure_ascii=False)}"
                )
            tk.Label(
                cuerpo, text=step, bg=PANEL, fg=TEXT, padx=12, pady=9, font=("Consolas", 9),
                justify="left", wraplength=660, anchor="w",
            ).pack(fill="x", pady=3)
        self._set_plan_voice_controls(recording=False)

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
        try:
            etiqueta = self.tool_registry.get(name).display_name
        except ToolError:
            etiqueta = name
        tk.Label(cuerpo, text=f"{etiqueta} · riesgo {risk}\n{description}", bg=PANEL_LIGHT, fg=TEXT,
                 font=("Segoe UI", 10), wraplength=680, justify="left").pack(anchor="w")
        tk.Label(cuerpo, text=json.dumps(arguments, ensure_ascii=False, indent=2), bg=PANEL, fg=MUTED,
                 font=("Consolas", 9), justify="left", padx=10, pady=8, anchor="w").pack(fill="x", pady=(6, 0))
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

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                try:
                    if event[0] == "transcript":
                        self._add_message("user", event[1])
                        self._set_status("GENERANDO RESPUESTA…", CYAN)
                    elif event[0] == "tool_confirmation":
                        _, name, description, risk, arguments, reply = event
                        if not self._closing and self._pending_plan_reply is None:
                            self._show_tool_confirmation(name, description, risk, arguments, reply)
                            continue
                        if self._closing:
                            approved = False
                        else:
                            if self.root.state() == "withdrawn":
                                self._show_from_tray()
                            approved = messagebox.askyesno(
                                "Confirmar acción de Jarvis",
                                (
                                    f"¿Autorizas esta acción?\n\n"
                                    f"Herramienta: {name}\n"
                                    f"Riesgo: {risk}\n"
                                    f"Acción: {description}\n"
                                    f"Parámetros:\n"
                                    f"{json.dumps(arguments, ensure_ascii=False, indent=2)}"
                                ),
                                parent=self.root,
                                icon="warning" if risk == "high" else "question",
                            )
                        reply.put(approved)
                    elif event[0] == "power_countdown":
                        _, nombre, accion, reply = event
                        self._show_power_countdown(nombre, accion, reply)
                    elif event[0] == "power_confirmation":
                        _, action, reply = event
                        if self.root.state() == "withdrawn":
                            self._show_from_tray()
                        self._show_power_confirmation(action, reply)
                    elif event[0] == "plan_confirmation":
                        _, goal, actions, reply = event
                        if self.root.state() == "withdrawn" and not self._closing:
                            self._show_from_tray()
                        self._show_plan_confirmation(goal, actions, reply)
                    elif event[0] == "routine_save_confirmation":
                        _, goal, actions, reply = event
                        if self.root.state() == "withdrawn" and not self._closing:
                            self._show_from_tray()
                        self._show_plan_confirmation(
                            goal,
                            actions,
                            reply,
                            kind="routine_save",
                        )
                    elif event[0] == "mcp_connected":
                        _, servidor, cantidad = event
                        logging.info("Integración MCP %s lista (%s herramientas).", servidor, cantidad)
                        if not self.busy and self.recorder is None:
                            self._set_status(f"INTEGRACIÓN {servidor.upper()} LISTA", CYAN)
                    elif event[0] == "integrations_error":
                        self._add_message(
                            "assistant",
                            f"Aviso: no activé las integraciones porque {event[1]}",
                        )
                    elif event[0] == "compact_toggle":
                        if self._compact_mode_active():
                            self._exit_compact_mode()
                        else:
                            self._enter_compact_mode()
                    elif event[0] == "taskbar_misplaced":
                        self._on_taskbar_misplaced(event[1])
                    elif event[0] == "display_keep":
                        _, descripcion, reply = event
                        if self._closing:
                            reply.put(False)
                        else:
                            self._show_display_keep_dialog(descripcion, reply)
                    elif event[0] == "vision_request":
                        _, numero, pregunta, command = event
                        self._set_idle()
                        self._start_vision_capture(numero, pregunta, command)
                    elif event[0] == "vision_cancelled":
                        self._add_message(
                            "assistant", "Descarté la captura; no se envió nada al modelo."
                        )
                        self._set_status("CAPTURA DESCARTADA", MUTED)
                        self._set_idle()
                    elif event[0] == "clear_history_request":
                        self._set_idle()
                        self._confirm_clear_history()
                    elif event[0] == "cancelled":
                        self._clear_live_answer()
                        self._add_message("assistant", event[1])
                        self._set_status("OPERACIÓN CANCELADA", MUTED)
                        self._set_idle()
                    elif event[0] == "ollama_unavailable":
                        self._add_message(
                            "assistant",
                            f"Aviso: Ollama {event[1]} Las órdenes directas (volumen, "
                            "YouTube, consultas del equipo…) funcionan igual; las preguntas "
                            "al asistente fallarán hasta que Ollama esté disponible.",
                        )
                        if not self.busy:
                            self._set_status("OLLAMA NO DISPONIBLE", ERROR)
                    elif event[0] == "device_event":
                        self._handle_device_event(event[1])
                    elif event[0] == "routine_due":
                        self._pending_routines.put(event[1])
                    elif event[0] == "routine_result":
                        _, routine, succeeded, result = event
                        self._routine_active = False
                        automatica = (
                            getattr(routine, "permission", "") == jarvis_routines.PERMISO_AUTOMATICA
                        )
                        if succeeded:
                            status = (
                                f"Rutina «{routine.name}» completada"
                                + (" automáticamente" if automatica else "")
                                + (f": {result}" if result else ".")
                            )
                        else:
                            status = f"Rutina «{routine.name}» no completada: {result}"
                        try:
                            core.guardar_turno(
                                self.conversation_id,
                                f"Ejecutar rutina programada: {routine.name}",
                                status,
                            )
                        except Exception:
                            logging.exception(
                                "No se pudo guardar el resultado de la rutina %s.",
                                routine.name,
                            )
                        self._add_message("assistant", status)
                        self._set_status(
                            "RUTINA COMPLETADA" if succeeded else "RUTINA INTERRUMPIDA",
                            CYAN if succeeded else ERROR,
                        )
                        self._set_idle()
                    elif event[0] == "routine_scheduler_error":
                        self._add_message(
                            "assistant",
                            f"No pude revisar las rutinas programadas: {event[1]}",
                        )
                        self._set_status("ERROR DEL PLANIFICADOR", ERROR)
                    elif event[0] == "plan_voice_decision":
                        _, decision, transcript = event
                        approval_kind = self._pending_approval_kind or "plan"
                        if self._pending_plan_reply is None:
                            continue
                        self._add_message("user", f"Confirmación por voz: {transcript}")
                        if decision is None:
                            self._set_plan_voice_controls(recording=False)
                            self.mic_button.configure(state="normal", text="🎙  MIC", fg=CYAN)
                            self._set_status(
                                f"NO RECONOCÍ «{transcript[:36]}»; "
                                + self._approval_hint(approval_kind),
                                ERROR,
                            )
                        else:
                            self._resolve_plan_approval(decision)
                            self._set_status(
                                {
                                    "routine_save": (
                                        "GUARDADO DE RUTINA CANCELADO POR VOZ",
                                        "RUTINA AUTORIZADA PARA GUARDAR POR VOZ",
                                    ),
                                    "vision": (
                                        "CAPTURA DESCARTADA POR VOZ",
                                        "ANALIZANDO LA CAPTURA…",
                                    ),
                                    "power": (
                                        "CANCELADO POR VOZ",
                                        "CONFIRMADO POR VOZ",
                                    ),
                                    "screen": (
                                        "CANCELADO POR VOZ",
                                        "CONFIRMADO POR VOZ",
                                    ),
                                }.get(
                                    approval_kind,
                                    ("PLAN RECHAZADO POR VOZ", "PLAN APROBADO POR VOZ"),
                                )[bool(decision)],
                                CYAN if decision else MUTED,
                            )
                    elif event[0] == "plan_voice_unrecognized":
                        if self._pending_plan_reply is None:
                            continue
                        approval_kind = self._pending_approval_kind or "plan"
                        self._set_plan_voice_controls(recording=False)
                        self.mic_button.configure(state="normal", text="🎙  MIC", fg=CYAN)
                        self._set_status(
                            "NO RECONOCÍ LA DECISIÓN; " + self._approval_hint(approval_kind),
                            ERROR,
                        )
                    elif event[0] == "plan_voice_error":
                        if self._pending_plan_reply is None:
                            continue
                        logging.error("No se pudo reconocer la aprobación por voz: %s", event[1])
                        self._set_plan_voice_controls(recording=False)
                        self.mic_button.configure(state="normal", text="🎙  MIC", fg=CYAN)
                        self._set_status("ERROR DE VOZ; USA LOS BOTONES", ERROR)
                    elif event[0] == "plan_confirmation_timeout":
                        _, reply = event
                        if self._pending_plan_reply is reply:
                            self._resolve_plan_approval(False)
                    elif event[0] == "tool_ui_request":
                        _, action, value, reply = event
                        try:
                            if action == "set_audio":
                                self._set_audio_enabled(value)
                                result = (
                                    "Audio de Jarvis activado."
                                    if value
                                    else "Audio de Jarvis silenciado."
                                )
                            elif action == "set_compact":
                                if value:
                                    self._enter_compact_mode()
                                    result = "Modo compacto activado. Toca el orbe para hablarme."
                                else:
                                    self._exit_compact_mode()
                                    result = "Volví a la ventana completa."
                            elif action == "set_theme":
                                result = self._set_theme(value)
                            elif action == "minimize_jarvis":
                                self._hide_to_tray()
                                result = "Jarvis se minimizó en la bandeja."
                            elif action == "maximize_jarvis":
                                self.root.state("zoomed")
                                result = "La ventana de Jarvis se maximizó."
                            else:
                                raise ValueError(f"Acción de interfaz desconocida: {action}")
                        except Exception as error:
                            logging.exception("Falló una acción de interfaz solicitada por herramienta.")
                            reply.put((False, str(error)))
                        else:
                            reply.put((True, result))
                    elif event[0] == "global_hotkey_ready":
                        logging.info("Atajo global F9 registrado para el micrófono.")
                    elif event[0] == "global_hotkey_unavailable":
                        logging.error("No se pudo registrar el atajo global F9: %s", event[1])
                        self._set_status("F9 NO DISPONIBLE; USA EL BOTÓN MIC", ERROR)
                    elif event[0] == "reminder_due":
                        recordatorio = event[1]
                        self._announce(
                            jarvis_reminders.describir_aviso(recordatorio, datetime.now()),
                            alarm=True,
                        )
                    elif event[0] == "headset_battery_low":
                        self._announce(
                            f"Batería baja en los auriculares: queda alrededor de un {event[1]} %."
                        )
                    elif event[0] == "announce_audio":
                        audio = event[1]
                        if self.audio_enabled and self.recorder is None and Path(audio).is_file():
                            self._play_audio(audio)
                    elif event[0] == "learned":
                        self._add_message(
                            "assistant",
                            "🧠 " + "\n".join(event[1])
                            + "\n(Si algo no es correcto, di «olvida el recuerdo N».)",
                        )
                    elif event[0] == "ollama_gpu_notice":
                        self._add_message("assistant", event[1])
                    elif event[0] == "docker_services_ready":
                        logging.info("Servicios Docker de STT y TTS listos.")
                        if not self._closing and not self.busy and self.recorder is None:
                            self._set_status("SERVICIOS DOCKER LISTOS", CYAN)
                    elif event[0] == "docker_services_error":
                        logging.error("No se pudieron iniciar los servicios Docker: %s", event[1])
                        if not self._closing:
                            self._add_message(
                                "assistant",
                                f"No pude iniciar los servicios de voz en Docker: {event[1]}",
                            )
                            if not self.busy and self.recorder is None:
                                self._set_status("ERROR AL INICIAR DOCKER", ERROR)
                    elif event[0] == "docker_shutdown_done":
                        self._finish_close()
                        return
                    elif event[0] == "docker_shutdown_error":
                        messagebox.showerror(
                            "Error al cerrar Jarvis",
                            f"No pude detener los contenedores STT y TTS:\n\n{event[1]}",
                            parent=self.root,
                        )
                        self._finish_close()
                        return
                    elif event[0] == "tray_show":
                        self._show_from_tray()
                    elif event[0] == "tray_exit":
                        self._close()
                    elif event[0] == "tray_error":
                        logging.error("Icono de bandeja no disponible: %s", event[1])
                        if not self._closing:
                            self._set_status("BANDEJA NO DISPONIBLE", ERROR)
                    elif event[0] == "global_microphone_hotkey":
                        self._on_orb_click()
                    elif event[0] == "window_action":
                        _, action, command = event
                        self._set_idle()
                        self._execute_window_action(action, command)
                    elif event[0] == "volume_action":
                        _, porcentaje, command = event
                        self._set_idle()
                        self._execute_volume_action(porcentaje, command)
                    elif event[0] == "output_device_action":
                        _, dispositivo, command = event
                        self._set_idle()
                        self._execute_output_device_action(dispositivo, command)
                    elif event[0] == "audio_toggle":
                        _, enabled, command = event
                        self._set_idle()
                        self._execute_audio_toggle(enabled, command)
                    elif event[0] == "autostart_toggle":
                        _, enabled, command, desde_voz = event
                        if desde_voz:
                            self._set_idle()
                        self._execute_autostart_toggle(enabled, command)
                    elif event[0] == "wakeword_toggle":
                        _, enabled, command = event
                        self._set_idle()
                        self._execute_wakeword_toggle(enabled, command)
                    elif event[0] == "wakeword_ready":
                        logging.info("Escuchando «Hey Jarvis».")
                        self._refresh_wakeword_indicator()
                    elif event[0] == "wakeword_detected":
                        self._on_wakeword_detected()
                    elif event[0] == "wakeword_no_command":
                        if not self.busy and self._pending_plan_reply is None:
                            self._set_status("NO OÍ NINGUNA ORDEN", MUTED)
                    elif event[0] == "wakeword_command":
                        self._on_wakeword_command(event[1])
                    elif event[0] == "approval_utterance":
                        self._on_approval_utterance(event[1])
                    elif event[0] == "free_approval_result":
                        _, decision, transcript, reply = event
                        self._free_approval_busy = False
                        if self._pending_plan_reply is not reply:
                            continue  # esa confirmación ya se resolvió
                        if decision is None:
                            # No era «sí» ni «no» (o era ruido): seguir esperando sin avisos.
                            self._set_status(
                                self._approval_hint(self._pending_approval_kind or "plan"), CYAN
                            )
                            continue
                        self.events.put(("plan_voice_decision", decision, transcript))
                    elif event[0] == "wakeword_error":
                        self.wakeword_detector = None
                        self._refresh_wakeword_indicator()
                        self._add_message(
                            "assistant",
                            f"La palabra de activación dejó de funcionar: {event[1]}. "
                            "Puedes seguir usando F9.",
                        )
                        self._set_status("PALABRA DE ACTIVACIÓN NO DISPONIBLE", ERROR)
                    elif event[0] == "game_action":
                        _, game_name, command = event
                        self._set_idle()
                        self._execute_game_action(game_name, command)
                    elif event[0] == "screen_action":
                        _, pedido, command = event
                        self._set_idle()
                        self._start_screen_action(pedido, command)
                    elif event[0] == "screen_approval":
                        self._show_screen_approval(*event[1:])
                    elif event[0] == "screen_execute":
                        self._execute_screen_action(*event[1:])
                    elif event[0] == "screen_finished":
                        _, mensaje, command, conversation_id = event
                        self._finish_screen_action(mensaje, command, conversation_id)
                    elif event[0] == "phrase_routine":
                        _, rutina, command = event
                        self._set_idle()
                        self._start_phrase_routine(rutina, command)
                    elif event[0] == "system_query":
                        _, tool_name, arguments, command = event
                        self._set_idle()
                        self._start_direct_tool_action(tool_name, arguments, command)
                    elif event[0] == "direct_action_done":
                        _, _tool_name, response = event
                        self._show_direct_response(_tool_name, response)
                        self._set_idle()
                        if _tool_name in ("configure_display", "fix_display_scaling") \
                                and self._current_page == "ajustes":
                            self._refresh_monitor_map()
                    elif event[0] == "direct_action_error":
                        _, _tool_name, message = event
                        self._show_direct_response(_tool_name, message)
                        if not message.startswith("Cancelé"):
                            self._set_status("ACCIÓN NO COMPLETADA", ERROR)
                        else:
                            self._set_status("ACCIÓN CANCELADA", MUTED)
                        self._set_idle()
                    elif event[0] == "action_result":
                        self._show_action_result(event[1])
                        try:
                            tarjeta = self._add_action_card(event[1])
                            self._last_action_cards[event[1].herramienta] = tarjeta
                        except tk.TclError:
                            logging.exception("No se pudo dibujar la tarjeta de la acción.")
                    elif event[0] == "youtube_search":
                        _, query, play_index, command = event
                        self._start_youtube_search(
                            query,
                            play_index,
                            command,
                            self.conversation_id,
                        )
                    elif event[0] == "browser_action":
                        _, action, value, command = event
                        self._start_browser_action(action, value, command)
                    elif event[0] == "youtube_play_result":
                        _, play_index, command = event
                        if self._last_youtube_query is None:
                            self._add_message(
                                "assistant",
                                "Primero busca una canción en YouTube y luego dime qué resultado quieres reproducir.",
                            )
                            self._set_idle()
                        else:
                            self._start_youtube_search(
                                self._last_youtube_query,
                                play_index,
                                command,
                                self.conversation_id,
                            )
                    elif event[0] == "answer_delta":
                        self._show_live_answer(event[1])
                    elif event[0] == "answer":
                        _, answer, audio, elapsed = event
                        self._clear_live_answer()
                        if answer is None:
                            self._add_message(
                                "assistant",
                                "No pude obtener una respuesta. Comprueba que Ollama esté activo y vuelve a intentar.",
                            )
                        else:
                            self._add_message(
                                "assistant",
                                answer,
                                audio if audio and Path(audio).exists() else None,
                            )
                            self._set_status(f"LISTO  ·  {elapsed:.1f} S", CYAN)
                            ya_dicho = audio in self._spoken_audio
                            self._spoken_audio.discard(audio)
                            if (
                                self.audio_enabled
                                and not ya_dicho
                                and audio
                                and Path(audio).is_file()
                            ):
                                self._play_audio(audio)
                        self._set_idle()
                    elif event[0] == "empty_transcript":
                        self._add_message(
                            "assistant",
                            "No pude reconocer voz en esa grabación. Intenta hablar más cerca del micrófono.",
                        )
                        self._set_idle()
                    elif event[0] == "error":
                        self._add_message("assistant", event[1])
                        self._set_status("ERROR", ERROR)
                        self._set_idle()
                    elif event[0] == "player_progress":
                        _, token, elapsed, duration = event
                        if token == self.player_token:
                            self.player_time.configure(
                                text=f"{self._format_time(elapsed)} / {self._format_time(duration)}"
                            )
                            self.player_progress.configure(
                                value=elapsed / duration * 100 if duration else 0
                            )
                    elif event[0] == "player_error":
                        _, token, message = event
                        if token == self.player_token:
                            self._set_status("ERROR DE REPRODUCCIÓN", ERROR)
                            self._add_message("assistant", f"No pude reproducir el audio: {message}")
                    elif event[0] == "player_finished":
                        _, token = event
                        if token == self.player_token:
                            self.player_toggle.configure(text="▶", state="disabled")
                            self.player_stop_button.configure(state="disabled")
                            if self.audio_enabled and not self.player_paused:
                                self.player_progress.configure(value=100)
                                self._set_status("REPRODUCCIÓN FINALIZADA", CYAN)
                except Exception as error:
                    logging.exception("Falló el manejo del evento %s.", event[0])
                    self._recover_from_event_error(event, error)
        except queue.Empty:
            pass
        finally:
            # Reprogramar siempre: un evento fallido no debe congelar la app.
            if not self._closing_finished and self.root.winfo_exists():
                self._start_next_pending_routine()
                self.root.after(100, self._poll_events)


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

    DISPLAY_KEEP_SECONDS = 15

    def _tool_configure_display(
        self,
        action: str,
        monitor: int,
        second_monitor: int | None = None,
    ) -> str:
        numeros = (monitor,) if second_monitor is None else (monitor, second_monitor)
        mapa = jarvis_displays.cargar_mapa(core.obtener_preferencia)
        descripcion, previa, mapa = jarvis_displays.aplicar(action, numeros, mapa)
        try:
            _pantallas, mapa = jarvis_displays.listar_pantallas(mapa)
            jarvis_displays.guardar_mapa(core.guardar_preferencia, mapa)
        except Exception:
            logging.exception("No se pudo actualizar el mapa de monitores tras el cambio.")
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        self.events.put(("display_keep", descripcion, reply))
        try:
            mantener = reply.get(timeout=self.DISPLAY_KEEP_SECONDS + 10)
        except queue.Empty:
            mantener = False
        if mantener:
            watcher = getattr(self, "taskbar_watcher", None)
            if watcher is not None:
                watcher.despertar()  # tras cambiar monitores, revisar la barra enseguida
            return descripcion
        jarvis_displays.restaurar_configuracion(previa)
        raise RuntimeError(
            "No confirmaste el cambio de monitores, así que restauré la configuración anterior."
        )

    def _tool_move_window(
        self, app: str, monitor: int | None = None, screen_name: str | None = None
    ) -> str:
        numero = monitor
        if numero is None and screen_name:
            numero = self._monitor_por_nombre(screen_name)
        if numero is None:
            raise ValueError("Indica a qué monitor mover la ventana (número o «la TV»).")
        destino = jarvis_vision.elegir_monitor(jarvis_vision.listar_monitores(), numero)
        movidas, total, programas = jarvis_windows.mover_ventanas(app, destino)
        if movidas == 0:
            raise RuntimeError(f"No pude mover las ventanas de «{app}» al monitor {numero}.")
        quien = "todas las ventanas" if jarvis_windows.normalizar(app) in jarvis_windows.TODO else (
            f"{movidas} ventana{'s' if movidas != 1 else ''} de {', '.join(programas)}"
        )
        faltan = f" ({total - movidas} no se pudieron mover)" if movidas < total else ""
        return f"Moví {quien} al monitor {numero}{faltan}."

    def _monitor_por_nombre(self, nombre: str) -> int:
        """«tv» → el monitor activo cuyo nombre lo contiene (la «Smart TV Pro»)."""
        clave = jarvis_windows.normalizar(nombre)
        if clave in ("tele", "televisor", "television", "smart tv"):
            clave = "tv"
        pantallas, _ = jarvis_displays.listar_pantallas(jarvis_displays.cargar_mapa(core.obtener_preferencia))
        for pantalla in pantallas:
            if pantalla.numero and clave in jarvis_windows.normalizar(pantalla.nombre).split():
                return pantalla.numero
        for pantalla in pantallas:
            if pantalla.numero and clave in jarvis_windows.normalizar(pantalla.nombre):
                return pantalla.numero
        raise ValueError(f"No encontré ningún monitor llamado «{nombre}».")

    def _tool_fix_display_scaling(self, monitor: int | None = None) -> str:
        mapa = jarvis_displays.cargar_mapa(core.obtener_preferencia)
        return jarvis_displays.arreglar_escalado(monitor, mapa)

    def _show_display_keep_dialog(self, descripcion: str, reply: queue.Queue[bool]) -> None:
        """Como Windows: si no se confirma en 15 s, la configuración se revierte."""
        restante = {"segundos": self.DISPLAY_KEEP_SECONDS, "resuelto": False}
        # Se registra como confirmación pendiente para que también se pueda
        # responder por voz con «sí»/«no» (sin «Hey Jarvis»), como las demás.
        por_voz = self._pending_plan_reply is None

        def responder(valor: bool) -> None:
            if restante["resuelto"]:
                return
            restante["resuelto"] = True
            if cuenta.winfo_exists():
                cuenta.configure(text="")
            if self._pending_plan_reply is reply:
                self._resolve_plan_approval(valor)  # marca la tarjeta y responde
                return
            try:
                reply.put_nowait(valor)
            except queue.Full:
                pass
            if tarjeta.winfo_exists():
                tarjeta.jarvis_resolver(valor, "✓  MANTENIDA" if valor else "✕  REVERTIDA")

        if por_voz:
            self._pending_plan_reply = reply
            self._pending_approval_kind = "display"
        tarjeta, cuerpo = self._confirmation_card(
            titulo="¿MANTENER ESTA CONFIGURACIÓN DE PANTALLAS?", color=CYAN,
            aprobar="MANTENER", rechazar="REVERTIR", voz=False,
            resumen=f"¿Mantengo esta configuración? {descripcion}",
            pista=("Di «sí» o «no» · " if por_voz else "") + "ENTER mantiene · ESC revierte.",
            al_aprobar=lambda: responder(True), al_rechazar=lambda: responder(False),
        )
        if por_voz:
            self._plan_dialog = tarjeta
        tk.Label(cuerpo, text=descripcion, bg=PANEL_LIGHT, fg=TEXT, font=("Segoe UI", 10),
                 wraplength=680, justify="left").pack(anchor="w")
        cuenta = tk.Label(cuerpo, bg=PANEL_LIGHT, fg=MUTED, font=("Segoe UI", 9))
        cuenta.pack(anchor="w", pady=(6, 0))
        try:
            # Encima de todo un momento: tras cambiar monitores la ventana puede quedar detrás.
            self.root.attributes("-topmost", True)
            self.root.after(1500, lambda: self.root.attributes("-topmost", False))
            self.root.bell()
        except tk.TclError:
            pass

        def tic() -> None:
            if not tarjeta.winfo_exists():
                return
            if restante["resuelto"] or (por_voz and self._pending_plan_reply is not reply):
                cuenta.configure(text="")  # ya se respondió (por botón o por voz)
                return
            if restante["segundos"] <= 0:
                responder(False)
                return
            cuenta.configure(text=f"Si no respondes, se revertirá en {restante['segundos']} s.")
            restante["segundos"] -= 1
            tarjeta.after(1000, tic)

        tic()

    def _center_over_jarvis(self, dialog: tk.Toplevel) -> None:
        try:
            self.root.update_idletasks()
            dialog.update_idletasks()
            ancho, alto = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
            for referencia in (self.chat_window, self.root):
                if referencia.winfo_exists() and referencia.state() not in ("withdrawn", "iconic"):
                    x = referencia.winfo_rootx() + (referencia.winfo_width() - ancho) // 2
                    y = referencia.winfo_rooty() + (referencia.winfo_height() - alto) // 3
                    break
            else:
                x = (dialog.winfo_screenwidth() - ancho) // 2
                y = (dialog.winfo_screenheight() - alto) // 3
            dialog.geometry(f"+{max(x, dialog.winfo_vrootx())}+{max(y, dialog.winfo_vrooty())}")
        except tk.TclError:
            logging.debug("No se pudo centrar el diálogo de monitores.", exc_info=True)

    def _tool_set_system_volume(self, percentage: int) -> str:
        cambiar_volumen_sistema(percentage)
        return f"Volumen del sistema establecido en {percentage}%."

    def _tool_set_output_device(self, device: str) -> str:
        name = cambiar_dispositivo_salida(device)
        return f"Salida de audio cambiada a {name}."

    def _tool_set_theme(self, theme: str) -> str:
        return self._request_tool_ui_action("set_theme", theme)

    def _set_theme(self, nombre: str) -> str:
        """Hilo de Tk: cambia los colores de todo sin reiniciar y guarda la elección."""
        nombre = jarvis_theme.nombre_valido(nombre)
        mapa = jarvis_theme.activar(nombre)
        jarvis_theme.recolorear(self.root, mapa)
        self._configure_styles()
        self._draw_orb()
        if self._current_page:
            self._show_page(self._current_page)
        self._refresh_theme_buttons()
        try:
            core.guardar_preferencia(jarvis_theme.PREFERENCIA, nombre)
        except Exception:
            logging.exception("No se pudo guardar el tema.")
        logging.info("Tema cambiado a %s (%s colores de widgets).", nombre, len(mapa))
        return f"Tema cambiado a {jarvis_theme.ETIQUETAS[nombre]}."

    def _tool_set_jarvis_audio(self, enabled: bool) -> str:
        return self._request_tool_ui_action("set_audio", enabled)

    def _tool_open_new_browser_tab(self) -> str:
        brave = encontrar_brave()
        if brave is None:
            raise FileNotFoundError(
                "No encontré Brave. Instálalo o agrega brave.exe al PATH."
            )
        if not abrir_nueva_pestana_brave(brave):
            subprocess.Popen([str(brave), "about:blank"], shell=False)
        return "Abrí una pestaña nueva en Brave."

    def _tool_navigate_browser(self, url: str) -> str:
        url = normalizar_url_web(url)
        brave = encontrar_brave()
        if brave is None:
            raise FileNotFoundError(
                "No encontré Brave. Instálalo o agrega brave.exe al PATH."
            )
        if not navegar_en_ventana_brave(url, brave):
            subprocess.Popen([str(brave), url], shell=False)
        return f"Navegué a {url} en Brave."

    def _tool_launch_game(self, name: str) -> str:
        folder_name, executable = localizar_ejecutable_juego(name)
        iniciar_ejecutable_juego(executable)
        return f"Inicié {folder_name} con {executable.name}."

    def _tool_manage_window(self, action: str) -> str:
        if action in {"brave_minimize", "brave_maximize"}:
            brave = encontrar_brave()
            if brave is None:
                raise FileNotFoundError(
                "No encontré Brave. Instálalo o agrega brave.exe al PATH."
            )
            state = "minimize" if action == "brave_minimize" else "maximize"
            if not cambiar_estado_ventana_brave(state, brave):
                raise RuntimeError("No encontré una ventana abierta de Brave.")
            return (
                "Minimicé la ventana de Brave."
                if state == "minimize"
                else "Maximicé la ventana de Brave."
            )
        if action == "minimize":
            return self._request_tool_ui_action("minimize_jarvis")
        if action == "maximize":
            return self._request_tool_ui_action("maximize_jarvis")
        raise ValueError(f"Acción de ventana no permitida: {action}")

    def _tool_search_youtube(
        self,
        query: str,
        play_index: int | None = None,
    ) -> str:
        brave = encontrar_brave()
        if brave is None:
            raise FileNotFoundError(
                "No encontré Brave. Instálalo o agrega brave.exe al PATH."
            )
        if play_index is None:
            url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
            result = f"Abrí la búsqueda de «{query}» en YouTube."
        else:
            video_id = obtener_primer_video_youtube(query, play_index)
            url = f"https://www.youtube.com/watch?v={video_id}&autoplay=1"
            result = f"Abrí el resultado {play_index} de «{query}» en YouTube."
        if not navegar_en_ventana_brave(url, brave):
            subprocess.Popen([str(brave), url], shell=False)
        return result

    def _execute_audio_toggle(self, enabled: bool, command: str) -> None:
        self._set_audio_enabled(enabled)
        respuesta = (
            "Audio de Jarvis activado."
            if enabled
            else "Audio de Jarvis silenciado."
        )
        try:
            core.guardar_turno(self.conversation_id, command, respuesta)
        except Exception as error:
            logging.exception("No se pudo guardar el cambio de audio en memoria.")
            self._set_status("ERROR DE MEMORIA", ERROR)
            self._add_message(
                "assistant",
                f"{respuesta} No pude guardar la acción en la memoria: {error}",
            )
            return

        self._add_message("assistant", respuesta)
        self._set_status(
            "AUDIO DE JARVIS ACTIVADO" if enabled else "AUDIO DE JARVIS SILENCIADO",
            CYAN if enabled else MUTED,
        )

    # --- Palabra de activación «Hey Jarvis» -------------------------------------

    _PREFERENCIA_WAKEWORD = "palabra_activacion"

    def _wakeword_preferida(self) -> bool:
        try:
            return core.obtener_preferencia(self._PREFERENCIA_WAKEWORD) != "0"
        except Exception:
            logging.exception("No se pudo leer la preferencia de la palabra de activación.")
            return False

    def _wakeword_active(self) -> bool:
        detector = getattr(self, "wakeword_detector", None)
        return detector is not None and detector.is_alive()

    def _refresh_wakeword_indicator(self) -> None:
        """Botón «HEY JARVIS ON/OFF» junto a «AUDIO ON»: siempre muestra si escucha."""
        boton = getattr(self, "wakeword_button", None)
        if boton is None:
            return
        activo = self._wakeword_active()
        boton.configure(
            text="HEY JARVIS ON" if activo else "HEY JARVIS OFF",
            fg=CYAN if activo else MUTED,
        )

    def _toggle_wakeword_button(self) -> None:
        activar = not self._wakeword_active()
        self._execute_wakeword_toggle(
            activar,
            "Botón: activar «Hey Jarvis»" if activar else "Botón: desactivar «Hey Jarvis»",
        )

    def _can_listen_for_wakeword(self) -> bool:
        """Se consulta desde el hilo del detector: solo lee atributos, sin Tk."""
        if self._closing or self.recorder is not None:
            return False
        if self.busy and self._pending_plan_reply is None:
            return False
        player = getattr(self, "player_thread", None)
        reproduciendo = (
            player is not None and player.is_alive() and not getattr(self, "player_paused", False)
        )
        return not reproduciendo

    def _start_wakeword(self) -> bool:
        if self.wakeword_detector is not None and self.wakeword_detector.is_alive():
            return True
        if not jarvis_wakeword.disponible():
            logging.warning("openWakeWord no está instalado; sin palabra de activación.")
            return False
        self.wakeword_detector = jarvis_wakeword.DetectorPalabraActivacion(
            self.events,
            self._can_listen_for_wakeword,
            escucha_libre=self._free_approval_listening,
        )
        self.wakeword_detector.start()
        return True

    def _stop_wakeword(self) -> None:
        detector = getattr(self, "wakeword_detector", None)
        self.wakeword_detector = None
        if detector is not None:
            detector.stop()

    # --- Inicio con Windows ---------------------------------------------------

    _PREFERENCIA_INICIO_WINDOWS = "inicio_windows"

    _PREFERENCIA_AVISO = "aviso_flotante"
    _PREFERENCIA_COMPACTO = "compacto_pos"

    def _refresh_floating_button(self) -> None:
        boton = getattr(self, "floating_button", None)
        if boton is not None:
            boton.configure(
                text="▤  AVISO FLOTANTE: SÍ" if self._floating_enabled else "▤  AVISO FLOTANTE: NO",
                fg=CYAN if self._floating_enabled else MUTED,
            )

    def _toggle_floating(self) -> None:
        self._floating_enabled = not self._floating_enabled
        try:
            core.guardar_preferencia(self._PREFERENCIA_AVISO, "1" if self._floating_enabled else "0")
        except Exception:
            logging.exception("No se pudo guardar la preferencia del aviso flotante.")
        if not self._floating_enabled:
            self.floating.ocultar()
        self._refresh_floating_button()

    def _compact_position(self, ancho: int, alto: int) -> tuple[int, int]:
        """Posición guardada del modo compacto o, la primera vez, arriba a la derecha."""
        try:
            x, y = (int(v) for v in (core.obtener_preferencia(self._PREFERENCIA_COMPACTO) or "").split(","))
            if -10000 < x < 10000 and -10000 < y < 10000:
                return x, y
        except (ValueError, TypeError):
            pass
        except Exception:
            logging.exception("No se pudo leer la posición del modo compacto.")
        izq, arr, der, _abj = jarvis_floating.area_de_trabajo(self.root)
        return der - ancho - 24, arr + 24

    def _save_compact_position(self, x: int, y: int) -> None:
        try:
            core.guardar_preferencia(self._PREFERENCIA_COMPACTO, f"{x},{y}")
        except Exception:
            logging.exception("No se pudo guardar la posición del modo compacto.")

    def _tool_set_compact_mode(self, enabled: bool) -> str:
        return self._request_tool_ui_action("set_compact", enabled)

    _PREFERENCIA_ANIMACIONES = "animaciones"

    def _refresh_animations_button(self) -> None:
        boton = getattr(self, "animations_button", None)
        if boton is not None:
            activas = self._animations_enabled()
            boton.configure(
                text="✦  ANIMACIONES: SÍ" if activas else "✦  ANIMACIONES: NO",
                fg=CYAN if activas else MUTED,
            )

    def _toggle_animations(self) -> None:
        self._animations = not self._animations_enabled()
        try:
            core.guardar_preferencia(self._PREFERENCIA_ANIMACIONES, "1" if self._animations else "0")
        except Exception:
            logging.exception("No se pudo guardar la preferencia de animaciones.")
        self._refresh_animations_button()

    _PREFERENCIA_BARRA_AUTO = "barra_auto"

    def _barra_auto_preferida(self) -> bool:
        """Activado salvo que el usuario lo haya desactivado."""
        try:
            return core.obtener_preferencia(self._PREFERENCIA_BARRA_AUTO) != "0"
        except Exception:
            logging.exception("No se pudo leer la preferencia de la barra de tareas.")
            return True

    def _refresh_taskbar_button(self) -> None:
        boton = getattr(self, "taskbar_button", None)
        if boton is not None:
            boton.configure(
                text="▭  ARREGLO AUTOMÁTICO: SÍ" if self._barra_auto else "▭  ARREGLO AUTOMÁTICO: NO",
                fg=CYAN if self._barra_auto else MUTED,
            )

    def _toggle_taskbar_button(self) -> None:
        self._barra_auto = not self._barra_auto
        try:
            core.guardar_preferencia(self._PREFERENCIA_BARRA_AUTO, "1" if self._barra_auto else "0")
        except Exception:
            logging.exception("No se pudo guardar la preferencia de la barra de tareas.")
        self._refresh_taskbar_button()

    def _refresh_monitor_map(self) -> None:
        """Lee el estado de los monitores (rápido: API de Windows) y redibuja la vista."""
        try:
            self._monitor_views = jarvis_monitor_view.recolectar(
                jarvis_displays.cargar_mapa(core.obtener_preferencia)
            )
        except Exception:
            logging.exception("No se pudo leer el estado de los monitores para la vista.")
            self._monitor_views = []
        self._draw_monitor_map()

    def _run_monitor_action(self, herramienta: str, argumentos: dict, texto: str) -> None:
        if self.busy or self.recorder is not None or self._pending_plan_reply is not None:
            self._set_status("ESPERA A QUE TERMINE LA ACCIÓN EN CURSO", MUTED)
            return
        self._start_direct_tool_action(herramienta, dict(argumentos), f"Vista de monitores: {texto}")

    def _on_taskbar_misplaced(self, numero: int | None) -> None:
        """Una barra de tareas quedó fuera de su sitio: se arregla sola, sin preguntar."""
        if self._closing or self.busy or self.recorder is not None or self._pending_plan_reply is not None:
            logging.info("Barra de tareas mal, pero Jarvis está ocupado; se reintentará.")
            return
        donde = f"del monitor {numero}" if numero else "de un monitor"
        self._start_direct_tool_action(
            "fix_display_scaling",
            {"monitor": numero} if numero else {},
            f"Arreglo automático: la barra de tareas {donde} se veía fuera de su sitio.",
        )

    def _autostart_preferido(self) -> bool:
        """Activado salvo que el usuario lo haya desactivado."""
        try:
            return core.obtener_preferencia(self._PREFERENCIA_INICIO_WINDOWS) != "0"
        except Exception:
            logging.exception("No se pudo leer la preferencia de inicio con Windows.")
            return True

    def _refresh_settings_info(self) -> None:
        """Datos de solo lectura en la sección Ajustes."""
        etiqueta = getattr(self, "settings_info", None)
        if etiqueta is None:
            return
        try:
            admin = "sí" if jarvis_autostart.es_administrador() else "no"
        except Exception:
            admin = "desconocido"
        etiqueta.configure(text=(
            f"MODELO DE IA     {core.MODEL_LLM}\n"
            f"VISIÓN           {core.MODEL_VISION}\n"
            f"VOZ A TEXTO      {core.STT_MODEL}\n"
            f"VOZ              {core.VOZ_PIPER}\n"
            f"ADMINISTRADOR    {admin}"
        ))

    def _refresh_autostart_button(self) -> None:
        boton = getattr(self, "autostart_button", None)
        if boton is None:
            return
        activo = self._autostart_wanted
        boton.configure(
            text="⏻  INICIO CON WINDOWS: SÍ" if activo else "⏻  INICIO CON WINDOWS: NO",
            fg=CYAN if activo else MUTED,
        )

    def _toggle_autostart_button(self) -> None:
        activar = not self._autostart_wanted
        self._execute_autostart_toggle(
            activar,
            "Botón: activar el inicio con Windows" if activar else "Botón: desactivar el inicio con Windows",
        )

    def _execute_autostart_toggle(self, enabled: bool, command: str) -> None:
        try:
            respuesta = jarvis_autostart.activar() if enabled else jarvis_autostart.desactivar()
        except OSError as error:
            logging.exception("No se pudo cambiar el inicio con Windows.")
            respuesta = f"No pude cambiar el inicio con Windows: {error}"
        else:
            self._autostart_wanted = enabled
            try:
                core.guardar_preferencia(self._PREFERENCIA_INICIO_WINDOWS, "1" if enabled else "0")
            except Exception:
                logging.exception("No se pudo guardar la preferencia de inicio con Windows.")
        try:
            core.guardar_turno(self.conversation_id, command, respuesta)
        except Exception:
            logging.exception("No se pudo guardar el cambio de inicio con Windows en memoria.")
        self._add_message("assistant", respuesta)
        self._refresh_autostart_button()
        try:
            self.tray_icon.update_menu()
        except Exception:
            logging.debug("No se pudo refrescar el menú de la bandeja.", exc_info=True)
        self._set_status(
            "SE INICIARÁ CON WINDOWS" if self._autostart_wanted else "NO SE INICIARÁ CON WINDOWS",
            CYAN if self._autostart_wanted else MUTED,
        )

    def _execute_wakeword_toggle(self, enabled: bool, command: str) -> None:
        try:
            core.guardar_preferencia(self._PREFERENCIA_WAKEWORD, "1" if enabled else "0")
        except Exception:
            logging.exception("No se pudo guardar la preferencia de la palabra de activación.")
        if enabled:
            if self._start_wakeword():
                respuesta = "Palabra de activación activada. Di «Hey Jarvis» y luego tu orden."
            else:
                respuesta = (
                    "No pude activar la palabra de activación: falta openWakeWord. "
                    "Sigue disponible F9."
                )
        else:
            self._stop_wakeword()
            respuesta = "Palabra de activación desactivada. Usa F9 para hablar."
        try:
            core.guardar_turno(self.conversation_id, command, respuesta)
        except Exception:
            logging.exception("No se pudo guardar el cambio de escucha en memoria.")
        self._add_message("assistant", respuesta)
        self._refresh_wakeword_indicator()
        self._set_status(
            "ESCUCHANDO «HEY JARVIS»" if enabled and self.wakeword_detector else "F9 PARA HABLAR",
            CYAN,
        )

    def _on_wakeword_detected(self) -> None:
        try:
            jarvis_wakeword.reproducir_pitido()
        except Exception:
            logging.exception("No se pudo reproducir el pitido de activación.")
        if self._pending_plan_reply is not None:
            self._set_plan_voice_controls(recording=True)
            self._set_status("TE ESCUCHO… " + self._approval_hint(
                self._pending_approval_kind or "plan"
            ), ERROR)
        else:
            self._set_status("TE ESCUCHO…", ERROR)

    def _on_wakeword_command(self, audio_path: str) -> None:
        """Procesa la orden dicha tras «Hey Jarvis» como una grabación de F9."""
        plan_approval = self._pending_plan_reply is not None
        if self._closing or self.recorder is not None or (self.busy and not plan_approval):
            limpiar_audio(audio_path)
            return
        approval_kind = self._pending_approval_kind or "plan"
        if plan_approval:
            self._set_plan_voice_controls(recording=False, recognizing=True)
            self.mic_button.configure(state="disabled")
            self._set_status("RECONOCIENDO CONFIRMACIÓN…", CYAN)
        else:
            self._start_busy("TRANSCRIBIENDO AUDIO…")
        threading.Thread(
            target=self._voice_worker,
            args=(
                audio_path,
                time.perf_counter(),
                self.conversation_id,
                plan_approval,
                approval_kind,
                self.cancel_token,
            ),
            daemon=True,
        ).start()

    _DIRECT_SPOKEN_TOOLS = frozenset(
        {
            "get_current_datetime",
            "get_headset_battery",
            "get_now_playing",
            "set_reminder",
            "list_reminders",
            "cancel_reminder",
        }
    )

    _DIRECT_ACTION_BUSY = {
        "set_system_volume": "AJUSTANDO VOLUMEN…",
        "set_output_device": "CAMBIANDO SALIDA DE AUDIO…",
        "launch_game": "BUSCANDO EL JUEGO…",
        "open_new_browser_tab": "ABRIENDO PESTAÑA EN BRAVE…",
        "navigate_browser": "ABRIENDO PÁGINA EN BRAVE…",
        "search_youtube": "ABRIENDO YOUTUBE…",
        "list_open_applications": "REVISANDO APLICACIONES ABIERTAS…",
        "list_top_processes": "MIDIENDO PROCESOS…",
        "get_system_status": "REVISANDO EL EQUIPO…",
        "configure_display": "CAMBIANDO LA CONFIGURACIÓN DE MONITORES…",
        "fix_display_scaling": "REAJUSTANDO EL ESCALADO…",
        "set_theme": "CAMBIANDO EL TEMA…",
        "set_compact_mode": "CAMBIANDO EL MODO DE VENTANA…",
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
        self._start_direct_tool_action("launch_game", {"name": game_name}, command)

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
        if tool_name in JarvisApp._DIRECT_SPOKEN_TOOLS:
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

    _APPROVAL_PHRASES = {
        "plan": ("SÍ", "NO"),
        "routine_save": ("SÍ", "NO"),
        "vision": ("SÍ", "NO"),
        "power": ("SÍ", "NO"),
        "screen": ("SÍ", "NO"),
        "display": ("SÍ", "NO"),
        "tool": ("SÍ", "NO"),
    }

    def _approval_hint(self, kind: str) -> str:
        approve, reject = self._APPROVAL_PHRASES.get(kind, self._APPROVAL_PHRASES["plan"])
        return f"DI «{approve}» O «{reject}»"

    # --- Visión de pantalla (roadmap punto 6) ------------------------------------

    def _start_vision_capture(self, numero: int | None, pregunta: str, command: str) -> None:
        if self.busy or self._pending_plan_reply is not None:
            return
        try:
            monitores = jarvis_vision.listar_monitores()
            monitor = jarvis_vision.elegir_monitor(monitores, numero)
        except (OSError, ValueError) as error:
            self._add_message("assistant", str(error))
            self._set_status("NO PUDE CAPTURAR", ERROR)
            return
        self._start_busy(f"CAPTURANDO {monitor.descripcion.upper()}…")
        self._vision = {
            "monitores": monitores,
            "monitor": monitor,
            "pregunta": pregunta,
            "command": command,
            "imagen": None,
            "conversation_id": self.conversation_id,
            "cancel_token": self.cancel_token,
        }
        self._capture_hidden(monitor, self._on_first_capture)

    def _capture_hidden(self, monitor: jarvis_vision.Monitor, on_done) -> None:
        """Oculta las ventanas de Jarvis, captura el monitor y las vuelve a mostrar."""
        candidatas = [self.root, self.chat_window]
        if isinstance(self._plan_dialog, tk.Toplevel):
            candidatas.append(self._plan_dialog)
        if self._routines_panel is not None and self._routines_panel.is_open():
            # Incrustado en la ventana principal: se oculta con ella.
            if isinstance(self._routines_panel.window, tk.Toplevel):
                candidatas.append(self._routines_panel.window)
        visibles = []
        for ventana in candidatas:
            try:
                if ventana is not None and ventana.winfo_exists() and ventana.state() != "withdrawn":
                    visibles.append(ventana)
                    ventana.withdraw()
            except tk.TclError:
                continue

        def capturar() -> None:
            imagen, error = None, None
            try:
                imagen = jarvis_vision.capturar_monitor(monitor)
            except Exception as exc:  # se informa al usuario
                logging.exception("No se pudo capturar el monitor %s.", monitor.numero)
                error = exc
            for ventana in visibles:
                try:
                    ventana.deiconify()
                except tk.TclError:
                    pass
            on_done(imagen, error)

        self.root.after(400, capturar)

    def _on_first_capture(self, imagen, error) -> None:
        if error is not None or imagen is None:
            self._vision = None
            self._add_message("assistant", f"No pude capturar la pantalla: {error}")
            self._set_status("ERROR DE CAPTURA", ERROR)
            self._set_idle()
            return
        self._vision["imagen"] = imagen
        self._show_vision_preview()

    def _show_vision_preview(self) -> None:
        datos = self._vision
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        self._pending_plan_reply = reply
        self._pending_approval_kind = "vision"
        tarjeta, container = self._confirmation_card(
            titulo="¿ANALIZO ESTA CAPTURA?", color=CYAN, aprobar="🔍  ANALIZAR", rechazar="CANCELAR",
            pista="Di «analizar» o «cancelar», sin «Hey Jarvis». ENTER analiza · ESC cancela.",
            necesita_ventana=True,
        )
        self._plan_dialog = tarjeta
        info = tk.Label(container, bg=PANEL_LIGHT, fg=MUTED, font=("Consolas", 8), justify="left")
        info.pack(anchor="w", pady=(0, 8))
        preview = tk.Label(container, bg=PANEL)
        preview.pack(anchor="w")

        def mostrar() -> None:
            imagen = datos["imagen"]
            miniatura = imagen.copy()
            miniatura.thumbnail((680, 380))
            foto = ImageTk.PhotoImage(miniatura)
            preview.configure(image=foto)
            preview.image = foto
            info.configure(
                text=(
                    f"{datos['monitor'].descripcion} · Se enviará solo al modelo local "
                    f"{core.MODEL_VISION}; la imagen no se guarda en disco."
                )
            )

        mostrar()

        if len(datos["monitores"]) > 1:
            selector = tk.Frame(container, bg=PANEL_LIGHT)
            selector.pack(anchor="w", pady=(8, 0))
            tk.Label(selector, text="Cambiar a:", bg=PANEL_LIGHT, fg=MUTED, font=("Consolas", 8)).pack(side="left")

            def cambiar(monitor: jarvis_vision.Monitor) -> None:
                def listo(imagen, error) -> None:
                    if error is not None or imagen is None:
                        self._set_status("ERROR DE CAPTURA", ERROR)
                        return
                    datos["monitor"], datos["imagen"] = monitor, imagen
                    if tarjeta.winfo_exists():
                        mostrar()

                self._capture_hidden(monitor, listo)

            for monitor in datos["monitores"]:
                tk.Button(
                    selector, text=f"MONITOR {monitor.numero}",
                    command=lambda m=monitor: cambiar(m), bg=PANEL, fg=CYAN, activebackground=HOVER,
                    relief="flat", padx=10, pady=4, font=("Consolas", 8, "bold"), cursor="hand2",
                ).pack(side="left", padx=(6, 0))

        tk.Label(container, text="Pregunta para el modelo:", bg=PANEL_LIGHT, fg=MUTED,
                 font=("Consolas", 8)).pack(anchor="w", pady=(10, 2))
        pregunta = tk.StringVar(value=datos["pregunta"] or jarvis_vision.PREGUNTA_POR_DEFECTO)
        pregunta.trace_add("write", lambda *_: datos.update(pregunta=pregunta.get()))
        datos["pregunta"] = pregunta.get()
        entry = tk.Entry(container, textvariable=pregunta, bg=PANEL, fg=TEXT,
                         insertbackground=TEXT, relief="flat", font=("Segoe UI", 10), width=80)
        entry.pack(fill="x", ipady=6)
        entry.bind("<Return>", lambda _e: (self._resolve_plan_approval(True), "break")[1])
        entry.bind("<Escape>", lambda _e: (self._resolve_plan_approval(False), "break")[1])
        tarjeta.after_idle(entry.focus_set)
        self._set_status("REVISA LA CAPTURA: " + self._approval_hint("vision"), CYAN)
        threading.Thread(
            target=self._vision_worker, args=(reply, datos), name="JarvisVision", daemon=True
        ).start()

    def _record_vision_action(self, estado: EstadoAccion, inicio: float, monitor, error=None) -> None:
        self._record_action(
            ResultadoAccion(
                herramienta="screen_vision",
                etiqueta="Analizar pantalla",
                origen="directo",
                estado=estado,
                duracion=time.perf_counter() - inicio,
                argumentos={"monitor": monitor.numero},
                error=error,
            )
        )

    def _vision_worker(self, reply: queue.Queue[bool], datos: dict) -> None:
        try:
            approved = reply.get(timeout=180)
        except queue.Empty:
            self.events.put(("plan_confirmation_timeout", reply))
            approved = False
        imagen = datos.get("imagen")
        datos["imagen"] = None
        self._vision = None
        if not approved or imagen is None:
            self.events.put(("vision_cancelled",))
            return
        inicio = time.perf_counter()
        monitor = datos["monitor"]
        try:
            jpeg = jarvis_vision.preparar_imagen(imagen)
            del imagen
            answer = jarvis_vision.analizar_imagen(jpeg, datos["pregunta"], datos["cancel_token"])
        except OperacionCancelada:
            self._record_vision_action(EstadoAccion.CANCELADA, inicio, monitor, "Cancelado por el usuario.")
            self.events.put(("cancelled", "Cancelé el análisis de la pantalla."))
            return
        except Exception as error:
            logging.exception("Falló el análisis de la pantalla.")
            self._record_vision_action(EstadoAccion.ERROR, inicio, monitor, str(error))
            self.events.put(("error", f"No pude analizar la pantalla: {error}"))
            return
        self._record_vision_action(EstadoAccion.EXITO, inicio, monitor)
        try:
            core.guardar_turno(datos["conversation_id"], datos["command"], answer)
        except Exception:
            logging.exception("No se pudo guardar el análisis de pantalla en el historial.")
        audio_path = None
        try:
            response_id = core.guardar_respuesta(answer)
            audio_path = ejecutar_con_limites(
                lambda: core.generar_audio_tts(answer, response_id),
                timeout=core.TTS_TIMEOUT + 5,
                descripcion="texto a voz",
                cancel_token=datos["cancel_token"],
            )
        except Exception:
            logging.warning("No se generó el audio del análisis de pantalla.", exc_info=True)
        self.events.put(("answer", answer, audio_path, time.perf_counter() - inicio))

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

    def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._resolve_plan_approval(False)
        self._close_compact_window()
        aviso = getattr(self, "floating", None)
        if aviso is not None:
            aviso.destruir()
        self._set_status("DETENIENDO SERVICIOS DOCKER…", CYAN)
        self.global_hotkey_listener.stop()
        self.global_hotkey_listener.join(timeout=1)
        self.headphones_watcher.stop()
        self.battery_watcher.stop()
        self.reminder_watcher.stop()
        self.taskbar_watcher.stop()
        self._stop_wakeword()
        if self._routines_panel is not None:
            self._routines_panel.close()
        self._stop_player(wait=True)
        if self.recorder is not None:
            try:
                self.recorder.cancelar()
            except Exception:
                logging.exception("No se pudo cerrar el micrófono.")
        threading.Thread(
            target=self._docker_shutdown_worker,
            name="JarvisDockerShutdown",
            daemon=True,
        ).start()

    def _finish_close(self) -> None:
        self._closing_finished = True
        self.tray_icon.stop()
        self.root.destroy()

    def _recover_from_event_error(self, event: tuple, error: Exception) -> None:
        """Deja la app usable si falló el manejo de un evento.

        Responde «no» a cualquier confirmación pendiente (nunca se aprueba nada
        por un error), cierra un diálogo de aprobación a medio construir y
        avisa al usuario.
        """
        try:
            reply = event[-1] if event else None
            if isinstance(reply, queue.Queue):
                try:
                    reply.put_nowait(
                        (False, f"Error interno: {error}")
                        if event[0] == "tool_ui_request"
                        else False
                    )
                except queue.Full:
                    pass
            if self._pending_plan_reply is not None:
                self._resolve_plan_approval(False)
            self._add_message(
                "assistant",
                f"Ocurrió un error interno al procesar «{event[0]}»: {error}. "
                "Cancelé esa operación; puedes seguir usando Jarvis.",
            )
            self._set_status("ERROR INTERNO; OPERACIÓN CANCELADA", ERROR)
        except Exception:
            logging.exception("Tampoco se pudo recuperar del error del evento.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="🕒 %(asctime)s | %(levelname)s | %(message)s",
    )
    instance = SingleInstance()
    if not instance.acquire():
        if jarvis_autostart.arrancado_con_windows():
            # Inicio con Windows y Jarvis ya abierto: no mostrar la ventana.
            logging.info("Jarvis ya está abierto; se ignora el inicio con Windows.")
            return
        logging.info("Jarvis ya está abierto; se pide a esa instancia que se muestre.")
        if not instance.notify_existing():
            hidden = tk.Tk()
            hidden.withdraw()
            messagebox.showinfo(
                "Jarvis ya está abierto",
                "Jarvis ya se está ejecutando. Búscalo en la bandeja del sistema.",
                parent=hidden,
            )
            hidden.destroy()
        return

    try:
        log_path = configurar_registro(core.APP_DIR)
        logging.info("Jarvis iniciado. Registro en %s", log_path)
    except OSError:
        logging.exception("No se pudo crear el archivo de registro.")

    if jarvis_autostart.debe_relanzar_como_administrador():
        # Sin permisos de administrador el clic en pantalla falla sobre ventanas
        # elevadas. Soltar el mutex antes: la copia elevada debe poder adquirirlo.
        instance.release()
        if jarvis_autostart.relanzar_como_administrador():
            return
        instance = SingleInstance()
        if not instance.acquire():
            return  # otra copia se abrió mientras tanto

    root = tk.Tk()
    capturar_errores_tk(root)
    resource_dir = Path(
        getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)
    )
    icon_path = resource_dir / "assets" / "icon.ico"
    root.iconbitmap(str(icon_path))
    app = JarvisApp(root, icon_path)
    instance.start_listener(lambda: app.events.put(("tray_show",)))
    try:
        root.mainloop()
    finally:
        instance.release()


if __name__ == "__main__":
    main()
