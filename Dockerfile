FROM python:3.12-slim

# Evita archivos .pyc y mantiene los logs visibles
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencias del sistema necesarias
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copiar aplicación
COPY app/ .

# Directorios que serán utilizados mediante volúmenes
RUN mkdir -p \
    /data \
    /mail \
    /attachments \
    /backup \
    /logs \
    /oauth

EXPOSE 8080

CMD ["python", "web.py"]
