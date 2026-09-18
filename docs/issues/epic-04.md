# Epic 4: [coreAI] Córtex Asíncrono (Motor de Eventos Relacional)

## Contexto del Epic
Desarrollo e implementación del sistema de procesamiento en segundo plano (Event-Driven) para la asimilación autónoma de contexto. Para garantizar la consistencia fuerte (ACID) y evitar problemas de sincronización en estados distribuidos, se prescinde de brokers en memoria de terceros (ej. Redis/RabbitMQ). La cola de mensajes se implementará de forma nativa sobre PostgreSQL utilizando bloqueos transaccionales a nivel de fila (`SELECT ... FOR UPDATE SKIP LOCKED`), convirtiendo la tabla `tasks` en un motor de orquestación atómico y completamente resiliente a caídas de contenedores.

---

### Issue 4.1: Repositorio de Colas y Bloqueos Atómicos (PostgreSQL)
**Contexto**
La capa de acceso a datos debe soportar la extracción segura y concurrente de tareas pendientes. Es imperativo garantizar que un mismo evento no sea consumido por dos workers simultáneamente, asegurando el principio de aislamiento transaccional.

**Tareas**
- [x] Implementar el método `fetch_next_task()` dentro del repositorio de tareas (`TaskRepository`). *(Nota: En el código actual se llama `claim_next_task()`)*.
- [x] Construir la consulta aplicando la cláusula `FOR UPDATE SKIP LOCKED` para capturar atómicamente el registro más antiguo con `status = 'PENDING'`.
- [x] **[Testing]** Test de concurrencia: Levantar dos sesiones asíncronas en paralelo e intentar extraer tareas simultáneamente. Asertar que cada sesión obtiene un UUID distinto, confirmando la ausencia de condiciones de carrera.

**Criterios de Aceptación**
- El repositorio extrae y bloquea las tareas de forma atómica a nivel de base de datos.
- Si la transacción del worker se aborta (ej. caída crítica del contenedor), el bloqueo se libera automáticamente mediante rollback y la tarea vuelve a estar disponible.

---

### Issue 4.2: Motor del Worker Asíncrono
**Contexto**
Creación del bucle de ejecución que operará en segundo plano, consumiendo la cola relacional mediante un *polling* eficiente sin bloquear el event loop principal del servidor MCP/FastAPI.

**Tareas**
- [x] Desarrollar la clase `WorkerDaemon` con un ciclo de vida continuo regulado por pausas asíncronas (`asyncio.sleep()`).
- [x] Implementar un enrutador (`TaskRouter`) para mapear el identificador de la tarea dentro del `payload` con su función controladora correspondiente.
- [x] Configurar un sistema de captura de excepciones globales en el bucle principal para evitar que un error no manejado detenga el demonio.
- [x] **[Testing]** Test de integración end-to-end: Inyectar una tarea sintética en PostgreSQL, arrancar una iteración del `WorkerDaemon` y asertar que la tarea es procesada y su estado pasa a `COMPLETED`.

**Criterios de Aceptación**
- El worker consume las tareas de forma silenciosa y entra en estado de latencia controlada cuando la cola está vacía, minimizando el consumo de CPU.

---

### Issue 4.3: Implementación de Handlers Cognitivos
**Contexto**
Con la fontanería de la cola operativa, se requiere implementar los controladores (handlers) que ejecuten la lógica de negocio real de asimilación de memoria de [coreAI].

**Tareas**
- [ ] Desarrollar el controlador de **Vectorización de Eventos**: Leer el contenido de la tarea, generar el embedding a través del `LLMClient` y sincronizarlo en Qdrant.
- [ ] Desarrollar el controlador de **Consolidación de Memoria**: Tarea de mantenimiento periódico que agrupa memorias episódicas antiguas, las resume mediante el LLM y las reinyecta como contexto denso.
- [ ] **[Testing]** Tests unitarios que aíslen cada controlador mediante mocks, validando exclusivamente su lógica algorítmica sin necesidad de interactuar con la cola.

**Criterios de Aceptación**
- Los handlers operan de forma determinista, delegando todas las operaciones de persistencia y comunicación externa a los Managers y Repositorios ya existentes.

---

### Issue 4.4: Resiliencia, Retries y Dead Letter Queue (DLQ)
**Contexto**
La red y los motores de inferencia local están sujetos a fallos transitorios. El sistema de colas debe ser lo suficientemente robusto para gestionar errores sin bloquear el flujo de otras tareas.

**Tareas**
- [ ] Añadir una columna `retry_count` (Integer, default 0) al modelo ORM de `Task` mediante una nueva migración de Alembic.
- [ ] Implementar un mecanismo de retardo exponencial (*Exponential Backoff*) para aquellas tareas que devuelven errores recuperables.
- [ ] Implementar la transición al estado `FAILED` (funcionalidad de Dead Letter Queue lógica) cuando una tarea supera el número máximo de reintentos (ej. `max_retries = 3`).
- [ ] **[Testing]** Simulación de caída del proxy LiteLLM durante el procesamiento de una tarea. Validar que la tarea incrementa su contador de reintentos y, finalmente, queda aparcada en estado `FAILED` sin paralizar el sistema.

**Criterios de Aceptación**
- Ninguna tarea envenenada o fallida detiene el procesamiento del resto de la cola. El historial de errores y el `payload` original permanecen intactos en la tabla para auditoría o reinyección manual.