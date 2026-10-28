<!--
Plantilla de ADR — dialecto MADR: estructura coreAI (extraída de ADR-006/007/008)
+ secciones adoptadas de MADR oficial (adr/madr): Decision Drivers,
Considered Options y Pros and Cons of the Options.
Convenciones:
- Fichero: docs/adr/NNN-slug.md (siguiente número libre).
- Regla de la casa: el doc del ADR aterriza al repo con el arranque de la familia de issues, no al final.
- Post-implementación se añade SOLO tras implementar; bórrala del draft hasta entonces.
- Pros and Cons of the Options es opcional: si el análisis de alternativas ya vive en un
  issue de spike (p. ej. [ADR-XXX-001]), referéncialo aquí en vez de duplicarlo.
-->

# ADR-XXX: Nombre de la decisión — Tesis en una frase

## Status
Accepted (fecha) — familia [ADR-XXX] completada: [ADR-XXX-001] #NN, [ADR-XXX-002] #NN (PR #NN). Cabósito heredado, si aplica.

<!-- Durante el draft: Proposed. Al aceptar: Accepted + fecha. Vocabulario MADR admitido: proposed | rejected | accepted | deprecated | superseded by ADR-YYY. -->

## Context and Problem Statement

<!-- La situación real y la tensión, en párrafos. Datos verificables del repo (issues, eventos, PRs), no opinión: qué existe hoy, por qué duele, a quién, y qué crece si no se decide. Cierra con la Pregunta. -->

**Pregunta:** ¿la pregunta única y concreta que este ADR responde?

## Decision Drivers

<!-- Las fuerzas en juego que condicionan la decisión: cualidades deseadas, constraints, preocupaciones. Máximo 5; si hay más, la decisión es demasiado grande. -->

*   Driver 1 — p. ej. "el acceso por red a la frontera es responsabilidad del substrato, no de los inquilinos".
*   Driver 2 — p. ej. "cero duplicación de lógica entre transportes".

## Considered Options

<!-- TODAS las opciones consideradas, incluida la elegida. Títulos cortos; el detalle de cada una, abajo en Pros and Cons. -->

*   Opción A.
*   Opción B.
*   Opción C.

## Decision Outcome

<!-- La decisión en una frase, con lo esencial en negrita. Desplegar en bullets. -->

- **Lead en negrita** — explicación del punto de la decisión.
- **Lead en negrita** — explicación.

<!-- Recomendado: -->
<!-- - Vallas de alcance explícitas: qué queda fuera, qué no se decide aún. -->
<!-- - "Lo que este ADR NO decide:" — separar las decisiones que viven en otro sitio. -->

### Positive Consequences
*   **Lead en negrita.** Explicación del beneficio.

### Negative Consequences
*   **Lead en negrita.** Coste o riesgo asumido, declarado explícitamente.

## Pros and Cons of the Options

<!-- Opcional pero recomendado cuando hubo spike: aquí vive el porqué de las descartadas. Referencia al issue si el análisis ya está allí. -->

### Opción A

*   Good, porque ...
*   Neutral, porque ...
*   Bad, porque ...

### Opción B

*   Good, porque ...
*   Bad, porque ...

## Post-implementación (fecha)

<!-- Solo tras implementar la familia. Qué aterrizó y cómo difiere del texto de la decisión:
- Desviaciones registradas sobre el alcance original y por qué se justifican.
- Evidencia E2E / verificación (rol MADR "Confirmation": cómo se confirma el cumplimiento).
- Deuda viva absorbida o caducada, y por quién. -->

## Related
*   ADR-YYY (#NN) — Nombre. Relación en una frase.
*   BUG-NNN (#NN) — Nombre. Relación en una frase.

---

# ISSUES

<!-- Un bloque por sub-issue de la familia. El título lleva el ID largo; el cuerpo se actualiza al completarse. -->

### [ADR-XXX-001] Título del sub-issue — #NN
Estado (fecha). Qué cerró, con qué PR y con qué evidencia. El issue manda sobre esta línea.

### [ADR-XXX-002] Título del sub-issue — #NN
Estado (fecha, PR #NN). Qué cerró y qué dejó vivo.
