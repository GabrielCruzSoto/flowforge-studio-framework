---
name: rodar-capitulo-flow
description: >-
  Usar este skill cuando el usuario invoca /rodar-capitulo-flow o autoriza el rodaje de video en
  Google Flow (Modo Directo, Agente OFF, x1) con control de créditos y continuidad.
---

# Rodaje Principal de Video en Google Flow (`/rodar-capitulo-flow`)

Ejecuta el rodaje de los planos de video de un capítulo en **Modo Directo (`Agente OFF`, `x1`)** protegiendo el presupuesto de créditos Google AI Ultra y garantizando continuidad óptica.

## Procedimiento Paso a Paso

1. **Auditoría Previa y Presupuesto Obligatorio**:
   - Ejecutar `flowforge_check_gates(target_gate="render")` (`storyboard == "approved"`).
   - Ejecutar `flowforge_validate_shot_manifest` (`valid: true`).
   - Ejecutar `flowforge_quote_and_log_credits(action="quote")` y conservar el `quote_id` devuelto (si `within_budget: false`, no se emite `quote_id` y no se rueda). Mostrar al usuario el desglose exacto de créditos y el saldo restante del mes/capítulo. **No invocar generación de video sin confirmación explícita del usuario.**
2. **Preparación de Proyecto en Flow (`flow_status`)**:
   - Confirmar que Chrome dedicado está autenticado y con el proyecto abierto. Si es un proyecto nuevo por capítulo, verificar que los personajes de `cast_registry.json` estén creados (`cast_rehydrated: true`).
3. **Ejecución Secuencial y Continuidad**:
   - Enviar los planos usando `compiled_flow_prompt` y adjuntando `resolved_reference_images` en `flow_generate` (Modo Directo, `x1`, fijando `max_credits` acorde a la cotización). Nunca reescribir a mano el prompt compilado: eso rompe la continuidad garantizada.
   - Para planos continuos sin corte visible, usar `chain_previous: true` o `flow_continue(mode="exact")` (recordando no adjuntar chip de personaje simultáneamente en uniones exactas).
   - Esperar con `flow_wait` hasta `finished: true`. Jamás repetir una llamada que devuelva `still_working: true`; consultar `flow_assets` ante cualquier duda.
4. **Descarga `1080p` Gratuita y Contabilidad**:
   - Descargar cada clip terminado (aprovechando el escalado gratuito a `1080p` en `flow_download`) dentro de `03_flow_renders/raw_clips/`.
   - Verificar duración y audio con `flowforge_probe_media` y extraer fotogramas frontera con `flowforge_extract_frame` si se requieren ajustes.
   - Registrar los créditos reales consumidos mediante `flowforge_quote_and_log_credits(action="record", quote_id)` (si se supera lo cotizado, exige `overrun_reason`) y ejecutar `flowforge_set_gate(gate="render", state="in_review", episode_id)`.
