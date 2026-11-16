# FlowForge Studio — Constitución del Framework Audiovisual para Antigravity + Google Flow

Este directorio es **FlowForge Studio**, un framework de producción audiovisual de extremo a extremo diseñado para orquestar desde **una idea vaga de una línea** hasta **capítulos completos masterizados** (series episódicas, cortometrajes y videos musicales) combinando **Google Antigravity** con **Google Flow (`flow.google.com`)**.

---

## Ley 00: Naturaleza del Entorno y Transparencia Técnica

1. **Estudio Cinematográfico, No Consola Técnica**: La persona usuaria actúa como **Dirección General / Creación**. Los agentes resuelven en segundo plano toda validación de esquemas, extracción de fotogramas (`ffmpeg`/`ffprobe`), cálculo de créditos, armado de carpetas y operación del puente MCP hacia Google Flow. Nunca se solicita al usuario ejecutar comandos de terminal ni depurar código.
2. **Idioma Editorial vs. Idioma de Motor Óptico**:
   - Las biblias de universo, arcos de temporada, guiones literarios y reportes de revisión se redactan en **español neutro/profesional** (salvo que `production.yaml` defina otro idioma para la obra).
   - Todos los prompts enviados a Google Flow (`flow_generate`, `flow_agent_images`, `flow_character`, `flow_edit`, `flow_continue`) se redactan obligatoriamente en **inglés cinematográfico preciso**, ya que el motor óptico y el puente `google-flow` operan sobre selectores y semántica en inglés (`?hl=en`).

---

## Ley 01: Aislamiento Estricto por Producción y Capítulo (`Cartridge Isolation`)

1. **Producción Activa Obligatoria (`production_slug`)**: Toda lectura o escritura creativa exige un `production_slug` explícito confinado a `productions/{production_slug}/`. Si no existe o no fue indicado, consultar antes de acceder a `productions/`.
2. **Prohibición de Búsquedas Cruzadas**: Queda prohibido ejecutar búsquedas globales (`grep`, `find`) sobre la raíz `productions/`. Cada universo narrativo es hermético.
3. **Memoria Persistente en Disco porque Google Flow No Tiene Memoria**: Google Flow carece de memoria entre escenas y entre proyectos. Toda identidad visual (`visual_dna`), descriptores literales de personajes (`02_cast_and_ingredients/cast_registry.json`), estado de episodios (`episodes/ep_{NN}_{slug}/episode.yaml`) y contabilidad de créditos (`credit_ledger.json`) residen en el cartucho de la producción y deben inyectarse explícitamente en cada plano.

---

## Ley 02: Arquitectura Dual de Google Flow (Modo Agente vs. Modo Directo)

El framework separa estrictamente los dos modos de `flow.google.com` según la etapa productiva:

1. **Modo Agente ACTIVADO (`flow_agent_images`) -> Solo Previsualización y Storyboard (0 Créditos)**:
   - Se emplea para generar lotes paralelos de imágenes de *storyboard* ($\le 20$ escenas por tanda, costo **0 créditos** con *Nano Banana 2.1*) adjuntando referencias de reparto/escenarios.
   - **Prohibido para rodaje final de video**: El Agente de Flow parafrasea los prompts ópticos y sus tarjetas fallidas carecen de botón *Retry* gratuito.
2. **Modo Directo / Agente DESACTIVADO (`flow_generate`, lote `x1`) -> Rodaje Principal de Video**:
   - Todo plano de video pagado se ejecuta con el modo Agente apagado y multiplicador **`x1`** (evitando cobros dobles accidentales por `x2`/`x4`).
   - Cada plano debe pasar previamente por `flowforge_validate_shot_manifest` y `flowforge_quote_and_log_credits`.
3. **Regla de Retratos para Video**: Los personajes recurrentes destinados a video deben crearse mediante descripción ilustrada/generada (`flow_character` con `describe` o lámina neutra de *Nano Banana 2.1*), evitando subir fotografías de personas reales que disparen bloqueos de políticas en video.
4. **Regla de Continuidad Exacta (`chain_previous` / `flow_continue`)**: Para cortes invisibles entre dos planos continuos se prioriza el anclaje al último fotograma real extraído (`mode: "exact"` o `flowforge_extract_frame`), dado que Google Flow no permite fijar simultáneamente un cuadro inicial exacto y un avatar externo sin producir salto visual.

---

## Ley 03: Máquina de Estados, Compuertas Humanas (`HITL`) y Presupuesto de Créditos

Ningún agente marca por cuenta propia una compuerta como `approved`. Al concluir una fase, deja el estado en `in_review`, presenta el entregable (y la cotización de créditos si el siguiente paso consume saldo de Google AI Ultra) y **SE DETIENE** hasta recibir aprobación explícita.

### Compuertas de Nivel Producción (`production.yaml`)
1. `universe_bible`: Semilla expandida en biblia de mundo, `visual_dna` y arco de temporada/obra (`01_universe_bible/`).
2. `cast_locked`: Registro invariable de personajes, utilería y locaciones (`02_cast_and_ingredients/cast_registry.json`) + retratos maestros aprobados (0 créditos).
3. `sound_identity`: Identidad sonora, asignación de voces (`voice_cast.json`) y mapa musical (`03_sound_and_music/`). *(En formato `music-video`, `sound_identity` es la compuerta #1 obligatoria antes de guion).*

### Compuertas por Capítulo / Episodio (`episodes/ep_{NN}_{slug}/episode.yaml`)
1. `outline`: Escaleta dramática del capítulo conectada al episodio previo.
2. `script`: Guion literario + `shot_manifest.json` validado sin errores por `flowforge_validate_shot_manifest`.
3. `storyboard`: Viñetas y *First/Last Frames* generados a **0 créditos** y aprobados en plancha de contacto (`flowforge_render_contact_sheet`).
4. `render`: Clips de video generados en Google Flow (previa aprobación humana del presupuesto en créditos) y descargados en `1080p` sin costo adicional (`flow_download`).
5. `postprod`: Locuciones/diálogos (`flow_narrate`), transcripción verificada palabra por palabra (`flow_transcribe` -> confirmación humana -> `flow_captions`) y mezcla sonora.
6. `master`: Ensamblaje final normalizado a `-14 LUFS` (`flowforge_master_episode`) y fotograma final exportado para enlazar el capítulo `N+1`.
