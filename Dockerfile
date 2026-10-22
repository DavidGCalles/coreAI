# Python 3.13-slim para mantener la imagen ligera y eficiente
FROM python:3.13-slim

# Evita que Python genere archivos .pyc y asegura que los logs salgan directos
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencias de sistema básicas para drivers de DB y compilación
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Instalación de dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos SOLO el código del servicio.
# core-mcp y worker son clientes del gateway LiteLLM: los modelos los sirve
# Infinity en su propia imagen (Dockerfile.infinity). COPY . . horneaba en
# cada imagen los pesos de models/ (e5 + NLI + kit CLIP), el .venv de dev
# (con torch/triton dentro) y el .git completo — gigas de lastre muerto que
# agotaban el disco en cada build. El contexto se cerca además en
# Dockerfile.dockerignore para que ni se transfiera al builder.
COPY src/ ./src

# Exponemos el puerto del servidor MCP
EXPOSE 8000

# El comando por defecto es el servidor de producción.
# En el docker-compose lo sobrescribimos con --reload para desarrollo.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
