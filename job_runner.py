# job_runner.py
# =============================================================================
# SKYPLUS — Cloud Run Job Entry Point
# Se ejecuta como proceso independiente. Recibe configuración via variables
# de entorno inyectadas por app.py al crear el Job.
# Flujo: 7 sims EnergyPlus → PDF ReportLab → correo SMTP → Google Sheets
# =============================================================================

import os
import io
import sys
import json
import math
import logging
import smtplib
import datetime
import traceback
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import A4
from i18n import T, fmt_length, fmt_area, fmt_illuminance, fmt_energy, fmt_uvalue, fmt_dims, get_compliance_label, SETPOINTS, CONVERSION
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, white
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image as RLImage, PageBreak, HRFlowable,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("skyplus-job")

# ── Paleta ECO ────────────────────────────────────────────────────────────────
ECO_AZUL  = HexColor("#003C52")
ECO_VERDE = HexColor("#4A7C2F")
ECO_GRIS  = HexColor("#4A5568")
ECO_CLARO = HexColor("#E8F0F3")
ECO_LINEA = HexColor("#CBD5E0")

# ── Credenciales desde variables de entorno ───────────────────────────────────
GMAIL_USER     = os.getenv("GMAIL_USER",        "ingenieria@ecoconsultor.com")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
SHEETS_ID      = os.getenv("SHEETS_ID",         "")


# =============================================================================
# LEER CONFIGURACIÓN DEL JOB
# app.py inyecta los parámetros como JOB_CONFIG (JSON en variable de entorno)
# =============================================================================
def leer_config():
    config_json = os.getenv("JOB_CONFIG")
    if not config_json:
        logger.error("JOB_CONFIG no encontrada. Abortando.")
        sys.exit(1)
    try:
        return json.loads(config_json)
    except json.JSONDecodeError as e:
        logger.error(f"JOB_CONFIG inválida: {e}")
        sys.exit(1)


# =============================================================================
# VISTA ISOMÉTRICA
# =============================================================================
def generar_isometrico(ancho, largo, alto, num_domos, sfr_real, domo_ancho, domo_largo, lang="ES"):
    fig = plt.figure(figsize=(8, 5), facecolor='white')
    ax  = fig.add_subplot(111, projection='3d', facecolor='white')

    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    def draw_box(ax, x0, y0, z0, dx, dy, dz, color_face, color_edge, alpha=0.4):
        verts = [
            [(x0,y0,z0),(x0+dx,y0,z0),(x0+dx,y0+dy,z0),(x0,y0+dy,z0)],
            [(x0,y0,dz),(x0+dx,y0,dz),(x0+dx,y0+dy,dz),(x0,y0+dy,dz)],
            [(x0,y0,z0),(x0+dx,y0,z0),(x0+dx,y0,dz),(x0,y0,dz)],
            [(x0,y0+dy,z0),(x0+dx,y0+dy,z0),(x0+dx,y0+dy,dz),(x0,y0+dy,dz)],
            [(x0,y0,z0),(x0,y0+dy,z0),(x0,y0+dy,dz),(x0,y0,dz)],
            [(x0+dx,y0,z0),(x0+dx,y0+dy,z0),(x0+dx,y0+dy,dz),(x0+dx,y0,dz)],
        ]
        poly = Poly3DCollection(verts, alpha=alpha)
        poly.set_facecolor(color_face)
        poly.set_edgecolor(color_edge)
        ax.add_collection3d(poly)

    draw_box(ax, 0, 0, 0, ancho, largo, alto, '#E8F0F3', '#003C52')

    cols  = max(1, round((num_domos * (ancho / largo)) ** 0.5))
    filas = max(1, math.ceil(num_domos / cols))
    dx_d  = ancho / cols
    dy_d  = largo / filas

    for i in range(cols):
        for j in range(filas):
            cx = i * dx_d + dx_d / 2
            cy = j * dy_d + dy_d / 2
            x0 = cx - domo_ancho / 2
            y0 = cy - domo_largo / 2
            verts = [[
                (x0, y0, alto),
                (x0 + domo_ancho, y0, alto),
                (x0 + domo_ancho, y0 + domo_largo, alto),
                (x0, y0 + domo_largo, alto),
            ]]
            p = Poly3DCollection(verts, alpha=0.85)
            p.set_facecolor('#7AAFC4')
            p.set_edgecolor('#003C52')
            ax.add_collection3d(p)

    ax.set_xlim(0, ancho)
    ax.set_ylim(0, largo)
    ax.set_zlim(0, alto * 1.5)

    _M2FT = 3.28084
    if lang == "EN":
        _aw = ancho * _M2FT; _al = largo * _M2FT; _ah = alto * _M2FT
        ax.set_xlabel(f"Width (ft)", fontsize=8)
        ax.set_ylabel(f"Length (ft)", fontsize=8)
        ax.set_zlabel(f"Height (ft)", fontsize=8)
        ax.set_title(
            f"Warehouse {_aw:.0f}×{_al:.0f}×{_ah:.0f} ft — {num_domos} skylights (SFR {sfr_real*100:.1f}%)",
            fontsize=9, color='#003C52', pad=10,
        )
    else:
        ax.set_xlabel("Ancho (m)", fontsize=8)
        ax.set_ylabel("Largo (m)", fontsize=8)
        ax.set_zlabel("Altura (m)", fontsize=8)
        ax.set_title(
            f"Nave {ancho:.0f}×{largo:.0f}×{alto:.0f} m — {num_domos} domos (SFR {sfr_real*100:.1f}%)",
            fontsize=9, color='#003C52', pad=10,
        )
    ax.view_init(elev=25, azim=-60)
    ax.grid(True, alpha=0.2)

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    buf.seek(0)
    return buf.read()


# =============================================================================
# HEATMAP LUXES
# =============================================================================
def generar_heatmap_luxes(epw_path, sfr_pct, vlt, tipo_uso, lang="ES"):
    transmis = (sfr_pct / 100.0) * vlt * 0.736

    with open(epw_path, "r", errors="ignore") as f:
        lineas = f.readlines()[8:]
    illum = []
    for l in lineas:
        p = l.strip().split(",")
        try:
            illum.append(float(p[19]) * 10.0 if len(p) >= 20 else 0.0)
        except (ValueError, IndexError):
            illum.append(0.0)
    illum   = np.array(illum[:8760])
    fc_8760 = illum * transmis

    dias_mes = [31,28,31,30,31,30,31,31,30,31,30,31]
    # Bilingüe — meses según idioma
    if lang == "EN":
        meses = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        lbl_title   = f"Daylight Availability — SFR {sfr_pct}%  |  Setpoint: {{lux_sp}} lux"
        lbl_tod      = "Time of Day"
        lbl_month    = "Month"
        lbl_colorbar = "Average Lux"
    else:
        meses = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
        lbl_title   = f"Disponibilidad de Luz Natural — SFR {sfr_pct}%  |  Setpoint: {{lux_sp}} lux"
        lbl_tod      = "Hora del día"
        lbl_month    = "Mes"
        lbl_colorbar = "Lux promedio"

    matriz   = np.zeros((12, 24))
    h = 0
    for m in range(12):
        for d in range(dias_mes[m]):
            for hr in range(24):
                if h < 8760:
                    matriz[m][hr] += fc_8760[h]
                h += 1
        matriz[m] /= dias_mes[m]

    lux_sp = {"Warehouse":300,"Manufacturing":500,"Retail":500,
              "SuperMarket":500,"MediumOffice":500}.get(tipo_uso, 300)

    fig, ax = plt.subplots(figsize=(10, 4), facecolor='white')
    im = ax.imshow(matriz, aspect='auto', cmap='YlOrRd', origin='upper',
                   vmin=0, vmax=min(float(matriz.max()), 3000))
    ax.set_xticks(range(0, 24, 2))
    ax.set_xticklabels([f"{h}:00" for h in range(0, 24, 2)], fontsize=7)
    ax.set_yticks(range(12))
    ax.set_yticklabels(meses, fontsize=8)
    ax.set_xlabel(lbl_tod, fontsize=9)
    ax.set_ylabel(lbl_month, fontsize=9)
    ax.set_title(lbl_title.format(lux_sp=lux_sp), fontsize=10, color='#003C52')
    plt.colorbar(im, ax=ax, shrink=0.8).set_label(lbl_colorbar, fontsize=8)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    buf.seek(0)
    return buf.read()


# =============================================================================
# GRÁFICA CURVA SFR
# =============================================================================
def generar_grafica_curva(df_curva, sfr_opt, sfr_dual, tipo_uso, ancho, largo, lang="ES"):
    sfrs  = [r["sfr_pct"]  for r in df_curva]
    netos = [r.get("neto_kwh", 0) for r in df_curva]
    luces = [r.get("ah_luz", 0)   for r in df_curva]
    cools = [r.get("pen_cool", 0) for r in df_curva]
    luxes = [r.get("fc_lux", 0)   for r in df_curva]

    _M2FT = 3.28084
    if lang == "EN":
        _lbl_net   = "Net savings (kWh/yr)"
        _lbl_light = "Lighting savings (kWh/yr)"
        _lbl_cool  = "Cooling penalty (kWh/yr)"
        _lbl_lux   = "Avg. illuminance (lux)"
        _lbl_y1    = "Energy (kWh/yr)"
        _lbl_y2    = "Average illuminance (lux)"
        _lbl_x     = "SFR (%)"
        _aw = ancho * _M2FT; _al = largo * _M2FT
        _lbl_title = f"SkyPlus Optimization Curve — {tipo_uso} {_aw:.0f}×{_al:.0f} ft"
        _lbl_dual  = f"Dual optimum SFR={sfr_dual}%"
    else:
        _lbl_net   = "Ahorro neto (kWh/año)"
        _lbl_light = "Ahorro iluminación (kWh/año)"
        _lbl_cool  = "Penalización cooling (kWh/año)"
        _lbl_lux   = "Iluminancia (lux)"
        _lbl_y1    = "Energía (kWh/año)"
        _lbl_y2    = "Iluminancia promedio (lux)"
        _lbl_x     = "SFR (%)"
        _lbl_title = f"Curva de Optimización SkyPlus — {tipo_uso} {ancho:.0f}×{largo:.0f}m"
        _lbl_dual  = f"Óptimo Dual SFR={sfr_dual}%"

    fig, ax1 = plt.subplots(figsize=(10, 5), facecolor='white')
    ax2 = ax1.twinx()

    ax1.plot(sfrs, netos, 'o-',  color='#2ecc71', lw=2.5, label=_lbl_net,   zorder=5)
    ax1.plot(sfrs, luces, 's--', color='#3498db', lw=1.5, label=_lbl_light)
    ax1.plot(sfrs, cools, '^--', color='#e74c3c', lw=1.5, label=_lbl_cool)
    ax2.plot(sfrs, luxes, 'd:',  color='#9b59b6', lw=1.5, label=_lbl_lux)

    if sfr_opt and sfr_opt in sfrs:
        idx_o = sfrs.index(sfr_opt)
        ax1.axvline(x=sfr_opt, color='#2ecc71', ls='--', alpha=0.6)
        ax1.plot(sfr_opt, netos[idx_o], '*', color='#2ecc71', ms=14, zorder=6)

    if sfr_dual and sfr_dual in sfrs and sfr_dual != sfr_opt:
        idx_d = sfrs.index(sfr_dual)
        ax1.axvline(x=sfr_dual, color='#f39c12', ls='--', alpha=0.6)
        ax1.plot(sfr_dual, netos[idx_d], 'D', color='#f39c12', ms=10, zorder=6,
                 label=_lbl_dual)

    ax1.set_xlabel(_lbl_x, fontsize=10)
    ax1.set_ylabel(_lbl_y1, fontsize=10)
    ax2.set_ylabel(_lbl_y2, fontsize=10)
    ax1.set_title(_lbl_title, fontsize=11, color='#003C52')
    ax1.set_xticks(sfrs)
    ax1.set_xticklabels([f"{s}%" for s in sfrs])
    ax1.grid(True, alpha=0.2)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, fontsize=8, loc='lower right')

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    buf.seek(0)
    return buf.read()


# =============================================================================
# GENERAR PDF — Versión Premium con header persistente
# =============================================================================

MESES_ES = ["enero","febrero","marzo","abril","mayo","junio",
            "julio","agosto","septiembre","octubre","noviembre","diciembre"]

def fecha_es():
    h = datetime.datetime.now()
    return f"{h.day} de {MESES_ES[h.month-1]} de {h.year}"

def _draw_header(canvas_obj, doc, eco_path, sun_path, seccion=""):
    """Header blanco ECO + Sunoptics en todas las páginas."""
    W, H = A4
    canvas_obj.saveState()
    try:
        canvas_obj.setFillColor(white)
        canvas_obj.rect(0, H - 2.4*cm, W, 2.4*cm, fill=1, stroke=0)

        canvas_obj.setStrokeColor(ECO_AZUL)
        canvas_obj.setLineWidth(4)
        canvas_obj.line(0, H - 0.15*cm, W, H - 0.15*cm)

        # Logo ECO — cuadrado 800x800, mostrar bien grande
        if os.path.exists(eco_path):
            canvas_obj.drawImage(
                eco_path, 0.4*cm, H - 2.3*cm,
                width=2.8*cm, height=2.8*cm,
                preserveAspectRatio=True, mask='auto',
            )

        if seccion:
            canvas_obj.setFillColor(ECO_GRIS)
            canvas_obj.setFont("Helvetica", 7)
            canvas_obj.drawCentredString(W/2, H - 1.35*cm, seccion.upper())

        # Logo Sunoptics — horizontal 377x134, reducido
        if os.path.exists(sun_path):
            canvas_obj.drawImage(
                sun_path, W - 3.8*cm, H - 1.8*cm,
                width=3.3*cm, height=1.15*cm,
                preserveAspectRatio=True, mask='auto',
            )

        canvas_obj.setStrokeColor(ECO_VERDE)
        canvas_obj.setLineWidth(2)
        canvas_obj.line(0, H - 2.4*cm, W, H - 2.4*cm)

        canvas_obj.setFillColor(ECO_GRIS)
        canvas_obj.setFont("Helvetica", 6.5)
        canvas_obj.drawCentredString(
            W/2, 0.6*cm,
            "SkyPlus v22.2  ·  ECO Consultor  ·  EnergyPlus 23.2 (DOE)  ·  ISO 8995-1  ·  ANSI/IES RP-7-21"
        )
        canvas_obj.setStrokeColor(ECO_LINEA)
        canvas_obj.setLineWidth(0.5)
        canvas_obj.line(1.5*cm, 1.0*cm, W - 1.5*cm, 1.0*cm)

        canvas_obj.setFillColor(ECO_GRIS)
        canvas_obj.setFont("Helvetica", 7)
        canvas_obj.drawRightString(W - 1.5*cm, 0.6*cm, f"Página {doc.page}")

    except Exception as e:
        import logging
        logging.warning(f"Header error p{doc.page}: {e}")
    finally:
        canvas_obj.restoreState()


def generar_pdf(config, resultado, lead):
    eco_path = "assets/eco_logo.png"
    sun_path = "assets/sunoptics_logo.png"
    fecha    = fecha_es()

    ancho    = config.get("ancho", 0)
    largo    = config.get("largo", 0)
    alto     = config.get("altura", 0)
    tipo_uso = config.get("tipo_uso", "Warehouse")
    ciudad   = config.get("ciudad", "—")
    pais     = config.get("pais", "—")
    modelo   = config.get("modelo_domo", "Sunoptics 800MD")
    vlt      = config.get("domo_vlt", 0.67)
    shgc     = config.get("domo_shgc", 0.48)
    sfr_d    = config.get("sfr_diseno", 0.03)

    n_domos  = resultado.get("n_domos",   resultado.get("n_domos_diseno",  0))
    sfr_real_pct = resultado.get("sfr_real", resultado.get("sfr_real_diseno", sfr_d * 100))
    sfr_real = sfr_real_pct / 100 if sfr_real_pct > 1 else sfr_real_pct

    # Si n_domos sigue en 0, buscarlo en df_curva_raw según sfr_diseno
    df_curva_raw_tmp = resultado.get("df_curva_raw", [])
    if n_domos == 0 and df_curva_raw_tmp:
        sfr_d_pct = round(sfr_d * 100)
        # Buscar el SFR más cercano al de diseño
        match = next((r for r in df_curva_raw_tmp if r.get("sfr_pct") == sfr_d_pct), None)
        if match is None:
            match = min(df_curva_raw_tmp, key=lambda r: abs(r.get("sfr_pct", 0) - sfr_d_pct))
        if match:
            n_domos = match.get("n_domos", 0)
            sfr_real_raw = match.get("sfr_real_pct", sfr_d * 100)
            sfr_real = sfr_real_raw / 100 if sfr_real_raw > 1 else sfr_real_raw
            logger.info(f"n_domos calculado desde df_curva_raw: {n_domos} (SFR diseño {sfr_d_pct}%)")
    sfr_opt  = resultado.get("sfr_opt")
    sfr_dual = resultado.get("sfr_dual")
    kwh_base = resultado.get("kwh_base", 0)
    neto_opt = resultado.get("neto_opt", 0)
    pct_opt  = resultado.get("pct_opt", 0)
    df_curva = resultado.get("df_curva_raw", [])
    fc_lux_list   = resultado.get("fc_lux",         [0]*len(df_curva))
    sem_txt_list  = resultado.get("semaforo_txt",   [""]*len(df_curva))

    # Calcular ahorros reales a partir de los datos crudos del motor
    if df_curva:
        base_r    = next((r for r in df_curva if r.get("sfr_pct") == 0), df_curva[0])
        kwh_base_luz  = base_r.get("kwh_luz",     0)
        kwh_base_cool = base_r.get("kwh_cooling", 0)
        kwh_base_heat = base_r.get("kwh_heating", 0)
        kwh_base_tot  = kwh_base_luz + kwh_base_cool + kwh_base_heat

        for i, r in enumerate(df_curva):
            ah_luz   = kwh_base_luz  - r.get("kwh_luz",     0)
            pen_cool = r.get("kwh_cooling", 0) - kwh_base_cool
            ah_heat  = kwh_base_heat - r.get("kwh_heating",  0)
            neto     = ah_luz - pen_cool + ah_heat
            pct_base = (neto / kwh_base_tot * 100) if kwh_base_tot else 0
            r["ah_luz"]   = round(ah_luz)
            r["pen_cool"] = round(pen_cool)
            r["neto_kwh"] = round(neto)
            r["pct_base"] = round(pct_base, 1)
            r["fc_lux"]   = fc_lux_list[i]  if i < len(fc_lux_list)  else 0
            r["semaforo"] = sem_txt_list[i]  if i < len(sem_txt_list) else ""
    recomend = resultado.get("recomendacion", "")

    # Limpiar markdown del texto de recomendación
    import re
    recomend_limpio = re.sub(r'\*\*(.+?)\*\*', r'\1', recomend)
    recomend_limpio = re.sub(r'\*(.+?)\*',   r'\1', recomend_limpio)

    # Calcular valores del SFR óptimo dual para KPIs
    sfr_show  = sfr_dual or sfr_opt
    neto_dual = 0
    pct_dual  = 0
    lux_dual  = 0
    for r in df_curva:
        if r.get("sfr_pct") == sfr_show:
            neto_dual = r.get("neto_kwh", 0)
            pct_dual  = r.get("pct_base", 0)
            lux_dual  = r.get("fc_lux",  0)
            break

    buf = io.BytesIO()
    _L = config.get("lang", "ES")
    _U = config.get("units", "metric")

    # Márgenes con espacio para header (2.8cm top) y footer (1.5cm bottom)
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=3.0*cm, bottomMargin=1.8*cm,
        title="Reporte Técnico SkyPlus", author="ECO Consultor",
    )

    W, H = A4
    styles = getSampleStyleSheet()

    def s(nombre, parent='Normal', **kw):
        return ParagraphStyle(nombre, parent=styles[parent], **kw)

    s_titulo    = s('T',  fontSize=26, textColor=ECO_AZUL,  spaceAfter=10,  fontName='Helvetica-Bold', leading=32)
    s_subtitulo = s('ST', fontSize=11, textColor=ECO_VERDE, spaceAfter=20, leading=18)
    s_h1        = s('H1', fontSize=13, textColor=ECO_AZUL,  spaceBefore=14, spaceAfter=6,  fontName='Helvetica-Bold')
    s_h2        = s('H2', fontSize=10, textColor=ECO_VERDE, spaceBefore=8,  spaceAfter=4,  fontName='Helvetica-Bold')
    s_body      = s('B',  fontSize=9,  textColor=ECO_GRIS,  spaceAfter=4,  leading=14)
    s_small     = s('SM', fontSize=7,  textColor=ECO_GRIS,  spaceAfter=2)
    s_kpi_val   = s('KV', fontSize=18, textColor=ECO_VERDE, fontName='Helvetica-Bold', alignment=TA_CENTER, leading=22)
    s_kpi_lbl   = s('KL', fontSize=7.5,textColor=ECO_GRIS,  alignment=TA_CENTER, leading=10)
    s_kpi_sub   = s('KS', fontSize=8,  textColor=ECO_AZUL,  alignment=TA_CENTER, fontName='Helvetica-Bold')
    s_cta_t     = s('CT', fontSize=11, textColor=white,     fontName='Helvetica-Bold', spaceAfter=4)
    s_cta_b     = s('CB', fontSize=9,  textColor=white,     leading=13)
    s_disc      = s('DC', fontSize=7.5,textColor=ECO_GRIS,  leading=11, backColor=ECO_CLARO, borderPadding=6)

    secciones = [
        "",                                    # Pág 1 — sin texto en portada
        "Modelo Geométrico",
        "Análisis Energético",
        ("Visual Comfort & Recommendation" if _L=="EN" else "Confort Visual & Recomendación"),
    ]

    story = []

    # =========================================================================
    # PÁG 1 — PORTADA PREMIUM
    # =========================================================================
    # Banda azul de título
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(T("pdf_title", _L), s_titulo))
    story.append(Paragraph(T("pdf_subtitle", _L), s_subtitulo))
    story.append(HRFlowable(width="100%", thickness=2, color=ECO_VERDE, spaceAfter=14))

    # Tarjetas KPI de portada
    sfr_show_str = f"SFR {sfr_show}%" if sfr_show else "—"
    kpi_data = [[
        Paragraph(f"{pct_opt:.1f}%",       s_kpi_val),
        Paragraph(f"{neto_opt:,.0f}",       s_kpi_val),
        Paragraph(sfr_show_str,             s_kpi_val),
        Paragraph(f"{lux_dual:.0f}" if lux_dual else "—", s_kpi_val),
    ],[
        Paragraph(T("pdf_kpi_max_savings",_L), s_kpi_lbl),
        Paragraph(T("pdf_kpi_kwh_year",_L),    s_kpi_lbl),
        Paragraph(T("pdf_kpi_sfr_rec",_L), s_kpi_lbl),
        Paragraph(T("pdf_kpi_lux",_L), s_kpi_lbl),
    ]]
    col_w = (W - 3.6*cm) / 4
    t_kpi_cover = Table(kpi_data, colWidths=[col_w]*4)
    t_kpi_cover.setStyle(TableStyle([
        ('BACKGROUND',    (0,0),(-1,-1), ECO_CLARO),
        ('BACKGROUND',    (0,0),(0,-1),  HexColor("#E8F5E1")),
        ('TOPPADDING',    (0,0),(-1,-1), 10),
        ('BOTTOMPADDING', (0,0),(-1,-1), 10),
        ('LINEAFTER',     (0,0),(2,-1),  0.5, ECO_LINEA),
        ('BOX',           (0,0),(-1,-1), 1, ECO_VERDE),
        ('ROUNDEDCORNERS',(0,0),(-1,-1), [4,4,4,4]),
    ]))
    story.append(t_kpi_cover)
    story.append(Spacer(1, 0.6*cm))

    # Datos del cliente + proyecto en dos columnas
    col_cliente = [
        [Paragraph(f"<b>{T('pdf_client_header',_L)}</b>", s('lbl', fontSize=7, textColor=ECO_VERDE, fontName='Helvetica-Bold')), ""],
        [Paragraph(T("pdf_field_name",_L),   s('fl', fontSize=8, textColor=ECO_GRIS,  fontName='Helvetica-Bold')),
         Paragraph(lead.get("nombre","—"),  s('fv', fontSize=9, textColor=ECO_AZUL))],
        [Paragraph(T("pdf_field_company",_L),  s('fl2',fontSize=8, textColor=ECO_GRIS,  fontName='Helvetica-Bold')),
         Paragraph(lead.get("empresa","—"), s('fv2',fontSize=9, textColor=ECO_AZUL))],
        [Paragraph(T("pdf_field_email",_L),   s('fl3',fontSize=8, textColor=ECO_GRIS,  fontName='Helvetica-Bold')),
         Paragraph(lead.get("correo","—"),  s('fv3',fontSize=8, textColor=ECO_GRIS))],
        [Paragraph(T("pdf_field_date",_L),    s('fl4',fontSize=8, textColor=ECO_GRIS,  fontName='Helvetica-Bold')),
         Paragraph(fecha,                   s('fv4',fontSize=9, textColor=ECO_AZUL))],
    ]
    col_proyecto = [
        [Paragraph(f"<b>{T('pdf_project_header',_L)}</b>", s('lbl2', fontSize=7, textColor=ECO_VERDE, fontName='Helvetica-Bold')), ""],
        [Paragraph(T("pdf_field_building",_L),     s('pl', fontSize=8, textColor=ECO_GRIS,  fontName='Helvetica-Bold')),
         Paragraph(
         f"{ancho*3.28084:.0f}×{largo*3.28084:.0f}×{alto*3.28084:.0f} ft  ({ancho*largo*10.7639:,.0f} ft²)"
         if _U=="imperial" else
         f"{ancho:.0f}×{largo:.0f}×{alto:.0f} m  ({ancho*largo:,.0f} m²)",
         s('pv', fontSize=9, textColor=ECO_AZUL))],
        [Paragraph(T("pdf_field_usage",_L),      s('pl2',fontSize=8, textColor=ECO_GRIS,  fontName='Helvetica-Bold')),
         Paragraph(tipo_uso,   s('pv2',fontSize=9, textColor=ECO_AZUL))],
        [Paragraph(T("pdf_field_climate",_L),    s('pl3',fontSize=8, textColor=ECO_GRIS,  fontName='Helvetica-Bold')),
         Paragraph(f"{ciudad}, {pais}", s('pv3',fontSize=8, textColor=ECO_GRIS))],
        [Paragraph(T("pdf_field_engine",_L),    s('pl4',fontSize=8, textColor=ECO_GRIS,  fontName='Helvetica-Bold')),
         Paragraph("EnergyPlus 23.2 (DOE)", s('pv4',fontSize=8, textColor=ECO_GRIS))],
    ]

    t_cl = Table(col_cliente, colWidths=[2.5*cm, 6*cm])
    t_cl.setStyle(TableStyle([
        ('SPAN',         (0,0),(-1,0)),
        ('BACKGROUND',   (0,0),(-1,0), ECO_AZUL),
        ('TEXTCOLOR',    (0,0),(-1,0), white),
        ('TOPPADDING',   (0,0),(-1,-1), 5),
        ('BOTTOMPADDING',(0,0),(-1,-1), 5),
        ('LEFTPADDING',  (0,0),(-1,-1), 8),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[white, ECO_CLARO]),
        ('BOX',          (0,0),(-1,-1), 0.5, ECO_LINEA),
    ]))

    t_pr = Table(col_proyecto, colWidths=[2.0*cm, 6.5*cm])
    t_pr.setStyle(TableStyle([
        ('SPAN',         (0,0),(-1,0)),
        ('BACKGROUND',   (0,0),(-1,0), ECO_VERDE),
        ('TEXTCOLOR',    (0,0),(-1,0), white),
        ('TOPPADDING',   (0,0),(-1,-1), 5),
        ('BOTTOMPADDING',(0,0),(-1,-1), 5),
        ('LEFTPADDING',  (0,0),(-1,-1), 8),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[white, ECO_CLARO]),
        ('BOX',          (0,0),(-1,-1), 0.5, ECO_LINEA),
    ]))

    t_info = Table([[t_cl, Spacer(0.4*cm, 1), t_pr]], colWidths=[8.5*cm, 0.4*cm, 8.5*cm])
    t_info.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP')]))
    story.append(t_info)

    # Domo specs
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(T("pdf_dome_spec", _L), s_h2))
    s_lbl = s("lbl", fontSize=7.5, textColor=ECO_AZUL, fontName="Helvetica-Bold")
    s_val = s("val", fontSize=8,   textColor=ECO_AZUL)
    t_domo = Table([
        [Paragraph("Modelo",     s_lbl), Paragraph(modelo,                          s_val),
         Paragraph("VLT",        s_lbl), Paragraph(f"{vlt:.0%}",                    s_val),
         Paragraph("SHGC",       s_lbl), Paragraph(f"{shgc:.2f}",                   s_val),
         Paragraph(T("pdf_sfr_design",_L), s_lbl), Paragraph(f"{sfr_d*100:.0f}%\n{n_domos} {T('pdf_dome_units',_L)}", s_val)],
    ], colWidths=[2*cm, 4*cm, 1.2*cm, 1.5*cm, 1.5*cm, 1.5*cm, 2.5*cm, 3.0*cm])
    t_domo.setStyle(TableStyle([
        ('FONTSIZE',       (0,0),(-1,-1), 8),
        ('BACKGROUND',     (0,0),(-1,-1), ECO_CLARO),
        ('TOPPADDING',     (0,0),(-1,-1), 7),
        ('BOTTOMPADDING',  (0,0),(-1,-1), 7),
        ('LEFTPADDING',    (0,0),(-1,-1), 6),
        ('VALIGN',         (0,0),(-1,-1), 'TOP'),
        ('BOX',            (0,0),(-1,-1), 0.5, ECO_LINEA),
    ]))
    story.append(t_domo)

    # Comentarios
    comentario = lead.get("comentario","").strip()
    if comentario:
        story += [Spacer(1,0.4*cm), Paragraph(T("pdf_client_notes",_L), s_h2),
                  Paragraph(comentario, s_body)]

    # Normativa
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(
        T("pdf_normativa", _L) if False else "<b>Normativa aplicada:</b>  ISO 8995-1:2002 (CIE S 008)  ·  ANSI/IES RP-7-21  ·  "
        "UDI Mardaljevic 2006  ·  ASHRAE 90.1-2022  ·  EnergyPlus 23.2 DOE",
        s('norm', fontSize=7.5, textColor=ECO_GRIS, backColor=ECO_CLARO,
          borderPadding=5, leading=11)
    ))
    story.append(PageBreak())

    # =========================================================================
    # PÁG 2 — GEOMETRÍA
    # =========================================================================
    story.append(Paragraph(T("pdf_geometry_title", _L), s_h1))
    _M2FT = 3.28084
    if _L == "EN":
        _aw = ancho * _M2FT; _al = largo * _M2FT; _ah = alto * _M2FT
        _area_disp = f"{_aw * _al:,.0f} ft²"
        _dims_disp = f"{_aw:.0f}×{_al:.0f}×{_ah:.0f} ft"
        _geo_desc = (
            f"Matrix distribution of <b>{n_domos} Sunoptics® skylights</b> across a "
            f"<b>{_dims_disp}</b> ({_area_disp}) warehouse. "
            f"Actual Skylight-to-Floor Ratio (SFR): <b>{sfr_real*100:.1f}%</b>."
        )
    else:
        _dims_disp = f"{ancho:.0f}×{largo:.0f}×{alto:.0f} m"
        _area_disp = f"{ancho*largo:,.0f} m²"
        _geo_desc = (
            f"Distribución matricial de <b>{n_domos} domos Sunoptics®</b> sobre una nave de "
            f"<b>{_dims_disp}</b> ({_area_disp}). "
            f"Superficie de Fenestración en Techo (SFR) real: <b>{sfr_real*100:.1f}%</b>."
        )
    story.append(Paragraph(_geo_desc, s_body))
    story.append(Spacer(1, 0.3*cm))
    try:
        iso = generar_isometrico(ancho, largo, alto, n_domos, sfr_real,
                                 config.get("domo_ancho_m", 1.328),
                                 config.get("domo_largo_m", 2.547),
                                 lang=_L)
        story.append(RLImage(io.BytesIO(iso), width=15*cm, height=10*cm, kind='proportional'))
    except Exception as e:
        story.append(Paragraph(f"[Vista isométrica no disponible: {e}]", s_small))

    # Tabla técnica domo
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(T("pdf_ficha_title", _L), s_h2))
    t_ficha = Table([
        [T("pdf_ficha_param",_L), T("pdf_ficha_value",_L), T("pdf_ficha_desc",_L)],
        [T("pdf_dome_label",_L),      Paragraph(modelo, s("mod", fontSize=8, textColor=ECO_GRIS, wordWrap="CJK")), T("pdf_vlt_desc",_L) if False else "Referencia Sunoptics®"],
        ["VLT",         f"{vlt:.0%}",     "Transmitancia luminosa visible (NFRC 200)"],
        ["SHGC",        f"{shgc:.2f}",    "Solar Heat Gain Coefficient (NFRC 200)"],
        [T("pdf_sfr_design",_L), f"{sfr_d*100:.0f}%", T("pdf_ficha_sfr_desc",_L)],
        [T("pdf_ficha_domes_row",_L), str(n_domos), T("pdf_ficha_domes_desc",_L)],
        [T("pdf_ficha_norm",_L), T("pdf_ficha_norm_val",_L), T("pdf_ficha_norm_desc",_L)],
    ], colWidths=[3.0*cm, 4.5*cm, 10.0*cm])
    t_ficha.setStyle(TableStyle([
        ('FONTNAME',       (0,0),(-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',       (0,0),(-1,-1), 8.5),
        ('TEXTCOLOR',      (0,0),(-1,0), white),
        ('BACKGROUND',     (0,0),(-1,0), ECO_AZUL),
        ('ROWBACKGROUNDS', (0,1),(-1,-1), [white, ECO_CLARO]),
        ('ALIGN',          (0,0),(-1,-1), 'LEFT'),
        ('TOPPADDING',     (0,0),(-1,-1), 5),
        ('BOTTOMPADDING',  (0,0),(-1,-1), 5),
        ('LEFTPADDING',    (0,0),(-1,-1), 8),
        ('GRID',           (0,0),(-1,-1), 0.3, ECO_LINEA),
    ]))
    story.append(t_ficha)
    story.append(PageBreak())

    # =========================================================================
    # PÁG 3 — ANÁLISIS ENERGÉTICO
    # =========================================================================
    story.append(Paragraph(T("pdf_energy_title", _L), s_h1))
    if _L == "EN":
        _aw = ancho * _M2FT; _al = largo * _M2FT
        _energy_intro = (
            f"<b>7 EnergyPlus 23.2 simulations</b> were run sweeping SFR from 0% to 6% "
            f"for the {_aw:.0f}×{_al:.0f} ft warehouse in <b>{ciudad}, {pais}</b>. "
            "The model simultaneously evaluates artificial lighting savings and the "
            "thermal penalty from solar heat gain through the skylights."
        )
    else:
        _energy_intro = (
            f"Se corrieron <b>7 simulaciones EnergyPlus 23.2</b> variando el SFR de 0% a 6% "
            f"para la nave de {ancho:.0f}×{largo:.0f} m en <b>{ciudad}, {pais}</b>. "
            "El modelo evalúa simultáneamente el ahorro en iluminación artificial y la "
            "penalización por carga térmica solar."
        )
    story.append(Paragraph(_energy_intro, s_body))
    story.append(Spacer(1, 0.3*cm))

    # 4 KPI cards
    kpi_rows = [[
        Paragraph(f"{sfr_opt}%",                s_kpi_val),
        Paragraph(f"{pct_opt:.1f}%",            s_kpi_val),
        Paragraph(f"{neto_opt:,.0f}",           s_kpi_val),
        Paragraph(f"{kwh_base:,.0f}",           s_kpi_val),
    ],[
        Paragraph(T("pdf_kpi_sfr_opt",_L), s_kpi_lbl),
        Paragraph(T("pdf_kpi_max_pct",_L), s_kpi_lbl),
        Paragraph(T("pdf_kpi_kwh_saved",_L), s_kpi_lbl),
        Paragraph(T("pdf_kpi_kwh_base",_L), s_kpi_lbl),
    ]]
    t_kpis = Table(kpi_rows, colWidths=[col_w]*4)
    t_kpis.setStyle(TableStyle([
        ('BACKGROUND',    (0,0),(0,-1),  HexColor("#E8F5E1")),
        ('BACKGROUND',    (1,0),(-1,-1), ECO_CLARO),
        ('TOPPADDING',    (0,0),(-1,-1), 8),
        ('BOTTOMPADDING', (0,0),(-1,-1), 8),
        ('LINEAFTER',     (0,0),(2,-1),  0.5, ECO_LINEA),
        ('BOX',           (0,0),(-1,-1), 1, ECO_VERDE),
    ]))
    story.append(t_kpis)
    story.append(Spacer(1, 0.4*cm))

    # Gráfica
    try:
        curva = generar_grafica_curva(df_curva, sfr_opt, sfr_dual, tipo_uso, ancho, largo, lang=_L)
        story.append(RLImage(io.BytesIO(curva), width=15.5*cm, height=8*cm, kind='proportional'))
    except Exception as e:
        story.append(Paragraph(f"[Gráfica no disponible: {e}]", s_small))

    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(T("pdf_table_title", _L), s_h2))

    sem_map = {
        "Subiluminado (<150 lux)":       get_compliance_label("Subiluminado (<150 lux)",      _L),
        "Confort óptimo (ISO+IES)":      get_compliance_label("Confort óptimo (ISO+IES)",     _L),
        "Límite UDI-Autonomous":         get_compliance_label("Límite UDI-Autonomous",        _L),
        "Sobreiluminación UDI-Exceeded": get_compliance_label("Sobreiluminación UDI-Exceeded",_L),
    }
    filas_t = [[T("pdf_col_sfr",_L), T("pdf_col_domos",_L), T("pdf_col_ah_luz",_L), T("pdf_col_pen_cool",_L),
                T("pdf_col_neto",_L), T("pdf_col_pct",_L), T("pdf_col_lux",_L), T("pdf_col_sem",_L)]]
    for r in df_curva:
        es_opt  = "★ " if r.get("sfr_pct") == sfr_opt  else ""
        es_dual = "◆ " if r.get("sfr_pct") == sfr_dual and sfr_dual != sfr_opt else ""
        filas_t.append([
            f"{es_opt}{es_dual}{r['sfr_pct']}%",
            str(r.get("n_domos","—")),
            f"{r.get('ah_luz',0):,.0f}",
            f"{r.get('pen_cool',0):,.0f}",
            f"{r.get('neto_kwh',0):,.0f}",
            f"{r.get('pct_base',0):.1f}%",
            f"{r.get('fc_lux',0):.0f}",
            get_compliance_label(r.get("semaforo",""), _L),
        ])

    t_res = Table(filas_t, colWidths=[1.6*cm, 1.4*cm, 2.5*cm, 2.5*cm, 2.5*cm, 1.6*cm, 1.6*cm, 4.3*cm])
    t_res.setStyle(TableStyle([
        ('FONTNAME',       (0,0),(-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',       (0,0),(-1,-1),  7.5),
        ('TEXTCOLOR',      (0,0),(-1,0),   white),
        ('BACKGROUND',     (0,0),(-1,0),   ECO_AZUL),
        ('ROWBACKGROUNDS', (0,1),(-1,-1),  [white, ECO_CLARO]),
        ('ALIGN',          (0,0),(-1,-1),  'CENTER'),
        ('VALIGN',         (0,0),(-1,-1),  'MIDDLE'),
        ('TOPPADDING',     (0,0),(-1,-1),  4),
        ('BOTTOMPADDING',  (0,0),(-1,-1),  4),
        ('GRID',           (0,0),(-1,-1),  0.3, ECO_LINEA),
    ]))
    story += [t_res, PageBreak()]

    # =========================================================================
    # PÁG 4 — CONFORT VISUAL + RECOMENDACIÓN
    # =========================================================================
    story.append(Paragraph(T("pdf_comfort_title", _L), s_h1))
    if _L == "EN":
        _comfort_intro = (
            f"Hourly map of average interior illuminance for <b>SFR={sfr_show}%</b>. "
            "Red zones indicate periods where daylight exceeds the normative setpoint, "
            "completely eliminating the need for artificial lighting."
        )
    else:
        _comfort_intro = (
            f"Mapa horario de iluminancia interior promedio para <b>SFR={sfr_show}%</b>. "
            "Las zonas rojas indican períodos donde la luz natural supera el setpoint normativo, "
            "eliminando totalmente la necesidad de iluminación artificial."
        )
    story.append(Paragraph(_comfort_intro, s_body))
    story.append(Spacer(1, 0.3*cm))

    epw_path_local = config.get("epw_path","")
    if epw_path_local and os.path.exists(epw_path_local):
        try:
            hm = generar_heatmap_luxes(epw_path_local, sfr_show, vlt, tipo_uso, lang=_L)
            story.append(RLImage(io.BytesIO(hm), width=15.5*cm, height=7*cm, kind='proportional'))
        except Exception as e:
            story.append(Paragraph(f"[Heatmap no disponible: {e}]", s_small))

    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(T("pdf_recom_title", _L), s_h1))
    story.append(Paragraph(recomend_limpio, s_body))
    story.append(Spacer(1, 0.4*cm))

    # Semáforo visual
    sem_txt = resultado.get("semaforo_dual", resultado.get("semaforo_opt","Confort óptimo (ISO+IES)"))
    sem_color_hex = "#2ecc71"
    if "Sobreiluminación" in sem_txt: sem_color_hex = "#e74c3c"
    elif "Límite" in sem_txt:         sem_color_hex = "#f39c12"
    elif "Subiluminado" in sem_txt:   sem_color_hex = "#3498db"

    t_sem = Table([[
        Paragraph(
            f"<b>{T('pdf_normative_status',_L)} SFR={sfr_show}%:</b>  {get_compliance_label(sem_txt, _L)}",
            s('sem', fontSize=10, textColor=HexColor(sem_color_hex), fontName='Helvetica-Bold')
        )
    ]], colWidths=[W - 3.6*cm])
    t_sem.setStyle(TableStyle([
        ('BOX',           (0,0),(-1,-1), 2, HexColor(sem_color_hex)),
        ('BACKGROUND',    (0,0),(-1,-1), HexColor("#f8f9fa")),
        ('TOPPADDING',    (0,0),(-1,-1), 10),
        ('BOTTOMPADDING', (0,0),(-1,-1), 10),
        ('LEFTPADDING',   (0,0),(-1,-1), 12),
    ]))
    story.append(t_sem)
    story.append(Spacer(1, 0.5*cm))

    # CTA Ventas Premium
    t_cta = Table([[
        Paragraph(
            f"<b>{T('pdf_bem_title', _L)}</b><br/><br/>" +
            T("pdf_bem_desc",_L).replace(". ",".<br/>"),
            s('C1', fontSize=9, textColor=white, leading=14)
        ),
        Paragraph(
            f"<b>{T('pdf_exec_title', _L)}</b><br/><br/>" +
            T("pdf_exec_desc",_L).replace(". ",".<br/>"),
            s('C2', fontSize=9, textColor=white, leading=14)
        ),
    ]], colWidths=[(W-3.6*cm)/2]*2)
    t_cta.setStyle(TableStyle([
        ('BACKGROUND',    (0,0),(0,-1), ECO_AZUL),
        ('BACKGROUND',    (1,0),(1,-1), ECO_VERDE),
        ('TOPPADDING',    (0,0),(-1,-1), 14),
        ('BOTTOMPADDING', (0,0),(-1,-1), 14),
        ('LEFTPADDING',   (0,0),(-1,-1), 14),
        ('RIGHTPADDING',  (0,0),(-1,-1), 14),
    ]))
    story.append(t_cta)

    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(
        T("pdf_disclaimer",_L),
        s_disc,
    ))

    # Build con header en todas las páginas
    eco_path_ref = eco_path
    sun_path_ref = sun_path

    def _on_page(canvas_obj, doc_obj):
        pg = doc_obj.page
        sec = secciones[min(pg-1, len(secciones)-1)]
        _draw_header(canvas_obj, doc_obj, eco_path_ref, sun_path_ref, sec)

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    buf.seek(0)
    return buf.read()


def enviar_correo(destinatario, nombre, pdf_bytes, config):
    if not GMAIL_PASSWORD:
        logger.error("GMAIL_APP_PASSWORD no configurada.")
        return False
    try:
        ancho    = config.get("ancho", 0)
        largo    = config.get("largo", 0)
        tipo_uso = config.get("tipo_uso", "Warehouse")

        msg = MIMEMultipart()
        msg['From']    = f"SkyPlus — ECO Consultor <{GMAIL_USER}>"
        msg['To']      = destinatario
        msg['Cc']      = GMAIL_USER
        _L_mail = config.get("lang", "ES")
        _M2FT   = 3.28084
        if _L_mail == "EN":
            _aw = ancho * _M2FT; _al = largo * _M2FT
            msg['Subject'] = f"SkyPlus Report — {_aw:.0f}×{_al:.0f} ft {tipo_uso}"
        else:
            msg['Subject'] = f"Reporte SkyPlus — Nave {ancho:.0f}×{largo:.0f}m {tipo_uso}"

        _L_mail = config.get("lang", "ES")
        if _L_mail == "EN":
            cuerpo = f"""Dear {nombre},

Please find attached your SkyPlus Technical Report with the complete daylighting optimization analysis for your industrial warehouse.

The report includes:
  • Site bioclimatic analysis
  • 3D warehouse model with Sunoptics® skylight distribution
  • SFR optimization curve 0%→6% (7 EnergyPlus simulations)
  • Design recommendation with normative compliance indicator
  • Daylight availability heatmap

Engine: EnergyPlus 23.2 (DOE) · Standards: ISO 8995-1 / IES RP-7

Best regards,
ECO Consultor Engineering Team
ingenieria@ecoconsultor.com"""
        else:
            cuerpo = f"""Estimado/a {nombre},

Adjunto encontrará su Reporte Técnico SkyPlus con el análisis completo de optimización
de iluminación natural para su nave industrial.

El reporte incluye:
  • Análisis bioclimático del sitio
  • Modelo 3D de la nave con distribución de domos Sunoptics®
  • Curva de optimización SFR 0%→6% (7 simulaciones EnergyPlus)
  • Recomendación de diseño con semáforo normativo
  • Mapa de disponibilidad de luz natural

Motor: EnergyPlus 23.2 (DOE) · Normativa: ISO 8995-1 / IES RP-7

Saludos,
Equipo ECO Consultor
ingenieria@ecoconsultor.com"""

        msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))

        adjunto = MIMEBase('application', 'pdf')
        adjunto.set_payload(pdf_bytes)
        encoders.encode_base64(adjunto)
        adjunto.add_header('Content-Disposition',
                           f'attachment; filename="SkyPlus_{ancho:.0f}x{largo:.0f}m.pdf"')
        msg.attach(adjunto)

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, [destinatario, GMAIL_USER], msg.as_string())

        logger.info(f"Correo enviado a {destinatario} + CC {GMAIL_USER}")
        return True
    except Exception as e:
        logger.error(f"Error enviando correo: {e}")
        return False


# =============================================================================
# REGISTRAR EN SHEETS
# =============================================================================
def registrar_sheets(lead, config, resultado=None):
    try:
        import google.auth
        import google.auth.transport.requests
        from googleapiclient.discovery import build
        import googleapiclient.errors

        logger.info(f"[SHEETS] Iniciando registro — SHEETS_ID: '{SHEETS_ID}'")

        if not SHEETS_ID:
            logger.error("[SHEETS] ERROR: SHEETS_ID vacío — variable de entorno no configurada")
            return

        # Autenticación via ADC
        try:
            creds, project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            logger.info(f"[SHEETS] Credenciales OK — proyecto: {project} | tipo: {type(creds).__name__}")
        except Exception as e:
            logger.error(f"[SHEETS] Fallo ADC: {e}")
            return

        # Refrescar token
        try:
            request_obj = google.auth.transport.requests.Request()
            creds.refresh(request_obj)
            logger.info(f"[SHEETS] Token refrescado — expira: {creds.expiry}")
        except Exception as e:
            logger.error(f"[SHEETS] Fallo refresh token: {e}")
            return

        service = build("sheets", "v4", credentials=creds, cache_discovery=False)

        # Detectar nombre real de la primera pestaña
        try:
            meta = service.spreadsheets().get(spreadsheetId=SHEETS_ID).execute()
            sheet_title = meta.get('properties', {}).get('title', '?')
            first_sheet = meta['sheets'][0]['properties']['title']
            logger.info(f"[SHEETS] Acceso OK — documento: '{sheet_title}' | primera pestaña: '{first_sheet}'")
        except googleapiclient.errors.HttpError as e:
            logger.error(f"[SHEETS] Sin acceso: HTTP {e.resp.status} — {e.error_details}")
            return

        # Extraer KPIs del resultado
        sfr_opt  = str(resultado.get("sfr_opt",  "")) if resultado else ""
        sfr_dual = str(resultado.get("sfr_dual", "")) if resultado else ""
        neto_opt = str(round(resultado.get("neto_opt", 0))) if resultado else ""
        pct_opt  = str(round(resultado.get("pct_opt",  0), 1)) if resultado else ""
        kwh_base = str(round(resultado.get("kwh_base", 0))) if resultado else ""

        fila = [[
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            lead.get("correo","").strip().lower(),
            lead.get("nombre",""),
            lead.get("empresa",""),
            lead.get("telefono",""),
            str(config.get("ancho","")),
            str(config.get("largo","")),
            config.get("tipo_uso",""),
            config.get("ciudad",""),
            lead.get("comentario",""),
            sfr_opt,
            sfr_dual,
            neto_opt,
            pct_opt,
            kwh_base,
            "1",
        ]]

        logger.info(f"Escribiendo fila: {fila[0][:4]}")

        response = service.spreadsheets().values().append(
            spreadsheetId=SHEETS_ID,
            range=f"{first_sheet}!A:P",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": fila},
        ).execute()

        updated = response.get("updates", {}).get("updatedRows", 0)
        logger.info(f"✅ Lead registrado en Sheets — filas actualizadas: {updated} | correo: {lead.get('correo')}")

    except google.auth.exceptions.DefaultCredentialsError as e:
        logger.error(f"Error de credenciales ADC: {e}")
    except Exception as e:
        logger.error(f"Error registrando en Sheets: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())


def download_epw_from_gcs(gcs_uri):
    """
    Descarga el EPW desde GCS a /tmp/ local.
    Retorna la ruta local del archivo.
    """
    from google.cloud import storage
    import re
    match = re.match(r"gs://([^/]+)/(.+)", gcs_uri)
    if not match:
        raise ValueError(f"URI GCS inválida: {gcs_uri}")
    bucket_name = match.group(1)
    blob_name   = match.group(2)
    filename    = os.path.basename(blob_name)
    local_path  = f"/tmp/{filename}"
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob   = bucket.blob(blob_name)
    blob.download_to_filename(local_path)
    logger.info(f"EPW descargado: {gcs_uri} → {local_path}")
    return local_path


# =============================================================================
# MAIN — Entry point del Cloud Run Job
# =============================================================================
if __name__ == "__main__":
    logger.info("=== SkyPlus Cloud Run Job iniciado ===")

    # Leer configuración
    payload = leer_config()
    config  = payload.get("config", {})
    lead    = payload.get("lead",   {})
    sql_base = payload.get("sql_base_existente")

    logger.info(f"Proyecto: {config.get('ancho')}x{config.get('largo')}m "
                f"| Cliente: {lead.get('correo')}")

    # Importar motor
    try:
        from motor.termico import calcular_curva_sfr, configurar_proyecto
    except ImportError as e:
        logger.error(f"No se pudo importar el motor: {e}")
        sys.exit(1)

    # Resolver EPW — descargar de GCS si es URI gs://
    epw_path = config.get("epw_path", "")
    if epw_path.startswith("gs://"):
        logger.info(f"Descargando EPW desde GCS: {epw_path}")
        try:
            epw_path = download_epw_from_gcs(epw_path)
            config["epw_path"] = epw_path
        except Exception as e:
            logger.error(f"No se pudo descargar EPW de GCS: {e}")
            sys.exit(1)
    elif not os.path.exists(epw_path):
        logger.error(f"EPW no encontrado: {epw_path}")
        sys.exit(1)

    # Resolver sql_base — descargar de GCS si es URI gs://
    sql_base = payload.get("sql_base_existente")
    if sql_base and str(sql_base).startswith("gs://"):
        logger.info(f"Descargando sql_base desde GCS: {sql_base}")
        try:
            sql_base = download_epw_from_gcs(sql_base)
        except Exception as e:
            logger.warning(f"No se pudo descargar sql_base — se re-simulará SFR=0: {e}")
            sql_base = None

    # 7 simulaciones
    logger.info("Iniciando 7 simulaciones EnergyPlus...")
    try:
        config_curva = configurar_proyecto(
            ancho        = config["ancho"],
            largo        = config["largo"],
            altura       = config["altura"],
            tipo_uso     = config["tipo_uso"],
            epw_path     = config["epw_path"],
            sfr_diseno   = config["sfr_diseno"],
            sfr_curva    = [0, 1, 2, 3, 4, 5, 6],
            domo_vlt     = config["domo_vlt"],
            domo_shgc    = config["domo_shgc"],
            domo_u       = config["domo_u"],
            domo_ancho_m = config["domo_ancho_m"],
            domo_largo_m = config["domo_largo_m"],
        )
        resultado = calcular_curva_sfr(
            config_curva,
            callback=lambda paso, total, msg: logger.info(f"Sim {paso}/{total}: {msg}"),
            sql_base_existente=sql_base,
        )
    except Exception as e:
        logger.error(f"Error en simulación: {e}\n{traceback.format_exc()}")
        sys.exit(1)

    if resultado.get("error"):
        logger.error(f"Motor retornó error: {resultado['error']}")
        sys.exit(1)

    logger.info("Simulaciones completadas. Generando PDF...")

    # PDF
    try:
        pdf_bytes = generar_pdf(config, resultado, lead)
        logger.info(f"PDF generado: {len(pdf_bytes):,} bytes")
    except Exception as e:
        logger.error(f"Error generando PDF: {e}\n{traceback.format_exc()}")
        sys.exit(1)

    # Correo
    ok = enviar_correo(lead.get("correo",""), lead.get("nombre",""), pdf_bytes, config)
    if not ok:
        logger.warning("Correo no enviado — continuando...")

    # Sheets
    registrar_sheets(lead, config, resultado)

    logger.info("=== Cloud Run Job completado exitosamente ===")
    sys.exit(0)
