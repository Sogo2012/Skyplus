# app.py
# =============================================================================
# SKYPLUS 1.0 — Eco Consultor | Sunoptics LATAM
# Motor: EnergyPlus 23.2 (DOE) + EPW analítico (ISO 8995-1 / IES RP-7)
# Diseño: Identidad ECO — Pantone 309C / 575C / 432C
# =============================================================================

import os
import time
import logging
import traceback
import streamlit as st

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SKYPLUS] %(message)s")
_log = logging.getLogger("skyplus")
import pandas as pd
import numpy as np
import folium
import plotly.graph_objects as go
import plotly.express as px
from streamlit_folium import st_folium
# streamlit_vtkjs removido — reemplazado por Plotly 3D

# Módulos locales
from geometry_utils import generar_nave_3d_vtk
from i18n import T, fmt_length, fmt_area, fmt_illuminance, fmt_energy, fmt_uvalue, fmt_dims, get_setpoint, get_occupancy_label, get_compliance_label, SETPOINTS, CONVERSION
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
ECO_AZUL    = "#003C52"
ECO_VERDE   = "#4A7C2F"
ECO_GRIS    = "#4A5568"
ECO_AZUL_LT = "#E8F0F3"
ECO_VERDE_LT= "#EBF5E1"
ECO_GRIS_LT = "#F4F5F6"
ECO_LINEA   = "#CBD5E0"

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
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}
    .main {{ background-color: {ECO_GRIS_LT}; }}
    header[data-testid="stHeader"] {{ background-color: {ECO_AZUL}; }}
    [data-testid="stSidebar"] {{ background-color: #FFFFFF; border-right: 1px solid {ECO_LINEA}; }}
    [data-testid="stSidebarCollapsedControl"] {{ background-color: {ECO_AZUL} !important; }}
    [data-testid="stSidebarCollapsedControl"] button {{ background-color: transparent !important; border: none !important; }}
    [data-testid="stSidebarCollapsedControl"] button svg,
    [data-testid="stSidebarCollapsedControl"] button svg path {{ stroke: #F0F2F6 !important; fill: #F0F2F6 !important; color: #F0F2F6 !important; }}
    [data-testid="stSidebarContent"] button[kind="header"] svg,
    button[data-testid="baseButton-header"] svg {{ stroke: #F0F2F6 !important; color: #F0F2F6 !important; }}
    [data-testid="stSidebar"] label, [data-testid="stSidebar"] .stMarkdown p {{ color: {ECO_GRIS}; font-size: 0.82rem; }}
    .stTabs [data-baseweb="tab-list"] {{ gap: 0; border-bottom: 2px solid {ECO_LINEA}; background-color: white; }}
    .stTabs [data-baseweb="tab"] {{ height: 40px; padding: 0 20px; font-size: 0.82rem; font-weight: 500; letter-spacing: 0.03em; text-transform: uppercase; color: {ECO_GRIS}; border: none; background: transparent; }}
    .stTabs [aria-selected="true"] {{ color: {ECO_AZUL} !important; border-bottom: 2px solid {ECO_AZUL} !important; background: transparent !important; font-weight: 600; }}
    .stButton > button[kind="primary"] {{ background-color: {ECO_AZUL}; color: white; border: none; border-radius: 3px; font-size: 0.85rem; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; padding: 0.65em 1.5em; transition: background 0.2s; }}
    .stButton > button[kind="primary"]:hover {{ background-color: #005070; }}
    .stButton > button:not([kind="primary"]) {{ background-color: white; color: {ECO_AZUL}; border: 1px solid {ECO_AZUL}; border-radius: 3px; font-size: 0.82rem; font-weight: 500; }}
    [data-testid="stMetricValue"] {{ font-size: 1.5rem !important; font-weight: 600 !important; color: {ECO_AZUL} !important; }}
    [data-testid="stMetricLabel"] {{ font-size: 0.72rem !important; font-weight: 500 !important; text-transform: uppercase; letter-spacing: 0.06em; color: {ECO_GRIS} !important; }}
    [data-testid="stMetricDelta"] {{ font-size: 0.75rem !important; color: {ECO_VERDE} !important; }}
    .eco-card {{ background: white; border-radius: 4px; border: 1px solid {ECO_LINEA}; border-left: 3px solid {ECO_AZUL}; padding: 12px 14px; margin-bottom: 8px; min-width: 0; box-sizing: border-box; height: 100%; }}
    .eco-card-label {{ font-size: 0.63rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.07em; color: {ECO_GRIS}; margin-bottom: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .eco-card-value {{ font-size: clamp(0.85rem, 2vw, 1.3rem); font-weight: 700; color: {ECO_AZUL}; line-height: 1.2; word-break: break-word; overflow-wrap: break-word; }}
    .eco-card-delta {{ font-size: 0.67rem; color: {ECO_VERDE}; margin-top: 4px; font-weight: 500; word-break: break-word; }}
    .eco-card-green {{ border-left-color: {ECO_VERDE}; }}
    .eco-card-green .eco-card-value {{ color: {ECO_VERDE}; }}
    .eco-card-sm .eco-card-value {{ font-size: clamp(0.75rem, 1.5vw, 1.05rem); }}
    .eco-card-sm .eco-card-label {{ font-size: 0.58rem; }}
    .eco-section-title {{ font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; color: {ECO_GRIS}; border-bottom: 1px solid {ECO_LINEA}; padding-bottom: 6px; margin-bottom: 14px; margin-top: 4px; }}
    .eco-page-title {{ font-size: 1.25rem; font-weight: 700; color: {ECO_AZUL}; letter-spacing: -0.01em; }}
    .eco-page-subtitle {{ font-size: 0.78rem; color: {ECO_GRIS}; margin-top: 2px; }}
    .eco-brand {{ background: {ECO_AZUL}; margin: -1rem -1rem 1.2rem -1rem; padding: 0; border-bottom: 3px solid {ECO_VERDE}; }}
    .eco-brand-logo-zone {{ background: #FFFFFF; padding: 12px 20px 10px 20px; border-bottom: 1px solid {ECO_LINEA}; }}
    .eco-brand-text-zone {{ padding: 10px 20px 12px 20px; }}
    .eco-logo-wrap {{ max-width: 160px; margin-bottom: 0; }}
    .eco-logo-wrap img {{ max-width: 100% !important; height: auto !important; }}
    .eco-sunoptics-logo-wrap {{ max-width: 140px; margin: 0 auto 4px 0; }}
    .eco-sunoptics-logo-wrap img {{ width: 100% !important; height: auto !important; }}
    .eco-brand-name {{ font-size: 1.1rem; font-weight: 700; color: white; letter-spacing: 0.05em; }}
    .eco-brand-sub, .eco-brand-sub-dark {{ font-size: 0.68rem; color: rgba(255,255,255,0.65); letter-spacing: 0.04em; margin-top: 1px; }}
    .eco-brand-product, .eco-brand-product-dark {{ font-size: 0.7rem; font-weight: 600; color: {ECO_VERDE}; letter-spacing: 0.12em; text-transform: uppercase; margin-top: 6px; }}
    .eco-sidebar-section {{ font-size: 0.65rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; color: {ECO_GRIS}; background: {ECO_GRIS_LT}; border-left: 2px solid {ECO_AZUL}; padding: 5px 8px; margin: 14px 0 8px 0; }}
    .eco-badge-ok {{ display: inline-block; background: {ECO_VERDE_LT}; color: {ECO_VERDE}; border: 1px solid {ECO_VERDE}; border-radius: 2px; font-size: 0.65rem; font-weight: 600; letter-spacing: 0.05em; padding: 2px 7px; text-transform: uppercase; }}
    .eco-badge-warn {{ display: inline-block; background: #FFF8E1; color: #B7791F; border: 1px solid #F6AD55; border-radius: 2px; font-size: 0.65rem; font-weight: 600; padding: 2px 7px; text-transform: uppercase; }}
    .eco-badge-info {{ display: inline-block; background: {ECO_AZUL_LT}; color: {ECO_AZUL}; border: 1px solid #90B8C8; border-radius: 2px; font-size: 0.65rem; font-weight: 600; padding: 2px 7px; text-transform: uppercase; }}
    .eco-disclaimer {{ background: {ECO_AZUL_LT}; border-left: 3px solid {ECO_AZUL}; border-radius: 2px; padding: 10px 14px; font-size: 0.75rem; color: {ECO_GRIS}; line-height: 1.5; }}
    .pollination-logo, .ladybug-logo {{ display: none !important; }}
    div[title="Powered by Pollination"] {{ display: none !important; }}
    a[href*="pollination.cloud"] {{ display: none !important; }}
    a[href*="ladybug.tools"] {{ display: none !important; }}
    [data-testid="stDataFrame"] {{ border: 1px solid {ECO_LINEA}; border-radius: 4px; }}
    details summary {{ font-size: 0.8rem; font-weight: 600; color: {ECO_AZUL}; text-transform: uppercase; letter-spacing: 0.04em; }}
    </style>
""", unsafe_allow_html=True)


# =============================================================================
# HELPER — Cards HTML
# =============================================================================
def _img_base64(path, max_width, extra_style=""):
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
    fig.update_layout(
        title=dict(y=0.97, x=0.0, xanchor='left', yanchor='top', font=dict(size=13, color=ECO_AZUL)),
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0, font=dict(size=11), bgcolor="rgba(255,255,255,0.8)"),
        margin=dict(t=80, b=130, l=60, r=130),
        height=580,
    )
    return fig

def page_header(title, subtitle=None):
    sub = f'<div class="eco-page-subtitle">{subtitle}</div>' if subtitle else ""
    st.markdown(f'<div class="eco-page-title">{title}</div>{sub}', unsafe_allow_html=True)
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)


# =============================================================================
# 2. CATÁLOGO SUNOPTICS
# =============================================================================
@st.cache_data
def cargar_catalogo():
    _SGZ_IP = 1.20;  _SGZ_SI = round(_SGZ_IP * 5.67826, 4)
    _DGZ_IP = 0.72;  _DGZ_SI = round(_DGZ_IP * 5.67826, 4)
    data = {
        'Modelo': [
            'Signature 800MD 4040 SGZ', 'Signature 800MD 4040 DGZ',
            'Signature 800MD 4070 SGZ', 'Signature 800MD 4070 DGZ',
            'Signature 800MD 4080 SGZ', 'Signature 800MD 4080 DGZ',
            'Signature 900SC 4080 (Storm)', 'Smoke Vent SVT2 4080 DGZ',
        ],
        'Acristalamiento': [
            'Sencillo (SGZ)', 'Doble (DGZ)', 'Sencillo (SGZ)', 'Doble (DGZ)',
            'Sencillo (SGZ)', 'Doble (DGZ)', 'Storm Class',    'Doble (DGZ)',
        ],
        'VLT':        [0.74,    0.67,    0.74,    0.67,    0.74,    0.67,    0.52,    0.64   ],
        'SHGC':       [0.68,    0.48,    0.68,    0.48,    0.68,    0.48,    0.24,    0.31   ],
        'U_Value':    [_SGZ_SI, _DGZ_SI, _SGZ_SI, _DGZ_SI, _SGZ_SI, _DGZ_SI, _DGZ_SI, _DGZ_SI],
        'U_Value_IP': [_SGZ_IP, _DGZ_IP, _SGZ_IP, _DGZ_IP, _SGZ_IP, _DGZ_IP, _DGZ_IP, _DGZ_IP],
        'Ancho_in':   [51.25,   51.25,   51.25,   51.25,   52.25,   52.25,   52.25,   52.25  ],
        'Largo_in':   [51.25,   51.25,   87.25,   87.25,   100.25,  100.25,  100.25,  100.25 ],
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
    'lat': 9.9281,
    'lon': -84.0858,
    'lang': 'ES',
    'units': 'metric',
}
for key, val in _defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val

_L = st.session_state.get("lang",  "ES")
_U = st.session_state.get("units", "metric")

_ancho_usr = st.session_state.get("_ancho_usr")
_largo_usr = st.session_state.get("_largo_usr")
_alto_usr  = st.session_state.get("_alto_usr")


def buscar_estaciones():
    with st.spinner(T("spinner_climate", _L)):
        df = obtener_estaciones_cercanas(st.session_state.lat, st.session_state.lon)
        st.session_state.df_cercanas = df
        if df is None or df.empty:
            st.error("No se encontraron estaciones para esta ubicación.")
        else:
            st.success(f"{len(df)} {T('stations_found', _L)}")


# =============================================================================
# 4. SIDEBAR
# =============================================================================

if "_ancho_nave" not in st.session_state:
    st.session_state["_ancho_nave"] = 50.0
if "_largo_nave" not in st.session_state:
    st.session_state["_largo_nave"] = 100.0
if "_alto_nave" not in st.session_state:
    st.session_state["_alto_nave"] = 8.0
if "_area_nave" not in st.session_state:
    st.session_state["_area_nave"] = 5000.0
if "_sfr_target" not in st.session_state:
    st.session_state["_sfr_target"] = 0.03
if "_modelo_sel" not in st.session_state:
    st.session_state["_modelo_sel"] = ""
if "_tipo_uso" not in st.session_state:
    st.session_state["_tipo_uso"] = "Warehouse"

with st.sidebar:

    _eco_logo = os.path.exists("assets/eco_logo.png")
    _sun_logo = os.path.exists("assets/sunoptics_logo.png")

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

    st.markdown("""
    <style>
    div[data-testid="stToggle"] { display: flex !important; align-items: center !important; gap: 6px !important; min-width: 0 !important; flex-wrap: nowrap !important; }
    div[data-testid="stToggle"] label { font-size: 13px !important; font-weight: 600 !important; white-space: nowrap !important; overflow: visible !important; min-width: 0 !important; }
    div[data-testid="stToggle"] p { white-space: nowrap !important; font-size: 13px !important; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

    _is_english = st.toggle(
        "ES  /  EN",
        value=(st.session_state.lang == "EN"),
        key="lang_toggle_v2",
        help="Cambiar idioma / Switch language",
    )

    if _is_english:
        st.session_state.lang  = "EN"
        st.session_state.units = "imperial"
    else:
        st.session_state.lang  = "ES"
        st.session_state.units = "metric"

    _L = st.session_state.lang
    _U = st.session_state.units
    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

    # ── 1. Ubicación ──────────────────────────────────────────────────────
    st.markdown(f'<div class="eco-sidebar-section">{T("sidebar_01", _L)}</div>', unsafe_allow_html=True)

    with st.expander(T("search_location", _L), expanded=False):
        search_name = st.text_input("Ciudad o país", placeholder="Ej: Alajuela, Costa Rica", label_visibility="collapsed")
        if st.button(T("search_by_name", _L), use_container_width=True):
            if search_name:
                from geopy.geocoders import Nominatim
                from geopy.exc import GeocoderTimedOut, GeocoderServiceError
                try:
                    geo = Nominatim(user_agent="skyplus_ecoconsultor_v2", timeout=10)
                    loc = geo.geocode(search_name, language="es")
                    if loc:
                        st.session_state.lat = loc.latitude
                        st.session_state.lon = loc.longitude
                        buscar_estaciones()
                    else:
                        st.error(T("err_geocoder_notfound", _L))
                except GeocoderTimedOut:
                    st.error(T("err_geocoder_timeout", _L))
                except GeocoderServiceError as e:
                    st.error(f"Servicio de geocodificación no disponible: {e}. Usa las coordenadas manualmente.")
                except Exception as e:
                    st.error(f"Error inesperado: {type(e).__name__}: {e}")

        st.divider()
        st.session_state.lat = st.number_input(T("latitude", _L),  value=st.session_state.lat,  format="%.4f")
        st.session_state.lon = st.number_input(T("longitude", _L), value=st.session_state.lon, format="%.4f")
        if st.button(T("search_by_coords", _L), use_container_width=True):
            buscar_estaciones()

    if st.session_state.epw_path:
        st.markdown(f'<span class="eco-badge-ok">{T("climate_active", _L)}</span>', unsafe_allow_html=True)
        st.caption(f"{st.session_state.estacion_seleccionada or 'Estación cargada'}")
    else:
        st.markdown(f'<span class="eco-badge-info">{T("no_climate", _L)}</span>', unsafe_allow_html=True)

    # ── 2. Geometría ──────────────────────────────────────────────────────
    st.markdown(f'<div class="eco-sidebar-section">{T("sidebar_02", _L)}</div>', unsafe_allow_html=True)

    _FT2M = 1 / CONVERSION["m_to_ft"]
    _M2FT = CONVERSION["m_to_ft"]

    # ── FIX v22.6 — Keys separadas por sistema de unidades ───────────────
    # La VERDAD siempre se guarda en METROS en session_state["_ancho_usr"].
    # Cada sistema tiene su propia key → Streamlit nunca ve valor fuera de
    # rango → UnboundLocalError es imposible por diseño.
    # ─────────────────────────────────────────────────────────────────────

    # Leer la VERDAD MÉTRICA — siempre en metros, nunca en pies
    _a_m = float(st.session_state.get("_ancho_usr_base") or 50.0)
    _l_m = float(st.session_state.get("_largo_usr_base") or 100.0)
    _h_m = float(st.session_state.get("_alto_usr_base")  or 8.0)

    if _U == "imperial":
        # Convertir metros → pies para el widget, con clamping de seguridad
        _a_ft = float(max(33.0,  min(460.0, round(_a_m * _M2FT))))
        _l_ft = float(max(33.0,  min(460.0, round(_l_m * _M2FT))))
        _h_ft = float(max(10.0,  min(100.0, round(_h_m * _M2FT))))
        _ancho_disp = st.number_input(T("width_m",  _L), min_value=33.0,  max_value=460.0, value=_a_ft, step=1.0,  key="ni_ancho_imp")
        _largo_disp = st.number_input(T("length_m", _L), min_value=33.0,  max_value=460.0, value=_l_ft, step=1.0,  key="ni_largo_imp")
        _alto_disp  = st.number_input(T("height_m", _L), min_value=10.0,  max_value=100.0, value=_h_ft, step=1.0,  key="ni_alto_imp")
        _ancho_usr = _ancho_disp          # pies — lo que ve el usuario
        _largo_usr = _largo_disp
        _alto_usr  = _alto_disp
        ancho_nave = _ancho_disp * _FT2M  # metros — para EnergyPlus
        largo_nave = _largo_disp * _FT2M
        alto_nave  = _alto_disp  * _FT2M
    else:
        _a_m2 = float(max(10.0, min(140.0, _a_m)))
        _l_m2 = float(max(10.0, min(140.0, _l_m)))
        _h_m2 = float(max(3.0,  min(30.0,  _h_m)))
        _ancho_usr = st.number_input(T("width_m",  _L), min_value=10.0, max_value=140.0, value=_a_m2, step=1.0,  key="ni_ancho_met")
        _largo_usr = st.number_input(T("length_m", _L), min_value=10.0, max_value=140.0, value=_l_m2, step=1.0,  key="ni_largo_met")
        _alto_usr  = st.number_input(T("height_m", _L), min_value=3.0,  max_value=30.0,  value=_h_m2, step=0.5,  key="ni_alto_met")
        ancho_nave = _ancho_usr           # metros — idéntico para EnergyPlus
        largo_nave = _largo_usr
        alto_nave  = _alto_usr

    area_nave    = ancho_nave * largo_nave
    area_usr     = _ancho_usr * _largo_usr
    area_max_usr = 10_000 if _U == "metric" else 10_000 * CONVERSION["m2_to_ft2"]

    st.caption(f"{T('floor_area', _L)}: **{fmt_area(area_nave, _U)}**")
    if area_nave > 10_000:
        st.markdown(f'<span class="eco-badge-warn">{T("bem_required", _L)}</span>', unsafe_allow_html=True)

    # ── 3. Tipo de uso ────────────────────────────────────────────────────
    st.markdown(f'<div class="eco-sidebar-section">{T("sidebar_03", _L)}</div>', unsafe_allow_html=True)

    tipo_uso = st.selectbox(
        "ASHRAE 90.1",
        options=["Warehouse", "Manufacturing", "Retail", "SuperMarket", "MediumOffice"],
        format_func=lambda x: get_occupancy_label(x, _L),
        help="LPD, setpoints & schedules per ASHRAE 90.1-2019." if _L == "EN"
             else "Define LPD, setpoints y horarios según ASHRAE 90.1-2019.",
    )

    # ── 4. Domo Sunoptics ─────────────────────────────────────────────────
    st.markdown(f'<div class="eco-sidebar-section">{T("sidebar_04", _L)}</div>', unsafe_allow_html=True)

    tipo_capa = st.radio(
        "Acristalamiento",
        options=["Doble (DGZ)", "Sencillo (SGZ)"],
        index=0,
        horizontal=True,
        help="DGZ: mejor aislamiento térmico. SGZ: mayor transmitancia de luz.",
    )
    _filtro_capa = "DGZ" if "DGZ" in tipo_capa else "SGZ"
    _df_filtrado = df_domos[df_domos['Acristalamiento'].str.contains(_filtro_capa)]

    _modelo_default = "Signature 800MD 4070 DGZ" if _filtro_capa == "DGZ" else "Signature 800MD 4070 SGZ"
    _idx_default = _df_filtrado[_df_filtrado['Modelo'] == _modelo_default].index
    _idx_filtrado = list(_df_filtrado.index).index(int(_idx_default[0])) if len(_idx_default) else 0

    modelo_sel = st.selectbox(T("dome_model_select", _L), _df_filtrado['Modelo'], index=_idx_filtrado)
    sfr_target = st.slider(
        T("sfr_target", _L), 1.0, 10.0, 3.0, 0.1,
        help="Skylight-to-Floor Ratio. Límite ASHRAE 90.1: ≤5%.",
    ) / 100.0

    datos_domo_sel = df_domos[df_domos['Modelo'] == modelo_sel].iloc[0]
    with st.expander(T("skylight_props", _L)):
        st.write(f"**{T('vlt', _L)}:** {datos_domo_sel['VLT']:.0%}")
        st.write(f"**{T('shgc', _L)}:** {datos_domo_sel['SHGC']:.2f}")
        st.write(f"**{T('u_value', _L)}:** {fmt_uvalue(datos_domo_sel['U_Value'], _U)}")
        st.write(f"**{T('size', _L)}:** {fmt_length(datos_domo_sel['Ancho_m'], _U, 2)} × {fmt_length(datos_domo_sel['Largo_m'], _U, 2)}")

    st.divider()
    if not MOTOR_DISPONIBLE:
        st.markdown('<span class="eco-badge-warn">Motor EnergyPlus no disponible</span>', unsafe_allow_html=True)

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
        v22.3 · Eco Consultor 2026
    </div>
    """, unsafe_allow_html=True)

    # Persistir en session_state para uso en cuerpo principal
    st.session_state["_ancho_nave"] = ancho_nave
    st.session_state["_largo_nave"] = largo_nave
    st.session_state["_alto_nave"]  = alto_nave
    st.session_state["_area_nave"]  = area_nave
    st.session_state["_sfr_target"] = sfr_target
    st.session_state["_modelo_sel"] = modelo_sel
    st.session_state["_tipo_uso"]   = tipo_uso
    # Verdad métrica — para conversión correcta en el próximo toggle
    st.session_state["_ancho_usr_base"] = ancho_nave
    st.session_state["_largo_usr_base"] = largo_nave
    st.session_state["_alto_usr_base"]  = alto_nave
    # Valor de display (pies o metros) — para textos UI
    st.session_state["_ancho_usr"] = _ancho_usr
    st.session_state["_largo_usr"] = _largo_usr
    st.session_state["_alto_usr"]  = _alto_usr


# =============================================================================
# 5. TABS
# =============================================================================

# Leer variables del sidebar desde session_state
ancho_nave = st.session_state.get("_ancho_nave", 50.0)
largo_nave = st.session_state.get("_largo_nave", 100.0)
alto_nave  = st.session_state.get("_alto_nave",  8.0)
area_nave  = st.session_state.get("_area_nave",  5000.0)
sfr_target = st.session_state.get("_sfr_target", 0.03)
modelo_sel = st.session_state.get("_modelo_sel", "")
tipo_uso   = st.session_state.get("_tipo_uso",   "Warehouse")
_ancho_usr = st.session_state.get("_ancho_usr",  50.0)
_largo_usr = st.session_state.get("_largo_usr",  100.0)
_alto_usr  = st.session_state.get("_alto_usr",   8.0)

tab_config, tab_clima, tab_3d, tab_analitica = st.tabs([
    T("tab_climate", _L),
    T("tab_context", _L),
    T("tab_3d", _L),
    T("tab_energy", _L),
])


# =============================================================================
# TAB 1 — MAPA Y DESCARGA EPW
# =============================================================================
with tab_config:
    page_header(T("tab_climate", _L), "TMYx · OneBuilding.org — EnergyPlus Weather")

    col1, col2 = st.columns([2, 1])

    with col1:
        section_title(T("interactive_map", _L))
        st.caption(T("map_caption", _L))

        m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=8, tiles="CartoDB positron")
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

        output = st_folium(m, width=700, height=480, use_container_width=True, key="mapa_estaciones")

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
        section_title(T("stations_available", _L))

        if st.session_state.clima_data:
            st.markdown(f'<span class="eco-badge-ok">{T("active_climate",_L)}</span><br>'
                        f'<span style="font-size:0.75rem;color:{ECO_GRIS}">'
                        f'{st.session_state.estacion_seleccionada}</span>',
                        unsafe_allow_html=True)
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        if st.session_state.df_cercanas is not None and not st.session_state.df_cercanas.empty:
            st.caption(T("select_station", _L))
            for idx, row in st.session_state.df_cercanas.iterrows():
                st_name = row.get('name') or row.get('Station') or f"Estación {idx}"
                st_dist = row.get('distancia_km') or 0
                url     = row.get('URL_ZIP') or row.get('epw')
                with st.container():
                    st.markdown(f"**{st_name}**")
                    st.caption(f"{T('distance_label',_L)}: **{st_dist} km**")
                    if st.button(T("download_climate", _L), key=f"btn_st_{idx}", use_container_width=True):
                        if url:
                            with st.spinner(T("spinner_epw", _L)):
                                path = descargar_y_extraer_epw(url)
                                if path:
                                    try:
                                        data = procesar_datos_clima(path)
                                        if data:
                                            st.session_state.clima_data            = data
                                            st.session_state.estacion_seleccionada = st_name
                                            st.session_state.epw_path              = path
                                            st.session_state.resultado_motor       = None
                                            st.session_state.calculo_completado    = False
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
    page_header(T("tab_context", _L), T("temp_caption", _L))

    if st.session_state.clima_data and 'vel_viento' in st.session_state.clima_data:
        clima = st.session_state.clima_data
        md    = clima.get('metadata', {})

        render_cards([
            {"label": T("latitude", _L),      "value": f"{md.get('lat', st.session_state.lat):.1f}°N"},
            {"label": T("longitude", _L),     "value": f"{md.get('lon', st.session_state.lon):.1f}°W"},
            {"label": T("elevation", _L),     "value": f"{int(round(md.get('elevacion', 0)))} m"},
            {"label": T("rel_humidity", _L),  "value": f"{round(sum(clima.get('hum_relativa',[0]))/8760)} %"},
            {"label": T("wind_speed", _L),    "value": f"{round(sum(clima.get('vel_viento',[0]))/8760, 1)} m/s"},
        ])

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        col_g1, col_g2 = st.columns(2)

        with col_g1:
            section_title(T("wind_rose", _L))
            df_viento = pd.DataFrame({'dir': clima.get('dir_viento', []), 'vel': clima.get('vel_viento', [])})
            if not df_viento.empty:
                df_viento = df_viento[df_viento['vel'] > 0.5]
                bins_dir  = np.arange(-11.25, 372.0, 22.5)
                labels_dir = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW','N2']
                df_viento['Dir_Cat'] = pd.cut(df_viento['dir'], bins=bins_dir, labels=labels_dir, right=False)
                df_viento['Dir_Cat'] = df_viento['Dir_Cat'].replace('N2', 'N')
                bins_vel   = [0, 2, 4, 6, 8, 20]
                labels_vel = ['0–2 m/s','2–4 m/s','4–6 m/s','6–8 m/s','>8 m/s']
                df_viento['Vel_Cat'] = pd.cut(df_viento['vel'], bins=bins_vel, labels=labels_vel)
                df_rose = df_viento.groupby(['Dir_Cat','Vel_Cat']).size().reset_index(name='Frequency')
                fig_rose = px.bar_polar(df_rose, r="Frequency", theta="Dir_Cat", color="Vel_Cat",
                    color_discrete_sequence=["#B8D4E0","#7AAFC4","#3E8CA8","#003C52","#001F2B"],
                    template="plotly_white")
                fig_rose.update_layout(margin=dict(t=20, b=20, l=20, r=20))
                st.plotly_chart(fig_rose, use_container_width=True)

        with col_g2:
            section_title(T("radiation_balance", _L))
            st.caption(T("radiation_caption", _L))
            suma_directa = sum(clima.get('rad_directa', [0]))
            suma_difusa  = sum(clima.get('rad_dif', [0]))
            fig_pie = go.Figure(data=[go.Pie(
                labels=[T('rad_direct',_L), T('rad_diffuse',_L)],
                values=[suma_directa, suma_difusa],
                hole=.45,
                marker_colors=[ECO_AZUL, "#7AAFC4"],
                textfont=dict(size=11),
                textinfo='percent+label',
            )])
            fig_pie.update_layout(margin=dict(t=10, b=10, l=20, r=20), template="plotly_white", showlegend=False)
            st.plotly_chart(fig_pie, use_container_width=True)

        st.divider()
        section_title(T("temp_heatmap", _L))
        st.caption(T("temp_caption", _L))

        temp_array = np.array(clima.get('temp_seca', np.zeros(8760)))
        if len(temp_array) == 8760:
            temp_matriz = temp_array.reshape(365, 24).T
            fig_calor = go.Figure(data=go.Heatmap(
                z=(temp_matriz * 9/5 + 32) if _U == 'imperial' else temp_matriz,
                x=list(range(1, 366)),
                y=list(range(0, 24)),
                colorscale='RdYlBu_r',
                colorbar=dict(title=T("temp_unit",_L), titleside="right"),
                hovertemplate=("Day %{x} · Hour %{y}:00 · %{z:.1f} °F<extra></extra>" if _L=="EN" else "Día %{x} · Hora %{y}:00 · %{z:.1f} °C<extra></extra>"),
            ))
            fig_calor.update_layout(
                xaxis_title=T("days_of_year", _L),
                yaxis_title=T("hour_of_day", _L),
                yaxis=dict(tickmode='linear', tick0=0, dtick=4),
                margin=dict(t=10, b=30, l=40, r=20),
                height=380,
                template="plotly_white",
            )
            st.plotly_chart(fig_calor, use_container_width=True)

        st.divider()
        section_title(T("thermodynamics", _L))

        temp_diaria = np.array([sum(temp_array[i:i+24])/24 for i in range(0, 8760, 24)]) if len(temp_array) == 8760 else np.zeros(365)
        cdd_anual = sum(t - 18.3 for t in temp_diaria if t > 18.3)
        hdd_anual = sum(18.3 - t for t in temp_diaria if t < 18.3)

        render_cards([
            {"label": T("cdd_label", _L), "value": f"{int(cdd_anual):,}", "delta": T("cdd_delta", _L)},
            {"label": T("hdd_label", _L), "value": f"{int(hdd_anual):,}", "delta": T("hdd_delta", _L)},
        ])

        nubes_array = clima.get('nubes', np.zeros(8760))
        if len(nubes_array) == 8760:
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
            section_title(T("cloudiness", _L))
            fechas   = pd.date_range(start="2023-01-01", periods=8760, freq="h")
            df_nubes = pd.DataFrame({'Fecha': fechas, 'Nubosidad': np.array(nubes_array) * 10})
            df_nubes['Mes'] = df_nubes['Fecha'].dt.month
            nubes_mensual = df_nubes.groupby('Mes')['Nubosidad'].mean()
            meses_labels  = (
                ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
                if _L == "ES" else
                ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
            )
            fig_nubes = go.Figure(data=[go.Bar(
                x=meses_labels, y=nubes_mensual,
                marker_color=ECO_GRIS,
                text=[f"{v:.0f}%" for v in nubes_mensual],
                textposition='auto',
                textfont=dict(size=10),
            )])
            fig_nubes.update_layout(
                yaxis_title=T("sky_cover_pct", _L),
                yaxis=dict(range=[0, 100]),
                template="plotly_white",
                height=320,
                margin=dict(t=10, b=20, l=40, r=20),
            )
            st.plotly_chart(fig_nubes, use_container_width=True)
    else:
        st.info(T("bioclim_download", _L))


# =============================================================================
# TAB 3 — GEOMETRÍA 3D
# =============================================================================
with tab_3d:
    page_header(T("tab_3d_title", _L), T("tab_3d_subtitle", _L))

    if st.button(T("btn_generate_3d", _L), use_container_width=True, type="primary"):
        with st.spinner(T("spinner_3d", _L)):
            try:
                datos_domo = df_domos[df_domos['Modelo'] == modelo_sel].iloc[0]
                try:
                    vtk_path, num_domos, sfr_real = generar_nave_3d_vtk(
                        ancho_nave, largo_nave, alto_nave, sfr_target,
                        datos_domo['Ancho_m'], datos_domo['Largo_m'],
                        lat=st.session_state.lat, lon=st.session_state.lon,
                    )
                except Exception:
                    vtk_path  = None
                    num_domos = max(1, round(ancho_nave * largo_nave * sfr_target /
                                            (float(datos_domo['Ancho_m']) * float(datos_domo['Largo_m']))))
                    sfr_real  = num_domos * float(datos_domo['Ancho_m']) * float(datos_domo['Largo_m']) / (ancho_nave * largo_nave)

                st.session_state.vtk_path          = vtk_path or "plotly_mode"
                st.session_state.num_domos_real    = num_domos
                st.session_state.sfr_final         = sfr_real
                st.session_state.datos_domo_actual = datos_domo
            except Exception as e:
                st.error(f"Error en el motor 3D: {e}")

    if st.session_state.vtk_path:
        import plotly.graph_objects as go
        import math as _math

        sfr_pct    = st.session_state.sfr_final * 100
        num_domos  = st.session_state.num_domos_real
        datos_domo = st.session_state.datos_domo_actual
        domo_ancho = float(datos_domo['Ancho_m'])
        domo_largo = float(datos_domo['Largo_m'])
        A, L, H    = st.session_state.get("_ancho_usr", 50.0), st.session_state.get("_largo_usr", 100.0), st.session_state.get("_alto_usr", 8.0)
        _domo_a_vis = domo_ancho * CONVERSION["m_to_ft"] if _U == "imperial" else domo_ancho
        _domo_l_vis = domo_largo * CONVERSION["m_to_ft"] if _U == "imperial" else domo_largo

        if sfr_pct <= 3.0:
            st.markdown(f'<span class="eco-badge-ok">{T("ashrae_compliant", _L)}</span>', unsafe_allow_html=True)
        elif sfr_pct <= 5.0:
            st.markdown(f'<span class="eco-badge-warn">{T("ashrae_controls", _L)}</span>', unsafe_allow_html=True)
        else:
            st.markdown(f'<span class="eco-badge-warn">{T("ashrae_exceeds", _L)}</span>', unsafe_allow_html=True)

        render_cards([
            {"label": T("skylights_count",_L), "value": f"{num_domos} uds"},
            {"label": T("sfr_real",_L),        "value": f"{sfr_pct:.2f} %"},
        ])

        st.divider()
        mostrar_sol = st.toggle(T("sunpath_toggle", _L), value=False)

        fig3d = go.Figure()
        COL_PARED = "rgba(255,255,0,0.20)"
        COL_TECHO = "rgba(255,0,0,0.15)"
        COL_PISO  = "rgba(160,160,160,0.35)"
        COL_EDGE  = "#555555"
        COL_DOMO  = "#4FC3F7"
        COL_DOMO_E= "#003C52"
        COL_SOL   = "#FFD600"

        pts = [(0,0,0),(A,0,0),(A,L,0),(0,L,0),(0,0,H),(A,0,H),(A,L,H),(0,L,H)]
        ex,ey,ez = [],[],[]
        for i,j in [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]:
            x0,y0,z0=pts[i]; x1,y1,z1=pts[j]
            ex+=[x0,x1,None]; ey+=[y0,y1,None]; ez+=[z0,z1,None]
        fig3d.add_trace(go.Scatter3d(x=ex,y=ey,z=ez,mode='lines',
            line=dict(color=COL_EDGE,width=3),showlegend=False,hoverinfo='skip'))

        for verts, col, nom, show in [
            ([(0,0,0),(A,0,0),(A,L,0),(0,L,0)], COL_PISO,  T("floor_3d",_L),  True),
            ([(0,0,H),(A,0,H),(A,L,H),(0,L,H)], COL_TECHO, T("roof_3d",_L),   True),
            ([(0,0,0),(A,0,0),(A,0,H),(0,0,H)], COL_PARED, T("walls_3d",_L),  True),
            ([(0,L,0),(A,L,0),(A,L,H),(0,L,H)], COL_PARED, "",       False),
            ([(0,0,0),(0,L,0),(0,L,H),(0,0,H)], COL_PARED, "",       False),
            ([(A,0,0),(A,L,0),(A,L,H),(A,0,H)], COL_PARED, "",       False),
        ]:
            xs=[v[0] for v in verts]; ys=[v[1] for v in verts]; zs=[v[2] for v in verts]
            fig3d.add_trace(go.Mesh3d(x=xs,y=ys,z=zs,i=[0,0],j=[1,2],k=[2,3],
                color=col,opacity=0.7,flatshading=True,showlegend=show,name=nom,hoverinfo='skip'))

        cols_d = max(1, round((num_domos*(A/L))**0.5))
        rows_d = max(1, _math.ceil(num_domos/cols_d))
        dx_d, dy_d = A/cols_d, L/rows_d
        for ci in range(cols_d):
            for ri in range(rows_d):
                cx=ci*dx_d+dx_d/2; cy=ri*dy_d+dy_d/2
                x0d=cx-_domo_a_vis/2; x1d=cx+_domo_a_vis/2
                y0d=cy-_domo_l_vis/2; y1d=cy+_domo_l_vis/2
                fig3d.add_trace(go.Mesh3d(
                    x=[x0d,x1d,x1d,x0d],y=[y0d,y0d,y1d,y1d],z=[H+0.05]*4,
                    i=[0,0],j=[1,2],k=[2,3],color=COL_DOMO,opacity=0.9,
                    flatshading=True,showlegend=False,hoverinfo='skip'))

        fig3d.add_trace(go.Scatter3d(x=[None],y=[None],z=[None],mode='markers',
            marker=dict(size=6,color=COL_DOMO,symbol='square'),
            name=f"Sunoptics® Skylights ({num_domos})" if _L=="EN" else f"Domos Sunoptics® ({num_domos} uds)",
            showlegend=True,hoverinfo='skip'))

        if mostrar_sol:
            lat_rad = _math.radians(st.session_state.lat)
            cx_nav, cy_nav = A/2, L/2
            radio = max(A,L)*0.7
            meses = [(0,"Ene","#FF6B35"),(3,"Abr","#FFD600"),(5,"Jun","#FF0000"),
                     (8,"Sep","#FF8C00"),(11,"Dic","#4FC3F7")]
            for mi, mnombre, mcolor in meses:
                decl = _math.radians(-23.45*_math.cos(_math.radians(360/365*(mi*30+10))))
                sx,sy,sz=[],[],[]
                for hora in range(5,20):
                    h_ang = _math.radians(15*(hora-12))
                    sin_alt = (_math.sin(lat_rad)*_math.sin(decl)+
                               _math.cos(lat_rad)*_math.cos(decl)*_math.cos(h_ang))
                    if sin_alt<=0: continue
                    alt = _math.asin(sin_alt)
                    cos_az = ((_math.sin(decl)-_math.sin(lat_rad)*sin_alt)/
                              (_math.cos(lat_rad)*_math.cos(alt)+1e-9))
                    cos_az = max(-1,min(1,cos_az))
                    az = _math.acos(cos_az)
                    if h_ang>0: az=2*_math.pi-az
                    r=radio*_math.cos(alt)
                    sx.append(cx_nav+r*_math.sin(az))
                    sy.append(cy_nav+r*_math.cos(az))
                    sz.append(H+radio*_math.sin(alt))
                if sx:
                    fig3d.add_trace(go.Scatter3d(x=sx,y=sy,z=sz,mode='lines+markers',
                        line=dict(color=mcolor,width=2),marker=dict(size=2,color=mcolor),
                        name=f"Sunpath {mnombre}",showlegend=True))
            fig3d.add_trace(go.Scatter3d(x=[cx_nav],y=[cy_nav],z=[H+radio],mode='markers',
                marker=dict(size=12,color=COL_SOL,line=dict(color='orange',width=2)),
                name=T('solar_zenith',_L),showlegend=True))

        fig3d.update_layout(
            scene=dict(
                xaxis=dict(title=f"{T('width_label',_L)} ({A:.0f} {T('units_m',_L)})",backgroundcolor="rgba(245,240,230,0.8)",gridcolor="#D4B896",showbackground=True),
                yaxis=dict(title=f"{T('length_label',_L)} ({L:.0f} {T('units_m',_L)})",backgroundcolor="rgba(245,240,230,0.8)",gridcolor="#D4B896",showbackground=True),
                zaxis=dict(title=f"{T('height_label',_L)} ({H:.0f} {T('units_m',_L)})",backgroundcolor="rgba(220,210,200,0.5)",gridcolor="#C4A882",showbackground=True),
                camera=dict(eye=dict(x=1.5,y=-1.8,z=1.2)),
                aspectmode="data",
            ),
            margin=dict(l=0,r=0,t=35,b=0), height=520,
            paper_bgcolor="white",
            legend=dict(x=0.01,y=0.99,bgcolor="rgba(255,255,255,0.8)",bordercolor="#D4B896",borderwidth=1,font=dict(size=9)),
            title=dict(
                text=f"{st.session_state.get('_ancho_usr',50.0):.0f}×{st.session_state.get('_largo_usr',100.0):.0f}×{st.session_state.get('_alto_usr',8.0):.0f} {T('units_m',_L)} — {num_domos} {'Skylights' if _L=='EN' else 'domos'} Sunoptics® (SFR {sfr_pct:.1f}%)",
                font=dict(size=11,color="#003C52"),x=0.5),
        )
        st.plotly_chart(fig3d, use_container_width=True)

    else:
        st.markdown(f'<div class="eco-disclaimer">{T("configure_prompt", _L)}</div>', unsafe_allow_html=True)


# =============================================================================
# TAB 4 — SIMULACIÓN ENERGÉTICA
# =============================================================================
with tab_analitica:
    page_header(T("tab_energy_title", _L), T("tab_energy_sub", _L))

    if not MOTOR_DISPONIBLE:
        st.error(T("err_motor_unavailable", _L))
        st.stop()

    if not st.session_state.clima_data:
        st.markdown(f'<div class="eco-disclaimer">Descarga un archivo climático en <strong>Selección de Clima</strong> para habilitar la simulación.</div>', unsafe_allow_html=True)
        st.stop()

    if not st.session_state.epw_path or not os.path.exists(st.session_state.epw_path):
        st.markdown(f'<div class="eco-disclaimer">Archivo EPW no disponible. Vuelve a descargar el clima.</div>', unsafe_allow_html=True)
        st.stop()

    if area_nave > 10_000:
        st.error(T("err_area_too_large", _L))
        st.stop()

    clima      = st.session_state.clima_data
    md         = clima.get("metadata", {})
    ciudad     = md.get("ciudad", st.session_state.estacion_seleccionada or "Desconocida")
    pais       = md.get("pais", "")
    datos_domo = df_domos[df_domos["Modelo"] == modelo_sel].iloc[0]

    with st.expander(T("project_summary", _L), expanded=True):
        render_cards([
            {"label": T("facility_label",_L), "value": fmt_dims(ancho_nave,largo_nave,alto_nave,_U)},
            {"label": T("area_label",_L),     "value": fmt_area(area_nave,_U)},
            {"label": T("pdf_use",_L),        "value": get_occupancy_label(tipo_uso,_L)},
            {"label": T("climate_label",_L),  "value": ciudad},
        ], sm=True)
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        render_cards([
            {"label": T("skyplus_dome_label",_L), "value": f"{modelo_sel.split(' ')[2]} {modelo_sel.split(' ')[3]}"},
            {"label": T("vlt",_L),       "value": f"{datos_domo['VLT']:.0%}"},
            {"label": T("sfr_design",_L), "value": f"{sfr_target*100:.0f}%"},
            {"label": T("pdf_field_engine",_L), "value": "EnergyPlus 23.2"},
        ], sm=True)

    st.divider()

    # ── Etapa 1 — Simulación base vs diseño ──────────────────────────────
    if not st.session_state.diseno_completado:

        col_btn, col_info = st.columns([1, 2])
        with col_btn:
            ejecutar_diseno = st.button(T("btn_simulate", _L), use_container_width=True, type="primary")
        with col_info:
            st.markdown(f'<div class="eco-disclaimer">{T("compare_prompt", _L).format(sfr=f"{sfr_target*100:.0f}")}</div>', unsafe_allow_html=True)

        if ejecutar_diseno:
            barra      = st.progress(0, text=T("spinner_motor", _L))
            status_box = st.empty()
            facts = [T("fact_prismatic",_L), T("fact_lighting",_L), T("fact_co2",_L), T("fact_sfr",_L), T("fact_ashrae",_L)]
            fact_box = st.info(facts[0])

            def actualizar_progreso(paso, total, mensaje):
                pct = int(paso / max(total, 1) * 100)
                barra.progress(pct, text=mensaje)
                status_box.caption(f"{mensaje}")
                if paso < len(facts):
                    fact_box.info(facts[paso % len(facts)])

            if not st.session_state.get("_ancho_nave"):
                st.error(T("err_no_geometry", _L))
                st.stop()

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
                config["lang"]  = _L
                config["units"] = _U
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

    # ── Resultado Etapa 1 ─────────────────────────────────────────────────
    if st.session_state.diseno_completado and st.session_state.resultado_diseno:
        res = st.session_state.resultado_diseno

        section_title(T("energy_results", _L))

        render_cards([
            {"label": T("savings_label", _L),
             "value": fmt_energy(res['ahorro_neto'], _U),
             "delta": T("pct_above_base",_L).format(pct=f"{res['pct_ahorro']:.1f}"),
             "green": True},
            {"label": T("base_consumption", _L),
             "value": fmt_energy(res['kwh_base'], _U),
             "delta": T("ashrae_ref",_L)},
        ])
        render_cards([
            {"label": T("skylights_sfr_label",_L).format(sfr=f"{res['sfr_real']:.1f}"),
             "value": f"{res['n_domos']} {T('units_count',_L)}",
             "delta": f"{fmt_illuminance(res['fc_lux'], _U, 0)} {T('lux_avg', _L)}"},
            {"label": T("visual_comfort", _L),
             "value": get_compliance_label(res["semaforo_txt"], _L),
             "delta": "ISO 8995-1 + IES RP-7"},
        ])

        st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)
        st.plotly_chart(fix_figura(res["figura"]), use_container_width=True)
        st.markdown(res["recomendacion"])
        st.divider()

        # ── CTA Lead Magnet ───────────────────────────────────────────────
        if not st.session_state.lead_capturado:
            section_title(T("optimization_curve", _L))
            st.markdown(
                T("lead_subtitle_tmpl", _L).format(
                    dim=f"{st.session_state.get('_ancho_usr',50.0):.0f}×{st.session_state.get('_largo_usr',100.0):.0f} {T('units_m',_L)}",
                    mins=max(20, min(40, int(ancho_nave*largo_nave/1000)*3 + 20))
                )
            )
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            with st.form("formulario_leads_cta"):
                col_f1, col_f2 = st.columns(2)
                nombre_contacto   = col_f1.text_input(T("field_name", _L))
                empresa_contacto  = col_f2.text_input(T("field_company", _L))
                correo_contacto   = col_f1.text_input(T("field_email", _L))
                telefono_contacto = col_f2.text_input(T("field_phone", _L))
                comentario        = st.text_area(T("field_comments", _L), height=60)

                st.markdown("""
                <style>
                div[data-testid="stFormSubmitButton"] button { background-color: #28a745 !important; background: #28a745 !important; color: #FFFFFF !important; font-weight: 800 !important; font-size: 1.0rem !important; border: 2px solid #1e7e34 !important; border-radius: 6px !important; padding: 0.65rem 1rem !important; width: 100% !important; }
                div[data-testid="stFormSubmitButton"] button:hover { background-color: #1e7e34 !important; background: #1e7e34 !important; color: #FFFFFF !important; }
                div[data-testid="stFormSubmitButton"] button p { color: #FFFFFF !important; font-weight: 800 !important; }
                </style>
                """, unsafe_allow_html=True)
                enviado = st.form_submit_button(T("btn_request_report", _L), use_container_width=True)

            if enviado:
                if nombre_contacto and empresa_contacto and correo_contacto:
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
                        st.session_state.lead_nombre     = nombre_contacto
                        st.session_state.lead_empresa    = empresa_contacto
                        st.session_state.lead_correo     = correo_contacto
                        st.session_state.lead_telefono   = telefono_contacto
                        st.session_state.lead_comentario = comentario
                        st.session_state.lead_capturado  = True
                        st.rerun()
                else:
                    st.error(T("err_lead_incomplete", _L))

        # ── Etapa 2 — lanzar Cloud Run Job ───────────────────────────────
        if st.session_state.lead_capturado and not st.session_state.bg_lanzado:

            clima = st.session_state.clima_data or {}
            md    = clima.get("metadata", {})

            with st.spinner(T("spinner_prep", _L)):
                gcs_uri = upload_epw_to_gcs(st.session_state.epw_path, st.session_state.lead_correo)
            if not gcs_uri:
                st.error(T("err_epw_prep", _L))
                st.stop()

            _sql_base = st.session_state.resultado_diseno.get("sql_base") if st.session_state.resultado_diseno else None
            gcs_sql_base = None
            if _sql_base and os.path.exists(_sql_base):
                with st.spinner(T("spinner_opt", _L)):
                    gcs_sql_base = upload_epw_to_gcs(_sql_base, st.session_state.lead_correo + "_sql")

            _config_job = {
                "ancho":        ancho_nave,
                "largo":        largo_nave,
                "altura":       alto_nave,
                "tipo_uso":     tipo_uso,
                "epw_path":     gcs_uri,
                "sfr_diseno":   sfr_target,
                "domo_vlt":     float(datos_domo["VLT"]),
                "domo_shgc":    float(datos_domo["SHGC"]),
                "domo_u":       float(datos_domo["U_Value"]),
                "domo_ancho_m": float(datos_domo["Ancho_m"]),
                "domo_largo_m": float(datos_domo["Largo_m"]),
                "modelo_domo":  modelo_sel,
                "ciudad":       md.get("ciudad", st.session_state.estacion_seleccionada or ""),
                "pais":         md.get("pais", ""),
                "lang":         _L,
                "units":        _U,
            }
            _lead_job = {
                "nombre":     st.session_state.lead_nombre,
                "empresa":    st.session_state.lead_empresa,
                "correo":     st.session_state.lead_correo,
                "telefono":   st.session_state.lead_telefono,
                "comentario": st.session_state.lead_comentario,
            }
            _sql_base_job = st.session_state.resultado_diseno.get("sql_base") if st.session_state.resultado_diseno else None

            ok, msg = lanzar_cloud_run_job(_config_job, _lead_job, gcs_sql_base)

            if ok:
                st.session_state.bg_lanzado = True
                st.rerun()
            else:
                st.error(f"No se pudo lanzar el análisis: {msg}")
                st.info(T("err_contact_us", _L))

        # ── Confirmación post-lanzamiento ─────────────────────────────────
        if st.session_state.bg_lanzado:
            st.markdown(f"""
            <div style="background:#EBF5E1;border-left:4px solid #4A7C2F;border-radius:4px;padding:20px 24px;margin:16px 0;">
                <div style="font-size:1rem;font-weight:700;color:#4A7C2F;margin-bottom:6px;">{T("processing_title", _L)}</div>
                <div style="font-size:0.85rem;color:#4A5568;line-height:1.7;">
                    {T("processing_mins_tmpl", _L).format(mins=max(20, min(40, int(ancho_nave*largo_nave/1000)*3 + 20)))}
                    <strong>{st.session_state.lead_empresa}</strong>.<br>
                    {T("report_to_email", _L)} <strong>{st.session_state.lead_correo}</strong>.<br><br>
                    {T("can_close_window", _L)}
                </div>
            </div>
            """, unsafe_allow_html=True)

            render_cards([
                {"label": T("status_label",_L),   "value": T("simulating_cloud",_L), "delta": "7 × EnergyPlus 23.2", "green": True},
                {"label": T("delivery_label",_L),  "value": f"~{max(20, min(40, int(ancho_nave*largo_nave/1000)*3 + 20))} min", "delta": f"A: {st.session_state.lead_correo}"},
                {"label": T("pdf_field_engine",_L),"value": "EnergyPlus 23.2", "delta": "DOE oficial"},
                {"label": T("analysis_label",_L),  "value": T("analysis_value",_L), "delta": T("analysis_delta",_L)},
            ])

        # ── Reset ─────────────────────────────────────────────────────────
        st.divider()
        if st.button(T("btn_new_sim", _L)):
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
