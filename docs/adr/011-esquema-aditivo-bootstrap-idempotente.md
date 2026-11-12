# ADR-011: Esquema aditivo con bootstrap idempotente — Alembic queda fuera del repo

## Status
Proposed (12 nov 2026) — pendiente de aceptación de David. Al aceptarse pasa a Accepted con fecha, como el resto de la serie.

## Context and Problem Statement

Estado real del repo (main @ e8d8b19, auditado 12 nov 2026): `alembic==1.19.1` está pineado en `requirements.txt`, pero no existe `alembic.ini` ni carpeta de migraciones en ninguna rama, y ningún commit del historial menciona migraciones. El PRD lo daba como "migraciones previstas". Es decir: Alembic es hoy una dependencia sin uso real, una segunda vía medio viva que invita a la confusión sobre qué manda.

El esquema real lo garantiza un bootstrap idempotente (`src/db/bootstrap.py`, BUG-010): enums condicionales por `pg_type` + `create_all(checkfirst)` en una única transacción, corriendo al arranque. Ese mecanismo resolvió de raíz los despliegues limpios (`docker compose down -v`) y los despliegues por `include:` de clientes: un cliente monta el stack vía submodule + `include:` y no puede ejecutar un paso manual de `alembic upgrade head` sin romper el arranque autónomo que vende el substrate.

La fricción de montar Alembic de verdad en este modelo es real: añade una segunda fuente de verdad (migraciones vs modelos), una tabla de estado (`alembic_version`) que sincronizar en cada despliegue, y un paso manual en el arranque de cada cliente. Y el riesgo de quedarse solo con bootstrap también es real: `create_all` no altera tablas existentes (harían falta `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` escritos a mano) y los enums de Postgres son rígidos (añadir un valor es viable con salvedad transaccional; renombrar o eliminar valores es doloroso).

**Pregunta:** ¿cómo evoluciona el esquema relacional de coreAI sin pagar el coste de Alembic en despliegues limpios y por `include:`, y sin perder la guarda contra cambios destructivos sobre datos existentes?

## Decision Drivers

*   Fuente de verdad única — los modelos ORM describen todo el esquema; no hay migraciones que mantener ni tabla de estado.
*   Arranque autónomo — un cliente que despliega por `include:` no ejecuta pasos manuales; el esquema se garantiza solo al arranque.
*   Cambios solo aditivos — nada destructivo (DROP, rename, cambio de tipo de columna, eliminar o renombrar valores de enum) sin ADR explícito y script one-shot documentado.
*   La guarda es una regla escrita + revisión de PR, no una herramienta — Alembic no impide escribir una migración destructiva; una norma sí se puede auditar en review.
*   Despliegues limpios frecuentes — `down -v` es rutina en el lab; deben funcionar sin estado previo que proteger.

## Considered Options

*   Opción A — Bootstrap idempotente único + regla de esquema aditivo; el pin de Alembic se elimina de `requirements.txt` (elegida).
*   Opción B — Montar Alembic de verdad (`alembic.ini` + `env.py` + `versions/`, baseline del esquema actual).
*   Opción C — Doble mecanismo: bootstrap para despliegues limpios + Alembic para despliegues existentes (el status quo implícito).

## Decision Outcome

**Los modelos ORM son la única fuente de verdad del esquema; el repo no tendrá migraciones de Alembic y el pin se elimina de `requirements.txt`.** La evolución del esquema queda sometida a la regla aditiva y al bootstrap idempotente ampliado:

- **Regla de esquema aditivo.** Solo se permite añadir: tabla nueva, columna nueva (nullable o con default), índice nuevo, valor nuevo de enum vía `ALTER TYPE ... ADD VALUE IF NOT EXISTS`. Prohibido sin ADR explícito: DROP, rename, cambiar el tipo de una columna, eliminar o renombrar valores de enum.
- **Bootstrap idempotente ampliado** (`src/db/bootstrap.py`): enums condicionales + `create_all(checkfirst)` + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` para columnas nuevas sobre tablas existentes + `ALTER TYPE ... ADD VALUE IF NOT EXISTS` para valores de enum, con commit intermedio (Postgres no permite usar el valor nuevo en la misma transacción). Sigue corriendo al arranque del worker y del core-mcp, sin pasos manuales.
- **Lo que este ADR NO decide:** la implementación concreta (sub-issue [ADR-011-001]); el caso excepcional de un despliegue con datos irreemplazables que exija un cambio no aditivo — se resuelve montando Alembic puntualmente con baseline del esquema actual y su propio ADR, no reinstalándolo como mecanismo por defecto.

### Positive Consequences
*   **Arranque autónomo de verdad** en despliegues por `include:` de clientes, sin pasos manuales ni contenedores one-shot.
*   **Una sola fuente de verdad**: desaparece `alembic_version` y la divergencia modelo/migraciones.
*   **La guarda contra "tocar la DB a lo loco" queda escrita y auditable**: regla aditiva + revisión de PR, en vez de una dependencia que solo añade fricción.

### Negative Consequences
*   **Los cambios sobre tablas con datos existentes exigen ALTERs idempotentes escritos a mano** — disciplina, no automatización; el coste se paga en review, no en tooling.
*   **Un despliegue con datos irreemplazables queda sin red de migraciones**; se acepta porque el modelo de despliegue de coreAI es local-first con recreación asumible (`down -v`), y el caso excepcional se trata con ADR puntual.
*   **Los enums de Postgres siguen siendo rígidos**: añadir valores exige la salvedad transaccional (commit intermedio), documentada en el bootstrap.

## Pros and Cons of the Options

### Opción A — Bootstrap único + regla aditiva (elegida)
*   Good, porque elimina la segunda fuente de verdad y el paso manual en clientes por `include:`.
*   Good, porque los despliegues limpios (`down -v`) quedan cubiertos sin estado previo.
*   Bad, porque los cambios sobre tablas existentes son ALTERs manuales idempotentes, no autogenerados.
*   Bad, porque no protege datos irreemplazables en despliegues existentes; se asume recreación.

### Opción B — Alembic montado de verdad
*   Good, porque da red de seguridad y diffs autogenerados para despliegues con datos.
*   Bad, porque añade segunda fuente de verdad + tabla `alembic_version` + `upgrade head` en cada despliegue, incluido el de clientes por `include:`.
*   Bad, porque en un repo que hace `down -v` de rutina las migraciones son lastre que se mantienen pero rara vez se ejercitan.

### Opción C — Doble mecanismo (status quo implícito)
*   Good, porque cubriría limpios y existentes.
*   Bad, porque es lo peor de ambos mundos: dos mecanismos vivos que divergen, y hoy Alembic está a medias (pin sin setup), que es exactamente la confusión que este ADR elimina.

## Related

*   BUG-010 — Bootstrap idempotente del esquema relacional en despliegue limpio / por `include:` (`src/db/bootstrap.py`).
*   BUG-001 (#34) — Garantía idempotente de colecciones Qdrant al arranque (gemelo del lado vectorial).
*   Issue #30 (4.4) — Resiliencia, retries y DLQ: sus columnas nuevas (`retry_count`, `available_at`) son el primer cambio de esquema bajo esta regla, vía bootstrap aditivo.
*   PRD coreAI — la mención legacy "migraciones Alembic previstas" queda retirada por este ADR.

---

# ISSUES

### [ADR-011-001] Implementación — quitar el pin de Alembic y ampliar el bootstrap con ALTERs idempotentes — #100
Pendiente. Quita `alembic` de `requirements.txt`; amplía `src/db/bootstrap.py` con `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` y `ALTER TYPE ... ADD VALUE IF NOT EXISTS` (con commit intermedio); suite hermética que verifique idempotencia sobre esquema existente y despliegue limpio.
