"""Ajustes: interruptores de la sección ⚙ (aviso flotante, animaciones, inicio con Windows,
undervolt, resumen de la mañana, modo compacto).

Parte de ``JarvisApp`` (jarvis_app.py), que hereda de esta clase. Los métodos usan
los atributos de la app (``self.root``, ``self.events``, ``self.tool_registry``…).
"""

import jarvis_autostart
import jarvis_buenos_dias
import jarvis_core as core
import jarvis_floating
import jarvis_gpu_tuning
import logging
from jarvis_theme import CYAN, MUTED


class AjustesMixin:
    _PREFERENCIA_INICIO_WINDOWS = "inicio_windows"

    _PREFERENCIA_AVISO = "aviso_flotante"

    _PREFERENCIA_COMPACTO = "compacto_pos"

    def _gpu_profile_preferido(self) -> bool:
        """Activado si existe el perfil y el usuario no lo desactivó."""
        try:
            valor = core.obtener_preferencia(jarvis_gpu_tuning.PREFERENCIA_ACTIVO)
        except Exception:
            valor = None
        if valor is not None:
            return valor == "1"
        return jarvis_gpu_tuning.ruta_por_defecto().is_file()

    def _refresh_gpu_profile_button(self) -> None:
        boton = getattr(self, "gpu_profile_button", None)
        if boton is not None:
            boton.configure(
                text="⚡  UNDERVOLT: SÍ" if self._gpu_profile_enabled else "⚡  UNDERVOLT: NO",
                fg=CYAN if self._gpu_profile_enabled else MUTED,
            )

    def _toggle_gpu_profile(self) -> None:
        self._gpu_profile_enabled = not self._gpu_profile_enabled
        try:
            core.guardar_preferencia(jarvis_gpu_tuning.PREFERENCIA_ACTIVO, "1" if self._gpu_profile_enabled else "0")
        except Exception:
            logging.exception("No se pudo guardar el ajuste del undervolt.")
        if self._gpu_profile_enabled:
            self.gpu_watcher.desbloquear()
        self._refresh_gpu_profile_button()

    def _refresh_floating_button(self) -> None:
        boton = getattr(self, "floating_button", None)
        if boton is not None:
            boton.configure(
                text="▤  AVISO FLOTANTE: SÍ" if self._floating_enabled else "▤  AVISO FLOTANTE: NO",
                fg=CYAN if self._floating_enabled else MUTED,
            )

    def _toggle_floating(self) -> None:
        self._floating_enabled = not self._floating_enabled
        try:
            core.guardar_preferencia(self._PREFERENCIA_AVISO, "1" if self._floating_enabled else "0")
        except Exception:
            logging.exception("No se pudo guardar la preferencia del aviso flotante.")
        if not self._floating_enabled:
            self.floating.ocultar()
        self._refresh_floating_button()

    def _compact_position(self, ancho: int, alto: int) -> tuple[int, int]:
        """Posición guardada del modo compacto o, la primera vez, arriba a la derecha."""
        try:
            x, y = (int(v) for v in (core.obtener_preferencia(self._PREFERENCIA_COMPACTO) or "").split(","))
            if -10000 < x < 10000 and -10000 < y < 10000:
                return x, y
        except (ValueError, TypeError):
            pass
        except Exception:
            logging.exception("No se pudo leer la posición del modo compacto.")
        izq, arr, der, _abj = jarvis_floating.area_de_trabajo(self.root)
        return der - ancho - 24, arr + 24

    def _save_compact_position(self, x: int, y: int) -> None:
        try:
            core.guardar_preferencia(self._PREFERENCIA_COMPACTO, f"{x},{y}")
        except Exception:
            logging.exception("No se pudo guardar la posición del modo compacto.")

    _PREFERENCIA_ANIMACIONES = "animaciones"

    def _refresh_animations_button(self) -> None:
        boton = getattr(self, "animations_button", None)
        if boton is not None:
            activas = self._animations_enabled()
            boton.configure(
                text="✦  ANIMACIONES: SÍ" if activas else "✦  ANIMACIONES: NO",
                fg=CYAN if activas else MUTED,
            )

    def _refresh_morning_brief_button(self) -> None:
        boton = getattr(self, "morning_brief_button", None)
        if boton is None:
            return
        try:
            activo = core.obtener_preferencia(jarvis_buenos_dias.PREFERENCIA_ACTIVO) != "0"
        except Exception:
            activo = True
        boton.configure(text="☀  RESUMEN: SÍ" if activo else "☀  RESUMEN: NO", fg=CYAN if activo else MUTED)

    def _toggle_morning_brief(self) -> None:
        try:
            activo = core.obtener_preferencia(jarvis_buenos_dias.PREFERENCIA_ACTIVO) != "0"
            core.guardar_preferencia(jarvis_buenos_dias.PREFERENCIA_ACTIVO, "0" if activo else "1")
        except Exception:
            logging.exception("No se pudo guardar la preferencia del resumen de la mañana.")
        self._refresh_morning_brief_button()

    def _toggle_animations(self) -> None:
        self._animations = not self._animations_enabled()
        try:
            core.guardar_preferencia(self._PREFERENCIA_ANIMACIONES, "1" if self._animations else "0")
        except Exception:
            logging.exception("No se pudo guardar la preferencia de animaciones.")
        self._refresh_animations_button()

    def _autostart_preferido(self) -> bool:
        """Activado salvo que el usuario lo haya desactivado."""
        try:
            return core.obtener_preferencia(self._PREFERENCIA_INICIO_WINDOWS) != "0"
        except Exception:
            logging.exception("No se pudo leer la preferencia de inicio con Windows.")
            return True

    def _refresh_settings_info(self) -> None:
        """Datos de solo lectura en la sección Ajustes."""
        etiqueta = getattr(self, "settings_info", None)
        if etiqueta is None:
            return
        try:
            admin = "sí" if jarvis_autostart.es_administrador() else "no"
        except Exception:
            admin = "desconocido"
        etiqueta.configure(text=(
            f"MODELO DE IA     {core.MODEL_LLM}\n"
            f"VISIÓN           {core.MODEL_VISION}\n"
            f"VOZ A TEXTO      {core.STT_MODEL}\n"
            f"VOZ              {core.VOZ_PIPER}{' (' + core.VOZ_HABLANTE + ')' if core.VOZ_HABLANTE else ''}\n"
            f"ADMINISTRADOR    {admin}"
        ))

    def _refresh_autostart_button(self) -> None:
        boton = getattr(self, "autostart_button", None)
        if boton is None:
            return
        activo = self._autostart_wanted
        boton.configure(
            text="⏻  INICIO CON WINDOWS: SÍ" if activo else "⏻  INICIO CON WINDOWS: NO",
            fg=CYAN if activo else MUTED,
        )

    def _toggle_autostart_button(self) -> None:
        activar = not self._autostart_wanted
        self._execute_autostart_toggle(
            activar,
            "Botón: activar el inicio con Windows" if activar else "Botón: desactivar el inicio con Windows",
        )

    def _execute_autostart_toggle(self, enabled: bool, command: str) -> None:
        try:
            respuesta = jarvis_autostart.activar() if enabled else jarvis_autostart.desactivar()
        except OSError as error:
            logging.exception("No se pudo cambiar el inicio con Windows.")
            respuesta = f"No pude cambiar el inicio con Windows: {error}"
        else:
            self._autostart_wanted = enabled
            try:
                core.guardar_preferencia(self._PREFERENCIA_INICIO_WINDOWS, "1" if enabled else "0")
            except Exception:
                logging.exception("No se pudo guardar la preferencia de inicio con Windows.")
        try:
            core.guardar_turno(self.conversation_id, command, respuesta)
        except Exception:
            logging.exception("No se pudo guardar el cambio de inicio con Windows en memoria.")
        self._add_message("assistant", respuesta)
        self._refresh_autostart_button()
        try:
            self.tray_icon.update_menu()
        except Exception:
            logging.debug("No se pudo refrescar el menú de la bandeja.", exc_info=True)
        self._set_status(
            "SE INICIARÁ CON WINDOWS" if self._autostart_wanted else "NO SE INICIARÁ CON WINDOWS",
            CYAN if self._autostart_wanted else MUTED,
        )
