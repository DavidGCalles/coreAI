-- ==============================================================================
-- 1. LIMPIEZA DEL ENTORNO (Para que puedas iterar en local sin llorar)
-- ==============================================================================
DROP TABLE IF EXISTS messages CASCADE;
DROP TABLE IF EXISTS tasks CASCADE;
DROP TABLE IF EXISTS sessions CASCADE;
DROP TABLE IF EXISTS telegram_identities CASCADE;
DROP TABLE IF EXISTS users CASCADE;

DROP TYPE IF EXISTS user_role CASCADE;
DROP TYPE IF EXISTS user_status CASCADE;
DROP TYPE IF EXISTS session_status CASCADE;
DROP TYPE IF EXISTS task_status CASCADE;
DROP TYPE IF EXISTS memory_visibility CASCADE;
DROP TYPE IF EXISTS memory_domain_type CASCADE;