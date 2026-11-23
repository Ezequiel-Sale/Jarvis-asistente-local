// Jarvis para Brave: pulsa solo «Omitir intro», «Omitir créditos» y parecidos en cuanto aparecen
// (2026-11-23, pedido del usuario para Crunchyroll). Corre dentro de las páginas de streaming de
// manifest.json, también en el iframe del reproductor. No lee ni envía nada: solo mira el texto
// de los botones de esta página.
(() => {
  const OMITIR = /^(?:omitir|saltar|skip)\s+(?:la\s+|el\s+)?(?:intro|introducci[oó]n|opening|apertura|cr[eé]ditos|credits|ending|resumen|recap)$/i;
  // Botón → texto con el que se pulsó. Crunchyroll reutiliza el mismo botón: lo esconde tras la
  // intro y lo vuelve a mostrar como «Omitir créditos» (2026-11-23: con un WeakSet ya no se
  // pulsaba). Se olvida cuando el botón desaparece, y se vuelve a pulsar si cambia el texto.
  const pulsados = new Map();

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
  };

  function revisar() {
    for (const boton of [...pulsados.keys()]) {
      if (!boton.isConnected || !visible(boton)) pulsados.delete(boton);
    }
    // El elemento MÁS INTERNO con ese texto (el <span> del botón): el clic sube desde ahí hasta el
    // botón real. 2026-11-23: se tomaba el primero, a veces el contenedor de afuera del botón, y
    // ese clic no llegaba al botón.
    const texto = (el) => (el.innerText || el.textContent || el.getAttribute("aria-label") || "")
      .replace(/\s+/g, " ").trim();
    const omite = (el) => { const t = texto(el); return t.length > 0 && t.length <= 40 && OMITIR.test(t); };
    for (const el of document.querySelectorAll("button, [role=button], [data-testid*=skip i], span, div")) {
      if (el.childElementCount > 2 || !omite(el)) continue;
      if ([...el.children].some(omite)) continue;  // hay uno más adentro: se toma ese
      const dicho = texto(el);
      const boton = el.closest("button, [role=button]") || el.closest("[data-testid*=skip i]") || el;
      if (pulsados.get(boton) === dicho || !visible(boton) || !visible(el)) continue;
      pulsados.set(boton, dicho);
      el.click();
      console.info(`Jarvis: pulsé «${dicho}».`);
      return;
    }
  }

  let pendiente = false;
  const programar = () => {
    if (pendiente) return;
    pendiente = true;
    setTimeout(() => { pendiente = false; revisar(); }, 250);
  };
  new MutationObserver(programar).observe(document.documentElement, {childList: true, subtree: true, characterData: true});
  setInterval(revisar, 1000);  // por si el botón aparece sin cambios en el DOM (solo se hace visible)
})();
