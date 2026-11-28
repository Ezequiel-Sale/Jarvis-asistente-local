// Jarvis para Brave: pide órdenes a Jarvis (http://127.0.0.1:8766) con su token, las hace en la
// pestaña activa y devuelve el resultado. No habla con ningún otro servidor.
// config.js lo escribe Jarvis al arrancar (JARVIS_TOKEN y JARVIS_PUERTO).
importScripts("config.js");

const BASE = `http://127.0.0.1:${JARVIS_PUERTO}`;
// Bancos, pagos y gestores de contraseñas: nunca se leen ni se tocan (lo mismo controla Jarvis).
const PROTEGIDOS = /(?:banco|banca|banking|homebanking|santander|galicia|bbva|macro|mercadopago|mercado pago|paypal|binance|coinbase|billetera|wallet|bitwarden|1password|keepass|lastpass|authenticator|accounts\.google|login\.live|contrase|password)/i;
let escuchando = false;

async function pestanaActiva() {
  const [tab] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
  if (!tab || !tab.id) throw new Error("No hay una pestaña activa en Brave.");
  if (!/^https?:/.test(tab.url || "")) throw new Error("Esa pestaña no es una página web (" + (tab.url || "vacía") + ").");
  if (PROTEGIDOS.test((tab.url || "") + " " + (tab.title || ""))) throw new Error("Esa página es de un banco, pagos o contraseñas: no la toco.");
  return tab;
}

// Corre dentro de la página. Numera lo que se puede usar y devuelve el texto visible.
function leerPagina(maxTexto) {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
  };
  const nombre = (el) => (el.getAttribute("aria-label") || el.innerText || el.value || el.placeholder ||
                          el.title || el.alt || el.name || "").replace(/\s+/g, " ").trim().slice(0, 80);
  document.querySelectorAll("[data-jarvis-n]").forEach((el) => el.removeAttribute("data-jarvis-n"));
  const selector = "a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], " +
                   "[role=checkbox], [role=tab], [role=menuitem], [contenteditable=true], summary";
  const elementos = [];
  let n = 0;
  for (const el of document.querySelectorAll(selector)) {
    if (!visible(el) || el.disabled) continue;
    n += 1;
    if (n > 250) break;
    el.setAttribute("data-jarvis-n", String(n));
    const tag = el.tagName.toLowerCase();
    const tipo = tag === "input" ? (el.type || "text") : (el.getAttribute("role") || tag);
    const secreto = tag === "input" && (el.type === "password" || /pass|contrase|cvv|cvc|tarjeta|card/i.test(el.name + " " + el.id + " " + el.autocomplete));
    const datos = {n, tipo, nombre: nombre(el)};
    if (secreto) datos.secreto = true;
    else if (tag === "input" && (el.type === "checkbox" || el.type === "radio")) datos.marcado = el.checked;
    else if (tag === "input" || tag === "textarea") datos.valor = (el.value || "").slice(0, 80);
    else if (tag === "select") datos.valor = el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : "";
    elementos.push(datos);
  }
  const texto = (document.body ? document.body.innerText : "").replace(/\n{3,}/g, "\n\n").slice(0, maxTexto);
  return {titulo: document.title, url: location.href, elementos, texto};
}

// Corre dentro de la página: hace la acción sobre el elemento número n de la última lectura.
function actuar(n, accion, texto) {
  const el = document.querySelector(`[data-jarvis-n="${n}"]`);
  if (!el) return {ok: false, error: "Ese elemento ya no está: volvé a leer la página."};
  const tag = el.tagName.toLowerCase();
  if (tag === "input" && (el.type === "password" || /pass|contrase|cvv|cvc/i.test(el.name + " " + el.id))) {
    return {ok: false, error: "Es un campo de contraseña o de tarjeta: no lo completo."};
  }
  el.scrollIntoView({block: "center"});
  if (accion === "click") {
    el.focus();
    el.click();
    return {ok: true};
  }
  if (accion === "fill") {
    el.focus();
    if (el.isContentEditable) {
      el.textContent = texto;
    } else {
      const proto = tag === "textarea" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;  // para React y similares
      setter.call(el, texto);
    }
    el.dispatchEvent(new Event("input", {bubbles: true}));
    el.dispatchEvent(new Event("change", {bubbles: true}));
    return {ok: true, valor: (el.value || el.textContent || "").slice(0, 80)};
  }
  if (accion === "select") {
    if (tag !== "select") return {ok: false, error: "Ese elemento no es una lista para elegir."};
    const opcion = [...el.options].find((o) => o.text.trim().toLowerCase() === texto.trim().toLowerCase())
                || [...el.options].find((o) => o.text.toLowerCase().includes(texto.trim().toLowerCase()));
    if (!opcion) return {ok: false, error: "No encontré esa opción en la lista."};
    el.value = opcion.value;
    el.dispatchEvent(new Event("change", {bubbles: true}));
    return {ok: true, valor: opcion.text};
  }
  if (accion === "submit") {
    const formulario = el.form || el.closest("form");
    if (formulario) { formulario.requestSubmit ? formulario.requestSubmit() : formulario.submit(); return {ok: true}; }
    el.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", code: "Enter", keyCode: 13, bubbles: true}));
    return {ok: true};
  }
  return {ok: false, error: "Acción desconocida."};
}

// «Mueve la pestaña 3 al monitor 2»: la saca a una ventana propia (Jarvis la lleva al monitor).
// No lee ni toca la página, así que vale para cualquier pestaña.
async function moverPestana(numero) {
  const ventana = await chrome.windows.getLastFocused({populate: true, windowTypes: ["normal"]});
  const pestanas = (ventana && ventana.tabs) || [];
  const tab = numero ? pestanas[numero - 1] : pestanas.find((t) => t.active);
  if (!tab) throw new Error(`Esa ventana de Brave tiene ${pestanas.length} pestañas: no hay una número ${numero}.`);
  if (pestanas.length === 1) return {titulo: tab.title || "", sola: true};
  const nueva = await chrome.windows.create({tabId: tab.id, focused: true});
  return {titulo: tab.title || "", sola: false, ventana: nueva.id};
}

// Abrir una pestaña o navegar sin teclado (2026-11-28): con un video en pantalla completa los atajos
// (Ctrl+T, Ctrl+L) no llegaban a Brave y Jarvis igual informaba que lo había hecho.
async function abrirUrl(url, nueva) {
  if (url && !/^https?:\/\//i.test(url)) throw new Error("Solo abro direcciones http o https.");
  const ventana = await chrome.windows.getLastFocused({populate: true, windowTypes: ["normal"]}).catch(() => null);
  if (nueva || !ventana) {
    const datos = {active: true};
    if (url) datos.url = url;
    if (ventana) datos.windowId = ventana.id;
    const tab = await chrome.tabs.create(datos);
    if (ventana) await chrome.windows.update(ventana.id, {focused: true});
    return {id: tab.id};
  }
  const activa = (ventana.tabs || []).find((t) => t.active);
  if (!activa) throw new Error("No hay una pestaña activa en Brave.");
  await chrome.tabs.update(activa.id, {url});
  await chrome.windows.update(ventana.id, {focused: true});
  return {id: activa.id};
}

async function ejecutar(orden) {
  if (orden.accion === "abrir_url") return await abrirUrl(orden.url || "", !!orden.nueva);
  if (orden.accion === "mover_pestana") return await moverPestana(orden.numero);
  const tab = await pestanaActiva();
  if (orden.accion === "leer") {
    const [r] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: leerPagina, args: [orden.max_texto || 6000]});
    return r.result;
  }
  if (["click", "fill", "select", "submit"].includes(orden.accion)) {
    const [r] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: actuar,
                                                      args: [orden.n, orden.accion, orden.texto || ""]});
    if (!r.result.ok) throw new Error(r.result.error);
    return r.result;
  }
  throw new Error("Orden desconocida: " + orden.accion);
}

async function escuchar() {
  if (escuchando) return;
  escuchando = true;
  try {
    while (true) {
      const respuesta = await fetch(`${BASE}/siguiente`, {headers: {"X-Jarvis-Token": JARVIS_TOKEN}, cache: "no-store"});
      if (respuesta.status === 204) continue;
      if (!respuesta.ok) throw new Error("Jarvis respondió " + respuesta.status);
      const orden = await respuesta.json();
      let resultado;
      try {
        resultado = {id: orden.id, ok: true, datos: await ejecutar(orden)};
      } catch (error) {
        resultado = {id: orden.id, ok: false, error: String(error.message || error)};
      }
      await fetch(`${BASE}/resultado`, {method: "POST", headers: {"X-Jarvis-Token": JARVIS_TOKEN,
                  "Content-Type": "application/json"}, body: JSON.stringify(resultado)});
    }
  } catch (error) {
    // Jarvis cerrado o reiniciándose: se reintenta con la alarma.
  } finally {
    escuchando = false;
  }
}

chrome.alarms.create("jarvis", {periodInMinutes: 0.5});
chrome.alarms.onAlarm.addListener(() => escuchar());
chrome.runtime.onStartup.addListener(() => escuchar());
chrome.runtime.onInstalled.addListener(() => escuchar());
escuchar();
