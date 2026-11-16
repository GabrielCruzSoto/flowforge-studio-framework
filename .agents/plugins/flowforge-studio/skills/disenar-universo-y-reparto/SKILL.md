---
name: disenar-universo-y-reparto
description: >-
  Usar este skill cuando el usuario invoca /disenar-universo-y-reparto o pide crear hojas de
  personajes, objetos, escenarios (Ingredients) y voces consistentes para Google Flow.
---

# Diseño de Reparto, Activos e Identidad Sonora (`/disenar-universo-y-reparto`)

Bloquea la identidad visual y vocal de todos los personajes, objetos recurrentes y escenarios en `02_cast_and_ingredients/` y `03_sound_and_music/` gastando **0 créditos**.

## Procedimiento Paso a Paso

1. **Auditar Compuerta**:
   - Ejecutar `flowforge_check_gates(production_slug, target_gate="cast_locked")` y confirmar `unlocked: true` (`universe_bible == "approved"`).
2. **Registrar Descriptores Literales Invariables**:
   - Por cada personaje, criatura, objeto o escenario, invocar `flowforge_register_cast_asset` con `visual_spec` completo en inglés según su tipo (personaje: `apparent_age`, `build`, `face`, `skin`, `hair`, `eyes`, `wardrobe`, `palette`; criatura: `scale`, `silhouette`, `anatomy`, `surface`, `palette`, `eyes`, `locomotion`; objeto: `scale`, `shape`, `materials`, `palette`, `surface_details`, `orientation_rule`; escenario: `setting`, `layout`, `landmarks`, `materials`, `palette`, `lighting`, `atmosphere`) y `continuity_locks` (rasgos que nunca cambian). El motor rechaza rasgos faltantes, vagos (menos de 2 palabras) o desconocidos, y compila automáticamente el núcleo `verbatim_prompt_descriptor` (máx. 90 palabras).
   - Presentar a dirección las fichas generadas en `02_cast_and_ingredients/fichas/` como parte de la revisión de `cast_locked`.
3. **Generar Retratos Maestros a 0 Créditos en Google Flow**:
   - Verificar sesión con `flow_status`. Si no hay proyecto activo, crear uno con `flow_new_project`.
   - Para personajes de video, usar `flow_character` con `describe` (`portrait_source="flow_character_describe"`, **0 créditos**) o generar lámina neutra con *Nano Banana 2.1* (`flow_generate` modo imagen, **0 créditos**). Nunca usar fotos de personas reales para evitar bloqueos de seguridad en video.
   - Descargar los retratos a `02_cast_and_ingredients/portraits/` y generar lámina resumen con `flowforge_render_contact_sheet`.
4. **Definir Mapa Vocal y Sonoro (`03_sound_and_music/`)**:
   - Consultar `flow_voices`, asignar voces en `03_sound_and_music/voice_cast.json` y documentar la identidad musical en `03_sound_and_music/sound_bible.md`.
5. **Compuerta de Revisión**:
   - Ejecutar `flowforge_set_gate(state="in_review")` para `cast_locked` y `sound_identity` y **DETENERSE** hasta recibir aprobación humana.
