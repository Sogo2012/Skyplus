# app.py
# =============================================================================
# SKYPLUS 1.0 — Eco Consultor | Sunoptics LATAM
# Motor: EnergyPlus 23.2 (DOE) + EPW analítico (ISO 8995-1 / IES RP-7)
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

# ── Motor SkyPlus v22 ────────────────────────────────────────────────────────
try:
    from motor import calcular_curva_sfr, configurar_proyecto
    MOTOR_DISPONIBLE = True
except ImportError:
    MOTOR_DISPONIBLE = False   # Corre sin motor (Cloud sin EnergyPlus)

# =============================================================================
# 1. CONFIGURACIÓN DE PÁGINA
# =============================================================================
st.set_page_config(
    page_title="SkyPlus — Eco Consultor",
    layout="wide",
    page_icon="⚡",
)

st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stButton>button {
        width: 100%; border-radius: 5px; height: 3em;
        background-color: #007bff; color: white;
    }
    .stMetric {
        background-color: white; padding: 15px;
        border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    /* Ocultar branding del visor 3D */
    .pollination-logo, .ladybug-logo { display: none !important; }
    div[title="Powered by Pollination"] { display: none !important; }
    a[href*="pollination.cloud"] { display: none !important; }
    a[href*="ladybug.tools"] { display: none !important; }
    /* Tabla de resultados */
    .resultado-card {
        background: white; border-radius: 10px; padding: 20px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 16px;
    }
    </style>
""", unsafe_allow_html=True)

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
        'Ancho_in':[51.25,51.25,51.25,51.25,52.25,52.25,52.25,52.25],
        'Largo_in':[51.25,51.25,87.25,87.25,100.25,100.25,100.25,100.25],
    }
    df = pd.DataFrame(data)
    df['Ancho_m'] = (df['Ancho_in'] * 0.0254).round(3)
    df['Largo_m'] = (df['Largo_in'] * 0.0254).round(3)
    return df

df_domos = cargar_catalogo()

# =============================================================================
# 3. INICIALIZACIÓN DE SESSION STATE
# =============================================================================
_defaults = {
    'clima_data': None,
    'estacion_seleccionada': None,
    'df_cercanas': None,
    'vtk_path': None,
    'epw_path': None,           # ← NUEVO: ruta EPW en disco para el motor
    'resultado_motor': None,    # ← NUEVO: resultado calcular_curva_sfr()
    'num_domos_real': 0,
    'sfr_final': 0.0,
    'datos_domo_actual': None,
    'calculo_completado': False,
    'lat': 20.5888,
    'lon': -100.3899,
}
for key, val in _defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val


def buscar_estaciones():
    with st.spinner("Buscando estaciones cercanas..."):
        df = obtener_estaciones_cercanas(
            st.session_state.lat, st.session_state.lon
        )
        st.session_state.df_cercanas = df
        if df is None or df.empty:
            st.error("No se encontraron estaciones para esta ubicación.")
        else:
            st.success(f"Encontradas {len(df)} estaciones.")


# =============================================================================
# 4. SIDEBAR — CONFIGURACIÓN DEL PROYECTO
# =============================================================================
with st.sidebar:
    st.markdown("## 🍃 Eco Consultor")
    st.title("SkyPlus 1.0")

    # ── Ubicación y Clima ─────────────────────────────────────
    with st.expander("📍 1. Ubicación y Clima", expanded=False):
        search_name = st.text_input(
            "Buscar por ciudad o país", placeholder="Ej: Querétaro, México"
        )
        if st.button("🔍 Buscar por Nombre"):
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
                    st.error("Error al conectar con el servicio de búsqueda.")

        st.divider()
        st.session_state.lat = st.number_input(
            "Latitud", value=st.session_state.lat, format="%.4f"
        )
        st.session_state.lon = st.number_input(
            "Longitud", value=st.session_state.lon, format="%.4f"
        )
        if st.button("🚀 Buscar en Coordenadas"):
            buscar_estaciones()

    # ── Geometría ─────────────────────────────────────────────
    st.subheader("📐 2. Geometría de la Nave")

    ancho_nave = st.number_input(
        "Ancho (m)",  min_value=10.0, max_value=100.0, value=50.0, step=1.0,
        help="Máximo 100 m. Proyectos mayores requieren servicio BEM premium."
    )
    largo_nave = st.number_input(
        "Largo (m)",  min_value=10.0, max_value=100.0, value=100.0, step=1.0,
    )
    alto_nave = st.number_input(
        "Altura (m)", min_value=3.0,  max_value=30.0,  value=8.0,  step=0.5,
    )

    area_nave = ancho_nave * largo_nave
    st.caption(f"Área nave: **{area_nave:,.0f} m²**")
    if area_nave > 10_000:
        st.warning(
            "⚠️ Área > 10,000 m². Para proyectos de esta escala, "
            "solicita el servicio **BEM Premium**."
        )

    # ── Tipo de uso ───────────────────────────────────────────
    st.subheader("🏭 3. Tipo de Uso")
    tipo_uso = st.selectbox(
        "Perfil ASHRAE 90.1",
        options=["Warehouse", "Manufacturing", "Retail", "SuperMarket", "MediumOffice"],
        format_func=lambda x: {
            "Warehouse":     "🏭 Bodega / Warehouse",
            "Manufacturing": "⚙️ Manufactura",
            "Retail":        "🛒 Retail / Tienda",
            "SuperMarket":   "🛍️ Supermercado",
            "MediumOffice":  "🏢 Oficina Mediana",
        }[x],
        help="Define LPD, setpoints y horarios según ASHRAE 90.1-2019.",
    )

    # ── Domo Sunoptics ────────────────────────────────────────
    st.subheader("☀️ 4. Domo Sunoptics®")
    modelo_sel = st.selectbox("Modelo NFRC", df_domos['Modelo'])
    sfr_target = st.slider(
        "Objetivo SFR (%)", 1.0, 10.0, 4.0, 0.1,
        help="Skylight-to-Floor Ratio. Límite ASHRAE 90.1: ≤5%."
    ) / 100.0

    # Mostrar propiedades del domo seleccionado
    datos_domo_sel = df_domos[df_domos['Modelo'] == modelo_sel].iloc[0]
    with st.expander("Ver propiedades del domo"):
        col_a, col_b = st.columns(2)
        col_a.metric("VLT",   f"{datos_domo_sel['VLT']:.0%}")
        col_b.metric("SHGC",  f"{datos_domo_sel['SHGC']:.2f}")
        col_a.metric("U-val", f"{datos_domo_sel['U_Value']:.2f} W/m²K")
        col_b.metric("Tamaño",f"{datos_domo_sel['Ancho_m']:.2f}×{datos_domo_sel['Largo_m']:.2f} m")

    # Estado del motor
    st.divider()
    if st.session_state.epw_path:
        st.success("✅ Clima listo para simular")
    else:
        st.info("ℹ️ Descarga un archivo climático para habilitar la simulación")

    if not MOTOR_DISPONIBLE:
        st.warning("⚠️ Motor EnergyPlus no disponible en este entorno.")


# =============================================================================
# 5. TABS PRINCIPALES
# =============================================================================
tab_config, tab_clima, tab_3d, tab_analitica, tab_reporte = st.tabs([
    "🌍 Selección de Clima",
    "🌤️ Contexto Climático",
    "📐 Geometría 3D",
    "📊 Simulación Energética",
    "📄 Reporte Final",
])


# =============================================================================
# TAB 1 — MAPA Y DESCARGA EPW
# =============================================================================
with tab_config:
    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("🌍 Mapa Interactivo")
        st.caption("Haz clic en el mapa para buscar estaciones en ese punto.")

        m = folium.Map(
            location=[st.session_state.lat, st.session_state.lon], zoom_start=8
        )
        folium.Marker(
            [st.session_state.lat, st.session_state.lon],
            tooltip="Ubicación de Proyecto",
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

        output = st_folium(m, width=700, height=500,
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
        st.subheader("Estaciones Disponibles")

        if st.session_state.clima_data:
            st.success(f"✅ Clima Activo: **{st.session_state.estacion_seleccionada}**")
            if st.session_state.epw_path:
                st.caption(f"📁 `{os.path.basename(st.session_state.epw_path)}`")

        if st.session_state.df_cercanas is not None and not st.session_state.df_cercanas.empty:
            st.write("Selecciona una estación para descargar el .epw:")

            for idx, row in st.session_state.df_cercanas.iterrows():
                st_name = row.get('name') or row.get('Station') or f"Estación {idx}"
                st_dist = row.get('distancia_km') or 0
                url     = row.get('URL_ZIP') or row.get('epw')

                with st.container():
                    st.markdown(f"**{st_name}**")
                    st.caption(f"📏 Distancia: **{st_dist} km**")
                    if st.button(f"📥 Descargar Datos",
                                 key=f"btn_st_{idx}", use_container_width=True):
                        if url:
                            with st.spinner("Descargando e inyectando datos..."):
                                path = descargar_y_extraer_epw(url)
                                if path:
                                    try:
                                        data = procesar_datos_clima(path)
                                        if data:
                                            st.session_state.clima_data          = data
                                            st.session_state.estacion_seleccionada = st_name
                                            # ← GUARDAR ruta EPW para el motor
                                            st.session_state.epw_path            = path
                                            # Resetear resultado anterior al cambiar clima
                                            st.session_state.resultado_motor     = None
                                            st.session_state.calculo_completado  = False
                                            st.rerun()
                                        else:
                                            st.error("Error al procesar el EPW con Ladybug.")
                                    except Exception as e:
                                        st.error(f"Error: {e}")
                                else:
                                    st.error("Error de descarga. El archivo no está disponible.")


# =============================================================================
# TAB 2 — ANÁLISIS BIOCLIMÁTICO
# =============================================================================
with tab_clima:
    st.subheader("Análisis Bioclimático del Sitio")

    if st.session_state.clima_data and 'vel_viento' in st.session_state.clima_data:
        clima = st.session_state.clima_data
        md    = clima.get('metadata', {})

        cols_hvac = st.columns(4)
        cols_hvac[0].metric("Latitud",               f"{md.get('lat', st.session_state.lat)}°")
        cols_hvac[1].metric("Elevación",              f"{md.get('elevacion', 0)} m")
        cols_hvac[2].metric("Humedad Relativa Media", f"{round(sum(clima.get('hum_relativa',[0]))/8760, 1)} %")
        cols_hvac[3].metric("Velocidad Viento Media", f"{round(sum(clima.get('vel_viento',[0]))/8760, 1)} m/s")

        st.divider()
        col_g1, col_g2 = st.columns(2)

        with col_g1:
            st.markdown("### 🌬️ Rosa de los Vientos Anual")
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
                bins_vel  = [0, 2, 4, 6, 8, 20]
                labels_vel = ['0-2 m/s','2-4 m/s','4-6 m/s','6-8 m/s','>8 m/s']
                df_viento['Vel_Cat'] = pd.cut(df_viento['vel'], bins=bins_vel, labels=labels_vel)
                df_rose = df_viento.groupby(['Dir_Cat','Vel_Cat']).size().reset_index(name='Frecuencia')
                fig_rose = px.bar_polar(df_rose, r="Frecuencia", theta="Dir_Cat",
                                        color="Vel_Cat",
                                        color_discrete_sequence=px.colors.sequential.Plasma_r,
                                        template="plotly_white")
                fig_rose.update_layout(margin=dict(t=20, b=20, l=20, r=20))
                st.plotly_chart(fig_rose, use_container_width=True)

        with col_g2:
            st.markdown("### ☀️ Balance de Irradiación")
            st.caption("Justificación técnica para domos prismáticos de alta difusión.")
            suma_directa = sum(clima.get('rad_directa', [0]))
            suma_difusa  = sum(clima.get('rad_dif', [0]))
            fig_pie = go.Figure(data=[go.Pie(
                labels=['Radiación Directa (Luz Dura)', 'Radiación Difusa (Luz Suave)'],
                values=[suma_directa, suma_difusa],
                hole=.4,
                marker_colors=['#f39c12', '#bdc3c7'],
            )])
            fig_pie.update_layout(margin=dict(t=20,b=20,l=20,r=20), template="plotly_white")
            st.plotly_chart(fig_pie, use_container_width=True)

        st.divider()
        st.markdown("### 🌡️ Mapa de Calor Anual (Temperatura de Bulbo Seco)")
        st.caption("Las 8,760 horas del año. Picos críticos de calor (rojo) y frío (azul).")

        temp_array = np.array(clima.get('temp_seca', np.zeros(8760)))
        if len(temp_array) == 8760:
            temp_matriz = temp_array.reshape(365, 24).T
            fig_calor = go.Figure(data=go.Heatmap(
                z=temp_matriz,
                x=list(range(1, 366)),
                y=list(range(0, 24)),
                colorscale='RdYlBu_r',
                colorbar=dict(title="Temp (°C)"),
                hovertemplate="Día: %{x}<br>Hora: %{y}:00<br>Temp: %{z:.1f} °C<extra></extra>",
            ))
            fig_calor.update_layout(
                xaxis_title="Días del Año (Enero → Diciembre)",
                yaxis_title="Hora del Día",
                yaxis=dict(tickmode='linear', tick0=0, dtick=4),
                margin=dict(t=10, b=30, l=40, r=20),
                height=400, template="plotly_white",
            )
            st.plotly_chart(fig_calor, use_container_width=True)
        else:
            st.warning("Formato inusual de archivo climático (no 8760 horas).")

        st.divider()
        st.markdown("### 🌡️ Termodinámica y Nubosidad")
        temp_diaria = np.array([sum(temp_array[i:i+24])/24 for i in range(0,8760,24)]) if len(temp_array)==8760 else np.zeros(365)
        cdd_anual = sum(t - 18.3 for t in temp_diaria if t > 18.3)
        hdd_anual = sum(18.3 - t for t in temp_diaria if t < 18.3)
        col_t1, col_t2 = st.columns(2)
        col_t1.metric("Grados Día Refrigeración (CDD)", f"{int(cdd_anual)}", "Demanda A/C", delta_color="inverse")
        col_t2.metric("Grados Día Calefacción (HDD)",   f"{int(hdd_anual)}", "Demanda calefacción")

        nubes_array = clima.get('nubes', np.zeros(8760))
        if len(nubes_array) == 8760:
            st.markdown("#### ☁️ Perfil de Nubosidad Mensual")
            fechas = pd.date_range(start="2023-01-01", periods=8760, freq="h")
            df_nubes = pd.DataFrame({'Fecha': fechas, 'Nubosidad': np.array(nubes_array) * 10})
            df_nubes['Mes'] = df_nubes['Fecha'].dt.month
            nubes_mensual = df_nubes.groupby('Mes')['Nubosidad'].mean()
            meses_labels  = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
            fig_nubes = go.Figure(data=[go.Bar(
                x=meses_labels, y=nubes_mensual,
                marker_color='#95a5a6',
                text=[f"{v:.0f}%" for v in nubes_mensual],
                textposition='auto',
            )])
            fig_nubes.update_layout(
                yaxis_title="% Cielo Cubierto", yaxis=dict(range=[0,100]),
                template="plotly_white", height=350, margin=dict(t=20,b=20,l=20,r=20),
            )
            st.plotly_chart(fig_nubes, use_container_width=True)
    else:
        st.warning("⚠️ Descarga un archivo climático en la pestaña 'Selección de Clima'.")


# =============================================================================
# TAB 3 — GEOMETRÍA 3D
# =============================================================================
with tab_3d:
    st.subheader("Modelo Paramétrico Sunoptics®")

    if st.button("🏗️ Generar Modelo 3D", use_container_width=True):
        with st.spinner("Construyendo geometría Honeybee..."):
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

    if st.session_state.vtk_path and os.path.exists(st.session_state.vtk_path):
        sfr_pct = st.session_state.sfr_final * 100
        if sfr_pct <= 3.0:
            alerta_sfr = "🟢 **Cumple límite base ASHRAE (≤3%)**"
        elif sfr_pct <= 5.0:
            alerta_sfr = "🟠 **Requiere sensores de luz (≤5%)**"
        else:
            alerta_sfr = "🔴 **Excede límite ASHRAE (>5%)**"

        cmet1, cmet2, cmet3 = st.columns([1, 1, 2])
        with cmet1:
            st.metric("Domos Generados", f"{st.session_state.num_domos_real} uds")
        with cmet2:
            st.metric("SFR Real", f"{sfr_pct:.2f} %")
            st.markdown(alerta_sfr)
        with cmet3:
            st.info(
                "📘 **ASHRAE 90.1:** SFR ≤3% cumple sin controles. "
                "Hasta 5% con Daylighting Controls automáticos instalados."
            )

        st.divider()
        mostrar_sol = st.toggle("☀️ Mostrar Bóveda Solar", value=False)
        ruta_base   = st.session_state.vtk_path
        ruta_cargar = ruta_base if mostrar_sol else ruta_base.replace('.vtkjs', '_solo.vtkjs')
        if not os.path.exists(ruta_cargar):
            ruta_cargar = ruta_base

        with open(ruta_cargar, "rb") as f:
            vtk_data = f.read()
        st_vtkjs(content=vtk_data, key=f"visor_nave_{mostrar_sol}")
    else:
        st.info("Configura la nave en el sidebar y presiona 'Generar Modelo 3D'.")


# =============================================================================
# TAB 4 — SIMULACIÓN ENERGÉTICA (MOTOR SKYPLUS v22)
# =============================================================================
with tab_analitica:
    st.subheader("Motor de Cálculo SkyPlus v22")
    st.caption(
        "Motor 1: **EnergyPlus 23.2** (DOE) — kWh reales  |  "
        "Motor 2: **EPW analítico** — Iluminancia + Semáforo normativo"
    )

    # ── Verificaciones previas ────────────────────────────────
    if not MOTOR_DISPONIBLE:
        st.error(
            "⚠️ El motor EnergyPlus no está disponible en este entorno.  \n"
            "Despliega la aplicación en **Docker + Google Cloud Run** para "
            "habilitar las simulaciones completas."
        )
        st.stop()

    if not st.session_state.clima_data:
        st.warning("⚠️ Primero descarga un archivo climático en la pestaña '🌍 Selección de Clima'.")
        st.stop()

    if not st.session_state.epw_path or not os.path.exists(st.session_state.epw_path):
        st.warning("⚠️ El archivo EPW no está disponible. Vuelve a descargar el clima.")
        st.stop()

    if area_nave > 10_000:
        st.error(
            "⚠️ El área de la nave supera los 10,000 m².  \n"
            "Para proyectos de esta escala, solicita el **servicio BEM Premium**."
        )
        st.stop()

    # ── Resumen del proyecto a simular ────────────────────────
    clima   = st.session_state.clima_data
    md      = clima.get('metadata', {})
    ciudad  = md.get('ciudad', st.session_state.estacion_seleccionada or 'Desconocida')
    pais    = md.get('pais', '')
    datos_domo = df_domos[df_domos['Modelo'] == modelo_sel].iloc[0]

    with st.expander("📋 Resumen del proyecto a simular", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Nave",       f"{ancho_nave:.0f}×{largo_nave:.0f}×{alto_nave:.0f} m")
        c2.metric("Área",       f"{area_nave:,.0f} m²")
        c3.metric("Uso",        tipo_uso)
        c4.metric("Clima",      f"{ciudad}, {pais}")
        c1.metric("Domo",       modelo_sel.split(" ")[2] + " " + modelo_sel.split(" ")[3])
        c2.metric("VLT",        f"{datos_domo['VLT']:.0%}")
        c3.metric("SFR curva",  "0% → 6%")
        c4.metric("Motor",      "EnergyPlus 23.2")

    st.divider()

    # ── Botón de simulación ───────────────────────────────────
    col_btn, col_info = st.columns([1, 2])
    with col_btn:
        ejecutar = st.button(
            "⚡ EJECUTAR SIMULACIÓN SKYPLUS",
            use_container_width=True,
            type="primary",
            disabled=(area_nave > 10_000),
        )
    with col_info:
        st.info(
            "La simulación corre **7 casos EnergyPlus** (SFR 0%→6%).  \n"
            "Tiempo estimado: **~5-10 minutos** en Cloud Run."
        )

    if ejecutar:
        # Barra de progreso con texto dinámico
        barra      = st.progress(0, text="Preparando motor...")
        status_box = st.empty()

        def actualizar_progreso(paso, total, mensaje):
            pct = int(paso / max(total, 1) * 100)
            barra.progress(pct, text=mensaje)
            status_box.caption(f"⏳ {mensaje}")

        try:
            config = configurar_proyecto(
                ancho       = ancho_nave,
                largo       = largo_nave,
                altura      = alto_nave,
                tipo_uso    = tipo_uso,
                epw_path    = st.session_state.epw_path,
                sfr_curva   = [0, 1, 2, 3, 4, 5, 6],
                domo_vlt    = float(datos_domo['VLT']),
                domo_shgc   = float(datos_domo['SHGC']),
                domo_u      = float(datos_domo['U_Value']),
                domo_ancho_m= float(datos_domo['Ancho_m']),
                domo_largo_m= float(datos_domo['Largo_m']),
            )

            resultado = calcular_curva_sfr(config, callback=actualizar_progreso)

            if resultado.get("error"):
                barra.empty()
                status_box.empty()
                st.error(f"❌ Error en la simulación: {resultado['error']}")
            else:
                barra.progress(100, text="¡Simulación completa!")
                status_box.empty()
                st.session_state.resultado_motor    = resultado
                st.session_state.calculo_completado = True
                st.balloons()
                st.rerun()

        except (ValueError, FileNotFoundError) as e:
            barra.empty()
            st.error(f"❌ Error de configuración: {e}")
        except Exception as e:
            barra.empty()
            st.error(f"❌ Error inesperado: {e}")

    # ── Mostrar resultados si ya se calcularon ────────────────
    if st.session_state.calculo_completado and st.session_state.resultado_motor:
        res = st.session_state.resultado_motor

        # Métricas principales
        st.markdown("---")
        st.markdown("### 📊 Resultados de la Optimización")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            "SFR Óptimo Energético",
            f"{res['sfr_opt']}%",
            f"{res['neto_opt']:,.0f} kWh/año",
        )
        m2.metric(
            "Ahorro sobre caso base",
            f"{res['pct_opt']:.1f}%",
            f"Base: {res['kwh_base']:,.0f} kWh/año",
        )
        if res['sfr_dual'] is not None:
            idx_d = [r['sfr_pct'] for r in res['df_curva_raw']].index(res['sfr_dual'])
            m3.metric(
                "SFR Óptimo Dual (Energía+Confort)",
                f"{res['sfr_dual']}%",
                f"{res['fc_lux'][idx_d]:.0f} lux promedio",
            )
        m4.metric(
            "Consumo Base",
            f"{res['kwh_base']:,.0f} kWh/año",
            "SFR=0% (sin domos)",
        )

        # Gráfica dual-eje
        st.plotly_chart(res['figura'], use_container_width=True)

        # Tabla de resultados
        st.markdown("#### Tabla de Resultados por SFR")
        df_show = res['tabla'].copy()
        # Colorear semáforo con emojis
        semaforo_emoji = {
            "Subiluminado (<150 lux)":       "🔵 Subiluminado",
            "Confort óptimo (ISO+IES)":       "🟢 Confort óptimo",
            "Límite UDI-Autonomous":          "🟡 Límite UDI",
            "Sobreiluminación UDI-Exceeded":  "🔴 Sobreiluminación",
        }
        df_show["Semáforo"] = df_show["Semáforo"].map(
            lambda x: semaforo_emoji.get(x, x)
        )
        st.dataframe(df_show, use_container_width=True, hide_index=True)

        # Recomendación ejecutiva
        st.divider()
        st.markdown("### 💡 Recomendación SkyPlus")
        st.markdown(res['recomendacion'])

        st.info(
            "📋 **Disclaimer técnico:** Resultados generados por EnergyPlus 23.2 (DOE oficial). "
            "La métrica de confort visual (fc promedio) es una estimación analítica EPW bajo "
            "ISO 8995-1 e IES RP-7. Para validación espacial punto por punto, "
            "certificaciones LEED o análisis de deslumbramiento, solicita el estudio BEM detallado."
        )

        # Botón para nueva simulación
        if st.button("🔄 Nueva Simulación (limpiar resultados)"):
            st.session_state.resultado_motor    = None
            st.session_state.calculo_completado = False
            st.rerun()


# =============================================================================
# TAB 5 — REPORTE FINAL (LEAD MAGNET)
# =============================================================================
with tab_reporte:
    st.subheader("📄 Reporte Ejecutivo SkyPlus")

    if not st.session_state.calculo_completado or not st.session_state.resultado_motor:
        st.info(
            "Completa la simulación en la pestaña '📊 Simulación Energética' "
            "para generar el reporte."
        )
    else:
        res    = st.session_state.resultado_motor
        clima  = st.session_state.clima_data or {}
        md     = clima.get('metadata', {})
        ciudad = md.get('ciudad', 'N/D')
        pais   = md.get('pais', '')
        datos_domo = df_domos[df_domos['Modelo'] == modelo_sel].iloc[0]

        st.success("✅ El reporte está listo.")

        # Vista previa del contenido
        with st.expander("👁️ Vista previa del reporte", expanded=True):
            st.markdown(f"""
**REPORTE TÉCNICO SKYPLUS**
---
**Proyecto:** Nave Industrial {ancho_nave:.0f}×{largo_nave:.0f}×{alto_nave:.0f} m  
**Uso:** {tipo_uso}  
**Ubicación:** {ciudad}, {pais}  
**Clima:** {st.session_state.estacion_seleccionada or 'N/D'} (TMYx OneBuilding.org)  
**Domo:** {modelo_sel}  
**Fecha:** {pd.Timestamp.now().strftime('%d/%m/%Y')}

---
{res['recomendacion']}

---
**Motor:** EnergyPlus 23.2 (DOE) + Método analítico EPW  
**Normativa:** ISO 8995-1:2002 | ANSI/IES RP-7-21 | UDI Mardaljevic 2006
            """)

        st.divider()

        # Formulario de captura de leads (desbloquea descarga del PDF)
        st.markdown("### 📬 Recibe el reporte completo en PDF")
        st.caption(
            "Ingresa tus datos para descargar el reporte detallado con "
            "curvas de optimización, tabla SFR completa y recomendación técnica."
        )

        with st.form("formulario_leads"):
            col_f1, col_f2 = st.columns(2)
            nombre_contacto  = col_f1.text_input("Nombre completo *")
            empresa_contacto = col_f2.text_input("Empresa *")
            correo_contacto  = col_f1.text_input("Correo electrónico *")
            telefono_contacto= col_f2.text_input("Teléfono (opcional)")
            comentario       = st.text_area("¿Tienes alguna pregunta o proyecto específico?", height=80)

            enviado = st.form_submit_button(
                "📥 Descargar Reporte PDF", use_container_width=True, type="primary"
            )

        if enviado:
            if nombre_contacto and empresa_contacto and correo_contacto:
                st.success(
                    f"✅ ¡Gracias, **{nombre_contacto}**! "
                    f"Te enviamos el reporte a **{correo_contacto}** en breve."
                )
                st.balloons()
                # TODO: aquí irá la lógica de:
                # 1. Generar PDF con WeasyPrint/ReportLab
                # 2. Enviar por correo (SendGrid / Mailgun)
                # 3. Guardar lead en base de datos / Google Sheets
                st.info(
                    "💼 Un consultor de Eco Consultor se pondrá en contacto "
                    "contigo para discutir el proyecto en detalle."
                )
            else:
                st.error("⚠️ Por favor completa todos los campos obligatorios (*).")

        st.divider()
        st.markdown("### 🤝 ¿Necesitas más precisión?")
        col_cta1, col_cta2 = st.columns(2)
        with col_cta1:
            st.info(
                "**🔬 Estudio BEM Premium**  \n"
                "Simulación espacial con Radiance. "
                "Validación punto por punto, certificaciones LEED, "
                "análisis de deslumbramiento.\n\n"
                "*Contacta a tu consultor Sunoptics.*"
            )
        with col_cta2:
            st.info(
                "**📐 Proyecto Ejecutivo**  \n"
                "Ingeniería completa: layout de domos, "
                "especificaciones técnicas, presupuesto y ROI detallado.\n\n"
                "*Diseñado para licitaciones y proyectos de inversión.*"
            )
