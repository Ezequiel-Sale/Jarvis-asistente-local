CREATE TABLE IF NOT EXISTS preferencias (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL,
    actualizado TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rutinas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    hora TEXT NOT NULL,
    accion TEXT NOT NULL,
    activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1)),
    dias_semana TEXT NOT NULL DEFAULT '1,2,3,4,5,6,7',
    ultima_ejecucion TEXT,
    -- 'hora' (usa hora/dias_semana), 'frase' o un evento: 'auriculares_conectados',
    -- 'auriculares_desconectados', 'juego_abierto', 'juego_cerrado'
    disparador TEXT NOT NULL DEFAULT 'hora',
    -- 'preguntar' o 'automatica' (solo si todas las acciones son de riesgo bajo)
    modo_permiso TEXT NOT NULL DEFAULT 'preguntar',
    -- 1 = se desactiva sola después de ejecutarse (p. ej. «apaga el equipo a las 2»)
    una_vez INTEGER NOT NULL DEFAULT 0,
    -- Disparador 'frase': lista JSON de frases que la activan («modo juego»…)
    frases TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS permisos (
    recurso TEXT PRIMARY KEY,
    nivel TEXT CHECK(nivel IN ('lectura','escritura','ejecucion')),
    requiere_confirmacion INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS conversaciones (
    id TEXT PRIMARY KEY,
    titulo TEXT NOT NULL DEFAULT 'Nueva conversación',
    creado TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
    actualizado TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
);

CREATE TABLE IF NOT EXISTS mensajes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversacion_id TEXT NOT NULL REFERENCES conversaciones(id) ON DELETE CASCADE,
    rol TEXT NOT NULL CHECK(rol IN ('user', 'assistant')),
    contenido TEXT NOT NULL,
    creado TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
);

CREATE TABLE IF NOT EXISTS memorias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contenido TEXT NOT NULL COLLATE NOCASE UNIQUE,
    creado TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
    -- 'usuario' (lo pidió con «recuerda que…») o 'automatica' (aprendizaje automático)
    origen TEXT NOT NULL DEFAULT 'usuario'
);

-- Vector de significado de cada recuerdo (jarvis_embeddings.py), float32 normalizado.
CREATE TABLE IF NOT EXISTS memorias_vectores (
    memoria_id INTEGER PRIMARY KEY REFERENCES memorias(id) ON DELETE CASCADE,
    modelo TEXT NOT NULL,
    vector BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mensajes_conversacion
    ON mensajes(conversacion_id, id);

-- Registro de acciones ejecutadas (se conservan solo las últimas 500)
CREATE TABLE IF NOT EXISTS acciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    herramienta TEXT NOT NULL,
    etiqueta TEXT NOT NULL,
    origen TEXT NOT NULL,
    estado TEXT NOT NULL CHECK(estado IN (
        'exito', 'error', 'tiempo_agotado', 'cancelada', 'no_verificada'
    )),
    argumentos TEXT NOT NULL DEFAULT '{}',
    resultado TEXT,
    error TEXT,
    verificacion TEXT,
    duracion_ms INTEGER NOT NULL,
    creado TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
);

-- Temporizadores, alarmas y recordatorios (jarvis_reminders.py). Horas locales.
CREATE TABLE IF NOT EXISTS recordatorios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL CHECK (tipo IN ('temporizador', 'alarma', 'recordatorio')),
    texto TEXT NOT NULL DEFAULT '',
    vence TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente'
        CHECK (estado IN ('pendiente', 'avisado', 'cancelado')),
    creado TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recordatorios_pendientes
    ON recordatorios(estado, vence);

-- Inserciones iniciales seguras
INSERT OR IGNORE INTO preferencias (clave, valor) VALUES ('idioma', 'español');
INSERT OR IGNORE INTO preferencias (clave, valor) VALUES ('tono', 'directo y útil');
INSERT OR IGNORE INTO preferencias (clave, valor) VALUES ('unidad_temperatura', '°C');

INSERT OR IGNORE INTO permisos (recurso, nivel, requiere_confirmacion) VALUES ('archivos_personales', 'lectura', 1);
