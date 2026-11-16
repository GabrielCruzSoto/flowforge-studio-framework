---
name: escribir-capitulo
description: >-
  Usar este skill cuando el usuario invoca /escribir-capitulo o solicita redactar la escaleta,
  guion literario y manifiesto de planos (shot_manifest.json) de un episodio o cortometraje.
---

# Escritura de Capítulo y Desglose Óptico (`/escribir-capitulo`)

Desarrolla un capítulo completo desde la escaleta hasta un `shot_manifest.json` validado sin errores para Google Flow.

## Procedimiento Paso a Paso

1. **Inicializar Capítulo y Continuidad**:
   - Ejecutar `flowforge_init_episode` (enlaza automáticamente `ep_final_continuity_frame.png` del capítulo anterior si es Episodio $\ge 2$).
   - Verificar compuertas previas con `flowforge_check_gates`.
2. **Escaleta (`outline`) y Guion Literario**:
   - Redactar la escaleta de 3 bloques con gancho en los primeros 2 segundos. Tras aprobación de `outline`, escribir `episodes/{episode_id}/01_script/literary_script.md` en español neutro/profesional.
3. **Construir `01_script/shot_manifest.json`**:
   - Seguir la especificación de `templates/shot_manifest.schema.json` y el skill `flow-prompt-engineering`.
   - Reglas obligatorias por plano:
     - `batches`: `"x1"` (evita cobro doble accidental).
     - `model`: `"Omni 1.1 Flash"` (`4`/`6`/`8`/`10`s) para planos ágiles/transiciones o `"Veo 3.1 - Fast"` / `"Veo 3.1 - Quality"` (`8`s fijos) para planos principales.
     - `cast_ids`: IDs registrados en `cast_registry.json`.
     - `motion_prompt` en inglés: una toma continua, un único movimiento físico de cámara, una acción con inicio y cierre claros, y marcas de tiempo (`0.0-1.8s:`) si hay `spoken_dialogue`.
4. **Validación Pre-Vuelo Obligatoria**:
   - Ejecutar `flowforge_validate_shot_manifest(production_slug, episode_id, compile_prompts=true)`.
   - Resolver el 100% de `errors` y revisar `warnings` hasta obtener `valid: true`.
   - Dejar `episode_gates.script: "in_review"` y mostrar resumen de planos + estimación preliminar de créditos.
