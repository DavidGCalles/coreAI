import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from src.managers.config_manager import config_manager

# Instanciamos el engine tirando de tu config_manager. 
# pool_size y max_overflow te blindarán cuando los workers asíncronos ataquen en paralelo.
engine = create_async_engine(
    config_manager.get_postgres_url(),
    echo=os.getenv("APP_ENV") == "development", # Logueamos las queries solo en dev
    pool_size=10,
    max_overflow=20
)

# Fábrica de sesiones asíncronas
AsyncSessionLocal = async_sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False, 
    autocommit=False, 
    autoflush=False
)

Base = declarative_base()