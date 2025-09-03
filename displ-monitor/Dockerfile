FROM python:3.11-slim

# Crear carpeta de la app
WORKDIR /app

# Dependencias del sistema (opcionales)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates && rm -rf /var/lib/apt/lists/*

# Copiar e instalar requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY app.py ./
COPY displ_api.py ./

# Crear ruta de datos (el disco persistente se montará aquí)
RUN mkdir -p /data
ENV DB_PATH=/data/monitor.db
ENV PORT=8000
ENV START_SCHEDULER_ON_BOOT=true

EXPOSE 8000

CMD ["python", "app.py"]
