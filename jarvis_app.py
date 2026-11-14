import json
import logging
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import jarvis_core as core
import jarvis_health
import jarvis_deshacer
import jarvis_servicios
import jarvis_displays
import jarvis_ollama_gpu
import jarvis_aprendizaje
import jarvis_autostart
import jarvis_reminders
import jarvis_wakeword
import jarvis_vision
import jarvis_memory
import jarvis_mobile
import jarvis_video_movil
import jarvis_system_info
import jarvis_fps
import jarvis_archivos
import jarvis_encendido
import jarvis_input
import jarvis_audio_movil
import jarvis_media
import psutil
import jarvis_audio_devices
import jarvis_dashboard
import jarvis_floating
import jarvis_games_watch
import jarvis_gpu_tuning
import jarvis_voices
import jarvis_hardware
import jarvis_routines
import jarvis_system
import jarvis_theme
import jarvis_adb
import jarvis_app_tv
import jarvis_routine_form
import jarvis_tv
from jarvis_actions import CancellationToken
from audio_capture import GrabadorAudio
import pystray
from PIL import Image

from jarvis_events import HeadphonesWatcher, HeadsetBatteryWatcher
from jarvis_integraciones import cargar_integraciones
from jarvis_mcp import GestorMCP, HerramientaMCP
from jarvis_plugins import GestorPlugins
from jarvis_hotkeys import GlobalHotkeyListener
from jarvis_routines_panel import RoutinesPanel
from jarvis_logging import capturar_errores_tk, configurar_registro
from jarvis_single_instance import SingleInstance
from jarvis_theme import BG, CYAN, ERROR, MUTED
from jarvis_ui import JarvisUIMixin
from jarvis_app_ordenes import OrdenesMixin
from jarvis_app_voz import VozMixin
from jarvis_app_confirmaciones import ConfirmacionesMixin
from jarvis_app_rutinas import RutinasMixin
from jarvis_app_monitores import MonitoresMixin
from jarvis_app_pantalla import PantallaMixin
from jarvis_app_movil import MovilMixin
from jarvis_app_ajustes import AjustesMixin
from jarvis_app_juego import JuegoMixin
from jarvis_app_tv import TvMixin
from jarvis_app_herramientas import HerramientasMixin
from jarvis_player import AudioPlayerMixin
from jarvis_tool_registry import ToolRisk
from jarvis_tools import crear_registro_herramientas
from jarvis_verifiers import crear_verificadores


def _numeracion_fija_monitores() -> dict[int, int]:
    """{DISPLAYN: número fijo} para que visión, clic y vídeo numeren como los monitores."""
    mapa = jarvis_displays.cargar_mapa(core.obtener_preferencia)
    equivalencias, nuevo = jarvis_displays.numeros_por_gdi(mapa)
    if nuevo != mapa:
        jarvis_displays.guardar_mapa(core.guardar_preferencia, nuevo)
    return equivalencias


class JarvisApp(
    OrdenesMixin,
    VozMixin,
    ConfirmacionesMixin,
    RutinasMixin,
    MonitoresMixin,
    PantallaMixin,
    MovilMixin,
    AjustesMixin,
    JuegoMixin,
    TvMixin,
    HerramientasMixin,
    JarvisUIMixin,
    AudioPlayerMixin,
):
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
        historial = core.obtener_historial_con_fecha(self.conversation_id)

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
            voz = jarvis_voices.por_clave(core.obtener_preferencia(jarvis_voices.PREFERENCIA))
            if not jarvis_voices.instalada(voz):
                voz = jarvis_voices.por_clave(jarvis_voices.POR_DEFECTO)
            jarvis_voices.aplicar(voz)
            self._voice_key = voz.clave
        except Exception:
            logging.exception("No se pudo cargar la voz guardada.")
            self._voice_key = jarvis_voices.POR_DEFECTO
        try:
            self._floating_enabled = core.obtener_preferencia(self._PREFERENCIA_AVISO) != "0"
        except Exception:
            self._floating_enabled = True
        self._configure_styles()
        self._build_layout()
        self._refresh_animations_button()
        self._refresh_morning_brief_button()
        self.floating =jarvis_floating.AvisoFlotante(self.root, self._show_from_tray)
        self._mobile_callbacks: dict[str, tuple] = {}
        self.mobile = jarvis_mobile.ServidorMovil(
            self.events,
            jarvis_mobile.Vinculacion(core.obtener_preferencia, core.guardar_preferencia),
            ocupado=lambda: bool(self.busy),
            pantallas=jarvis_vision.pantallas_para_movil,
            captura=jarvis_vision.captura_para_movil,
            video=jarvis_video_movil.transmitir,
            pantalla_permitida=lambda: self._mobile_screen_enabled,
            carpeta_app=core.APP_DIR / "movil",
            tts=self._tts_for_mobile,
            musica=self._media_for_mobile,
            portada=jarvis_media.portada_actual,
            accion=self._action_from_mobile,
            estado_equipo=self._status_for_mobile,
            cursor=jarvis_vision.cursor_para_movil,
            audio=jarvis_audio_movil.capturar,
            entrada=jarvis_input.aplicar,
            control_permitido=lambda: self._mobile_control_enabled,
            carpetas=jarvis_archivos.carpeta,
            raices=jarvis_archivos.raices,
            encendido=jarvis_encendido.diagnostico,
            abrir_link=self._open_link_from_mobile,
            portapapeles=(jarvis_archivos.leer_portapapeles, jarvis_archivos.escribir_portapapeles),
            tele=self._tv_for_mobile,
            tele_accion=self._tv_action_from_mobile,
        )
        self.fps_meter = jarvis_fps.MedidorFPS()
        self._stt_vocabulary_cache: tuple[float, list[str]] = (0.0, [])
        core.configurar_vocabulario_stt(self._stt_vocabulary)
        # La lista de apps del menú Inicio tarda ~0,4 s: se lee de fondo para que «abre X» responda enseguida.
        threading.Thread(target=jarvis_system.apps_instaladas, name="JarvisApps", daemon=True).start()
        try:
            self._mobile_enabled = core.obtener_preferencia(jarvis_mobile.PREFERENCIA_ACTIVO) == "1"
        except Exception:
            self._mobile_enabled = False
        try:  # ver la pantalla desde el celular: activado salvo que se apague en Ajustes
            self._mobile_screen_enabled = core.obtener_preferencia(jarvis_mobile.PREFERENCIA_PANTALLA) != "0"
        except Exception:
            self._mobile_screen_enabled = True
        try:  # controlar mouse y teclado desde el celular: apagado salvo que se active en Ajustes
            self._mobile_control_enabled = core.obtener_preferencia(jarvis_mobile.PREFERENCIA_CONTROL) == "1"
        except Exception:
            self._mobile_control_enabled = False
        self._control_card = None
        self._screen_badge: tk.Toplevel | None = None
        self._ensure_mobile_server()
        try:
            self._interrupt_enabled = core.obtener_preferencia(self._PREFERENCIA_INTERRUMPIR) != "0"
        except Exception:
            self._interrupt_enabled = True
        self._refresh_interrupt_button()
        self.compact_window = None
        self._refresh_floating_button()
        self.tool_registry = crear_registro_herramientas(
            self,
            recorder=self._record_action,
            verificadores=crear_verificadores(self),
        )
        # «Deshaz lo último»: cada acción reversible anota cómo volver atrás.
        # Televisor (Google TV): certificado y configuración en APP_DIR/tv.
        self.tv = jarvis_tv.ControlTV(core.APP_DIR / "tv", al_cambiar=lambda: self.events.put(("tv_state",)),
                                      nombrar=jarvis_app_tv.nombre_app, adb=jarvis_adb.ADB())
        # Las apps aprendidas de la tele también se ofrecen en el formulario de rutinas.
        jarvis_routine_form.SUGERENCIAS["tv_open_app"] = lambda: {n: c for c, n in self.tv.apps()}
        self.deshacer = jarvis_deshacer.Historial({
            "volumen": jarvis_audio_devices.obtener_volumen_sistema,
            "volumen_app": jarvis_media.leer_volumen_aplicacion,
            "salida": lambda: ("headphones" if jarvis_audio_devices.es_auriculares(
                jarvis_audio_devices.salida_predeterminada()) else "speakers"),
            "tema": jarvis_theme.tema_actual,
            "monitores": jarvis_displays.guardar_configuracion,
            "restaurar_monitores": jarvis_displays.restaurar_configuracion,
        })
        self.tool_registry.antes_de_ejecutar = self.deshacer.capturar
        self.tool_registry.tras_exito = self.deshacer.anotar
        self.tool_registry.register(
            "mobile_remote_control",
            "Activa o corta el control del mouse y el teclado de la PC desde la app del celular.",
            {"type": "object", "properties": {"enable": {"type": "boolean"}}, "required": ["enable"]},
            self._tool_mobile_remote_control,
            risk=ToolRisk.HIGH,
            requires_confirmation=True,
            label="Control remoto desde el celular",
            timeout_seconds=10,
            exposed=False,  # solo tras la tarjeta de confirmación; el modelo no la ve
        )
        self.tool_registry.register(
            "send_file_to_phone",
            "Manda al celular del usuario un archivo (ruta completa) de Descargas, Documentos o de los otros "
            "discos (D:, E:…; no del disco del sistema). Le llega como notificación para descargarlo.",
            {"type": "object", "properties": {"path": {"type": "string", "description": "Ruta completa del archivo."}},
             "required": ["path"]},
            self._tool_send_file_to_phone,
            risk=ToolRisk.LOW,
            label="Mandar archivo al celular",
            timeout_seconds=10,
        )
        self.tool_registry.register(
            "send_clipboard_to_phone",
            "Manda al celular el texto que el usuario copió en la PC («manda lo que copié al celular»).",
            {"type": "object", "properties": {}},
            self._tool_send_clipboard_to_phone,
            risk=ToolRisk.LOW,
            label="Mandar portapapeles al celular",
            timeout_seconds=10,
            record_result=False,  # el texto copiado puede ser privado
        )
        self.integraciones = cargar_integraciones()
        self.plugins = GestorPlugins()
        self.plugins.cargar(self.integraciones.plugins, self.tool_registry)
        self.mcp = GestorMCP(self.integraciones.servidores_mcp, core.APP_DIR / "logs")
        jarvis_health.registrar_proveedor(self._integration_checks)
        if historial:
            for role, content, momento in historial:
                self._add_message(role, content, momento=momento)
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
        self.health_watcher = jarvis_hardware.VigilanteSalud(
            self.events, discos=jarvis_system_info.listar_discos,
            memoria=lambda: psutil.virtual_memory().percent,
        )
        self.health_watcher.start()
        self.games_watcher = jarvis_games_watch.VigilanteJuegos(self.events)
        self.games_watcher.start()
        reiniciar_docker = lambda: core.iniciar_servicios_docker(espera=120)  # noqa: E731
        self.services_watcher = jarvis_servicios.VigilanteServicios(self.events, [
            jarvis_servicios.Servicio(
                "la voz a texto",
                lambda: jarvis_health.comprobar_puerto("STT", "127.0.0.1", jarvis_health.STT_PORT).ok,
                "Mientras tanto, si la tarjeta gráfica está libre, sigo entendiéndote con ella.",
                reiniciar_docker),
            jarvis_servicios.Servicio(
                "la voz de Jarvis",
                lambda: jarvis_health.comprobar_puerto("TTS", core.TTS_HOST, core.TTS_PORT).ok,
                "Respondo solo por escrito hasta que vuelva; revisa que Docker Desktop esté abierto.",
                reiniciar_docker),
            jarvis_servicios.Servicio(
                "Ollama (la IA)",
                lambda: jarvis_health.comprobar_ollama().ok,
                "Las órdenes directas funcionan igual; abre la aplicación de Ollama para las preguntas."),
        ])
        self.services_watcher.start()
        self._gpu_profile_enabled = self._gpu_profile_preferido()
        self.gpu_watcher = jarvis_gpu_tuning.VigilanteGPU(
            self.events, lambda: self._gpu_profile_enabled, core.obtener_preferencia, core.guardar_preferencia
        )
        self.gpu_watcher.start()
        self._refresh_gpu_profile_button()
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
        self.root.after(20000, self._check_morning_brief)
        self.root.after(60000, self._check_tv_connection)
        try:
            # Un aviso de cierre que quedó de antes (Jarvis ya estaba cerrado) no cuenta.
            (core.APP_DIR / self.SOLICITUD_CIERRE).unlink(missing_ok=True)
        except OSError:
            logging.exception("No se pudo borrar una solicitud de cierre antigua.")
        self.root.after(2000, self._check_close_request)
        self._dashboard_running = False
        self.root.after(1500, self._schedule_dashboard)
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

    DASHBOARD_MS = 5000

    def _refresh_home_now(self) -> None:
        """Lee los datos del panel en un hilo (si la página está a la vista)."""
        if self._closing or getattr(self, "_dashboard_running", False):
            return
        try:
            if self._current_page != "inicio" or self.root.state() in ("withdrawn", "iconic"):
                return
        except tk.TclError:
            return
        self._dashboard_running = True
        vigilante = getattr(self, "health_watcher", None)
        salud = vigilante.ultimo if vigilante is not None else None

        def recolectar() -> None:
            try:
                datos = jarvis_dashboard.recolectar(salud)
            except Exception:
                logging.exception("No se pudieron reunir los datos del panel de inicio.")
                datos = jarvis_dashboard.DatosPanel()
            self.events.put(("dashboard_data", datos))

        threading.Thread(target=recolectar, name="JarvisPanel", daemon=True).start()

    def _schedule_dashboard(self) -> None:
        if self._closing:
            return
        self._refresh_home_now()
        self.root.after(self.DASHBOARD_MS, self._schedule_dashboard)

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
        sonido opcional y voz. No cambia el estado ocupado/libre de la app.

        Jugando (modo juego con silencio), los avisos no urgentes solo quedan en el chat y se
        resumen al cerrar el juego; las alarmas y los de temperatura suenan igual."""
        if getattr(self, "_game_quiet", False) is True and not alarm:
            self._quiet_message(text)
            self._quiet_backlog = [*getattr(self, "_quiet_backlog", []), text]
            return
        self._add_message("assistant", text, aviso="alarm" if alarm else "info")
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
            # Al arrancar, los avisos (p. ej. disco casi lleno) llegaban antes de que el servidor
            # de voz estuviera listo y fallaban: se espera a que termine de iniciar.
            listo = getattr(self, "_docker_start_finished", None)
            if listo is not None:
                listo.wait(timeout=120)
            try:
                audio = core.generar_audio_tts(text)
            except Exception:
                logging.exception("No se pudo generar el audio del aviso.")
                return
            self.events.put(("announce_audio", audio))

        threading.Thread(target=sonar_y_hablar, name="JarvisAviso", daemon=True).start()

    # --- Resumen «Buenos días» ----------------------------------------------

    # --- Archivos por voz ---------------------------------------------------------

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
            core.precalentar_stt()  # respaldo si la voz en la GPU no está disponible
        finally:
            self._docker_start_finished.set()

    def _startup_checks_worker(self) -> None:
        """Comprueba Ollama y limpia respuestas antiguas sin bloquear el arranque."""
        try:
            ollama = jarvis_health.comprobar_ollama()
            if not ollama.ok:
                logging.warning("Ollama no disponible al iniciar: %s", ollama.detalle)
                self.events.put(("ollama_unavailable", ollama.detalle))
            else:
                self._preload_llm("arranque")
        except Exception:
            logging.exception("No se pudo comprobar Ollama al iniciar.")
        try:
            import jarvis_stt_gpu

            if jarvis_games_watch.juegos_abiertos(jarvis_games_watch.carpetas_de_juegos()):
                # Jarvis arrancó con un juego abierto (el vigilante no avisa «al abrir»): la
                # memoria de video queda para el juego hasta que se cierre.
                jarvis_stt_gpu.liberar()
                self._game_quiet = self._game_quiet_enabled()
            else:
                jarvis_stt_gpu.cargar(core.CARPETA_STT_GPU)  # ~3 s; así la primera orden no espera
        except Exception:
            logging.exception("No se pudo preparar la voz a texto en la GPU.")
        try:
            self.tv.conectar()  # si está vinculada; se reconecta sola si la tele se apaga
        except Exception:
            logging.exception("No se pudo conectar con la tele.")
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

    # --- Modo juego -------------------------------------------------------------

    _PREFERENCIA_AVISOS_DISCO = "avisos_disco"

    def _first_disk_alert_today(self, clave: str) -> bool:
        """El vigilante avisa de cada disco una vez al día, pero lo olvidaba al reiniciar Jarvis
        (el aviso salía en cada arranque): se recuerda el día en las preferencias."""
        if not clave:
            return True
        hoy = datetime.now().date().isoformat()
        try:
            avisados = json.loads(core.obtener_preferencia(self._PREFERENCIA_AVISOS_DISCO) or "{}")
            if not isinstance(avisados, dict):
                avisados = {}
            if avisados.get(clave) == hoy:
                return False
            avisados = {k: v for k, v in avisados.items() if v == hoy}
            avisados[clave] = hoy
            core.guardar_preferencia(self._PREFERENCIA_AVISOS_DISCO, json.dumps(avisados))
        except Exception:
            logging.exception("No se pudo recordar el aviso de disco.")
        return True

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

    # --- Respuesta en vivo (texto y voz mientras el modelo escribe) -------------

    # --- Clic y escritura en pantalla ------------------------------------------

    # --- Comandos personalizados (rutinas con disparador «frase») -----------

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
                    elif event[0] == "cleanup_start":
                        # Como «system_query»: la transcripción ya mostró la frase y dejó
                        # a Jarvis «ocupado»; se libera antes de empezar la limpieza.
                        self._set_idle()
                        self._start_cleanup(event[1], event[2])
                    elif event[0] == "cleanup_preview":
                        self._show_cleanup_preview(*event[1:])
                    elif event[0] == "cleanup_decision":
                        self._on_cleanup_decision(*event[1:])
                    elif event[0] == "voice_sample":
                        self._play_audio(event[1])
                    elif event[0] == "voice_sample_error":
                        self._set_status("NO PUDE PROBAR LA VOZ", ERROR)
                        self._add_message("assistant", event[1])
                    elif event[0] == "mobile_text":
                        self._on_mobile_text(event[1])
                    elif event[0] == "mobile_audio":
                        self._on_wakeword_command(event[1])
                    elif event[0] == "mobile_answer":
                        respuestas = self._mobile_callbacks.pop(event[1], None)
                        if respuestas is not None:
                            respuestas[0 if event[2] else 1]()
                    elif event[0] == "mobile_cancel":
                        self._cancel_current_operation()
                    elif event[0] == "games_list":
                        self._render_games(event[1])
                    elif event[0] == "mobile_screen":
                        self._show_screen_badge()
                    elif event[0] == "mobile_control_request":
                        self._on_mobile_control_request()
                    elif event[0] == "mobile_control_changed":
                        self._refresh_screen_badge()
                    elif event[0] == "mobile_file_received":
                        self._add_message("assistant", f"📥 Recibí «{event[1]}» del celular "
                                          f"({jarvis_archivos.tamano_legible(event[2])}). Está en Descargas.")
                    elif event[0] == "mobile_shared":
                        if event[1] == "link":
                            self._add_message("assistant", f"🔗 Abrí en la PC el link que compartiste: {event[2]}")
                        else:
                            self._add_message("assistant", "📋 Copié en el portapapeles de la PC el texto que "
                                                           "compartiste desde el celular.")
                    elif event[0] == "mobile_vision":
                        self._start_mobile_vision(event[1], event[2], event[3])
                    elif event[0] == "mobile_paired":
                        self.mobile_code.configure(text="✓ Celular vinculado")
                        self._refresh_mobile_settings()
                    elif event[0] == "gpu_profile_applied":
                        self._add_message("assistant", "🛠 " + event[1])
                    elif event[0] == "gpu_profile_warning":
                        self._announce(event[1], alarm=True)
                    elif event[0] == "health_alert":
                        self._announce(event[1], alarm=True)
                    elif event[0] == "disk_alert":
                        if self._first_disk_alert_today(event[2] if len(event) > 2 else ""):
                            self._announce(event[1])
                    elif event[0] in ("health_notice", "service_alert", "service_recovered"):
                        self._announce(event[1])
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
                    elif event[0] == "tv_state":
                        self._refresh_tv_views()
                    elif event[0] in ("tv_info", "tv_error"):
                        self._refresh_tv_views()
                        self._set_status(event[1][:70].upper(), ERROR if event[0] == "tv_error" else CYAN)
                        estado = getattr(self, "tv_settings_hint", None)
                        if estado is not None:
                            estado.configure(text=event[1], fg=ERROR if event[0] == "tv_error" else MUTED)
                    elif event[0] == "game_mode_info":
                        self._quiet_message(event[1])
                    elif event[0] == "show_commands_help":
                        self._open_commands_help(event[1])
                    elif event[0] in ("game_started", "game_stopped"):
                        if event[0] == "game_started":
                            self._on_game_started(event[1])
                        else:
                            self._on_game_stopped()
                        self._handle_device_event(
                            jarvis_routines.DISPARADOR_JUEGO_ABIERTO if event[0] == "game_started"
                            else jarvis_routines.DISPARADOR_JUEGO_CERRADO,
                            detalle=event[1],
                        )
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
                            elif action == "set_voice":
                                result = self._set_voice(value)
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
                    elif event[0] == "dashboard_data":
                        self._dashboard_running = False
                        self._render_home(event[1])
                    elif event[0] == "docker_services_ready":
                        self._voice_services_ready = True
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
                        if event[1].origen == "celular" and event[1].exitosa:
                            # Deslizadores y botones de la pestaña Música: ya se ven en el
                            # celular y quedan en el registro; en el chat solo serían ruido.
                            continue
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

    # --- Limpieza ------------------------------------------------------------------

    # --- Palabra de activación «Hey Jarvis» -------------------------------------

    # --- Inicio con Windows ---------------------------------------------------

    # --- Undervolt de la GPU (jarvis_gpu_tuning) -----------------------------------------

    # --- App del celular -------------------------------------------------------------

    # --- Control del mouse y el teclado desde el celular -------------------------------

    # --- «¿Qué es esto?» desde el celular ------------------------------------------------

    # --- Visión de pantalla (roadmap punto 6) ------------------------------------

    def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._resolve_plan_approval(False)
        self._close_compact_window()
        aviso = getattr(self, "floating", None)
        if aviso is not None:
            aviso.destruir()
        movil = getattr(self, "mobile", None)
        if movil is not None:
            movil.detener()
        self._set_status("DETENIENDO SERVICIOS DOCKER…", CYAN)
        self.global_hotkey_listener.stop()
        self.global_hotkey_listener.join(timeout=1)
        self.headphones_watcher.stop()
        self.battery_watcher.stop()
        self.reminder_watcher.stop()
        self.health_watcher.stop()
        self.fps_meter.detener()
        self.games_watcher.stop()
        self.services_watcher.stop()
        self.tv.detener()
        self.gpu_watcher.stop()
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
