# Epic 3: [coreAI] Enrutamiento de Inferencia (Infinity + LiteLLM Proxy)

## Contexto del Epic
Abstracción total del motor cognitivo priorizando la soberanía de los datos (local-first). Se despliega Infinity como motor local de embeddings/inferencia, se configura LiteLLM como proxy único de enrutamiento con *fallback* externo, y se establece la telemetría transaccional para auditar el consumo de tokens en PostgreSQL.

---

### Issue 3.1: Despliegue y Validación de Infinity
**Contexto**
Antes de configurar el proxy, el motor cognitivo local debe estar levantado y testeado de forma aislada.
**Tareas**
- [ ] Configurar el servicio `infinity` en el `docker-compose.yml` utilizando el `Dockerfile.infinity` existente.
- [ ] Mapear los volúmenes necesarios para descargar y cachear los modelos sin depender de descargas en cada reinicio.
- [ ] **[Testing]** Acceder al Swagger UI nativo de Infinity (puerto expuesto temporalmente) y ejecutar manualmente una prueba de generación de embeddings para validar que el modelo carga en GPU/CPU correctamente.
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