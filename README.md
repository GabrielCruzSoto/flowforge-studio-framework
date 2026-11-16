# FlowForge Studio (`~/flowforge-studio`)

Framework integral de dirección y producción audiovisual para **Google Antigravity + Google Flow (`flow.google.com`)**, diseñado para llevar cualquier obra desde **una idea vaga de una línea** hasta **capítulos completos masterizados (`1080p`, `-14 LUFS`)** maximizando la eficiencia de la suscripción **Google AI Ultra**.

---

## Arquitectura General del Framework

```mermaid
flowchart TD
    Director["Dirección Humana (Idea Vaga -> Aprobaciones HITL)"] --> Showrunner["Agente Principal: flowforge-showrunner"]

    subgraph Agents["Equipo de Subagentes Especialistas (Antigravity)"]
        Showrunner --> UA["universe-architect (Biblia y Temporada)"]
        Showrunner --> CAD["casting-art-director (Ingredients y Retratos 0 cr)"]
        Showrunner --> ESW["episodic-screenwriter (Guion y Shot Manifest)"]
        Showrunner --> SPA["storyboard-previz-artist (Modo Agente 0 cr)"]
        Showrunner --> DOP["flow-cinematographer (Modo Directo x1)"]
        Showrunner --> PPS["postprod-sound-finisher (Voces, Subtítulos, Master)"]
        Showrunner --> CA["continuity-archivist (Memoria FTS5 y Grafo de Canon)"]
    end

    subgraph MCPLayer["Doble Capa MCP"]
        EngineMCP["MCP Local: flowforge-engine (17 Herramientas: Gobernanza, Memoria FTS5 y Master)"]
        BrowserMCP["MCP Puente: google-flow (22 Herramientas CDP sobre Chrome Aislado)"]
    end

    UA & CAD & ESW & SPA & DOP & PPS & CA --> EngineMCP
    CAD & SPA & DOP & PPS --> BrowserMCP
    EngineMCP --> Cartridge["productions/{production_slug}/ (Estado, Canon FTS5, Grafo y Créditos)"]
    EngineMCP --> CraftKB[".studio_memory/craft_kb.db (Aprendizaje Óptico Transversal)"]
    BrowserMCP --> GoogleFlow["Google Flow Web (flow.google.com - Créditos AI Ultra)"]
```

---

## Qué Problemas Reales Resuelve

1. **Google Flow no tiene memoria entre escenas ni entre proyectos:** `FlowForge Studio` mantiene una bóveda local (`cast_registry.json`) e inyecta automáticamente el descriptor literal en inglés (`verbatim_prompt_descriptor`) de cada personaje, utilería y locación en todos los planos de todos los capítulos.
2. **Separación inteligente entre Modo Agente y Modo Directo:**
   - **Modo Agente ACTIVADO (`flow_agent_images`, 0 créditos):** Usado exclusivamente en preproducción para levantar *storyboards* en paralelo sin gastar saldo.
   - **Modo Directo / Agente DESACTIVADO (`flow_generate`, lote `x1`):** Usado en el rodaje principal de video para conservar control óptico literal, cotización previa de créditos y botón *Retry* gratuito.
3. **Linter Pre-Vuelo Anti-Desperdicio de Créditos (`flowforge_validate_shot_manifest`):** Bloquea multiplicadores accidentales (`x2`/`x4`), duraciones incompatibles por modelo, frases negativas que alucinan objetos y diálogos sin marcas de tiempo en segundos.
4. **Continuidad Multicapítulo Frame-a-Frame:** Al masterizar el Capítulo `N`, el motor extrae automáticamente `ep_final_continuity_frame.png` y lo enlaza como punto de partida del Capítulo `N+1`.
5. **Memoria Persistente, Indexación Híbrida FTS5 (BM25) y Grafo de Canon:** Indexa incrementalmente por SHA-256 biblias, guiones, planos, subtítulos `.srt` y documentos de `00_references/` en SQLite FTS5 (`unicode61` sin tildes), versiona memorias y grafo de entidades en `memory/canon_snapshot.json` y separa una base global `.studio_memory/craft_kb.db` para aprendizajes técnicos de cámara y sonido reutilizables entre series.

---

## Componentes Incluidos

### 1. Agentes y Subagentes (`.agents/plugins/flowforge-studio/agents/`)
| Agente | Rol en el Estudio |
| :--- | :--- |
| `flowforge-showrunner` | Productor ejecutivo principal (`mainAgent: true`). Orquesta fases, compuertas humanas y presupuesto. |
| `universe-architect` | Convierte semillas vagas en `universe_bible.md`, `visual_dna` y arco multicapítulo `season_01_arc.md`. |
| `casting-art-director` | Construye `cast_registry.json` y retratos maestros ilustrados a **0 créditos** (evitando bloqueos por fotos reales). |
| `episodic-screenwriter` | Redacta guion literario y desglose técnico `shot_manifest.json` adaptado a la física de Veo y Omni Flash. |
| `storyboard-previz-artist` | Genera viñetas y *First/Last Frames* a **0 créditos** y arma la plancha `storyboard_grid.png`. |
| `flow-cinematographer` | Rueda video en Modo Directo (`x1`), controla tickets asíncronos (`flow_wait`) y descarga `1080p` gratis. |
| `postprod-sound-finisher` | Graba voces (`flow_narrate`), verifica transcripción (`flow_transcribe` -> `flow_captions`) y masteriza a `-14 LUFS`. |
| `continuity-archivist` | Mantiene el índice FTS5, memorias evolutivas (`topic_key`), grafo de relaciones y dossiers 360° por episodio. |

### 2. Skills Operativos (`.agents/plugins/flowforge-studio/skills/`)
- `/iniciar-produccion`: De idea vaga a cartucho inicializado, biblia de universo y arco de temporada.
- `/disenar-universo-y-reparto`: Fijación de reparto, utilería, escenarios y mapa vocal a 0 créditos.
- `/escribir-capitulo`: Escaleta, guion literario y validación completa de `shot_manifest.json`.
- `/crear-storyboard-gratis`: Storyboard y *First/Last Frames* gratuitos + plancha visual de contacto.
- `/rodar-capitulo-flow`: Cotización AI Ultra, rodaje seguro en Modo Directo y raccord exacto.
- `/postproducir-capitulo`: Locución, mezcla musical con *ducking*, subtítulos verificados y máster MP4.
- `/indexar-canon` (`memoria-e-indexacion`): Indexación documental FTS5, memoria de canon, grafo de entidades y base de oficio óptico.
- `flow-prompt-engineering`: Diccionario óptico, costos por modelo y 7 leyes físicas anti-morphing.
- `continuidad-multicapitulo`: Enlace visual y rehidratación de reparto del Capítulo `N` al `N+1`.

### 3. Servidores MCP Integrados
- **`flowforge-engine` (incluido en este framework, 17 herramientas):**
  - *Gobernanza y Multimedia (10):* `flowforge_init_production`, `flowforge_init_episode`, `flowforge_check_gates`, `flowforge_register_cast_asset`, `flowforge_validate_shot_manifest`, `flowforge_quote_and_log_credits`, `flowforge_extract_frame`, `flowforge_probe_media`, `flowforge_render_contact_sheet`, `flowforge_master_episode`.
  - *Memoria, Indexación FTS5 y Grafo (7):* `flowforge_index_production`, `flowforge_search`, `flowforge_memory_save`, `flowforge_memory_context`, `flowforge_graph_link`, `flowforge_entity_dossier`, `flowforge_craft_kb`.
- **`google-flow` (configurado globalmente en Antigravity):** 22 herramientas sobre Chrome aislado (`~/.flow-mcp/chrome-profile`) para operar `flow.google.com` con los créditos de Google AI Ultra.

---

## Cómo Empezar

1. Abrir la carpeta `/home/gabrielcruzsoto/flowforge-studio` como Workspace en **Google Antigravity**.
2. Escribir en el chat de Antigravity:
   > `/iniciar-produccion Quiero hacer una serie sobre [tu idea vaga en una línea]`
3. El `flowforge-showrunner` guiará cada compuerta creativa desde la biblia hasta el archivo `.mp4` final de cada capítulo sin necesidad de ejecutar comandos manuales.
