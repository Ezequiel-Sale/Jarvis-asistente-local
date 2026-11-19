"""Examen del modelo de Jarvis: manda cada orden de ``casos.json`` al modelo con las mismas
instrucciones y herramientas que usa el agente y corrige qué herramientas pidió. **No ejecuta nada**:
solo mira la primera respuesta del modelo. No lee ``jarvis_memory.db`` (datos personales): usa las
preferencias por defecto y ningún recuerdo.

Uso (con Ollama abierto):

    .venv313/Scripts/python.exe -m examen.correr_examen
    .venv313/Scripts/python.exe -m examen.correr_examen --modelo qwen3.6:27b --repeticiones 3
    .venv313/Scripts/python.exe -m examen.correr_examen --solo plan-   (los ids que empiezan así)

Deja el detalle en ``examen/resultados/`` (fuera de git).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import requests

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ.parent))

import jarvis_agent  # noqa: E402
import jarvis_core as core  # noqa: E402
import jarvis_system_info  # noqa: E402
import jarvis_temas  # noqa: E402
from examen.evaluar import CORRECTO, ILEGIBLE, NO_ACTUO, VEREDICTOS, corregir  # noqa: E402
from jarvis_tools import crear_registro_herramientas  # noqa: E402

# Lo que en Jarvis sale de la base (preferencias por defecto, sin recuerdos).
CONTEXTO_SIN_BASE = "Sistema: Responde en español. Tono: directo y útil. Usa °C si aplica."
SIN_RECUERDOS = "No hay recuerdos guardados que coincidan claramente con esta consulta."


def registro():
    """Las herramientas del agente (con búsqueda en internet y clima). Nada se ejecuta."""
    registry = crear_registro_herramientas(Mock())
    from plugins import busqueda, clima

    contexto = Mock()
    contexto.obtener_preferencia.return_value = None
    busqueda.registrar(registry, contexto)
    clima.registrar(registry, contexto)
    return registry


def mensaje_sistema(registry) -> str:
    """El mismo texto que arma ``jarvis_agent._procesar_con_herramientas``, sin la base."""
    capturado: dict = {}

    def espiar(messages, *_args, **_kwargs):
        capturado["system"] = messages[0]["content"]
        raise _Listo

    with patch.object(core, "cargar_contexto_db", return_value=CONTEXTO_SIN_BASE), \
            patch.object(core, "obtener_historial", return_value=[]), \
            patch.object(jarvis_agent.jarvis_memory, "buscar_recuerdos", return_value=[]), \
            patch.object(jarvis_agent, "_solicitar_chat", side_effect=espiar):
        try:
            jarvis_agent.procesar_con_herramientas("hola", "examen", registry, lambda *_: False)
        except _Listo:
            pass
    return capturado["system"]


class _Listo(Exception):
    pass


def lo_resuelve_sin_ia(texto: str) -> bool:
    """¿Jarvis lo haría sin preguntarle al modelo? (órdenes directas, tele, pantalla, secciones…)."""
    from jarvis_app import JarvisApp

    host = Mock()
    host._direct_tool_for = lambda t: JarvisApp._direct_tool_for(host, t)
    with patch("jarvis_routines.buscar_rutina_por_frase", return_value=None):
        try:
            return bool(JarvisApp._is_known_order(host, texto))
        except Exception:
            return False


def piensa(modelo: str) -> bool:
    """¿El modelo «razona» antes de responder (qwen3.x, etc.)? Ollama lo dice en /api/show."""
    url = core.LLM_URL.rsplit("/", 1)[0] + "/show"
    try:
        datos = requests.post(url, json={"model": modelo}, timeout=30).json()
    except (requests.RequestException, ValueError):
        return False
    return "thinking" in (datos.get("capabilities") or [])


def preguntar(modelo: str, system: str, tools: list, texto: str,
              pensar: bool | None = None) -> tuple[list[dict], str, float]:
    """``pensar``: None = no tocar (modelos sin razonamiento); False = apagarlo (Jarvis necesita
    respuestas rápidas); True = dejarlo."""
    cuerpo = {
        "model": modelo,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": texto}],
        "tools": tools,
        "stream": False,
        "keep_alive": jarvis_agent.KEEP_ALIVE,
        "options": {"num_ctx": jarvis_agent.NUM_CTX, "num_predict": jarvis_agent.MAX_TOKENS_RESPUESTA,
                    "temperature": 0.2},
    }
    if pensar is not None:
        cuerpo["think"] = pensar
    url = core.LLM_URL.rsplit("/", 1)[0] + "/chat"
    inicio = time.perf_counter()
    respuesta = requests.post(url, json=cuerpo, timeout=300)
    respuesta.raise_for_status()
    mensaje = respuesta.json()["message"]
    llamadas = jarvis_agent._extract_tool_calls(mensaje)
    escritas = bool(llamadas) and not mensaje.get("tool_calls")
    return llamadas, ("[JSON escrito] " if escritas else "") + (mensaje.get("content") or ""), \
        time.perf_counter() - inicio


def main() -> None:
    parser = argparse.ArgumentParser(description="Examen del modelo de Jarvis (no ejecuta nada).")
    parser.add_argument("--modelo", default=core.MODEL_LLM)
    parser.add_argument("--repeticiones", type=int, default=1)
    parser.add_argument("--pensar", action="store_true",
                        help="dejar que los modelos que razonan lo hagan (más lento; por defecto se apaga)")
    parser.add_argument("--solo", default="", help="solo los casos cuyo id empieza así")
    parser.add_argument("--informe", help="rehacer el informe de un resultado guardado (.json), sin preguntar")
    args = parser.parse_args()
    if args.informe:
        filas = json.loads(Path(args.informe).read_text(encoding="utf-8"))
        for f in filas:  # corrige de nuevo con las reglas actuales
            caso = next((c for c in json.loads((RAIZ / "casos.json").read_text(encoding="utf-8"))
                         if c["id"] == f["id"]), None)
            if caso is None:
                continue
            f["veredicto"], f["detalle"] = corregir(caso, f["llamadas"])
            if f["veredicto"] == NO_ACTUO and jarvis_agent._llamada_escrita(f["respuesta"]):
                f["veredicto"], f["detalle"] = ILEGIBLE, " ".join(f["respuesta"][:120].split())
        informe = resumen(filas, Path(args.informe).stem.split("-", 2)[-1])
        Path(args.informe).with_suffix(".md").write_text(informe, encoding="utf-8")
        print(informe)
        return

    casos = [c for c in json.loads((RAIZ / "casos.json").read_text(encoding="utf-8"))
             if c["id"].startswith(args.solo)]
    registry = registro()
    tools = registry.ollama_tools()
    system = mensaje_sistema(registry)
    pensar = (True if args.pensar else False) if piensa(args.modelo) else None
    print(f"Modelo {args.modelo}: {len(casos)} casos × {args.repeticiones}, {len(tools)} herramientas"
          + ("" if pensar is None else f", razonamiento {'encendido' if pensar else 'apagado'}") + ".\n")

    filas = []
    for caso in casos:
        sin_ia = lo_resuelve_sin_ia(caso["texto"])
        for vuelta in range(args.repeticiones):
            try:
                llamadas, texto, segundos = preguntar(args.modelo, system, jarvis_temas.elegir(tools, caso["texto"]),
                                                      caso["texto"], pensar)
                veredicto, detalle = corregir(caso, llamadas)
            except (requests.RequestException, KeyError, ValueError) as error:
                llamadas, texto, segundos, veredicto, detalle = [], "", 0.0, "error", str(error)
            if veredicto == NO_ACTUO and jarvis_agent._llamada_escrita(texto):
                veredicto = ILEGIBLE  # p. ej. comillas simples o dos bloques ```json: Jarvis no lo lee
                detalle = " ".join(texto[:120].split())
            if texto.startswith("[JSON escrito]") and veredicto == CORRECTO:
                detalle = "JSON escrito (Jarvis lo acepta)"
            filas.append({"id": caso["id"], "grupo": caso["grupo"], "categoria": caso["categoria"],
                          "texto": caso["texto"], "vuelta": vuelta + 1, "veredicto": veredicto, "detalle": detalle,
                          "llamadas": [{"tool": jarvis_agent_nombre(c), "args": c.get("function", {}).get("arguments")}
                                       for c in llamadas],
                          "respuesta": texto[:300], "segundos": round(segundos, 2), "sin_ia": sin_ia})
            marca = "✔" if veredicto == CORRECTO else "✘"
            print(f"{marca} {caso['id']:8} {segundos:5.1f}s  {veredicto:28} {caso['texto'][:60]}"
                  + (f"  ← {detalle[:80]}" if veredicto != CORRECTO and detalle else ""))

    informe = resumen(filas, args.modelo)
    print("\n" + informe)
    carpeta = RAIZ / "resultados"
    carpeta.mkdir(exist_ok=True)
    nombre = f"{datetime.now():%Y%m%d-%H%M}-{args.modelo.replace(':', '_').replace('/', '_')}"
    (carpeta / f"{nombre}.json").write_text(json.dumps(filas, ensure_ascii=False, indent=1), encoding="utf-8")
    (carpeta / f"{nombre}.md").write_text(informe, encoding="utf-8")
    print(f"\nDetalle en examen/resultados/{nombre}.json")


def jarvis_agent_nombre(llamada: dict) -> str:
    return str((llamada.get("function") or {}).get("name") or "")


def _porcentaje(bien: int, total: int) -> str:
    return f"{bien}/{total} ({100 * bien / total:.0f} %)" if total else "—"


def resumen(filas: list[dict], modelo: str) -> str:
    total = len(filas)
    bien = sum(f["veredicto"] == CORRECTO for f in filas)
    lineas = [f"# Examen de {modelo} — {datetime.now():%Y-%m-%d %H:%M}", "",
              f"**Aciertos: {_porcentaje(bien, total)}** · tiempo medio "
              f"{sum(f['segundos'] for f in filas) / max(1, total):.1f} s", ""]
    por_categoria = defaultdict(list)
    for f in filas:
        por_categoria[f["categoria"]].append(f)
    lineas += ["| Categoría | Aciertos |", "|---|---|"]
    for categoria, grupo in sorted(por_categoria.items()):
        lineas.append(f"| {categoria} | {_porcentaje(sum(f['veredicto'] == CORRECTO for f in grupo), len(grupo))} |")
    errores = Counter(f["veredicto"] for f in filas if f["veredicto"] != CORRECTO)
    if errores:
        lineas += ["", "| Tipo de error | Veces |", "|---|---|"]
        lineas += [f"| {v} | {errores[v]} |" for v in (*VEREDICTOS, "error") if errores.get(v)]
    # Robustez: grupos de frases que piden lo mismo con otras palabras.
    grupos = defaultdict(list)
    for f in filas:
        grupos[f["grupo"]].append(f["veredicto"] == CORRECTO)
    variados = {g: v for g, v in grupos.items() if len(v) > 1}
    if variados:
        estables = sum(all(v) for v in variados.values())
        lineas += ["", f"**Mismo pedido con otras palabras:** {estables} de {len(variados)} grupos salen bien "
                       "en todas sus variantes."]
        flojos = [g for g, v in variados.items() if not all(v)]
        if flojos:
            lineas.append("Fallan en alguna variante: " + ", ".join(sorted(flojos)) + ".")
    llm = [f for f in filas if not f["sin_ia"]]
    lineas += ["", f"**Solo lo que de verdad llega a la IA** (Jarvis no lo resuelve con sus órdenes directas): "
                   f"{_porcentaje(sum(f['veredicto'] == CORRECTO for f in llm), len(llm))}."]
    fallos = [f for f in filas if f["veredicto"] != CORRECTO]
    if fallos:
        lineas += ["", "## Fallos", "", "| Caso | Orden | Error | Qué hizo | ¿Sin IA? |", "|---|---|---|---|---|"]
        for f in fallos:
            hizo = f["detalle"] or (f["respuesta"][:80].replace("\n", " ") if f["respuesta"] else "")
            lineas.append(f"| {f['id']} | {f['texto']} | {f['veredicto']} | {hizo.replace('|', '/')} | "
                          f"{'sí' if f['sin_ia'] else 'no'} |")
    return "\n".join(lineas)


if __name__ == "__main__":
    main()
