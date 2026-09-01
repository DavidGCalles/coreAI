# Documento de Visión: [coreAI]

## 1. Naturaleza y Paradigma
[coreAI] no es una aplicación; es infraestructura soberana. Se concibe como la capa base transaccional y cognitiva para cualquier ecosistema de automatizaciones, agentes o interfaces futuras. Su propósito es actuar como el motor de verdad inmutable (Source of Truth) de un sistema, aislando la complejidad del almacenamiento híbrido, el enrutamiento de inferencia y el procesamiento asíncrono en un núcleo hermético.

## 2. Soberanía y Agnosticismo
El sistema prioriza el control absoluto de los datos y del ciclo de ejecución, rechazando dependencias críticas externas.
*   **Local-First por Diseño:** Arquitectado para desplegarse y operar con máxima eficiencia en infraestructuras propias y hardware dedicado, reteniendo los datos sensibles y las tareas de inferencia pesadas dentro del perímetro local.
*   **Agnosticismo de Interfaz:** [coreAI] es estrictamente *headless*. Ignora activamente cómo se consumen sus datos o quién los solicita, delegando la presentación, la orquestación de enjambres y la interacción a clientes externos bajo reglas estrictas de autorización.
*   **Agnosticismo de Modelo:** La cognición del sistema está totalmente desacoplada del proveedor de inferencia. A través de un proxy integrado, garantiza la supervivencia del sistema frente a la obsolescencia, caídas de servicio o cambios de políticas de cualquier LLM externo.

## 3. Arquitectura de Memoria Unificada
La persistencia trasciende el concepto de base de datos tradicional para convertirse en un motor de resolución de contexto temporal y relacional.
*   **Determinismo Estructural:** Una base de datos relacional robusta (PostgreSQL) actúa como el ancla de la realidad del sistema. Garantiza la inmutabilidad del registro de eventos, la integridad referencial y la topología de las entidades.
*   **Abstracción Semántica:** La capa vectorial opera en estricta subordinación a la relacional. Proporciona recuperación por similitud y expansión de contexto, pero cada vector es un apéndice de un registro determinista, erradicando la fragmentación de la verdad.

## 4. Estandarización de Capacidades (Protocolo MCP)
El sistema rechaza el acoplamiento y las integraciones *ad-hoc*. Cualquier lectura de sensores, escritura en sistemas de archivos o ejecución de código de terceros se expone al núcleo exclusivamente a través del estándar Model Context Protocol (MCP). [coreAI] actúa como un registro de herramientas dinámico, permitiendo escalar las capacidades físicas y lógicas del sistema sin alterar su código fundacional.

## 5. Córtex Autónomo (Event-Driven)
[coreAI] abandona la limitación del modelo reactivo (petición-respuesta). Implementa un bus de eventos y consumidores en segundo plano que permiten al sistema trabajar en la sombra: consolida y comprime memorias episódicas, audita inconsistencias en el grafo relacional y pre-computa contextos en horas valle. El sistema estructura su propio estado aunque no reciba estímulos externos.