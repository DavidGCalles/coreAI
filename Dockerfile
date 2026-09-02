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

# Copiamos el resto del código
COPY . .

# Exponemos el puerto del servidor MCP
EXPOSE 8000

# El comando por defecto es el servidor de producción.
# En el docker-compose lo sobrescribimos con --reload para desarrollo.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]