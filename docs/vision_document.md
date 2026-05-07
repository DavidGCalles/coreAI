# Vision Document: CoreAI - Infraestructura de Persistencia Contextual y Capa de Memoria Híbrida

## 1. Declaración de Intenciones
**CoreAI** es una pieza de infraestructura diseñada para resolver el problema de la volatilidad del contexto en sistemas de Inteligencia Artificial. Su objetivo principal es proporcionar un backend de memoria persistente que sea independiente de interfaces de usuario (IDE, chats) y modelos de lenguaje (LLMs).

El sistema asume que la inteligencia es un **commodity intercambiable**, mientras que el valor real reside en:
- La estructura del contexto
- Su veracidad
- Disponibilidad local

---

## 2. Genesis y Justificación Técnica
CoreAI nace de la abstracción de componentes de grado industrial previamente validados en entornos de automatización de alta fidelidad (ejemplo: lifeOS). Implementa un **stack de resiliencia** compuesto por:

| Component | Función Principal                          |
|-----------|--------------------------------------------|
| Gestión de Estado Relacional | Manejo de datos estructurados              |
| Memoria Vectorial            | Recuperación semántica                     |
| Orquestación Tareas         | Procesamiento en segundo plano             |

---

## 3. Arquitectura del Sistema ("El Stack Potente")

### A. Capa de Servicios Persistentes (El Core)
**Memoria Híbrida:**
- **PostgreSQL**: Gestión de hechos y metadatos estructurados
- **pgvector/Infinity**: Recuperación semántica avanzada

**Sincronización Asíncrona:**
- Implementación basada en ADR-014
- Delegación a trabajadores independientes para:
  - Ingestión de datos
  - Recalculo de embeddings
- Garantiza contexto actualizado sin afectar rendimiento principal

**Abstracción de Inferencia:**
- Centralización de peticiones mediante LiteLLM e Infinity
- Permite pivotar entre modelos locales de forma transparente

### B. Capa de Interfaz: Acceso vía MCP (SSE)
Diferencias clave frente a servidores MCP tradicionales:
| Característica       | Implementación CoreAI                          |
|----------------------|-----------------------------------------------|
| Ciclo de Vida        | Persistente (independiente del cliente)         |
| Soporte Multi-cliente| Un nodo sirve múltiples interfaces simultáneas  |
| Estado Persistente   | Contexto no se pierde al reiniciar UI          |

---

## 4. Definición de Capacidad Contextual
CoreAI estandariza cómo una IA "entiende" su entorno mediante:

### Conceptos Clave:
**Recursos (Fuentes de Verdad):**
- Esquemas de bases de datos
- Grafos de conocimiento
- Registros históricos
*Características:*
✔ Solo lectura
✔ Estructura determinista

**Herramientas (Acción y Descubrimiento):**
- Búsqueda semántica (RAG)
- Filtrado de registros
- Disparadores de re-indexación
*Funcionalidades:*
🔍 Exploración activa de información no estructurada
📊 Soporte para contextos complejos

---

## 5. Objetivos de Diseño (Key Results)

| Metric               | Descripción                                                                 |
|----------------------|-----------------------------------------------------------------------------|
| Resiliencia Local    | Operación 100% offline-first                                                |
| Latencia UI          | Actualización en segundo plano                                             |
| Portabilidad         | Instancia por propósito                                                    |

**Beneficios clave:**
✅ Eliminación de dependencias de API externas
✅ Privacidad absoluta del contexto
✅ Adaptabilidad a múltiples dominios (desarrollo, investigación legal, etc.)