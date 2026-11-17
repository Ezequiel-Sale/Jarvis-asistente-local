# Examen del modelo de Jarvis

Mide qué tan bien elige herramientas el modelo de lenguaje, para comparar modelos (o un modelo
afinado) con números en vez de impresiones.

- `casos.json`: las órdenes y lo que debería pedir el modelo. Cada caso tiene `grupo` (varias frases
  que piden lo mismo con otras palabras, para medir si el modelo entiende aunque cambies las
  palabras), `categoria`, `texto`, `esperado` (lista de pasos en orden, o `null` si lo correcto es
  contestar sin herramientas) y `alternativas` opcionales. En `args` solo se revisan las claves
  escritas; el texto se compara sin mayúsculas ni tildes y `"a|b"` acepta cualquiera.
- `evaluar.py`: la corrección (probada en `tests/test_examen.py`).
- `correr_examen.py`: manda cada orden al modelo con las mismas instrucciones y herramientas que
  usa el agente y corrige **solo la primera respuesta**. No ejecuta ninguna herramienta ni lee
  `jarvis_memory.db`.

```
.venv313/Scripts/python.exe -m examen.correr_examen
.venv313/Scripts/python.exe -m examen.correr_examen --modelo qwen3.6:27b --repeticiones 2
.venv313/Scripts/python.exe -m examen.correr_examen --solo plan-
```

El informe muestra aciertos por categoría, tipos de error, qué grupos fallan en alguna variante y el
porcentaje de lo que **de verdad llega a la IA** (sin las órdenes que Jarvis resuelve solo con sus
reglas). El detalle queda en `examen/resultados/` (fuera de git).

Límites: corrige la primera respuesta, sin el reintento de plan ni los controles que Jarvis aplica
después (así se compara el modelo solo); no incluye las herramientas MCP de archivos ni historial
de conversación.
