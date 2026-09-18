import os
import pytest

# Secuestramos la variable de entorno ANTES de que ningún módulo la lea.
# Así obligamos al ConfigManager a apuntar siempre a la base de datos de pruebas.
os.environ["POSTGRES_URL"] = "postgresql+asyncpg://coreai_user:postgres_secure_password@localhost:5432/coreai_test_db"

@pytest.fixture(autouse=True)
def protect_dev_database():
    """
    Cortafuegos paranoico: Si por algún motivo la URL no apunta a la DB de test, 
    aborta la ejecución inmediatamente para proteger los datos de desarrollo.
    """
    from src.managers.config_manager import config_manager
    url = config_manager.get_postgres_url()
    
    if "coreai_test_db" not in url:
        pytest.exit(f"¡ALERTA! Pytest intentó conectarse a la BD de desarrollo: {url}")