# =============================================================================
# SKYPLUS 1.0 — Dockerfile
# Base: Ubuntu 22.04 LTS
# EnergyPlus 23.2.0 + Python 3.11 + Streamlit
# =============================================================================

FROM ubuntu:22.04

# Evitar prompts interactivos durante la instalación
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=America/Mexico_City

# =============================================================================
# 1. DEPENDENCIAS DEL SISTEMA
# =============================================================================
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3-pip \
    python3.11-venv \
    wget \
    curl \
    unzip \
    libgomp1 \
    libglu1-mesa \
    libgl1-mesa-glx \
    libxt6 \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Python 3.11 como default
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python  python  /usr/bin/python3.11 1

# =============================================================================
# 2. ENERGYPLUS 23.2.0
# =============================================================================
ENV ENERGYPLUS_VERSION=23.2.0
ENV ENERGYPLUS_DIR=/usr/local/EnergyPlus-23-2-0

RUN wget -q \
    "https://github.com/NREL/EnergyPlus/releases/download/v${ENERGYPLUS_VERSION}/EnergyPlus-${ENERGYPLUS_VERSION}-7636e6b3e9-Linux-Ubuntu22.04-x86_64.tar.gz" \
    -O /tmp/energyplus.tar.gz \
    && mkdir -p ${ENERGYPLUS_DIR} \
    && tar -xzf /tmp/energyplus.tar.gz -C ${ENERGYPLUS_DIR} --strip-components=1 \
    && ln -sf ${ENERGYPLUS_DIR}/energyplus /usr/local/bin/energyplus \
    && rm /tmp/energyplus.tar.gz

# Verificar instalación
RUN energyplus --version

# =============================================================================
# 3. DIRECTORIO DE TRABAJO
# =============================================================================
WORKDIR /app

# =============================================================================
# 4. DEPENDENCIAS PYTHON
# =============================================================================
# Copiar solo requirements primero (aprovecha cache de Docker)
COPY requirements.txt .

RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel \
    && pip3 install --no-cache-dir -r requirements.txt

# =============================================================================
# 5. CÓDIGO DE LA APLICACIÓN
# =============================================================================
COPY . .

# Crear directorio para archivos temporales de simulación
RUN mkdir -p /tmp/skyplus_sims \
    && mkdir -p /app/data \
    && chmod 777 /tmp/skyplus_sims

# =============================================================================
# 6. STREAMLIT CONFIG
# =============================================================================
RUN mkdir -p /app/.streamlit
COPY .streamlit/config.toml /app/.streamlit/config.toml

# =============================================================================
# 7. VARIABLES DE ENTORNO
# =============================================================================
ENV PYTHONPATH=/app
ENV ENERGYPLUS_EXEC=/usr/local/bin/energyplus
ENV STREAMLIT_SERVER_PORT=8080
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# =============================================================================
# 8. PUERTO Y COMANDO DE INICIO
# =============================================================================
EXPOSE 8080

# Health check para Cloud Run
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.port=8080", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
