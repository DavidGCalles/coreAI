# Epic 2: [coreAI] Capa de Memoria Unificada (Data Layer)

## Contexto del Epic
Persistencia determinista y semántica de estado sin fragmentación. Este epic abarca la configuración de PostgreSQL y Qdrant, la integración de SQLAlchemy y Alembic, la definición de los esquemas fundacionales y la creación del patrón repositorio, asegurando la cobertura de pruebas de conectividad.

---

### Issue 2.1: Motor Relacional, ORM y Esquema Base
**Contexto**
Configuración del motor relacional en PostgreSQL (SQLAlchemy + asyncpg) e inicialización del sistema de migraciones (Alembic).

**Tareas**
- [ ] Configurar motor asíncrono de SQLAlchemy (`asyncpg`) e inicializar Alembic (`alembic init -t async`).
- [ ] Migrar tipos `ENUM`: `task_status`, `memory_visibility`, `memory_domain_type`.
- [ ] Definir modelos: `Entity` (UUID, rol, metadatos), `Task` (UUID, FK, status, payload) y `Event` (UUID, FK, content, domain, visibility, consolidated).
- [ ] Implementar índices de rendimiento: `idx_tasks_status` y `idx_events_owner`.
- [ ] Generar y aplicar la migración inicial unificada.
- [ ] **[Testing]** Crear un script o test (`pytest`) de *health check* que verifique la conexión asíncrona a la base de datos y valide que las tablas existen.

**Criterios de Aceptación**
- `alembic upgrade head` levanta el esquema sin excepciones.
- El test de *health check* asíncrono pasa en verde, conectándose al contenedor de PostgreSQL e insertando/borrando un registro de prueba de forma exitosa.

---

### Issue 2.2: Inicialización y Validación de Qdrant
**Contexto**
Validación de la infraestructura del motor vectorial mediante el cliente asíncrono oficial, asegurando la inyección estricta de UUIDs.

**Tareas**
- [ ] Configurar la conexión del cliente asíncrono de Qdrant.
- [ ] Implementar función de inicialización para la colección `core_memory` (dimensiones y métrica Coseno).
- [ ] **[Testing]** Crear un *fixture* o test unitario que levante el cliente, cree una colección temporal, inserte un vector con UUID de Python, ejecute una búsqueda de similitud y destruya la colección.

**Criterios de Aceptación**
- La conexión asíncrona con el contenedor de Docker no sufre *timeouts*.
- El test unitario pasa en verde, demostrando compatibilidad total entre el UUID generado en la lógica de negocio y el identificador de punto de Qdrant.

---

### Issue 2.3: Motor Semántico y Capa de Repositorios
**Contexto**
Aislamiento de la complejidad de las consultas distribuidas a través del patrón repositorio.

**Tareas**
- [ ] Implementar `RelationalRepository` (CRUD asíncrono sobre `Entity`, `Task`, `Event`).
- [ ] Implementar `VectorRepository` (Upsert/Search sobre Qdrant con control de integridad de UUID).
- [ ] Implementar método unificado de búsqueda híbrida.
- [ ] **[Testing]** Crear un test de integración (End-to-End de la capa de datos).

**Criterios de Aceptación**
- Las operaciones SQL y vectoriales no existen fuera de los repositorios.
- El test de integración pasa en verde: crea una `Entity`, inserta un `Event`, sincroniza su embedding en Qdrant (mismo UUID) y una búsqueda híbrida recupera el objeto relacional completo sin fallos de *mapping*.