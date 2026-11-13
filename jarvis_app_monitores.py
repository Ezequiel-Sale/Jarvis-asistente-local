"""Monitores: cambios con confirmación («¿Mantener?»), lotes de rutinas y planes, mover ventanas,
escalado y barra de tareas.

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Los métodos usan
los atributos de la app (``self.root``, ``self.events``, ``self.tool_registry``…).
"""

import contextlib
import jarvis_core as core
import jarvis_displays
import jarvis_monitor_view
import jarvis_vision
import jarvis_windows
import json
import logging
import queue
import tkinter as tk
from jarvis_theme import CYAN, MUTED, PANEL_LIGHT, TEXT


class MonitoresMixin:
    DISPLAY_KEEP_SECONDS = 15

    def _tool_configure_display(
        self,
        action: str,
        monitor: int,
        second_monitor: int | None = None,
    ) -> str:
        numeros = (monitor,) if second_monitor is None else (monitor, second_monitor)
        mapa = jarvis_displays.cargar_mapa(core.obtener_preferencia)
        lote = getattr(self, "_display_batch", None)
        if lote is not None and lote["previa"] is None:
            lote["antes"] = self._estado_monitores(mapa)
        try:
            descripcion, previa, mapa = jarvis_displays.aplicar(action, numeros, mapa)
        except jarvis_displays.YaEstaAsi as ya:
            return str(ya)  # «El monitor 2 ya está desactivado.»: nada que confirmar
        try:
            _pantallas, mapa = jarvis_displays.listar_pantallas(mapa)
            jarvis_displays.guardar_mapa(core.guardar_preferencia, mapa)
        except Exception:
            logging.exception("No se pudo actualizar el mapa de monitores tras el cambio.")
        lote = getattr(self, "_display_batch", None)
        if lote is not None:
            # Dentro de una rutina: se pregunta una sola vez al final (_confirm_display_batch)
            # y, si no se confirma, se vuelve a la configuración de antes del primer cambio.
            if lote["previa"] is None:
                lote["previa"] = previa
            lote["descripciones"].append(descripcion)
            return descripcion
        if self._ask_display_keep(descripcion):
            return descripcion
        jarvis_displays.restaurar_configuracion(previa)
        raise RuntimeError(
            "No confirmaste el cambio de monitores, así que restauré la configuración anterior."
        )

    def _ask_display_keep(self, descripcion: str) -> bool:
        """«¿Mantener esta configuración de pantallas?» (sin respuesta: no)."""
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
        return mantener

    def _estado_monitores(self, mapa=None) -> tuple[list[int], int | None] | None:
        """(monitores activos, principal) para saber si se encadenan rutinas."""
        try:
            pantallas, _ = jarvis_displays.listar_pantallas(mapa)
            activos = sorted(p.numero for p in pantallas if p.activa and p.numero is not None)
            return activos, jarvis_displays.numero_principal(mapa)
        except Exception:
            logging.debug("No se pudo leer el estado de los monitores.", exc_info=True)
            return None

    def _recordar_base_monitores(self, lote: dict, nombre: str) -> None:
        """Guarda cómo estaban los monitores antes de la rutina («desactiva <rutina>»).

        Si los monitores estaban como los dejó otra rutina (p. ej. «modo gaming 4K» y
        luego «2K»), se conserva la configuración de antes de la primera."""
        try:
            anterior = jarvis_displays.base_desde_texto(
                core.obtener_preferencia(jarvis_displays.PREFERENCIA_BASE))
            antes = lote.get("antes")
            if anterior is not None and antes is not None and anterior.get("despues") == list(antes):
                texto = json.loads(core.obtener_preferencia(jarvis_displays.PREFERENCIA_BASE))
            else:
                activos, principal = antes if antes is not None else ([], None)
                texto = json.loads(jarvis_displays.base_a_texto(lote["previa"], nombre, activos, principal))
            despues = self._estado_monitores()
            texto["despues"] = list(despues) if despues is not None else None
            core.guardar_preferencia(jarvis_displays.PREFERENCIA_BASE, json.dumps(texto, ensure_ascii=False))
        except Exception:
            logging.exception("No se pudo guardar la configuración de monitores anterior a la rutina.")

    def _tool_restore_display(self, routine: str = "") -> str:
        """Vuelve los monitores a como estaban antes de la rutina que los cambió."""
        datos = jarvis_displays.base_desde_texto(core.obtener_preferencia(jarvis_displays.PREFERENCIA_BASE))
        if datos is None:
            raise RuntimeError(
                "No tengo guardada la configuración de monitores de antes de una rutina; "
                "dime qué monitores activar."
            )
        if routine.strip():
            datos["rutina"] = routine.strip()
        mapa = jarvis_displays.cargar_mapa(core.obtener_preferencia)
        descripcion, previa = jarvis_displays.volver_a_base(datos, mapa)
        try:
            _pantallas, mapa = jarvis_displays.listar_pantallas(mapa)
            jarvis_displays.guardar_mapa(core.guardar_preferencia, mapa)
        except Exception:
            logging.exception("No se pudo actualizar el mapa de monitores tras restaurar.")
        lote = getattr(self, "_display_batch", None)
        if lote is not None:
            if lote["previa"] is None:
                lote["previa"] = previa
            lote["descripciones"].append(descripcion)
            lote["restaurando"] = True
            return descripcion
        if self._ask_display_keep(descripcion):
            core.guardar_preferencia(jarvis_displays.PREFERENCIA_BASE, "")
            return descripcion
        jarvis_displays.restaurar_configuracion(previa)
        raise RuntimeError(
            "No confirmaste el cambio de monitores, así que dejé la configuración como estaba."
        )

    def _start_display_batch(self) -> dict:
        """Los cambios de monitores de una rutina se confirman juntos al final."""
        lote = {"previa": None, "descripciones": []}
        self._display_batch = lote
        return lote

    @contextlib.contextmanager
    def _plan_display_scope(self, _plan):
        """Plan aprobado de la IA: sus cambios de monitores se confirman juntos al final,
        igual que en las rutinas. Lo que se deja en ``avisos`` va a la respuesta."""
        avisos: list[str] = []
        lote = self._start_display_batch()
        try:
            yield avisos
        finally:
            try:
                revertido = self._confirm_display_batch(lote, "Cambios del plan", recordar=False)
            except Exception:
                logging.exception("No se pudo confirmar o restaurar la configuración de monitores.")
                revertido = "No pude restaurar la configuración de monitores anterior."
            if revertido is not None:
                avisos.append(revertido)

    def _confirm_display_batch(self, lote: dict, nombre: str, recordar: bool = True) -> str | None:
        """Una sola pregunta para todos los cambios de monitores de la rutina.

        Devuelve None si no hubo cambios o se mantuvieron; si no, restaura la
        configuración de antes del primer cambio y devuelve el aviso."""
        if getattr(self, "_display_batch", None) is lote:
            self._display_batch = None
        if lote["previa"] is None:
            return None
        cambios = lote["descripciones"]
        descripcion = f"{nombre}: " + " ".join(cambios) if len(cambios) > 1 else cambios[0]
        if self._ask_display_keep(descripcion):
            if lote.get("restaurando"):
                core.guardar_preferencia(jarvis_displays.PREFERENCIA_BASE, "")
            elif recordar:  # solo las rutinas («desactiva <rutina>»), no los planes de la IA
                self._recordar_base_monitores(lote, nombre)
            return None
        jarvis_displays.restaurar_configuracion(lote["previa"])
        return "No confirmaste los cambios de monitores, así que restauré la configuración anterior."

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
