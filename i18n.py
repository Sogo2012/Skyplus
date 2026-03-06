# =============================================================================
# i18n.py — SkyPlus Bilingual Dictionary
# ES: Español técnico (sistema métrico, ISO/IES)
# EN: Technical English (imperial + metric, ASHRAE/IES)
# =============================================================================
# USAGE:
#   from i18n import T, UNITS, get_units
#   lang  = st.session_state.get("lang", "ES")
#   units = st.session_state.get("units", "metric")
#   label = T("sidebar_geometry", lang)
# =============================================================================

# ── UNIT CONVERSION FACTORS (metric → imperial) ──────────────────────────────
CONVERSION = {
    # Length
    "m_to_ft":      3.28084,
    "m2_to_ft2":    10.7639,
    # Illuminance
    "lux_to_fc":    0.09290,   # 1 lux = 0.0929 fc  (÷ 10.764)
    # Energy
    "kwh_to_kbtu":  3.41214,
    "kwh_to_mmbtu": 0.00341,
    # Thermal
    "wm2k_to_btuh": 0.17611,   # U-value W/m²K → BTU/hr·ft²·°F
    "celsius_to_f": lambda c: c * 9/5 + 32,
    # Power density
    "wm2_to_btuh_ft2": 0.31699,
}

# ── NORMATIVE ILLUMINANCE SETPOINTS ──────────────────────────────────────────
# IES RP-7 values in both lux and footcandles
SETPOINTS = {
    "Warehouse":    {"lux": 300,  "fc": 30,  "ES": "Bodega",          "EN": "Warehouse"},
    "Manufacturing":{"lux": 500,  "fc": 50,  "ES": "Manufactura",     "EN": "Manufacturing"},
    "Retail":       {"lux": 500,  "fc": 50,  "ES": "Retail / Tienda", "EN": "Retail"},
    "SuperMarket":  {"lux": 500,  "fc": 50,  "ES": "Supermercado",    "EN": "Supermarket"},
    "MediumOffice": {"lux": 500,  "fc": 50,  "ES": "Oficina Mediana", "EN": "Medium Office"},
}

# ── NORMATIVE THRESHOLDS ─────────────────────────────────────────────────────
THRESHOLDS = {
    "udi_lower":  {"lux": 100,  "fc": 9.3,   "label_ES": "Subiluminado",        "label_EN": "Underlit"},
    "udi_opt_lo": {"lux": 300,  "fc": 27.9,  "label_ES": "Confort óptimo",      "label_EN": "Visual comfort"},
    "udi_opt_hi": {"lux": 3000, "fc": 278.7, "label_ES": "Límite UDI",          "label_EN": "UDI upper limit"},
    "udi_upper":  {"lux": 3000, "fc": 278.7, "label_ES": "Sobreiluminación",    "label_EN": "UDI-Exceeded"},
}

# ── COMPLIANCE STATUS LABELS ──────────────────────────────────────────────────
COMPLIANCE = {
    "Subiluminado (<150 lux)": {
        "ES": "Subiluminado",
        "EN": "Non-compliant — Underlit",
    },
    "Confort óptimo (ISO+IES)": {
        "ES": "Confort óptimo",
        "EN": "Compliant — IES RP-7 / ISO 8995-1",
    },
    "Límite UDI-Autonomous": {
        "ES": "Límite UDI",
        "EN": "Caution — UDI-Autonomous limit",
    },
    "Sobreiluminación UDI-Exceeded": {
        "ES": "Sobreiluminación",
        "EN": "Non-compliant — UDI-Exceeded",
    },
}

# =============================================================================
# MAIN DICTIONARY — all UI strings indexed by key
# Format: "key": {"ES": "...", "EN": "..."}
# =============================================================================
STRINGS = {

    # ── APP TITLE & BRANDING ─────────────────────────────────────────────────
    "app_title": {
        "ES": "SkyPlus — ECO Consultor",
        "EN": "SkyPlus — ECO Consultor",
    },
    "app_tagline": {
        "ES": "Optimización de iluminación natural para naves industriales · Motor EnergyPlus 23.2",
        "EN": "Daylighting optimization for industrial facilities · EnergyPlus 23.2 engine",
    },

    # ── TABS ─────────────────────────────────────────────────────────────────
    "tab_climate":   {"ES": "Selección de Clima",    "EN": "Climate Selection"},
    "tab_context":   {"ES": "Contexto Climático",    "EN": "Climate Context"},
    "tab_3d":        {"ES": "Geometría 3D",          "EN": "3D Geometry"},
    "tab_energy":    {"ES": "Simulación Energética", "EN": "Energy Simulation"},

    # ── SIDEBAR SECTIONS ─────────────────────────────────────────────────────
    "sidebar_01":    {"ES": "01 — Ubicación y Clima",     "EN": "01 — Location & Climate"},
    "sidebar_02":    {"ES": "02 — Geometría de la Nave",  "EN": "02 — Facility Geometry"},
    "sidebar_03":    {"ES": "03 — Tipo de Uso",           "EN": "03 — Occupancy Type"},
    "sidebar_04":    {"ES": "04 — Domo Sunoptics®",       "EN": "04 — Sunoptics® Skylight"},

    # ── LOCATION & CLIMATE ───────────────────────────────────────────────────
    "search_location":        {"ES": "Buscar ubicación",              "EN": "Search location"},
    "search_by_name":         {"ES": "Buscar por nombre",             "EN": "Search by name"},
    "search_by_coords":       {"ES": "Buscar por coordenadas",        "EN": "Search by coordinates"},
    "latitude":               {"ES": "Latitud",                       "EN": "Latitude"},
    "longitude":              {"ES": "Longitud",                      "EN": "Longitude"},
    "climate_active":         {"ES": "Clima activo",                  "EN": "Climate loaded"},
    "no_climate":             {"ES": "Sin archivo climático",         "EN": "No climate file"},
    "stations_found":         {"ES": "estaciones encontradas",        "EN": "stations found"},
    "stations_available":     {"ES": "Estaciones disponibles",        "EN": "Available stations"},
    "select_station":         {"ES": "Selecciona una estación para descargar el archivo .epw:",
                               "EN": "Select a station to download the .epw file:"},
    "download_climate":       {"ES": "Descargar datos climáticos",    "EN": "Download climate data"},
    "distance_km":            {"ES": "Distancia",                     "EN": "Distance"},
    "interactive_map":        {"ES": "Mapa interactivo del proyecto", "EN": "Interactive project map"},
    "map_caption":            {"ES": "Haz clic en el mapa para buscar estaciones climáticas en ese punto.",
                               "EN": "Click on the map to search climate stations at that point."},

    # ── ERRORS & STATUS ──────────────────────────────────────────────────────
    "err_no_stations":        {"ES": "No se encontraron estaciones para esta ubicación.",
                               "EN": "No climate stations found for this location."},
    "err_geocoder_notfound":  {"ES": "No se pudo localizar ese lugar. Intenta con el nombre en inglés o usa coordenadas.",
                               "EN": "Location not found. Try a different spelling or use coordinates."},
    "err_geocoder_timeout":   {"ES": "Timeout al conectar con el geocodificador. Intenta de nuevo o ingresa las coordenadas manualmente.",
                               "EN": "Geocoder connection timeout. Please retry or enter coordinates manually."},
    "err_geocoder_service":   {"ES": "Servicio de geocodificación no disponible. Usa las coordenadas manualmente.",
                               "EN": "Geocoding service unavailable. Please use manual coordinates."},
    "err_epw_unavailable":    {"ES": "Archivo no disponible. Intenta otra estación.",
                               "EN": "File unavailable. Please try another station."},
    "err_epw_process":        {"ES": "Error al procesar el EPW con Ladybug.",
                               "EN": "Error processing EPW file with Ladybug."},
    "err_motor_unavailable":  {"ES": "El motor EnergyPlus no está disponible. Despliega la aplicación en Docker + Google Cloud Run.",
                               "EN": "EnergyPlus engine not available. Deploy the app on Docker + Google Cloud Run."},
    "err_no_climate_sim":     {"ES": "Descarga un archivo climático en Selección de Clima para habilitar la simulación.",
                               "EN": "Download a climate file in Climate Selection to enable simulation."},
    "err_epw_missing":        {"ES": "Archivo EPW no disponible. Vuelve a descargar el clima.",
                               "EN": "EPW file not found. Please re-download the climate data."},
    "err_area_too_large":     {"ES": "Área > 10,000 m². Proyectos de esta escala requieren el servicio BEM Premium.",
                               "EN": "Area > 107,640 ft². Projects of this scale require BEM Premium service."},
    "err_3d_engine":          {"ES": "Error en el motor 3D",          "EN": "3D engine error"},
    "err_sim_generic":        {"ES": "Error en la simulación",        "EN": "Simulation error"},
    "err_sim_launch":         {"ES": "No se pudo lanzar el análisis", "EN": "Could not launch analysis"},
    "err_lead_incomplete":    {"ES": "Completa nombre, empresa y correo para continuar.",
                               "EN": "Please complete name, company and email to continue."},
    "err_contact_us":         {"ES": "Intenta nuevamente o contáctanos en ingenieria@ecoconsultor.com",
                               "EN": "Please retry or contact us at ingenieria@ecoconsultor.com"},
    "err_epw_prep":           {"ES": "No se pudo preparar el archivo climático. Intenta nuevamente.",
                               "EN": "Could not prepare climate file. Please try again."},

    # ── GEOMETRY ─────────────────────────────────────────────────────────────
    "width_m":           {"ES": "Ancho (m)",   "EN": "Width (ft)"},
    "length_m":          {"ES": "Largo (m)",   "EN": "Length (ft)"},
    "height_m":          {"ES": "Altura (m)",  "EN": "Height (ft)"},
    "width_label":       {"ES": "Ancho",       "EN": "Width"},
    "length_label":      {"ES": "Largo",       "EN": "Length"},
    "height_label":      {"ES": "Height",      "EN": "Height"},
    "floor_area":        {"ES": "Área de planta", "EN": "Floor area"},
    "bem_required":      {"ES": "Requiere servicio BEM Premium", "EN": "Requires BEM Premium service"},

    # ── OCCUPANCY TYPES ──────────────────────────────────────────────────────
    "occ_warehouse":     {"ES": "Bodega / Warehouse",  "EN": "Warehouse"},
    "occ_manufacturing": {"ES": "Manufactura",          "EN": "Manufacturing"},
    "occ_retail":        {"ES": "Retail / Tienda",      "EN": "Retail"},
    "occ_supermarket":   {"ES": "Supermercado",         "EN": "Supermarket"},
    "occ_office":        {"ES": "Oficina Mediana",      "EN": "Medium Office"},

    # ── SKYLIGHT / DOMO ──────────────────────────────────────────────────────
    "skylight_model":    {"ES": "Modelo de domo",              "EN": "Skylight model"},
    "sfr_target":        {"ES": "Objetivo SFR (%)",            "EN": "Target SFR (%)"},
    "sfr_full":          {"ES": "Superficie de Fenestración en Techo",
                          "EN": "Skylight-to-Roof Ratio"},
    "skylight_props":    {"ES": "Propiedades del domo",        "EN": "Skylight properties"},
    "vlt":               {"ES": "VLT",                         "EN": "VLT"},
    "vlt_full":          {"ES": "Transmitancia luminosa visible",
                          "EN": "Visible Light Transmittance"},
    "shgc":              {"ES": "SHGC",                        "EN": "SHGC"},
    "shgc_full":         {"ES": "Coeficiente de ganancia solar",
                          "EN": "Solar Heat Gain Coefficient"},
    "u_value":           {"ES": "U-valor",                     "EN": "U-value"},
    "u_value_units_m":   {"ES": "W/m²K",                       "EN": "BTU/hr·ft²·°F"},
    "size":              {"ES": "Tamaño",                      "EN": "Size"},
    "units_m":           {"ES": "m",                           "EN": "ft"},
    "units_m2":          {"ES": "m²",                          "EN": "ft²"},
    "skylights_count":   {"ES": "Domos generados",             "EN": "Skylights installed"},
    "sfr_real":          {"ES": "SFR real del modelo",         "EN": "Model actual SFR"},
    "domo_sunoptics":    {"ES": "Domos Sunoptics®",            "EN": "Sunoptics® Skylights"},

    # ── CLIMATE CONTEXT TABS ─────────────────────────────────────────────────
    "wind_rose":         {"ES": "Rosa de vientos anual",       "EN": "Annual wind rose"},
    "radiation_balance": {"ES": "Balance de irradiación",      "EN": "Solar radiation balance"},
    "radiation_caption": {"ES": "Justificación técnica para domos prismáticos de alta difusión.",
                          "EN": "Technical rationale for high-diffusion prismatic skylights."},
    "temp_heatmap":      {"ES": "Mapa de calor anual — temperatura de bulbo seco (°C)",
                          "EN": "Annual dry-bulb temperature heatmap (°F)"},
    "temp_caption":      {"ES": "8,760 horas del año. Picos críticos de calor y demanda HVAC.",
                          "EN": "8,760 annual hours. Critical heat peaks and HVAC demand."},
    "thermodynamics":    {"ES": "Termodinámica del sitio",     "EN": "Site thermodynamics"},
    "cloudiness":        {"ES": "Perfil de nubosidad mensual", "EN": "Monthly cloud cover profile"},
    "bioclim_download":  {"ES": "Descarga un archivo climático en la pestaña 'Selección de Clima' para visualizar el análisis bioclimático.",
                          "EN": "Download a climate file in the 'Climate Selection' tab to view the bioclimatic analysis."},

    # ── 3D GEOMETRY TAB ──────────────────────────────────────────────────────
    "tab_3d_title":      {"ES": "Visualización 3D — Nave Industrial",
                          "EN": "3D Visualization — Industrial Facility"},
    "tab_3d_subtitle":   {"ES": "Distribución de domos Sunoptics® y análisis de cumplimiento ASHRAE 90.1",
                          "EN": "Sunoptics® skylight layout and ASHRAE 90.1 compliance analysis"},
    "btn_generate_3d":   {"ES": "Generar modelo 3D",           "EN": "Generate 3D model"},
    "ashrae_compliant":  {"ES": "ASHRAE 90.1 — Cumple sin controles (SFR ≤ 3%)",
                          "EN": "ASHRAE 90.1 — Compliant, no controls required (SFR ≤ 3%)"},
    "ashrae_controls":   {"ES": "ASHRAE 90.1 — Requiere daylighting controls (SFR ≤ 5%)",
                          "EN": "ASHRAE 90.1 — Daylighting controls required (SFR ≤ 5%)"},
    "ashrae_exceeds":    {"ES": "ASHRAE 90.1 — Excede límite (SFR > 5%)",
                          "EN": "ASHRAE 90.1 — Exceeds limit (SFR > 5%)"},
    "sunpath_toggle":    {"ES": "Mostrar trayectoria solar",   "EN": "Show sun path"},

    # ── ENERGY SIMULATION ────────────────────────────────────────────────────
    "tab_energy_title":  {"ES": "Simulación Energética",       "EN": "Energy Simulation"},
    "tab_energy_sub":    {"ES": "Motor 1: EnergyPlus 23.2 (DOE) — kWh reales  ·  Motor 2: EPW analítico — Iluminancia + Semáforo normativo",
                          "EN": "Engine 1: EnergyPlus 23.2 (DOE) — actual kWh  ·  Engine 2: Analytical EPW — Illuminance + Compliance status"},
    "facility_label":    {"ES": "Nave",                        "EN": "Facility"},
    "area_label":        {"ES": "Área",                        "EN": "Area"},
    "climate_label":     {"ES": "Clima",                       "EN": "Climate"},
    "sfr_design":        {"ES": "SFR diseño",                  "EN": "Design SFR"},
    "btn_simulate":      {"ES": "Simular mi nave",             "EN": "Run energy simulation"},
    "sim_compare":       {"ES": "Compara tu nave <strong>sin domos vs con SFR={sfr}%</strong>.",
                          "EN": "Compare your facility <strong>without skylights vs SFR={sfr}%</strong>."},

    # ── ENERGY RESULTS ───────────────────────────────────────────────────────
    "energy_results":    {"ES": "Resultado energético — tu nave",     "EN": "Energy results — your facility"},
    "savings_label":     {"ES": "Ahorro con tu diseño",               "EN": "Savings with your design"},
    "base_consumption":  {"ES": "Consumo base sin domos",             "EN": "Baseline consumption (no skylights)"},
    "skylights_sfr":     {"ES": "Domos instalados — SFR {sfr}%",      "EN": "Skylights installed — SFR {sfr}%"},
    "visual_comfort":    {"ES": "Confort visual",                     "EN": "Visual comfort"},
    "lux_avg":           {"ES": "lux promedio zona",                  "EN": "fc avg. workplane"},
    "kwh_year":          {"ES": "kWh/año",                            "EN": "kBtu/yr"},
    "optimization_curve":{"ES": "Curva de optimización completa — SFR 0% → 6%",
                          "EN": "Full optimization curve — SFR 0% → 6%"},

    # ── LEAD FORM ────────────────────────────────────────────────────────────
    "lead_title":        {"ES": "Solicitar reporte técnico",          "EN": "Request technical report"},
    "lead_subtitle_tmpl":{"ES": "Ingresa tus datos y calculamos la curva completa para tu nave de {dim}. "
                               "Recibirás el <b>reporte técnico PDF en tu correo</b> en aproximadamente {mins} minutos.",
                          "EN": "Enter your details and we'll compute the full curve for your {dim} facility. "
                               "You'll receive the <b>technical PDF report by email</b> in approximately {mins} minutes."},
    "field_name":        {"ES": "Nombre completo *",                  "EN": "Full name *"},
    "field_company":     {"ES": "Empresa *",                          "EN": "Company *"},
    "field_email":       {"ES": "Correo electrónico *",               "EN": "Email address *"},
    "field_phone":       {"ES": "Teléfono (opcional)",                "EN": "Phone (optional)"},
    "field_comments":    {"ES": "Comentarios",                        "EN": "Comments"},
    "btn_request_report":{"ES": "Descargue Reporte Tecnico Completo", "EN": "Download Complete Technical Report"},
    "daily_limit":       {"ES": "Límite diario alcanzado.",           "EN": "Daily limit reached."},
    "daily_limit_msg":   {"ES": "La cuenta <em>{email}</em> ya tiene {n} simulaciones registradas hoy (máximo 3). "
                               "Contáctanos directamente en <strong>ingenieria@ecoconsultor.com</strong>.",
                          "EN": "Account <em>{email}</em> has reached {n} simulations today (max 3). "
                               "Contact us directly at <strong>ingenieria@ecoconsultor.com</strong>."},

    # ── PROCESSING STATUS ────────────────────────────────────────────────────
    "processing_title":  {"ES": "Análisis en progreso",               "EN": "Analysis in progress"},
    "processing_body":   {"ES": "Estamos calculando la curva de optimización completa (SFR 0%→6%) para tu nave. "
                               "Recibirás el PDF en tu correo en aproximadamente <strong>{mins} minutos</strong>.",
                          "EN": "We are computing the full optimization curve (SFR 0%→6%) for your facility. "
                               "You'll receive the PDF by email in approximately <strong>{mins} minutes</strong>."},
    "delivery_label":    {"ES": "Entrega",                            "EN": "Delivery"},
    "analysis_label":    {"ES": "Análisis",                           "EN": "Analysis"},
    "analysis_value":    {"ES": "SFR 0% → 6%",                       "EN": "SFR 0% → 6%"},
    "analysis_delta":    {"ES": "Curva de optimización completa",     "EN": "Full optimization curve"},
    "btn_new_sim":       {"ES": "Nueva simulación (limpiar resultados)", "EN": "New simulation (clear results)"},

    # ── INTERESTING FACTS (rotating) ─────────────────────────────────────────
    "fact_prismatic":    {"ES": "Los domos prismáticos Sunoptics difunden la luz hasta 3× más que una ventana plana.",
                          "EN": "Sunoptics prismatic skylights diffuse light up to 3× more than flat glazing."},
    "fact_co2":          {"ES": "Cada kWh ahorrado evita ~0.45 kg de CO₂ en la red eléctrica.",
                          "EN": "Each kWh saved avoids ~0.45 kg of CO₂ from the electrical grid."},
    "fact_sfr":          {"ES": "El SFR óptimo depende de la geometría, clima y uso específico de la nave.",
                          "EN": "Optimal SFR depends on facility geometry, climate, and occupancy type."},
    "fact_ashrae":       {"ES": "ASHRAE 90.1 permite hasta SFR=5% con controles de daylighting automáticos.",
                          "EN": "ASHRAE 90.1 allows up to SFR=5% with automatic daylighting controls."},

    # ── PDF REPORT ───────────────────────────────────────────────────────────
    "pdf_title":         {"ES": "Reporte Técnico",                    "EN": "Technical Report"},
    "pdf_subtitle":      {"ES": "SkyPlus® — Optimización de Iluminación Natural con Domos Sunoptics®",
                          "EN": "SkyPlus® — Daylighting Optimization with Sunoptics® Skylights"},
    "pdf_client":        {"ES": "CLIENTE",                            "EN": "CLIENT"},
    "pdf_project":       {"ES": "PROYECTO",                           "EN": "PROJECT"},
    "pdf_name":          {"ES": "Nombre",                             "EN": "Name"},
    "pdf_company":       {"ES": "Empresa",                            "EN": "Company"},
    "pdf_email":         {"ES": "Correo",                             "EN": "Email"},
    "pdf_date":          {"ES": "Fecha",                              "EN": "Date"},
    "pdf_facility":      {"ES": "Nave",                               "EN": "Facility"},
    "pdf_use":           {"ES": "Uso",                                "EN": "Occupancy"},
    "pdf_climate":       {"ES": "Clima",                              "EN": "Climate"},
    "pdf_engine":        {"ES": "Motor",                              "EN": "Engine"},
    "pdf_geometry_title":{"ES": "Modelo Geométrico de la Nave",       "EN": "Facility Geometric Model"},
    "pdf_geometry_body": {"ES": "Distribución matricial de <b>{n} domos Sunoptics®</b> sobre una nave de "
                               "<b>{dim}</b> ({area:,.0f} m²). SFR real: <b>{sfr:.1f}%</b>.",
                          "EN": "Matrix layout of <b>{n} Sunoptics® skylights</b> over a "
                               "<b>{dim}</b> facility ({area:,.0f} ft²). Actual SFR: <b>{sfr:.1f}%</b>."},
    "pdf_ficha_title":   {"ES": "Ficha Técnica del Domo",             "EN": "Skylight Technical Data Sheet"},
    "pdf_param":         {"ES": "Parámetro",                          "EN": "Parameter"},
    "pdf_value":         {"ES": "Valor",                              "EN": "Value"},
    "pdf_description":   {"ES": "Descripción",                        "EN": "Description"},
    "pdf_model":         {"ES": "Modelo",                             "EN": "Model"},
    "pdf_vlt_desc":      {"ES": "Transmitancia luminosa visible (NFRC 200)",
                          "EN": "Visible Light Transmittance (NFRC 200)"},
    "pdf_shgc_desc":     {"ES": "Solar Heat Gain Coefficient (NFRC 200)",
                          "EN": "Solar Heat Gain Coefficient (NFRC 200)"},
    "pdf_sfr_desc":      {"ES": "Área domos / Área techo",            "EN": "Skylight area / Roof area"},
    "pdf_domos_desc":    {"ES": "Unidades instaladas en cuadrícula simétrica",
                          "EN": "Units installed in symmetric grid pattern"},
    "pdf_norm_desc":     {"ES": "Setpoint iluminación según tipo de uso",
                          "EN": "Illuminance setpoint per occupancy type"},
    "pdf_energy_title":  {"ES": "Análisis Energético — Curva de Optimización SFR",
                          "EN": "Energy Analysis — SFR Optimization Curve"},
    "pdf_energy_body":   {"ES": "Se corrieron <b>7 simulaciones EnergyPlus 23.2</b> variando el SFR de 0% a 6% "
                               "para la nave de {dim} en <b>{city}, {country}</b>. "
                               "El modelo evalúa simultáneamente el ahorro en iluminación artificial y la "
                               "penalización por carga térmica solar.",
                          "EN": "<b>7 EnergyPlus 23.2 simulations</b> were run varying SFR from 0% to 6% "
                               "for the {dim} facility in <b>{city}, {country}</b>. "
                               "The model simultaneously evaluates artificial lighting savings and "
                               "solar heat gain penalty."},
    "pdf_sfr_opt":       {"ES": "SFR Óptimo Energético",              "EN": "Energy-Optimal SFR"},
    "pdf_max_savings":   {"ES": "Ahorro máximo",                      "EN": "Maximum savings"},
    "pdf_sfr_dual":      {"ES": "SFR Óptimo Dual",                    "EN": "Dual-Optimal SFR"},
    "pdf_base_kwh":      {"ES": "Consumo base",                       "EN": "Baseline consumption"},
    "pdf_energy_comfort":{"ES": "Confort + energía",                  "EN": "Comfort + energy"},
    "pdf_no_skylights":  {"ES": "SFR=0% sin domos",                   "EN": "SFR=0% no skylights"},
    "pdf_table_title":   {"ES": "Tabla de Resultados por SFR",        "EN": "Results Table by SFR"},
    "pdf_col_sfr":       {"ES": "SFR",                                "EN": "SFR"},
    "pdf_col_domos":     {"ES": "Domos",                              "EN": "Skylights"},
    "pdf_col_ah_luz":    {"ES": "Ah. Ilum.\nkWh/año",                 "EN": "Light Sav.\nkBtu/yr"},
    "pdf_col_pen_cool":  {"ES": "Pen. Cool\nkWh/año",                 "EN": "Cool Penalty\nkBtu/yr"},
    "pdf_col_neto":      {"ES": "Neto\nkWh/año",                      "EN": "Net\nkBtu/yr"},
    "pdf_col_pct":       {"ES": "% Base",                             "EN": "% Baseline"},
    "pdf_col_lux":       {"ES": "Ilum.\nlux",                         "EN": "Illum.\nfc"},
    "pdf_col_sem":       {"ES": "Semáforo\nNormativo",                "EN": "Compliance\nStatus"},
    "pdf_comfort_title": {"ES": "Confort Visual — Disponibilidad de Luz Natural",
                          "EN": "Visual Comfort — Daylight Availability"},
    "pdf_comfort_body":  {"ES": "Mapa horario de iluminancia interior promedio para <b>SFR={sfr}%</b>. "
                               "Las zonas rojas indican períodos donde la luz natural supera el setpoint normativo, "
                               "eliminando totalmente la necesidad de iluminación artificial.",
                          "EN": "Hourly interior illuminance map for <b>SFR={sfr}%</b>. "
                               "Red zones indicate periods where daylight exceeds the normative setpoint, "
                               "fully eliminating the need for artificial lighting."},
    "pdf_recom_title":   {"ES": "Recomendación de Diseño SkyPlus®",   "EN": "SkyPlus® Design Recommendation"},
    "pdf_compliance":    {"ES": "Estado normativo SFR={sfr}%:",        "EN": "Compliance status SFR={sfr}%:"},
    "pdf_bem_title":     {"ES": "Estudio BEM Premium",                 "EN": "BEM Premium Study"},
    "pdf_bem_body":      {"ES": "Simulación espacial con Radiance.\nValidación punto por punto, certificación\nLEED v4.1, EDGE y BREEAM.\nMapas de iluminancia y deslumbramiento.",
                          "EN": "Spatial simulation with Radiance.\nPoint-by-point validation, certification\nLEED v4.1, EDGE & BREEAM.\nIlluminance and glare maps."},
    "pdf_exec_title":    {"ES": "Proyecto Ejecutivo",                  "EN": "Executive Design Package"},
    "pdf_exec_body":     {"ES": "Layout optimizado de domos.\nEspecificaciones técnicas completas.\nAnálisis de ROI y período de retorno.\nPresupuesto de instalación Sunoptics®.",
                          "EN": "Optimized skylight layout.\nComplete technical specifications.\nROI analysis and payback period.\nSunoptics® installation budget."},
    "pdf_disclaimer":    {"ES": "Para mayor información contáctenos en <b>ingenieria@ecoconsultor.com</b>  ·  "
                               "Los resultados fueron generados con EnergyPlus 23.2 (DOE). "
                               "Para certificaciones LEED, EDGE o validación espacial, se requiere estudio BEM completo.",
                          "EN": "For more information contact us at <b>ingenieria@ecoconsultor.com</b>  ·  "
                               "Results generated with EnergyPlus 23.2 (DOE). "
                               "For LEED, EDGE or spatial validation certifications, a full BEM study is required."},
    "pdf_footer":        {"ES": "SkyPlus v22.2 · ECO Consultor · {date} · EnergyPlus 23.2 (DOE) · ISO 8995-1 · ANSI/IES RP-7-21",
                          "EN": "SkyPlus v22.2 · ECO Consultor · {date} · EnergyPlus 23.2 (DOE) · ISO 8995-1 · ANSI/IES RP-7-21"},
    "pdf_normativa":     {"ES": "Normativa aplicada:  ISO 8995-1:2002 (CIE S 008)  ·  ANSI/IES RP-7-21  ·  UDI Mardaljevic 2006  ·  ASHRAE 90.1-2022  ·  EnergyPlus 23.2 DOE",
                          "EN": "Standards applied:  ISO 8995-1:2002 (CIE S 008)  ·  ANSI/IES RP-7-21  ·  UDI Mardaljevic 2006  ·  ASHRAE 90.1-2022  ·  EnergyPlus 23.2 DOE"},
    "pdf_client_notes":  {"ES": "Notas del Cliente",                   "EN": "Client Notes"},

    # ── EMAIL ────────────────────────────────────────────────────────────────
    "email_subject":     {"ES": "Reporte SkyPlus — Nave {dim} {use}",  "EN": "SkyPlus Report — {dim} {use} Facility"},
    "email_greeting":    {"ES": "Estimado/a {name},",                  "EN": "Dear {name},"},
    "email_body":        {"ES": "Adjunto encontrará su Reporte Técnico SkyPlus con el análisis completo de optimización de iluminación natural para su nave industrial.",
                          "EN": "Please find attached your SkyPlus Technical Report with the complete daylighting optimization analysis for your industrial facility."},
    "email_includes":    {"ES": "El reporte incluye:",                 "EN": "The report includes:"},
    "email_item1":       {"ES": "Análisis bioclimático del sitio",     "EN": "Site bioclimatic analysis"},
    "email_item2":       {"ES": "Modelo 3D de la nave con distribución de domos Sunoptics®",
                          "EN": "3D facility model with Sunoptics® skylight layout"},
    "email_item3":       {"ES": "Curva de optimización SFR 0%→6% (7 simulaciones EnergyPlus)",
                          "EN": "SFR 0%→6% optimization curve (7 EnergyPlus simulations)"},
    "email_item4":       {"ES": "Recomendación de diseño con semáforo normativo",
                          "EN": "Design recommendation with compliance status"},
    "email_item5":       {"ES": "Mapa de disponibilidad de luz natural",
                          "EN": "Daylight availability map"},
    "email_signature":   {"ES": "Equipo ECO Consultor",               "EN": "ECO Consultor Team"},


    # ── CLIMATE CONTEXT CARDS ─────────────────────────────────────────────
    "rel_humidity":  {"ES": "Humedad relativa media",  "EN": "Mean relative humidity"},
    "wind_speed":    {"ES": "Velocidad viento media",  "EN": "Mean wind speed"},
    "days_of_year":  {"ES": "Días del año (Enero → Diciembre)", "EN": "Days of year (Jan → Dec)"},

    # ── PROJECT SUMMARY ──────────────────────────────────────────────────
    "project_summary": {"ES": "Resumen del proyecto a simular", "EN": "Project summary"},
    "sfr_design":      {"ES": "SFR diseño",                     "EN": "Design SFR"},

    # ── 3D LABELS ────────────────────────────────────────────────────────
    "width_label":   {"ES": "Ancho",   "EN": "Width"},
    "length_label":  {"ES": "Largo",   "EN": "Length"},
    "height_label":  {"ES": "Altura",  "EN": "Height"},

    # ── LANGUAGE SELECTOR ────────────────────────────────────────────────────
    "lang_selector":     {"ES": "Idioma / Language",                   "EN": "Idioma / Language"},
    "units_selector":    {"ES": "Sistema de unidades",                 "EN": "Unit system"},
    "units_metric":      {"ES": "Métrico (m, kWh, lux)",               "EN": "Metric (m, kWh, lux)"},
    "units_imperial":    {"ES": "Imperial (ft, kBtu, fc)",             "EN": "Imperial (ft, kBtu, fc)"},
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def T(key: str, lang: str = "ES") -> str:
    """Get translated string. Falls back to ES if key not found in EN."""
    entry = STRINGS.get(key)
    if entry is None:
        return f"[{key}]"
    return entry.get(lang, entry.get("ES", f"[{key}]"))


def convert_length(val: float, units: str) -> float:
    """Convert meters to feet if imperial."""
    return val * CONVERSION["m_to_ft"] if units == "imperial" else val


def convert_area(val: float, units: str) -> float:
    """Convert m² to ft² if imperial."""
    return val * CONVERSION["m2_to_ft2"] if units == "imperial" else val


def convert_illuminance(val: float, units: str) -> float:
    """Convert lux to footcandles if imperial."""
    return val * CONVERSION["lux_to_fc"] if units == "imperial" else val


def convert_energy(val: float, units: str) -> float:
    """Convert kWh to kBtu if imperial."""
    return val * CONVERSION["kwh_to_kbtu"] if units == "imperial" else val


def convert_uvalue(val: float, units: str) -> float:
    """Convert W/m²K to BTU/hr·ft²·°F if imperial."""
    return val * CONVERSION["wm2k_to_btuh"] if units == "imperial" else val


def fmt_length(val: float, units: str, decimals: int = 0) -> str:
    """Format length with unit label."""
    v = convert_length(val, units)
    u = "ft" if units == "imperial" else "m"
    return f"{v:,.{decimals}f} {u}"


def fmt_area(val: float, units: str) -> str:
    """Format area with unit label."""
    v = convert_area(val, units)
    u = "ft²" if units == "imperial" else "m²"
    return f"{v:,.0f} {u}"


def fmt_illuminance(val: float, units: str, decimals: int = 0) -> str:
    """Format illuminance with unit label."""
    v = convert_illuminance(val, units)
    u = "fc" if units == "imperial" else "lux"
    return f"{v:,.{decimals}f} {u}"


def fmt_energy(val: float, units: str) -> str:
    """Format energy with unit label."""
    v = convert_energy(val, units)
    u = "kBtu/yr" if units == "imperial" else "kWh/año"
    return f"{v:,.0f} {u}"


def fmt_uvalue(val: float, units: str) -> str:
    """Format U-value with unit label."""
    v = convert_uvalue(val, units)
    u = "BTU/hr·ft²·°F" if units == "imperial" else "W/m²K"
    return f"{v:.3f} {u}"


def fmt_dims(ancho: float, largo: float, alto: float, units: str) -> str:
    """Format nave dimensions as string."""
    a = convert_length(ancho, units)
    l = convert_length(largo, units)
    h = convert_length(alto, units)
    u = "ft" if units == "imperial" else "m"
    return f"{a:.0f}×{l:.0f}×{h:.0f} {u}"


def get_setpoint(occ_type: str, units: str) -> float:
    """Get normative illuminance setpoint in correct units."""
    sp = SETPOINTS.get(occ_type, SETPOINTS["Warehouse"])
    return sp["fc"] if units == "imperial" else sp["lux"]


def get_occupancy_label(occ_type: str, lang: str) -> str:
    """Get translated occupancy label."""
    sp = SETPOINTS.get(occ_type, SETPOINTS["Warehouse"])
    return sp.get(lang, sp["ES"])


def get_compliance_label(semaforo_key: str, lang: str) -> str:
    """Get translated compliance status."""
    entry = COMPLIANCE.get(semaforo_key)
    if entry:
        return entry.get(lang, entry["ES"])
    return semaforo_key
