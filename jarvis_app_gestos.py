"""Gestos con la mano: activarlos, indicador en la barra lateral y vista de cámara.

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Fase 1 [2026-11-16]: solo
se reconocen los gestos (se ven en la barra lateral y en la vista de cámara); las acciones
(scroll, puntero, clic) llegan en las fases siguientes y pasarán por ``tool_registry``.
"""

import json
import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import jarvis_core as core
import jarvis_gestos
import jarvis_screen_actions
import jarvis_vision
from jarvis_theme import BG, BORDER, CYAN, ERROR, MUTED, PANEL, TEXT


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
            al_desplazar=self._queue_gesture_scroll, invertir_scroll=self._gesture_scroll_inverted(),
            al_apuntar=self._queue_gesture_pointer, monitor=self._gesture_monitor(),
            al_atajo=lambda accion, repetida: self.events.put(("gesture_shortcut", accion, repetida)),
            mano=jarvis_gestos.MANOS[self._gesture_hand()])
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

    def _gesture_queue(self) -> queue.Queue:
        cola = getattr(self, "_gesture_actions", None)
        if cola is None:
            cola = self._gesture_actions = queue.Queue()
            threading.Thread(target=self._gesture_action_worker, args=(cola,),
                             name="JarvisAccionesGestos", daemon=True).start()
        return cola

    def _queue_gesture_scroll(self, cantidad: int) -> None:
        """Hilo de la cámara: deja el scroll en la cola y sigue (nunca espera a Windows)."""
        self._gesture_queue().put(("scroll", cantidad))

    def _queue_gesture_pointer(self, acciones: list) -> None:
        """Hilo de la cámara: movimientos y clics del puntero, en orden, a la misma cola."""
        cola = self._gesture_queue()
        for accion in acciones:
            cola.put(tuple(accion))

    @staticmethod
    def _merge_gesture_actions(pendientes: list[tuple]) -> list[tuple]:
        """Junta lo que se acumuló mientras Windows respondía: scrolls seguidos se suman y de
        movimientos seguidos queda el último. Los clics nunca se juntan ni cambian de orden."""
        juntas: list[tuple] = []
        for accion in pendientes:
            if juntas and accion[0] == juntas[-1][0] and accion[0] in ("scroll", "volumen"):
                juntas[-1] = (accion[0], juntas[-1][1] + accion[1])
            elif juntas and accion[0] == juntas[-1][0] == "mover":
                juntas[-1] = accion
            else:
                juntas.append(accion)
        return juntas

    def _gesture_action_worker(self, cola: queue.Queue) -> None:
        """Hace lo que piden los gestos: el scroll y los clics por el registro de herramientas
        (límite de tiempo y registro); el movimiento del puntero, directo (24 por segundo)."""
        while not self._closing:
            try:
                pendientes = [cola.get(timeout=1.0)]
            except queue.Empty:
                continue
            while True:
                try:
                    pendientes.append(cola.get_nowait())
                except queue.Empty:
                    break
            for accion in self._merge_gesture_actions(pendientes):
                try:
                    if accion[0] == "scroll":
                        self.tool_registry.execute("gesture_scroll", {"amount": max(-3000, min(3000, accion[1]))},
                                                   confirmed=True, origin="gestos")
                    elif accion[0] == "mover":
                        self._set_pointer_target(accion[1], accion[2])  # el hilo de deslizar lo lleva suave
                    elif accion[0] == "media":
                        self.tool_registry.execute("media_control", {"action": accion[1]},
                                                   confirmed=True, origin="gestos")
                    elif accion[0] == "browser":
                        self.tool_registry.execute("browser_history", {"action": accion[1]},
                                                   confirmed=True, origin="gestos")
                    elif accion[0] == "teclas":
                        self.tool_registry.execute("gesture_keys", {"keys": accion[1]},
                                                   confirmed=True, origin="gestos")
                    elif accion[0] == "volumen":
                        import jarvis_audio_devices

                        actual = jarvis_audio_devices.obtener_volumen_sistema()
                        self.tool_registry.execute(
                            "set_system_volume", {"percentage": max(0, min(100, actual + accion[1]))},
                            confirmed=True, origin="gestos")
                    else:  # click / down / up
                        self._set_pointer_target(accion[1], accion[2], saltar=True)
                        self.tool_registry.execute("gesture_click", {"action": accion[0], "x": accion[1],
                                                                     "y": accion[2]},
                                                   confirmed=True, origin="gestos")
                except Exception:
                    logging.debug("Falló una acción de los gestos (%s).", accion[0], exc_info=True)

    # --- Deslizamiento del puntero entre cuadros -----------------------------------------
    def _set_pointer_target(self, x: int, y: int, saltar: bool = False) -> None:
        """Nuevo destino del puntero; un hilo lo lleva hasta ahí de a poco (``saltar``: va directo,
        para que un clic no arrastre un deslizamiento pendiente)."""
        self._pointer_target = (int(x), int(y))
        if saltar:
            self._pointer_jump = True
        hilo = getattr(self, "_pointer_glide_thread", None)
        if hilo is None or not hilo.is_alive():
            self._pointer_glide_thread = threading.Thread(target=self._pointer_glide_worker,
                                                          name="JarvisPunteroSuave", daemon=True)
            self._pointer_glide_thread.start()

    def _pointer_glide_worker(self) -> None:
        actual: tuple[float, float] | None = None
        mostrado: tuple[int, int] | None = None
        quieto_desde = None
        while not self._closing:
            objetivo = getattr(self, "_pointer_target", None)
            if objetivo is None:  # el puntero se soltó: esperar sin gastar
                actual = mostrado = None
                if quieto_desde is None:
                    quieto_desde = time.monotonic()
                elif time.monotonic() - quieto_desde > 30:
                    return  # sin uso hace rato: el próximo destino lo vuelve a arrancar
                time.sleep(0.05)
                continue
            quieto_desde = None
            if actual is None or getattr(self, "_pointer_jump", False):
                self._pointer_jump = False
                actual = (float(objetivo[0]), float(objetivo[1]))
            else:
                actual = jarvis_gestos.deslizar(actual, objetivo)
            redondo = (round(actual[0]), round(actual[1]))
            if redondo != mostrado:
                try:
                    jarvis_screen_actions.mover_rapido(*redondo)
                except Exception:
                    logging.debug("No se pudo mover el puntero.", exc_info=True)
                mostrado = redondo
            time.sleep(jarvis_gestos.PASO_DESLIZAR)

    # --- Qué mano se sigue -----------------------------------------------------------------
    _PREFERENCIA_MANO = "gestos_mano"

    def _gesture_hand(self) -> str:
        try:
            valor = core.obtener_preferencia(self._PREFERENCIA_MANO)
        except Exception:
            valor = None
        return valor if valor in jarvis_gestos.MANOS else jarvis_gestos.MANO_POR_DEFECTO

    def _cycle_gesture_hand(self) -> None:
        orden = list(jarvis_gestos.MANOS)
        siguiente = orden[(orden.index(self._gesture_hand()) + 1) % len(orden)]
        try:
            core.guardar_preferencia(self._PREFERENCIA_MANO, siguiente)
        except Exception:
            logging.exception("No se pudo guardar qué mano siguen los gestos.")
        detector = getattr(self, "gesture_detector", None)
        if detector is not None:
            detector.mano = jarvis_gestos.MANOS[siguiente]
        logging.info("Gestos: mano %s.", siguiente)
        self._refresh_gesture_indicator()

    # --- Monitor del puntero (fase 3) ---------------------------------------------------
    _PREFERENCIA_MONITOR = "gestos_monitor"

    def _gesture_monitor_number(self) -> int:
        try:
            return int(core.obtener_preferencia(self._PREFERENCIA_MONITOR) or 1)
        except (TypeError, ValueError):
            return 1
        except Exception:
            return 1

    def _gesture_monitor(self):
        """El monitor que recorre el puntero (el 1 por defecto: la cámara está centrada sobre él)."""
        try:
            return jarvis_vision.elegir_monitor(jarvis_vision.listar_monitores(), self._gesture_monitor_number())
        except Exception:
            logging.warning("No pude leer los monitores para el puntero con gestos.", exc_info=True)
            return None

    def _set_gesture_monitor(self, numero: int, command: str | None = None) -> None:
        try:
            monitor = jarvis_vision.elegir_monitor(jarvis_vision.listar_monitores(), numero)
        except (OSError, ValueError) as error:
            if command is not None:
                self._add_message("assistant", str(error))
            return
        try:
            core.guardar_preferencia(self._PREFERENCIA_MONITOR, str(monitor.numero))
        except Exception:
            logging.exception("No se pudo guardar el monitor del puntero con gestos.")
        detector = getattr(self, "gesture_detector", None)
        if detector is not None and detector.puntero is not None:
            detector.puntero.monitor = monitor
        if command is not None:
            respuesta = f"El puntero con gestos ahora recorre el {monitor.descripcion.lower()}."
            try:
                core.guardar_turno(self.conversation_id, command, respuesta)
            except Exception:
                logging.exception("No se pudo guardar el cambio de monitor en memoria.")
            self._add_message("assistant", respuesta)
        self._refresh_gesture_indicator()

    def _cycle_gesture_monitor(self) -> None:
        try:
            numeros = sorted(m.numero for m in jarvis_vision.listar_monitores())
        except OSError:
            return
        actual = self._gesture_monitor_number()
        siguiente = next((n for n in numeros if n > actual), numeros[0]) if numeros else 1
        self._set_gesture_monitor(siguiente)

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

    def _on_gesture_pointer(self, estado: str | None) -> None:
        self._gesture_pointer = estado
        if estado is None:
            self._pointer_target = None  # se soltó: el deslizamiento se detiene
        self._refresh_gesture_indicator()

    # --- Fase 4: atajos con gestos ------------------------------------------------------
    PASO_VOLUMEN = 5  # % por pulgar (se repite mientras se sostiene)

    def _on_gesture_shortcut(self, accion: str, repetida: bool) -> None:
        """Hilo de Tk. Pulgares: con una confirmación abierta la aprueban o cancelan (la primera
        vez, no al repetirse); si no, cambian el volumen. Mano abierta quieta: play/pausa.
        Deslizar: atrás/adelante en el navegador. ``propio:…``: lo que el usuario asignó (fase 5)."""
        if accion.startswith("propio:"):
            self._run_custom_gesture(accion.split(":", 1)[1])
            return
        if accion in ("pulgar_arriba", "pulgar_abajo"):
            aprobar = accion == "pulgar_arriba"
            if self._pending_plan_reply is not None:
                if repetida:
                    return
                tipo = self._pending_approval_kind or "plan"
                if aprobar and tipo == "power":
                    # Apagar o reiniciar no se confirma con un gesto (un pulgar sin querer); cancelar, sí.
                    self._set_status("PARA APAGAR O REINICIAR, CONFIRMA CON LA VOZ O EL BOTÓN", ERROR)
                    return
                logging.info("Confirmación %s con gesto: %s.", tipo, "sí" if aprobar else "no")
                self._add_message("user", f"Confirmación con gesto: {'👍 sí' if aprobar else '👎 no'}.")
                self._resolve_plan_approval(aprobar)
                self._set_status("CONFIRMADO CON UN GESTO" if aprobar else "CANCELADO CON UN GESTO", CYAN)
                return
            self._gesture_queue().put(("volumen", self.PASO_VOLUMEN if aprobar else -self.PASO_VOLUMEN))
            self._set_status("✋ VOLUMEN +" if aprobar else "✋ VOLUMEN −", CYAN)
        elif accion == "pausa":
            self._gesture_queue().put(("media", "toggle"))
            self._set_status("✋ PLAY / PAUSA", CYAN)
        elif accion in ("atras", "adelante"):
            self._gesture_queue().put(("browser", "back" if accion == "atras" else "forward"))
            self._set_status("✋ ATRÁS" if accion == "atras" else "✋ ADELANTE", CYAN)
        logging.info("Atajo con gestos: %s%s.", accion, " (repetido)" if repetida else "")

    # --- Fase 5: gestos propios -------------------------------------------------------------
    _PREFERENCIA_PROPIOS = "gestos_propios"
    TIPOS_PROPIOS = {"rutina": "Rutina", "frase": "Frase (como si se la dijeras)", "teclas": "Atajo de teclado"}
    HISTORIAL_GESTOS = 20

    def _custom_gestures(self) -> dict:
        """{gesto: {"tipo": "rutina"|"frase"|"teclas", "valor": …}} (lo que el usuario asignó)."""
        try:
            datos = json.loads(core.obtener_preferencia(self._PREFERENCIA_PROPIOS) or "{}")
        except (ValueError, TypeError):
            return {}
        except Exception:
            logging.exception("No se pudieron leer los gestos propios.")
            return {}
        return {g: d for g, d in datos.items() if g in jarvis_gestos.PROPIOS and isinstance(d, dict)
                and d.get("tipo") in self.TIPOS_PROPIOS and d.get("valor") not in (None, "")}

    def _save_custom_gesture(self, gesto: str, tipo: str | None, valor) -> None:
        datos = self._custom_gestures()
        if tipo is None:
            datos.pop(gesto, None)
        else:
            datos[gesto] = {"tipo": tipo, "valor": valor}
        core.guardar_preferencia(self._PREFERENCIA_PROPIOS, json.dumps(datos, ensure_ascii=False))
        self._refresh_custom_gestures()

    def _describe_custom_gesture(self, asignacion: dict | None) -> str:
        if not asignacion:
            return "sin asignar"
        tipo, valor = asignacion["tipo"], asignacion["valor"]
        if tipo == "rutina":
            import jarvis_routines

            rutina = next((r for r in jarvis_routines.listar_automatizaciones() if r.identifier == valor), None)
            return f"rutina «{rutina.name}»" if rutina else "rutina (ya no existe)"
        if tipo == "frase":
            return f"decir «{valor}»"
        return f"teclas {valor}"

    def _gesture_history_add(self, gesto: str, texto: str) -> None:
        historial = getattr(self, "_gesture_history", [])
        historial.insert(0, f"{time.strftime('%H:%M:%S')}  {jarvis_gestos.EMOJIS.get(gesto, '✋')}  {texto}")
        self._gesture_history = historial[:self.HISTORIAL_GESTOS]
        self._refresh_custom_gestures()

    def _run_custom_gesture(self, gesto: str) -> None:
        """Hilo de Tk: hace lo que el usuario asignó a ese gesto propio."""
        asignacion = self._custom_gestures().get(gesto)
        nombre = f"{jarvis_gestos.EMOJIS.get(gesto, '')} {jarvis_gestos.NOMBRES.get(gesto, gesto)}".strip()
        if asignacion is None:
            self._set_status(f"✋ {nombre.upper()}: SIN ASIGNAR (AJUSTES → GESTOS PROPIOS)", MUTED)
            self._gesture_history_add(gesto, "sin asignar")
            return
        tipo, valor = asignacion["tipo"], asignacion["valor"]
        logging.info("Gesto propio %s: %s.", gesto, tipo)
        if tipo == "teclas":
            self._gesture_queue().put(("teclas", valor))
            self._set_status(f"✋ {nombre.upper()} → {str(valor).upper()}", CYAN)
        elif self.busy or self.recorder is not None or self._pending_plan_reply is not None:
            self._set_status("✋ JARVIS ESTÁ OCUPADO; PRUEBA EN UN MOMENTO", ERROR)
            self._gesture_history_add(gesto, "ocupado: no se hizo")
            return
        elif tipo == "rutina":
            self._run_routine_from_mobile(int(valor), desde="con un gesto")
        else:  # frase: como si la escribieras en el chat
            self._send_text_as_typed(str(valor))
        self._gesture_history_add(gesto, self._describe_custom_gesture(asignacion))

    def _on_gesture_progress(self, cuarto) -> None:
        """Anillo de progreso al sostener un gesto propio (en el indicador de la barra lateral)."""
        self._gesture_progress = cuarto
        self._refresh_gesture_indicator()

    def _refresh_custom_gestures(self) -> None:
        etiquetas = getattr(self, "custom_gesture_labels", None)
        if etiquetas:
            asignados = self._custom_gestures()
            for gesto, etiqueta in etiquetas.items():
                etiqueta.configure(text=self._describe_custom_gesture(asignados.get(gesto)),
                                   fg=TEXT if gesto in asignados else MUTED)
        historial = getattr(self, "gesture_history_label", None)
        if historial is not None:
            lineas = getattr(self, "_gesture_history", [])
            historial.configure(text="\n".join(lineas[:8]) if lineas else "Todavía no usaste ningún gesto propio.")

    def _assign_custom_gesture(self, gesto: str) -> None:
        """Ventana para elegir qué hace un gesto propio: una rutina, una frase o un atajo de teclado."""
        import jarvis_routines
        import jarvis_screen_actions as acciones

        ventana = tk.Toplevel(self.root)
        nombre = f"{jarvis_gestos.EMOJIS[gesto]}  {jarvis_gestos.NOMBRES[gesto].capitalize()}"
        ventana.title(f"Gesto {jarvis_gestos.NOMBRES[gesto]}")
        ventana.configure(bg=BG)
        ventana.transient(self.root)
        ventana.geometry("520x380")
        marco = tk.Frame(ventana, bg=PANEL, padx=20, pady=16, highlightthickness=1, highlightbackground=BORDER)
        marco.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(marco, text=nombre, bg=PANEL, fg=CYAN, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(marco, text="Sostenelo 0,6 s frente a la cámara. ¿Qué hace?", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 10))
        actual = self._custom_gestures().get(gesto) or {}
        tipo = tk.StringVar(value=actual.get("tipo", "nada"))
        rutinas = [r for r in jarvis_routines.listar_automatizaciones()]
        nombres_rutinas = [r.name for r in rutinas]
        valor_rutina = tk.StringVar(value=next((r.name for r in rutinas if r.identifier == actual.get("valor")), ""))
        valor_texto = tk.StringVar(value=str(actual.get("valor", "")) if actual.get("tipo") in ("frase", "teclas") else "")
        error = tk.Label(marco, text="", bg=PANEL, fg=ERROR, font=("Segoe UI", 9), wraplength=460, justify="left")

        def opcion(clave: str, texto: str) -> None:
            tk.Radiobutton(marco, text=texto, value=clave, variable=tipo, bg=PANEL, fg=TEXT, selectcolor=BG,
                           activebackground=PANEL, activeforeground=CYAN, font=("Segoe UI", 10),
                           anchor="w").pack(fill="x")

        opcion("nada", "Nada (sin asignar)")
        opcion("rutina", "Rutina")
        ttk.Combobox(marco, textvariable=valor_rutina, values=nombres_rutinas, state="readonly").pack(
            fill="x", padx=(24, 0), pady=(0, 6))
        opcion("frase", "Frase (como si se la dijeras: «pausa», «abre Steam», «modo gaming 4K»…)")
        opcion("teclas", "Atajo de teclado (ctrl+w, alt+tab, win+d, f5…)")
        tk.Entry(marco, textvariable=valor_texto, bg=BG, fg=TEXT, insertbackground=TEXT, relief="flat",
                 font=("Segoe UI", 10)).pack(fill="x", padx=(24, 0), ipady=4)
        error.pack(anchor="w", pady=(8, 0))

        def guardar() -> None:
            elegido = tipo.get()
            try:
                if elegido == "nada":
                    self._save_custom_gesture(gesto, None, None)
                elif elegido == "rutina":
                    rutina = next((r for r in rutinas if r.name == valor_rutina.get()), None)
                    if rutina is None:
                        raise ValueError("Elegí una rutina de la lista.")
                    self._save_custom_gesture(gesto, "rutina", rutina.identifier)
                else:
                    texto = valor_texto.get().strip()
                    if not texto:
                        raise ValueError("Escribí la frase o el atajo.")
                    if elegido == "teclas":
                        acciones.parsear_atajo(texto)  # valida antes de guardar
                    self._save_custom_gesture(gesto, elegido, texto[:200])
            except ValueError as problema:
                error.configure(text=str(problema))
                return
            ventana.destroy()

        botones = tk.Frame(marco, bg=PANEL)
        botones.pack(side="bottom", fill="x", pady=(10, 0))
        self._ui_button(botones, "GUARDAR", guardar, fg=CYAN).pack(side="left")
        self._ui_button(botones, "CANCELAR", ventana.destroy, fg=MUTED).pack(side="right")

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
                puntero = getattr(self, "_gesture_pointer", None)
                progreso = getattr(self, "_gesture_progress", None)
                if getattr(self, "_gestures_paused_for_game", False):
                    texto, color = "✋ GESTOS EN PAUSA (JUEGO)", MUTED
                elif progreso is not None:  # sosteniendo un gesto propio: anillo de 4 cuartos
                    gesto, cuartos = progreso
                    texto = (f"{jarvis_gestos.EMOJIS.get(gesto, '✋')} {jarvis_gestos.NOMBRES[gesto].upper()}  "
                             + "▰" * cuartos + "▱" * (4 - cuartos))
                    color = CYAN
                elif puntero == "arrastrando":
                    texto, color = "✋ ARRASTRANDO", CYAN
                elif puntero == "puntero":
                    texto, color = f"✋ PUNTERO · MONITOR {self._gesture_monitor_number()}", CYAN
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
        boton = getattr(self, "gesture_hand_button", None)
        if boton is not None:
            boton.configure(text=f"✋  MANO: {self._gesture_hand().upper()}")
        boton = getattr(self, "gesture_monitor_button", None)
        if boton is not None:
            boton.configure(text=f"🖱  PUNTERO: MONITOR {self._gesture_monitor_number()}")
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
