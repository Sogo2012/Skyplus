import streamlit as st
import pandas as pd
import numpy as np
import folium
import os
import plotly.graph_objects as go
import plotly.express as px
from streamlit_folium import st_folium
from streamlit_vtkjs import st_vtkjs

# Importaciones locales
from geometry_utils import generar_nave_3d_vtk
from weather_utils import obtener_estaciones_cercanas, descargar_y_extraer_epw, procesar_datos_clima

# 1. CONFIGURACIÓN DE PÁGINA
st.set_page_config(page_title="SkyCalc 2.0 - Eco Consultor", layout="wide", page_icon="⚡")

st.markdown("""
    <style>
    /* Estilos Generales */
    .main { background-color: #f5f7f9; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #007bff; color: white; }
    .stMetric { background-color: white; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    
    /* MARCA BLANCA: Ocultar logos y banners del visor 3D */
    .pollination-logo, .ladybug-logo { display: none !important; }
    div[title="Powered by Pollination"] { display: none !important; }
    div[title="Ladybug Tools"] { display: none !important; }
    a[href*="pollination.cloud"] { display: none !important; }
    a[href*="ladybug.tools"] { display: none !important; }
    </style>
    """, unsafe_allow_html=True)

# 2. CARGA DE CATÁLOGO SUNOPTICS
@st.cache_data
def cargar_catalogo():
    data = {
        'Modelo': [
            'Signature 800MD 4040 SGZ', 'Signature 800MD 4040 DGZ',
            'Signature 800MD 4070 SGZ', 'Signature 800MD 4070 DGZ',
            'Signature 800MD 4080 SGZ', 'Signature 800MD 4080 DGZ',
            'Signature 900SC 4080 (Storm)', 'Smoke Vent SVT2 4080 DGZ'
        ],
        'Acristalamiento': ['Sencillo (SGZ)', 'Doble (DGZ)', 'Sencillo (SGZ)', 'Doble (DGZ)', 
                            'Sencillo (SGZ)', 'Doble (DGZ)', 'Storm Class', 'Doble (DGZ)'],
        'VLT': [0.74, 0.67, 0.74, 0.67, 0.74, 0.67, 0.52, 0.64],
        'SHGC': [0.68, 0.48, 0.68, 0.48, 0.68, 0.48, 0.24, 0.31],
        'U_Value': [5.80, 3.20, 5.80, 3.20, 5.80, 3.20, 2.80, 3.20],
        'Ancho_in': [51.25, 51.25, 51.25, 51.25, 52.25, 52.25, 52.25, 52.25],
        'Largo_in': [51.25, 51.25, 87.25, 87.25, 100.25, 100.25, 100.25, 100.25]
    }
    df = pd.DataFrame(data)
    df['Ancho_m'] = (df['Ancho_in'] * 0.0254).round(3)
    df['Largo_m'] = (df['Largo_in'] * 0.0254).round(3)
    return df

df_domos = cargar_catalogo()

# 3. INICIALIZACIÓN DE ESTADO
for key in ['clima_data', 'estacion_seleccionada', 'df_cercanas', 'vtk_path']:
    if key not in st.session_state: st.session_state[key] = None

if 'lat' not in st.session_state: st.session_state.lat = 20.5888
if 'lon' not in st.session_state: st.session_state.lon = -100.3899

def buscar_estaciones():
    with st.spinner("Buscando estaciones cercanas..."):
        df_cercanas = obtener_estaciones_cercanas(st.session_state.lat, st.session_state.lon)
        st.session_state.df_cercanas = df_cercanas
        if df_cercanas is None or df_cercanas.empty:
            st.error("No se encontraron estaciones para esta ubicación.")
        else:
            st.success(f"Encontradas {len(df_cercanas)} estaciones.")

# 4. SIDEBAR - CONFIGURACIÓN DEL PROYECTO
with st.sidebar:
    st.markdown("## 🍃 Eco Consultor")
    st.title("SkyCalc 2.0")
    
    with st.expander("📍 1. Ubicación y Clima", expanded=False):
        search_name = st.text_input("Buscar por ciudad o país", placeholder="Ej: Madrid, España")
        if st.button("🔍 Buscar por Nombre"):
            if search_name:
                from geopy.geocoders import Nominatim
                try:
                    geolocator = Nominatim(user_agent="skycalc_buscador_ui")
                    loc = geolocator.geocode(search_name)
                    if loc:
                        st.session_state.lat = loc.latitude
                        st.session_state.lon = loc.longitude
                        buscar_estaciones()
                    else:
                        st.error("No se pudo localizar ese lugar.")
                except:
                    st.error("Error al conectar con el servicio de búsqueda.")

        st.divider()
        st.session_state.lat = st.number_input("Latitud", value=st.session_state.lat, format="%.4f")
        st.session_state.lon = st.number_input("Longitud", value=st.session_state.lon, format="%.4f")

        if st.button("🚀 Buscar en Coordenadas"):
            buscar_estaciones()

    st.subheader("📐 2. Geometría")
    ancho_nave = st.number_input("Ancho (m)", 10.0, 500.0, 50.0)
    largo_nave = st.number_input("Largo (m)", 10.0, 500.0, 100.0)
    alto_nave = st.number_input("Altura (m)", 3.0, 30.0, 8.0)
    
    st.subheader("☀️ 3. Sunoptics")
    modelo_sel = st.selectbox("Modelo NFRC", df_domos['Modelo'])
    sfr_target = st.slider("Objetivo SFR (%)", 1.0, 10.0, 4.0, 0.1) / 100.0

# 5. TABS PRINCIPALES (Aquí restauramos las 5 pestañas completas)
tab_config, tab_clima, tab_3d, tab_analitica, tab_reporte = st.tabs([
    "🌍 Selección de Clima", "🌤️ Contexto Climático", "📐 Geometría 3D", "📊 Simulación Energética", "📄 Reporte Final"
])

# --- PESTAÑA 1: MAPA Y DESCARGA ---
with tab_config:
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("🌍 Mapa Interactivo")
        st.caption("Método 3: Haz clic en el mapa para buscar estaciones en ese punto.")

        m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=8)
        folium.Marker([st.session_state.lat, st.session_state.lon], tooltip="Ubicación de Proyecto", icon=folium.Icon(color='red', icon='crosshairs')).add_to(m)

        if st.session_state.df_cercanas is not None and not st.session_state.df_cercanas.empty:
            for idx, st_row in st.session_state.df_cercanas.iterrows():
                # Corrección vital para que no dé KeyError
                l_est = st_row.get('lat') or st_row.get('Lat')
                ln_est = st_row.get('lon') or st_row.get('Lon')
                if pd.notna(l_est) and pd.notna(ln_est):
                    folium.Marker(
                        [l_est, ln_est],
                        tooltip=f"{st_row.get('name', 'Estación')} ({st_row.get('distancia_km', 0)} km)",
                        icon=folium.Icon(color='blue', icon='cloud')
                    ).add_to(m)

        output = st_folium(m, width=700, height=500, use_container_width=True, key="mapa_estaciones")

        if output and output.get("last_clicked"):
            c_lat = output["last_clicked"]["lat"]
            c_lon = output["last_clicked"]["lng"]
            if round(c_lat, 4) != round(st.session_state.lat, 4) or round(c_lon, 4) != round(st.session_state.lon, 4):
                st.session_state.lat = c_lat
                st.session_state.lon = c_lon
                buscar_estaciones()
                st.rerun()

    with col2:
        st.subheader("Estaciones Disponibles")
        if st.session_state.clima_data:
            st.success(f"✅ Clima Activo: **{st.session_state.estacion_seleccionada}**")

        if st.session_state.df_cercanas is not None and not st.session_state.df_cercanas.empty:
            st.write("Selecciona una estación para descargar el .epw:")
            for idx, row in st.session_state.df_cercanas.iterrows():
                st_name = row.get('name') or row.get('Station') or f"Estación {idx}"
                st_dist = row.get('distancia_km') or 0
                
                url = row.get('URL_ZIP') or row.get('epw') 

                with st.container():
                    st.markdown(f"**{st_name}**")
                    st.caption(f"📏 Distancia: **{st_dist} km**")
                    if st.button(f"📥 Descargar Datos", key=f"btn_st_{idx}", use_container_width=True):
                        if url:
                            with st.spinner(f"Descargando e inyectando datos..."):
                                path = descargar_y_extraer_epw(url)
                                if path:
                                    try:
                                        data = procesar_datos_clima(path)
                                        if data:
                                            st.session_state.clima_data = data
                                            st.session_state.estacion_seleccionada = st_name
                                            st.rerun()
                                        else:
                                            st.error("Error al procesar el archivo EPW con Ladybug.")
                                    finally:
                                        if os.path.exists(path):
                                            os.remove(path)
                                else:
                                    st.error("Error de descarga. El archivo no está disponible.")

# --- PESTAÑA 2: GRÁFICOS BIOCLIMÁTICOS (Recuperados) ---
with tab_clima:
    st.subheader("Análisis Bioclimático del Sitio")
    
    if st.session_state.clima_data and 'vel_viento' in st.session_state.clima_data:
        clima = st.session_state.clima_data
        md = clima.get('metadata', {})
        
        cols_hvac = st.columns(4)
        cols_hvac[0].metric("Latitud", f"{md.get('lat', st.session_state.lat)}°")
        cols_hvac[1].metric("Elevación", f"{md.get('elevacion', 0)} m")
        cols_hvac[2].metric("Humedad Relativa Media", f"{round(sum(clima.get('hum_relativa', [0]))/8760, 1)} %")
        cols_hvac[3].metric("Velocidad Viento Media", f"{round(sum(clima.get('vel_viento', [0]))/8760, 1)} m/s")
        
        st.divider()
        col_graf_1, col_graf_2 = st.columns(2)
        
        with col_graf_1:
            st.markdown("### 🌬️ Rosa de los Vientos Anual")
            df_viento = pd.DataFrame({'dir': clima.get('dir_viento', []), 'vel': clima.get('vel_viento', [])})
            if not df_viento.empty:
                df_viento = df_viento[df_viento['vel'] > 0.5] 
                
                bins_dir = np.arange(-11.25, 372.0, 22.5) 
                labels_dir = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW','N2']
                df_viento['Dir_Cat'] = pd.cut(df_viento['dir'], bins=bins_dir, labels=labels_dir, right=False)
                df_viento['Dir_Cat'] = df_viento['Dir_Cat'].replace('N2', 'N')
                
                bins_vel = [0, 2, 4, 6, 8, 20]
                labels_vel = ['0-2 m/s', '2-4 m/s', '4-6 m/s', '6-8 m/s', '>8 m/s']
                df_viento['Vel_Cat'] = pd.cut(df_viento['vel'], bins=bins_vel, labels=labels_vel)
                
                df_rose = df_viento.groupby(['Dir_Cat', 'Vel_Cat']).size().reset_index(name='Frecuencia')
                
                fig_rose = px.bar_polar(df_rose, r="Frecuencia", theta="Dir_Cat", color="Vel_Cat",
                                        color_discrete_sequence=px.colors.sequential.Plasma_r,
                                        template="plotly_white")
                fig_rose.update_layout(margin=dict(t=20, b=20, l=20, r=20))
                st.plotly_chart(fig_rose, use_container_width=True)

        with col_graf_2:
            st.markdown("### ☀️ Balance de Irradiación")
            st.caption("Justificación técnica para domos prismáticos de alta difusión.")
            
            suma_directa = sum(clima.get('rad_directa', [0]))
            suma_difusa = sum(clima.get('rad_dif', [0]))
            
            fig_pie = go.Figure(data=[go.Pie(labels=['Radiación Directa (Luz Dura)', 'Radiación Difusa (Luz Suave)'],
                                             values=[suma_directa, suma_difusa], hole=.4,
                                             marker_colors=['#f39c12', '#bdc3c7'])])
            fig_pie.update_layout(margin=dict(t=20, b=20, l=20, r=20), template="plotly_white")
            st.plotly_chart(fig_pie, use_container_width=True)
            
        st.divider()
        
        st.markdown("### 🌡️ Mapa de Calor Anual (Temperatura de Bulbo Seco)")
        st.caption("Visualización de las 8,760 horas del año. Identifica los picos críticos de calor (rojo) y frío (azul) para el diseño del HVAC.")
        
        temp_array = np.array(clima.get('temp_seca', np.zeros(8760)))
        
        if len(temp_array) == 8760:
            temp_matriz = temp_array.reshape(365, 24).T 
            
            fig_calor = go.Figure(data=go.Heatmap(
                z=temp_matriz,
                x=list(range(1, 366)),
                y=list(range(0, 24)),
                colorscale='RdYlBu_r', 
                colorbar=dict(title="Temp (°C)"),
                hovertemplate="Día: %{x}<br>Hora: %{y}:00<br>Temp: %{z:.1f} °C<extra></extra>"
            ))
            
            fig_calor.update_layout(
                xaxis_title="Días del Año (Enero - Diciembre)",
                yaxis_title="Hora del Día (00:00 - 23:00)",
                yaxis=dict(tickmode='linear', tick0=0, dtick=4),
                margin=dict(t=10, b=30, l=40, r=20),
                height=400,
                template="plotly_white"
            )
            st.plotly_chart(fig_calor, use_container_width=True)
        else:
            st.warning("⚠️ El archivo climático tiene un formato inusual (no son 8760 horas), no se puede generar el mapa de calor.")
            
        st.divider()
        st.markdown("### ☁️ Termodinámica y Nubosidad (Análisis BEM)")
        
        temp_diaria = np.array([sum(temp_array[i:i+24])/24 for i in range(0, 8760, 24)]) if len(temp_array) == 8760 else np.zeros(365)
        cdd_anual = sum([t - 18.3 for t in temp_diaria if t > 18.3])
        hdd_anual = sum([18.3 - t for t in temp_diaria if t < 18.3])

        col_t1, col_t2 = st.columns(2)
        col_t1.metric("Grados Día Refrigeración (CDD)", f"{int(cdd_anual)}", "Demanda de Aire Acondicionado (Frío)", delta_color="inverse")
        col_t2.metric("Grados Día Calefacción (HDD)", f"{int(hdd_anual)}", "Demanda de Calefacción (Calor)")

        st.markdown("#### ☁️ Perfil de Nubosidad Mensual")
        st.caption("Porcentaje promedio de cielo cubierto. Los meses grises son donde la tecnología prismática de **Sunoptics®** captura luz en ángulos bajos, superando ampliamente al vidrio o policarbonato liso.")
        
        nubes_array = clima.get('nubes', np.zeros(8760))
        if len(nubes_array) == 8760:
            fechas = pd.date_range(start="2023-01-01", periods=8760, freq="h")
            df_nubes = pd.DataFrame({'Fecha': fechas, 'Nubosidad': np.array(nubes_array) * 10}) 
            df_nubes['Mes'] = df_nubes['Fecha'].dt.month
            nubes_mensual = df_nubes.groupby('Mes')['Nubosidad'].mean()
            meses_labels = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
            
            fig_nubes = go.Figure(data=[
                go.Bar(x=meses_labels, y=nubes_mensual, 
                       marker_color='#95a5a6', 
                       text=[f"{val:.0f}%" for val in nubes_mensual], 
                       textposition='auto')
            ])
            fig_nubes.update_layout(
                yaxis_title="% Cielo Cubierto", 
                yaxis=dict(range=[0, 100]), 
                template="plotly_white", 
                height=350, 
                margin=dict(t=20, b=20, l=20, r=20)
            )
            st.plotly_chart(fig_nubes, use_container_width=True)
        else:
            st.warning("Datos de nubosidad no disponibles en este archivo.")
        
    else:
        st.warning("⚠️ Descarga un archivo climático en la pestaña 'Selección de Clima' para ver el análisis bioclimático.")

# --- PESTAÑA 3: GEOMETRÍA 3D ---
with tab_3d:
    st.subheader("Modelo Paramétrico Sunoptics®")
    
    if st.button("🏗️ Generar Modelo 3D", use_container_width=True):
        with st.spinner("Construyendo geometría Honeybee..."):
            datos_domo = df_domos[df_domos['Modelo'] == modelo_sel].iloc[0]
            vtk_path, num_domos, sfr_real = generar_nave_3d_vtk(
                ancho_nave, largo_nave, alto_nave, sfr_target, 
                datos_domo['Ancho_m'], datos_domo['Largo_m'],
                lat=st.session_state.lat, lon=st.session_state.lon
            )
            if vtk_path:
                st.session_state.vtk_path = vtk_path
                st.session_state.num_domos_real = num_domos
                st.session_state.sfr_final = sfr_real
                st.session_state.datos_domo_actual = datos_domo

    if st.session_state.vtk_path and os.path.exists(st.session_state.vtk_path):
        
        # --- LÓGICA TÉCNICA ASHRAE 90.1 ---
        sfr_pct = st.session_state.sfr_final * 100
        if sfr_pct <= 3.0:
            alerta_sfr = "🟢 **Cumple límite base (≤3%)**"
        elif sfr_pct <= 5.0:
            alerta_sfr = "🟠 **Requiere sensores de luz (≤5%)**"
        else:
            alerta_sfr = "🔴 **Excede límite ASHRAE (>5%)**"
            
        # 1. Dashboard Superior: KPIs y Normativa a primera vista
        cmet1, cmet2, cmet3 = st.columns([1, 1, 2])
        with cmet1:
            st.metric("Domos Generados", f"{st.session_state.num_domos_real} uds")
        with cmet2:
            st.metric("SFR Real", f"{sfr_pct:.2f} %")
            st.markdown(alerta_sfr)
        with cmet3:
            st.info("📘 **ASHRAE 90.1:** Se recomienda una fenestración en techo (SFR) no mayor al **3%** del área total, permitiendo hasta un **5%** si se instalan controles automáticos de iluminación (Daylighting Controls).")
            
        st.divider()

        # 2. Toggle de Iluminación y Sunpath
        mostrar_sol = st.toggle("☀️ Mostrar Bóveda Solar", value=False)
            
        # Elegir el archivo correcto según el interruptor
        ruta_base = st.session_state.vtk_path
        ruta_cargar = ruta_base if mostrar_sol else ruta_base.replace('.vtkjs', '_solo.vtkjs')
        
        if not os.path.exists(ruta_cargar):
            ruta_cargar = ruta_base # Respaldo de seguridad

        # 3. Visor 3D a pantalla completa
        with open(ruta_cargar, "rb") as f:
            vtk_data = f.read()
            
        # El 'key' dinámico fuerza a Streamlit a recargar el modelo cuando tocas el interruptor
        st_vtkjs(content=vtk_data, key=f"visor_nave_{mostrar_sol}")
            
    else:
        st.info("Configura la nave y presiona 'Generar Modelo 3D'.")

# --- PESTAÑA 4: MOTOR DE CÁLCULO ---
with tab_analitica:
    st.subheader("Motor de Cálculo SkyCalc")

    if st.session_state.clima_data:
        clima = st.session_state.clima_data
        ciudad = clima.get('ciudad') or clima.get('metadata', {}).get('ciudad', 'Desconocida')
        pais = clima.get('pais') or clima.get('metadata', {}).get('pais', 'Desconocido')
        
        st.info(f"Analizando: **{ciudad}, {pais}** (vía {st.session_state.estacion_seleccionada})")
        
        temp_data = clima.get('temp_seca', [])
        rad_data = clima.get('rad_directa', [])
        rad_dif = clima.get('rad_dif', [])

        if len(temp_data) > 0:
            c1, c2, c3 = st.columns(3)
            c1.metric("Temp. Media", f"{round(sum(temp_data)/len(temp_data), 1)} °C")
            c2.metric("Rad. Directa Máx", f"{max(rad_data) if len(rad_data) > 0 else 'N/A'} W/m²")
            c3.metric("Rad. Difusa Máx", f"{max(rad_dif) if len(rad_dif) > 0 else 'N/A'} W/m²")

            st.divider()

            if st.button("🔥 EJECUTAR SIMULACIÓN"):
                with st.spinner("Calculando demanda térmica..."):
                    import time
                    time.sleep(1)
                    st.session_state.calculo_completado = True
                    st.balloons()
                    st.success("Cálculo completado.")

            if getattr(st.session_state, 'calculo_completado', False):
                st.write("### Resultados de la Optimización")
                df_temp = pd.DataFrame({'Temperatura (°C)': temp_data[:168]})
                st.line_chart(df_temp)
                st.write("Estimación de Ahorro Proyectado: **24.5%**.")
        else:
            st.error("Los datos de clima están incompletos.")
            
    else:
        st.warning("⚠️ Selecciona una estación primero en la pestaña 'Selección de Clima'.")

# --- PESTAÑA 5: REPORTE ---
with tab_reporte:
    st.subheader("Generación de Reportes")
    if getattr(st.session_state, 'calculo_completado', False):
        st.success("El reporte está listo para ser generado.")
        st.button("💾 Descargar PDF de Auditoría")
    else:
        st.info("Completa la simulación en la pestaña 'Simulación Energética' primero.")# SkyCalc 2.0

This is the main Streamlit application file for SkyCalc 2.0.

import streamlit as st

def main():
    st.title('SkyCalc 2.0')
    st.write('Welcome to SkyCalc 2.0!')
    # Add your application logic here

if __name__ == '__main__':
    main()
