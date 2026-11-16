# Reglas Activas del Plugin `flowforge-studio`

1. **Orquestación de Dos Motores MCP**:
   - Usa el servidor MCP `flowforge-engine` para toda gobernanza local determinista: inicializar producciones/episodios, auditar compuertas (`flowforge_check_gates`), transicionarlas con auditoría (`flowforge_set_gate`), validar manifiestos de planos (`flowforge_validate_shot_manifest`), presupuestar y registrar créditos (`flowforge_quote_and_log_credits`), extraer fotogramas exactos (`flowforge_extract_frame`), generar planchas de storyboard (`flowforge_render_contact_sheet`) y masterizar episodios (`flowforge_master_episode`).
   - Usa el servidor MCP `google-flow` para operar `flow.google.com` sobre el perfil aislado de Chrome (`flow_status`, `flow_new_project`, `flow_character`, `flow_agent_images`, `flow_generate`, `flow_continue`, `flow_edit`, `flow_wait`, `flow_download`, `flow_narrate`, `flow_transcribe`, `flow_captions`).
2. **Protección Innegociable de Créditos Google AI Ultra**:
   - Jamás llames a `flow_generate` (modo video), `flow_edit` ni `flow_continue` sin haber ejecutado antes `flowforge_validate_shot_manifest` y `flowforge_quote_and_log_credits`, ni sin haber mostrado al usuario el costo exacto estimado en créditos recibiendo su confirmación explícita. Conserva el `quote_id` devuelto y pásalo en cada `flowforge_quote_and_log_credits(action="record")`.
   - Cuando la dirección apruebe una compuerta, llama a `flowforge_set_gate(state="approved", human_confirmation="<mensaje literal de aprobación>")`. Nunca inventes ni parafrasees la confirmación.
   - Genera siempre las hojas de personajes y los *First/Last Frames* primero como imágenes gratuitas (**0 créditos** con *Nano Banana 2.1* o `flow_agent_images`) antes de gastar créditos en video.
   - Nunca re-ejecutes un trabajo que devuelva `still_working: true` con un `task_id`; usa exclusivamente `flow_wait` hasta que `finished` sea `true` y verifica `flow_assets` antes de reintentar para no pagar dos veces la misma toma.
3. **Aislamiento y Continuidad Episódica**:
   - Toda operación exige un `production_slug` válido dentro de `productions/{production_slug}/`.
   - Registra cada personaje, criatura, objeto y escenario como ficha estructurada (`visual_spec` + `continuity_locks`) con `flowforge_register_cast_asset`; el `verbatim_prompt_descriptor` se compila solo y nunca se escribe a mano.
   - En cada plano de `shot_manifest.json`, declara `cast_ids` (personajes/criaturas), `location_id` (obligatorio en video) y `props` (con `held_by`). `flowforge_validate_shot_manifest` inyecta los núcleos en `compiled_flow_prompt` y los retratos en `resolved_reference_images`, ya que Google Flow no conserva memoria entre escenas. Usa siempre ambos campos al rodar.
   - Todo cambio visible de look o estado se registra como variante (`base_asset_id` + `valid_from_episode`), nunca sobrescribiendo la ficha original.
4. **Protocolo Obligatorio de Memoria e Indexación Continua**:
   - Antes de redactar o desglosar un capítulo, consulta `flowforge_memory_context`, `flowforge_entity_dossier` o `flowforge_search` (además de `flowforge_craft_kb(action="search")` para mejores prácticas ópticas).
   - Después de aprobar una biblia, cambiar el estado de un personaje/objeto, resolver un hilo argumental o cerrar un capítulo, llama proactivamente a `flowforge_memory_save` (con `topic_key`), `flowforge_graph_link` y `flowforge_index_production`.
   - Nunca mezcles tramas ni nombres de personajes dentro de `flowforge_craft_kb`; ese almacén es exclusivamente para conocimiento técnico reutilizable entre producciones.
