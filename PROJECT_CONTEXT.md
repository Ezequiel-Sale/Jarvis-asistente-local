# PROJECT CONTEXT

> Documento de contexto para agentes de IA. Última revisión: 2026-11-05 (roadmap completo, puntos 1–6: seguimiento de acciones, consultas del sistema, automatizaciones, observabilidad, plugins/MCP y visión de pantalla).
> Convención: **[Verificado]** = comprobado leyendo código/config o ejecutando; **[Inferido]** = deducción razonable no confirmada.
> No hay README ni repositorio git: este archivo es la única documentación del proyecto.

---

## 1. Descripción general

- **Nombre:** J.A.R.V.I.S. — "Asistente local" (título de ventana en `jarvis_app.py`).
- **Propósito:** asistente personal de escritorio **en español**, controlado por texto o voz, que funciona 100 % en local: STT (Whisper), LLM (Ollama) y TTS (Piper) corren en contenedores/servicios locales.
- **Problema que resuelve:** controlar el PC por voz/texto (volumen, dispositivo de salida, navegador Brave, YouTube, lanzar juegos, ventanas), mantener conversaciones con memoria persistente y programar rutinas recurrentes, sin depender de la nube.
- **Tipo:** aplicación de escritorio Windows (Tkinter + icono de bandeja), empaquetable como `.exe` con PyInstaller. Existe además un modo CLI legado en `jarvis_core.py`.
- **Usuarios:** uso personal de un único usuario. [Inferido] Hay valores fijos del hardware/biblioteca del autor (auriculares *G533*, juegos concretos en `C:\Games` y Steam).

## 2. Stack tecnológico

| Área | Tecnología | Fuente |
|---|---|---|
| Lenguaje | Python ≥ 3.11 (usa `StrEnum`, `X | None`). Venv funcional: `.venv313` (Python 3.13) | `jarvis_tool_registry.py`, `.venv313/pyvenv.cfg` |
| GUI | Tkinter / ttk (ventana principal + ventana de chat + diálogos) | `jarvis_ui.py`, `jarvis_app.py` |
| Bandeja | `pystray` + `Pillow` | `jarvis_app.py` |
| Audio | `PyAudio` (captura 16 kHz mono y reproducción WAV) | `audio_capture.py`, `jarvis_player.py` |
| HTTP | `requests` (STT, Ollama, scraping de YouTube) | |
| Sistema | `psutil` (procesos, CPU, RAM, disco, batería; solo lectura) | `jarvis_system_info.py` |
| Integraciones | SDK `mcp` 2.3 (cliente stdio; API en snake_case: `server_info`, `input_schema`, `read_only_hint`) y Node.js/npx para servidores MCP; Open-Meteo (clima, sin clave) | `jarvis_mcp.py`, `plugins/clima.py` |
| Métricas | `prometheus_client` (servidor en puerto 8000) | `jarvis_core.py` |
| Win32 | `ctypes` puro: COM Core Audio (volumen/dispositivo), `RegisterHotKey` (F9), enumeración de ventanas Brave | `jarvis_audio_devices.py`, `jarvis_hotkeys.py`, `jarvis_system.py` |
| BD | SQLite (`sqlite3` stdlib, SQL crudo, **sin ORM**) | `jarvis_core.py`, `init_db.sql` |
| STT | `speaches` (faster-whisper, API compatible OpenAI) modelo `Systran/faster-whisper-small`, en CPU con `WHISPER__TTL=-1` (modelo siempre cargado), `WHISPER__COMPUTE_TYPE=int8`, `WHISPER__CPU_THREADS=8` | `docker-compose.yml` |
| Palabra de activación | openWakeWord (modelo `hey_jarvis`, ONNX en CPU, ~1,4 ms por bloque de 80 ms) | `jarvis_wakeword.py` |
| LLM | Ollama **nativo** (app de escritorio; debe estar abierta), modelo `qwen2.5-coder:14b`; visión: `qwen3.6:27b` (`MODEL_VISION`, ~18 GB, desplaza al principal de la GPU de 16 GB: ~40 s en frío, ~8 s si sigue cargado) | `jarvis_core.py` (`MODEL_LLM`); confirmado por el usuario |
| TTS | `wyoming-piper`, voz `es_ES-davefx-medium` (protocolo Wyoming por TCP) | `docker-compose.yml`, `jarvis_core.py` |
| Observabilidad | Prometheus + Grafana (docker-compose) | `prometheus.yml` |
| Build | PyInstaller (`Jarvis.spec`, modo ventana, sin consola) | |
| Tests | `unittest` + `unittest.mock` (64 tests) | `tests/` |

`requirements.txt` fija versiones exactas (probadas con Python 3.13; al actualizar una, pasar los tests y cambiarla en el mismo commit); `requirements-dev.txt` añade PyInstaller. Lista: Pillow, PyAudio, prometheus-client, pystray, requests, psutil, mcp, hidapi, openwakeword, pycaw, winrt-runtime, winrt-Windows.Foundation, winrt-Windows.Media.Control. Los modelos de openWakeWord no vienen con el paquete: `python -c "from openwakeword import utils; utils.download_models(model_names=['hey_jarvis'])"` (quedan en `site-packages/openwakeword/resources/models`; `Jarvis.spec` los incluye con `collect_data_files('openwakeword')`). Licencia de los modelos preentrenados: CC BY-NC-SA 4.0 (uso no comercial); no se suben al repo. Los servidores MCP por defecto necesitan Node.js (`npx`) en PATH. PyInstaller está instalado en `.venv313` pero no figura en requirements.

## 3. Arquitectura del proyecto

Monolito de escritorio con módulos planos en la raíz. Patrones clave:

- **`JarvisApp`** (`jarvis_app.py`) es el orquestador: hereda mixins de UI (`JarvisUIMixin`) y reproductor (`AudioPlayerMixin`), y actúa como *host* de las herramientas (métodos `_tool_*`).
- **Hilos + cola de eventos:** el trabajo pesado (STT, LLM, TTS, Docker, rutinas, juegos) corre en hilos daemon; se comunican con el hilo de Tk **solo** vía `self.events: queue.Queue[tuple]`, consumida por `_poll_events()` cada 100 ms. Las confirmaciones desde hilos usan una `queue.Queue(maxsize=1)` de respuesta con *timeout* (120 s; 15 s para `tool_ui_request`).
- **Enrutamiento por capas** de cada entrada del usuario (texto o transcripción):
  1. Comandos directos por regex (`jarvis_commands.detectar_*`): audio on/off, dispositivo de salida, volumen, YouTube (búsqueda / resultado ordinal), nueva pestaña, navegación, ventanas, juegos. Se ejecutan **sin pasar por el LLM**.
  2. `_process_agent_query`: comandos de memoria (`jarvis_memory.procesar_comando`) → comandos de rutinas (`jarvis_routines.procesar_comando`) → creación de rutina por regex (`parsear_solicitud_creacion`) → **agente LLM con herramientas** (`jarvis_agent.procesar_con_herramientas`).
  3. Se guarda el turno en SQLite, se genera TTS y se reproduce.
- **Registro de herramientas** (`ToolRegistry`) con esquema JSON, validadores y nivel de riesgo (LOW / MEDIUM / HIGH) que determina las confirmaciones.
- **Ejecución supervisada:** **toda** acción (agente, comando directo, rutina) pasa por `ToolRegistry.execute`, que aplica cancelación, tiempo máximo por herramienta, verificación posterior opcional y registra el resultado (log, Prometheus, tabla `acciones`) mediante el `recorder`. Ver `jarvis_actions.py`.

```mermaid
flowchart LR
  U[Usuario: texto / micrófono / F9] --> APP[JarvisApp - hilo Tk]
  APP -- hilo --> STT[speaches :9000]
  APP --> R{Regex directos<br/>jarvis_commands}
  R -- coincide --> REG
  R -- no --> Q[_process_agent_query]
  Q --> MEM[jarvis_memory]
  Q --> ROU[jarvis_routines]
  Q --> AG[jarvis_agent]
  AG -- /api/chat + tools --> LLM[Ollama :11434]
  AG --> REG[ToolRegistry.execute<br/>timeout · cancelación · verificación] --> SYS[Win32 / Brave / juegos]
  REG -- recorder --> LOG[(tabla acciones + métricas)]
  Q --> DB[(SQLite jarvis_memory.db)]
  Q --> TTS[Piper Wyoming :5555] --> WAV[responses/*.wav] --> PLAY[AudioPlayerMixin]
  SCHED[Planificador cada 15 s] --> ROU
  APP -. métricas :8000 .-> PROM[Prometheus :9090] --> GRAF[Grafana :3000]
```

## 4. Estructura de directorios

```
JARVIS/
├── jarvis_app.py            # Punto de entrada GUI (main). Clase JarvisApp: enrutamiento, eventos, handlers de herramientas
├── jarvis_ui.py             # JarvisUIMixin: layout Tk, orbe animado, burbujas de chat, estados
├── jarvis_player.py         # AudioPlayerMixin: reproducción WAV con PyAudio (play/pausa/stop, progreso)
├── jarvis_theme.py          # Paleta de colores (constantes)
├── jarvis_core.py           # Config global, Docker, SQLite, STT/LLM/TTS, métricas, modo CLI legado
├── jarvis_agent.py          # Bucle agente Ollama /api/chat con tool-calling, planes multi-paso
├── jarvis_tool_registry.py  # ToolRegistry (ejecución supervisada), ToolDefinition, ToolRisk, excepciones
├── jarvis_tools.py          # crear_registro_herramientas(host, recorder, verificadores): las 12 herramientas
├── jarvis_actions.py        # Límites/cancelación (ejecutar_con_limites, CancellationToken), ResultadoAccion, registro y comandos de historial
├── jarvis_verifiers.py      # Comprobaciones posteriores (volumen real, ventana Brave, rutina guardada, audio Jarvis)
├── jarvis_system_info.py    # Solo lectura: ventanas abiertas (ctypes), procesos y estado del equipo (psutil)
├── jarvis_events.py         # HeadphonesWatcher: detecta conexión/desconexión de auriculares → ("device_event", disparador)
├── jarvis_headset.py        # Enlace del G533 vía HID++ (consulta de batería al receptor): encendido/apagado
├── jarvis_routines_panel.py # RoutinesPanel (Toplevel): ver, pausar/reanudar, cambiar permiso y eliminar rutinas
├── jarvis_routine_form.py   # Formulario (Tk) para crear/editar rutinas y comandos personalizados sin dictarlos
├── jarvis_commands.py       # Detectores regex de comandos, normalizar_url_web, scraping YouTube, aprobaciones por voz
├── jarvis_memory.py         # Recuerdos (CRUD, origen usuario/automatica) + búsqueda mixta: significado (embeddings) y palabras
├── jarvis_embeddings.py     # Vectores con Ollama (embeddinggemma, /api/embed), prefijos consulta/documento
├── jarvis_aprendizaje.py    # Aprendizaje automático: extrae hechos de conversaciones inactivas y los guarda/actualiza
├── jarvis_voz_stream.py     # HabladorEnVivo: frases → Piper → reproducción en cola mientras el modelo escribe
├── jarvis_media.py          # Multimedia: GSMTC/winrt (play/pausa/siguiente/anterior, «¿qué está sonando?»), teclas multimedia de respaldo, volumen por app (pycaw)
├── jarvis_reminders.py      # Temporizadores/alarmas/recordatorios: frases → set_reminder/list_reminders/cancel_reminder, tabla recordatorios, VigilanteRecordatorios (cada 1 s), sonido de alarma
├── jarvis_routines.py       # Rutinas programadas: parseo, validación, persistencia, reclamación
├── jarvis_system.py         # Brave (localizar, ventanas, pestañas) y lanzamiento de juegos
├── jarvis_audio_devices.py  # Volumen del sistema y cambio de dispositivo de salida (COM vía ctypes)
├── jarvis_integraciones.py  # Lee/crea integraciones.json (plugins activos y servidores MCP con herramientas permitidas)
├── jarvis_plugins.py        # GestorPlugins: carga plugins integrados (import estático para PyInstaller)
├── plugins/                 # Plugins integrados (clima.py); ver plugins/__init__.py para el contrato
├── jarvis_displays.py       # Desactivar/extender/duplicar monitores (API CCD de Windows) con reversión
├── jarvis_vision.py         # Monitores (numeración de Windows), captura en memoria y análisis con MODEL_VISION
├── jarvis_ollama_gpu.py     # Al iniciar: si Ollama arrancó sin GPU (registro server.log solo con library=cpu), lo reinicia (máx. 3 veces, 60 s entre intentos)
├── jarvis_power.py          # Apagar/reiniciar (shutdown.exe con 10 s de margen), suspender (SetSuspendState) y bloquear (LockWorkStation)
├── jarvis_mcp.py            # GestorMCP: sesiones MCP en un hilo asyncio, permisos y registro de herramientas
├── jarvis_logging.py        # Registro rotativo logs/jarvis.log + captura de excepciones (hilos, Tk)
├── jarvis_health.py         # Diagnóstico: Ollama/STT/TTS, latencias por etapa, fallos 24 h
├── jarvis_single_instance.py # Instancia única: mutex con nombre + evento para mostrar la ventana existente
├── jarvis_hotkeys.py        # Hilo GlobalHotkeyListener (F9 global)
├── jarvis_wakeword.py       # Hilo DetectorPalabraActivacion («Hey Jarvis», openWakeWord) + SegmentadorVoz (fin de la orden por energía); la orden termina tras 1,3 s de silencio (máx. 25 s) y el ruido de fondo es el percentil 20 de los últimos 5 s (`estimar_ruido`: con la mediana, el propio «Hey Jarvis» subía el umbral y las órdenes largas se cortaban); cada orden deja en el log su duración y umbral, sin contenido
├── audio_capture.py         # GrabadorAudio (micrófono → WAV temporal), grabar_audio CLI
├── init_db.sql              # Esquema SQLite + datos iniciales (idempotente)
├── docker-compose.yml       # stt, llm, tts, prometheus, grafana
├── prometheus.yml           # Scrape de host.docker.internal:8000
├── Jarvis.spec              # Configuración PyInstaller
├── requirements.txt         # Versiones fijadas
├── requirements-dev.txt     # + PyInstaller
├── .github/workflows/tests.yml  # CI: tests en windows-latest con Python 3.13 en cada push/PR a main
├── assets/                  # icon.ico (ventana/bandeja/exe), icon.webp
├── tests/                   # unittest (sin __init__.py)
├── responses/               # Audios respuesta_*.wav; solo se conservan los 30 más recientes (ya no se crean .txt)
├── logs/                    # jarvis.log (1 MB × 5) y mcp_<servidor>.log (stderr de cada servidor MCP)
├── integraciones.json       # Se crea junto a la app (instalación: Desktop\Jarvis). Editable con Jarvis cerrado
├── jarvis_memory.db         # BD SQLite de runtime (datos personales; no tocar en tests)
├── piper-voices/            # Modelo ONNX de voz (volumen montado en tts)
├── whisper-models/          # Caché HF de faster-whisper base/small (volumen de stt)
├── ollama-data/             # Volumen del contenedor llm (vacío actualmente)
├── build/, dist/            # Artefactos PyInstaller (dist/Jarvis/Jarvis.exe)
├── .venv313/                # Venv Python 3.13 con todas las dependencias + PyInstaller
└── .venv/                   # Venv Python 3.15 incompleto (sin PyAudio/Pillow/pystray)
```

## 5. Módulos y funcionalidades

- **`jarvis_core.py`** — Constantes de configuración (URLs, modelo, rutas). `APP_DIR` = carpeta del exe si está congelado, si no la del script. Funciones: `iniciar_servicios_docker()` (espera `docker info` hasta 120 s, despausa y levanta **solo `stt` y `tts`**), `detener_servicios_docker()`, `inicializar_base_datos()` (una vez, con lock; migraciones ad hoc), `conexion_db()` (context manager con commit/rollback y `foreign_keys=ON`), conversación (`crear_conversacion`, `obtener_conversacion_actual`, `obtener_historial`, `guardar_turno`), `procesar_stt`, `precalentar_stt()` (envía 1 s de silencio tras levantar Docker para cargar Whisper; lo llama `_docker_start_worker`), `procesar_llm` (vía `/api/generate`, usado solo por el CLI), `guardar_respuesta`, `generar_audio_tts` (protocolo Wyoming manual), métricas Prometheus. Ejecutable como CLI: `--text-mode`, `--tts-test TEXTO`.
- **`jarvis_agent.py`** — `procesar_con_herramientas(texto, conversacion_id, registry, confirm, approve_plan=…, max_tool_rounds=3, max_tool_calls=5, cancel_token=None, max_total_seconds=300)`. La consulta a Ollama corre vía `ejecutar_con_limites` (se puede cancelar mientras espera; timeout 120 s + margen) y traduce conexión rechazada / timeout a mensajes claros («abre la aplicación de Ollama»). Si el usuario cancela devuelve «Cancelé la operación. Acciones ya completadas: …» sin volver a llamar al LLM. Los fallos de ejecución se añaden al final de la respuesta («Atención: «X» falló: …») para no depender de que el LLM los mencione. Construye *system prompt* (preferencias + recuerdos relevantes + reglas), añade historial (12 mensajes) y llama a `/api/chat`. Acepta tool calls nativas **o** JSON en el contenido (`{"name","arguments"}`, también en bloque ```). Heurística `_requested_action_count` (verbos de acción en español) para exigir planes completos; un único reintento si el modelo no devuelve herramientas. Planes con >1 paso requieren `approve_plan`; un fallo/cancelación detiene los pasos restantes.
- **`jarvis_tool_registry.py`** — Registro con validación de esquema propia (tipos `string/integer/number/boolean`, `minLength/maxLength/minimum/maximum/enum`, `additionalProperties`). HIGH exige `requires_confirmation=True`. Cada herramienta tiene `label` (nombre en español para el usuario), `timeout_seconds` (por defecto 30) y `verifier` opcional. `execute(name, args, *, confirmed, origin, cancel_token)`, en orden: valida → si está cancelado lanza `ToolCancelledError` → si falta confirmación lanza `ToolConfirmationRequired` (no se registra) → ejecuta el handler en un hilo con tiempo máximo → verifica → registra. `record_result=False` hace que el recorder reciba `resultado=None` (se registra la ejecución, no su contenido). Errores: los del handler se **relanzan con su tipo original**; tiempo agotado → `ToolTimeoutError`, cancelación → `ToolCancelledError`, verificación negativa → `ToolVerificationError` (todas `ToolError`). Un verificador que falla internamente no hace fallar la acción (`Verificacion(ok=None)`). `ToolRegistry(recorder=None)`: sin recorder no se registra nada (así lo usan los tests).
- **`jarvis_actions.py`** — `CancellationToken`, `ejecutar_con_limites(func, timeout, descripcion, cancel_token)` (no puede matar el hilo: deja de esperar y el hilo termina en segundo plano; los mensajes lo advierten), `EstadoAccion` (`exito`, `error`, `tiempo_agotado`, `cancelada`, `no_verificada`), `Verificacion(ok: bool|None, detalle)`, `ResultadoAccion`, `registrar_accion()` (log + métricas `jarvis_tool_executions_total{herramienta,estado,origen}` y `jarvis_tool_duration_seconds{herramienta}` + fila SQLite), `listar_acciones()`, `procesar_comando()` para «qué acciones hiciste», «historial de acciones», «cuál fue la última acción».
- **`jarvis_system_info.py`** — Roadmap punto 2, **solo lectura** (no cierra procesos ni cambia nada). Recolección separada del formato para testear sin el sistema: `obtener_ventanas_abiertas()` (EnumWindows: visibles, sin propietario, sin `WS_EX_TOOLWINDOW`, no *cloaked*), `obtener_uso_procesos()` (psutil, agrupado por programa; CPU medida en 0,5 s y normalizada al total del equipo), `obtener_estado_equipo()`; `formatear_*` produce el texto en español; `consultar_*` son los handlers. `nombre_aplicacion()` traduce ejecutables a nombres legibles (`_NOMBRES_AMIGABLES`; `ApplicationFrameHost` usa el título; el propio proceso = «Jarvis»). Privacidad: por defecto solo nombres de programas; los títulos de ventana solo con `include_titles=true` (petición explícita), recortados a 80 caracteres. Las tres herramientas usan `record_result=False` y **no** están permitidas en rutinas.
- **`jarvis_integraciones.py`** — `cargar_integraciones()` lee `APP_DIR/integraciones.json` o lo crea con `configuracion_por_defecto()` (plugin `clima` y servidor MCP `archivos`: `npx -y @modelcontextprotocol/server-filesystem <Documents>`, solo `HERRAMIENTAS_ARCHIVOS_LECTURA`, `lecturas_sin_preguntar: true`). JSON inválido → ninguna integración y aviso en el chat.
- **`jarvis_plugins.py` + `plugins/`** — Contrato de plugin: `NOMBRE`, `DESCRIPCION`, `registrar(registry, contexto)`, opcionales `detectar_comando(texto)` (comando directo → herramienta) y `procesar_comando(texto, contexto)`. `ContextoPlugin` da `obtener_preferencia`/`guardar_preferencia` (tabla `preferencias`). Nuevo plugin = módulo en `plugins/` + entrada en `PLUGINS_DISPONIBLES` + activarlo en `integraciones.json`.
- **`plugins/clima.py`** — Herramienta LOW `get_weather(city?, day: today|tomorrow)` con Open-Meteo (geocoding + forecast, timeout 8 s). Frases directas («¿qué tiempo hace?», «clima en X», «¿va a llover mañana?»). Búsqueda de ciudad con pistas (`buscar_ciudad_con_alternativas`): prueba las primeras palabras como nombre y exige que el resto coincida con provincia/país (Open-Meteo solo busca por nombre). «mi ciudad es X» guarda en `preferencias.ciudad_clima` un JSON con nombre, provincia, país y coordenadas (no se vuelve a geocodificar); si hay homónimos avisa de las alternativas. Un valor antiguo en texto plano se geocodifica como antes. Solo envía a Open-Meteo el nombre de la ciudad.
- **`jarvis_displays.py`** — Control de monitores con la API CCD (`QueryDisplayConfig`/`SetDisplayConfig`, estructuras ctypes de 72/64 bytes verificadas con `assert`). `listar_pantallas(mapa)` devuelve `Pantalla` (nombre EDID, activo, número, fuente) y actualiza el **mapa número↔monitor físico** (clave = `monitorDevicePath`), guardado en `preferencias.mapa_monitores`, para poder reactivar un monitor apagado por su número (Windows solo numera los activos). `planificar(accion, numeros, pantallas)`: `disable` (nunca deja 0 activos), `extend` (monitor apagado o duplicado → fuente propia), `duplicate`. Desactivar/extender usan `SDC_TOPOLOGY_SUPPLIED` (Windows elige resoluciones); **duplicar usa modos explícitos** (`_config_duplicado`: comparten la fuente con la resolución del monitor más pequeño del par) porque con topología el driver AMD devolvía error 31 en cualquier duplicado con el VX2428. `aplicar()` valida primero con **los mismos flags que la aplicación real** (`flags_para(FLAGS_*, solo_validar=True)`), guarda la configuración, valida también su restauración y aplica (`FLAGS_TOPOLOGIA` nunca lleva `SDC_SAVE_TO_DATABASE`: Windows devuelve 87; `FLAGS_EXPLICITO` sí lo lleva); `restaurar_configuracion()` reaplica exactamente la anterior. Si para duplicar hay que encender un monitor, se hace en dos pasos. `set_primary`: `_config_principal` desplaza todas las imágenes (modos de fuente) para que el monitor elegido quede en (0, 0) — en Windows el principal es el del origen — conservando la disposición relativa; solo permitido para `MONITORES_PRINCIPALES_PERMITIDOS = (1, 3)` (decisión del usuario) y si está activo.
  - App: comandos directos `detectar_comando_monitores` («desactiva/apaga el monitor 3», «extiende/activa el monitor 3», «duplica los monitores 1 y 3», «pon el monitor 3 como principal», «cambia el monitor principal al 1») → herramienta `configure_display(action: disable|extend|duplicate|set_primary, monitor, second_monitor?)` (HIGH, con confirmación si la pide el LLM; directa sin confirmación previa como el resto de comandos directos). `_tool_configure_display` aplica y publica `display_keep` → `_show_display_keep_dialog` (siempre visible, centrado sobre la ventana de Jarvis con `_center_over_jarvis` en coordenadas de Tk —no usar píxeles físicos: la TV tiene escala 300 % y el diálogo quedaba fuera de pantalla—, con aviso sonoro, cuenta atrás de `DISPLAY_KEEP_SECONDS` = 15; ENTER mantiene, ESC/cerrar/sin respuesta → `restaurar_configuracion`). El mapa de monitores se actualiza al arrancar (`_startup_checks_worker`).
  - App: comandos directos de energía `detectar_comando_energia` (frases completas: «apaga/reinicia/suspende/bloquea el equipo|la pc|el ordenador…», «pon el equipo en suspensión», «bloquea la sesión|pantalla», «cancela el apagado|reinicio», «no apagues el equipo»; «apaga el monitor 3» sigue siendo de monitores) → `power_control(action: shutdown|restart|sleep)` (HIGH; **siempre** confirma, también como comando directo: `_direct_tool_worker` —y `_request_tool_confirmation` si la propone el LLM— llaman a `_request_power_confirmation` → evento `power_confirmation` → `_show_power_confirmation`: diálogo con `jarvis_power.PREGUNTAS_CONFIRMACION`, botones SÍ/NO, ENTER/ESC y **voz** (F9 o «Hey Jarvis»; tipo de aprobación `power`, `detectar_decision_energia(texto, accion)`: «sí», «confirmo», «adelante» o el verbo de esa acción —«apágalo»—; lo que empiece por «no»/«cancela» rechaza)), `cancel_power_action` (LOW, `shutdown /a`; código 1116 = no había nada programado) y `lock_computer` (LOW). Apagar/reiniciar usan `shutdown /s|/r /t 10 /c <aviso>` (`ESPERA_APAGADO`) (código 1190 = ya había uno programado); suspender espera 3 s en un `threading.Timer` porque `SetSuspendState` no vuelve hasta despertar. En rutinas, `power_control` no pide aprobación sino que avisa con cuenta atrás (ver «Rutinas de energía»).
- **`jarvis_vision.py`** — `listar_monitores()` (EnumDisplayMonitors con DPI por monitor; `numero` = N de `\\.\DISPLAYN`, igual que Configuración > Pantalla), `elegir_monitor(monitores, numero|None→principal)`, `capturar_monitor()` (PIL `ImageGrab`, `all_screens=True`, coordenadas físicas), `preparar_imagen()` (máx. 1920 px, JPEG en memoria), `analizar_imagen(jpeg, pregunta, cancel_token)` (`/api/chat` con `images`, `think: false`, `keep_alive` 5 min para liberar pronto la GPU, timeout 300 s, latencia `vision`). **No es una herramienta del LLM**: solo se captura por petición explícita.
  - Flujo en la app: `detectar_vision(texto)` («mira mi pantalla», «mira el monitor 2 y dime qué error sale») → `_start_vision_capture` → `_capture_hidden` (oculta las ventanas de Jarvis 400 ms) → `_show_vision_preview` (miniatura, monitor, botones MONITOR N para recapturar, campo de pregunta editable; se registra como aprobación de tipo `vision` reutilizando `_pending_plan_reply`/`_resolve_plan_approval`, así funcionan botones, ENTER/ESC, Cancelar y la voz con `detectar_decision_vision`: «analizar»/«cancelar») → `_vision_worker` (espera la decisión hasta 180 s; si se rechaza, descarta la imagen sin llamar al modelo; si se aprueba, analiza, guarda el turno, genera TTS y registra la acción `screen_vision` con solo el número de monitor).
- **`jarvis_mcp.py`** — `GestorMCP.iniciar(al_conectar)` lanza cada servidor por stdio (SDK `mcp`; resuelve `npx.cmd` y usa `CREATE_NO_WINDOW`; `errlog` a `logs/mcp_<servidor>.log` porque el .exe no tiene stderr) y mantiene la sesión en un hilo asyncio; `llamar()` usa `run_coroutine_threadsafe` con timeout 30 s. Solo registra herramientas de `herramientas_permitidas` como `mcp_<servidor>_<herramienta>`. Permisos (`riesgo_de`): `read_only_hint` + `lecturas_sin_preguntar` → LOW sin confirmación; `destructive_hint` → HIGH; resto → MEDIUM con confirmación. `record_result=False`; resultados recortados a 4000 caracteres. No están en `_ROUTINE_ALLOWED_TOOLS`.
- **`jarvis_logging.py`** — `configurar_registro(app_dir)` (llamado en `main()`) añade `logs/jarvis.log` rotativo e instala `sys.excepthook`/`threading.excepthook`; `capturar_errores_tk(root)` registra errores de callbacks de Tk. **Privacidad:** los mensajes de registro no incluyen texto del usuario ni respuestas (solo eventos, tiempos, longitudes y errores).
- **`jarvis_health.py`** — `consultar_diagnostico()` (herramienta LOW `get_jarvis_diagnostics`, comandos «diagnóstico», «estado de Jarvis», «¿funciona Ollama?»): comprueba Ollama (`/api/tags`, modelo descargado, `/api/ps` cargado), puertos STT 9000 y TTS 5555, latencias recientes (`core.resumen_latencias`), acciones fallidas 24 h, tamaño de BD y ruta del registro. Al arrancar, `_startup_checks_worker` avisa en el chat si Ollama no responde y limpia respuestas antiguas.
- **`jarvis_verifiers.py`** — `crear_verificadores(host)`: volumen (relee el volumen de Windows, tolerancia ±1), `set_jarvis_audio` (estado del host), herramientas de Brave (al menos una ventana visible de Brave en ≤5 s; comprobación básica), `schedule_routine` (existe en BD). Sin verificador a propósito: `set_output_device` (el handler ya verifica la salida predeterminada), `launch_game` (los lanzadores terminan enseguida → falsos negativos), `manage_window`, `power_control`/`cancel_power_action`/`lock_computer` (el handler comprueba el código de Windows y después no hay nada que comprobar).
- **`jarvis_tools.py`** — Herramientas registradas (el host es `JarvisApp`):

  | Herramienta | Riesgo | Confirmación |
  |---|---|---|
  | `set_system_volume(percentage 0-100)` | LOW | no |
  | `set_output_device(device: speakers\|headphones)` | LOW | no |
  | `set_jarvis_audio(enabled)` | LOW | no |
  | `open_new_browser_tab()` | LOW | no |
  | `navigate_browser(url)` | MEDIUM | sí |
  | `search_youtube(query, play_index?)` | MEDIUM | sí |
  | `schedule_routine(name, time, days?, steps_json)` | MEDIUM | sí |
  | `launch_game(name)` | HIGH | sí |
  | `manage_window(action)` | HIGH | sí |
  | `list_open_applications(include_titles?)` | LOW | no (solo lectura) |
  | `list_top_processes(sort_by: memory\|cpu, limit 1-15)` | LOW | no (solo lectura) |
  | `get_system_status()` | LOW | no (solo lectura) |

  Regla: en un plan aprobado, las MEDIUM se ejecutan con `confirmed=True`; las HIGH siempre piden confirmación individual (`messagebox.askyesno`).
  Tiempos máximos: volumen 10 s; salida de audio, pestaña y navegación 15 s; audio de Jarvis y ventanas 20 s; juegos y YouTube 30 s; rutina 10 s.
- **`jarvis_commands.py`** — `detectar_consulta_sistema(texto) -> (herramienta, args) | None` (normaliza sin tildes; exige forma de pregunta; descarta frases con cerrar/matar/abrir/iniciar para que nunca actúe). Regex de comandos directos, `normalizar_url_web` (solo http/https), `limpiar_transcripcion_voz`, detectores de decisión por voz («ejecutar plan» / «no ejecutar plan», «guardar rutina» / «no guardar rutina»), `obtener_primer_video_youtube` (scraping de `ytInitialData`).
- **`jarvis_memory.py`** — Comandos: «recuerda (que) …», «guarda en tu memoria …», «qué recuerdas (sobre X)», «olvida el recuerdo N», «actualiza el recuerdo N a …». Búsqueda por solapamiento de tokens normalizados (sin acentos, stopwords). Corrige transcripción «braiding» → «Brave» en contexto de preferencias.
- **`jarvis_routines.py`** — **Disparadores** (`rutinas.disparador`): `hora`, `auriculares_conectados`, `auriculares_desconectados` (la herramienta usa `time`/`headphones_connected`/`headphones_disconnected`; `_validate_trigger` acepta ambos). **Permisos** (`rutinas.modo_permiso`): `preguntar` o `automatica`; automática solo si todas las acciones son de riesgo LOW (`acciones_con_permiso`), comprobado al crear (`crear_rutina`), al cambiarlo (`cambiar_permiso_rutina`) y antes de cada ejecución (`puede_ejecutarse_sin_preguntar`). Rutinas de evento: «cuando conecte/desconecte los auriculares, <pasos> [automáticamente|sin preguntar]» (`_EVENT_ROUTINE`, nombre automático «Al conectar auriculares [N]», `hora=''`). Pasos reconocidos por regex: volumen, nueva pestaña, navegar y **cambiar la salida de audio** («cambia la salida a auriculares/parlantes»). Gestión: `listar_automatizaciones()` → `RoutineInfo`, `cambiar_estado_rutina`, `eliminar_rutina`, `rutinas_para_evento(disparador)`, `describir_disparador`. `RoutineCreationRequest.tool_arguments()` solo incluye `trigger`/`automatic` si no son los predeterminados. Creación por lenguaje natural (`parsear_solicitud_creacion`: «Programa una rutina llamada X a las HH:MM [días] [pasos]»; pasos soportados por regex: volumen, nueva pestaña, navegar) o vía herramienta `schedule_routine`. Comandos: listar, pausar/reanudar, eliminar. `reclamar_rutinas_vencidas()` marca `ultima_ejecucion` atómicamente para no repetir en el día; desactiva rutinas con JSON inválido. Máx. 5 pasos; herramientas permitidas en `_ROUTINE_ALLOWED_TOOLS`.
- **Comandos personalizados y formulario [2026-11-06]:** disparador `frase` (`DISPARADOR_FRASE`; columna `rutinas.frases` = lista JSON, migración condicional): la rutina se ejecuta al decir o escribir una de sus frases. `buscar_rutina_por_frase` compara con `normalizar_frase` (sin tildes ni signos y sin «hey/oye jarvis», «por favor» delante; coincidencia exacta) y solo rutinas activas; `_validate_phrases` exige ≥ 3 caracteres y `_comprobar_frases_libres` impide repetir una frase entre rutinas. Se comprueba **antes que cualquier otro comando** en `_send_text` y `_voice_worker` (evento `phrase_routine`): las frases del usuario tienen prioridad. `_phrase_routine_worker` ejecuta los pasos como comando directo (`confirmed=True`, `origin='frase'`, sin aprobar el plan), salvo `power_control`, que muestra la confirmación de energía; se detiene en el primer fallo, guarda el turno y responde por voz (`answer`). Hasta `MAX_PASOS` = 10 pasos por rutina. `actualizar_rutina` reemplaza una rutina (edición). Nueva herramienta `open_program(target)` (MEDIUM, `os.startfile` de una ruta existente: .exe, .lnk, documento o carpeta). Formulario `RoutineForm` (botones «+ NUEVA» y «✎ EDITAR» del panel): nombre; cuándo (frase/s separadas por comas, hora+días+«solo una vez», auriculares); acciones elegidas de `herramientas_disponibles` (las de `_ROUTINE_ALLOWED_TOOLS`), con campos generados del esquema (`enum` → lista con `ETIQUETAS_VALOR`, `integer` → número, `boolean` → casilla, texto; «Examinar…» en `open_program`), reordenables; «sin preguntar» solo si todo es riesgo bajo y no es frase. Contenido desplazable y botones fijos abajo (hacían falta ~835 px). `_ROUTINE_ALLOWED_TOOLS` incluye también `cancel_power_action`, `open_program`, `configure_display`, `set_reminder` y consultas sin datos sensibles (fecha/hora, batería, qué suena, estado del equipo, diagnóstico, clima); `list_open_applications`/`list_top_processes` siguen fuera (títulos de ventanas).
- **Rutinas de energía y acciones nuevas [2026-11-06]:** `_ROUTINE_ALLOWED_TOOLS` incluye además `media_control`, `set_app_volume`, `lock_computer` y `power_control`; `_ROUTINE_STEP` reconoce «pausa/reanuda la música», «siguiente canción», «pon el volumen de Spotify al 20», «bloquea el equipo» y «apaga/reinicia/suspende el equipo» (y «los sábados»/«los martes y jueves» en los días: `_FRASE_DIAS`). Atajo `_POWER_SHORTCUT` → `_parsear_atajo_energia`: «apaga el equipo a las 2» crea una rutina **de una sola vez** (`rutinas.una_vez = 1`, `days` = el día que toca; `reclamar_rutinas_vencidas` la desactiva al ejecutarla) y «… todos los días / de lunes a viernes / los sábados a las 23:30» una recurrente; la hora se interpreta con `jarvis_reminders.CUANDO_ABS`/`momento_de`; nombre automático «Apagado 02:00» (`_nombre_libre`); si sobra texto en la frase, no es atajo. `schedule_routine` acepta `once`. Ejecución: si `requiere_cuenta_atras(pasos)` (hay `power_control`), `_routine_worker` no pide aprobación del plan: `_request_power_countdown` → evento `power_countdown` → `_show_power_countdown` (ventana siempre encima con «Apagaré el equipo en 60 s», `CUENTA_ATRAS_RUTINA`; CANCELAR/ESC, HACERLO YA/ENTER, voz con el tipo de aprobación `power` —«cancela», «sí»— y aviso hablado con alarma vía `_announce`); si llega a 0 se ejecuta, y `power_control` se lanza con `confirmed=True` (la aprobación fue al guardar la rutina, cuyo diálogo dice que avisará con cuenta atrás; `shutdown /t 10` deja además 10 s para «cancela el apagado»). Si hay otra aprobación abierta, la cuenta atrás no se muestra y la rutina no apaga. Nunca puede ser `automatica` (riesgo alto). En el panel se ve «⏳ CUENTA ATRÁS».
- **`jarvis_app.py`** — Ciclo de vida (Docker al iniciar/cerrar, bandeja, cierre → minimizar a bandeja), grabación (botón MIC o F9), palabra de activación (`_start_wakeword`/`_stop_wakeword`; preferencia `palabra_activacion` ≠ "0" → activa por defecto; «activa/desactiva la palabra de activación», «deja de escucharme» → `detectar_comando_escucha` → `_execute_wakeword_toggle`; botón **HEY JARVIS ON/OFF** junto a «AUDIO ON» (`wakeword_button`, `_refresh_wakeword_indicator`, al pulsarlo `_toggle_wakeword_button`) que siempre muestra si la escucha está activa; `_can_listen_for_wakeword` —leído desde el hilo del detector— ignora el audio mientras graba, está ocupado o reproduce su voz, salvo si hay una aprobación pendiente; `wakeword_command` → `_on_wakeword_command` usa `_voice_worker` igual que F9, como orden o como respuesta a la aprobación pendiente), workers, diálogos de aprobación de planes (botones o voz), planificador de rutinas (cada 15 s, una rutina a la vez, cola `_pending_routines`), handlers `_tool_*`, nueva conversación.
  - Comandos directos (volumen, consultas de sistema, salida, juego, pestaña, navegación, YouTube) → `_start_direct_tool_action` → `_direct_tool_worker` → `tool_registry.execute(..., confirmed=True, origin="directo")` → eventos `direct_action_done` / `direct_action_error`. El audio on/off y las acciones de ventana directas siguen su ruta propia (operan sobre Tk).
  - Automatizaciones: `HeadphonesWatcher` (hilo, sondeo cada 2 s, 2 lecturas estables, sin disparo al arrancar; lee `auriculares_en_uso()`: salida de audio del G533 presente **y** auricular encendido según `jarvis_headset.auricular_encendido()`; si HID++ no responde o falta hidapi, solo cuenta la salida de audio) → evento `device_event` → `_handle_device_event` encola en `_pending_routines` las rutinas activas de ese evento → `_routine_worker` las ejecuta sin preguntar si `puede_ejecutarse_sin_preguntar`, si no muestra la aprobación del plan. Botón **⏱ RUTINAS** en la ventana de chat → `_show_routines_panel`.
  - Cancelación: `_start_busy` crea un `CancellationToken` nuevo (`self.cancel_token`) que los workers reciben al arrancar; el botón **✕ CANCELAR** (visible solo mientras está ocupado) o **Esc** llaman a `_cancel_current_operation` (cancela el token y rechaza un plan pendiente).
  - `_record_action` es el `recorder` del registro: publica `("action_result", resultado)` (barra de estado: «AJUSTAR VOLUMEN · 0.1 S · VERIFICADA» o «…: TIEMPO AGOTADO») y llama a `jarvis_actions.registrar_accion`.
  - Orígenes registrados: `agente`, `directo`, `rutina`, `comando` (creación de rutina por lenguaje natural).
- **`jarvis_system.py`** — Busca `brave.exe`, interactúa con su ventana (nueva pestaña / navegar / min-max); si falla, lanza Brave por `subprocess`. Juegos: busca carpetas en `GAME_DIRECTORIES` con alias (`GAME_NAME_ALIASES`) y ejecutable preferido (`GAME_EXECUTABLE_PREFERENCES`).
- **`jarvis_audio_devices.py`** — `cambiar_volumen_sistema`, `obtener_volumen_sistema` (ambas sobre `_operar_volumen_maestro`, COM `IAudioEndpointVolume` índices 7/9), `cambiar_dispositivo_salida` (fragmentos de nombre: «high definition audio device» = speakers, «g533 gaming headset» = headphones).
- **`jarvis_ui.py` / `jarvis_player.py` / `jarvis_theme.py`** — Presentación; sin lógica de negocio.

Dependencias: `jarvis_core` es base de todo; `jarvis_tools` → `jarvis_routines`, `jarvis_commands`; `jarvis_agent` → `jarvis_core`, `jarvis_memory`, `jarvis_tool_registry`; `jarvis_app` importa todos.

## 6. Base de datos

- **Motor:** SQLite, archivo `APP_DIR/jarvis_memory.db`. Acceso con `sqlite3` y SQL parametrizado; siempre mediante `core.conexion_db()`.
- **Esquema** (`init_db.sql`, ejecutado completo en cada arranque, idempotente con `IF NOT EXISTS` / `INSERT OR IGNORE`):
  - `recordatorios(id, tipo temporizador|alarma|recordatorio, texto, vence 'YYYY-MM-DD HH:MM:SS' hora local, estado pendiente|avisado|cancelado, creado)` + índice `(estado, vence)`. Se crea con `CREATE TABLE IF NOT EXISTS` en `init_db.sql` (no hace falta migración). El texto es dato personal: no se escribe en logs (solo ids).
  - `preferencias(clave PK, valor, actualizado)` — semillas: `idioma`, `tono`, `unidad_temperatura`. Se inyectan en el prompt.
  - `conversaciones(id TEXT uuid PK, titulo, creado, actualizado)` — la "actual" es la de `actualizado` más reciente. Título = primeros 60 caracteres del primer mensaje.
  - `mensajes(id, conversacion_id FK → conversaciones ON DELETE CASCADE, rol 'user'|'assistant', contenido, creado)` + índice `idx_mensajes_conversacion`.
  - `memorias(id, contenido UNIQUE COLLATE NOCASE, creado, origen 'usuario'|'automatica')` — 3–500 caracteres. `origen` se añade con migración condicional en `inicializar_base_datos`.
  - `memorias_vectores(memoria_id PK → memorias ON DELETE CASCADE, modelo, vector BLOB float32 normalizado)`.
  - `rutinas(id, nombre UNIQUE, hora 'HH:MM', accion JSON de pasos, activa 0|1, dias_semana '1,…,7' (1=lunes), ultima_ejecucion 'YYYY-MM-DD')` + índice `idx_rutinas_programacion` (creado en código).
  - `permisos(recurso PK, nivel, requiere_confirmacion)` — [Verificado] solo se siembra; **ningún código lo lee**.
  - `rutinas` ganó `disparador TEXT DEFAULT 'hora'`, `modo_permiso TEXT DEFAULT 'preguntar'`, `una_vez INTEGER DEFAULT 0` y `frases TEXT DEFAULT '[]'` (migración con `ALTER TABLE` en `inicializar_base_datos`). El planificador horario filtra `disparador = 'hora'`.
  - `acciones(id, herramienta, etiqueta, origen, estado, argumentos JSON, resultado, error, verificacion, duracion_ms, creado)` — historial de ejecuciones. Se conservan las últimas `MAX_ACCIONES_GUARDADAS` (500); textos recortados a 300 caracteres y argumentos a 1000 (privacidad). `verificacion` = `ok: …` / `fallo: …` / `sin_comprobar: …`.
- **Migraciones:** no hay sistema formal. Se hacen en `inicializar_base_datos()` (`ALTER TABLE` condicional para `dias_semana`/`ultima_ejecucion`, desactivación de la rutina legada `buenos_dias`). Para cambios de esquema: actualizar `init_db.sql` **y** añadir migración condicional ahí.
- Fechas en texto con `strftime('%Y-%m-%d %H:%M:%f','now')` (UTC de SQLite); `ultima_ejecucion` usa fecha local.

## 7. API e integraciones

No expone API propia salvo `/metrics` (Prometheus, puerto 8000). Consume:

| Servicio | Endpoint | Uso |
|---|---|---|
| speaches (STT) | `POST http://localhost:9000/v1/audio/transcriptions` (`model`, `language=es`, archivo WAV) | `core.procesar_stt` |
| Ollama (app nativa) | `POST http://localhost:11434/api/chat` (con `tools`, `num_ctx 4096`, `num_predict 256`, `temperature 0.2`, `keep_alive 30m`) | `jarvis_agent._request_chat` |
| Ollama | `POST …/api/generate` | solo CLI (`core.procesar_llm`) |
| Piper (Wyoming) | TCP `127.0.0.1:5555`, evento `synthesize` → `audio-start/chunk/stop` | `core.generar_audio_tts` |
| YouTube | `GET https://www.youtube.com/results?search_query=…` (scraping HTML) | `obtener_primer_video_youtube` |
| Docker CLI | `docker info`, `docker compose ps/unpause/up -d/stop stt tts` | arranque/cierre |

- **Autenticación:** ninguna (todo local).
- **Respuesta en vivo [2026-11-06]:** `procesar_con_herramientas(..., on_text=)` pide `/api/chat` con `stream: true` solo en las rondas que pueden ser la respuesta final (si se pidió una acción y aún faltan llamadas no se transmite, porque esa respuesta se descartaría para reintentar); `on_text(None)` marca cada ronda nueva y `_CompuertaTexto` calla el texto que empieza por `{`, `[` o ``` ` ``` (llamadas en JSON). En la app, `_new_live_speaker` crea un `jarvis_voz_stream.HabladorEnVivo` (si el audio está activo) y `_live_text_callback` lo alimenta y publica `answer_delta` (burbuja provisional `_show_live_answer`, que se quita al llegar `answer`). Las frases (≥12 caracteres; si no hay punto en 140, corta en una coma) se convierten con Piper en un hilo y se reproducen en otro; `terminar` dice lo que falte (p. ej. avisos de fallos), espera, une los fragmentos en el WAV de la respuesta y borra los fragmentos; ese WAV va a `_spoken_audio` para no repetirlo. Medido: primer texto a ~0,25 s frente a ~2,2 s de respuesta completa.
- **Memoria por significado [2026-11-06]:** `embeddinggemma` vía Ollama (14/15 aciertos con preguntas dichas con otras palabras frente a 4/15 de la búsqueda por palabras; ~17 ms; `qwen3-embedding:0.6b` empató pero confundió temas sin relación). `buscar_recuerdos` combina similitud coseno (umbral `UMBRAL_RELEVANCIA` = 0,35; relevantes ~0,5, sin relación < 0,27) + 0,05 × palabras en común; sin modelo, solo palabras. Los vectores se guardan al crear/actualizar y `sincronizar_vectores` completa los que falten (lo llama el primer ciclo de aprendizaje). En `python -m unittest` los embeddings se desactivan (`_en_tests`, mira `sys.argv`, no `sys.modules`: la app importa unittest indirectamente).
- **Aprendizaje automático [2026-11-06]:** cada minuto (`_check_learning`, si no está ocupado) `procesar_pendientes` revisa conversaciones con mensajes sin revisar y 10 min sin actividad (`aprendizaje_progreso` guarda el último id revisado por conversación). Paso 1, `extraer_hechos`: el modelo devuelve JSON (`format` con esquema) con hechos duraderos que dijo el usuario. Paso 2, por hecho: filtro `_SENSIBLE` (contraseñas, banco, documentos, salud); si hay un recuerdo con similitud ≥ 0,85 se ignora (ya lo sabía); si hay parecidos ≥ 0,55, `decidir_reemplazo` pregunta al modelo si lo corrige (solo puede tocar los ids mostrados) y lo actualiza; si no, guarda con `origen='automatica'`. Aviso en el chat «🧠 Aprendí (N): …» / «Actualicé el recuerdo N: …» (evento `learned`). Comandos: «¿qué aprendiste de mí?», «activa/desactiva el aprendizaje automático» (preferencia `aprendizaje_automatico`). Los logs solo anotan cuántos cambios hubo. Se pidió la extracción en dos pasos porque en uno el modelo omitía los hechos nuevos cuando veía recuerdos existentes.
- **Control multimedia:** `detectar_comando_multimedia` → `jarvis_media.detectar_comando` (frases completas: «pausa (la música)», «para la canción», «reanuda», «continúa», «quita la pausa», «siguiente canción», «pasa», «otra canción», «la anterior», «¿qué canción es esta?», «¿qué está sonando?», «pon el volumen de Spotify al 30», «baja el volumen del navegador a 20», «silencia Discord», «quita el silencio de Discord»; «reproduce X» sigue siendo búsqueda en YouTube, «silencia tu voz» el audio de Jarvis y «pon el volumen al 30» el del sistema). Herramientas LOW: `media_control(action play|pause|toggle|next|previous|stop)` sobre la sesión multimedia actual de Windows (GSMTC vía `winrt`, `asyncio.run` en el hilo de la herramienta; pausa/reanuda explícitos; sin sesión, pausar/reanudar responde «No hay nada reproduciéndose» y saltar usa las teclas multimedia `keybd_event`), verificador `verificar_reproduccion` (estado pausado/reproduciendo); `get_now_playing` (título/artista/app, `record_result=False`, respuesta hablada; el título no va a los logs); `set_app_volume(app, percentage?, mute?)` con pycaw (`comtypes.CoInitialize` por hilo; alias: navegador/YouTube → brave, música → spotify; si no hay audio de esa app lista las que sí suenan), verificador `verificar_volumen_app`. Van en la cadena de comandos directos después de recordatorios y batería.
- **Temporizadores, alarmas y recordatorios:** `jarvis_reminders.detectar_comando` (frases completas; normaliza con mapa de índices para recuperar el texto original con tildes): «pon un temporizador de 10 minutos [para X]», «avísame en 5 minutos», «recuérdame en 20 minutos X» / «recuérdame X a las 18:30» / «recuérdame mañana a las 9 X», «pon una alarma (para mañana) a las 7», «despiértame a las 7 de la mañana», «¿qué recordatorios tengo?», «¿cuánto falta?», «cancela el temporizador | el recordatorio 3 | todas las alarmas». Duraciones en cifras o palabras (hasta «noventa», «media hora», «hora y media», «X y medio»). Hora sin día ni franja → la próxima vez que llegue (a las 13:42, «a las 6» = 18:00; «a las 7» para una alarma = 19:00 hoy: la respuesta dice el momento exacto). Herramientas LOW: `set_reminder(kind timer|reminder|alarm, text?, delay_seconds | at 'AAAA-MM-DD HH:MM')` (verificador `verificar_aviso_guardado`), `list_reminders`, `cancel_reminder(kind?, id?, all?)`. Máx. 60 días; recordatorio exige texto. `VigilanteRecordatorios` reclama vencidos atómicamente (`estado='avisado'`) y publica `reminder_due` → `JarvisApp._announce(texto, alarm=True)`: chat, notificación de la bandeja (`tray_icon.notify`), sonido (`reproducir_alarma`, 3 pitidos dobles) y voz (`announce_audio`), sin tocar el estado ocupado. Los vencidos con Jarvis cerrado se avisan al abrirlo («Jarvis no estaba abierto»).
- **Batería del G533:** `jarvis_headset.porcentaje_bateria(mV)` (curva Li-ion aproximada 4186 mV = 100 % … 3500 mV = 0 %, múltiplos de 5; no es la cifra exacta de G HUB), herramienta LOW `get_headset_battery`, frase directa `detectar_consulta_bateria` («¿cuánta batería tienen los auriculares?»). `HeadsetBatteryWatcher` (cada 120 s) publica `headset_battery_low` una vez al bajar del 15 % sin cargar; se rearma al cargar o superar el 25 %. Las consultas HID++ se serializan con `_bloqueo_consulta`. Respuestas directas habladas: `_DIRECT_SPOKEN_TOOLS` (fecha/hora, batería y avisos).
- **Fecha y hora:** el modelo no tiene reloj. `jarvis_agent` añade `jarvis_system_info.contexto_fecha_hora()` («Fecha y hora actuales: viernes 6 de noviembre de 2026, 13:42.») al prompt de sistema en cada consulta, y existe la herramienta LOW `get_current_datetime(what: date|time|both)` (`consultar_fecha_hora`, nombres de días/meses propios, sin depender del locale). Comando directo `detectar_consulta_fecha_hora` («¿qué hora es?», «¿qué día es hoy?», «dime la fecha y la hora»; «¿qué hora es en Japón?» va al modelo). Es la única respuesta directa que también se dice en voz alta: `JarvisApp._DIRECT_SPOKEN_TOOLS` → `_direct_tool_worker` genera TTS y publica `answer`.
- **Ollama y la GPU [Verificado 2026-11-06]:** si Ollama se inicia con Windows antes de que el driver de AMD esté listo, solo detecta el procesador (respuestas de 5–11 s en vez de ~0,3–1,5 s con la RX 9070 XT: 43 tok/s qwen2.5-coder:14b, 87 tok/s gpt-oss:20b). `JarvisApp._ollama_gpu_worker` → `jarvis_ollama_gpu.asegurar_gpu()` lee el último arranque de `%LOCALAPPDATA%\Ollama\server.log` (`msg="Listening on` + líneas `msg="inference compute" library=…`) y, si solo hay `cpu`, mata `ollama app.exe`/`ollama.exe` y vuelve a abrir la app; avisa en el chat del resultado (evento `ollama_gpu_notice`).
- **Procesos automáticos:** planificador de rutinas (cada 15 s mientras la app está abierta; siempre pide aprobación antes de ejecutar), arranque/parada de contenedores STT/TTS, hotkey global F9, escucha local de «Hey Jarvis» (el audio de escucha solo vive en memoria; solo la orden posterior va a un WAV temporal que se borra tras transcribirla).

## 8. Configuración y variables de entorno

- **No se usan variables de entorno ni `.env`** [Verificado]. Toda la configuración son constantes en código:
  - `jarvis_core.py`: `STT_URL`, `STT_MODEL`, `LLM_URL`, `TTS_HOST`, `TTS_PORT`, `VOZ_PIPER`, `MODEL_LLM`, `MAX_CONTEXT_MESSAGES`, `DB_PATH`, `RESPONSES_DIR`; puerto de métricas 8000 en `iniciar_metricas`.
  - `jarvis_system.py`: `GAME_DIRECTORIES`, alias y ejecutables de juegos.
  - `jarvis_audio_devices.py`: nombres de dispositivos de salida.
  - `jarvis_hotkeys.py`: tecla global (`VK_F9`).
- `docker-compose.yml` define una contraseña de administrador de Grafana en claro (variable `GF_SECURITY_ADMIN_PASSWORD`); conviene moverla a un `.env` si el proyecto se comparte.
- `prometheus.yml` apunta a `host.docker.internal:8000` (en Linux sería `localhost:8000`).

## 9. Ejecución del proyecto

**Qué NO está en el repositorio (ver `.gitignore`) y cómo obtenerlo al clonar:**
- `.venv*/`: crear con `python -m venv .venv313` e instalar `requirements.txt`.
- `whisper-models/`: caché de Hugging Face que usa el contenedor `stt` (modelo `Systran/faster-whisper-small`).
- `piper-voices/`: voz `es_ES-davefx-medium` (`.onnx` + `.onnx.json`, de `rhasspy/piper-voices` en Hugging Face) que monta el contenedor `tts`.
- `.env`: copiar `.env.example` como `.env` (contraseña de Grafana).
- Se generan solos en tiempo de ejecución y contienen datos personales: `jarvis_memory.db`, `responses/`, `logs/`, `integraciones.json`.
- `build/` y `dist/`: se regeneran con PyInstaller.
- Modelos de Ollama: `ollama pull qwen2.5-coder:14b` (y el de visión, `qwen3.6:27b`).

**Requisitos:** Windows (funciones Win32), Python 3.13 (usar `.venv313`), Docker Desktop en PATH, Brave instalado (para herramientas de navegador), **la app de escritorio de Ollama abierta** (sirve `localhost:11434`) con `qwen2.5-coder:14b` descargado, micrófono.

```bash
.venv313/Scripts/python.exe -m pip install -r requirements.txt
```

```bash
.venv313/Scripts/python.exe jarvis_app.py
```
(La app ejecuta `docker compose up -d stt tts` por sí misma. **No** levanta `llm`, `prometheus` ni `grafana`.)

Ollama corre de forma nativa (confirmado por el usuario); el servicio `llm` de `docker-compose.yml` no se usa y `ollama-data/` está vacío. Para la observabilidad:
```bash
docker compose up -d prometheus grafana
```

Modo CLI legado (sin herramientas, memoria ni rutinas):
```bash
.venv313/Scripts/python.exe jarvis_core.py --text-mode
```

**Build:**
```bash
.venv313/Scripts/pyinstaller.exe Jarvis.spec
```
Genera `dist/Jarvis/Jarvis.exe`. Incluye `assets/icon.ico` e `init_db.sql`.

**Instalación que usa el usuario [Verificado 2026-11-05]:** los accesos directos (escritorio y menú Inicio) abren `%USERPROFILE%\Desktop\Jarvis\Jarvis.exe`, **no** `dist/`. Esa carpeta tiene su propia `jarvis_memory.db`, `responses/`, `docker-compose.yml` y enlaces simbólicos a `piper-voices`/`whisper-models` del proyecto. Tras cada cambio de código hay que: compilar con PyInstaller, cerrar Jarvis y copiar `dist/Jarvis/Jarvis.exe` y `dist/Jarvis/_internal` a `Desktop\Jarvis` (sin tocar su `jarvis_memory.db`). Si no, el usuario sigue probando la versión anterior. `Desktop\Jarvis actualizado\` es una copia antigua sin uso.
El `.exe` usa siempre el `init_db.sql` empaquetado (`_MEIPASS`), no el que haya suelto junto a él (`core.inicializar_base_datos`). Ojo: en modo congelado `APP_DIR` es la carpeta del exe, y allí deben existir `docker-compose.yml` (no se empaqueta) y se crearán `jarvis_memory.db` y `responses/`.

**Tests** (177, todos pasan a 2026-11-05):
```bash
.venv313/Scripts/python.exe -m unittest discover -s tests
```
`tests/` no tiene `__init__.py`, por eso `-s tests` sin `-t .`. Los tests mockean Ollama/TTS y usan BD temporales (`tempfile` + `patch` de `core.DB_PATH`).

**Debugging:** logs `logging` a INFO con latencias por etapa (`⏱️ STT/LLM/TTS`); métricas en `http://localhost:8000`, Prometheus `:9090`, Grafana `:3000`.

## 10. Convenciones y buenas prácticas

- **Idioma:** nombres de funciones de dominio en español (`procesar_stt`, `guardar_recuerdo`); métodos internos de la app/registro en inglés (`_send_text`, `validate_arguments`). Mensajes al usuario, errores y logs **en español**. Mantener esa mezcla según el módulo que se edite.
- Privados con `_`; regex precompiladas como constantes de módulo `_NOMBRE` o `NOMBRE_PATTERN`; dataclasses `frozen=True, slots=True` para valores (`PlannedAction`, `Routine`, `RoutineStep`).
- Tipado moderno en todas las firmas (`list[...]`, `X | None`, `Protocol` para hosts: `ToolEventHost`, `RoutineHost`).
- **Errores:** las capas de dominio lanzan `ValueError` (validación) / `ToolError` / `RuntimeError` con mensaje en español apto para mostrar; los workers capturan, registran con `logging.exception` y publican un evento `("error", msg)`. STT devuelve `""` en fallo; `procesar_llm` devuelve `None`; TTS relanza.
- **Validación:** todo argumento de herramienta pasa por `ToolRegistry.validate_arguments` (esquema + validador). URLs siempre por `normalizar_url_web`. Rutinas revalidan pasos al guardarlas **y** al ejecutarlas.
- **Hilos:** nunca tocar widgets Tk desde un hilo secundario; publicar en `self.events` y manejar en `_poll_events`. Toda espera de confirmación debe tener *timeout* y devolver `False` por defecto.
- **BD:** siempre `with core.conexion_db() as c:`; SQL parametrizado.
- `subprocess` siempre con lista de argumentos y `shell=False`; `CREATE_NO_WINDOW` para Docker.
- Añadir una herramienta nueva: método `_tool_x` en `JarvisApp` (y en el `Protocol` `ToolEventHost`), `registry.register(...)` en `jarvis_tools.py` con riesgo adecuado, **`label` en español, `timeout_seconds` realista y `verifier=verificadores.get("x")`**; si se puede comprobar el efecto, añadir la función a `jarvis_verifiers.crear_verificadores`. Añadir a `_ROUTINE_ALLOWED_TOOLS` si procede, y tests en `tests/test_tool_registry.py` / `test_agent_tool_calls.py` / `test_jarvis_actions.py`.
- Nunca ejecutar un handler directamente desde la app: usar `tool_registry.execute` para que quede registrado, limitado en tiempo y cancelable.
- Los workers reciben el `CancellationToken` como argumento al arrancar (no leer `self.cancel_token` más tarde: `_start_busy` lo reemplaza).

## 11. Estado actual del proyecto

**Terminado y cubierto por tests [Verificado]:** registro de herramientas, bucle agente con planes multi-paso y confirmaciones, memoria explícita, rutinas (creación, listado, pausa, eliminación, ejecución con aprobación), aprobación de planes por voz, enrutamiento de memoria en la app, **seguimiento de acciones (roadmap punto 1): tiempo máximo, cancelación, verificación, registro en BD/métricas, historial por comando, tiempo total del agente y mensajes de error de Ollama**. La GUI del punto 1 se probó con una prueba de humo (comando directo de volumen verificado contra Windows y cancelación desde el botón), no con tests automáticos. El usuario confirmó que el historial de acciones funciona en su instalación.
**Control de monitores [Verificado con validación de Windows, tests y prueba de GUI; NO aplicado en el equipo real]:** todos los escenarios (desactivar 1/2/3, duplicar 1+2, 1+3, 2+3, extender un monitor apagado o duplicado, restaurar) se comprobaron con `SDC_VALIDATE` contra el driver real sin cambiar la configuración. Monitores: 1 = KG271U N3 (DisplayPort, 2560×1440, principal), 2 = VX2428-FHD (HDMI, 1920×1080), 3 = Smart TV Pro (HDMI, 3840×2160). La aplicación real la prueba el usuario.
**Visión de pantalla (roadmap punto 6) [Verificado con tests y prueba de GUI con el modelo real]:** en la prueba se sustituyó solo la captura por una imagen sintética (no se analizó la pantalla real del usuario); el resto fue real: 3 monitores detectados (KG271U 2560×1440 principal, VX2428 1920×1080 y Smart TV 3840×2160 cuando está encendida), vista previa, cambio de monitor, cancelación sin llamar al modelo y análisis correcto en ~42 s en frío. Ninguna imagen se escribe en disco; el texto del análisis sí se guarda en el historial como cualquier respuesta.
**Plugins e integraciones (roadmap punto 5) [Verificado con tests y prueba de GUI con Ollama y MCP reales]:** plugin de clima y servidor MCP de archivos (lectura de Documentos). En la prueba, el agente usó `mcp_archivos_list_directory` por sí solo y respondió en 4,6 s; leer fuera de Documentos lo bloquea el propio servidor. `ToolRegistry` es seguro entre hilos (lock) y valida tipos `array`, `object`, `null`, uniones de tipos y esquemas sin `type` (anyOf). El agente usa `num_ctx` 8192 (las ~19 herramientas ocupan ~1.700 tokens). Verificado también en el `.exe` instalado (registro: «MCP archivos conectado: 5 herramientas permitidas»).
**Observabilidad y robustez (roadmap punto 4) [Verificado con tests y prueba de GUI]:** registro en archivo sin datos privados; métricas `jarvis_stage_seconds{etapa=stt|llm|tts|total, estado}` + resumen en memoria; diagnóstico por comando; aviso de Ollama al iniciar; transcripción de voz y texto a voz con tiempo máximo (90 s / 60 s) y cancelables (si el TTS falla o se cancela, la respuesta se muestra sin audio); `_poll_events` aislado por evento. **Rendimiento:** las URLs de Ollama/STT usan `127.0.0.1` (con `localhost` cada petición a Ollama tardaba ~2 s más por el intento IPv6). **Privacidad:** sin `.txt` de respuestas, solo 30 audios (`core.limpiar_respuestas`), comando «borra el historial de conversaciones» con confirmación (`core.borrar_historial_conversaciones`: borra mensajes, conversaciones y audios; conserva recuerdos, rutinas y acciones). El historial de conversaciones se conserva por decisión del usuario.
**Automatizaciones (roadmap punto 3) [Verificado con tests y prueba de GUI]:** rutinas por conexión/desconexión de auriculares, permiso automático según riesgo y panel. En la prueba de GUI el cambio de salida real se sustituyó por uno falso. **Hardware:** el endpoint de audio del G533 sigue activo con el auricular apagado (solo desaparece al quitar el receptor), por eso el detector también consulta el enlace por HID++ (`jarvis_headset.py`: informe largo `11 FF 07 01` en la interfaz usage page 0xFF43/usage 0x0202; respuesta con voltaje = encendido, error HID++ `11 FF FF 07 01 …` o voltaje 0 = apagado; protocolo tomado de HeadsetControl, sin copiar código GPL). Verificado en el equipo con el auricular encendido (4099 mV); **pendiente de verificar** la respuesta con el auricular apagado.
**Consultas del sistema (roadmap punto 2) [Verificado]:** «¿qué aplicaciones tengo abiertas?», «¿qué consume más memoria/CPU?», «¿cómo está el equipo?» por comando directo (texto y voz) o vía agente; probadas en la GUI con datos reales del equipo y con tests.

**Roadmap del usuario (orden previsto):** 1) verificación y seguimiento ✅ · 2) conciencia del sistema de solo lectura (apps, procesos, recursos) ✅ · 3) rutinas y automatizaciones por evento con permisos configurables ✅ (evento implementado: auriculares; el usuario no pidió apps/inicio/umbrales) · 4) observabilidad y robustez (Ollama sin bloquear la UI, minimizar datos privados) ✅ · 5) plugins/integraciones modulares, evaluar MCP con permisos y aprobación ✅ (clima + archivos; decisión: lecturas MCP sin preguntar) · 6) visión de pantalla bajo demanda con controles de captura ✅ (pantalla completa por monitor, vista previa obligatoria). **Roadmap completo.**

**Implementado sin tests específicos:** control de volumen/dispositivo, Brave, YouTube, juegos, hotkey F9, bandeja, reproductor, gestión de Docker, UI.

**Problemas conocidos / deuda técnica [Verificado salvo indicación]:**
- **Confirmaciones de comandos directos [Verificado]:** las acciones que pasan por el LLM piden confirmación por voz o con un clic según su riesgo, pero los comandos directos por regex («abre <juego>», «navega a…», «busca en YouTube…», volumen, ventanas) se ejecutan **sin** confirmación (`confirmed=True` en `_direct_tool_worker`; `_execute_window_action` no pregunta). **Decisión del usuario (2026-11-05): se deja así.** Un comando directo ya es una petición explícita, así que no pide confirmación ni siquiera para herramientas HIGH. No cambiarlo sin que el usuario lo pida.
- Un timeout o una cancelación **no detiene** el handler (Python no puede matar hilos): Jarvis deja de esperar y lo informa; la acción podría completarse más tarde.
- Lógica de enrutamiento duplicada entre `_send_text` y `_voice_worker` (`jarvis_app.py`).
- `jarvis_app.py` es grande (~1730 líneas) con un `_poll_events` de ~380 líneas basado en `if/elif` por tipo de evento.
- `responses/` acumula `.txt`/`.wav` sin limpieza.
- Tabla `permisos` sin uso; `core.procesar_llm`/`ciclo_interactivo` solo los usa el CLI legado (no tiene herramientas ni memoria).
- Configuración *hardcoded* (modelo, puertos, rutas de juegos, nombres de dispositivos); sin `.env`.
- `.venv` (3.15) incompleto.
- **Modelo del agente [Medido 2026-11-06, con GPU]:** 23 frases de prueba con las herramientas reales: `qwen2.5-coder:14b` 20/23 aciertos (mediana 0,5 s); `gpt-oss:20b` 11/23 con `num_predict` 256 (gasta los tokens razonando y responde vacío) y 19/23 con `think: "low"` (mediana 0,5 s, pero falla planes de varios pasos, anida mal los argumentos y pide confirmación en vez de llamar a la herramienta). Se mantiene `qwen2.5-coder:14b`. Fallos conocidos de qwen: a veces devuelve la rutina como JSON en el texto y ante «cuéntame un chiste» llamó a `set_jarvis_audio`.
- Imágenes Docker con tag `latest` (no reproducible).
- Scraping de YouTube frágil ante cambios de HTML.
- Sin control de versiones (no es repo git) y sin README.

**Próximas tareas:** no hay TODOs ni roadmap documentados en el código.

## 12. Consideraciones importantes para futuras IAs

- **Antes de modificar…**
  - confirmaciones / riesgo de acciones → `jarvis_tool_registry.py`, `jarvis_tools.py`, `jarvis_agent.py` (líneas de ejecución del plan), `JarvisApp._routine_worker`, `JarvisApp._direct_tool_worker`.
  - tiempos, cancelación, verificación o historial → `jarvis_actions.py`, `jarvis_verifiers.py`, `ToolRegistry.execute`.
  - automatizaciones → `jarvis_routines.py`, `jarvis_events.py`, `jarvis_routines_panel.py`, `JarvisApp._handle_device_event` / `_routine_worker`.
  - monitores → `jarvis_displays.py`, `detectar_comando_monitores`, `JarvisApp._tool_configure_display` / `_show_display_keep_dialog`.
  - energía (apagar, reiniciar, suspender, bloquear) → `jarvis_power.py`, `detectar_comando_energia`, `_registrar_energia` en `jarvis_tools.py`, confirmación en `JarvisApp._direct_tool_worker`.
  - visión → `jarvis_vision.py`, `detectar_vision`/`detectar_decision_vision` en `jarvis_commands.py`, `JarvisApp._start_vision_capture` / `_show_vision_preview` / `_vision_worker`, `_APPROVAL_PHRASES`.
  - plugins/MCP → `jarvis_integraciones.py`, `jarvis_plugins.py`, `plugins/`, `jarvis_mcp.py`, `JarvisApp._on_mcp_connected` / `_integration_checks`.
  - consultas del sistema → `jarvis_system_info.py`, `detectar_consulta_sistema` en `jarvis_commands.py`, `_registrar_consultas_sistema` en `jarvis_tools.py`.
  - comandos de voz/texto → `jarvis_commands.py` **y** ambos enrutadores en `jarvis_app.py` (`_send_text`, `_voice_worker`), en el mismo orden de prioridad.
  - esquema de BD → `init_db.sql` + `core.inicializar_base_datos()` (migración condicional) + `Jarvis.spec` si cambian archivos de datos.
  - prompt del agente → `procesar_con_herramientas` (el texto del *system prompt* contiene reglas de negocio).
- **Comportamientos que no deben alterarse:**
  - Las herramientas HIGH **siempre** requieren confirmación (el registro lo impone).
  - Un plan con >1 paso se muestra y aprueba completo antes del primer paso; si un paso falla o se rechaza, el resto se cancela; si el plan propuesto es incompleto o inválido, no se ejecuta nada.
  - Las rutinas piden aprobación en cada ejecución y solo corren con la app abierta; una por día y hora (`ultima_ejecucion`).
  - Los recuerdos son contexto, **no instrucciones** (se indica explícitamente al LLM).
  - Orden de enrutamiento: regex directos → memoria → rutinas → creación de rutina → agente.
  - Toda interacción con Tk desde el hilo principal.
  - Solo una instancia: `main()` usa `SingleInstance` (mutex `Local\JarvisAsistenteLocal_Instancia`). Una segunda ejecución activa el evento `Local\JarvisAsistenteLocal_Mostrar` (la primera publica `tray_show`) y termina. Afecta también a `python jarvis_app.py`: no se puede ejecutar a la vez que el `.exe`.
  - `_poll_events` maneja cada evento dentro de su propio `try`; si uno falla, `_recover_from_event_error` responde «no» a su confirmación, cierra el diálogo pendiente y avisa, y el `finally` reprograma siempre el bucle. (Antes un error en un manejador congelaba la app: pasó con el diálogo de guardar rutinas de evento, que leía `arguments['time']`.) Al cambiar argumentos de una herramienta, revisar también los diálogos que los muestran.
  - Toda acción pasa por `ToolRegistry.execute`; los errores del handler conservan su tipo (el agente distingue `ToolError` de otras excepciones).
  - Un fallo al registrar (BD/métricas) o al verificar nunca debe hacer fallar la acción.
  - No registrar en `logging` texto del usuario, transcripciones ni respuestas (el archivo de registro persiste en disco). No volver a escribir `.txt` de respuestas.
  - Mantener `127.0.0.1` en `STT_URL`/`LLM_URL` (no `localhost`).
  - Monitores: todo cambio debe validarse antes con `flags_para(..., solo_validar=True)` (mismos flags que al aplicar; validar con otros flags dio un falso «válido» y luego error 87), guardar la configuración previa y revertirse si el usuario no lo confirma en el diálogo; nunca dejar 0 monitores activos. Al probar, no aplicar cambios reales en el equipo del usuario sin que él lo haga.
  - Visión: nunca capturar sin petición explícita ni enviar al modelo sin la confirmación de la vista previa; no guardar capturas en disco; no exponer la captura como herramienta del LLM (decisión del usuario).
  - MCP: nunca registrar herramientas fuera de `herramientas_permitidas`; las de escritura del servidor de archivos no deben añadirse a la configuración por defecto. Solo las de solo lectura pueden ir sin confirmación (decisión del usuario).
  - Una rutina solo se ejecuta sin preguntar si es `automatica` **y** todas sus acciones son LOW en el momento de ejecutarse; nunca relajar esta regla (decisión del usuario: «según el riesgo»).
  - Los detectores de eventos solo publican eventos; nunca ejecutan acciones directamente.
  - Las consultas del sistema son de solo lectura: no añadir cierre de procesos ni cambios de configuración sin que el usuario lo pida (decisión del roadmap). No mostrar títulos de ventana salvo petición explícita.
- **Dependencias críticas:** contrato de Ollama `/api/chat` con `tools` (y el *fallback* de JSON en el contenido para modelos sin tool-calling nativo); protocolo Wyoming de Piper implementado a mano; API compatible OpenAI de speaches.
- **Efectos secundarios:** abrir la app arranca contenedores Docker y al salir (menú de bandeja) los detiene; cerrar la ventana solo oculta en la bandeja. Las herramientas actúan sobre el sistema real (volumen, procesos). Los tests de app instancian `JarvisApp` con mocks: **no** ejecutar código que use `jarvis_memory.db` real en tests.
- `jarvis_memory.db` contiene conversaciones y datos personales del usuario: no leer/volcar su contenido salvo necesidad.

## 13. Glosario

- **Herramienta (tool):** acción registrada en `ToolRegistry` invocable por el LLM.
- **Riesgo LOW/MEDIUM/HIGH:** nivel que decide confirmaciones (`ToolRisk`).
- **Plan:** conjunto de llamadas a herramientas devueltas en un lote por el LLM (`PlannedAction`).
- **Host:** objeto que implementa los handlers `_tool_*` (en producción `JarvisApp`).
- **Recuerdo / memoria:** dato explícito guardado por el usuario (tabla `memorias`); distinto del historial de conversación.
- **Rutina:** secuencia de 1–5 pasos de herramientas programada a una hora y días (1 = lunes … 7 = domingo).
- **Reclamar rutina:** marcarla como ejecutada hoy antes de lanzarla para evitar duplicados.
- **Comando directo:** frase reconocida por regex que se ejecuta sin LLM (pero sí por `ToolRegistry.execute`).
- **Origen:** quién disparó una acción registrada: `agente`, `directo`, `rutina` o `comando`.
- **Verificación:** comprobación posterior del efecto real de una acción (`Verificacion`); distinta de la confirmación previa del usuario.
- **Disparador:** qué activa una rutina: una hora o un evento (auriculares conectados/desconectados).
- **Automatización / rutina automática:** rutina con `modo_permiso = 'automatica'`; se ejecuta sin aprobación si todas sus acciones son de riesgo bajo.
- **Plugin:** módulo de `plugins/` que añade herramientas y comandos propios.
- **MCP (Model Context Protocol):** estándar para conectar asistentes con servidores externos de herramientas; Jarvis actúa como cliente.
- **Recorder:** función que recibe cada `ResultadoAccion` (en la app, `JarvisApp._record_action`).
- **STT / LLM / TTS:** voz→texto (speaches/Whisper), modelo de lenguaje (Ollama), texto→voz (Piper).
- **Wyoming:** protocolo de eventos JSON + payload binario usado por Piper.
- **Orbe:** visualizador animado del estado de Jarvis en la UI (`idle`, grabando, procesando…).
- **G533:** auriculares Logitech del usuario (dispositivo `headphones`).
