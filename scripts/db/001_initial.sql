-- ==============================================================================
-- 2. TIPOS ESTRICTOS (ENUMS de Python)
-- ==============================================================================
CREATE TYPE session_status AS ENUM ('ACTIVE', 'CLOSED');
CREATE TYPE task_status AS ENUM ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED');
CREATE TYPE memory_visibility AS ENUM ('PRIVATE', 'FAMILY', 'PUBLIC');
CREATE TYPE memory_domain_type AS ENUM ('EPISODIC', 'DOCUMENT', 'SYSTEM');

-- ==============================================================================
-- 3. DEFINICIÓN DE TABLAS (La Arquitectura)
-- ==============================================================================


-- Tabla 3: Sesiones
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status session_status NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Tabla 4: Fundación para los Workers (ADR-014)
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    status task_status NOT NULL DEFAULT 'PENDING',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Tabla 5: El Historial de la Sesión (Mensajes aplanados y sin anidar)
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    name VARCHAR(255),
    input_type VARCHAR(50) NOT NULL,
    agent_key VARCHAR(255),
    consolidated BOOLEAN NOT NULL DEFAULT FALSE,
    
    visibility memory_visibility NOT NULL DEFAULT 'PRIVATE',
    domain memory_domain_type NOT NULL DEFAULT 'EPISODIC',
    
    -- Auditoría y ordenación
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ==============================================================================
-- 4. ÍNDICES DE RENDIMIENTO (Para que los SELECT no arrastren)
-- ==============================================================================
-- Índice para recuperar rápido el historial de una sesión concreta ordenada por tiempo
CREATE INDEX idx_messages_session_time ON messages(session_id, created_at);

-- Índice para los workers: buscar rápido qué tareas están pendientes
CREATE INDEX idx_tasks_status ON tasks(status) WHERE status = 'PENDING';