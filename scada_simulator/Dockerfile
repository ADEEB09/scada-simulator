FROM python:3.12-slim

LABEL maintainer="SCADA Hackathon"
LABEL description="Simulador de rede elétrica de distribuição para desafio técnico"

WORKDIR /app

# Dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código fonte
COPY config/ ./config/
COPY src/    ./src/

# Healthcheck básico
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import paho.mqtt.client; print('ok')" || exit 1

# Ponto de entrada
WORKDIR /app/src
CMD ["python", "main.py"]
