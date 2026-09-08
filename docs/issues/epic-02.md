# Epic 2: [coreAI] Capa de Memoria Unificada (Data Layer)

## Contexto del Epic
Persistencia determinista y semántica de estado sin fragmentación. Este epic abarca la configuración de PostgreSQL y Qdrant, la integración de SQLAlchemy y Alembic, la definición de los esquemas fundacionales y la creación del patrón repositorio, asegurando la cobertura de pruebas de conectividad.

---

### Issue 2.1: Motor Relacional, ORM y Esquema Base
**Contexto**
Configuración del motor relacional en PostgreSQL (SQLAlchemy + asyncpg) e inicialización del sistema de migraciones (Alembic).

**Tareas**
- [x] Configurar motor asíncrono de SQLAlchemy (`asyncpg`) e inicializar Alembic (`alembic init -t async`).
- [x] Migrar tipos `ENUM`: `task_status`, `memory_visibility`, `memory_domain_type`.
- [x] Definir modelos: `Entity` (UUID, rol, metadatos), `Task` (UUID, FK, status, payload) y `Event` (UUID, FK, content, domain, visibility, consolidated).
- [x] Implementar índices de rendimiento: `idx_tasks_status` y `idx_events_owner`.
- [x] Generar y aplicar la migración inicial unificada.
- [x] **[Testing]** Crear un script o test (`pytest`) de *health check* que verifique la conexión asíncrona a la base de datos y valide que las tablas existen.

**Criterios de Aceptación**
- `alembic upgrade head` levanta el esquema sin excepciones.
- El test de *health check* asíncrono pasa en verde, conectándose al contenedor de PostgreSQL e insertando/borrando un registro de prueba de forma exitosa.

---

### Issue 2.2: Inicialización y Validación de Qdrant
**Contexto**
Validación de la infraestructura del motor vectorial mediante el cliente asíncrono oficial, asegurando la inyección estricta de UUIDs.

**Tareas**
- [x] Configurar la conexión del cliente asíncrono de Qdrant.
- [x] Implementar función de inicialización para la colección `core_memory` (dimensiones y métrica Coseno).
- [x] **[Testing]** Crear un *fixture* o test unitario que levante el cliente, cree una colección temporal, inserte un vector con UUID de Python, ejecute una búsqueda de similitud y destruya la colección.

**Criterios de Aceptación**
- La conexión asíncrona con el contenedor de Docker no sufre *timeouts*.
- El test unitario pasa en verde, demostrando compatibilidad total entre el UUID generado en la lógica de negocio y el identificador de punto de Qdrant.

---

### Issue 2.3: Motor Semántico y Capa de Repositorios
**Contexto**
Aislamiento de la complejidad de las consultas distribuidas a través del patrón repositorio.

**Tareas**
- [x] Implementar `RelationalRepository` (CRUD asíncrono sobre `Entity`, `Task`, `Event`).
- [x] Implementar `VectorRepository` (Upsert/Search sobre Qdrant con control de integridad de UUID).
- [ ] Implementar método unificado de búsqueda híbrida.
- [x] **[Testing]** Crear un test de integración (End-to-End de la capa de datos).

**Criterios de Aceptación**
- Las operaciones SQL y vectoriales no existen fuera de los repositorios.
- El test de integración pasa en verde: crea una `Entity`, inserta un `Event`, sincroniza su embedding en Qdrant (mismo UUID) y una búsqueda híbrida recupera el objeto relacional completo sin fallos de *mapping*.

### Issue 2.4: Orquestador Híbrido (Memory Manager) y Control Transaccional

**Contexto**
El actual `VectorMemoryManager` heredado viola el principio de responsabilidad única (SRP) al mezclar llamadas a LLMs, lógica de negocio y acceso directo a Qdrant. Este issue aborda su destrucción y reescritura como un Orquestador Híbrido puro. Su función será coordinar el flujo de datos entre el `RelationalRepository`, el `VectorRepository` y el proxy de LiteLLM, garantizando la consistencia transaccional (rollback relacional si falla la inyección vectorial) y ejecutando el patrón *scatter-gather* para las búsquedas semánticas.

**Tareas**
- [x] Purgar el código *legacy* de `src/managers/memory_manager.py`, eliminando cualquier instancia directa de `AsyncQdrantClient`.
- [x] Implementar inyección de dependencias en el constructor del nuevo `MemoryManager` para recibir la sesión de Postgres (`AsyncSession`) y los repositorios (`EntityRepository`, `EventRepository` o `MessageRepository`, y `VectorRepository`).
- [x] Aislar la llamada asíncrona al proxy LiteLLM (endpoint `/v1/embeddings`) en un método privado puramente funcional.
- [x] **Implementar Flujo Ingesta:** Método `add_memory` que ejecute: Inserción en Postgres -> `flush()` -> Generación de Embedding -> Inserción en Qdrant -> `commit()` (o `rollback()` en caso de fallo vectorial).
- [x] **Implementar Flujo Búsqueda:** Método `search_memory` que ejecute: Generación de Embedding de la query -> Búsqueda en Qdrant (obtiene UUIDs) -> Hidratación de entidades desde Postgres usando la cláusula `IN` con los UUIDs recuperados.

**Criterios de Aceptación (DoD)**
- El `MemoryManager` no contiene ninguna consulta SQL directa ni invoca esquemas propietarios de Qdrant (como `models.Filter`), delegando el 100% de la persistencia a los repositorios.
- Un fallo forzado en la inyección a Qdrant cancela la transacción entera y no deja registros fantasma en PostgreSQL.
- La hidratación de la búsqueda devuelve objetos de dominio completos (ej. `Event` o `Message`), no vectores crudos.

**Testing Requerido**
- [x] **Test de Integración (`test_hybrid_manager.py`):** 
    - Validar el flujo *Happy Path* (Inserción + Recuperación Hidratada).
    - **Test Crítico de Rollback:** *Mockear* un fallo en el `VectorRepository.upsert` y asertar que el UUID generado en Postgres no existe en la base de datos relacional tras la ejecución (comprobando que el `rollback()` hizo su trabajo).


# Post-Mortem Técnico: Epic 2 - Capa de Memoria Unificada

## Resumen Ejecutivo
La **Epic 2** establece la capa de persistencia dual para `coreAI`, desacoplando completamente el almacenamiento relacional (PostgreSQL) del vectorial (Qdrant). Se eliminó el monolito `VectorMemoryManager` y se implementó una arquitectura basada en el patrón **Repository** y un **Orquestador Transaccional** (`HybridMemoryManager`) para garantizar consistencia fuerte (ACID) y aislamiento de responsabilidades.

---

## 1. Decisiones Arquitectónicas Clave

### A. Desacoplamiento mediante Repositorios Puros
Se separaron las responsabilidades de persistencia en componentes especializados y agnósticos del dominio:
- **`BaseRepository` (SQLAlchemy 2.0 Async):** Gestiona operaciones atómicas puras en PostgreSQL. Se programó bajo la regla estricta de **no ejecutar `commit()` en el repositorio**, limitándose a emitir `flush()` para sincronizar estados y propagar UUIDs autogenerados.
- **`VectorRepository` (Qdrant Client v1.19.0+):** Abstrae la comunicación con Qdrant. Operaciones acopladas a colecciones fijas, eliminando el antipatrón de inicialización de esquemas en tiempo de escritura (*schema-on-write*).

### B. Creación del Issue 2.4: Orquestador Híbrido (`HybridMemoryManager`)
Para evitar violaciones del Principio de Responsabilidad Única (SRP), se extrajo la lógica de coordinación de alto nivel a un mánager dedicado:
- **Inyección Transaccional:** Coordina la escritura dual. Ejecuta la inserción relacional (`flush()`), genera el vector (delegado a LiteLLM), inyecta en Qdrant y consolida con `commit()`. Si falla cualquier componente vectorial, emite un `rollback()` automático para evitar registros relacionales huérfanos.
- **Patrón Scatter-Gather (Búsqueda):** Resuelve la consulta semántica vectorizando el texto, obteniendo UUIDs ordenados por similitud en Qdrant, y ejecutando una hidratación relacional masiva en PostgreSQL mediante una cláusula optimizada `WHERE id IN (...)`.

---

## 2. Lecciones Aprendidas y Resolución de Incidencias Técnicas

### A. Gestión de Bucles de Eventos en Pytest (`asyncio`)
- **Problema:** El uso de un motor global de SQLAlchemy inicializado a nivel de módulo provocaba colisiones de descriptores de red atados al *event loop* principal, rompiendo los tests asíncronos (`RuntimeError: attached to a different loop`).
- **Solución:** Se implementó la destrucción explícita del pool de conexiones globales (`await engine.dispose()`) al inicio de las *fixtures* de sesión, garantizando que las conexiones se sincronicen con el *loop* dinámico de ejecución de pytest.

### B. Mapeo Tipado de Enums en SQLAlchemy 2.0 / PostgreSQL
- **Problema:** SQLAlchemy enviaba por defecto el nombre del atributo del Enum en mayúsculas (ej. `SYSTEM`), provocando un fallo de tipo estricto en PostgreSQL (`InvalidTextRepresentationError`) que esperaba valores en minúsculas.
- **Solución:** Se blindaron todas las columnas de tipo `Enum` en los modelos ORM configurando explícitamente el parámetro de extracción del valor subyacente para que coincida con las restricciones de la base de datos.

### C. Prevención de `MissingGreenlet` tras un `rollback()`
- **Problema:** Tras forzar una excepción para validar el mecanismo de `rollback()`, SQLAlchemy invalida los objetos asociados a la sesión. Intentar acceder de forma perezosa (*lazy-loading*) a atributos de dichos objetos (`base_entity.id`) en código síncrono rompía el entorno asíncrono.
- **Solución:** Extracción preventiva de identificadores críticos a variables nativas inmutables de Python antes de los bloques de ejecución de riesgo para evitar accesos al ORM en estado expirado.