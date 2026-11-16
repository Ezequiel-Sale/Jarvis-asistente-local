"""Gestos con la mano: activarlos, indicador en la barra lateral y vista de cámara.

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Fase 1 [2026-11-16]: solo
se reconocen los gestos (se ven en la barra lateral y en la vista de cámara); las acciones
(scroll, puntero, clic) llegan en las fases siguientes y pasarán por ``tool_registry``.
"""

import logging
import queue
import threading
import tkinter as tk

import jarvis_core as core
import jarvis_gestos
from jarvis_theme import BG, BORDER, CYAN, MUTED, PANEL, TEXT


class GestosMixin:
    _PREFERENCIA_GESTOS = "gestos"
    VISTA_MS = 100  # la vista de cámara se repinta 10 veces por segundo

    def _init_gestures(self) -> None:
        self.gesture_detector: jarvis_gestos.DetectorGestos | None = None
        self._gesture: str | None = None
        self._gesture_fps = 0.0
        self._gesture_brightness = 0
        self._gestures_paused_for_game = False
        try:
            activo = core.obtener_preferencia(self._PREFERENCIA_GESTOS) == "1"
        except Exception:
            logging.exception("No se pudo leer la preferencia de los gestos.")
            activo = False
        if activo:
            self._start_gestures()

    def _gestures_active(self) -> bool:
        detector = getattr(self, "gesture_detector", None)
        return detector is not None and detector.is_alive()

    def _can_use_camera(self) -> bool:
        """El hilo de la cámara lo consulta (solo lee atributos): no se usa jugando."""
        return not self._closing and not getattr(self, "_gestures_paused_for_game", False)

    def _start_gestures(self) -> str | None:
        """Arranca la cámara; devuelve qué falta si no se puede."""
        if self._gestures_active():
            return None
        falta = jarvis_gestos.que_falta(core.APP_DIR)
        if falta is not None:
            logging.warning("Gestos no disponibles: %s.", falta)
            return falta
        self.gesture_detector = jarvis_gestos.DetectorGestos(
            self.events, self._can_use_camera, core.APP_DIR,
            al_desplazar=self._queue_gesture_scroll, invertir_scroll=self._gesture_scroll_inverted())
        self.gesture_detector.start()
        return None

    # --- Acciones de los gestos (fase 2: scroll) ----------------------------------------
    _PREFERENCIA_SCROLL_INVERTIDO = "gestos_scroll_invertido"

    def _gesture_scroll_inverted(self) -> bool:
        try:
            return core.obtener_preferencia(self._PREFERENCIA_SCROLL_INVERTIDO) == "1"
        except Exception:
            return False

    def _toggle_gesture_scroll_direction(self) -> None:
        invertido = not self._gesture_scroll_inverted()
        try:
            core.guardar_preferencia(self._PREFERENCIA_SCROLL_INVERTIDO, "1" if invertido else "0")
        except Exception:
            logging.exception("No se pudo guardar el sentido del scroll con gestos.")
        detector = getattr(self, "gesture_detector", None)
        if detector is not None:
            detector.desplazamiento.signo = -1.0 if invertido else 1.0
        self._refresh_gesture_indicator()

    def _queue_gesture_scroll(self, cantidad: int) -> None:
        """Hilo de la cámara: deja el scroll en una cola y sigue (nunca espera a Windows)."""
        cola = getattr(self, "_gesture_actions", None)
        if cola is None:
            cola = self._gesture_actions = queue.Queue()
            threading.Thread(target=self._gesture_action_worker, args=(cola,),
                             name="JarvisAccionesGestos", daemon=True).start()
        cola.put(cantidad)

    def _gesture_action_worker(self, cola: queue.Queue) -> None:
        """Ejecuta el scroll por el registro de herramientas (límite de tiempo y registro). Si se
        juntaron varias tandas mientras Windows respondía, las suma en una sola."""
        while not self._closing:
            try:
                cantidad = cola.get(timeout=1.0)
            except queue.Empty:
                continue
            while True:
                try:
                    cantidad += cola.get_nowait()
                except queue.Empty:
                    break
            try:
                self.tool_registry.execute("gesture_scroll", {"amount": max(-3000, min(3000, cantidad))},
                                           confirmed=True, origin="gestos")
            except Exception:
                logging.debug("El scroll con gestos falló.", exc_info=True)

    def _stop_gestures(self) -> None:
        detector = getattr(self, "gesture_detector", None)
        self.gesture_detector = None
        self._gesture = None
        if detector is not None:
            detector.stop()

    def _execute_gestures_toggle(self, enabled: bool, command: str) -> None:
        try:
            core.guardar_preferencia(self._PREFERENCIA_GESTOS, "1" if enabled else "0")
        except Exception:
            logging.exception("No se pudo guardar la preferencia de los gestos.")
        if enabled:
            falta = self._start_gestures()
            if falta is not None:
                respuesta = f"No puedo usar los gestos: {falta}."
            else:
                respuesta = ("Gestos activados: la cámara está mirando tu mano. Por ahora solo los reconozco "
                             "(lo ves en la barra lateral); las acciones llegan en la próxima fase.")
        else:
            self._stop_gestures()
            respuesta = "Gestos desactivados: cerré la cámara."
        logging.info("Gestos %s.", "activados" if enabled and self._gestures_active() else "desactivados")
        try:
            core.guardar_turno(self.conversation_id, command, respuesta)
        except Exception:
            logging.exception("No se pudo guardar el cambio de gestos en memoria.")
        self._add_message("assistant", respuesta)
        self._refresh_gesture_indicator()

    def _toggle_gestures_button(self) -> None:
        activar = not self._gestures_active()
        self._execute_gestures_toggle(activar, "Botón: activar los gestos" if activar else
                                      "Botón: desactivar los gestos")

    def _pause_gestures_for_game(self, pausa: bool) -> None:
        """Modo juego: el hilo cierra la cámara mientras dure (y se apaga su luz)."""
        self._gestures_paused_for_game = pausa
        self._refresh_gesture_indicator()

    # --- Eventos del hilo de la cámara --------------------------------------------------
    def _on_gesture(self, gesto: str | None) -> None:
        self._gesture = gesto
        self._refresh_gesture_indicator()

    def _on_gesture_stats(self, cuadros_por_s: float, brillo: int) -> None:
        self._gesture_fps, self._gesture_brightness = cuadros_por_s, brillo

    def _on_gestures_error(self, mensaje: str) -> None:
        self.gesture_detector = None
        self._gesture = None
        self._add_message("assistant", f"Los gestos dejaron de funcionar: {mensaje}. Puedes volver a "
                                       "activarlos en Ajustes o con «activa los gestos».")
        self._refresh_gesture_indicator()

    # --- Interfaz -----------------------------------------------------------------------
    def _refresh_gesture_indicator(self) -> None:
        etiqueta = getattr(self, "gesture_indicator", None)
        activo = self._gestures_active()
        if etiqueta is not None:
            if not activo:
                etiqueta.pack_forget()
            else:
                if getattr(self, "_gestures_paused_for_game", False):
                    texto, color = "✋ GESTOS EN PAUSA (JUEGO)", MUTED
                elif self._gesture is None:
                    texto, color = "✋ GESTOS · SIN MANO", MUTED
                else:
                    texto, color = f"✋ GESTOS · {jarvis_gestos.NOMBRES[self._gesture].upper()}", CYAN
                etiqueta.configure(text=texto, fg=color)
                if not etiqueta.winfo_ismapped():
                    etiqueta.pack(fill="x", padx=16, pady=(0, 10), after=self.orb_status)
        boton = getattr(self, "gestures_button", None)
        if boton is not None:
            boton.configure(text="GESTOS ON" if activo else "GESTOS OFF", fg=CYAN if activo else MUTED)
        boton = getattr(self, "gesture_scroll_button", None)
        if boton is not None:
            boton.configure(text="↕  SCROLL: INVERTIDO" if self._gesture_scroll_inverted()
                            else "↕  SCROLL: COMO EL CELULAR")

    def _open_gesture_preview(self) -> None:
        """Ventana con la cámara y los puntos de la mano, para ver cómo te reconoce."""
        ventana = getattr(self, "_gesture_preview", None)
        if ventana is not None and ventana.winfo_exists():
            ventana.lift()
            return
        if not self._gestures_active():
            self._execute_gestures_toggle(True, "Ver la cámara de gestos")
            if not self._gestures_active():
                return
        ventana = tk.Toplevel(self.root)
        ventana.title("Cámara de gestos")
        ventana.configure(bg=BG)
        ventana.attributes("-topmost", True)
        ventana.geometry("680x560")
        self._gesture_preview = ventana
        imagen = tk.Label(ventana, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        imagen.pack(padx=16, pady=(16, 8))
        datos = tk.Label(ventana, bg=BG, fg=TEXT, font=("Consolas", 10, "bold"))
        datos.pack()
        tk.Label(ventana, text="Las imágenes no se guardan: solo se ven aquí mientras la ventana está abierta.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(pady=(4, 0))
        detector = self.gesture_detector
        if detector is not None:
            detector.vista = True

        def cerrar() -> None:
            actual = self.gesture_detector
            if actual is not None:
                actual.vista = False
                actual.ultima_imagen = None
            ventana.destroy()

        ventana.protocol("WM_DELETE_WINDOW", cerrar)
        ventana.bind("<Escape>", lambda _e: cerrar())

        def repintar() -> None:
            if not ventana.winfo_exists():
                return
            actual = self.gesture_detector
            if actual is None:
                datos.configure(text="Los gestos están desactivados.")
            else:
                actual.vista = True
                rgb = actual.ultima_imagen
                if rgb is not None:
                    from PIL import Image, ImageTk

                    foto = ImageTk.PhotoImage(Image.fromarray(rgb))
                    imagen.configure(image=foto)
                    imagen.image = foto
                gesto = jarvis_gestos.NOMBRES.get(self._gesture or "", "sin mano")
                aviso = "  ·  poca luz: la aclaro" if self._gesture_brightness < jarvis_gestos.BRILLO_OSCURO else ""
                datos.configure(text=f"Gesto: {gesto}   ·   {self._gesture_fps:.0f} cuadros/s{aviso}")
            ventana.after(self.VISTA_MS, repintar)

        repintar()
