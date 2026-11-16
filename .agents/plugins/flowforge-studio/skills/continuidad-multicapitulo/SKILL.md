---
name: continuidad-multicapitulo
description: >-
  Usar este skill para mantener continuidad perfecta de personajes, estado narrativo, raccord
  óptico y presupuesto de créditos al pasar del Capítulo N al Capítulo N+1 de una serie.
---

# Protocolo de Continuidad Multicapítulo (`continuidad-multicapitulo`)

Garantiza que una serie de múltiples episodios conserve exactamente el mismo reparto, estética y coherencia dramática aunque cada capítulo se ruede en sesiones o proyectos distintos de Google Flow.

## Procedimiento entre Capítulos (`Ep N` -> `Ep N+1`)

1. **Cierre Certificado del Capítulo `N`**:
   - Al ejecutar `flowforge_master_episode` en el Capítulo `N`, verificar que exista `episodes/ep_{NN}_{slug}/05_master/ep_final_continuity_frame.png`.
   - Documentar brevemente el estado dramático final (ubicación de personajes, vestuario alterado o heridas/objetos en mano) para pasarlo como `narrative_state_in` del Capítulo `N+1`.
2. **Apertura Enlazada del Capítulo `N+1`**:
   - Ejecutar `flowforge_init_episode` con `episode_number = N + 1`. La herramienta enlaza automáticamente `previous_episode_final_frame` en `episode.yaml`.
   - Si el Capítulo `N+1` arranca en el mismo instante exacto del *cliffhanger* anterior, asignar ese `previous_episode_final_frame` como `first_frame_rel_path` del Plano 1 del nuevo capítulo.
3. **Rehidratación del Reparto en Proyectos Limpios de Flow**:
   - Cuando un proyecto de Google Flow acumula demasiados mosaicos virtuales, abrir un proyecto limpio para el nuevo capítulo (`flow_new_project` con título `{Serie} - Ep {NN}`).
   - Como los personajes de Google Flow viven por proyecto, recrear en el proyecto nuevo únicamente los personajes activos en ese episodio leyendo su `verbatim_prompt_descriptor` o `portrait_rel_path` desde `02_cast_and_ingredients/cast_registry.json` (**0 créditos**) y marcar `cast_rehydrated: true` en `episode.yaml`.
4. **Actualización Controlada de Look**:
   - Si la historia exige que un personaje cambie de vestuario a partir del Capítulo `N+1`, registrar una variante nueva en `cast_registry.json` (ej. `elena_winter_coat`) en lugar de sobrescribir el activo original, preservando reproducibilidad histórica de los capítulos previos.
