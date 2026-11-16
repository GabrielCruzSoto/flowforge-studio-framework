---
name: postproducir-capitulo
description: >-
  Usar este skill cuando el usuario invoca /postproducir-capitulo o pide grabar narración,
  sincronizar música, añadir subtítulos verificados y masterizar el episodio final en MP4.
---

# Postproducción, Subtítulos y Masterización (`/postproducir-capitulo`)

Une los planos aprobados del capítulo, mezcla narración y música con *ducking*, normaliza sonoridad a estándar de streaming (`-14 LUFS`) y prepara subtítulos libres de errores.

## Procedimiento Paso a Paso

1. **Verificar Compuertas**:
   - Ejecutar `flowforge_check_gates(target_gate="postprod")` (`render == "approved"` y `sound_identity == "approved"`).
2. **Generar Locución / Narración Gratuita (si aplica)**:
   - Usar `flow_narrate` (motor local `kokoro` o `gemini` según `03_sound_and_music/voice_cast.json`, **0 créditos Flow**) guardando el audio en `episodes/{episode_id}/04_dialogue_and_sfx/`.
3. **Masterización Audiovisual (`flowforge_master_episode`)**:
   - Invocar `flowforge_master_episode` pasando la lista ordenada `clip_rel_paths`, `narration_rel_path` y `music_rel_path`.
   - Verificar en el retorno que se hayan generado tanto `05_master/{episode_id}_master.mp4` (`-14 LUFS`) como `05_master/ep_final_continuity_frame.png` (enlace para el próximo capítulo).
4. **Subtitulado Verificado en 2 Pasos (Opcional)**:
   - Paso A: Ejecutar `flow_transcribe` sobre el máster indicando `language` (`"es"` o `"en"`).
   - Paso B: Mostrar el texto transcrito al usuario corrigiendo posibles errores fonéticos en nombres propios y esperar confirmación.
   - Paso C: Con el texto confirmado, ejecutar `flow_captions` una sola vez (estilo `clean` para cine/serie o `bold` para formato vertical `9:16`).
5. **Cierre de Capítulo**:
   - Ejecutar `flowforge_set_gate` con `state="in_review"` para `postprod` (y para `master` una vez aprobado `postprod`) y presentar ruta del máster y métricas finales.
