# Reglas Activas del Plugin `flowforge-studio`

1. **Orquestación de Dos Motores MCP**:
   - Usa el servidor MCP `flowforge-engine` para toda gobernanza local determinista: inicializar producciones/episodios, auditar compuertas (`flowforge_check_gates`), validar manifiestos de planos (`flowforge_validate_shot_manifest`), presupuestar y registrar créditos (`flowforge_quote_and_log_credits`), extraer fotogramas exactos (`flowforge_extract_frame`), generar planchas de storyboard (`flowforge_render_contact_sheet`) y masterizar episodios (`flowforge_master_episode`).
   - Usa el servidor MCP `google-flow` para operar `flow.google.com` sobre el perfil aislado de Chrome (`flow_status`, `flow_new_project`, `flow_character`, `flow_agent_images`, `flow_generate`, `flow_continue`, `flow_edit`, `flow_wait`, `flow_download`, `flow_narrate`, `flow_transcribe`, `flow_captions`).
2. **Protección Innegociable de Créditos Google AI Ultra**:
   - Jamás llames a `flow_generate` (modo video), `flow_edit` ni `flow_continue` sin haber ejecutado antes `flowforge_validate_shot_manifest` y `flowforge_quote_and_log_credits`, ni sin haber mostrado al usuario el costo exacto estimado en créditos recibiendo su confirmación explícita.
   - Genera siempre las hojas de personajes y los *First/Last Frames* primero como imágenes gratuitas (**0 créditos** con *Nano Banana 2.1* o `flow_agent_images`) antes de gastar créditos en video.
   - Nunca re-ejecutes un trabajo que devuelva `still_working: true` con un `task_id`; usa exclusivamente `flow_wait` hasta que `finished` sea `true` y verifica `flow_assets` antes de reintentar para no pagar dos veces la misma toma.
3. **Aislamiento y Continuidad Episódica**:
   - Toda operación exige un `production_slug` válido dentro de `productions/{production_slug}/`.
   - En cada plano de `shot_manifest.json`, inyecta literalmente el campo `verbatim_prompt_descriptor` de cada personaje/objeto presente desde `02_cast_and_ingredients/cast_registry.json`, ya que Google Flow no conserva memoria entre escenas.
