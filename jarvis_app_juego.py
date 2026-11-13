"""Tarjeta gráfica y modo juego: precarga del modelo de lenguaje, liberar la GPU al jugar,
silencio de Jarvis y programas que se cierran y reabren.

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Los métodos usan
los atributos de la app (``self.root``, ``self.events``, ``self.tool_registry``…).
"""

import jarvis_agent
import jarvis_core as core
import jarvis_modo_juego
import logging
import threading
import time
from jarvis_theme import CYAN, MUTED


class JuegoMixin:
    # Entre dos precargas pedidas (al decir «Hey Jarvis», pulsar el micrófono o escribir).
    PRECARGA_INTERVALO = 60.0

    def _preload_llm(self, motivo: str) -> None:
        """Carga el modelo de lenguaje en segundo plano antes de que haga falta.

        Cargarlo tarda ~11 s; así se hace mientras hablas o escribes. No se precarga con
        un juego abierto (ocuparía ~10 GB de la GPU): si hace falta, se carga al preguntar.
        Nunca lanza: se llama al detectar «Hey Jarvis» y no puede cortar ese manejo."""
        try:
            ahora = time.monotonic()
            if ahora - getattr(self, "_last_llm_preload", -1e9) < self.PRECARGA_INTERVALO:
                return
            vigilante = getattr(self, "games_watcher", None)
            if vigilante is not None and vigilante.jugando:  # propiedad: conjunto de juegos abiertos
                return
            self._last_llm_preload = ahora
            logging.debug("Precarga del modelo de lenguaje (%s).", motivo)
            threading.Thread(target=jarvis_agent.precargar_modelo, name="JarvisPrecargaLLM", daemon=True).start()
        except Exception:
            logging.exception("No se pudo precargar el modelo de lenguaje.")

    def _free_gpu_for_game(self) -> None:
        """Al abrir un juego, saca de la tarjeta los modelos de Ollama (~10 GB) y la voz a texto
        (~1,5 GB). Mientras dura, la voz usa el servidor de siempre en el procesador."""
        self._last_llm_preload = -1e9

        def liberar() -> None:
            import jarvis_stt_gpu

            jarvis_stt_gpu.liberar()
            jarvis_agent.descargar_modelo()

        threading.Thread(target=liberar, name="JarvisLiberaGPU", daemon=True).start()

    def _restore_gpu_after_game(self) -> None:
        """Al cerrar el último juego, vuelve a cargar la voz a texto y el modelo de lenguaje."""

        def recargar() -> None:
            import jarvis_stt_gpu

            jarvis_stt_gpu.reanudar(core.CARPETA_STT_GPU)

        threading.Thread(target=recargar, name="JarvisRecargaGPU", daemon=True).start()
        self._last_llm_preload = -1e9
        self._preload_llm("fin del juego")

    def _game_quiet_enabled(self) -> bool:
        try:
            return core.obtener_preferencia(jarvis_modo_juego.PREF_SILENCIO) != "0"
        except Exception:
            return True

    def _on_game_started(self, juego: str) -> None:
        """Hilo de Tk. Libera la GPU, silencia a Jarvis y cierra los programas elegidos."""
        self._free_gpu_for_game()
        self._game_quiet = self._game_quiet_enabled()
        self._quiet_backlog: list[str] = []
        try:
            nombres = jarvis_modo_juego.nombres_de(core.obtener_preferencia(jarvis_modo_juego.PREF_CERRAR))
        except Exception:
            nombres = []
        if not nombres:
            return

        def cerrar() -> None:
            cerrados = jarvis_modo_juego.cerrar(nombres)
            self._closed_for_game = cerrados
            if cerrados:
                self.events.put(("game_mode_info",
                                 f"Modo juego ({juego}): cerré {jarvis_modo_juego.describir(cerrados)}; "
                                 "los vuelvo a abrir cuando termines."))

        threading.Thread(target=cerrar, name="JarvisModoJuego", daemon=True).start()

    def _on_game_stopped(self) -> None:
        """Hilo de Tk. Recarga la GPU, reabre lo cerrado y cuenta lo que pasó mientras jugabas."""
        self._restore_gpu_after_game()
        self._game_quiet = False
        pendientes = list(getattr(self, "_quiet_backlog", []))
        self._quiet_backlog = []
        cerrados = list(getattr(self, "_closed_for_game", []) or [])
        self._closed_for_game = []
        if cerrados:
            threading.Thread(target=jarvis_modo_juego.reabrir, args=(cerrados,),
                             name="JarvisReabre", daemon=True).start()
        if pendientes:
            resumen = "Mientras jugabas: " + " ".join(pendientes[:5])
            if len(pendientes) > 5:
                resumen += f" (y {len(pendientes) - 5} avisos más en el chat)."
            self._announce(resumen)

    def _quiet_message(self, text: str) -> None:
        """Mensaje solo en el chat: sin voz, sin notificación y sin burbuja sobre el juego."""
        self._floating_muted = True
        try:
            self._add_message("assistant", text)
        finally:
            self._floating_muted = False

    def _toggle_game_quiet(self) -> None:
        try:
            activo = self._game_quiet_enabled()
            core.guardar_preferencia(jarvis_modo_juego.PREF_SILENCIO, "0" if activo else "1")
        except Exception:
            logging.exception("No se pudo guardar la preferencia del silencio al jugar.")
        self._refresh_game_mode_settings()

    def _save_game_close_list(self) -> None:
        texto = self.game_close_entry.get()
        nombres = jarvis_modo_juego.nombres_de(texto)
        try:
            core.guardar_preferencia(jarvis_modo_juego.PREF_CERRAR, ", ".join(nombres))
        except Exception:
            logging.exception("No se pudo guardar la lista de programas del modo juego.")
            return
        self._refresh_game_mode_settings()
        self._set_status("MODO JUEGO GUARDADO", CYAN)

    def _refresh_game_mode_settings(self) -> None:
        boton = getattr(self, "game_quiet_button", None)
        if boton is not None:
            activo = self._game_quiet_enabled()
            boton.configure(text="🔕  SILENCIO AL JUGAR: SÍ" if activo else "🔕  SILENCIO AL JUGAR: NO",
                            fg=CYAN if activo else MUTED)
        entrada = getattr(self, "game_close_entry", None)
        if entrada is not None:
            try:
                guardado = core.obtener_preferencia(jarvis_modo_juego.PREF_CERRAR) or ""
            except Exception:
                guardado = ""
            entrada.delete(0, "end")
            entrada.insert(0, ", ".join(n.capitalize() for n in jarvis_modo_juego.nombres_de(guardado)))
