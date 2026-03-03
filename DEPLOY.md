# 🚀 Guía de Deploy — SkyPlus 1.0

## Estructura de archivos

```
Skyplus/
├── app.py                    ← UI Streamlit (5 tabs)
├── weather_utils.py          ← Motor climático (OneBuilding.org)
├── geometry_utils.py         ← Motor geométrico (Honeybee + VTK)
├── onebuilding_mapping.json  ← URLs EPW por país
├── requirements.txt          ← Dependencias Python (versiones fijas)
├── Dockerfile                ← Ubuntu 22.04 + EnergyPlus 23.2
├── .dockerignore
├── .streamlit/
│   └── config.toml
├── cloudbuild.yaml           ← CI/CD Google Cloud Build
└── motor/
    ├── __init__.py
    └── termico.py            ← Motor SkyPlus v22 (EnergyPlus + EPW)
```

---

## FASE A — Test local con Docker Desktop

### Requisitos previos
- Docker Desktop con WSL2 actualizado (`wsl --update`)
- Git instalado

### Pasos

```bash
# 1. Clonar el repo
git clone https://github.com/Sogo2012/Skyplus.git
cd Skyplus

# 2. Construir la imagen (primera vez: ~15-20 min por EnergyPlus)
docker build -t skyplus:local .

# 3. Correr el contenedor localmente
docker run -p 8080:8080 skyplus:local

# 4. Abrir en el navegador
# http://localhost:8080
```

### Verificar que EnergyPlus funciona dentro del contenedor
```bash
# Entrar al contenedor
docker run -it skyplus:local bash

# Dentro del contenedor:
energyplus --version
honeybee-energy --help
python3 -c "from motor import calcular_curva_sfr; print('Motor OK')"
```

---

## FASE B — Deploy en Google Cloud Run

### Requisitos previos
- Cuenta Google Cloud con proyecto creado
- `gcloud` CLI instalado y autenticado
- APIs habilitadas: Cloud Run, Cloud Build, Container Registry

### Pasos

```bash
# 1. Autenticar
gcloud auth login
gcloud config set project TU_PROJECT_ID

# 2. Habilitar APIs (solo primera vez)
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable containerregistry.googleapis.com

# 3. Deploy con Cloud Build (build + push + deploy en un comando)
gcloud builds submit --config cloudbuild.yaml

# 4. Ver la URL del servicio desplegado
gcloud run services describe skyplus --region=us-central1 --format='value(status.url)'
```

### Recursos configurados en Cloud Run
| Parámetro     | Valor    | Razón                                    |
|---------------|----------|------------------------------------------|
| Memory        | 4 GB     | EnergyPlus + Ladybug pueden usar 2-3 GB |
| CPU           | 2 vCPU   | Simulaciones paralelas                   |
| Timeout       | 600 s    | Simulación 7 SFRs ≈ 5-10 min            |
| Min instances | 0        | Scale-to-zero para ahorrar costos        |
| Max instances | 3        | Máximo 3 usuarios simultáneos            |
| Concurrency   | 5        | Requests por instancia                   |

### Costo estimado
- Sin tráfico: **$0/mes** (scale-to-zero)
- Uso normal (50 simulaciones/mes): **~$3-8 USD/mes**
- Tráfico alto (500 simulaciones/mes): **~$20-40 USD/mes**

---

## FASE C — Dominio personalizado + WordPress

```bash
# Mapear dominio personalizado en Cloud Run
gcloud run domain-mappings create \
  --service skyplus \
  --domain skyplus.tudominio.com \
  --region us-central1
```

En WordPress (SiteGround), embeber con iframe:
```html
<iframe 
  src="https://skyplus.tudominio.com" 
  width="100%" 
  height="900px" 
  frameborder="0">
</iframe>
```

---

## Troubleshooting común

### EnergyPlus no encontrado
```bash
# Verificar que el symlink existe dentro del contenedor
docker run -it skyplus:local which energyplus
# Debe retornar: /usr/local/bin/energyplus
```

### Error de memoria en simulación
Aumentar memoria en Cloud Run:
```bash
gcloud run services update skyplus --memory=8Gi --region=us-central1
```

### Timeout en simulación larga
Aumentar timeout:
```bash
gcloud run services update skyplus --timeout=900 --region=us-central1
```
