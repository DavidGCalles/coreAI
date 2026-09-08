import pytest
import asyncpg
from src.managers.config_manager import config_manager

@pytest.mark.asyncio
async def test_raw_postgres_connection():
    """
    Test de infraestructura puro. Valida que el contenedor de PostgreSQL 
    está vivo, el puerto está expuesto, y las credenciales del .env funcionan.
    """
    # 1. Obtenemos la URL del config manager (ya con el parche a 127.0.0.1 si estamos en dev)
    db_url = config_manager.get_postgres_url()
    
    # 2. asyncpg no entiende el prefijo '+asyncpg' de SQLAlchemy, así que lo limpiamos para la conexión cruda
    dsn = db_url.replace("+asyncpg", "")
    
    try:
        # 3. Intentamos levantar la conexión cruda
        conn = await asyncpg.connect(dsn, timeout=5.0)
        
        # 4. Validamos que podemos ejecutar una query básica
        version = await conn.fetchval('SELECT version()')
        
        assert version is not None
        assert "PostgreSQL" in version
        
        # 5. Cerramos limpio
        await conn.close()
        
    except asyncpg.exceptions.InvalidPasswordError:
        pytest.fail("Error de credenciales: El usuario o la contraseña no coinciden con la base de datos.")
    except ConnectionRefusedError:
        pytest.fail("Conexión rechazada: ¿Has abierto el puerto 5432 en el docker-compose.yml?")
    except Exception as e:
        pytest.fail(f"Fallo de conexión a la infraestructura base: {e}")