---
name: flow-prompt-engineering
description: >-
  Manual técnico de ingeniería de prompts ópticos, reglas físicas y selección de modelos en Google
  Flow (Veo 3.1, Omni Flash y Nano Banana 2.1). Consultarlo al redactar o auditar planos.
---

# Ingeniería de Prompts Ópticos y Reglas Físicas para Google Flow

Referencia técnica verificada empíricamente sobre el motor de Google Flow (`flow.google.com`).

## 1. Orden Canónico del Prompt (En Inglés)

Redactar siempre una única toma continua siguiendo este orden:
1. **Sujeto e Invariantes Literales**: Descripción física completa del personaje/objeto copiada de `cast_registry.json` + acción única con inicio y fin definidos.
2. **Encuadre y Movimiento de Cámara**: Tamaño de plano (*medium close-up*, *wide establishing shot*) + lente + **un solo movimiento físico** (*static locked-off tripod*, *slow dolly push-in*, *smooth 360 orbit*, *crane up*, *subtle handheld*).
3. **Locación y Continuidad Espacial**: Entorno concreto visto desde el lado de la cámara.
4. **Estilo Visual e Iluminación**: Copiados del `visual_dna` de `production.yaml`.
5. **Sonido Diegético y Diálogo con Marcas de Tiempo**: Diálogo entre comillas dentro de intervalos en segundos (`0.0-1.5s: Matteo keeps his hands still and says "Wait here."; 1.5-4.0s: he steps into the corridor`).

## 2. Tabla de Modelos y Créditos en Google Flow (`x1`)

| Modelo en Flow | Duraciones Soportadas | Costo Aprox. (`x1`) | Uso Ideal en el Estudio |
| :--- | :--- | :--- | :--- |
| `Nano Banana 2.1` / `Pro` | Imagen fija | **0 créditos** | Retratos de reparto, *storyboards* y *First/Last Frames*. |
| `Omni 1.1 Flash` | `4s`, `6s`, `8s`, `10s` (`360p`/`720p`) | **7 – 15 créditos** | Pruebas de movimiento, borradores y transiciones entre dos cuadros. |
| `Veo 3.1 - Lite` | `8s` fijos | **10 créditos** | Planos secundarios y ritmo ágil económico. |
| `Veo 3.1 - Fast` | `8s` fijos | **20 créditos** | Caballo de batalla para capítulos completos con audio nativo. |
| `Veo 3.1 - Quality` | `8s` fijos | **100 créditos** | Planos héroe (*hero shots*) de máxima complejidad física; exigir tope explícito. |

## 3. Siete Reglas Anti-Fallos Físicos

1. **Prohibido negar objetos**: Escribir *"no phone"* o *"never holding a weapon"* hace que el modelo dibuje ese objeto en escena. Describir solo lo presente.
2. **Cero giros de objetos de cabeza**: No girar botellas, teléfonos o armas 180° de punta a punta; a mitad de giro el modelo invierte tapas o extremos.
3. **Evitar sorbetes transparentes y partículas caóticas**: Usar recipientes opacos y evitar confeti, escombros volando o multitudes complejas.
4. **Evitar pedir doblar esquinas con cámara viajera**: Pedir *"gira a la izquierda en el callejón"* en un *tracking* produce deslizamiento lateral irreal o cortes bruscos a mitad del clip; mantener trayectoria recta o cortar a plano nuevo.
5. **Una acción concreta, no actividad vaga**: Reemplazar *"caminando sin rumbo"* por *"da tres pasos firmes hasta el mostrador y apoya la llave"*.
6. **Coherencia visual-verbal**: Si alguien grita *"¡Cuidado con el fuego!"*, describir llamas visibles desde el segundo `0.0s` desde el ángulo de cámara.
7. **Unión exacta vs. chip de personaje**: En `flow_continue(mode="exact")`, no adjuntar simultáneamente el chip de personaje; el rostro ya está contenido en el último fotograma real.
