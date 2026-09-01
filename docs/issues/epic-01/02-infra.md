# [coreAI] Infraestructura: Poda y optimización de Dockerfiles

## Contexto
El repositorio cuenta con varios archivos de construcción (`Dockerfile`, `Dockerfile.litellm`, `Dockerfile.infinity`). Es necesario destriparlos para asegurar que las imágenes finales sean mínimas, seguras y libres de dependencias zombi.

## Tareas
- [ ] Auditar el `Dockerfile` principal: eliminar instalaciones de dependencias de frontend o agentes complejos. Optimizar las capas para un backend puro (FastAPI/MCP).
- [ ] Validar la configuración estática de `Dockerfile.litellm` para asegurar que el enrutamiento proxy cumple con las nuevas políticas local-first.
- [ ] Auditar la necesidad real de `Dockerfile.infinity` bajo el nuevo modelo asíncrono. Si no es un pilar de carga, eliminar el archivo del repositorio.

## Criterios de Aceptación
- Las imágenes se construyen desde cero sin depender de caché (`docker-compose build --no-cache`).
- Las imágenes resultantes no contienen librerías del ecosistema de UI o bots (cero dependencias huérfanas).