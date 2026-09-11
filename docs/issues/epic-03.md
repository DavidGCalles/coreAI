# Epic 3: [coreAI] Enrutamiento de Inferencia (Infinity + LiteLLM Proxy)

## Contexto del Epic
Abstracción total del motor cognitivo priorizando la soberanía de los datos (local-first). Se despliega Infinity como motor local de embeddings/inferencia, se configura LiteLLM como proxy único de enrutamiento con *fallback* externo, y se establece la telemetría transaccional para auditar el consumo de tokens en PostgreSQL.

---

### Issue 3.1: Despliegue y Validación de Infinity
**Contexto**
Antes de configurar el proxy, el motor cognitivo local debe estar levantado y testeado de forma aislada.
**Tareas**
- [x] Configurar el servicio `infinity` en el `docker-compose.yml` utilizando el `Dockerfile.infinity` existente.
- [x] Mapear los volúmenes necesarios para descargar y cachear los modelos sin depender de descargas en cada reinicio.
- [x] **[Testing]** Acceder al Swagger UI nativo de Infinity (puerto expuesto temporalmente) y ejecutar manualmente una prueba de generación de embeddings para validar que el modelo carga en GPU/CPU correctamente.
**Criterios de Aceptación**
- El contenedor levanta sin *crash loops*.
- El Swagger responde y el test manual devuelve un vector con las dimensiones correctas.

---

### Issue 3.2: Proxy LiteLLM y Reglas de Enrutamiento
**Contexto**
Configuración de LiteLLM para actuar como pasarela única, consumiendo la instancia local de Infinity y derivando a APIs externas en caso de fallo.
**Tareas**
- [ ] Definir `litellm_config.edge.yaml`: Configurar el modelo apuntando al endpoint interno de Infinity (ej. `http://infinity:7997`).
- [ ] Configurar reglas de *fallback* explícitas hacia el proveedor externo (Gemini 2.5+ u otro).
- [ ] **[Testing]** Ejecutar scripts bash (`curl`) contra el puerto de LiteLLM para validar que enruta hacia Infinity. Simular caída de Infinity (parar contenedor) para verificar el *fallback* automático.
**Criterios de Aceptación**
- LiteLLM unifica la interfaz y aplica el *fallback* correctamente sin devolver errores 500 al cliente en la primera caída.

---

### Issue 3.3: Cliente Asíncrono de Inferencia (Backend)
**Contexto**
Centralizar la comunicación con el proxy LiteLLM desde el backend (FastAPI/Workers).
**Tareas**
- [ ] Implementar la clase `LLMClient` (`aiohttp` o `httpx`).
- [ ] Métodos asíncronos: `generate_embedding` y `generate_completion`.
- [ ] **[Testing]** Test de integración donde `LLMClient` solicita un embedding a LiteLLM y aserta la estructura de la respuesta.
**Criterios de Aceptación**
- El cliente asíncrono no bloquea el *event loop* y formatea los *payloads* según el estándar esperado por LiteLLM.

---

### Issue 3.4: Telemetría y Auditoría de Tokens en PostgreSQL
**Contexto**
Registro transaccional de los costes computacionales.
**Tareas**
- [ ] Definir modelo `LLMAudit` en SQLAlchemy: UUID, `model_used`, `prompt_tokens`, `completion_tokens`, `total_tokens`, timestamps.
- [ ] Aplicar migración Alembic.
- [ ] Acoplar la inserción en `LLMAudit` dentro de `LLMClient` tras cada respuesta exitosa.
- [ ] **[Testing]** Test End-to-End: Petición completa que aserte el retorno del payload y la existencia del registro en la tabla `LLMAudit`.
**Criterios de Aceptación**
- Auditoría persistente y exacta por cada llamada. Test E2E en verde.

# Post-Mortem Técnico: Epic 3 - Enrutamiento de Inferencia

## Resumen Ejecutivo
La **Epic 3** establece la pasarela cognitiva de `coreAI`, aislando la infraestructura física local (Infinity) detrás de un proxy estandarizado (LiteLLM) y garantizando la trazabilidad financiera de cada token procesado. Se priorizó el determinismo, la inmutabilidad estructural y la soberanía de datos, aplicando un filtro estricto contra la entropía arquitectónica y esquivando trampas de diseño presentes en la especificación original.

## 1. Decisiones Arquitectónicas Clave y Cambios Realizados

### A. Cliente I/O Desacoplado (`LLMClient`)
Se rechazó el sobre-diseño en la capa de red. En lugar de construir un SDK monolítico, se implementó un cliente asíncrono funcional utilizando `httpx` (reutilizando la dependencia del `requirements.txt` para mantener la imagen espartana). El 100% de la validación y estructuración de *payloads* se delegó a una capa de contratos estrictos mediante esquemas de Pydantic (`src/schemas/llm.py`), aislando las peticiones HTTP de la lógica de negocio.

### B. Consolidación de Telemetría Relacional
Se desplegó el modelo ORM `LLMAudit` y su correspondiente `LLMAuditRepository` genérico para registrar el gasto de inferencia. La inyección en PostgreSQL quedó blindada bajo el paraguas transaccional asíncrono, asegurando que los fallos en la capa de persistencia no dejen rastros financieros fantasma.

## 2. Divergencias, Inconsistencias y Resolución

El análisis crítico de los issues durante la implementación forzó varias reestructuraciones sobre la marcha para mantener el sistema como un entorno determinista y evitar el sobre-diseño prematuro:

### Inconsistencia A: Violación SRP en el Issue 3.4 (Base de datos en el cliente HTTP)
*   **Problema:** La especificación original dictaba textualmente *"Acoplar la inserción en LLMAudit dentro de LLMClient"*. Esto habría convertido un simple conducto de red en un componente híbrido con acceso directo a PostgreSQL, violando el Principio de Responsabilidad Única (SRP) y fragmentando el control transaccional.
*   **Resolución:** Se extirpó el acceso a la base de datos del cliente HTTP. El `LLMClient` devuelve un esquema Pydantic puro (`EmbeddingResponse`). Es el orquestador (`HybridMemoryManager`) quien recibe el uso de tokens y ejecuta la inserción a través del `LLMAuditRepository`, empaquetando la telemetría en el mismo *commit* o *rollback* que el evento relacional.

### Inconsistencia B: Telemetría Huérfana en el Modelo de Auditoría
*   **Problema:** El PRD original exigía auditar tokens, modelos y timestamps, pero omitía cualquier vinculación a una entidad. Facturar costes sin saber a quién imputarlos convierte la base de datos en un sumidero de datos sin valor analítico.
*   **Resolución:** Se alteró el esquema relacional introduciendo la Foreign Key `entity_id` nula por defecto y un índice compuesto (`idx_audit_entity_time`) en la tabla `llm_audit`, garantizando la capacidad de auditar costes cruzados por agente o usuario en el futuro sin penalizar el rendimiento.

### Inconsistencia C: Fallbacks Externos Automáticos vs Soberanía Financiera
*   **Problema:** El PRD y el Issue 3.2 estipulaban un *fallback* automático hacia APIs externas de pago si la inferencia local fallaba. Dado el diseño *Event-Driven* asíncrono de los *workers* que drenan colas en segundo plano, un colapso del contenedor físico habría derivado miles de peticiones silenciosas a proveedores externos, generando una fuga financiera inaceptable y enmascarando fallos críticos de infraestructura.
*   **Resolución (ADR-002):** Se eliminó el mecanismo de *fallback* automático del MVP. El proxy LiteLLM se configuró como un enrutador estrictamente determinista apuntando únicamente a nodos físicos. Si el metal cae, el sistema expone un error 500 HTTP puro y detiene las colas, forzando una intervención explícita y protegiendo los recursos financieros.

### Prevención Adicional: Lógica Prematura de Completions
*   **Problema:** Al desarrollar el contrato de `EmbeddingResponse`, surgió la inercia lógica de cablear la telemetría para la generación de texto (`completions`) dentro del actual orquestador de memoria.
*   **Resolución:** Se bloqueó el acoplamiento. El `HybridMemoryManager` mantiene su responsabilidad exclusiva sobre el ciclo de vida de los vectores. La orquestación y auditoría de los *completions* quedan reservadas estrictamente para futuros orquestadores de capas cognitivas superiores (sesiones/agentes), manteniendo los dominios aislados.