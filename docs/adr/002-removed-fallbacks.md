# ADR-002: Amputación de Fallbacks Externos Automáticos en el MVP

## Status
Accepted

## Context
La especificación original de `[coreAI]` (PRD, Punto 5 y Epic 3, Issue 3.2) definía un enrutamiento de inferencia a través de LiteLLM que incluía un mecanismo de *fallback* automático hacia APIs externas (ej. Gemini, OpenAI) en caso de fallo del hardware local. 

Dado que el motor cognitivo de `[coreAI]` opera bajo un paradigma asíncrono (Event-Driven Workers), el procesamiento de colas ocurre en segundo plano. Si el motor local (Infinity/LLM local) colapsa, un *fallback* automático provocaría que los workers continuaran drenando la cola de eventos disparando masivamente a modelos de pago. Esto introduce un riesgo de fuga financiera inaceptable y oculta el fallo real de la infraestructura local bajo una falsa sensación de alta disponibilidad. 

La prioridad del sistema es la soberanía absoluta de los datos y el determinismo, no mantener el sistema vivo a costa de la tarjeta de crédito.

## Decision
Se extrae completamente la conexión a APIs externas y el mecanismo de *fallback* automático del alcance del MVP. 

LiteLLM se configurará como un enrutador estrictamente determinista que apuntará de forma exclusiva a los nodos físicos locales (`infinity` y equivalentes). Si el nodo local no responde, LiteLLM devolverá un error HTTP 500 puro. Los *workers* asíncronos deberán capturar esta excepción y gestionar el fallo mediante retenciones lógicas (*Exponential Backoff* o *Dead Letter Queues*), deteniendo el procesamiento hasta que la infraestructura física sea restaurada.

La integración de modelos externos se traslada a una fase post-MVP, y se diseñará exclusivamente para enrutamientos deliberados (tareas de razonamiento complejo asignadas a un modelo concreto), nunca como un salvavidas automatizado.

## Consequences
* **Positive:** Soberanía financiera garantizada. Riesgo cero de costes descontrolados por procesamiento en segundo plano.
* **Positive:** Transparencia de infraestructura. Los fallos del clúster físico detienen el sistema de forma ruidosa, obligando al administrador a solucionar el problema real.
* **Positive:** Reducción drástica de la entropía en la configuración de LiteLLM para el MVP, facilitando el foco en la telemetría local.
* **Negative:** Pérdida total de Alta Disponibilidad (HA) para el servicio de inferencia. Si el metal cae, el sistema cognitivo se paraliza por completo.