"""Ventana «Registrar mi voz»: lees unas frases y Jarvis guarda la huella de tu voz.

Cada frase se graba con el botón (o ENTER), se calcula su huella en un hilo y el audio
se borra enseguida. Al terminar, ``al_terminar(huellas)`` la guarda (en el hilo de Tk).
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from collections.abc import Callable

import audio_capture
import jarvis_hablante
from jarvis_theme import BG, BORDER, CYAN, ERROR, HOVER, MUTED, PANEL, PANEL_LIGHT, TEXT

VOZ_MINIMA_FRASE = 1.2  # s de voz para aceptar una frase


class RegistroVoz:
    def __init__(self, padre: tk.Misc, verificador: jarvis_hablante.Verificador,
                 al_terminar: Callable[[list], None], al_abrir: Callable[[bool], None]) -> None:
        """``al_abrir(True/False)``: la app deja de escuchar mientras la ventana está abierta."""
        self.verificador = verificador
        self.al_terminar = al_terminar
        self.al_abrir = al_abrir
        self.frases = list(jarvis_hablante.FRASES_REGISTRO)
        self.huellas: list = []
        self.grabador: audio_capture.GrabadorAudio | None = None
        self.procesando = False

        self.ventana = tk.Toplevel(padre)
        self.ventana.title("Registrar mi voz")
        self.ventana.configure(bg=BG)
        self.ventana.geometry("640x380")
        self.ventana.minsize(480, 340)
        self.ventana.transient(padre)
        self.ventana.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.ventana.bind("<Return>", lambda _e: self.boton_grabar.invoke())
        self.ventana.bind("<Escape>", lambda _e: self.cerrar())

        marco = tk.Frame(self.ventana, bg=PANEL, padx=24, pady=20, highlightthickness=1,
                         highlightbackground=BORDER)
        marco.pack(fill="both", expand=True, padx=16, pady=16)
        tk.Label(marco, text="🎙  REGISTRAR MI VOZ", bg=PANEL, fg=CYAN,
                 font=("Consolas", 12, "bold")).pack(anchor="w")
        tk.Label(marco, text=("Lee cada frase en voz alta, como le hablas a Jarvis, con el micrófono "
                              "que usas siempre. Se guarda solo la «huella» de tu voz (256 números), "
                              "nunca el audio."),
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9), justify="left", wraplength=560).pack(anchor="w", pady=(4, 14))
        self.progreso = tk.Label(marco, bg=PANEL, fg=MUTED, font=("Consolas", 9, "bold"))
        self.progreso.pack(anchor="w")
        self.frase = tk.Label(marco, bg=PANEL_LIGHT, fg=TEXT, font=("Segoe UI", 15), justify="left",
                              wraplength=560, padx=16, pady=16, anchor="w")
        self.frase.pack(fill="x", pady=(6, 10))
        self.estado = tk.Label(marco, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), justify="left", wraplength=560)
        self.estado.pack(anchor="w")
        botones = tk.Frame(marco, bg=PANEL)
        botones.pack(side="bottom", fill="x", pady=(12, 0))
        self.boton_grabar = tk.Button(botones, text="🎙  GRABAR", command=self.alternar, bg=CYAN, fg=BG,
                                      activebackground=HOVER, activeforeground=TEXT, relief="flat",
                                      padx=16, pady=8, font=("Consolas", 10, "bold"), cursor="hand2")
        self.boton_grabar.pack(side="left")
        tk.Button(botones, text="CANCELAR", command=self.cerrar, bg=PANEL_LIGHT, fg=MUTED,
                  activebackground=HOVER, activeforeground=TEXT, relief="flat", padx=12, pady=8,
                  font=("Consolas", 9, "bold"), cursor="hand2").pack(side="right")
        self.al_abrir(True)
        self.mostrar()
        self.ventana.focus_force()

    def mostrar(self, aviso: str = "", color: str | None = None) -> None:
        indice = len(self.huellas)
        self.progreso.configure(text=f"FRASE {indice + 1} DE {len(self.frases)}")
        self.frase.configure(text=f"«{self.frases[indice]}»")
        self.estado.configure(text=aviso or "Pulsa GRABAR (o ENTER), lee la frase y pulsa LISTO.",
                              fg=color or MUTED)

    def alternar(self) -> None:
        if self.procesando:
            return
        if self.grabador is None:
            try:
                self.grabador = audio_capture.GrabadorAudio()
                self.grabador.iniciar()
            except Exception as error:
                logging.exception("No se pudo abrir el micrófono para registrar la voz.")
                self.grabador = None
                self.estado.configure(text=f"No pude abrir el micrófono: {error}", fg=ERROR)
                return
            self.boton_grabar.configure(text="■  LISTO", bg=ERROR)
            self.estado.configure(text="Grabando… lee la frase y pulsa LISTO (o ENTER).", fg=CYAN)
            return
        grabador, self.grabador = self.grabador, None
        self.procesando = True
        self.boton_grabar.configure(text="PROCESANDO…", bg=PANEL_LIGHT, fg=MUTED, state="disabled")
        threading.Thread(target=self._procesar, args=(grabador,), name="JarvisRegistroVoz", daemon=True).start()

    def _procesar(self, grabador) -> None:
        huella, segundos, error = None, 0.0, None
        ruta = None
        try:
            ruta = grabador.detener()
            if ruta is not None:
                huella, segundos = self.verificador.huella_de(jarvis_hablante.leer_wav(ruta))
        except Exception as exc:  # se muestra en la ventana
            logging.exception("No se pudo procesar una frase del registro de voz.")
            error = exc
        finally:
            audio_capture.limpiar_audio(ruta)
        try:
            self.ventana.after(0, lambda: self._listo(huella, segundos, error))
        except tk.TclError:
            pass  # la ventana se cerró

    def _listo(self, huella, segundos: float, error) -> None:
        self.procesando = False
        if not self.ventana.winfo_exists():
            return
        self.boton_grabar.configure(text="🎙  GRABAR", bg=CYAN, fg=BG, state="normal")
        if error is not None:
            self.mostrar(f"No pude procesarla: {error}", ERROR)
            return
        if huella is None or segundos < VOZ_MINIMA_FRASE:
            self.mostrar(f"Te escuché muy poco ({segundos:.1f} s de voz). ¿El micrófono está bajado y "
                         "activado? Repite la frase.", ERROR)
            return
        self.huellas.append(huella)
        if len(self.huellas) < len(self.frases):
            self.mostrar(f"✓ Bien ({segundos:.1f} s de voz). Sigue con la próxima.", CYAN)
            return
        huellas = self.huellas
        self._cerrar_ventana()
        self.al_terminar(huellas)

    def cerrar(self) -> None:
        if self.grabador is not None:
            self.grabador.cancelar()
            self.grabador = None
        self._cerrar_ventana()

    def _cerrar_ventana(self) -> None:
        try:
            self.ventana.destroy()
        except tk.TclError:
            pass
        self.al_abrir(False)
