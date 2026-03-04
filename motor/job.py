# motor/job.py
# =============================================================================
# SKYPLUS — Background Task
# Corre las 7 simulaciones SFR en un thread independiente,
# genera el PDF técnico con ReportLab y lo envía por correo SMTP.
# El navegador puede cerrarse — el thread sigue vivo en Cloud Run.
# =============================================================================

import os
import io
import math
import logging
import smtplib
import threading
import datetime
import tempfile
import traceback
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

import numpy as np
import matplotlib
matplotlib.use("Agg")  # Sin display — modo headless
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch

# ReportLab
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image as RLImage, PageBreak, HRFlowable,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

logger = logging.getLogger(__name__)

# ── Paleta ECO ───────────────────────────────────────────────────────────────
ECO_AZUL  = HexColor("#003C52")
ECO_VERDE = HexColor("#4A7C2F")
ECO_GRIS  = HexColor("#4A5568")
ECO_CLARO = HexColor("#E8F0F3")
ECO_LINEA = HexColor("#CBD5E0")

GMAIL_USER     = os.getenv("GMAIL_USER",         "ingenieria@ecoconsultor.com")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD",  "")


# =============================================================================
# VISTA ISOMÉTRICA MATPLOTLIB (sin VTK — headless safe)
# =============================================================================
def generar_isometrico(ancho, largo, alto, num_domos, sfr_real, domo_ancho, domo_largo):
    """
    Genera vista isométrica 2D de la nave con domos usando matplotlib.
    Retorna bytes PNG.
    """
    fig = plt.figure(figsize=(8, 5), facecolor='white')
    ax  = fig.add_subplot(111, projection='3d', facecolor='white')

    # ── Nave ────────────────────────────────────────────────────────────────
    def draw_box(ax, x0, y0, z0, dx, dy, dz, color_face, color_edge, alpha=0.6):
        verts = [
            [(x0,y0,z0),(x0+dx,y0,z0),(x0+dx,y0+dy,z0),(x0,y0+dy,z0)],       # piso
            [(x0,y0,dz),(x0+dx,y0,dz),(x0+dx,y0+dy,dz),(x0,y0+dy,dz)],        # techo
            [(x0,y0,z0),(x0+dx,y0,z0),(x0+dx,y0,dz),(x0,y0,dz)],              # frente
            [(x0,y0+dy,z0),(x0+dx,y0+dy,z0),(x0+dx,y0+dy,dz),(x0,y0+dy,dz)],  # fondo
            [(x0,y0,z0),(x0,y0+dy,z0),(x0,y0+dy,dz),(x0,y0,dz)],              # izq
            [(x0+dx,y0,z0),(x0+dx,y0+dy,z0),(x0+dx,y0+dy,dz),(x0+dx,y0,dz)],  # der
        ]
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        poly = Poly3DCollection(verts, alpha=alpha)
        poly.set_facecolor(color_face)
        poly.set_edgecolor(color_edge)
        ax.add_collection3d(poly)

    draw_box(ax, 0, 0, 0, ancho, largo, alto,
             color_face='#E8F0F3', color_edge='#003C52', alpha=0.4)

    # ── Domos en techo ──────────────────────────────────────────────────────
    cols  = max(1, round((num_domos * (ancho / largo)) ** 0.5))
    filas = max(1, math.ceil(num_domos / cols))
    dx_d  = ancho / cols
    dy_d  = largo / filas

    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    for i in range(cols):
        for j in range(filas):
            cx = i * dx_d + dx_d / 2
            cy = j * dy_d + dy_d / 2
            x0 = cx - domo_ancho / 2
            y0 = cy - domo_largo / 2
            domo_verts = [[
                (x0,            y0,            alto),
                (x0 + domo_ancho, y0,            alto),
                (x0 + domo_ancho, y0 + domo_largo, alto),
                (x0,            y0 + domo_largo, alto),
            ]]
            p = Poly3DCollection(domo_verts, alpha=0.85)
            p.set_facecolor('#7AAFC4')
            p.set_edgecolor('#003C52')
            ax.add_collection3d(p)

    ax.set_xlim(0, ancho)
    ax.set_ylim(0, largo)
    ax.set_zlim(0, alto * 1.5)
    ax.set_xlabel("Ancho (m)", fontsize=8, color='#4A5568')
    ax.set_ylabel("Largo (m)", fontsize=8, color='#4A5568')
    ax.set_zlabel("Altura (m)", fontsize=8, color='#4A5568')
    ax.view_init(elev=25, azim=-60)
    ax.set_title(
        f"Nave {ancho:.0f}×{largo:.0f}×{alto:.0f} m — {num_domos} domos Sunoptics® (SFR {sfr_real*100:.1f}%)",
        fontsize=9, color='#003C52', pad=10,
    )
    ax.tick_params(labelsize=7)
    ax.grid(True, alpha=0.2)

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    buf.seek(0)
    return buf.read()


# =============================================================================
# HEATMAP DE LUXES
# =============================================================================
def generar_heatmap_luxes(epw_path, sfr_pct, vlt, tipo_uso):
    """Genera heatmap mensual/horario de lux interiores. Retorna bytes PNG."""
    CU = 0.736
    transmis = (sfr_pct / 100.0) * vlt * CU

    with open(epw_path, "r", errors="ignore") as f:
        lineas = f.readlines()[8:]
    illum = []
    for l in lineas:
        p = l.strip().split(",")
        try:
            illum.append(float(p[19]) * 10.0 if len(p) >= 20 else 0.0)
        except (ValueError, IndexError):
            illum.append(0.0)
    illum    = np.array(illum[:8760])
    fc_8760  = illum * transmis

    dias_mes = [31,28,31,30,31,30,31,31,30,31,30,31]
    meses    = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
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
    ax.set_xlabel("Hora del día", fontsize=9)
    ax.set_ylabel("Mes", fontsize=9)
    ax.set_title(f"Disponibilidad de Luz Natural — SFR {sfr_pct}%  |  Setpoint: {lux_sp} lux",
                 fontsize=10, color='#003C52')
    cbar = plt.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Lux promedio", fontsize=8)
    for tick in [lux_sp, 750, 2000]:
        ax.axvline(x=0, alpha=0)  # placeholder
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    buf.seek(0)
    return buf.read()


# =============================================================================
# GRÁFICA CURVA SFR (matplotlib — headless)
# =============================================================================
def generar_grafica_curva(df_curva, sfr_opt, sfr_dual, tipo_uso, ancho, largo):
    """Genera gráfica de la curva de optimización. Retorna bytes PNG."""
    sfrs   = [r["sfr_pct"] for r in df_curva]
    netos  = [r["neto_kwh"] for r in df_curva]
    luces  = [r["ah_luz"] for r in df_curva]
    cools  = [r["pen_cool"] for r in df_curva]
    luxes  = [r["fc_lux"] for r in df_curva]

    fig, ax1 = plt.subplots(figsize=(10, 5), facecolor='white')
    ax2 = ax1.twinx()

    ax1.plot(sfrs, netos,  'o-', color='#2ecc71', lw=2.5, label='Ahorro neto (kWh/año)', zorder=5)
    ax1.plot(sfrs, luces,  's--', color='#3498db', lw=1.5, label='Ahorro iluminación')
    ax1.plot(sfrs, cools,  '^--', color='#e74c3c', lw=1.5, label='Penalización cooling')
    ax2.plot(sfrs, luxes,  'd:', color='#9b59b6', lw=1.5, label='Iluminancia (lux)')

    if sfr_opt:
        idx_o = sfrs.index(sfr_opt)
        ax1.axvline(x=sfr_opt, color='#2ecc71', ls='--', alpha=0.6)
        ax1.plot(sfr_opt, netos[idx_o], '*', color='#2ecc71', ms=14, zorder=6)

    if sfr_dual and sfr_dual != sfr_opt:
        idx_d = sfrs.index(sfr_dual)
        ax1.axvline(x=sfr_dual, color='#f39c12', ls='--', alpha=0.6)
        ax1.plot(sfr_dual, netos[idx_d], 'D', color='#f39c12', ms=10, zorder=6,
                 label=f'Óptimo Dual SFR={sfr_dual}%')

    ax1.set_xlabel("SFR (%)", fontsize=10)
    ax1.set_ylabel("Energía (kWh/año)", fontsize=10)
    ax2.set_ylabel("Iluminancia promedio (lux)", fontsize=10)
    ax1.set_title(f"Curva de Optimización SkyPlus — {tipo_uso} {ancho:.0f}×{largo:.0f}m",
                  fontsize=11, color='#003C52')
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
# GENERACIÓN DEL PDF
# =============================================================================
def generar_pdf(config, resultado, lead):
    """
    Genera el PDF técnico completo.
    config: dict con parámetros del proyecto
    resultado: dict con resultados de calcular_curva_sfr()
    lead: dict con nombre, empresa, correo, comentario
    Retorna bytes del PDF.
    """
    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title="Reporte Técnico SkyPlus",
        author="ECO Consultor",
    )

    W, H = A4
    styles = getSampleStyleSheet()

    # ── Estilos custom ───────────────────────────────────────────────────────
    def estilo(nombre, parent='Normal', **kwargs):
        return ParagraphStyle(nombre, parent=styles[parent], **kwargs)

    s_titulo    = estilo('Titulo',    fontSize=22, textColor=ECO_AZUL,  spaceAfter=6,  alignment=TA_LEFT, fontName='Helvetica-Bold')
    s_subtitulo = estilo('Subtitulo', fontSize=11, textColor=ECO_GRIS,  spaceAfter=12, alignment=TA_LEFT)
    s_h1        = estilo('H1',        fontSize=13, textColor=ECO_AZUL,  spaceBefore=14, spaceAfter=6, fontName='Helvetica-Bold')
    s_h2        = estilo('H2',        fontSize=10, textColor=ECO_VERDE, spaceBefore=8,  spaceAfter=4, fontName='Helvetica-Bold')
    s_body      = estilo('Body',      fontSize=9,  textColor=ECO_GRIS,  spaceAfter=4,  leading=14)
    s_small     = estilo('Small',     fontSize=7,  textColor=ECO_GRIS,  spaceAfter=2)
    s_disclaimer= estilo('Disc',      fontSize=7.5,textColor=ECO_GRIS,  spaceAfter=4,  leading=11,
                          backColor=ECO_CLARO, borderPadding=6)
    s_center    = estilo('Center',    fontSize=9,  alignment=TA_CENTER, textColor=ECO_GRIS)

    story = []

    # =========================================================================
    # PÁGINA 1 — PORTADA
    # =========================================================================

    # Header con logos
    eco_logo_path  = "assets/eco_logo.png"
    sun_logo_path  = "assets/sunoptics_logo.png"

    logos = []
    if os.path.exists(eco_logo_path):
        logos.append(RLImage(eco_logo_path, width=5*cm, height=2*cm, kind='proportional'))
    else:
        logos.append(Paragraph("<b>ECO Consultor</b>", estilo('LogoText', fontSize=14, textColor=ECO_AZUL)))

    logos.append(Spacer(1, 1))  # separador central

    if os.path.exists(sun_logo_path):
        logos.append(RLImage(sun_logo_path, width=5*cm, height=2*cm, kind='proportional'))
    else:
        logos.append(Paragraph("<b>Sunoptics®</b>", estilo('LogoText2', fontSize=14, textColor=ECO_VERDE, alignment=TA_RIGHT)))

    tabla_logos = Table([logos], colWidths=[7*cm, '*', 7*cm])
    tabla_logos.setStyle(TableStyle([
        ('ALIGN',     (0,0), (0,0), 'LEFT'),
        ('ALIGN',     (2,0), (2,0), 'RIGHT'),
        ('VALIGN',    (0,0), (-1,-1), 'MIDDLE'),
        ('LINEBELOW', (0,0), (-1,0), 1.5, ECO_VERDE),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
    ]))
    story.append(tabla_logos)
    story.append(Spacer(1, 1.5*cm))

    # Título
    story.append(Paragraph("Reporte Técnico SkyPlus", s_titulo))
    story.append(Paragraph("Análisis de Optimización de Iluminación Natural con Domos Sunoptics®", s_subtitulo))
    story.append(HRFlowable(width="100%", thickness=1, color=ECO_LINEA, spaceAfter=12))

    # Datos cliente
    fecha = datetime.datetime.now().strftime("%d de %B de %Y")
    datos_cliente = [
        ["Cliente",    lead.get("nombre",   "—")],
        ["Empresa",    lead.get("empresa",  "—")],
        ["Correo",     lead.get("correo",   "—")],
        ["Fecha",      fecha],
    ]
    t_cliente = Table(datos_cliente, colWidths=[4*cm, 13*cm])
    t_cliente.setStyle(TableStyle([
        ('FONTNAME',  (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTSIZE',  (0,0), (-1,-1), 9),
        ('TEXTCOLOR', (0,0), (0,-1), ECO_AZUL),
        ('TEXTCOLOR', (1,0), (1,-1), ECO_GRIS),
        ('TOPPADDING',   (0,0), (-1,-1), 4),
        ('BOTTOMPADDING',(0,0), (-1,-1), 4),
        ('LINEBELOW', (0,-1), (-1,-1), 0.5, ECO_LINEA),
    ]))
    story.append(t_cliente)
    story.append(Spacer(1, 0.8*cm))

    # Datos del proyecto
    story.append(Paragraph("Especificaciones del Proyecto", s_h1))
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
    n_domos  = resultado.get("n_domos_diseno", 0)
    sfr_real = resultado.get("sfr_real_diseno", sfr_d)

    datos_proyecto = [
        ["Geometría",    f"{ancho:.0f} × {largo:.0f} × {alto:.0f} m  ({ancho*largo:,.0f} m²)"],
        ["Tipo de uso",  tipo_uso],
        ["Ubicación",    f"{ciudad}, {pais}"],
        ["Domo",         modelo],
        ["VLT / SHGC",   f"{vlt:.0%} / {shgc:.2f}"],
        ["SFR diseño",   f"{sfr_d*100:.0f}%  ({n_domos} domos)"],
        ["Motor",        "EnergyPlus 23.2 (DOE)"],
        ["Normativa",    "ISO 8995-1:2002 · ANSI/IES RP-7-21 · UDI Mardaljevic 2006"],
    ]
    t_proyecto = Table(datos_proyecto, colWidths=[4*cm, 13*cm])
    t_proyecto.setStyle(TableStyle([
        ('FONTNAME',       (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTSIZE',       (0,0), (-1,-1), 9),
        ('TEXTCOLOR',      (0,0), (0,-1), ECO_AZUL),
        ('TEXTCOLOR',      (1,0), (1,-1), ECO_GRIS),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [ECO_CLARO, white]),
        ('TOPPADDING',     (0,0), (-1,-1), 4),
        ('BOTTOMPADDING',  (0,0), (-1,-1), 4),
        ('LEFTPADDING',    (0,0), (-1,-1), 6),
    ]))
    story.append(t_proyecto)

    # Comentarios del cliente
    comentario = lead.get("comentario", "").strip()
    if comentario:
        story.append(Spacer(1, 0.5*cm))
        story.append(Paragraph("Comentarios del cliente", s_h2))
        story.append(Paragraph(comentario, s_body))

    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 2 — GEOMETRÍA 3D
    # =========================================================================
    story.append(Paragraph("Modelo Geométrico de la Nave", s_h1))
    story.append(Paragraph(
        f"Vista isométrica de la nave industrial con distribución matricial de {n_domos} "
        f"domos Sunoptics® en cuadrícula simétrica. SFR real: {sfr_real*100:.1f}%.",
        s_body,
    ))
    story.append(Spacer(1, 0.3*cm))

    try:
        iso_bytes = generar_isometrico(
            ancho, largo, alto, n_domos, sfr_real,
            config.get("domo_ancho_m", 1.328),
            config.get("domo_largo_m", 2.547),
        )
        iso_img = RLImage(io.BytesIO(iso_bytes), width=16*cm, height=9*cm, kind='proportional')
        story.append(iso_img)
    except Exception as e:
        story.append(Paragraph(f"[Error generando vista isométrica: {e}]", s_small))

    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 3 — RESULTADOS ENERGÉTICOS
    # =========================================================================
    story.append(Paragraph("Análisis Energético — Curva de Optimización SFR", s_h1))

    sfr_opt  = resultado.get("sfr_opt")
    sfr_dual = resultado.get("sfr_dual")
    kwh_base = resultado.get("kwh_base", 0)
    neto_opt = resultado.get("neto_opt", 0)
    pct_opt  = resultado.get("pct_opt", 0)
    df_curva = resultado.get("df_curva_raw", [])

    # KPIs principales
    kpis = [
        ["SFR Óptimo Energético", f"{sfr_opt}%", "Máximo ahorro neto anual"],
        ["Ahorro máximo",         f"{pct_opt:.1f}%", f"{neto_opt:,.0f} kWh/año"],
        ["SFR Óptimo Dual",       f"{sfr_dual}%" if sfr_dual else "—", "Confort + energía verificado"],
        ["Consumo base",          f"{kwh_base:,.0f} kWh/año", "SFR=0% sin domos"],
    ]
    t_kpis = Table(kpis, colWidths=[5*cm, 4*cm, 8*cm])
    t_kpis.setStyle(TableStyle([
        ('FONTNAME',       (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME',       (1,0), (1,-1), 'Helvetica-Bold'),
        ('FONTSIZE',       (0,0), (-1,-1), 9),
        ('TEXTCOLOR',      (0,0), (0,-1), ECO_AZUL),
        ('TEXTCOLOR',      (1,0), (1,-1), ECO_VERDE),
        ('TEXTCOLOR',      (2,0), (2,-1), ECO_GRIS),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [ECO_CLARO, white]),
        ('TOPPADDING',     (0,0), (-1,-1), 5),
        ('BOTTOMPADDING',  (0,0), (-1,-1), 5),
        ('LEFTPADDING',    (0,0), (-1,-1), 6),
    ]))
    story.append(t_kpis)
    story.append(Spacer(1, 0.4*cm))

    # Gráfica curva
    try:
        curva_bytes = generar_grafica_curva(df_curva, sfr_opt, sfr_dual, tipo_uso, ancho, largo)
        story.append(RLImage(io.BytesIO(curva_bytes), width=16*cm, height=8*cm, kind='proportional'))
    except Exception as e:
        story.append(Paragraph(f"[Error generando gráfica: {e}]", s_small))

    story.append(Spacer(1, 0.3*cm))

    # Tabla detallada
    story.append(Paragraph("Tabla de Resultados por SFR", s_h2))
    sem_map = {
        "Subiluminado (<150 lux)":       "Subiluminado",
        "Confort óptimo (ISO+IES)":       "Confort óptimo",
        "Límite UDI-Autonomous":          "Límite UDI",
        "Sobreiluminación UDI-Exceeded":  "Sobreiluminación",
    }
    headers = ["SFR", "Domos", "Ah. Luz kWh", "Pen. Cool kWh", "Neto kWh", "% Base", "fc lux", "Semáforo"]
    filas_tabla = [headers]
    for r in df_curva:
        filas_tabla.append([
            f"{r['sfr_pct']}%",
            str(r.get("n_domos", "—")),
            f"{r.get('ah_luz', 0):,.0f}",
            f"{r.get('pen_cool', 0):,.0f}",
            f"{r.get('neto_kwh', 0):,.0f}",
            f"{r.get('pct_base', 0):.1f}%",
            f"{r.get('fc_lux', 0):.0f}",
            sem_map.get(r.get("semaforo", ""), r.get("semaforo", "—")),
        ])
    t_resultados = Table(filas_tabla, colWidths=[1.5*cm, 1.5*cm, 3*cm, 3*cm, 2.5*cm, 2*cm, 2*cm, 3.5*cm])
    t_resultados.setStyle(TableStyle([
        ('FONTNAME',       (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',       (0,0), (-1,-1), 7.5),
        ('TEXTCOLOR',      (0,0), (-1,0), white),
        ('BACKGROUND',     (0,0), (-1,0), ECO_AZUL),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [ECO_CLARO, white]),
        ('ALIGN',          (0,0), (-1,-1), 'CENTER'),
        ('TOPPADDING',     (0,0), (-1,-1), 3),
        ('BOTTOMPADDING',  (0,0), (-1,-1), 3),
        ('GRID',           (0,0), (-1,-1), 0.3, ECO_LINEA),
    ]))
    story.append(t_resultados)
    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 4 — CONFORT VISUAL + RECOMENDACIÓN
    # =========================================================================
    story.append(Paragraph("Confort Visual — Disponibilidad de Luz Natural", s_h1))
    story.append(Paragraph(
        f"Iluminancia promedio interior por mes y hora del día para SFR={sfr_dual or sfr_opt}%. "
        "Zonas amarillas indican ahorro total de iluminación artificial.",
        s_body,
    ))
    story.append(Spacer(1, 0.3*cm))

    epw_path = config.get("epw_path", "")
    if epw_path and os.path.exists(epw_path):
        try:
            hm_bytes = generar_heatmap_luxes(
                epw_path,
                sfr_dual or sfr_opt,
                vlt,
                tipo_uso,
            )
            story.append(RLImage(io.BytesIO(hm_bytes), width=16*cm, height=7*cm, kind='proportional'))
        except Exception as e:
            story.append(Paragraph(f"[Error generando heatmap: {e}]", s_small))

    story.append(Spacer(1, 0.5*cm))

    # Recomendación
    story.append(Paragraph("Recomendación de Diseño SkyPlus", s_h1))
    recomendacion = resultado.get("recomendacion", "")
    story.append(Paragraph(recomendacion, s_body))

    story.append(Spacer(1, 0.5*cm))

    # Semáforo normativo
    sem_txt = resultado.get("semaforo_dual", resultado.get("semaforo_opt", ""))
    sem_color_map = {
        "Confort óptimo": "#2ecc71",
        "Límite UDI":     "#f39c12",
        "Sobreiluminación": "#e74c3c",
        "Subiluminado":   "#3498db",
    }
    sem_color = "#2ecc71"
    for k, v in sem_color_map.items():
        if k.lower() in sem_txt.lower():
            sem_color = v
            break

    t_sem = Table([[
        Paragraph(f"<b>Estado normativo:</b> {sem_txt}", estilo('Sem', fontSize=10, textColor=HexColor(sem_color)))
    ]], colWidths=[17*cm])
    t_sem.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), HexColor("#f8f9fa")),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
        ('TOPPADDING',    (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('BOX',           (0,0), (-1,-1), 1.5, HexColor(sem_color)),
    ]))
    story.append(t_sem)
    story.append(Spacer(1, 0.5*cm))

    # Disclaimer
    story.append(Paragraph(
        "<b>Nota técnica:</b> Resultados generados por EnergyPlus 23.2 (DOE oficial). "
        "La métrica de confort visual (fc promedio) es una estimación analítica EPW bajo "
        "normativa ISO 8995-1 e IES RP-7. Para validación espacial punto por punto, "
        "certificaciones LEED o estudios de deslumbramiento, solicite un estudio BEM detallado.",
        s_disclaimer,
    ))

    story.append(Spacer(1, 0.5*cm))

    # CTA
    cta_data = [[
        Paragraph(
            "<b>Estudio BEM Premium</b><br/>"
            "Simulación espacial con Radiance. Validación punto por punto, LEED v4.1, EDGE.",
            estilo('CTA1', fontSize=9, textColor=ECO_AZUL)
        ),
        Paragraph(
            "<b>Proyecto Ejecutivo</b><br/>"
            "Layout de domos, especificaciones técnicas, presupuesto y análisis de ROI.",
            estilo('CTA2', fontSize=9, textColor=ECO_VERDE)
        ),
    ]]
    t_cta = Table(cta_data, colWidths=[8.5*cm, 8.5*cm])
    t_cta.setStyle(TableStyle([
        ('BOX',           (0,0), (0,0), 1, ECO_AZUL),
        ('BOX',           (1,0), (1,0), 1, ECO_VERDE),
        ('TOPPADDING',    (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
        ('COLPADDING',    (0,0), (-1,-1), 5),
    ]))
    story.append(t_cta)

    # Footer
    story.append(Spacer(1, 0.8*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=ECO_LINEA))
    story.append(Paragraph(
        f"SkyPlus v22.2 · ECO Consultor · {fecha} · Motor: EnergyPlus 23.2 (DOE) · "
        "Normativa: ISO 8995-1 · ANSI/IES RP-7-21 · UDI Mardaljevic 2006",
        estilo('Footer', fontSize=6.5, textColor=ECO_GRIS, alignment=TA_CENTER),
    ))

    doc.build(story)
    buf.seek(0)
    return buf.read()


# =============================================================================
# ENVÍO DE CORREO
# =============================================================================
def enviar_correo(destinatario, nombre, pdf_bytes, config):
    """Envía el PDF por correo usando Gmail SMTP con App Password."""
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
        msg['Cc']      = GMAIL_USER   # Copia interna a ingeniería
        msg['Subject'] = f"Reporte SkyPlus — Nave {ancho:.0f}×{largo:.0f}m {tipo_uso}"

        cuerpo = f"""
Estimado/a {nombre},

Adjunto encontrará su Reporte Técnico SkyPlus con el análisis completo de optimización
de iluminación natural para su nave industrial.

El reporte incluye:
  • Análisis bioclimático del sitio
  • Modelo 3D de la nave con distribución de domos Sunoptics®
  • Curva de optimización SFR 0%→6% (7 simulaciones EnergyPlus)
  • Recomendación de diseño con semáforo normativo
  • Mapa de disponibilidad de luz natural

Motor: EnergyPlus 23.2 (DOE) · Normativa: ISO 8995-1 / IES RP-7

Para una validación espacial punto por punto o certificaciones LEED/EDGE,
contáctenos para un estudio BEM detallado.

Saludos,
Equipo ECO Consultor
ingenieria@ecoconsultor.com
        """.strip()

        msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))

        # Adjunto PDF
        adjunto = MIMEBase('application', 'pdf')
        adjunto.set_payload(pdf_bytes)
        encoders.encode_base64(adjunto)
        adjunto.add_header(
            'Content-Disposition',
            f'attachment; filename="SkyPlus_Reporte_{ancho:.0f}x{largo:.0f}m.pdf"'
        )
        msg.attach(adjunto)

        # Envío SMTP
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            destinatarios = [destinatario, GMAIL_USER]
            server.sendmail(GMAIL_USER, destinatarios, msg.as_string())

        logger.info(f"Correo enviado a {destinatario} + CC {GMAIL_USER}")
        return True

    except Exception as e:
        logger.error(f"Error enviando correo a {destinatario}: {e}")
        return False


# =============================================================================
# BACKGROUND TASK — FUNCIÓN PRINCIPAL
# =============================================================================
def _ejecutar_en_background(config, lead, sql_base_existente=None):
    """
    Función interna que corre en el thread background.
    1. Corre las 7 simulaciones EnergyPlus
    2. Genera el PDF
    3. Envía el correo
    4. Registra en Google Sheets
    """
    from motor.termico import calcular_curva_sfr, configurar_proyecto
    from motor.sheets  import registrar_lead

    correo  = lead.get("correo", "")
    nombre  = lead.get("nombre", "")
    empresa = lead.get("empresa", "")

    logger.info(f"[BG] Iniciando curva SFR para {correo}")

    try:
        # ── 7 simulaciones ────────────────────────────────────────────────
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
            callback=lambda paso, total, msg: logger.info(f"[BG] {msg}"),
            sql_base_existente=sql_base_existente,
        )

        if resultado.get("error"):
            logger.error(f"[BG] Error en simulación: {resultado['error']}")
            return

        logger.info(f"[BG] Simulación completa. Generando PDF...")

        # Enriquecer config con metadata climática
        config["ciudad"] = config.get("ciudad", "—")
        config["pais"]   = config.get("pais",   "—")

        # ── PDF ───────────────────────────────────────────────────────────
        pdf_bytes = generar_pdf(config, resultado, lead)
        logger.info(f"[BG] PDF generado ({len(pdf_bytes):,} bytes)")

        # ── Correo ────────────────────────────────────────────────────────
        ok_correo = enviar_correo(correo, nombre, pdf_bytes, config)
        logger.info(f"[BG] Correo {'enviado' if ok_correo else 'FALLIDO'}")

        # ── Google Sheets ─────────────────────────────────────────────────
        registrar_lead(
            nombre     = nombre,
            empresa    = empresa,
            correo     = correo,
            telefono   = lead.get("telefono", ""),
            ancho      = config["ancho"],
            largo      = config["largo"],
            comentario = lead.get("comentario", ""),
        )
        logger.info(f"[BG] Lead registrado en Sheets. Proceso completo.")

    except Exception as e:
        logger.error(f"[BG] Error crítico: {e}\n{traceback.format_exc()}")


def lanzar_simulacion_background(config, lead, sql_base_existente=None):
    """
    Lanza las 7 simulaciones + PDF + correo en un thread daemon.
    Retorna inmediatamente — el navegador no necesita esperar.
    """
    hilo = threading.Thread(
        target=_ejecutar_en_background,
        args=(config, lead, sql_base_existente),
        daemon=True,   # muere si el proceso principal muere
        name=f"skyplus-bg-{lead.get('correo','unknown')}",
    )
    hilo.start()
    logger.info(f"[BG] Thread lanzado: {hilo.name}")
    return hilo.name
