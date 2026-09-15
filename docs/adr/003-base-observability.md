# ADR 003: Integración de Capa Base de Observabilidad y Centralización de Logs

## Status
Accepted

## Context and Problem Statement
Antes de iniciar el desarrollo del Córtex Asíncrono (Epic 4) y la inyección de carga mediante workers de mensajería, el sistema requiere tanto visibilidad sobre el rendimiento del pipeline de inferencia (métricas) como capacidad de depuración profunda (logs). Depender de la inspección manual mediante CLI (`docker logs`) introducira demasiada fricción al escalar servicios asíncronos. Sin embargo, implementar una suite de telemetría completa (ej. parseo estructurado de anomalías para procesamiento de IA) representa un riesgo severo de *scope creep* y desviación del MVP. ¿Cómo garantizamos la observabilidad y depuración de los workers sin incurrir en complejidad arquitectónica prematura?

## Decision Drivers
*   **Depuración centralizada:** Necesidad imperativa de buscar y correlacionar logs de múltiples contenedores desde un único punto para el Epic 4.
*   **Aislamiento del código base:** Cero instrumentación adicional en el código Python de `[coreAI]`; se deben aprovechar los flujos `stdout`/`stderr` y endpoints `/metrics` nativos.
*   **Reversibilidad:** La solución inicial debe permitir una migración futura y sin fricción hacia una ingestión más compleja (ej. Grafana Alloy).
*   **Prevención de rabbit-holes:** Implementación acotada en tiempo, priorizando "crudo y funcional" sobre "estructurado y bonito".

## Considered Options
1.  **Prometheus + Grafana (Solo Métricas):** Scraping a LiteLLM e Infinity. Rechazado por carecer de utilidad real para la depuración de los workers del Epic 4.
2.  **Prometheus + Loki + Grafana Alloy (Suite Completa):** Solución integral de telemetría con parseo y enrutamiento inteligente. Rechazado por su excesiva carga de configuración para la v1.0.
3.  **Prometheus + Loki vía Docker Logging Driver + Grafana:** Ingestión directa de logs crudos del daemon de Docker hacia Loki, combinada con scraping de métricas básico.

## Decision Outcome
Chosen option: **Option 3 (Prometheus + Loki vía Docker Logging Driver + Grafana)**.

Se implementará una arquitectura de observabilidad que combina series temporales y logs crudos. Prometheus recolectará las métricas de inferencia. Loki actuará como agregador de logs recibiendo el flujo directamente desde el plugin de logging nativo de Docker (o driver equivalente). Grafana centralizará ambos orígenes de datos.

Las restricciones de esta implementación son absolutas para la v1.0:
*   La ingestión de logs a Loki será plana. No se implementarán agentes intermediarios (Grafana Alloy) para transformar logs o inyectar labels estructurados complejos.
*   El código base seguirá escupiendo al `stdout` sin conocimiento de la infraestructura de telemetría.
*   Cualquier pivotaje hacia pipelines de recolección avanzados queda explícitamente bloqueado hasta la v1.1.

### Positive Consequences
*   Depuración unificada y búsqueda vía LogQL en Grafana lista para enfrentar los fallos asíncronos del Epic 4.
*   Decisión altamente reversible: transicionar a Grafana Alloy en el futuro solo requerirá cambiar el *logging driver* en el `docker-compose.yml` y levantar el contenedor del agente.
*   Cero sobrecarga de desarrollo en los microservicios.

### Negative Consequences
*   Requiere instalar y habilitar el plugin de Loki en el daemon de Docker del host (Proxmox node/VM).
*   Las queries en LogQL iniciales deberán depender más de expresiones regulares (regex) al no contar con un parseo JSON estructurado en la fase de ingestión.
*   Los contenedores configurados con el driver de Loki no mostrarán salida mediante el comando clásico `docker logs`, centralizando la dependencia en Grafana.



# ISSUES

### [ADR-003-001] Infraestructura: Habilitación del Plugin de Loki (Host)

**Descripción:**
Instalar y configurar el plugin oficial `loki-docker-driver` en el daemon de Docker del host (Proxmox) para permitir el enrutamiento nativo de logs sin agentes intermediarios.

**DoD (Definition of Done):**
* Plugin instalado y habilitado a nivel global en el daemon.
* El daemon de Docker ha sido reiniciado y opera sin errores.

**Tests / Validación:**
* Ejecutar `docker plugin ls` y verificar que el plugin `loki` aparece con el estado `true` (activo).
* Desplegar un contenedor temporal de prueba con el driver de loki (`docker run --log-driver=loki ... alpine echo "test"`) y comprobar que finaliza correctamente.

### [ADR-003-002] Orquestación: Inyección de Servicios en docker-compose.yml

**Descripción:**
Expandir el stack actual (`docker-compose.yml`) incluyendo los servicios `prometheus`, `loki` y `grafana` dentro de la red interna (bridge). Configurar el `logging` de los servicios existentes (ej. `litellm`, `infinity`) para apuntar al socket de Loki usando el modo `non-blocking`.

**DoD (Definition of Done):**
* Definiciones de los 3 nuevos servicios integradas en el YAML.
* Volúmenes persistentes mapeados para Prometheus (métricas) y Grafana (configuración/dashboards).
* Configuración del *logging driver* inyectada en los servicios core sin alterar su lógica interna.

**Tests / Validación:**
* El comando `docker-compose config` devuelve una configuración YAML validada y sin advertencias.
* Al hacer `docker-compose up -d`, todos los contenedores levantan con estado `healthy` o `running`.
* Ejecutar `docker logs [nombre_contenedor_core]` debe fallar, confirmando que el driver estándar ha sido reemplazado por Loki.

### [ADR-003-003] Configuración: Recolección de Datos y Provisionamiento

**Descripción:**
Automatizar la conexión de telemetría creando el archivo `prometheus.yml` (con los *scrape jobs* de LiteLLM e Infinity) y los archivos YAML de aprovisionamiento de Grafana para que registre a Loki y Prometheus como *Data Sources* automáticamente en el arranque.

**DoD (Definition of Done):**
* Archivo `prometheus.yml` montado en el contenedor y configurado para barrer los puertos internos.
* Grafana inicia con los Data Sources ya configurados sin requerir configuración manual en la interfaz web.

**Tests / Validación:**
* Acceder al endpoint de Prometheus (`http://[IP]:9090/targets`) y verificar que los *targets* de LiteLLM e Infinity aparecen en estado `UP`.
* Acceder a Grafana (`http://[IP]:3000`), ir a Data Sources, y verificar que los tests de conexión para Loki y Prometheus devuelven un *Success* en verde.

### [ADR-003-004] Validación: Dashboards y Queries de LogQL

**Descripción:**
Importar (no crear desde cero) un dashboard genérico compatible para visualizar métricas, y confirmar la correlación de eventos lanzando tráfico sintético y buscando la traza de logs.

**DoD (Definition of Done):**
* Dashboard de métricas de inferencia cargado.
* Demostración empírica de ingesta de logs en tiempo real vía UI.
* Tiempo total invertido en este issue estrictamente inferior a 30 minutos.

**Tests / Validación:**
* Generar una petición cURL al proxy de LiteLLM simulando tráfico.
* Abrir la pestaña *Explore* en Grafana y ejecutar `{compose_service="litellm"}` en LogQL: el log de la petición debe aparecer.
* El dashboard importado debe reflejar el pico de carga o latencia asociado a la petición de prueba.