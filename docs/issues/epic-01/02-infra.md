# [coreAI] Infraestructura: Poda y optimización de Dockerfiles

## Contexto
El repositorio cuenta con varios archivos de construcción (`Dockerfile`, `Dockerfile.litellm`, `Dockerfile.infinity`). Es necesario destriparlos para asegurar que las imágenes finales sean mínimas, seguras y libres de dependencias zombi.

## Tareas
- [x] Auditar el `Dockerfile` principal: eliminar instalaciones de dependencias de frontend o agentes complejos. Optimizar las capas para un backend puro (FastAPI/MCP).
- [x] Validar la configuración estática de `Dockerfile.litellm` para asegurar que el enrutamiento proxy cumple con las nuevas políticas local-first.
- [x] Auditar la necesidad real de `Dockerfile.infinity` bajo el nuevo modelo asíncrono. Si no es un pilar de carga, eliminar el archivo del repositorio.

## Criterios de Aceptación
- Las imágenes se construyen desde cero sin depender de caché (`docker-compose build --no-cache`).
- Las imágenes resultantes no contienen librerías del ecosistema de UI o bots (cero dependencias huérfanas).

## Comentarios
El Dockerfile de litellm es innecesario sin despliegues cloud, así como los comentarios en el de infinity. Quedarían por resolver cuestiones de seguridad menor como que el Dockerfile de mcp se ejecuta en root.