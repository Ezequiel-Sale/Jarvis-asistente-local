"""Herramientas: los ``_tool_*`` que no son de otro tema (ventanas, navegador, volumen, tema,
juegos, limpieza, resumen del día, deshacer, archivos, ayuda) y la limpieza de disco.

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Los métodos usan
los atributos de la app (``self.root``, ``self.events``, ``self.tool_registry``…).
"""

import jarvis_buenos_dias
import jarvis_buscar_archivos
import jarvis_cleanup
import jarvis_comandos
import jarvis_core as core
import jarvis_gpu_tuning
import jarvis_routines
import jarvis_theme
import jarvis_windows
import logging
import queue
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime
from jarvis_audio_devices import cambiar_dispositivo_salida, cambiar_volumen_sistema
from jarvis_commands import normalizar_url_web, obtener_primer_video_youtube
from jarvis_system import (
    abrir_nueva_pestana_brave,
    cambiar_estado_ventana_brave,
    encontrar_brave,
    iniciar_ejecutable_juego,
    localizar_ejecutable_juego,
    navegar_en_ventana_brave,
)
from jarvis_theme import CYAN, MUTED, PANEL, PANEL_LIGHT, TEXT
from pathlib import Path
from urllib.parse import quote_plus


class HerramientasMixin:
    _MORNING_BRIEF_MS = 60000

    def _tool_morning_briefing(self, automatic: bool | None = None) -> str:
        """Resumen del día, o activa/desactiva el que sale solo por la mañana."""
        if automatic is not None:
            core.guardar_preferencia(jarvis_buenos_dias.PREFERENCIA_ACTIVO, "1" if automatic else "0")
            if automatic:
                return "Listo: cada mañana, la primera vez que uses la PC, te daré el resumen del día."
            return "Listo: ya no daré el resumen del día solo. Puedes pedirlo con «buenos días»."
        vigilante = getattr(self, "health_watcher", None)
        return jarvis_buenos_dias.generar(vigilante.ultimo if vigilante is not None else None)

    def _tool_undo_last_action(self) -> str:
        """«Deshaz lo último»: ejecuta la acción inversa de la última acción reversible."""
        deshacer = self.deshacer.sacar()
        if deshacer is None:
            return ("No tengo nada reciente para deshacer (guardo lo que se puede revertir de la "
                    "última media hora: volumen, salida de audio, pestañas, vídeos, ventanas, tema y monitores).")
        if deshacer.herramienta is not None:
            self.tool_registry.execute(deshacer.herramienta, deshacer.argumentos, confirmed=True, origin="deshacer")
        elif deshacer.accion is not None:
            deshacer.accion()
        return f"Listo, deshecho: {deshacer.descripcion[0].lower()}{deshacer.descripcion[1:]}."

    _FILE_RESULTS_TTL = 10 * 60  # «abre el segundo» vale durante 10 minutos tras buscar

    def _tool_find_files(self, query: str = "", kind: str = "any", days: int = 0,
                         open_best: bool = False) -> str:
        encontrados = jarvis_buscar_archivos.buscar(query, kind, days)
        self._last_file_results = (time.monotonic(), encontrados)
        que = f"«{query}»" if query else "archivos de ese tipo"
        if not encontrados:
            return (f"No encontré {que} en Escritorio, Documentos, Descargas, Imágenes, Vídeos ni Música "
                    "(busco por el nombre del archivo).")
        if open_best:
            elegido = jarvis_buscar_archivos.elegir_para_abrir(
                encontrados, jarvis_buscar_archivos.palabras_de(query))
            if elegido is not None:
                self.tool_registry.execute("open_file", {"path": elegido.ruta}, confirmed=True, origin="directo")
                extra = ""
                if len(encontrados) > 1:
                    extra = ("\n\nTambién encontré:\n" + jarvis_buscar_archivos.describir(encontrados)
                             + "\nSi no era ese, dime «abre el segundo», «abre el tercero»…")
                return f"Abrí «{elegido.nombre}».{extra}"
        return ("Encontré:\n" + jarvis_buscar_archivos.describir(encontrados)
                + "\nDime «abre el primero», «abre el segundo»… para abrir uno.")

    def _tool_open_file(self, path: str) -> str:
        jarvis_buscar_archivos.abrir(path)
        return f"Abrí «{Path(path).name}»."

    def _file_choice(self, texto: str) -> tuple[str, dict[str, object]] | None:
        """«Abre el segundo» justo después de buscar archivos → open_file con ese resultado."""
        numero = jarvis_buscar_archivos.detectar_orden(texto)
        guardado = getattr(self, "_last_file_results", None)
        if numero is None or not guardado or time.monotonic() - guardado[0] > self._FILE_RESULTS_TTL:
            return None
        encontrados = guardado[1]
        if not 1 <= numero <= len(encontrados):
            return None
        return "open_file", {"path": encontrados[numero - 1].ruta}

    def _tool_show_commands_help(self, topic: str = "") -> str:
        """«¿Qué puedes hacer?»: abre la guía de comandos (si Jarvis está a la vista) y lo resume."""
        try:
            propias = jarvis_comandos.tus_comandos(jarvis_routines.listar_automatizaciones())
        except Exception:
            logging.exception("No se pudieron leer las rutinas para la ayuda.")
            propias = None
        self.events.put(("show_commands_help", topic))
        return jarvis_comandos.respuesta_ayuda(topic, propias)

    def _open_commands_help(self, topic: str) -> None:
        """Abre la guía filtrada por el tema; con Jarvis en la bandeja no salta la ventana."""
        try:
            if self.root.state() in ("withdrawn", "iconic"):
                return
        except tk.TclError:
            return
        seccion = jarvis_comandos.seccion_de_tema(topic) if topic else None
        self._show_commands_help(seccion.titulo if seccion else "")

    def _check_morning_brief(self) -> None:
        """Cada minuto: la primera vez que usas la PC por la mañana, da el resumen del día."""
        if self._closing:
            return
        self.root.after(self._MORNING_BRIEF_MS, self._check_morning_brief)
        if self.busy or self.recorder is not None or self._pending_plan_reply is not None:
            return
        vigilante = getattr(self, "games_watcher", None)
        if vigilante is not None and vigilante.jugando:
            return
        try:
            toca = jarvis_buenos_dias.toca_automatico(
                datetime.now(),
                core.obtener_preferencia(jarvis_buenos_dias.PREFERENCIA_ULTIMO),
                core.obtener_preferencia(jarvis_buenos_dias.PREFERENCIA_ACTIVO),
                jarvis_buenos_dias.segundos_inactivo(),
            )
            if not toca:
                return
            core.guardar_preferencia(jarvis_buenos_dias.PREFERENCIA_ULTIMO, datetime.now().date().isoformat())
        except Exception:
            logging.exception("No se pudo revisar el resumen de la mañana.")
            return
        logging.info("Resumen automático de la mañana.")
        self._start_direct_tool_action("morning_briefing", {}, "(resumen automático de la mañana)")

    def _tool_window_control(self, app: str, action: str) -> str:
        """«minimiza Discord», «maximiza Spotify», «trae el explorador al frente»."""
        clave = jarvis_windows.normalizar(app)
        if clave == "jarvis" and action in ("window_minimize", "window_maximize"):
            return self._tool_manage_window("minimize" if action == "window_minimize" else "maximize")
        hechas, programas = jarvis_windows.cambiar_estado(app, action)
        verbo = jarvis_windows.ACCIONES_ESTADO[action][0]
        if hechas == 0:
            raise RuntimeError(f"Windows no dejó cambiar la ventana de «{app}».")
        if clave in jarvis_windows.TODO:
            return f"{verbo.capitalize()} todas las ventanas ({hechas})."
        cuantas = f"{hechas} ventanas" if hechas > 1 else "la ventana"
        return f"{verbo.capitalize()} {cuantas} de {', '.join(programas)}."

    def _tool_browser_history(self, action: str) -> str:
        return jarvis_windows.navegar_historial(action)

    def _start_cleanup(self, command: str, solo: str | None) -> None:
        """«Libera espacio»: mide (en un hilo), muestra la tarjeta con casillas y espera."""
        if self.busy or self._pending_plan_reply is not None:
            self._add_message("assistant", "Espera a que termine lo que estoy haciendo y vuelve a pedírmelo.")
            return
        self._start_busy("MIDIENDO CUÁNTO SE PUEDE LIBERAR…")
        conversation_id = self.conversation_id

        def medir() -> None:
            try:
                categorias = jarvis_cleanup.analizar()
            except Exception as error:
                logging.exception("Falló el análisis de limpieza.")
                self.events.put(("direct_action_error", "clean_disk", f"No pude medir el disco: {error}"))
                return
            self.events.put(("cleanup_preview", categorias, solo, command, conversation_id))

        threading.Thread(target=medir, name="JarvisLimpiezaAnalisis", daemon=True).start()

    def _show_cleanup_preview(self, categorias, solo, command, conversation_id) -> None:
        if solo is not None:
            categorias = [c for c in categorias if c.clave == solo]
        if not categorias:
            self._add_message("assistant", "No encontré nada que valga la pena borrar. 👍")
            self._set_idle()
            return
        if self._pending_plan_reply is not None:
            self._add_message("assistant", "Hay otra confirmación abierta; vuelve a pedírmelo después.")
            self._set_idle()
            return
        if solo is not None:
            for categoria in categorias:
                categoria.marcada = True
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        self._pending_plan_reply = reply
        self._pending_approval_kind = "cleanup"
        total = sum(c.bytes for c in categorias)
        tarjeta, cuerpo = self._confirmation_card(
            titulo=f"PUEDO LIBERAR HASTA {jarvis_cleanup.tamano_legible(total).upper()}",
            color=CYAN, aprobar="LIBERAR ESPACIO", rechazar="NO BORRAR NADA",
            alto_riesgo=True,  # borra archivos: el celular pide huella
            pista="Marca lo que quieras borrar. Di «sí» para borrar lo marcado o «no». ESC cancela.",
            resumen=f"¿Libero espacio? Hasta {jarvis_cleanup.tamano_legible(total)} (lo marcado en la tarjeta).",
        )
        self._plan_dialog = tarjeta
        marcas: dict[str, tk.BooleanVar] = {}
        seleccion = tk.Label(cuerpo, bg=PANEL_LIGHT, fg=CYAN, font=("Consolas", 9, "bold"))

        def actualizar(*_args) -> None:
            elegido = sum(c.bytes for c in categorias if marcas[c.clave].get())
            seleccion.configure(text=f"Seleccionado: {jarvis_cleanup.tamano_legible(elegido)}")

        for categoria in categorias:
            fila = tk.Frame(cuerpo, bg=PANEL_LIGHT)
            fila.pack(fill="x", pady=2)
            marca = tk.BooleanVar(value=categoria.marcada)
            marca.trace_add("write", actualizar)
            marcas[categoria.clave] = marca
            tk.Checkbutton(
                fila, variable=marca, bg=PANEL_LIGHT, activebackground=PANEL_LIGHT, selectcolor=PANEL,
                fg=TEXT, activeforeground=TEXT, text=categoria.nombre, font=("Segoe UI", 10, "bold"),
                cursor="hand2", anchor="w",
            ).pack(side="left")
            tk.Label(fila, text=jarvis_cleanup.tamano_legible(categoria.bytes), bg=PANEL_LIGHT, fg=CYAN,
                     font=("Consolas", 9, "bold")).pack(side="left", padx=(8, 0))
            tk.Label(fila, text=categoria.descripcion, bg=PANEL_LIGHT, fg=MUTED,
                     font=("Segoe UI", 9)).pack(side="left", padx=(10, 0))
        seleccion.pack(anchor="w", pady=(6, 0))
        actualizar()
        self._cleanup_marks = marcas
        self._set_plan_voice_controls(recording=False)
        self._set_status(self._approval_hint("cleanup"), CYAN)

        def esperar() -> None:
            try:
                aprobado = reply.get(timeout=300)
            except queue.Empty:
                self.events.put(("plan_confirmation_timeout", reply))
                aprobado = False
            self.events.put(("cleanup_decision", aprobado, command, conversation_id))

        threading.Thread(target=esperar, name="JarvisLimpiezaEspera", daemon=True).start()

    def _on_cleanup_decision(self, aprobado: bool, command: str, conversation_id: str) -> None:
        marcas = getattr(self, "_cleanup_marks", {}) or {}
        self._cleanup_marks = {}
        claves = [clave for clave, marca in marcas.items() if marca.get()]
        if not aprobado or not claves:
            self._add_message("assistant", "De acuerdo, no borré nada.")
            self._set_idle()
            return
        self._start_direct_tool_action("clean_disk", {"categories": claves}, command,
                                       conversation_id=conversation_id)

    def _tool_clean_disk(self, categories: list) -> str:
        resultados = jarvis_cleanup.limpiar([str(c) for c in categories])
        try:
            libre = jarvis_cleanup.tamano_legible(jarvis_cleanup.disco_libre())
            extra = f"\nAhora hay **{libre}** libres en el disco del sistema."
        except OSError:
            extra = ""
        return jarvis_cleanup.describir_resultado(resultados) + extra

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

    def _tool_apply_gpu_profile(self) -> str:
        perfil, dev = self.gpu_watcher.perfil()
        pendientes = jarvis_gpu_tuning.aplicar(perfil, dev)
        self.gpu_watcher.desbloquear()
        if pendientes:
            raise RuntimeError("El driver de AMD no aceptó todos los valores del perfil.")
        return f"Apliqué el undervolt a la GPU: {jarvis_gpu_tuning.describir(perfil)}."

    def _tool_gpu_profile_status(self) -> str:
        perfil, _dev = self.gpu_watcher.perfil()
        estado, _dev_gpu, nombre = jarvis_gpu_tuning.leer_estado()
        distintos = jarvis_gpu_tuning.diferencias(perfil, estado)
        voltaje = estado[jarvis_gpu_tuning.OD8_OD_VOLTAGE].actual
        if not distintos:
            return f"El undervolt está aplicado en la {nombre}: {jarvis_gpu_tuning.describir(perfil)}."
        vigilancia = "Lo reaplico solo en la próxima revisión." if self._gpu_profile_enabled and not \
            self.gpu_watcher.bloqueado else "Di «aplica el undervolt» para ponerlo."
        return (f"El undervolt NO está aplicado en la {nombre} (voltaje actual {voltaje:+d} mV, "
                f"{len(distintos)} valores distintos del perfil). {vigilancia}")

    def _tool_set_compact_mode(self, enabled: bool) -> str:
        return self._request_tool_ui_action("set_compact", enabled)
