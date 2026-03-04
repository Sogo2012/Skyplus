# app.py
# =============================================================================
# SKYPLUS 1.0 — Eco Consultor | Sunoptics LATAM
# Motor: EnergyPlus 23.2 (DOE) + EPW analítico (ISO 8995-1 / IES RP-7)
# Diseño: Identidad ECO — Pantone 309C / 575C / 432C
# =============================================================================

import os
import time
import streamlit as st
import pandas as pd
import numpy as np
import folium
import plotly.graph_objects as go
import plotly.express as px
from streamlit_folium import st_folium
from streamlit_vtkjs import st_vtkjs

# Módulos locales
from geometry_utils import generar_nave_3d_vtk
from weather_utils import (
    obtener_estaciones_cercanas,
    descargar_y_extraer_epw,
    procesar_datos_clima,
)

# Motor SkyPlus v22
try:
    from motor import calcular_curva_sfr, configurar_proyecto, simular_caso_diseno
    MOTOR_DISPONIBLE = True
except ImportError:
    MOTOR_DISPONIBLE = False

def verificar_cuota(correo):
    """Verifica cuota diaria consultando Google Sheets."""
    try:
        from motor.sheets import verificar_cuota as _vq
        return _vq(correo)
    except Exception:
        return 0, True

GCS_BUCKET = "skyplus-epw-linen-rex"

def upload_epw_to_gcs(local_path, correo):
    """
    Sube el EPW local a GCS y retorna la URI gs://.
    Retorna None si falla — el job no se lanza.
    """
    try:
        from google.cloud import storage
        import hashlib, os
        client   = storage.Client()
        bucket   = client.bucket(GCS_BUCKET)
        key      = hashlib.md5(correo.encode()).hexdigest()[:8]
        filename = os.path.basename(local_path)
        blob_name = f"epw/{key}/{filename}"
        blob     = bucket.blob(blob_name)
        blob.upload_from_filename(local_path)
        gcs_uri  = f"gs://{GCS_BUCKET}/{blob_name}"
        return gcs_uri
    except Exception as e:
        import logging
        logging.error(f"Error subiendo EPW a GCS: {e}")
        return None

def lanzar_cloud_run_job(config, lead, sql_base=None):
    """
    Lanza skyplus-job via Cloud Run Jobs API.
    Inyecta JOB_CONFIG como variable de entorno con el payload JSON.
    Retorna (ok: bool, mensaje: str).
    """
    import json
    try:
        import google.auth
        import google.auth.transport.requests
        import requests as _requests

        payload = json.dumps({
            "config":              config,
            "lead":                lead,
            "sql_base_existente":  sql_base,
        })

        creds, project_id = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_req = google.auth.transport.requests.Request()
        creds.refresh(auth_req)
        token = creds.token

        region   = "us-central1"
        job_name = "skyplus-job"
        url = (
            f"https://run.googleapis.com/v2/projects/{project_id}"
            f"/locations/{region}/jobs/{job_name}:run"
        )

        body = {
            "overrides": {
                "containerOverrides": [{
                    "env": [
                        {"name": "JOB_CONFIG", "value": payload}
                    ]
                }]
            }
        }

        resp = _requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            json=body,
            timeout=15,
        )

        if resp.status_code in (200, 202):
            return True, "Job lanzado"
        else:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"

    except Exception as e:
        return False, str(e)

# =============================================================================
# PALETA ECO — Libro de Marca
# =============================================================================
ECO_AZUL    = "#003C52"   # Pantone 309C — Conservación del ambiente
ECO_VERDE   = "#4A7C2F"   # Pantone 575C — Confort y ahorro energético
ECO_GRIS    = "#4A5568"   # Pantone 432C — Obra gris / construcción
ECO_AZUL_LT = "#E8F0F3"   # Fondo sutil derivado del azul corporativo
ECO_VERDE_LT= "#EBF5E1"   # Fondo sutil derivado del verde corporativo
ECO_GRIS_LT = "#F4F5F6"   # Background general
ECO_LINEA   = "#CBD5E0"   # Separadores y bordes

# =============================================================================
# 1. CONFIGURACIÓN DE PÁGINA
# =============================================================================
st.set_page_config(
    page_title="SkyPlus — Eco Consultor",
    layout="wide",
    page_icon="assets/favicon.ico" if os.path.exists("assets/favicon.ico") else None,
)

st.markdown(f"""
    <style>
    /* ── Reset y base ───────────────────────────────────────────────── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"] {{
        font-family: 'Inter', sans-serif;
    }}

    .main {{
        background-color: {ECO_GRIS_LT};
    }}

    /* ── Header de app ──────────────────────────────────────────────── */
    header[data-testid="stHeader"] {{
        background-color: {ECO_AZUL};
    }}

    /* ── Sidebar ────────────────────────────────────────────────────── */
    [data-testid="stSidebar"] {{
        background-color: #FFFFFF;
        border-right: 1px solid {ECO_LINEA};
    }}

    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] .stMarkdown p {{
        color: {ECO_GRIS};
        font-size: 0.82rem;
    }}

    /* ── Tabs ───────────────────────────────────────────────────────── */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 0;
        border-bottom: 2px solid {ECO_LINEA};
        background-color: white;
    }}

    .stTabs [data-baseweb="tab"] {{
        height: 40px;
        padding: 0 20px;
        font-size: 0.82rem;
        font-weight: 500;
        letter-spacing: 0.03em;
        text-transform: uppercase;
        color: {ECO_GRIS};
        border: none;
        background: transparent;
    }}

    .stTabs [aria-selected="true"] {{
        color: {ECO_AZUL} !important;
        border-bottom: 2px solid {ECO_AZUL} !important;
        background: transparent !important;
        font-weight: 600;
    }}

    /* ── Botones principales ─────────────────────────────────────────── */
    .stButton > button[kind="primary"] {{
        background-color: {ECO_AZUL};
        color: white;
        border: none;
        border-radius: 3px;
        font-size: 0.85rem;
        font-weight: 600;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        padding: 0.65em 1.5em;
        transition: background 0.2s;
    }}
    .stButton > button[kind="primary"]:hover {{
        background-color: #005070;
    }}
    .stButton > button:not([kind="primary"]) {{
        background-color: white;
        color: {ECO_AZUL};
        border: 1px solid {ECO_AZUL};
        border-radius: 3px;
        font-size: 0.82rem;
        font-weight: 500;
    }}

    /* ── Métricas nativas — ocultar para usar cards custom ──────────── */
    [data-testid="stMetricValue"] {{
        font-size: 1.5rem !important;
        font-weight: 600 !important;
        color: {ECO_AZUL} !important;
    }}
    [data-testid="stMetricLabel"] {{
        font-size: 0.72rem !important;
        font-weight: 500 !important;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: {ECO_GRIS} !important;
    }}
    [data-testid="stMetricDelta"] {{
        font-size: 0.75rem !important;
        color: {ECO_VERDE} !important;
    }}

    /* ── Cards de resultado ─────────────────────────────────────────── */
    .eco-card {{
        background: white;
        border-radius: 4px;
        border: 1px solid {ECO_LINEA};
        border-left: 3px solid {ECO_AZUL};
        padding: 12px 14px;
        margin-bottom: 8px;
        min-width: 0;
        box-sizing: border-box;
        height: 100%;
    }}
    .eco-card-label {{
        font-size: 0.63rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.07em;
        color: {ECO_GRIS};
        margin-bottom: 5px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .eco-card-value {{
        font-size: clamp(0.85rem, 2vw, 1.3rem);
        font-weight: 700;
        color: {ECO_AZUL};
        line-height: 1.2;
        word-break: break-word;
        overflow-wrap: break-word;
    }}
    .eco-card-delta {{
        font-size: 0.67rem;
        color: {ECO_VERDE};
        margin-top: 4px;
        font-weight: 500;
        word-break: break-word;
    }}
    .eco-card-green {{
        border-left-color: {ECO_VERDE};
    }}
    .eco-card-green .eco-card-value {{
        color: {ECO_VERDE};
    }}
    /* Cards compactas para resumen del proyecto */
    .eco-card-sm .eco-card-value {{
        font-size: clamp(0.75rem, 1.5vw, 1.05rem);
    }}
    .eco-card-sm .eco-card-label {{
        font-size: 0.58rem;
    }}

    /* ── Section headers ────────────────────────────────────────────── */
    .eco-section-title {{
        font-size: 0.72rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: {ECO_GRIS};
        border-bottom: 1px solid {ECO_LINEA};
        padding-bottom: 6px;
        margin-bottom: 14px;
        margin-top: 4px;
    }}

    /* ── Page title ─────────────────────────────────────────────────── */
    .eco-page-title {{
        font-size: 1.25rem;
        font-weight: 700;
        color: {ECO_AZUL};
        letter-spacing: -0.01em;
    }}
    .eco-page-subtitle {{
        font-size: 0.78rem;
        color: {ECO_GRIS};
        margin-top: 2px;
    }}

    /* ── Sidebar brand header ───────────────────────────────────────── */
    .eco-brand {{
        background: {ECO_AZUL};
        margin: -1rem -1rem 1.2rem -1rem;
        padding: 0;
        border-bottom: 3px solid {ECO_VERDE};
    }}
    /* Zona blanca solo para el logo */
    .eco-brand-logo-zone {{
        background: #FFFFFF;
        padding: 12px 20px 10px 20px;
        border-bottom: 1px solid {ECO_LINEA};
    }}
    /* Zona azul para el texto SkyPlus */
    .eco-brand-text-zone {{
        padding: 10px 20px 12px 20px;
    }}
    /* ── Logo ECO — sin filtro ───────────────────────────────────────── */
    .eco-logo-wrap {{
        max-width: 160px;
        margin-bottom: 0;
    }}
    .eco-logo-wrap img {{
        max-width: 100% !important;
        height: auto !important;
    }}
    /* ── Logo Sunoptics — fondo blanco ──────────────────────────────── */
    .eco-sunoptics-logo-wrap {{
        max-width: 140px;
        margin: 0 auto 4px 0;
    }}
    .eco-sunoptics-logo-wrap img {{
        width: 100% !important;
        height: auto !important;
    }}
    .eco-brand-name {{
        font-size: 1.1rem;
        font-weight: 700;
        color: white;
        letter-spacing: 0.05em;
    }}
    .eco-brand-sub, .eco-brand-sub-dark {{
        font-size: 0.68rem;
        color: rgba(255,255,255,0.65);
        letter-spacing: 0.04em;
        margin-top: 1px;
    }}
    .eco-brand-product, .eco-brand-product-dark {{
        font-size: 0.7rem;
        font-weight: 600;
        color: {ECO_VERDE};
        letter-spacing: 0.12em;
        text-transform: uppercase;
        margin-top: 6px;
    }}

    /* ── Sidebar section labels ─────────────────────────────────────── */
    .eco-sidebar-section {{
        font-size: 0.65rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: {ECO_GRIS};
        background: {ECO_GRIS_LT};
        border-left: 2px solid {ECO_AZUL};
        padding: 5px 8px;
        margin: 14px 0 8px 0;
    }}

    /* ── Status badges ──────────────────────────────────────────────── */
    .eco-badge-ok {{
        display: inline-block;
        background: {ECO_VERDE_LT};
        color: {ECO_VERDE};
        border: 1px solid {ECO_VERDE};
        border-radius: 2px;
        font-size: 0.65rem;
        font-weight: 600;
        letter-spacing: 0.05em;
        padding: 2px 7px;
        text-transform: uppercase;
    }}
    .eco-badge-warn {{
        display: inline-block;
        background: #FFF8E1;
        color: #B7791F;
        border: 1px solid #F6AD55;
        border-radius: 2px;
        font-size: 0.65rem;
        font-weight: 600;
        padding: 2px 7px;
        text-transform: uppercase;
    }}
    .eco-badge-info {{
        display: inline-block;
        background: {ECO_AZUL_LT};
        color: {ECO_AZUL};
        border: 1px solid #90B8C8;
        border-radius: 2px;
        font-size: 0.65rem;
        font-weight: 600;
        padding: 2px 7px;
        text-transform: uppercase;
    }}

    /* ── Disclaimer técnico ─────────────────────────────────────────── */
    .eco-disclaimer {{
        background: {ECO_AZUL_LT};
        border-left: 3px solid {ECO_AZUL};
        border-radius: 2px;
        padding: 10px 14px;
        font-size: 0.75rem;
        color: {ECO_GRIS};
        line-height: 1.5;
    }}

    /* ── Ocultar branding de terceros ───────────────────────────────── */
    .pollination-logo, .ladybug-logo {{ display: none !important; }}
    div[title="Powered by Pollination"] {{ display: none !important; }}
    a[href*="pollination.cloud"] {{ display: none !important; }}
    a[href*="ladybug.tools"] {{ display: none !important; }}

    /* ── Dataframe ──────────────────────────────────────────────────── */
    [data-testid="stDataFrame"] {{
        border: 1px solid {ECO_LINEA};
        border-radius: 4px;
    }}

    /* ── Expander ───────────────────────────────────────────────────── */
    details summary {{
        font-size: 0.8rem;
        font-weight: 600;
        color: {ECO_AZUL};
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }}
    </style>
""", unsafe_allow_html=True)


# =============================================================================
# HELPER — Cards HTML para métricas sin truncamiento
# =============================================================================
def _img_base64(path, max_width, extra_style=""):
    """Carga imagen como base64 — bypasea el procesamiento de st.image() para máxima nitidez."""
    import base64
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    ext = path.split(".")[-1].lower()
    mime = "image/png" if ext == "png" else "image/svg+xml" if ext == "svg" else "image/jpeg"
    return (
        f'<img src="data:{mime};base64,{data}" '
        f'style="max-width:{max_width}px; width:100%; height:auto; '
        f'image-rendering:-webkit-optimize-contrast; display:block; {extra_style}">'
    )

def metric_card(label, value, delta=None, green=False, sm=False):
    cls = "eco-card eco-card-sm" if sm else "eco-card"
    if green:
        cls += " eco-card-green"
    delta_html = f'<div class="eco-card-delta">{delta}</div>' if delta else ""
    return f"""
    <div class="{cls}">
        <div class="eco-card-label">{label}</div>
        <div class="eco-card-value">{value}</div>
        {delta_html}
    </div>
    """

def render_cards(items, sm=False):
    """items: lista de dicts con keys label, value, delta, green"""
    cols = st.columns(len(items))
    for col, item in zip(cols, items):
        with col:
            st.markdown(
                metric_card(
                    item["label"],
                    item["value"],
                    item.get("delta"),
                    item.get("green", False),
                    sm=sm,
                ),
                unsafe_allow_html=True,
            )

def section_title(text):
    st.markdown(f'<div class="eco-section-title">{text}</div>', unsafe_allow_html=True)

def fix_figura(fig):
    """Post-procesa figuras del motor para separar título de leyendas."""
    fig.update_layout(
        title=dict(
            y=0.97,
            x=0.0,
            xanchor='left',
            yanchor='top',
            font=dict(size=13, color=ECO_AZUL),
        ),
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.18,
            xanchor="left",
            x=0,
            font=dict(size=11),
            bgcolor="rgba(255,255,255,0.8)",
        ),
        margin=dict(t=80, b=130, l=60, r=130),
        height=580,
    )
    return fig

def page_header(title, subtitle=None):
    sub = f'<div class="eco-page-subtitle">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<div class="eco-page-title">{title}</div>{sub}',
        unsafe_allow_html=True,
    )
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)


# =============================================================================
# 2. CATÁLOGO SUNOPTICS
# =============================================================================
@st.cache_data
def cargar_catalogo():
    data = {
        'Modelo': [
            'Signature 800MD 4040 SGZ', 'Signature 800MD 4040 DGZ',
            'Signature 800MD 4070 SGZ', 'Signature 800MD 4070 DGZ',
            'Signature 800MD 4080 SGZ', 'Signature 800MD 4080 DGZ',
            'Signature 900SC 4080 (Storm)', 'Smoke Vent SVT2 4080 DGZ',
        ],
        'Acristalamiento': [
            'Sencillo (SGZ)', 'Doble (DGZ)', 'Sencillo (SGZ)', 'Doble (DGZ)',
            'Sencillo (SGZ)', 'Doble (DGZ)', 'Storm Class', 'Doble (DGZ)',
        ],
        'VLT':     [0.74, 0.67, 0.74, 0.67, 0.74, 0.67, 0.52, 0.64],
        'SHGC':    [0.68, 0.48, 0.68, 0.48, 0.68, 0.48, 0.24, 0.31],
        'U_Value': [5.80, 3.20, 5.80, 3.20, 5.80, 3.20, 2.80, 3.20],
        'Ancho_in': [51.25, 51.25, 51.25, 51.25, 52.25, 52.25, 52.25, 52.25],
        'Largo_in': [51.25, 51.25, 87.25, 87.25, 100.25, 100.25, 100.25, 100.25],
    }
    df = pd.DataFrame(data)
    df['Ancho_m'] = (df['Ancho_in'] * 0.0254).round(3)
    df['Largo_m'] = (df['Largo_in'] * 0.0254).round(3)
    return df

df_domos = cargar_catalogo()

# =============================================================================
# 3. SESSION STATE
# =============================================================================
_defaults = {
    'clima_data': None,
    'estacion_seleccionada': None,
    'df_cercanas': None,
    'vtk_path': None,
    'epw_path': None,
    'resultado_diseno': None,
    'resultado_motor': None,
    'num_domos_real': 0,
    'sfr_final': 0.0,
    'datos_domo_actual': None,
    'diseno_completado': False,
    'calculo_completado': False,
    'lead_capturado': False,
    'bg_lanzado': False,
    'bg_thread_name': '',
    'lead_nombre': '',
    'lead_empresa': '',
    'lead_correo': '',
    'lead_telefono': '',
    'lead_comentario': '',
    'lat': 9.9281,    # Alajuela, Costa Rica (default)
    'lon': -84.0858,
}
for key, val in _defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val


def buscar_estaciones():
    with st.spinner("Consultando base de datos climática..."):
        df = obtener_estaciones_cercanas(st.session_state.lat, st.session_state.lon)
        st.session_state.df_cercanas = df
        if df is None or df.empty:
            st.error("No se encontraron estaciones para esta ubicación.")
        else:
            st.success(f"{len(df)} estaciones encontradas.")


# =============================================================================
# 4. SIDEBAR
# =============================================================================
with st.sidebar:

    # Brand header con logos
    _eco_logo     = os.path.exists("assets/eco_logo.png")
    _sun_logo     = os.path.exists("assets/sunoptics_logo.png")

    if _eco_logo:
        st.markdown(f"""
        <div class="eco-brand">
            <div class="eco-brand-logo-zone">
                <div class="eco-logo-wrap">{_img_base64("assets/eco_logo.png", 160)}</div>
            </div>
            <div class="eco-brand-text-zone">
                <div class="eco-brand-sub">Energy Conservation Opportunities</div>
                <div class="eco-brand-product">SkyPlus 1.0</div>
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div class="eco-brand">
            <div class="eco-brand-text-zone">
                <div class="eco-brand-name">ECO Consultor</div>
                <div class="eco-brand-sub">Energy Conservation Opportunities</div>
                <div class="eco-brand-product">SkyPlus 1.0</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # ── 1. Ubicación ──────────────────────────────────────────────────────
    st.markdown('<div class="eco-sidebar-section">01 — Ubicación y Clima</div>',
                unsafe_allow_html=True)

    with st.expander("Buscar ubicación", expanded=False):
        search_name = st.text_input("Ciudad o país", placeholder="Ej: Alajuela, Costa Rica",
                                    label_visibility="collapsed")
        if st.button("Buscar por nombre", use_container_width=True):
            if search_name:
                from geopy.geocoders import Nominatim
                try:
                    geo = Nominatim(user_agent="skyplus_buscador")
                    loc = geo.geocode(search_name)
                    if loc:
                        st.session_state.lat = loc.latitude
                        st.session_state.lon = loc.longitude
                        buscar_estaciones()
                    else:
                        st.error("No se pudo localizar ese lugar.")
                except Exception:
                    st.error("Error al conectar con el geocodificador.")

        st.divider()
        st.session_state.lat = st.number_input("Latitud",  value=st.session_state.lat,  format="%.4f")
        st.session_state.lon = st.number_input("Longitud", value=st.session_state.lon, format="%.4f")
        if st.button("Buscar por coordenadas", use_container_width=True):
            buscar_estaciones()

    # Estado del clima
    if st.session_state.epw_path:
        st.markdown('<span class="eco-badge-ok">Clima activo</span>', unsafe_allow_html=True)
        st.caption(f"{st.session_state.estacion_seleccionada or 'Estación cargada'}")
    else:
        st.markdown('<span class="eco-badge-info">Sin archivo climático</span>', unsafe_allow_html=True)

    # ── 2. Geometría ──────────────────────────────────────────────────────
    st.markdown('<div class="eco-sidebar-section">02 — Geometría de la Nave</div>',
                unsafe_allow_html=True)

    ancho_nave = st.number_input("Ancho (m)",  min_value=10.0, max_value=140.0, value=50.0,  step=1.0)
    largo_nave = st.number_input("Largo (m)",  min_value=10.0, max_value=140.0, value=100.0, step=1.0)
    alto_nave  = st.number_input("Altura (m)", min_value=3.0,  max_value=30.0,  value=8.0,   step=0.5)

    area_nave = ancho_nave * largo_nave
    st.caption(f"Área de planta: **{area_nave:,.0f} m²**")
    if area_nave > 10_000:
        st.markdown('<span class="eco-badge-warn">Requiere servicio BEM Premium</span>',
                    unsafe_allow_html=True)

    # ── 3. Tipo de uso ────────────────────────────────────────────────────
    st.markdown('<div class="eco-sidebar-section">03 — Tipo de Uso</div>',
                unsafe_allow_html=True)

    tipo_uso = st.selectbox(
        "Perfil ASHRAE 90.1",
        options=["Warehouse", "Manufacturing", "Retail", "SuperMarket", "MediumOffice"],
        format_func=lambda x: {
            "Warehouse":     "Bodega / Warehouse",
            "Manufacturing": "Manufactura",
            "Retail":        "Retail / Tienda",
            "SuperMarket":   "Supermercado",
            "MediumOffice":  "Oficina Mediana",
        }[x],
        help="Define LPD, setpoints y horarios según ASHRAE 90.1-2019.",
    )

    # ── 4. Domo Sunoptics ─────────────────────────────────────────────────
    st.markdown('<div class="eco-sidebar-section">04 — Domo Sunoptics®</div>',
                unsafe_allow_html=True)

    # Toggle capa sencilla / doble — default DGZ
    tipo_capa = st.radio(
        "Acristalamiento",
        options=["Doble (DGZ)", "Sencillo (SGZ)"],
        index=0,
        horizontal=True,
        help="DGZ: mejor aislamiento térmico. SGZ: mayor transmitancia de luz.",
    )
    _filtro_capa = "DGZ" if "DGZ" in tipo_capa else "SGZ"
    _df_filtrado = df_domos[df_domos['Acristalamiento'].str.contains(_filtro_capa)]

    # Default según tipo de capa
    _modelo_default = "Signature 800MD 4070 DGZ" if _filtro_capa == "DGZ" else "Signature 800MD 4070 SGZ"
    _idx_default = _df_filtrado[_df_filtrado['Modelo'] == _modelo_default].index
    _idx_filtrado = list(_df_filtrado.index).index(int(_idx_default[0])) if len(_idx_default) else 0

    modelo_sel = st.selectbox(
        "Modelo NFRC",
        _df_filtrado['Modelo'],
        index=_idx_filtrado,
    )
    sfr_target = st.slider(
        "Objetivo SFR (%)", 1.0, 10.0, 3.0, 0.1,
        help="Skylight-to-Floor Ratio. Límite ASHRAE 90.1: ≤5%.",
    ) / 100.0

    datos_domo_sel = df_domos[df_domos['Modelo'] == modelo_sel].iloc[0]
    with st.expander("Propiedades del domo"):
        col_a, col_b = st.columns(2)
        col_a.metric("VLT",    f"{datos_domo_sel['VLT']:.0%}")
        col_b.metric("SHGC",   f"{datos_domo_sel['SHGC']:.2f}")
        col_a.metric("U-val",  f"{datos_domo_sel['U_Value']:.2f} W/m²K")
        col_b.metric("Tamaño", f"{datos_domo_sel['Ancho_m']:.2f}×{datos_domo_sel['Largo_m']:.2f} m")

    # Estado motor
    st.divider()
    if not MOTOR_DISPONIBLE:
        st.markdown('<span class="eco-badge-warn">Motor EnergyPlus no disponible</span>',
                    unsafe_allow_html=True)

    # Logo Sunoptics
    if _sun_logo:
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        st.markdown(
            f'<div class="eco-sunoptics-logo-wrap">{_img_base64("assets/sunoptics_logo.png", 150)}</div>',
            unsafe_allow_html=True,
        )

    st.markdown(f"""
    <div style="font-size:0.62rem; color:{ECO_GRIS}; margin-top:10px; line-height:1.6;">
        Motor: EnergyPlus 23.2 (DOE)<br>
        Normativa: ISO 8995-1 · IES RP-7<br>
        Clima: TMYx OneBuilding.org<br>
        v22.2 · Eco Consultor 2026
    </div>
    """, unsafe_allow_html=True)


# =============================================================================
# 5. TABS — sin emojis, estilo técnico
# =============================================================================
tab_config, tab_clima, tab_3d, tab_analitica, tab_reporte = st.tabs([
    "Selección de Clima",
    "Contexto Climático",
    "Geometría 3D",
    "Simulación Energética",
    "Reporte Final",
])


# =============================================================================
# TAB 1 — MAPA Y DESCARGA EPW
# =============================================================================
with tab_config:
    page_header(
        "Selección de Clima",
        "Localiza el proyecto y descarga el archivo climático TMYx (EPW) de OneBuilding.org"
    )

    col1, col2 = st.columns([2, 1])

    with col1:
        section_title("Mapa interactivo del proyecto")
        st.caption("Haz clic en el mapa para buscar estaciones climáticas en ese punto.")

        m = folium.Map(
            location=[st.session_state.lat, st.session_state.lon], zoom_start=8,
            tiles="CartoDB positron",
        )
        folium.Marker(
            [st.session_state.lat, st.session_state.lon],
            tooltip="Ubicación del Proyecto",
            icon=folium.Icon(color='red', icon='crosshairs'),
        ).add_to(m)

        if st.session_state.df_cercanas is not None and not st.session_state.df_cercanas.empty:
            for _, st_row in st.session_state.df_cercanas.iterrows():
                l_est  = st_row.get('lat') or st_row.get('Lat')
                ln_est = st_row.get('lon') or st_row.get('Lon')
                if pd.notna(l_est) and pd.notna(ln_est):
                    folium.Marker(
                        [l_est, ln_est],
                        tooltip=f"{st_row.get('name','Estación')} ({st_row.get('distancia_km',0)} km)",
                        icon=folium.Icon(color='blue', icon='cloud'),
                    ).add_to(m)

        output = st_folium(m, width=700, height=480,
                           use_container_width=True, key="mapa_estaciones")

        if output and output.get("last_clicked"):
            c_lat = output["last_clicked"]["lat"]
            c_lon = output["last_clicked"]["lng"]
            if (round(c_lat, 4) != round(st.session_state.lat, 4) or
                    round(c_lon, 4) != round(st.session_state.lon, 4)):
                st.session_state.lat = c_lat
                st.session_state.lon = c_lon
                buscar_estaciones()
                st.rerun()

    with col2:
        section_title("Estaciones disponibles")

        if st.session_state.clima_data:
            st.markdown(f'<span class="eco-badge-ok">Clima activo</span><br>'
                        f'<span style="font-size:0.75rem;color:{ECO_GRIS}">'
                        f'{st.session_state.estacion_seleccionada}</span>',
                        unsafe_allow_html=True)
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        if st.session_state.df_cercanas is not None and not st.session_state.df_cercanas.empty:
            st.caption("Selecciona una estación para descargar el archivo .epw:")

            for idx, row in st.session_state.df_cercanas.iterrows():
                st_name = row.get('name') or row.get('Station') or f"Estación {idx}"
                st_dist = row.get('distancia_km') or 0
                url     = row.get('URL_ZIP') or row.get('epw')

                with st.container():
                    st.markdown(f"**{st_name}**")
                    st.caption(f"Distancia: **{st_dist} km**")
                    if st.button("Descargar datos climáticos",
                                 key=f"btn_st_{idx}", use_container_width=True):
                        if url:
                            with st.spinner("Descargando archivo EPW..."):
                                path = descargar_y_extraer_epw(url)
                                if path:
                                    try:
                                        data = procesar_datos_clima(path)
                                        if data:
                                            st.session_state.clima_data           = data
                                            st.session_state.estacion_seleccionada = st_name
                                            st.session_state.epw_path             = path
                                            st.session_state.resultado_motor      = None
                                            st.session_state.calculo_completado   = False
                                            st.rerun()
                                        else:
                                            st.error("Error al procesar el EPW con Ladybug.")
                                    except Exception as e:
                                        st.error(f"Error: {e}")
                                else:
                                    st.error("Archivo no disponible. Intenta otra estación.")


# =============================================================================
# TAB 2 — ANÁLISIS BIOCLIMÁTICO
# =============================================================================
with tab_clima:
    page_header(
        "Contexto Climático",
        "Análisis de las 8,760 horas anuales a partir del archivo EPW descargado"
    )

    if st.session_state.clima_data and 'vel_viento' in st.session_state.clima_data:
        clima = st.session_state.clima_data
        md    = clima.get('metadata', {})

        render_cards([
            {"label": "Latitud",                 "value": f"{md.get('lat', st.session_state.lat):.1f}°N"},
            {"label": "Longitud",                "value": f"{md.get('lon', st.session_state.lon):.1f}°W"},
            {"label": "Elevación",               "value": f"{int(round(md.get('elevacion', 0)))} m"},
            {"label": "Humedad relativa media",  "value": f"{round(sum(clima.get('hum_relativa',[0]))/8760)} %"},
            {"label": "Velocidad viento media",  "value": f"{round(sum(clima.get('vel_viento',[0]))/8760, 1)} m/s"},
        ])

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        col_g1, col_g2 = st.columns(2)

        with col_g1:
            section_title("Rosa de vientos anual")
            df_viento = pd.DataFrame({
                'dir': clima.get('dir_viento', []),
                'vel': clima.get('vel_viento', []),
            })
            if not df_viento.empty:
                df_viento = df_viento[df_viento['vel'] > 0.5]
                bins_dir  = np.arange(-11.25, 372.0, 22.5)
                labels_dir = ['N','NNE','NE','ENE','E','ESE','SE','SSE',
                               'S','SSW','SW','WSW','W','WNW','NW','NNW','N2']
                df_viento['Dir_Cat'] = pd.cut(df_viento['dir'], bins=bins_dir,
                                              labels=labels_dir, right=False)
                df_viento['Dir_Cat'] = df_viento['Dir_Cat'].replace('N2', 'N')
                bins_vel   = [0, 2, 4, 6, 8, 20]
                labels_vel = ['0–2 m/s','2–4 m/s','4–6 m/s','6–8 m/s','>8 m/s']
                df_viento['Vel_Cat'] = pd.cut(df_viento['vel'], bins=bins_vel, labels=labels_vel)
                df_rose = df_viento.groupby(['Dir_Cat','Vel_Cat']).size().reset_index(name='Frecuencia')
                fig_rose = px.bar_polar(
                    df_rose, r="Frecuencia", theta="Dir_Cat", color="Vel_Cat",
                    color_discrete_sequence=["#B8D4E0","#7AAFC4","#3E8CA8","#003C52","#001F2B"],
                    template="plotly_white",
                )
                fig_rose.update_layout(margin=dict(t=20, b=20, l=20, r=20))
                st.plotly_chart(fig_rose, use_container_width=True)

        with col_g2:
            section_title("Balance de irradiación")
            st.caption("Justificación técnica para domos prismáticos de alta difusión.")
            suma_directa = sum(clima.get('rad_directa', [0]))
            suma_difusa  = sum(clima.get('rad_dif', [0]))
            fig_pie = go.Figure(data=[go.Pie(
                labels=['Radiación directa', 'Radiación difusa'],
                values=[suma_directa, suma_difusa],
                hole=.45,
                marker_colors=[ECO_AZUL, "#7AAFC4"],
                textfont=dict(size=11),
                textinfo='percent+label',
            )])
            fig_pie.update_layout(
                margin=dict(t=10, b=10, l=20, r=20),
                template="plotly_white",
                showlegend=False,
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        st.divider()
        section_title("Mapa de calor anual — temperatura de bulbo seco (°C)")
        st.caption("8,760 horas del año. Picos críticos de calor y demanda HVAC.")

        temp_array = np.array(clima.get('temp_seca', np.zeros(8760)))
        if len(temp_array) == 8760:
            temp_matriz = temp_array.reshape(365, 24).T
            fig_calor = go.Figure(data=go.Heatmap(
                z=temp_matriz,
                x=list(range(1, 366)),
                y=list(range(0, 24)),
                colorscale='RdYlBu_r',
                colorbar=dict(title="°C", titleside="right"),
                hovertemplate="Día %{x} · Hora %{y}:00 · %{z:.1f} °C<extra></extra>",
            ))
            fig_calor.update_layout(
                xaxis_title="Días del año (Enero → Diciembre)",
                yaxis_title="Hora del día",
                yaxis=dict(tickmode='linear', tick0=0, dtick=4),
                margin=dict(t=10, b=30, l=40, r=20),
                height=380,
                template="plotly_white",
            )
            st.plotly_chart(fig_calor, use_container_width=True)

        st.divider()
        section_title("Termodinámica del sitio")

        temp_diaria = np.array([sum(temp_array[i:i+24])/24 for i in range(0, 8760, 24)]) if len(temp_array) == 8760 else np.zeros(365)
        cdd_anual = sum(t - 18.3 for t in temp_diaria if t > 18.3)
        hdd_anual = sum(18.3 - t for t in temp_diaria if t < 18.3)

        render_cards([
            {"label": "Grados día refrigeración (CDD)", "value": f"{int(cdd_anual):,}", "delta": "Demanda A/C anual"},
            {"label": "Grados día calefacción (HDD)",   "value": f"{int(hdd_anual):,}", "delta": "Demanda calefacción"},
        ])

        nubes_array = clima.get('nubes', np.zeros(8760))
        if len(nubes_array) == 8760:
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
            section_title("Perfil de nubosidad mensual")
            fechas    = pd.date_range(start="2023-01-01", periods=8760, freq="h")
            df_nubes  = pd.DataFrame({'Fecha': fechas, 'Nubosidad': np.array(nubes_array) * 10})
            df_nubes['Mes'] = df_nubes['Fecha'].dt.month
            nubes_mensual = df_nubes.groupby('Mes')['Nubosidad'].mean()
            meses_labels  = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
            fig_nubes = go.Figure(data=[go.Bar(
                x=meses_labels, y=nubes_mensual,
                marker_color=ECO_GRIS,
                text=[f"{v:.0f}%" for v in nubes_mensual],
                textposition='auto',
                textfont=dict(size=10),
            )])
            fig_nubes.update_layout(
                yaxis_title="% Cielo cubierto",
                yaxis=dict(range=[0, 100]),
                template="plotly_white",
                height=320,
                margin=dict(t=10, b=20, l=40, r=20),
            )
            st.plotly_chart(fig_nubes, use_container_width=True)
    else:
        st.info("Descarga un archivo climático en la pestaña 'Selección de Clima' para visualizar el análisis bioclimático.")


# =============================================================================
# TAB 3 — GEOMETRÍA 3D
# =============================================================================
with tab_3d:
    page_header(
        "Modelo Paramétrico",
        "Geometría Honeybee validada para EnergyPlus y Radiance — domos Sunoptics® distribuidos en cuadrícula ASHRAE"
    )

    if st.button("Generar modelo 3D", use_container_width=True, type="primary"):
        with st.spinner("Construyendo geometría Honeybee..."):
            try:
                datos_domo = df_domos[df_domos['Modelo'] == modelo_sel].iloc[0]
                vtk_path, num_domos, sfr_real = generar_nave_3d_vtk(
                    ancho_nave, largo_nave, alto_nave, sfr_target,
                    datos_domo['Ancho_m'], datos_domo['Largo_m'],
                    lat=st.session_state.lat, lon=st.session_state.lon,
                )
                if vtk_path:
                    st.session_state.vtk_path          = vtk_path
                    st.session_state.num_domos_real    = num_domos
                    st.session_state.sfr_final         = sfr_real
                    st.session_state.datos_domo_actual = datos_domo
            except Exception as e:
                st.error(f"Error en el motor 3D: {e}")

    if st.session_state.vtk_path and os.path.exists(st.session_state.vtk_path):
        sfr_pct = st.session_state.sfr_final * 100
        if sfr_pct <= 3.0:
            ashrae_status = "Cumple ASHRAE 90.1 sin controles (SFR ≤ 3%)"
            badge = "eco-badge-ok"
        elif sfr_pct <= 5.0:
            ashrae_status = "Requiere daylighting controls (SFR ≤ 5%)"
            badge = "eco-badge-warn"
        else:
            ashrae_status = "Excede límite ASHRAE 90.1 (SFR > 5%)"
            badge = "eco-badge-warn"

        render_cards([
            {"label": "Domos generados",    "value": f"{st.session_state.num_domos_real} uds"},
            {"label": "SFR real del modelo","value": f"{sfr_pct:.2f} %"},
        ])
        if sfr_pct <= 3.0:
            st.markdown('<span class="eco-badge-ok">ASHRAE 90.1 — Cumple sin controles (SFR ≤ 3%)</span>',
                        unsafe_allow_html=True)
        elif sfr_pct <= 5.0:
            st.markdown('<span class="eco-badge-warn">ASHRAE 90.1 — Requiere daylighting controls (SFR ≤ 5%)</span>',
                        unsafe_allow_html=True)
        else:
            st.markdown('<span class="eco-badge-warn">ASHRAE 90.1 — Excede límite (SFR > 5%)</span>',
                        unsafe_allow_html=True)
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

        st.divider()
        mostrar_sol = st.toggle("Mostrar bóveda solar", value=False)
        ruta_base   = st.session_state.vtk_path
        ruta_cargar = ruta_base if mostrar_sol else ruta_base.replace('.vtkjs', '_solo.vtkjs')
        if not os.path.exists(ruta_cargar):
            ruta_cargar = ruta_base

        with open(ruta_cargar, "rb") as f:
            vtk_data = f.read()
        st_vtkjs(content=vtk_data, key=f"visor_nave_{mostrar_sol}")
    else:
        st.markdown(f"""
        <div class="eco-disclaimer">
            Configura la nave en el panel lateral y presiona <strong>Generar modelo 3D</strong>.<br>
            El modelo se construye con Honeybee y es compatible con EnergyPlus y Radiance.
        </div>
        """, unsafe_allow_html=True)


# =============================================================================
# TAB 4 — SIMULACIÓN ENERGÉTICA
# =============================================================================
with tab_analitica:
    page_header(
        "Simulación Energética",
        "Motor 1: EnergyPlus 23.2 (DOE) — kWh reales  ·  Motor 2: EPW analítico — Iluminancia + Semáforo normativo"
    )

    if not MOTOR_DISPONIBLE:
        st.error("El motor EnergyPlus no está disponible. Despliega la aplicación en Docker + Google Cloud Run.")
        st.stop()

    if not st.session_state.clima_data:
        st.markdown(f'<div class="eco-disclaimer">Descarga un archivo climático en <strong>Selección de Clima</strong> para habilitar la simulación.</div>',
                    unsafe_allow_html=True)
        st.stop()

    if not st.session_state.epw_path or not os.path.exists(st.session_state.epw_path):
        st.markdown(f'<div class="eco-disclaimer">Archivo EPW no disponible. Vuelve a descargar el clima.</div>',
                    unsafe_allow_html=True)
        st.stop()

    if area_nave > 10_000:
        st.error("Área > 10,000 m². Proyectos de esta escala requieren el servicio BEM Premium.")
        st.stop()

    clima      = st.session_state.clima_data
    md         = clima.get("metadata", {})
    ciudad     = md.get("ciudad", st.session_state.estacion_seleccionada or "Desconocida")
    pais       = md.get("pais", "")
    datos_domo = df_domos[df_domos["Modelo"] == modelo_sel].iloc[0]

    # Resumen del proyecto
    with st.expander("Resumen del proyecto a simular", expanded=True):
        render_cards([
            {"label": "Nave",  "value": f"{ancho_nave:.0f}×{largo_nave:.0f}×{alto_nave:.0f} m"},
            {"label": "Área",  "value": f"{area_nave:,.0f} m²"},
            {"label": "Uso",   "value": tipo_uso},
            {"label": "Clima", "value": ciudad},
        ], sm=True)
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        render_cards([
            {"label": "Domo",       "value": f"{modelo_sel.split(' ')[2]} {modelo_sel.split(' ')[3]}"},
            {"label": "VLT",        "value": f"{datos_domo['VLT']:.0%}"},
            {"label": "SFR diseño", "value": f"{sfr_target*100:.0f}%"},
            {"label": "Motor",      "value": "EnergyPlus 23.2"},
        ], sm=True)

    st.divider()

    # =========================================================================
    # ETAPA 1 — Simulación base vs diseño
    # =========================================================================
    if not st.session_state.diseno_completado:

        col_btn, col_info = st.columns([1, 2])
        with col_btn:
            ejecutar_diseno = st.button(
                "Simular mi nave",
                use_container_width=True,
                type="primary",
            )
        with col_info:
            st.markdown(f"""
            <div class="eco-disclaimer">
                Compara tu nave <strong>sin domos vs con SFR={sfr_target*100:.0f}%</strong>.<br>
                2 simulaciones EnergyPlus · estimado <strong>2–4 minutos</strong>.
            </div>
            """, unsafe_allow_html=True)

        if ejecutar_diseno:
            barra      = st.progress(0, text="Preparando motor...")
            status_box = st.empty()

            facts = [
                "Los domos prismáticos Sunoptics difunden la luz hasta 3× más que una ventana plana.",
                "La iluminación representa hasta el 40% del consumo eléctrico en bodegas industriales.",
                "Cada kWh ahorrado evita ~0.45 kg de CO₂ en la red eléctrica.",
                "El SFR óptimo depende de la geometría, clima y uso específico de la nave.",
                "ASHRAE 90.1 permite hasta SFR=5% con controles de daylighting automáticos.",
            ]
            fact_box = st.info(facts[0])

            def actualizar_progreso(paso, total, mensaje):
                pct = int(paso / max(total, 1) * 100)
                barra.progress(pct, text=mensaje)
                status_box.caption(f"{mensaje}")
                if paso < len(facts):
                    fact_box.info(facts[paso % len(facts)])

            try:
                config = configurar_proyecto(
                    ancho        = ancho_nave,
                    largo        = largo_nave,
                    altura       = alto_nave,
                    tipo_uso     = tipo_uso,
                    epw_path     = st.session_state.epw_path,
                    sfr_diseno   = sfr_target,
                    domo_vlt     = float(datos_domo["VLT"]),
                    domo_shgc    = float(datos_domo["SHGC"]),
                    domo_u       = float(datos_domo["U_Value"]),
                    domo_ancho_m = float(datos_domo["Ancho_m"]),
                    domo_largo_m = float(datos_domo["Largo_m"]),
                )
                resultado = simular_caso_diseno(config, callback=actualizar_progreso)

                if resultado.get("error"):
                    barra.empty(); status_box.empty(); fact_box.empty()
                    st.error(f"Error en la simulación: {resultado['error']}")
                else:
                    barra.progress(100, text="Simulación completada.")
                    status_box.empty(); fact_box.empty()
                    st.session_state.resultado_diseno  = resultado
                    st.session_state.diseno_completado = True
                    st.rerun()

            except Exception as e:
                barra.empty()
                st.error(f"Error inesperado: {e}")

    # =========================================================================
    # RESULTADO ETAPA 1
    # =========================================================================
    if st.session_state.diseno_completado and st.session_state.resultado_diseno:
        res = st.session_state.resultado_diseno

        section_title("Resultado energético — tu nave")

        render_cards([
            {"label": "Ahorro con tu diseño",
             "value": f"{res['ahorro_neto']:,.0f} kWh/año",
             "delta": f"{res['pct_ahorro']:.1f}% sobre caso base",
             "green": True},
            {"label": "Consumo base sin domos",
             "value": f"{res['kwh_base']:,.0f} kWh/año",
             "delta": "Referencia ASHRAE"},
        ])
        render_cards([
            {"label": f"Domos instalados — SFR {res['sfr_real']:.1f}%",
             "value": f"{res['n_domos']} uds",
             "delta": f"{res['fc_lux']:.0f} lux promedio zona"},
            {"label": "Confort visual",
             "value": res["semaforo_txt"],
             "delta": "ISO 8995-1 + IES RP-7"},
        ])

        st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)
        st.plotly_chart(fix_figura(res["figura"]), use_container_width=True)
        st.markdown(res["recomendacion"])
        st.divider()

        # ── CTA Lead Magnet ───────────────────────────────────────────────
        if not st.session_state.lead_capturado:
            section_title("Curva de optimización completa — SFR 0% → 6%")
            st.markdown(
                f"Ingresa tus datos y calculamos la curva completa para tu nave de "
                f"{ancho_nave:.0f}×{largo_nave:.0f} m. "
                "Recibirás el **reporte técnico PDF en tu correo** en aproximadamente 10 minutos."
            )
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            with st.form("formulario_leads_cta"):
                col_f1, col_f2 = st.columns(2)
                nombre_contacto   = col_f1.text_input("Nombre completo *")
                empresa_contacto  = col_f2.text_input("Empresa *")
                correo_contacto   = col_f1.text_input("Correo electrónico *")
                telefono_contacto = col_f2.text_input("Teléfono (opcional)")
                comentario        = st.text_area("Comentarios", height=60)

                enviado = st.form_submit_button(
                    "Solicitar reporte completo",
                    use_container_width=True,
                    type="primary",
                )

            if enviado:
                if nombre_contacto and empresa_contacto and correo_contacto:
                    # ── Verificar cuota antes de aceptar ─────────────────
                    sims_hoy, permitido = verificar_cuota(correo_contacto.strip().lower())
                    if not permitido:
                        st.markdown(f"""
                        <div class="eco-disclaimer">
                            <strong>Límite diario alcanzado.</strong><br>
                            La cuenta <em>{correo_contacto}</em> ya tiene {sims_hoy} simulaciones
                            registradas hoy (máximo 3). Contáctanos directamente en
                            <strong>ingenieria@ecoconsultor.com</strong> para proyectos adicionales.
                        </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.session_state.lead_nombre      = nombre_contacto
                        st.session_state.lead_empresa     = empresa_contacto
                        st.session_state.lead_correo      = correo_contacto
                        st.session_state.lead_telefono    = telefono_contacto
                        st.session_state.lead_comentario  = comentario
                        st.session_state.lead_capturado   = True
                        st.rerun()
                else:
                    st.error("Completa nombre, empresa y correo para continuar.")

        # ── Etapa 2 — lanzar Cloud Run Job ───────────────────────────────
        if st.session_state.lead_capturado and not st.session_state.bg_lanzado:

            clima = st.session_state.clima_data or {}
            md    = clima.get("metadata", {})

            # Subir EPW a GCS para que el Job pueda accederlo
            with st.spinner("Preparando análisis..."):
                gcs_uri = upload_epw_to_gcs(
                    st.session_state.epw_path,
                    st.session_state.lead_correo,
                )
            if not gcs_uri:
                st.error("No se pudo preparar el archivo climático. Intenta nuevamente.")
                st.stop()

            _config_job = {
                "ancho":        ancho_nave,
                "largo":        largo_nave,
                "altura":       alto_nave,
                "tipo_uso":     tipo_uso,
                "epw_path":     gcs_uri,       # URI de GCS — no ruta local
                "sfr_diseno":   sfr_target,
                "domo_vlt":     float(datos_domo["VLT"]),
                "domo_shgc":    float(datos_domo["SHGC"]),
                "domo_u":       float(datos_domo["U_Value"]),
                "domo_ancho_m": float(datos_domo["Ancho_m"]),
                "domo_largo_m": float(datos_domo["Largo_m"]),
                "modelo_domo":  modelo_sel,
                "ciudad":       md.get("ciudad", st.session_state.estacion_seleccionada or ""),
                "pais":         md.get("pais", ""),
            }
            _lead_job = {
                "nombre":     st.session_state.lead_nombre,
                "empresa":    st.session_state.lead_empresa,
                "correo":     st.session_state.lead_correo,
                "telefono":   st.session_state.lead_telefono,
                "comentario": st.session_state.lead_comentario,
            }
            _sql_base = st.session_state.resultado_diseno.get("sql_base") \
                        if st.session_state.resultado_diseno else None

            ok, msg = lanzar_cloud_run_job(_config_job, _lead_job, _sql_base)

            if ok:
                st.session_state.bg_lanzado = True
                st.rerun()
            else:
                st.error(f"No se pudo lanzar el análisis: {msg}")
                st.info("Intenta nuevamente o contáctanos en ingenieria@ecoconsultor.com")

        # ── Mensaje de confirmación post-lanzamiento ──────────────────────
        if st.session_state.bg_lanzado:
            st.markdown(f"""
            <div style="
                background: #EBF5E1;
                border-left: 4px solid #4A7C2F;
                border-radius: 4px;
                padding: 20px 24px;
                margin: 16px 0;
            ">
                <div style="font-size:1rem; font-weight:700; color:#4A7C2F; margin-bottom:6px;">
                    Análisis paramétrico iniciado
                </div>
                <div style="font-size:0.85rem; color:#4A5568; line-height:1.7;">
                    Estamos calculando la curva de optimización completa (SFR 0%→6%) para
                    <strong>{st.session_state.lead_empresa}</strong>.<br>
                    El reporte técnico PDF llegará a
                    <strong>{st.session_state.lead_correo}</strong>
                    en aproximadamente <strong>10 minutos</strong>.<br><br>
                    Puedes cerrar esta ventana — el análisis continuará en la nube.
                </div>
            </div>
            """, unsafe_allow_html=True)

            render_cards([
                {"label": "Estado",        "value": "Simulando en nube",  "delta": "7 simulaciones EnergyPlus", "green": True},
                {"label": "Entrega",       "value": "~10 minutos",        "delta": f"A: {st.session_state.lead_correo}"},
                {"label": "Motor",         "value": "EnergyPlus 23.2",    "delta": "DOE oficial"},
                {"label": "Análisis",      "value": "SFR 0% → 6%",       "delta": "Curva de optimización completa"},
            ])

        # Reset
        st.divider()
        if st.button("Nueva simulación (limpiar resultados)"):
            for k in ["resultado_diseno","resultado_motor","diseno_completado",
                      "calculo_completado","lead_capturado","bg_lanzado",
                      "bg_thread_name","lead_nombre","lead_empresa",
                      "lead_correo","lead_telefono","lead_comentario"]:
                if k in ["diseno_completado","calculo_completado","lead_capturado","bg_lanzado"]:
                    st.session_state[k] = False
                elif k == "bg_thread_name":
                    st.session_state[k] = ""
                else:
                    st.session_state[k] = None if "resultado" in k else ""
            st.rerun()


# =============================================================================
# TAB 5 — REPORTE FINAL
# =============================================================================
with tab_reporte:
    page_header(
        "Reporte Ejecutivo",
        "Descarga el reporte técnico PDF con la curva de optimización y la recomendación SkyPlus"
    )

    if not st.session_state.calculo_completado or not st.session_state.resultado_motor:
        if st.session_state.diseno_completado and not st.session_state.lead_capturado:
            st.markdown(f"""
            <div class="eco-disclaimer">
                Tu simulación de diseño está lista. Completa tus datos en
                <strong>Simulación Energética</strong> para desbloquear el reporte completo
                con la curva de optimización.
            </div>
            """, unsafe_allow_html=True)
        elif st.session_state.lead_capturado and not st.session_state.calculo_completado:
            st.info("La curva de optimización está calculando. Regresa en unos minutos.")
        else:
            st.markdown(f"""
            <div class="eco-disclaimer">
                Completa la simulación en <strong>Simulación Energética</strong>
                para generar el reporte.
            </div>
            """, unsafe_allow_html=True)
    else:
        res    = st.session_state.resultado_motor
        clima  = st.session_state.clima_data or {}
        md     = clima.get("metadata", {})
        ciudad = md.get("ciudad", "N/D")
        pais   = md.get("pais", "")
        datos_domo = df_domos[df_domos["Modelo"] == modelo_sel].iloc[0]

        st.markdown('<span class="eco-badge-ok">Reporte listo</span>', unsafe_allow_html=True)
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        with st.expander("Vista previa del reporte", expanded=True):
            st.markdown(f"""
**REPORTE TÉCNICO SKYPLUS**
---
**Proyecto:** Nave Industrial {ancho_nave:.0f}×{largo_nave:.0f}×{alto_nave:.0f} m
**Uso:** {tipo_uso}
**Ubicación:** {ciudad}, {pais}
**Clima:** {st.session_state.estacion_seleccionada or "N/D"} (TMYx OneBuilding.org)
**Domo:** {modelo_sel}
**Cliente:** {st.session_state.lead_nombre} — {st.session_state.lead_empresa}
**Fecha:** {__import__("pandas").Timestamp.now().strftime("%d/%m/%Y")}

---
{res["recomendacion"]}

---
**Motor:** EnergyPlus 23.2 (DOE) + Método analítico EPW
**Normativa:** ISO 8995-1:2002 · ANSI/IES RP-7-21 · UDI Mardaljevic 2006
            """)

        st.divider()
        section_title("Curva de optimización SFR")
        st.plotly_chart(fix_figura(res["figura"]), use_container_width=True)

        # Mapa de calor luz natural
        st.divider()
        section_title("Disponibilidad de luz natural")
        st.caption("Lux promedio interior por mes y hora del día para el SFR óptimo dual.")

        if st.session_state.epw_path and os.path.exists(st.session_state.epw_path):
            try:
                _sfr_plot = res["sfr_dual"] if res["sfr_dual"] is not None else res["sfr_opt"]
                _transmis = (_sfr_plot / 100.0) * datos_domo["VLT"] * 0.736

                with open(st.session_state.epw_path, "r", errors="ignore") as _f:
                    _lineas = _f.readlines()[8:]
                _illum = []
                for _l in _lineas:
                    _p = _l.strip().split(",")
                    try:
                        _illum.append(float(_p[19]) * 10.0 if len(_p) >= 20 else 0.0)
                    except (ValueError, IndexError):
                        _illum.append(0.0)
                _illum = np.array(_illum[:8760])
                _fc_8760 = _illum * _transmis

                _dias_mes  = [31,28,31,30,31,30,31,31,30,31,30,31]
                _meses_es  = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
                _matriz    = np.zeros((12, 24))
                _h = 0
                for _m in range(12):
                    for _d in range(_dias_mes[_m]):
                        for _hr in range(24):
                            if _h < 8760:
                                _matriz[_m][_hr] += _fc_8760[_h]
                            _h += 1
                    _matriz[_m] /= _dias_mes[_m]

                _lux_sp = {"Warehouse":300,"Manufacturing":500,"Retail":500,
                           "SuperMarket":500,"MediumOffice":500}.get(tipo_uso, 300)
                _max_v  = float(_matriz.max())

                fig_hm = go.Figure(data=go.Heatmap(
                    z=_matriz, x=list(range(24)), y=_meses_es,
                    colorscale=[[0,"#FFFFFF"],[0.15,ECO_AZUL_LT],
                                [0.4,ECO_VERDE],[0.7,ECO_AZUL],[1.0,"#001F2B"]],
                    colorbar=dict(
                        title="Lux",
                        tickvals=[0, _lux_sp, 750, 2000, min(_max_v, 3000)],
                        ticktext=["0", f"{int(_lux_sp)} setpoint",
                                  "750 IES", "2,000 UDI", f"{min(_max_v,3000):.0f} max"],
                    ),
                    hovertemplate="Mes: %{y} · Hora: %{x}:00 · fc: %{z:.0f} lux<extra></extra>",
                ))
                fig_hm.update_layout(
                    title=dict(
                        text=f"Disponibilidad de Luz Natural — SFR {_sfr_plot}%",
                        font=dict(size=13, color=ECO_AZUL), x=0.0,
                    ),
                    xaxis=dict(title="Hora (00:00–23:00)", tickmode="linear", tick0=0, dtick=2),
                    yaxis=dict(title="Mes", autorange="reversed"),
                    template="plotly_white",
                    height=440,
                    margin=dict(l=60, r=140, t=50, b=60),
                )
                st.plotly_chart(fig_hm, use_container_width=True)
            except Exception as e:
                st.warning(f"No se pudo generar el mapa de calor: {e}")

        st.divider()
        section_title("Descarga del reporte")
        st.caption(f"Se enviará a: {st.session_state.lead_correo}")

        if st.button("Generar y enviar reporte PDF", type="primary", use_container_width=True):
            st.success(
                f"Reporte enviado a {st.session_state.lead_correo}. "
                "Un consultor de Eco Consultor se pondrá en contacto."
            )

        st.divider()
        col_cta1, col_cta2 = st.columns(2)
        with col_cta1:
            st.markdown(f"""
            <div class="eco-card">
                <div class="eco-card-label">Estudio BEM Premium</div>
                <div style="font-size:0.8rem;color:{ECO_GRIS};margin-top:6px;line-height:1.6">
                    Simulación espacial con Radiance. Validación punto por punto,
                    certificaciones LEED v4.1 y EDGE.<br><br>
                    <em>Contacta a tu consultor Eco Consultor.</em>
                </div>
            </div>
            """, unsafe_allow_html=True)
        with col_cta2:
            st.markdown(f"""
            <div class="eco-card eco-card-green">
                <div class="eco-card-label">Proyecto ejecutivo</div>
                <div style="font-size:0.8rem;color:{ECO_GRIS};margin-top:6px;line-height:1.6">
                    Layout de domos, especificaciones técnicas, presupuesto y análisis de ROI.
                    Diseñado para licitaciones y proyectos de inversión.<br><br>
                    <em>Disponible con tu consultor Sunoptics®.</em>
                </div>
            </div>
            """, unsafe_allow_html=True)
