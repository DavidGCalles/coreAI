# 🟢 Must Have (Lo vital para que CoreAI exista)

Sin esto, no hay sistema; solo tenemos un script suelto.

## Infraestructura Base (Docker Compose)
Orquestación de PostgreSQL (con pgvector), Infinity (embeddings) y LiteLLM. Es el chasis mínimo.

## Servidor MCP vía SSE
Implementación de la interfaz MCP sobre HTTP/SSE para permitir que CoreAI sea un servicio persistente e independiente del IDE.

## Motor de Workers (ADR-014)
Sistema de colas para procesar la ingesta y el embedding en segundo plano, evitando bloqueos del servidor principal.

## Herramienta de Búsqueda Semántica
Una "Tool" MCP robusta que realice búsquedas vectoriales contra la base de datos local.

## Recurso de Esquema (Resource)
Un "Resource" MCP que exponga de forma determinista la estructura de la base de datos (relacional o documental) al modelo.

## Gestión de Hashing
Lógica para detectar cambios en archivos/entidades y evitar re-indexaciones innecesarias de lo que no ha cambiado.

---

# 🟡 Should Have (Lo que da potencia real al sistema)

Lo que diferencia a CoreAI de un plugin de RAG del montón:

## Watcher de Sistema de Archivos
Integración de un observador de eventos (`on_save`, `on_change`) que dispare automáticamente tareas a los Workers.

## Soporte Multimodal Inicial
Capacidad de ingesta no solo de texto plano, sino de estructuras jerárquicas (Markdown, JSON, esquemas SQL).

## CLI de Administración
Herramientas de línea de comandos para inicializar el sistema, purgar la base de datos o forzar una re-indexación masiva.

## Reranking Local
Integración de un modelo de re-ranking ligero para mejorar la precisión de los resultados del RAG antes de enviarlos al modelo.

---

# 🔵 Could Have (Esteroides y refinamiento)

Lo que hace que el sistema sea "sexy" y ultra-eficiente:

## Graph Memory
Implementación de una capa de grafos sobre Postgres para entender relaciones complejas entre entidades (no solo similitud semántica).

## Dashboard de Observabilidad
Una interfaz web mínima para ver el estado de los Workers, el uso de memoria de los vectores y los logs de las peticiones MCP.

## Auto-selección de Contexto
Lógica para que CoreAI decida qué Recursos enviar automáticamente al modelo basándose en la intención de la pregunta (sin que el modelo los pida).

## Cifrado en Reposo
Capa de seguridad local para los datos sensibles almacenados en la base de datos vectorial.

---

# 🔴 Won't Have (Fuera de scope por ahora)

Para evitar que la "Catedral" se vuelva inmanejable:

## Interfaz de Chat Propia
CoreAI es un backend; la UI la pone el cliente (Continue, VS Code, Telegram, etc.).

## Gestión de Modelos (Hosting)
CoreAI no levanta los LLMs; se conecta a ellos vía LiteLLM. El usuario es responsable de su LM Studio o instancia de Ollama.

## Sincronización Cloud
No hay "nube de CoreAI". La persistencia es estrictamente local y soberana por diseño.

## Multi-tenancy complejo
El sistema es una instancia por propósito/usuario; no está diseñado para ser un SaaS multi-usuario con roles y permisos complejos.