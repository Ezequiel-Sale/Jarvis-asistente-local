"""Voz: micrófono (F9), palabra de activación, respuesta en vivo, interrupción y voces de Piper.

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Los métodos usan
los atributos de la app (``self.root``, ``self.events``, ``self.tool_registry``…).
"""

import audio_capture
import jarvis_audio_devices
import jarvis_cards
import jarvis_core as core
import jarvis_hablante
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



PISTA_MINIMA_VOZ = 0.8  # s de voz para darle a Whisper la pista de palabras
# Voz dudosa [2026-11-17]: «¿qué consume más memoria?» se descartó dos veces en silencio (parecido 0,28 y
# 0,19 con 0,3–0,5 s de voz medida; tus frases aceptadas ese día: 0,36–0,52, las voces ajenas de la
# tele: −0,05 a 0,17). Entre este piso y el umbral se transcribe igual: si es una orden que Jarvis
# conoce, se hace; si no, se avisa en vez de quedarse callado.
PARECIDO_DUDOSO = 0.18


def palabra_suelta_desconocida(texto: str, orden_directa) -> bool:
    """¿Es una sola palabra que no es una orden conocida (sección, orden directa o frase de
    una rutina)? En la escucha continua eso es ruido mal transcripto: no va a la IA."""
    import re

    import jarvis_commands

    if len(re.findall(r"\w+", texto)) != 1:
        return False
    if jarvis_commands.detectar_navegacion(texto) or orden_directa(texto):
        return False
    try:
        return jarvis_routines.buscar_rutina_por_frase(texto) is None
    except Exception:
        return False

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
            target=self._voice_worker,  # F9: decides tú cuándo grabar; «Solo mi voz» no filtra
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

    def _wakeword_voice_worker(self, audio_path: str, started: float, conversation_id: str,
                               plan_approval: bool, approval_kind: str, cancel_token) -> None:
        """Orden tras «Hey Jarvis»: si «Solo mi voz» la rechaza, no se transcribe."""
        if self._speaker_rejected(audio_path):
            audio_capture.limpiar_audio(audio_path)
            self.events.put(("speaker_rejected", plan_approval))
            return
        self._voice_worker(audio_path, started, conversation_id, plan_approval, approval_kind, cancel_token)

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

    INTERVALO_EN_VIVO_MS = 120  # redibujar el formato como mucho ~8 veces por segundo

    def _show_live_answer(self, trozo: str | None) -> None:
        """Burbuja provisional que crece mientras el modelo escribe, ya con formato (2026-11-20, pedido
        del usuario: antes se veía el markdown crudo, con ** y #, hasta que terminaba de hablar)."""
        if trozo is None:  # nueva ronda del modelo: empezar de cero
            self._live_text = ""
            if self._live_row is not None and self._live_row.winfo_exists():
                self._pintar_en_vivo()
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
            self._live_bubble = burbuja
            self._pintar_en_vivo()
        elif not getattr(self, "_live_pendiente", False):
            self._live_pendiente = True
            self.root.after(self.INTERVALO_EN_VIVO_MS, self._pintar_en_vivo)
        self._notify_mobile_live(self._live_text)

    def _pintar_en_vivo(self) -> None:
        """Reescribe el texto con formato de la burbuja en vivo, en la misma caja (crear una nueva
        en cada trozo la hacía saltar de alto)."""
        import jarvis_markdown

        self._live_pendiente = False
        burbuja = getattr(self, "_live_bubble", None)
        if self._live_row is None or burbuja is None or not burbuja.winfo_exists():
            return
        texto = jarvis_markdown.cerrar_parcial(self._live_text.strip()) or "…"
        caja = self._live_label if self._live_label is not None and self._live_label.winfo_exists() else None
        if caja is None:
            self._live_label = self._rich_text(burbuja, texto, PANEL_LIGHT)
            self._live_label.pack(anchor="w")
        else:
            self._rich_text(burbuja, texto, PANEL_LIGHT, caja=caja)
        self._scroll_chat_to_bottom()  # solo si estabas al final (``_chat_following``)

    def _clear_live_answer(self, *, keep_text: bool = False) -> None:
        if self._live_row is not None and self._live_row.winfo_exists():
            self._live_row.destroy()
        self._live_row = None
        self._live_label = None
        self._live_bubble = None
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
        self._refresh_continuous_button()

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
        if self._closing or self.recorder is not None or getattr(self, "_enrolling", False):
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
            continua=self._continuous_listening,
        )
        self.wakeword_detector.start()
        return True

    def _stop_wakeword(self) -> None:
        detector = getattr(self, "wakeword_detector", None)
        self.wakeword_detector = None
        if detector is not None:
            detector.stop()

    # --- Escucha continua [2026-11-16] --------------------------------------------
    _PREFERENCIA_CONTINUA = "escucha_continua"

    def _continuous_preferred(self) -> bool:
        try:
            return core.obtener_preferencia(self._PREFERENCIA_CONTINUA) == "1"
        except Exception:
            logging.exception("No se pudo leer la preferencia de la escucha continua.")
            return False

    def _continuous_listening(self) -> bool:
        """El detector lo consulta desde su hilo (solo lee un atributo)."""
        return bool(getattr(self, "_continuous_enabled", False))

    def _refresh_continuous_button(self) -> None:
        boton = getattr(self, "continuous_button", None)
        if boton is None:
            return
        activo = self._continuous_listening() and self._wakeword_active()
        boton.configure(text="ESCUCHA CONTINUA ON" if activo else "ESCUCHA CONTINUA OFF",
                        fg=CYAN if activo else MUTED)

    def _toggle_continuous_button(self) -> None:
        activar = not (self._continuous_listening() and self._wakeword_active())
        self._execute_continuous_toggle(
            activar, "Botón: activar la escucha continua" if activar else "Botón: desactivar la escucha continua"
        )

    def _execute_continuous_toggle(self, enabled: bool, command: str) -> None:
        logging.info("Escucha continua %s.", "activada" if enabled else "desactivada")
        self._continuous_enabled = enabled
        try:
            core.guardar_preferencia(self._PREFERENCIA_CONTINUA, "1" if enabled else "0")
            if enabled:
                core.guardar_preferencia(self._PREFERENCIA_WAKEWORD, "1")
        except Exception:
            logging.exception("No se pudo guardar la preferencia de la escucha continua.")
        if enabled and not self._start_wakeword():
            respuesta = "No pude activar la escucha: falta openWakeWord. Sigue disponible F9."
        elif enabled:
            respuesta = ("Escucha continua activada: con el micrófono abierto, todo lo que digas es una orden. "
                         "Silencia el micrófono para hablar sin que te escuche.")
        elif self._wakeword_active():
            respuesta = "Escucha continua desactivada. Vuelvo a esperar «Hey Jarvis»."
        else:
            respuesta = "Escucha continua desactivada. Usa F9 para hablar."
        try:
            core.guardar_turno(self.conversation_id, command, respuesta)
        except Exception:
            logging.exception("No se pudo guardar el cambio de escucha en memoria.")
        self._add_message("assistant", respuesta)
        self._refresh_wakeword_indicator()
        self._set_status(self._listening_status(), CYAN)

    def _listening_status(self) -> str:
        if not self._wakeword_active():
            return "F9 PARA HABLAR"
        return "TE ESCUCHO SIEMPRE" if self._continuous_listening() else "ESCUCHANDO «HEY JARVIS»"

    def _on_continuous_utterance(self, audio_path: str, cortada: bool = False) -> None:
        """Una frase de la escucha continua: se transcribe aparte, sin «ocupar» a Jarvis.

        ``cortada``: la grabación llegó al tope con la voz todavía sonando; lo que se transcriba
        queda en espera y se une al trozo siguiente, que no pasa por «solo mi voz» (es la misma
        frase, sin silencio en el medio; 2026-11-20 se descartaba como «voz ajena»)."""
        hablando = self._jarvis_speaking()
        if (self._closing or self.recorder is not None or self._pending_plan_reply is not None
                or getattr(self, "_continuous_transcribing", False)
                or not self._continuous_listening() or (self.busy and not hablando)):
            audio_capture.limpiar_audio(audio_path)
            return
        self._continuous_transcribing = True
        confiar = bool(getattr(self, "_continuous_partial", None)) and getattr(self, "_continuous_partial_cut", False) is True
        self._continuous_cut = cortada
        self._preload_llm("escucha continua")
        threading.Thread(target=self._continuous_worker, args=(audio_path, hablando, confiar),
                         name="JarvisEscuchaContinua", daemon=True).start()

    def _continuous_worker(self, audio_path: str, hablando: bool, confiar: bool = False) -> None:
        from jarvis_actions import ejecutar_con_limites
        from jarvis_commands import frase_continua, limpiar_transcripcion_voz

        decision = None
        recorte = None
        try:
            # «Solo mi voz»: si no es tu voz, ni se transcribe (decision queda en None). En un trozo
            # largo con la tele de fondo se busca el tramo con tu voz y se transcribe solo ese.
            recorte = audio_path if confiar else self._speaker_clip(audio_path)
            if recorte is not None:
                audio_path_original, audio_path = audio_path, recorte
                try:  # cuánta voz hubo de verdad: con 0,4 s no caben 456 caracteres
                    segundos_voz = jarvis_hablante.solo_voz(jarvis_hablante.leer_wav(audio_path))[1]
                except Exception:
                    segundos_voz = None
                # Con muy poca voz, sin pista: Whisper la copiaba («Maximiza agranda minimiza»,
                # «Rápido Brave YouTube…») a partir de un ruido (2026-11-16).
                pista = segundos_voz is None or segundos_voz >= PISTA_MINIMA_VOZ
                texto = limpiar_transcripcion_voz(ejecutar_con_limites(
                    lambda: core.procesar_stt(audio_path, pista=pista), timeout=core.STT_TIMEOUT + 10,
                    descripcion="transcripción de voz",
                ))
                decision = frase_continua(texto, hablando, segundos_voz)
                logging.info("Escucha continua: %s (%s caracteres).",
                             decision[0] if decision else "ignorada", len(texto))
            elif self._voice_unsure():
                decision = self._unsure_voice_decision(audio_path, hablando)
        except Exception:
            logging.exception("No se pudo transcribir una frase de la escucha continua.")
        finally:
            audio_capture.limpiar_audio(audio_path)
            if recorte is not None and recorte != audio_path_original:
                audio_capture.limpiar_audio(audio_path_original)
        self.events.put(("continuous_result", decision))

    def _voice_unsure(self) -> bool:
        """¿La última frase rechazada quedó en la franja dudosa (quizás eras vos con poca voz)?"""
        resultado = getattr(self, "_last_speaker_check", None)
        if resultado is None or resultado.aceptada or resultado.similitud is None:
            return False
        return resultado.similitud >= PARECIDO_DUDOSO

    def _unsure_voice_decision(self, audio_path: str, hablando: bool) -> tuple[str, str] | None:
        """Hilo de la escucha continua: transcribe la frase dudosa; si es una orden que Jarvis conoce
        se acepta, si no se avisa (``speaker_unsure``) y se descarta."""
        from jarvis_actions import ejecutar_con_limites
        from jarvis_commands import frase_continua, limpiar_transcripcion_voz

        resultado = self._last_speaker_check
        texto = limpiar_transcripcion_voz(ejecutar_con_limites(
            lambda: core.procesar_stt(audio_path, pista=False), timeout=core.STT_TIMEOUT + 10,
            descripcion="transcripción de voz",
        ))
        if texto and self._known_command(texto):
            logging.info("Voz dudosa (parecido %.2f) pero es una orden que Jarvis conoce: se acepta.",
                         resultado.similitud)
            return frase_continua(texto, hablando, resultado.segundos_voz)
        logging.info("Voz dudosa (parecido %.2f) y no es una orden conocida: se descarta y se avisa.",
                     resultado.similitud)
        self.events.put(("speaker_unsure",))
        return None

    def _known_command(self, texto: str) -> bool:
        """Una orden que Jarvis resuelve solo (no una charla para la IA): directas, consultas del
        equipo, fecha y hora, batería, visión, abrir un programa, rutinas."""
        from jarvis_commands import (detectar_comando_juego, detectar_consulta_bateria,
                                     detectar_consulta_fecha_hora, detectar_consulta_sistema, detectar_vision)

        try:
            return bool(self._is_known_order(texto) or detectar_consulta_sistema(texto)
                        or detectar_consulta_fecha_hora(texto) or detectar_consulta_bateria(texto)
                        or detectar_vision(texto) or detectar_comando_juego(texto))
        except Exception:
            logging.debug("No se pudo revisar si la frase dudosa es una orden.", exc_info=True)
            return False

    def _on_continuous_result(self, decision: tuple[str, str] | None) -> None:
        self._continuous_transcribing = False
        if decision is None:
            return
        accion, texto = decision
        if self._jarvis_speaking():
            self._interrupt_speech()
        if accion != "orden":
            return
        from jarvis_commands import frase_incompleta

        anterior = getattr(self, "_continuous_partial", None)
        if anterior:
            # La continuación de una frase cortada por una pausa: «… de» + «fútbol».
            texto = f"{anterior} {texto}"
            self._continuous_partial = None
            self._continuous_partial_cut = False
        elif palabra_suelta_desconocida(texto, self._direct_tool_for):
            # «Yerda» (2026-11-16): una palabra suelta que no es ninguna orden no va a la IA.
            logging.info("Escucha continua: palabra suelta que no es una orden; se ignora.")
            return
        if getattr(self, "_continuous_cut", False) is True:
            # Cortada por el tope de la grabación: el resto ya se está grabando; se espera hasta otro tope.
            self._continuous_cut = False
            self._continuous_partial = texto
            self._continuous_partial_cut = True
            self._continuous_partial_token = token = object()
            self._set_status("TE ESCUCHO…", CYAN)
            espera = self.ESPERA_CONTINUACION + jarvis_wakeword.MAXIMO_FRASE
            self.root.after(int(espera * 1000), lambda: self._flush_continuous_partial(token))
            return
        if anterior:
            pass
        elif frase_incompleta(texto):
            # Termina en «de», «la», «que»…: seguramente hiciste una pausa; espero el resto.
            self._continuous_partial = texto
            self._continuous_partial_token = token = object()
            self._set_status("TE ESCUCHO…", CYAN)
            self.root.after(int(self.ESPERA_CONTINUACION * 1000), lambda: self._flush_continuous_partial(token))
            return
        self._run_continuous_command(texto)

    ESPERA_CONTINUACION = 4.0  # s para seguir una frase que quedó colgada

    def _flush_continuous_partial(self, token) -> None:
        """No llegó la continuación: se hace la orden tal como quedó."""
        if getattr(self, "_continuous_partial_token", None) is not token or not getattr(self, "_continuous_partial", None):
            return
        texto, self._continuous_partial = self._continuous_partial, None
        self._continuous_partial_cut = False
        self._run_continuous_command(texto)

    def _run_continuous_command(self, texto: str) -> None:
        if (self.busy and self._pending_plan_reply is None and not self._closing
                and time.monotonic() < getattr(self, "_interrupt_deadline", 0.0)):
            # Se acaba de interrumpir una respuesta: esperar a que termine de cancelarse.
            self.root.after(250, lambda: self._run_continuous_command(texto))
            return
        if self._closing or self.recorder is not None or self.busy or self._pending_plan_reply is not None:
            return
        self._start_busy("PROCESANDO…")
        threading.Thread(
            target=self._voice_worker,
            args=(None, time.perf_counter(), self.conversation_id, False, "plan", self.cancel_token),
            kwargs={"transcript": texto},
            daemon=True,
        ).start()

    # --- «Solo mi voz» [2026-11-16] ---------------------------------------------------
    _PREFERENCIA_SOLO_MI_VOZ = "solo_mi_voz"
    _PREFERENCIA_EXIGENCIA = "exigencia_voz"

    def _init_speaker_filter(self) -> None:
        self.speaker_verifier = jarvis_hablante.Verificador()
        self._enrolling = False
        try:
            activo = core.obtener_preferencia(self._PREFERENCIA_SOLO_MI_VOZ) == "1"
            exigencia = core.obtener_preferencia(self._PREFERENCIA_EXIGENCIA)
        except Exception:
            logging.exception("No se pudieron leer las preferencias de «Solo mi voz».")
            activo, exigencia = False, None
        self._speaker_strictness = exigencia if exigencia in jarvis_hablante.EXIGENCIAS \
            else jarvis_hablante.EXIGENCIA_POR_DEFECTO
        self._speaker_filter = activo and self.speaker_verifier.registrada()

    def _speaker_rejected(self, audio_path: str, corta_pasa: bool = False) -> bool:
        """Hilos de voz: True si «Solo mi voz» está activo y no es tu voz. Ante un error, deja pasar.
        ``corta_pasa``: un sonido demasiado corto para reconocerlo se acepta (respuestas «sí/no»)."""
        if not getattr(self, "_speaker_filter", False):
            return False
        try:
            resultado = self.speaker_verifier.verificar(audio_path, self._speaker_strictness,
                                                        corta_pasa=corta_pasa)
        except Exception:
            logging.exception("No se pudo comprobar de quién es la voz; se acepta la frase.")
            return False
        self._last_speaker_check = resultado
        if resultado.similitud is not None:
            logging.info("Voz %s (parecido %.2f, %.1f s de voz, exigencia %s).",
                         "reconocida" if resultado.aceptada else "ajena: ignorada",
                         resultado.similitud, resultado.segundos_voz, self._speaker_strictness)
        elif not resultado.aceptada:
            logging.info("Sonido demasiado corto para reconocer la voz (%.2f s): ignorado.", resultado.segundos_voz)
        return not resultado.aceptada

    TRAMO_DESDE = 2.5  # s de voz: con menos, el trozo no es «largo con ruido de fondo»

    def _speaker_clip(self, audio_path: str) -> str | None:
        """Escucha continua: la ruta a transcribir si es tu voz (la misma, o un recorte con solo tu
        tramo cuando el trozo largo mezcla la tele o un directo); None si no es tu voz."""
        if not getattr(self, "_speaker_filter", False):
            return audio_path
        if not self._speaker_rejected(audio_path):
            return audio_path
        try:
            muestras = jarvis_hablante.leer_wav(audio_path)
            if jarvis_hablante.solo_voz(muestras)[1] < self.TRAMO_DESDE:
                return None
            tramo = self.speaker_verifier.buscar_tramo(muestras, self._speaker_strictness)
        except Exception:
            logging.exception("No se pudo buscar tu voz dentro del trozo.")
            return None
        if tramo is None:
            return None
        inicio, fin, parecido = tramo
        import tempfile

        descriptor, ruta = tempfile.mkstemp(suffix=".wav", prefix="jarvis_tramo_")
        os.close(descriptor)
        jarvis_hablante.guardar_wav(muestras[inicio:fin], ruta)
        logging.info("Tu voz dentro de un trozo con sonido de fondo: tramo de %.1f s a %.1f s (parecido %.2f).",
                     inicio / jarvis_hablante.FRECUENCIA, fin / jarvis_hablante.FRECUENCIA, parecido)
        return ruta

    def _on_speaker_rejected(self, plan_approval: bool) -> None:
        if plan_approval:
            self.events.put(("plan_voice_unrecognized",))
            return
        if self.busy:
            self._set_idle()
        self._set_status("NO RECONOCÍ TU VOZ", MUTED)

    def _refresh_speaker_buttons(self) -> None:
        activo = getattr(self, "_speaker_filter", False)
        registrada = getattr(self, "speaker_verifier", None) is not None and self.speaker_verifier.registrada()
        boton = getattr(self, "speaker_button", None)
        if boton is not None:
            boton.configure(text="SOLO MI VOZ: SÍ" if activo else "SOLO MI VOZ: NO", fg=CYAN if activo else MUTED)
        boton = getattr(self, "speaker_enroll_button", None)
        if boton is not None:
            boton.configure(text="🎙  VOLVER A REGISTRAR MI VOZ" if registrada else "🎙  REGISTRAR MI VOZ")
        boton = getattr(self, "speaker_strictness_button", None)
        if boton is not None:
            nombre = jarvis_hablante.NOMBRES_EXIGENCIA.get(getattr(self, "_speaker_strictness", ""), "MEDIA")
            boton.configure(text=f"EXIGENCIA: {nombre}")

    def _toggle_speaker_filter(self) -> None:
        if getattr(self, "_speaker_filter", False):
            self._set_speaker_filter(False)
            self._add_message("assistant", "«Solo mi voz» desactivado: vuelvo a aceptar órdenes de cualquier voz.")
            return
        if not self.speaker_verifier.modelo_disponible():
            self._add_message("assistant", "Falta el modelo de reconocimiento de voz (carpeta speaker-onnx junto a "
                                           "Jarvis); sin él no puedo reconocer tu voz.")
            return
        if not self.speaker_verifier.registrada():
            self._open_voice_enrollment(activar_al_terminar=True)
            return
        self._set_speaker_filter(True)
        self._add_message("assistant", "«Solo mi voz» activado: solo obedezco órdenes dichas con tu voz.")

    def _set_speaker_filter(self, activo: bool) -> None:
        self._speaker_filter = activo
        try:
            core.guardar_preferencia(self._PREFERENCIA_SOLO_MI_VOZ, "1" if activo else "0")
        except Exception:
            logging.exception("No se pudo guardar la preferencia de «Solo mi voz».")
        logging.info("«Solo mi voz» %s.", "activado" if activo else "desactivado")
        self._refresh_speaker_buttons()

    def _cycle_speaker_strictness(self) -> None:
        orden = list(jarvis_hablante.EXIGENCIAS)
        siguiente = orden[(orden.index(self._speaker_strictness) + 1) % len(orden)]
        self._speaker_strictness = siguiente
        try:
            core.guardar_preferencia(self._PREFERENCIA_EXIGENCIA, siguiente)
        except Exception:
            logging.exception("No se pudo guardar la exigencia de «Solo mi voz».")
        self._refresh_speaker_buttons()

    def _open_voice_enrollment(self, activar_al_terminar: bool = False) -> None:
        import jarvis_registro_voz

        if not self.speaker_verifier.modelo_disponible():
            self._add_message("assistant", "Falta el modelo de reconocimiento de voz (carpeta speaker-onnx junto a "
                                           "Jarvis).")
            return
        actual = getattr(self, "_voice_enrollment", None)
        if actual is not None and actual.ventana.winfo_exists():
            actual.ventana.lift()
            return

        def al_abrir(abierta: bool) -> None:
            self._enrolling = abierta

        def al_terminar(huellas: list) -> None:
            try:
                coherencia = self.speaker_verifier.guardar_huella(huellas)
            except Exception as error:
                logging.exception("No se pudo guardar la huella de voz.")
                self._add_message("assistant", f"No pude guardar tu voz: {error}")
                return
            logging.info("Voz registrada: %s frases, coherencia %.2f.", len(huellas), coherencia)
            if activar_al_terminar or getattr(self, "_speaker_filter", False):
                self._set_speaker_filter(True)
            self._refresh_speaker_buttons()
            aviso = (" Las frases salieron algo distintas entre sí; si te rechaza a menudo, baja la exigencia "
                     "o regístrate de nuevo en un lugar más silencioso." if coherencia < 0.6 else "")
            self._add_message("assistant", "Listo, ya reconozco tu voz. «Solo mi voz» está "
                              + ("activado." if self._speaker_filter else "desactivado; actívalo en Ajustes.")
                              + aviso)

        self._voice_enrollment = jarvis_registro_voz.RegistroVoz(
            self.root, self.speaker_verifier, al_terminar, al_abrir)

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
            if self._start_wakeword() and self._continuous_listening():
                respuesta = "Escucha activada: con la escucha continua, todo lo que digas es una orden."
            elif self.wakeword_detector is not None:
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
        self._set_status(self._listening_status(), CYAN)

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

    def _on_wakeword_command(self, audio_path: str, verificar_voz: bool = True) -> None:
        """Procesa la orden dicha tras «Hey Jarvis» como una grabación de F9.

        ``verificar_voz``: pasa por «Solo mi voz»; el audio del celular no (otro micrófono, y el
        celular ya está vinculado con su token)."""
        plan_approval = self._pending_plan_reply is not None
        if (self.busy and not plan_approval and not self._closing
                and time.monotonic() < getattr(self, "_interrupt_deadline", 0.0)):
            # Se acaba de interrumpir una respuesta: esperar a que termine de cancelarse.
            self.root.after(250, lambda: self._on_wakeword_command(audio_path, verificar_voz))
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
            target=self._wakeword_voice_worker if verificar_voz else self._voice_worker,
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
