import logging
import asyncio
from typing import Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import Task, SessionStatus
from src.schemas.tasks import TaskType
from src.workers.task_router import route_task
from src.logging_config import get_logger

logger = get_logger("worker")


async def _initialize(session_id: int) -> None:
    """
    Inicializa el worker verificando que la sesión existe y está activa.
    
    Args:
        session_id: ID de la sesión para verificar
    
    Raises:
        ValueError: Si la sesión no existe o no está en estado ACTIVE
    """
    # Simulación de inicialización - TODO: Conectar a DB real y validar sesión
    logger.info("  [Worker] Inicializando worker para sesión %d", session_id)
    # Aquí iría: await session.execute(f"SELECT * FROM sessions WHERE id = {session_id}")


async def _unlock(session_id: int) -> None:
    """
    Libera el bloqueo atómico sobre la fila de la sesión.
    Esto permite que otras consultas lean/esciban datos concurrentemente.
    
    Args:
        session_id: ID de la sesión para liberar el lock
    """
    # Simulación de liberación - TODO: Implementar con advisory locks o PostgreSQL row locks
    logger.debug("  [Worker] Liberando bloqueo atómico sobre sesión %d", session_id)
    # Aquí iría: await session.execute(f"SELECT pg_advisory_xact_lock(%s)", (session_id,))


async def _fetch_task(session_id: int, session: AsyncSession) -> Optional[Task]:
    """
    Recupera la tarea asociada a esta sesión (pending, running, blocked).
    
    Args:
        session_id: ID de la sesión
        session: Sesión SQLAlchemy asíncrona activa
        
    Returns:
        Task si existe, None si no hay tareas pendientes
    """
    # Buscar tarea con el status correcto y liberar lock automáticamente
    query = (
        session.query(Task)
        .filter(
            Task.status.in_(["PENDING", "RUNNING", "BLOCKED"]),
            Task.session_id == session_id
        )
        .limit(1)
        .first()
    )
    return query


async def _update_task_status(task_id: int, new_status: str) -> None:
    """
    Actualiza el estado de la tarea y libera automáticamente el lock.
    
    Args:
        task_id: ID de la tarea a actualizar
        new_status: Nuevo estado (PENDING, RUNNING, COMPLETED, FAILED)
    """
    logger.debug("  [Worker] Actualizando estado de tarea %s a %s", task_id, new_status)
    # Aquí iría: await session.execute(f"UPDATE tasks SET status = %s WHERE id = %s", (new_status, task_id))


async def worker_loop(session_id: int) -> None:
    """
    Bucle principal del worker.
    Monitoriza tareas pendientes y las ejecuta en memoria hasta que cambien de estado
    a COMPLETED o FAILED, liberando el bloqueo atómico (row lock).
    
    Args:
        session_id: ID único de la sesión asociada al worker
    """
    logger.info("  [Worker] Worker iniciado con sesión %d", session_id)
    
    # =====================================================================
    # FASE 1: INICIALIZACIÓN
    # =====================================================================
    await _initialize(session_id)
    
    # =====================================================================
    # FASE 2: TRABAJO EN MEMORIA (Bucle Principal del Worker)
    # =====================================================================
    try:
        while True:
            logger.debug("  [Worker] Iteración del worker iniciada...")
            
            await _unlock(session_id)  # Bloqueo atómico
            
            async with AsyncSession as session:
                # Recupero la tarea asociada a esta sesión (pending, running, blocked)
                task = await _fetch_task(session_id, session)
                if not task:
                    logger.debug("  [Worker] Sin tarea pendiente para la sesión %d", session_id)
                    break
                
                # --- CAMBIO DE ESTADO: PENDING -> RUNNING (Fase 2) ---
                try:
                    await session.execute(
                        """
                            UPDATE sessions SET status = 'ACTIVE' WHERE id = :id
                        """,
                        {"id": session_id}
                    )
                    
                    # Actualizamos el estado de la tarea a RUNNING (bloqueando por defecto en PostgreSQL)
                    await _update_task_status(task.id, "RUNNING")
                    
                    # --------------------------------------------------
                    # CAMBIA LA LÍNEA DE ABAJO PARA USAR EL NUEVO ROUTER
                    # --------------------------------------------------
                    
                    # --- EJECUCIÓN EN MEMORIA (Fase 2: Reemplaza la lógica local) ---
                    await route_task(task, session)  # <-- NUESTRO NUEVO ROUTER!
                    
                    # --- CAMBIA DE ESTADO: RUNNING -> COMPLETED (Fase 3) ---
                    await _update_task_status(task.id, "COMPLETED")
                    await _unlock(session_id)
                    
                except Exception as e:
                    # Tratamiento de excepciones: FASE 4 - FALLA
                    logger.exception("  [Worker] Ejecución fallida para la tarea %s", task.id)
                    
                    try:
                        await session.execute(
                            """
                                UPDATE sessions SET status = 'CLOSED' WHERE id = :id
                            """,
                            {"id": session_id}
                        )
                    except Exception as ex_close:
                        logger.error("  [Worker] Error al cerrar sesión: %s", str(ex_close))
                    
                    # Actualizamos el estado de la tarea a FAILED y liberamos
                    await _update_task_status(task.id, "FAILED")
                    await _unlock(session_id)
                    break
            
    except KeyboardInterrupt:
        logger.info("  [Worker] Worker detenido manualmente.")
    finally:
        logger.info("  [Worker] Bucle del worker finalizado para la sesión %d", session_id)
