"""Bandeja de conversaciones del chat (2026-11-25, pedido del usuario: «generé varias y me gustaría volver a la
primera»).

El botón «☰ CONVERSACIONES» de la cabecera del chat abre un panel a la derecha con las conversaciones que
tienen mensajes (la más reciente arriba, con buscador): un clic la abre en el chat (``_open_conversation``)
y queda como la actual también al reiniciar Jarvis. «✕» la borra después de confirmar.
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime

import jarvis_core as core
import jarvis_ui_kit as kit

ANCHO = 340


def cuando(momento: datetime | None, ahora: datetime | None = None) -> str:
    """«hoy 14:05», «ayer», «lun 17/11» o «17/11/2025»."""
    if momento is None:
        return ""
    ahora = ahora or datetime.now()
    dias = (ahora.date() - momento.date()).days
    if dias == 0:
        return f"hoy {momento:%H:%M}"
    if dias == 1:
        return "ayer"
    if 1 < dias < 7:
        return ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")[momento.weekday()] + f" {momento:%d/%m}"
    return f"{momento:%d/%m}" if momento.year == ahora.year else f"{momento:%d/%m/%Y}"


def coincide(conversacion: core.ResumenConversacion, filtro: str) -> bool:
    filtro = filtro.strip().casefold()
    return not filtro or filtro in conversacion.titulo.casefold()


def alternar(app) -> None:
    """Abre o cierra la bandeja sobre la página del chat."""
    panel = app.__dict__.get("_bandeja_conversaciones")
    if panel is not None and panel.winfo_exists():
        cerrar(app)
        return
    abrir(app)


def cerrar(app) -> None:
    panel = app.__dict__.get("_bandeja_conversaciones")
    if panel is not None and panel.winfo_exists():
        panel.destroy()
    app._bandeja_conversaciones = None


def abrir(app) -> None:
    pagina = app._pages["chat"]
    panel = tk.Frame(pagina, bg=kit.color("PANEL"), highlightthickness=1,
                     highlightbackground=kit.color("BORDER_STRONG"))
    panel.place(relx=1, rely=0, relheight=1, width=ANCHO, anchor="ne")
    panel.lift()
    app._bandeja_conversaciones = panel

    cabecera = tk.Frame(panel, bg=kit.color("PANEL"), padx=14, pady=12)
    cabecera.pack(fill="x")
    tk.Label(cabecera, text="CONVERSACIONES", bg=kit.color("PANEL"), fg=kit.color("CYAN"),
             font=("Segoe UI", 11, "bold")).pack(side="left")
    kit.boton(cabecera, "✕", lambda: cerrar(app), fondo="PANEL").pack(side="right")

    caja = tk.Frame(panel, bg=kit.color("PANEL_LIGHT"), highlightthickness=1,
                    highlightbackground=kit.color("BORDER"))
    caja.pack(fill="x", padx=14, pady=(0, 8))
    tk.Label(caja, text="⌕", bg=kit.color("PANEL_LIGHT"), fg=kit.color("MUTED"),
             font=("Segoe UI", 11)).pack(side="left", padx=(8, 2))
    filtro = tk.StringVar(master=panel)
    entrada = tk.Entry(caja, textvariable=filtro, bg=kit.color("PANEL_LIGHT"), fg=kit.color("TEXT"),
                       insertbackground=kit.color("CYAN"), relief="flat", font=("Segoe UI", 10))
    entrada.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))

    cuerpo = tk.Frame(panel, bg=kit.color("PANEL"))
    cuerpo.pack(fill="both", expand=True)
    lienzo = tk.Canvas(cuerpo, bg=kit.color("PANEL"), highlightthickness=0, bd=0)
    barra = tk.Scrollbar(cuerpo, orient="vertical", command=lienzo.yview)
    lienzo.configure(yscrollcommand=barra.set)
    barra.pack(side="right", fill="y")
    lienzo.pack(side="left", fill="both", expand=True)
    lista = tk.Frame(lienzo, bg=kit.color("PANEL"))
    ventana_lista = lienzo.create_window(0, 0, window=lista, anchor="nw")
    lista.bind("<Configure>", lambda _e: lienzo.configure(scrollregion=lienzo.bbox("all")))
    lienzo.bind("<Configure>", lambda e: lienzo.itemconfigure(ventana_lista, width=e.width))
    lista.jarvis_scroll = lienzo  # la rueda del mouse mueve la lista

    try:
        conversaciones = core.listar_conversaciones()
    except Exception as error:
        kit.nota(lista, f"No pude leer las conversaciones: {error}").pack(fill="x", padx=14, pady=8)
        return

    def dibujar(*_args) -> None:
        for hijo in lista.winfo_children():
            hijo.destroy()
        visibles = [c for c in conversaciones if coincide(c, filtro.get())]
        if not visibles:
            kit.nota(lista, "Ninguna conversación con ese nombre." if filtro.get() else
                     "Todavía no hay conversaciones guardadas.").pack(fill="x", padx=14, pady=8)
        for conversacion in visibles:
            _fila(app, lista, conversacion, conversaciones, dibujar)
        lienzo.yview_moveto(0)

    filtro.trace_add("write", dibujar)
    dibujar()
    app._bandeja_lista = lista


def _fila(app, lista, conversacion, conversaciones, redibujar) -> None:
    actual = conversacion.id == app.conversation_id
    fondo = kit.color("PANEL_LIGHT" if actual else "PANEL")
    fila = tk.Frame(lista, bg=fondo, padx=14, pady=8, cursor="hand2",
                    highlightthickness=1, highlightbackground=kit.color("CYAN" if actual else "PANEL"))
    fila.pack(fill="x", padx=8, pady=2)
    titulo = tk.Label(fila, text=conversacion.titulo, bg=fondo, fg=kit.color("TEXT"), anchor="w",
                      justify="left", font=("Segoe UI", 10, "bold" if actual else "normal"),
                      wraplength=ANCHO - 90, cursor="hand2")
    titulo.pack(fill="x")
    detalle = f"{cuando(conversacion.actualizado)} · {conversacion.mensajes} mensajes"
    if actual:
        detalle += " · abierta"
    pie = tk.Label(fila, text=detalle, bg=fondo, fg=kit.color("MUTED"), anchor="w", font=("Segoe UI", 8),
                   cursor="hand2")
    pie.pack(fill="x")
    for widget in (fila, titulo, pie):
        widget.bind("<Button-1>", lambda _e, c=conversacion.id: _abrir_conversacion(app, c))
    if not actual:
        borrar = tk.Label(fila, text="✕", bg=fondo, fg=kit.color("MUTED"), cursor="hand2", font=("Segoe UI", 9))
        borrar.place(relx=1, x=-2, y=0, anchor="ne")
        borrar.bind("<Button-1>", lambda _e: _borrar(app, conversacion, conversaciones, redibujar))


def _abrir_conversacion(app, conversacion_id: str) -> None:
    if app._open_conversation(conversacion_id):
        cerrar(app)


def _borrar(app, conversacion, conversaciones, redibujar) -> str:
    from tkinter import messagebox

    if not messagebox.askyesno("Borrar conversación",
                               f"¿Borrar «{conversacion.titulo}» y sus {conversacion.mensajes} mensajes?\n"
                               "No se puede deshacer.", parent=app.root):
        return "break"
    try:
        core.borrar_conversacion(conversacion.id)
    except Exception as error:
        app._add_message("assistant", f"No pude borrar la conversación: {error}")
        return "break"
    conversaciones.remove(conversacion)
    redibujar()
    return "break"
