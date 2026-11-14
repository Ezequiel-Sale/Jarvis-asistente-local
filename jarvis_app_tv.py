"""Televisor: herramientas (``tv_control``, ``tv_open_app``, ``tv_status``), vinculación desde
Ajustes y el control remoto de la página 📺 TELE y del celular.

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. ``self.tv`` es un
``jarvis_tv.ControlTV``; sus avisos de cambio llegan como evento ``tv_state``.
"""

import logging
import threading

import jarvis_tv

# Nombres de las apps que informa la tele (paquete de Android) → como se dicen.
APPS_CONOCIDAS = {
    "com.google.android.youtube.tv": "YouTube", "com.netflix.ninja": "Netflix",
    "com.disney.disneyplus": "Disney+", "com.amazon.amazonvideo.livingroom": "Prime Video",
    "com.wbd.stream": "Max", "com.hbo.hbonow": "Max", "com.spotify.tv.android": "Spotify",
    "tv.twitch.android.app": "Twitch", "com.crunchyroll.crunchyroid": "Crunchyroll",
    "com.nousguide.android.rbtv": "Red Bull TV", "com.plexapp.android": "Plex",
    "com.google.android.apps.tv.launcherx": "la pantalla de inicio",
    "com.google.android.tvlauncher": "la pantalla de inicio", "com.cbs.ott": "Paramount+",
    "com.apple.atve.androidtv.appletv": "Apple TV", "com.cablevision.flow": "Flow",
    "com.google.android.apps.tv.dreamx": "el protector de pantalla", "com.tcl.tv": "una entrada HDMI",
    "com.android.tv.settings": "la configuración", "com.google.android.youtube.tvkids": "YouTube Kids",
}
TEXTOS_TECLA = {
    "mute": "Silencié la tele (o le devolví el sonido).", "play_pause": "Pausé o reanudé la tele.",
    "play": "Le di play en la tele.", "pause": "Pausé la tele.", "stop": "Detuve la reproducción en la tele.",
    "next": "Pasé al siguiente en la tele.", "previous": "Volví al anterior en la tele.",
    "forward": "Adelanté en la tele.", "rewind": "Retrocedí en la tele.", "home": "Fui al inicio en la tele.",
    "back": "Volví atrás en la tele.", "settings": "Abrí el panel de la tele: ahí está Configuración.",
    "input": "Abrí el panel de la tele: ahí están las Entradas.", "channel_up": "Subí de canal.", "channel_down": "Bajé de canal.",
}


def nombre_app(paquete: str) -> str:
    return APPS_CONOCIDAS.get(paquete, paquete.rsplit(".", 1)[-1].capitalize() if paquete else "")


def etiqueta_app(paquete: str) -> str:
    """Nombre corto para mostrar en el control («Inicio», «Protector de pantalla», «Netflix»)."""
    nombre = nombre_app(paquete)
    for articulo in ("la pantalla de ", "el ", "la ", "una "):
        if nombre.startswith(articulo):
            nombre = nombre[len(articulo):]
            break
    return nombre[:1].upper() + nombre[1:]


def resumen_estado(estado: "jarvis_tv.EstadoTV") -> tuple[str, str, str]:
    """Para la tarjeta de estado del control: (estado corto, detalle, tono: on/off/error)."""
    if not estado.vinculada:
        return "Sin vincular", "Vincúlala en ⚙ Ajustes → Televisor.", "error"
    if estado.esperando_codigo:
        return "Vinculando", "Escribe el código que muestra la tele.", "off"
    if not estado.conectada:
        return "Sin conexión", "Puede estar apagada del todo o sin Wi-Fi.", "error"
    if estado.encendida is False:
        return "Apagada", "En reposo: «Prender» la enciende.", "off"
    partes = []
    if estado.app:
        partes.append(etiqueta_app(estado.app))
    if estado.volumen is not None:
        partes.append(f"Volumen {estado.volumen}" + (" · silenciada" if estado.silenciada else ""))
    return "Encendida", " · ".join(partes), "on"


def describir_estado(estado: "jarvis_tv.EstadoTV") -> str:
    if not estado.vinculada:
        return "La tele no está vinculada todavía (⚙ Ajustes → Televisor → VINCULAR)."
    if not estado.conectada:
        return f"No estoy conectado a {estado.nombre or 'la tele'}: puede estar apagada del todo o sin Wi-Fi."
    if estado.encendida is False:
        return f"{estado.nombre or 'La tele'} está apagada (en reposo)."
    partes = [f"{estado.nombre or 'La tele'} está encendida"]
    if estado.app:
        partes.append(f"con {nombre_app(estado.app)}")
    texto = ", ".join(partes)
    if estado.volumen is not None:
        texto += f"; volumen en {estado.volumen}" + (f" de {estado.volumen_max}" if estado.volumen_max else "")
        if estado.silenciada:
            texto += " (silenciada)"
    return texto + "."


class TvMixin:
    # --- herramientas -------------------------------------------------------------
    def _tool_tv_control(self, action: str, steps: int = 1, level: int | None = None) -> str:
        tv = self.tv
        if action == "power_on":
            return tv.encender()
        if action == "power_off":
            return tv.apagar()
        if action == "set_volume":
            if level is None:
                raise ValueError("Falta el volumen (0 a 100).")
            return tv.fijar_volumen(int(level))
        if action in ("volume_up", "volume_down"):
            pasos = max(1, min(int(steps or 1), jarvis_tv.MAX_PASOS_VOLUMEN))
            tv.tecla(action, pasos)
            return f"{'Subí' if action == 'volume_up' else 'Bajé'} el volumen de la tele ({pasos} pasos)."
        if action.startswith("hdmi"):
            tv.tecla(action)
            return f"Puse la tele en la entrada HDMI {action[4:]}."
        tv.tecla(action)
        return TEXTOS_TECLA.get(action, "Listo.")

    def _tool_tv_open_app(self, app: str) -> str:
        return f"Abrí {self.tv.abrir_app(app)} en la tele."

    def _tool_tv_status(self) -> str:
        if self.tv.estado.vinculada and not self.tv.estado.conectada:
            self.tv.conectar()
        return describir_estado(self.tv.estado)

    _TV_RETRY_MS = 60000

    def _check_tv_connection(self) -> None:
        """Cada minuto, en otro hilo: reconectar la tele si se prendió con su control."""
        if getattr(self, "_closing", False):
            return
        self.root.after(self._TV_RETRY_MS, self._check_tv_connection)
        if getattr(self, "_tv_retry_running", False):
            return
        self._tv_retry_running = True

        def intentar() -> None:
            try:
                self.tv.intentar_en_segundo_plano()
            except Exception:
                logging.debug("Falló el reintento de conexión con la tele.", exc_info=True)
            finally:
                self._tv_retry_running = False

        threading.Thread(target=intentar, name="JarvisTVReintento", daemon=True).start()

    # --- control remoto (página 📺 TELE y celular) ----------------------------------
    def _tv_button(self, accion: str) -> None:
        """Botón del control: por el registro de herramientas, en otro hilo."""
        if accion.startswith("app:"):
            herramienta, argumentos = "tv_open_app", {"app": accion[4:]}
        else:
            herramienta, argumentos = "tv_control", {"action": accion}

        def ejecutar() -> None:
            try:
                self.tool_registry.execute(herramienta, argumentos, confirmed=True, origin="control")
            except Exception as error:
                self.events.put(("tv_error", str(error)))

        threading.Thread(target=ejecutar, name="JarvisTVBoton", daemon=True).start()

    def _tv_for_mobile(self) -> dict:
        datos = self.tv.estado.como_dict()
        datos["appName"] = etiqueta_app(self.tv.estado.app)
        datos["apps"] = [{"id": clave, "name": nombre} for clave, (nombre, _enlace) in jarvis_tv.APPS.items()]
        return datos

    def _tv_action_from_mobile(self, accion: str) -> str:
        if accion.startswith("app:"):
            return str(self.tool_registry.execute("tv_open_app", {"app": accion[4:]}, confirmed=True, origin="celular"))
        return str(self.tool_registry.execute("tv_control", {"action": accion}, confirmed=True, origin="celular"))

    # --- vincular desde Ajustes ---------------------------------------------------------
    def _tv_search(self) -> None:
        self._set_status("BUSCANDO LA TELE…", self._tv_color())

        def buscar() -> None:
            try:
                encontradas = jarvis_tv.buscar()
            except Exception as error:
                logging.exception("No se pudo buscar la tele.")
                self.events.put(("tv_error", f"No pude buscar la tele: {error}"))
                return
            if not encontradas:
                self.events.put(("tv_error", "No encontré ninguna tele Google TV en la red. ¿Está encendida?"))
                return
            nombre, ip = encontradas[0]
            self.tv.elegir(ip, nombre)
            self.events.put(("tv_info", f"Encontré «{nombre}» ({ip}). Pulsa VINCULAR para conectarla."))

        threading.Thread(target=buscar, name="JarvisTVBuscar", daemon=True).start()

    def _tv_pair_start(self) -> None:
        def empezar() -> None:
            try:
                self.tv.iniciar_vinculacion()
                self.events.put(("tv_info", "La tele muestra un código de 6 caracteres: escríbelo y pulsa CONFIRMAR."))
            except Exception as error:
                self.events.put(("tv_error", str(error)))

        threading.Thread(target=empezar, name="JarvisTVVincular", daemon=True).start()

    def _tv_pair_finish(self) -> None:
        codigo = self.tv_code_entry.get()

        def terminar() -> None:
            try:
                self.tv.terminar_vinculacion(codigo)
                self.events.put(("tv_info", "¡Listo! La tele quedó vinculada con Jarvis."))
            except Exception as error:
                self.events.put(("tv_error", str(error)))

        threading.Thread(target=terminar, name="JarvisTVCodigo", daemon=True).start()

    def _tv_forget(self) -> None:
        self.tv.olvidar()
        self._refresh_tv_views()

    def _tv_color(self):
        from jarvis_theme import CYAN

        return CYAN

    def _refresh_tv_views(self) -> None:
        """Hilo de Tk: actualiza el estado en Ajustes y en la página 📺 TELE."""
        estado = self.tv.estado
        texto = describir_estado(estado)
        if estado.esperando_codigo:
            texto = "Esperando el código que muestra la tele…"
        etiqueta = getattr(self, "tv_settings_status", None)
        if etiqueta is not None:
            try:
                etiqueta.configure(text=texto)
            except Exception:
                pass
        pintar = getattr(self, "_paint_tv_status", None)
        if pintar is not None:
            try:
                pintar(estado.nombre or "La tele", *resumen_estado(estado))
            except Exception:
                logging.debug("No se pudo pintar el estado de la tele.", exc_info=True)
