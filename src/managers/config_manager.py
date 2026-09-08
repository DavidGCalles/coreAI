import os
import logging
from typing import Any

logger = logging.getLogger(__name__)

class ConfigManager:
    """
    Gestor de configuración centralizado y agnóstico de entorno.
    Sigue los principios de 12-Factor App: un entorno, un set de variables.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        # Espacio para validaciones tempranas de variables críticas si fuera necesario en el futuro
        pass

    def get_postgres_url(self) -> str:
        """Obtiene y formatea la URL de conexión para el driver asíncrono."""
        url = os.getenv("POSTGRES_URL", "postgresql+asyncpg://coreai_user:postgres_secure_password@localhost:5432/coreai_db")

        if os.getenv("APP_ENV", "development") == "development" and "@postgres:" in url:
            url = url.replace("@postgres:", "@192.168.1.20:")
        return self._ensure_asyncpg_url(url)

    def _ensure_asyncpg_url(self, url: str) -> str:
        """Fuerza el uso de asyncpg para SQLAlchemy."""
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url

    def get_qdrant_config(self) -> dict[str, Any]:
        """Configuración del motor vectorial."""
        url = os.getenv("QDRANT_URL", "http://qdrant:6333")
        if os.getenv("APP_ENV", "development") == "development" and "qdrant" in url:
            url = url.replace("qdrant", "192.168.1.20")
        return {
            "url": url,
            "api_key": os.getenv("QDRANT_API_KEY", None)
        }

    def get_litellm_config(self) -> dict[str, Any]:
        """Configuración del proxy de enrutamiento LLM/Embeddings."""
        return {
            "url": os.getenv("LITELLM_URL", "http://localhost:4000"),
            "api_key": os.getenv("LITELLM_API_KEY", "sk-coreai-internal")
        }

    def get_app_config(self) -> dict[str, Any]:
        """Configuración general del sistema."""
        return {
            "env": os.getenv("APP_ENV", "development"),
            "log_level": os.getenv("LOG_LEVEL", "INFO")
        }

# Instancia Singleton exportada
config_manager = ConfigManager()