# [coreAI] Infraestructura: Blindaje de redes y persistencia de volúmenes

## Contexto
La soberanía de los datos exige que la configuración de volúmenes del compose actual sea a prueba de balas y que la topología de red evite fugas de información.

## Tareas
- [ ] Auditar los mapeos de volúmenes en el `docker-compose.yml` para garantizar que la data de PostgreSQL, la base de datos vectorial y Redis persisten físicamente en el host.
- [ ] Refactorizar la configuración de redes (`networks`) creando una topología `bridge` estricta.
- [ ] Aplicar reglas de red: el contenedor de la base de datos relacional y la vectorial no deben tener salida a internet bajo ninguna circunstancia.

## Criterios de Aceptación
- Tras un `docker-compose down -v` simulado (protegiendo los volúmenes host) y un posterior `up -d`, el estado de las bases de datos se recupera intacto.
- Comprobación manual: entrar a la shell del contenedor de PostgreSQL y verificar que no hay ping al exterior.