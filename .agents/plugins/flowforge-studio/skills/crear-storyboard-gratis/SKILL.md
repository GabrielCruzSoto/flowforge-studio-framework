---
name: crear-storyboard-gratis
description: >-
  Usar este skill cuando el usuario invoca /crear-storyboard-gratis o pide previsualizar un
  capítulo y generar sus keyframes (First/Last Frames) a 0 créditos en Google Flow.
---

# Storyboard y Keyframes a 0 Créditos (`/crear-storyboard-gratis`)

Aprovecha la gratuidad de generación de imágenes en Google Flow (**0 créditos** en *Nano Banana 2.1* y Modo Agente) para aprobar toda la composición visual del capítulo antes de invertir créditos en video.

## Procedimiento Paso a Paso

1. **Verificar Compuerta y Sesión**:
   - Confirmar `flowforge_check_gates(target_gate="storyboard") -> unlocked: true`.
   - Llamar a `flow_status` para verificar sesión activa en `flow.google.com`.
2. **Seleccionar Modo Óptimo de Imagen (0 Créditos)**:
   - **Lote Rápido con Modo Agente (`flow_agent_images`)**: Cuando el capítulo tiene entre 5 y 20 planos y se busca validar composición general rápidamente. Adjuntar personajes y locaciones en `references` y `scene_references`. Usar `flow_wait` hasta completar; si algún cuadro falla, usar `flow_retry`.
   - **Precisión Exacta con Modo Directo (`flow_generate`, tipo imagen, *Nano Banana 2.1*)**: Cuando se requieren pares exactos `first_frame` + `last_frame` sin paráfrasis del Agente.
3. **Vincular Keyframes y Armar Plancha de Contacto**:
   - Guardar los archivos `.png` en `episodes/{episode_id}/02_storyboard/keyframes/`.
   - Actualizar `first_frame_rel_path` (y `last_frame_rel_path` cuando aplique) en `01_script/shot_manifest.json` y re-validar con `flowforge_validate_shot_manifest`.
   - Ejecutar `flowforge_render_contact_sheet` generando `02_storyboard/storyboard_grid.png`.
4. **Compuerta Visual (`HITL`)**:
   - Ejecutar `flowforge_set_gate(gate="storyboard", state="in_review", episode_id)`, presentar `storyboard_grid.png` a dirección y **DETENERSE** hasta su aprobación.
