---
name: memoria-e-indexacion
description: >-
  Usar este skill cuando el usuario invoca /indexar-canon o necesita guardar memoria persistente,
  indexar documentos de referencia, buscar en capítulos previos o consultar el grafo de continuidad.
---

# Memoria Persistente, Indexación FTS5 y Grafo de Canon (`memoria-e-indexacion`)

Permite que el estudio recuerde cada detalle narrativo, visual y técnico a lo largo de decenas de episodios mediante SQLite FTS5 (`unicode61` sin tildes + ranking BM25), instantáneas JSON versionables en Git (`canon_snapshot.json`) y una base global de oficio cinematográfico (`craft_kb.db`).

## Protocolo Operativo

### 1. Indexar Documentos, Guiones e Investigación (`00_references/`)
- Cualquier documento `.md`, `.txt`, `.yaml`, `.json` o `.srt` colocado en `productions/{production_slug}/00_references/` (o en biblias y episodios) se indexa ejecutando:
  `flowforge_index_production(production_slug="{slug}", force_rebuild=false)`
- El indexador divide automáticamente por encabezados Markdown, escenas, planos individuales de `shot_manifest.json`, fichas de `cast_registry.json` y bloques de subtítulos `.srt`, recalculando solo archivos cuyo SHA-256 haya cambiado.

### 2. Recuperar Contexto Antes de Escribir o Rodar un Capítulo
- **Resumen activo del canon:** Ejecutar `flowforge_memory_context(production_slug="{slug}", up_to_episode=N)`.
- **Ficha 360° de personaje/objeto/locación:** Ejecutar `flowforge_entity_dossier(production_slug="{slug}", entity_id="{id}", up_to_episode=N)`.
- **Búsqueda híbrida exacta o semántica por palabras clave:** Ejecutar `flowforge_search(production_slug="{slug}", query="...", scope="all", episode_number=N)`. Ignora tildes automáticamente (*"relojería"* encuentra *"relojeria"*).

### 3. Persistir Hechos, Cambios de Estado e Hilos Abiertos al Cerrar Cada Fase
- **Hechos y mutaciones (`flowforge_memory_save`):**
  - `memory_type`: `"canon_fact"` | `"state_change"` | `"plot_thread"` | `"visual_rule"` | `"director_preference"` | `"continuity_warning"`.
  - Usar `topic_key` jerárquico (ej. `state/elena/abrigo`, `thread/llave_maestra`) para actualizar estados conservando historial completo de revisiones, y pasar `"status": "resolved"` cuando un hilo dramático se cierre.
- **Relaciones del grafo (`flowforge_graph_link`):**
  - Conectar entidades (`subject_id`, `relation`, `object_id`, `since_episode`, `until_episode`).

### 4. Aprendizaje Técnico Transversal (`flowforge_craft_kb`)
- Cuando se descubra un patrón técnico valioso sobre `Veo 3.1`, `Omni Flash`, `Nano Banana` o mezcla de audio, guardarlo con `flowforge_craft_kb(action="save", ...)` redactado de forma genérica (cero spoilers o nombres propios de la serie).
