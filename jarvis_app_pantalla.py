"""Pantalla: clic en pantalla y visión (capturas con vista previa y confirmación).

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Los métodos usan
los atributos de la app (``self.root``, ``self.events``, ``self.tool_registry``…).
"""

import jarvis_core as core
import jarvis_screen_actions
import jarvis_vision
import logging
import os
import queue
import threading
import time
import tkinter as tk
from PIL import ImageTk
from jarvis_actions import EstadoAccion, OperacionCancelada, ResultadoAccion, ejecutar_con_limites
from jarvis_theme import CYAN, ERROR, HOVER, MUTED, PANEL, PANEL_LIGHT, TEXT
from jarvis_tool_registry import ToolError


class PantallaMixin:
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
            necesita_ventana=True, resumen=f"{titulo.capitalize()} {detalle}", imagen=miniatura,
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
            necesita_ventana=True, imagen=datos["imagen"],
            resumen=f"¿Analizo esta captura del {datos['monitor'].descripcion.lower()}?",
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
                argumentos={"monitor": getattr(monitor, "numero", None)},
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
