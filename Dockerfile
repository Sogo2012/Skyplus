# =============================================================================
# SKYPLUS 1.0 — Dockerfile v3.2 (Fixed Dependencies & CMD)
# =============================================================================
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=America/Mexico_City

# 1. SISTEMA OPERATIVO Y PYTHON 3.11
# -----------------------------------------------------------------------------
RUN apt-get update && apt-get install -y \
    python3.11 python3.11-dev python3-pip python3.11-venv \
    wget curl unzip libgomp1 libglu1-mesa libgl1-mesa-glx libxt6 ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python  python  /usr/bin/python3.11 1

# 2. ENERGYPLUS 23.2.0
# -----------------------------------------------------------------------------
ENV ENERGYPLUS_DIR=/usr/local/EnergyPlus-23-2-0
RUN wget -q \
    "https://github.com/NREL/EnergyPlus/releases/download/v23.2.0/EnergyPlus-23.2.0-7636e6b3e9-Linux-Ubuntu22.04-x86_64.tar.gz" \
    -O /tmp/energyplus.tar.gz \
    && mkdir -p ${ENERGYPLUS_DIR} \
    && tar -xzf /tmp/energyplus.tar.gz -C ${ENERGYPLUS_DIR} --strip-components=1 \
    && ln -sf ${ENERGYPLUS_DIR}/energyplus /usr/local/bin/energyplus \
    && rm /tmp/energyplus.tar.gz

# 3. DIRECTORIO DE TRABAJO
# -----------------------------------------------------------------------------
WORKDIR /app

# 4. INSTALACIÓN DE PYTHON Y LIBRERÍAS
# -----------------------------------------------------------------------------
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel

# 4.1 Copiar e instalar requirements.txt (La mejor práctica)
# Esto asegura que folium, geopy y streamlit-vtkjs se instalen automáticamente.
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# 4.2 Extensiones adicionales de Honeybee necesarias para el backend
RUN pip3 install --no-cache-dir \
    "pydantic<2.0" \
    "ladybug-display-schema" \
    "lbt-dragonfly" \
    "lbt-honeybee" \
    "honeybee-openstudio" \
    "vtk" \
    "ladybug-vtk" \
    "honeybee-vtk"
# 5. CÓDIGO Y ESTRUCTURA DE CARPETAS
# -----------------------------------------------------------------------------
COPY . .
RUN mkdir -p /tmp/skyplus_sims /app/data && chmod 777 /tmp/skyplus_sims
RUN mkdir -p /app/.streamlit

# 6. VARIABLES DE ENTORNO Y ARRANQUE DEFINITIVO
# -----------------------------------------------------------------------------
ENV PYTHONPATH=/app
ENV ENERGYPLUS_EXEC=/usr/local/bin/energyplus
ENV STREAMLIT_SERVER_PORT=8080
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0

# ESTA ES LA LÍNEA QUE ENCIENDE EL SERVIDOR DE STREAMLIT
CMD ["python3", "-m", "streamlit", "run", "app.py"]