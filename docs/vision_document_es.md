# Documento de Visión: [coreAI]

> **Estado de revisión (nov 2026):** alineado con la doctrina vigente del repo (ADR-001 a ADR-012). Los cambios frente a la versión fundacional, con sus razones, viven en los ADRs y en la sección 6 del PRD.

## 1. Naturaleza y Paradigma
[coreAI] no es una aplicación; es infraestructura soberana. Se concibe como la capa base transaccional y cognitiva para cualquier ecosistema de automatizaciones, agentes o interfaces futuras. Su propósito es actuar como el motor de verdad inmutable (Source of Truth) de un sistema, aislando la complejidad del almacenamiento híbrido, el enrutamiento de inferencia y el procesamiento asíncrono en un núcleo hermético. Es un **sustrato, no un cerebro**: el gateway enruta, no piensa; la orquestación interactiva vive en el cliente de borde; [coreAI] garantiza persistencia, memoria unificada y procesamiento de fondo.

## 2. Soberanía y Agnosticismo
El sistema prioriza el control absoluto de los datos y del ciclo de ejecución, rechazando dependencias críticas externas.
*   **Local-First por Diseño:** Arquitectado para desplegarse y operar con máxima eficiencia en infraestructuras propias y hardware dedicado, reteniendo los datos sensibles y las tareas de inferencia pesadas dentro del perímetro local.
*   **Agnosticismo de Interfaz:** [coreAI] es estrictamente *headless*. Ignora activamente cómo se consumen sus datos o quién los solicita, delegando la presentación, la orquestación de enjambres y la interacción a clientes externos bajo reglas estrictas de autorización.
*   **Agnosticismo de Modelo:** La cognición del sistema está totalmente desacoplada del proveedor de inferencia. A través de un proxy integrado, garantiza la supervivencia del sistema frente a la obsolescencia, caídas de servicio o cambios de políticas de cualquier LLM externo.

## 3. Arquitectura de Memoria Unificada
La persistencia trasciende el concepto de base de datos tradicional para convertirse en un motor de resolución de contexto temporal y relacional.
*   **Determinismo Estructural:** Una base de datos relacional robusta (PostgreSQL) actúa como el ancla de la realidad del sistema. Garantiza la inmutabilidad del registro de eventos, la integridad referencial y la topología de las entidades.
*   **Abstracción Semántica:** La capa vectorial (Qdrant) opera en estricta subordinación a la relacional. Proporciona recuperación por similitud y expansión de contexto, pero cada vector es un apéndice de un registro determinista, erradicando la fragmentación de la verdad.
*   **Identidad y Titularidad:** El sustrato sabe quién despacha: entidades declarativas gobernadas en el despliegue, actor de trazabilidad en cada tarea y titularidad por registro, con visibilidad respetada en el recall (ADR-012). El modelo de permisos completo (RBAC) queda explícitamente diferido, y consumirá esta identidad.

## 4. Estandarización de Capacidades (Protocolo MCP)
El sistema rechaza el acoplamiento y las integraciones *ad-hoc*. Toda lectura, escritura o consulta se expone al núcleo exclusivamente a través del estándar Model Context Protocol (MCP): una superficie fija de tools con contratos formales, validados en la frontera y publicados como resources (ADR-006). [coreAI] escala sus capacidades de forma aditiva, mediante revisión deliberada (ADR + PR), sin alterar su código fundacional. *(Cambio frente a la versión fundacional: el registro dinámico de herramientas en caliente quedó descartado a favor de la estabilidad del contrato que los inquilinos consumen.)*

## 5. Córtex Autónomo (Event-Driven)
[coreAI] abandona la limitación del modelo reactivo (petición-respuesta). Implementa un córtex asíncrono sobre cola relacional nativa (PostgreSQL con `SELECT ... FOR UPDATE SKIP LOCKED`, sin broker externo) con consumidores en segundo plano que permiten al sistema trabajar en la sombra: consolida y comprime memorias episódicas, corrige la metadata de lo ya almacenado sin re-embedding (ADR-009) y deja trazada la auditoría de cada intento en la DLQ. El sistema estructura su propio estado aunque no reciba estímulos externos. *(Cambio frente a la versión fundacional: el "bus de eventos" se materializó como cola nativa sobre PostgreSQL — consistencia fuerte y resiliencia sin una pieza más que operar.)*
