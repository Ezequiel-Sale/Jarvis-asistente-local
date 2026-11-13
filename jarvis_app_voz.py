"""Voz: micrófono (F9), palabra de activación, respuesta en vivo, interrupción y voces de Piper.

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Los métodos usan
los atributos de la app (``self.root``, ``self.events``, ``self.tool_registry``…).
"""

import audio_capture
import jarvis_audio_devices
import jarvis_cards
import jarvis_core as core
import jarvis_routines
import jarvis_voices
import jarvis_voz_stream
import jarvis_wakeword
import jarvis_windows
import logging
import os
import threading
import time
import tkinter as tk
from audio_capture import GrabadorAudio
from jarvis_theme import CYAN, ERROR, MUTED, PANEL, PANEL_LIGHT, TEXT, VIOLET


class VozMixin:
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
        if not self._recording_for_plan_approval:
            self._preload_llm("micrófono")  # carga mientras hablas
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
            self._notify_mobile_live("")
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
            self._bubble_header(burbuja, "JARVIS", PANEL_LIGHT, VIOLET, jarvis_cards.hora_mensaje())
            self._live_label = tk.Label(
                burbuja, text="", bg=PANEL_LIGHT, fg=TEXT, justify="left", anchor="w",
                wraplength=650, font=("Segoe UI", 11),
            )
            self._live_label.pack(anchor="w")
        self._live_label.configure(text=self._live_text.strip() or "…")
        self._scroll_chat_to_bottom()
        self._notify_mobile_live(self._live_text)

    def _clear_live_answer(self, *, keep_text: bool = False) -> None:
        if self._live_row is not None and self._live_row.winfo_exists():
            self._live_row.destroy()
        self._live_row = None
        self._live_label = None
        if not keep_text:
            self._live_text = ""
            self._notify_mobile_live(None)

    STT_VOCABULARY_FIXED = ("Jarvis", "Claude", "Brave", "YouTube", "Discord", "Spotify", "WhatsApp", "Steam")

    def _stt_vocabulary(self) -> list[str]:
        """Nombres para la pista de Whisper (hilo de la transcripción; se guarda 30 s)."""
        momento, nombres = self._stt_vocabulary_cache
        if time.monotonic() - momento < 30 and nombres:
            return nombres
        nombres = list(self.STT_VOCABULARY_FIXED)
        try:
            nombres += jarvis_windows.nombres_para_voz(jarvis_windows.listar_ventanas(), os.getpid())
        except Exception:
            logging.debug("No se pudieron leer las ventanas para la transcripción.", exc_info=True)
        try:
            # Las frases con que se activan («Modo gaming 2K») y los nombres de las rutinas.
            nombres += jarvis_routines.frases_de_rutinas()[:15]
            nombres += [rutina[1] for rutina in jarvis_routines.listar_rutinas()][:15]
        except Exception:
            logging.debug("No se pudieron leer las rutinas para la transcripción.", exc_info=True)
        nombres = list(dict.fromkeys(n.strip() for n in nombres if n and n.strip()))
        self._stt_vocabulary_cache = (time.monotonic(), nombres)
        return nombres

    def _tool_set_voice(self, voice: str) -> str:
        return self._request_tool_ui_action("set_voice", voice)

    def _set_voice(self, clave: str) -> str:
        """Hilo de Tk: cambia la voz, la guarda y dice una frase con ella."""
        voz = jarvis_voices.por_clave(clave)
        if not jarvis_voices.instalada(voz):
            raise FileNotFoundError(
                f"No tengo instalada la voz {voz.nombre} (falta {voz.modelo}.onnx en piper-voices)."
            )
        jarvis_voices.aplicar(voz)
        self._voice_key = voz.clave
        try:
            core.guardar_preferencia(jarvis_voices.PREFERENCIA, voz.clave)
        except Exception:
            logging.exception("No se pudo guardar la voz elegida.")
        self._refresh_voice_buttons()
        self._refresh_settings_info()
        self._test_voice(voz.clave)
        logging.info("Voz cambiada a %s.", voz.modelo)
        return f"Ahora hablo con la voz {voz.nombre} ({voz.descripcion})."

    def _test_voice(self, clave: str) -> None:
        """Dice la frase de prueba con esa voz (sin cambiar la elegida)."""
        voz = jarvis_voices.por_clave(clave)

        def sintetizar() -> None:
            try:
                ruta = core.generar_audio_tts(
                    jarvis_voices.FRASE_PRUEBA, f"prueba_voz_{voz.clave}", voz=voz.modelo, hablante=voz.hablante
                )
            except Exception as error:
                logging.exception("No se pudo probar la voz %s.", voz.modelo)
                self.events.put(("voice_sample_error", f"No pude probar la voz {voz.nombre}: {error}"))
                return
            self.events.put(("voice_sample", ruta))

        threading.Thread(target=sintetizar, name="JarvisPruebaVoz", daemon=True).start()

    def _tool_set_jarvis_audio(self, enabled: bool) -> str:
        return self._request_tool_ui_action("set_audio", enabled)

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

    def _jarvis_speaking(self) -> bool:
        """¿Está sonando la voz de Jarvis? (reproductor o respuesta en vivo). Sin Tk."""
        player = getattr(self, "player_thread", None)
        if player is not None and player.is_alive() and not getattr(self, "player_paused", False):
            return True
        hablador = getattr(self, "_live_speaker", None)
        return bool(hablador is not None and hablador.empezo and not hablador.cancelado.is_set())

    _CACHE_SALIDA = 3.0

    def _headphones_output(self) -> bool:
        """La salida predeterminada son los auriculares (consulta cacheada 3 s; sin Tk)."""
        ahora = time.monotonic()
        if ahora - getattr(self, "_salida_revisada", -1e9) >= self._CACHE_SALIDA:
            self._salida_revisada = ahora
            try:
                self._salida_auriculares = jarvis_audio_devices.es_auriculares(
                    jarvis_audio_devices.salida_predeterminada()
                )
            except Exception:
                logging.debug("No se pudo leer la salida de audio predeterminada.", exc_info=True)
                self._salida_auriculares = False
        return getattr(self, "_salida_auriculares", False)

    def _can_listen_for_wakeword(self) -> bool:
        """Se consulta desde el hilo del detector: solo lee atributos, sin Tk.

        Mientras Jarvis habla solo se escucha si la salida son los auriculares
        (por los altavoces el micrófono oiría su propia voz) y la interrupción está
        activada: así «Hey Jarvis» lo puede cortar.
        """
        if self._closing or self.recorder is not None:
            return False
        if self._jarvis_speaking():
            return getattr(self, "_interrupt_enabled", True) and self._headphones_output()
        if self.busy and self._pending_plan_reply is None:
            return False
        return True

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

    _PREFERENCIA_INTERRUMPIR = "interrumpir"

    def _refresh_interrupt_button(self) -> None:
        boton = getattr(self, "interrupt_button", None)
        if boton is not None:
            activo = getattr(self, "_interrupt_enabled", True)
            boton.configure(text="✋  INTERRUMPIR: SÍ" if activo else "✋  INTERRUMPIR: NO",
                            fg=CYAN if activo else MUTED)

    def _toggle_interrupt(self) -> None:
        self._interrupt_enabled = not getattr(self, "_interrupt_enabled", True)
        try:
            core.guardar_preferencia(self._PREFERENCIA_INTERRUMPIR, "1" if self._interrupt_enabled else "0")
        except Exception:
            logging.exception("No se pudo guardar la preferencia de interrupción.")
        self._refresh_interrupt_button()

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

    INTERRUPCION_ESPERA = 10.0

    def _interrupt_speech(self) -> None:
        """«Hey Jarvis» mientras habla: se calla y deja la respuesta en curso."""
        self._stop_player()
        hablador = getattr(self, "_live_speaker", None)
        if hablador is not None:
            hablador.cancelar()
        if self.busy and self._pending_plan_reply is None:
            self.cancel_token.cancel()
        self._interrupt_deadline = time.monotonic() + self.INTERRUPCION_ESPERA
        logging.info("Jarvis interrumpido con «Hey Jarvis».")

    def _on_wakeword_detected(self) -> None:
        self._preload_llm("palabra de activación")  # carga mientras hablas
        if self._jarvis_speaking():
            self._interrupt_speech()
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
        if (self.busy and not plan_approval and not self._closing
                and time.monotonic() < getattr(self, "_interrupt_deadline", 0.0)):
            # Se acaba de interrumpir una respuesta: esperar a que termine de cancelarse.
            self.root.after(250, lambda: self._on_wakeword_command(audio_path))
            return
        if self._closing or self.recorder is not None or (self.busy and not plan_approval):
            audio_capture.limpiar_audio(audio_path)
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
