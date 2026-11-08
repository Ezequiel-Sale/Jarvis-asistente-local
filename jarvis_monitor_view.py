"""Vista de monitores (Ajustes): datos, disposición a escala y acciones de cada pantalla.

La parte de Tk (dibujo y menú) está en ``jarvis_ui``; aquí solo hay lógica que
se puede probar sin pantalla. Los números son los fijos de ``jarvis_displays``.
"""

from dataclasses import dataclass

import jarvis_displays


@dataclass(frozen=True)
class MonitorVista:
    numero: int
    nombre: str
    activa: bool
    rect: tuple[int, int, int, int] | None  # (x, y, ancho, alto) físicos si está activa
    escala: int | None
    principal: bool


def recolectar(mapa: dict[str, int] | None) -> list[MonitorVista]:
    """Estado actual de los monitores (activos con su posición real y apagados)."""
    import jarvis_vision

    pantallas, _ = jarvis_displays.listar_pantallas(mapa)
    geometria = {m.numero: m for m in jarvis_vision.listar_monitores()}
    vistas = []
    for p in pantallas:
        if p.numero is None:
            continue
        monitor = geometria.get(p.numero) if p.activa else None
        escala = None
        if p.activa:
            try:
                escala = jarvis_displays.leer_escala(p).actual
            except jarvis_displays.ErrorPantalla:
                escala = None
        vistas.append(MonitorVista(
            numero=p.numero,
            nombre=p.nombre,
            activa=p.activa,
            rect=(monitor.x, monitor.y, monitor.ancho, monitor.alto) if monitor else None,
            escala=escala,
            principal=bool(monitor and monitor.principal),
        ))
    return sorted(vistas, key=lambda v: v.numero)


ANCHO_APAGADOS = 120  # columna derecha para los monitores desactivados


def distribuir(
    vistas: list[MonitorVista], ancho: int, alto: int, margen: int = 14,
) -> dict[int, tuple[float, float, float, float]]:
    """Rectángulos en el lienzo: los activos a escala y en su posición real; los
    apagados, en una columna a la derecha."""
    activos = [v for v in vistas if v.activa and v.rect]
    apagados = [v for v in vistas if not (v.activa and v.rect)]
    zona_ancho = ancho - 2 * margen - (ANCHO_APAGADOS + margen if apagados else 0)
    zona_alto = alto - 2 * margen
    cajas: dict[int, tuple[float, float, float, float]] = {}
    if activos and zona_ancho > 20 and zona_alto > 20:
        izq = min(v.rect[0] for v in activos)
        arr = min(v.rect[1] for v in activos)
        der = max(v.rect[0] + v.rect[2] for v in activos)
        abj = max(v.rect[1] + v.rect[3] for v in activos)
        factor = min(zona_ancho / max(1, der - izq), zona_alto / max(1, abj - arr))
        dx = margen + (zona_ancho - (der - izq) * factor) / 2
        dy = margen + (zona_alto - (abj - arr) * factor) / 2
        hueco = 3  # separación visual entre monitores que se tocan
        for v in activos:
            x, y, w, h = v.rect
            cajas[v.numero] = (
                dx + (x - izq) * factor + hueco, dy + (y - arr) * factor + hueco,
                dx + (x + w - izq) * factor - hueco, dy + (y + h - arr) * factor - hueco,
            )
    if apagados:
        x0 = ancho - margen - ANCHO_APAGADOS
        alto_caja = min(70, (zona_alto - 8 * (len(apagados) - 1)) / len(apagados))
        for i, v in enumerate(apagados):
            y0 = margen + i * (alto_caja + 8)
            cajas[v.numero] = (x0, y0, x0 + ANCHO_APAGADOS, y0 + alto_caja)
    return cajas


def acciones(vista: MonitorVista) -> list[tuple[str, str, dict[str, object]]]:
    """(etiqueta, herramienta, argumentos) que ofrece el menú de un monitor."""
    if not vista.activa:
        return [(f"Extender el escritorio al monitor {vista.numero}", "configure_display",
                 {"action": "extend", "monitor": vista.numero})]
    opciones: list[tuple[str, str, dict[str, object]]] = []
    if not vista.principal and vista.numero in jarvis_displays.MONITORES_PRINCIPALES_PERMITIDOS:
        opciones.append(("Hacer monitor principal", "configure_display",
                         {"action": "set_primary", "monitor": vista.numero}))
    opciones.append(("Arreglar el escalado", "fix_display_scaling", {"monitor": vista.numero}))
    opciones.append((f"Desactivar el monitor {vista.numero}", "configure_display",
                     {"action": "disable", "monitor": vista.numero}))
    return opciones


def describir(vista: MonitorVista, *, compacto: bool = False) -> str:
    """«300 % · 3840×2160 · principal»; en cajas chicas, sin la resolución."""
    if not vista.activa:
        return "APAGADO"
    partes = []
    if vista.escala:
        partes.append(f"{vista.escala} %")
    if vista.rect and not compacto:
        partes.append(f"{vista.rect[2]}×{vista.rect[3]}")
    if vista.principal:
        partes.append("principal")
    return " · ".join(partes)
