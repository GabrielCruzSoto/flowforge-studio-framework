---
name: iniciar-produccion
description: >-
  Usar este skill cuando el usuario invoca /iniciar-produccion o pide crear desde cero una serie,
  cortometraje o videoclip a partir de una idea vaga, semilla narrativa o concepto inicial.
---

# Iniciar Producción desde Cero (`/iniciar-produccion`)

Transforma una idea embrionaria de una sola frase en un cartucho aislado de producción con biblia de universo, ADN visual (`visual_dna`) y arco de capítulos listo para revisión.

## Procedimiento Paso a Paso

1. **Definir Identificador y Formato**:
   - Derivar un `production_slug` limpio en minúsculas (ej. `relojero-neon`) y confirmar formato (`series`, `short-film` o `music-video`) y relación de aspecto (`16:9` o `9:16`).
2. **Inicializar Cartucho Determinista**:
   - Ejecutar `flowforge_init_production` para crear `productions/{production_slug}/`, `production.yaml`, `credit_ledger.json`, `cast_registry.json` y `voice_cast.json`.
3. **Expandir Semilla con Criterio Cinematográfico (`universe-architect`)**:
   - Si la idea inicial es vaga, proponer 3 enfoques dramáticos distintos con conflicto inmediato y gancho en los primeros 2 segundos, evitando físicas propensas a fallos en modelos de video (escombros diminutos, multitudes densas, texto pequeño).
   - Una vez elegido el rumbo (o si la premisa ya es clara), redactar:
     - `01_universe_bible/universe_bible.md`: logline, reglas del universo, tono y estética.
     - `01_universe_bible/season_01_arc.md`: desglose de capítulos con gancho de apertura, escalada y *cliffhanger* de cierre por episodio.
   - Completar `visual_dna` en `production.yaml` en inglés técnico conciso (`aesthetic_style`, `lens_package`, `color_palette`, `lighting_signature`).
4. **Compuerta de Revisión (`HITL`)**:
   - Actualizar `production_gates.universe_bible: "in_review"` en `production.yaml`, presentar el resumen ejecutivo a dirección y **DETENERSE** hasta recibir aprobación explícita antes de pasar al reparto.
