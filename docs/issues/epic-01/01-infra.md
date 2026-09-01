# [coreAI] Infraestructura: Limpieza y reconfiguración del docker-compose.yml

## Contexto
El archivo `docker-compose.yml` actual contiene la infraestructura base, pero arrastra inercia y posibles servicios basura de iteraciones anteriores. El objetivo es purgarlo y reconfigurar los contenedores existentes para servir exclusivamente a la nueva arquitectura transaccional asíncrona.

## Tareas
- [ ] Eliminar cualquier servicio acoplado a interfaces de usuario, bots o dependencias de lifeOS.
- [ ] Reconfigurar el contenedor principal para que actúe estrictamente como `core-api` (Servidor MCP), eliminando variables de entorno obsoletas.
- [ ] Validar y mantener los contenedores de infraestructura existentes (`db-relational`, `db-vector`, `llm-proxy`, broker de mensajería).
- [ ] Añadir/Configurar el servicio `worker-node` para que consuma del broker de mensajería usando la misma imagen base que la API.

## Criterios de Aceptación
- El comando `docker-compose up -d` levanta el clúster refactorizado sin errores.
- Los contenedores de base de datos y broker no exponen puertos al host, quedando aislados en la red interna. Solo `core-api` y `llm-proxy` (si es necesario para debug) tienen salida externa.