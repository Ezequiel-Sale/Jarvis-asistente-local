"""Celular: servidor de la app, vinculación, pantalla, control remoto y lo que pide el teléfono.

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Los métodos usan
los atributos de la app (``self.root``, ``self.events``, ``self.tool_registry``…).
"""

import jarvis_archivos
import jarvis_audio_devices
import jarvis_core as core
import jarvis_dashboard
import jarvis_media
import jarvis_mobile
import jarvis_system_info
import jarvis_vision
import jarvis_voz_stream
import logging
import psutil
import threading
import time
import tkinter as tk
from jarvis_actions import EstadoAccion, OperacionCancelada
from jarvis_theme import CYAN, ERROR, MUTED, PANEL, PANEL_LIGHT, TEXT
from jarvis_tool_registry import ToolError
from pathlib import Path
from tkinter import messagebox


class MovilMixin:
    def _media_for_mobile(self) -> dict:
        """Pestaña «Música» del celular (hilo HTTP; solo lectura)."""
        datos: dict = {"playing": {}, "apps": [], "volume": None, "output": None,
                       "outputs": [{"device": "headphones", "name": "Auriculares"},
                                   {"device": "speakers", "name": "Parlantes"}]}
        for clave, leer in (("playing", jarvis_media.estado_reproduccion),
                            ("apps", jarvis_media.aplicaciones_con_audio),
                            ("volume", jarvis_audio_devices.obtener_volumen_sistema)):
            try:
                datos[clave] = leer()
            except Exception:
                logging.debug("No se pudo leer %s para el celular.", clave, exc_info=True)
        try:
            salida = jarvis_audio_devices.salida_predeterminada()
            datos["output"] = "headphones" if jarvis_audio_devices.es_auriculares(salida) else "speakers"
            datos["outputName"] = salida
        except Exception:
            logging.debug("No se pudo leer la salida de audio.", exc_info=True)
        return datos

    def _action_from_mobile(self, tool: str, arguments: dict) -> str:
        """Música desde el celular: por tool_registry.execute (validación, tiempo máximo, registro)."""
        return str(self.tool_registry.execute(tool, arguments, origin="celular"))

    def _status_for_mobile(self, fps: bool) -> dict:
        discos = []
        try:
            discos = jarvis_system_info.listar_discos()
        except Exception:
            logging.debug("No se pudieron leer los discos para el celular.", exc_info=True)
        return jarvis_dashboard.estado_movil(
            self.health_watcher, discos=discos, memoria=psutil.virtual_memory(),
            fps=self.fps_meter.actual() if fps else None,
            juegos=self.games_watcher.jugando if getattr(self, "games_watcher", None) else (),
        )

    @staticmethod
    def _tts_for_mobile(texto: str) -> bytes:
        """WAV de Piper para el celular (hilo HTTP): sin markdown ni enlaces, sin guardarlo."""
        import secrets

        ruta = Path(core.generar_audio_tts(jarvis_voz_stream.limpiar_para_voz(texto),
                                           "movil_" + secrets.token_hex(4)))
        try:
            return ruta.read_bytes()
        finally:
            ruta.unlink(missing_ok=True)

    MOBILE_RETRY_MS = 30000

    def _ensure_mobile_server(self) -> None:
        """Abre o cierra el servidor según el ajuste; reintenta si Tailscale aún no está."""
        if self._closing:
            return
        if not self._mobile_enabled:
            self.mobile.detener()
        elif not self.mobile.activo():
            ip = None
            try:
                ip = jarvis_mobile.ip_tailscale()
                if ip is not None:
                    self.mobile.iniciar(ip)
            except OSError as error:
                logging.warning("No se pudo abrir el servidor del celular: %s", error)
            if not self.mobile.activo():
                self.root.after(self.MOBILE_RETRY_MS, self._ensure_mobile_server)
        self._refresh_mobile_settings()

    def _refresh_mobile_settings(self) -> None:
        boton = getattr(self, "mobile_button", None)
        if boton is None:
            return
        boton.configure(text="▯  CELULAR: SÍ" if self._mobile_enabled else "▯  CELULAR: NO",
                        fg=CYAN if self._mobile_enabled else MUTED)
        if not self._mobile_enabled:
            texto = "Desactivado."
        elif not self.mobile.activo():
            texto = "Esperando a Tailscale: no está conectado en esta PC."
        else:
            vinculados = self.mobile.vinculacion.dispositivos()
            texto = f"Dirección para la app:  {self.mobile.direccion}\n" + (
                "Vinculados: " + ", ".join(vinculados) if vinculados else "Ningún celular vinculado todavía."
            )
        self.mobile_info.configure(text=texto)
        control = getattr(self, "mobile_control_button", None)
        if control is not None:
            control.configure(text="🖱  CONTROL DESDE EL CELULAR: SÍ" if self._mobile_control_enabled
                              else "🖱  CONTROL DESDE EL CELULAR: NO",
                              fg=ERROR if self._mobile_control_enabled else MUTED)
        pantalla = getattr(self, "mobile_screen_button", None)
        if pantalla is not None:
            pantalla.configure(text="▣  VER PANTALLA: SÍ" if self._mobile_screen_enabled else "▣  VER PANTALLA: NO",
                               fg=CYAN if self._mobile_screen_enabled else MUTED)
        self.mobile_pair_button.configure(state="normal" if self.mobile.activo() else "disabled")

    def _toggle_mobile(self) -> None:
        self._mobile_enabled = not self._mobile_enabled
        try:
            core.guardar_preferencia(jarvis_mobile.PREFERENCIA_ACTIVO, "1" if self._mobile_enabled else "0")
        except Exception:
            logging.exception("No se pudo guardar el ajuste de la app del celular.")
        self.mobile_code.configure(text="")
        self._ensure_mobile_server()

    def _toggle_mobile_screen(self) -> None:
        self._mobile_screen_enabled = not self._mobile_screen_enabled
        try:
            core.guardar_preferencia(jarvis_mobile.PREFERENCIA_PANTALLA, "1" if self._mobile_screen_enabled else "0")
        except Exception:
            logging.exception("No se pudo guardar el ajuste de ver la pantalla desde el celular.")
        if not self._mobile_screen_enabled:
            self._hide_screen_badge()
        self._refresh_mobile_settings()

    SCREEN_BADGE_MS = 1000

    def _show_screen_badge(self) -> None:
        """Aviso siempre visible mientras un celular ve la pantalla; clic = dejar de compartirla."""
        if self._screen_badge is None:
            aviso = tk.Toplevel(self.root)
            aviso.overrideredirect(True)
            aviso.attributes("-topmost", True)
            aviso.configure(bg=CYAN)
            etiqueta = tk.Label(aviso, text="", bg=PANEL, fg=CYAN, font=("Segoe UI", 10, "bold"),
                                padx=14, pady=6, cursor="hand2")
            etiqueta.pack(padx=1, pady=1)
            etiqueta.bind("<Button-1>", lambda _e: self._on_screen_badge_click())
            self._screen_badge_label = etiqueta
            self._refresh_screen_badge(aviso)
            aviso.update_idletasks()
            x = (aviso.winfo_screenwidth() - aviso.winfo_reqwidth()) // 2
            aviso.geometry(f"+{max(0, x)}+8")
            self._screen_badge = aviso
            self.root.after(self.SCREEN_BADGE_MS, self._check_screen_badge)

    def _refresh_screen_badge(self, aviso=None) -> None:
        aviso = aviso or self._screen_badge
        etiqueta = getattr(self, "_screen_badge_label", None)
        if aviso is None or etiqueta is None or not etiqueta.winfo_exists():
            if self.mobile.control_activo():
                self._show_screen_badge()
            return
        if self.mobile.control_activo():
            etiqueta.configure(text="🖱  El celular está CONTROLANDO la PC  ·  clic para cortar", fg=ERROR)
            aviso.configure(bg=ERROR)
        else:
            etiqueta.configure(text="📱  El celular está viendo la pantalla  ·  clic para cortar", fg=CYAN)
            aviso.configure(bg=CYAN)
        aviso.update_idletasks()
        x = (aviso.winfo_screenwidth() - aviso.winfo_reqwidth()) // 2
        aviso.geometry(f"+{max(0, x)}+8")

    def _on_screen_badge_click(self) -> None:
        """Con control activo, el clic corta solo el control; si no, deja de compartir la pantalla."""
        if self.mobile.control_activo():
            self.mobile.activar_control(False)
            self._refresh_screen_badge()
        else:
            self._toggle_mobile_screen()

    def _check_screen_badge(self) -> None:
        if self._screen_badge is None:
            return
        if self.mobile.control_activo() and self.mobile.control_inactivo():
            logging.info("Control desde el celular cortado: 2 minutos sin usarlo ni mirar la pantalla.")
            self.mobile.activar_control(False)
        if (self.mobile.mirando() or self.mobile.control_activo()) and self._mobile_screen_enabled:
            self.root.after(self.SCREEN_BADGE_MS, self._check_screen_badge)
        else:
            logging.info("El celular dejó de ver la pantalla.")
            self._hide_screen_badge()

    def _toggle_mobile_control(self) -> None:
        self._mobile_control_enabled = not self._mobile_control_enabled
        try:
            core.guardar_preferencia(jarvis_mobile.PREFERENCIA_CONTROL, "1" if self._mobile_control_enabled else "0")
        except Exception:
            logging.exception("No se pudo guardar el ajuste del control desde el celular.")
        if not self._mobile_control_enabled:
            self.mobile.activar_control(False)
        self._refresh_mobile_settings()

    def _open_link_from_mobile(self, url: str) -> str:
        """Link compartido desde el celular. ``navigate_browser`` (riesgo medio) pide
        confirmación; compartir el link a propósito es esa confirmación, igual que
        escribir «abre youtube.com». Sin ``confirmed`` el registro lo rechazaba."""
        return str(self.tool_registry.execute("navigate_browser", {"url": url}, confirmed=True, origin="celular"))

    def _tool_send_file_to_phone(self, path: str) -> str:
        if not self.mobile.activo():
            raise ToolError("La app del celular no está activada en Ajustes.")
        archivo = Path(path)
        for raiz in (r["key"] for r in jarvis_archivos.raices()):
            base = jarvis_archivos.carpeta(raiz)
            try:
                relativa = archivo.resolve().relative_to(base.resolve())
            except (OSError, ValueError):
                continue
            if not archivo.is_file():
                raise ToolError("Ese archivo no existe.")
            tamano = archivo.stat().st_size
            self.mobile.registro.publicar("file", root=raiz, path=relativa.as_posix(), name=archivo.name, size=tamano)
            return (f"Le mandé «{archivo.name}» ({jarvis_archivos.tamano_legible(tamano)}) al celular: "
                    "le llega como notificación para descargarlo.")
        raise ToolError("Solo puedo mandar archivos de Descargas, Documentos o los otros discos (no del disco del sistema).")

    def _tool_send_clipboard_to_phone(self) -> str:
        if not self.mobile.activo():
            raise ToolError("La app del celular no está activada en Ajustes.")
        texto = jarvis_archivos.leer_portapapeles()
        if not texto.strip():
            return "El portapapeles de la PC no tiene texto."
        self.mobile.registro.publicar("clipboard", text=texto)
        return "Le mandé al celular lo que copiaste; tócalo en la notificación para copiarlo allí."

    def _tool_mobile_remote_control(self, enable: bool) -> str:
        if enable and not self._mobile_control_enabled:
            raise ToolError("El control desde el celular está desactivado en Ajustes.")
        self.mobile.activar_control(bool(enable))
        return "Control desde el celular activado." if enable else "Control desde el celular cortado."

    def _on_mobile_control_request(self) -> None:
        """El celular pide el control: tarjeta roja (riesgo alto); en el celular pide huella."""
        if not self._mobile_control_enabled or self.mobile.control_activo():
            return
        if self._control_card is not None and self._control_card.winfo_exists():
            return
        estado = {"resuelto": False}

        def responder(permitir: bool) -> None:
            if estado["resuelto"]:
                return
            estado["resuelto"] = True
            if tarjeta.winfo_exists():
                tarjeta.jarvis_resolver(permitir, "✓  CONTROL PERMITIDO" if permitir else "✕  NO PERMITIDO")
            self._control_card = None
            if not permitir:
                self.mobile.registro.publicar("notice", text="No se permitió controlar la PC.")
                return

            def activar() -> None:
                try:
                    self.tool_registry.execute("mobile_remote_control", {"enable": True}, confirmed=True,
                                               origin="celular")
                except Exception as error:
                    logging.warning("No se pudo activar el control desde el celular: %s", error)
                    self.mobile.registro.publicar("notice", text=f"No pude activar el control: {error}")

            threading.Thread(target=activar, name="JarvisControlMovil", daemon=True).start()

        tarjeta, cuerpo = self._confirmation_card(
            titulo="¿EL CELULAR PUEDE CONTROLAR LA PC?", color=ERROR, aprobar="PERMITIR", rechazar="NO",
            voz=False, resumen="¿Permito que el celular controle el mouse y el teclado de la PC?",
            pista="ENTER permite · ESC rechaza.",
            al_aprobar=lambda: responder(True), al_rechazar=lambda: responder(False),
        )
        self._control_card = tarjeta
        tk.Label(cuerpo, text="Podrá mover el mouse, hacer clic y escribir. Se corta al cerrar la vista de "
                 "pantalla del celular, con un clic en el aviso de arriba o tras 2 minutos sin usarlo.",
                 bg=PANEL_LIGHT, fg=TEXT, font=("Segoe UI", 10), wraplength=680, justify="left").pack(anchor="w")

    def _start_mobile_vision(self, numero: int, zona: tuple, pregunta: str) -> None:
        """La zona marcada en el celular hace de vista previa: se captura y se analiza."""
        if self.busy or self._pending_plan_reply is not None:
            self.mobile.registro.publicar("notice", text="Estoy ocupado; espera a que termine.")
            return
        self._add_message("user", f"🔍 {pregunta} (zona marcada en el celular)")
        self._start_busy("ANALIZANDO LA ZONA…")
        datos = {"numero": numero, "zona": zona, "pregunta": pregunta, "cancel_token": self.cancel_token,
                 "conversation_id": self.conversation_id, "command": f"{pregunta} (zona de la pantalla)"}
        threading.Thread(target=self._mobile_vision_worker, args=(datos,), name="JarvisVisionMovil",
                         daemon=True).start()

    def _mobile_vision_worker(self, datos: dict) -> None:
        inicio = time.perf_counter()
        monitor = None
        try:
            imagen = jarvis_vision.capturar_zona(datos["numero"], datos["zona"])
            monitor = jarvis_vision.Monitor(datos["numero"], 0, 0, imagen.width, imagen.height, False)
            jpeg = jarvis_vision.preparar_imagen(imagen)
            del imagen
            answer = jarvis_vision.analizar_imagen(jpeg, datos["pregunta"], datos["cancel_token"])
        except OperacionCancelada:
            self._record_vision_action(EstadoAccion.CANCELADA, inicio, monitor, "Cancelado por el usuario.")
            self.events.put(("cancelled", "Cancelé el análisis de la zona."))
            return
        except Exception as error:
            logging.exception("Falló el análisis de la zona marcada en el celular.")
            self._record_vision_action(EstadoAccion.ERROR, inicio, monitor, str(error))
            self.events.put(("error", f"No pude analizar la zona: {error}"))
            return
        self._record_vision_action(EstadoAccion.EXITO, inicio, monitor)
        try:
            core.guardar_turno(datos["conversation_id"], datos["command"], answer)
        except Exception:
            logging.exception("No se pudo guardar el análisis de la zona en el historial.")
        self.events.put(("answer", answer, None, time.perf_counter() - inicio))

    def _hide_screen_badge(self) -> None:
        aviso, self._screen_badge = self._screen_badge, None
        if aviso is not None and aviso.winfo_exists():
            aviso.destroy()

    def _pair_mobile(self) -> None:
        codigo = self.mobile.vinculacion.nuevo_codigo()
        self.mobile_code.configure(text=f"Código: {codigo[:3]} {codigo[3:]}   (vale 5 minutos)")

    def _unpair_mobile(self) -> None:
        if not messagebox.askyesno(
            "Desvincular celulares",
            "Todos los celulares vinculados dejarán de poder usar Jarvis hasta que los vincules de nuevo. "
            "¿Continuar?",
            parent=self.root,
        ):
            return
        self.mobile.vinculacion.desvincular_todos()
        self.mobile_code.configure(text="")
        self._refresh_mobile_settings()

    def _on_mobile_text(self, texto: str) -> None:
        """Una orden escrita en el celular entra como si se escribiera en la PC."""
        if self.busy or self.recorder is not None:
            self.mobile.registro.publicar("notice", text="Estoy ocupado; espera a que termine.")
            return
        borrador = self.input.get("1.0", "end-1c")
        self.input.delete("1.0", "end")
        self.input.insert("1.0", texto)
        self._send_text()
        if borrador.strip():  # lo que se estaba escribiendo en la PC no se pierde
            estado = str(self.input.cget("state"))
            self.input.configure(state="normal")
            self.input.insert("1.0", borrador)
            self.input.configure(state=estado)
