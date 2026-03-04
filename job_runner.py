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
def generar_isometrico(ancho, largo, alto, num_domos, sfr_real, domo_ancho, domo_largo):
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
    ax.set_xlabel("Ancho (m)", fontsize=8)
    ax.set_ylabel("Largo (m)", fontsize=8)
    ax.set_zlabel("Altura (m)", fontsize=8)
    ax.view_init(elev=25, azim=-60)
    ax.set_title(
        f"Nave {ancho:.0f}×{largo:.0f}×{alto:.0f} m — {num_domos} domos (SFR {sfr_real*100:.1f}%)",
        fontsize=9, color='#003C52', pad=10,
    )
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
def generar_heatmap_luxes(epw_path, sfr_pct, vlt, tipo_uso):
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
    plt.colorbar(im, ax=ax, shrink=0.8).set_label("Lux promedio", fontsize=8)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    buf.seek(0)
    return buf.read()


# =============================================================================
# GRÁFICA CURVA SFR
# =============================================================================
def generar_grafica_curva(df_curva, sfr_opt, sfr_dual, tipo_uso, ancho, largo):
    sfrs  = [r["sfr_pct"]  for r in df_curva]
    netos = [r.get("neto_kwh", 0) for r in df_curva]
    luces = [r.get("ah_luz", 0)   for r in df_curva]
    cools = [r.get("pen_cool", 0) for r in df_curva]
    luxes = [r.get("fc_lux", 0)   for r in df_curva]

    fig, ax1 = plt.subplots(figsize=(10, 5), facecolor='white')
    ax2 = ax1.twinx()

    ax1.plot(sfrs, netos, 'o-',  color='#2ecc71', lw=2.5, label='Ahorro neto (kWh/año)', zorder=5)
    ax1.plot(sfrs, luces, 's--', color='#3498db', lw=1.5, label='Ahorro iluminación')
    ax1.plot(sfrs, cools, '^--', color='#e74c3c', lw=1.5, label='Penalización cooling')
    ax2.plot(sfrs, luxes, 'd:',  color='#9b59b6', lw=1.5, label='Iluminancia (lux)')

    if sfr_opt and sfr_opt in sfrs:
        idx_o = sfrs.index(sfr_opt)
        ax1.axvline(x=sfr_opt, color='#2ecc71', ls='--', alpha=0.6)
        ax1.plot(sfr_opt, netos[idx_o], '*', color='#2ecc71', ms=14, zorder=6)

    if sfr_dual and sfr_dual in sfrs and sfr_dual != sfr_opt:
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
# GENERAR PDF
# =============================================================================
def generar_pdf(config, resultado, lead):
    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title="Reporte Técnico SkyPlus", author="ECO Consultor",
    )
    styles = getSampleStyleSheet()

    def s(nombre, parent='Normal', **kw):
        return ParagraphStyle(nombre, parent=styles[parent], **kw)

    s_titulo    = s('T',  fontSize=22, textColor=ECO_AZUL,  spaceAfter=6,  fontName='Helvetica-Bold')
    s_subtitulo = s('ST', fontSize=11, textColor=ECO_GRIS,  spaceAfter=12)
    s_h1        = s('H1', fontSize=13, textColor=ECO_AZUL,  spaceBefore=14, spaceAfter=6,  fontName='Helvetica-Bold')
    s_h2        = s('H2', fontSize=10, textColor=ECO_VERDE, spaceBefore=8,  spaceAfter=4,  fontName='Helvetica-Bold')
    s_body      = s('B',  fontSize=9,  textColor=ECO_GRIS,  spaceAfter=4,  leading=14)
    s_small     = s('SM', fontSize=7,  textColor=ECO_GRIS,  spaceAfter=2)
    s_footer    = s('F',  fontSize=6.5,textColor=ECO_GRIS,  alignment=TA_CENTER)
    s_disc      = s('D',  fontSize=7.5,textColor=ECO_GRIS,  leading=11, backColor=ECO_CLARO, borderPadding=6)

    story  = []
    fecha  = datetime.datetime.now().strftime("%d de %B de %Y")
    ancho  = config.get("ancho", 0)
    largo  = config.get("largo", 0)
    alto   = config.get("altura", 0)
    tipo_uso = config.get("tipo_uso", "Warehouse")
    ciudad   = config.get("ciudad", "—")
    pais     = config.get("pais", "—")
    modelo   = config.get("modelo_domo", "Sunoptics 800MD")
    vlt      = config.get("domo_vlt", 0.67)
    shgc     = config.get("domo_shgc", 0.48)
    sfr_d    = config.get("sfr_diseno", 0.03)
    n_domos  = resultado.get("n_domos_diseno", 0)
    sfr_real = resultado.get("sfr_real_diseno", sfr_d)
    sfr_opt  = resultado.get("sfr_opt")
    sfr_dual = resultado.get("sfr_dual")
    kwh_base = resultado.get("kwh_base", 0)
    neto_opt = resultado.get("neto_opt", 0)
    pct_opt  = resultado.get("pct_opt", 0)
    df_curva = resultado.get("df_curva_raw", [])

    # ── PORTADA ──────────────────────────────────────────────────────────────
    eco_path = "assets/eco_logo.png"
    sun_path = "assets/sunoptics_logo.png"

    logo_eco = RLImage(eco_path, width=5*cm, height=2*cm, kind='proportional') \
               if os.path.exists(eco_path) else Paragraph("<b>ECO Consultor</b>", s_h1)
    logo_sun = RLImage(sun_path, width=5*cm, height=2*cm, kind='proportional') \
               if os.path.exists(sun_path) else Paragraph("<b>Sunoptics®</b>", s_h1)

    t_logos = Table([[logo_eco, Spacer(1,1), logo_sun]], colWidths=[7*cm,'*',7*cm])
    t_logos.setStyle(TableStyle([
        ('ALIGN',     (0,0),(0,0),'LEFT'),
        ('ALIGN',     (2,0),(2,0),'RIGHT'),
        ('VALIGN',    (0,0),(-1,-1),'MIDDLE'),
        ('LINEBELOW', (0,0),(-1,0),1.5,ECO_VERDE),
        ('BOTTOMPADDING',(0,0),(-1,0),8),
    ]))
    story += [t_logos, Spacer(1,1.5*cm),
              Paragraph("Reporte Técnico SkyPlus", s_titulo),
              Paragraph("Análisis de Optimización de Iluminación Natural con Domos Sunoptics®", s_subtitulo),
              HRFlowable(width="100%", thickness=1, color=ECO_LINEA, spaceAfter=12)]

    t_cliente = Table([
        ["Cliente",  lead.get("nombre",  "—")],
        ["Empresa",  lead.get("empresa", "—")],
        ["Correo",   lead.get("correo",  "—")],
        ["Fecha",    fecha],
    ], colWidths=[4*cm, 13*cm])
    t_cliente.setStyle(TableStyle([
        ('FONTNAME',  (0,0),(0,-1),'Helvetica-Bold'),
        ('FONTSIZE',  (0,0),(-1,-1),9),
        ('TEXTCOLOR', (0,0),(0,-1),ECO_AZUL),
        ('TEXTCOLOR', (1,0),(1,-1),ECO_GRIS),
        ('TOPPADDING',   (0,0),(-1,-1),4),
        ('BOTTOMPADDING',(0,0),(-1,-1),4),
        ('LINEBELOW', (0,-1),(-1,-1),0.5,ECO_LINEA),
    ]))
    story += [t_cliente, Spacer(1,0.8*cm), Paragraph("Especificaciones del Proyecto", s_h1)]

    t_proyecto = Table([
        ["Geometría",  f"{ancho:.0f} × {largo:.0f} × {alto:.0f} m  ({ancho*largo:,.0f} m²)"],
        ["Tipo de uso", tipo_uso],
        ["Ubicación",  f"{ciudad}, {pais}"],
        ["Domo",       modelo],
        ["VLT / SHGC", f"{vlt:.0%} / {shgc:.2f}"],
        ["SFR diseño", f"{sfr_d*100:.0f}%  ({n_domos} domos)"],
        ["Motor",      "EnergyPlus 23.2 (DOE)"],
        ["Normativa",  "ISO 8995-1:2002 · ANSI/IES RP-7-21 · UDI Mardaljevic 2006"],
    ], colWidths=[4*cm, 13*cm])
    t_proyecto.setStyle(TableStyle([
        ('FONTNAME',       (0,0),(0,-1),'Helvetica-Bold'),
        ('FONTSIZE',       (0,0),(-1,-1),9),
        ('TEXTCOLOR',      (0,0),(0,-1),ECO_AZUL),
        ('TEXTCOLOR',      (1,0),(1,-1),ECO_GRIS),
        ('ROWBACKGROUNDS', (0,0),(-1,-1),[ECO_CLARO, white]),
        ('TOPPADDING',     (0,0),(-1,-1),4),
        ('BOTTOMPADDING',  (0,0),(-1,-1),4),
        ('LEFTPADDING',    (0,0),(-1,-1),6),
    ]))
    story.append(t_proyecto)

    comentario = lead.get("comentario", "").strip()
    if comentario:
        story += [Spacer(1,0.5*cm), Paragraph("Comentarios del cliente", s_h2),
                  Paragraph(comentario, s_body)]
    story.append(PageBreak())

    # ── GEOMETRÍA ─────────────────────────────────────────────────────────────
    story += [Paragraph("Modelo Geométrico de la Nave", s_h1),
              Paragraph(f"Vista isométrica con {n_domos} domos Sunoptics® — SFR real: {sfr_real*100:.1f}%.", s_body),
              Spacer(1,0.3*cm)]
    try:
        iso = generar_isometrico(ancho, largo, alto, n_domos, sfr_real,
                                 config.get("domo_ancho_m", 1.328),
                                 config.get("domo_largo_m", 2.547))
        story.append(RLImage(io.BytesIO(iso), width=16*cm, height=9*cm, kind='proportional'))
    except Exception as e:
        story.append(Paragraph(f"[Vista isométrica no disponible: {e}]", s_small))
    story.append(PageBreak())

    # ── RESULTADOS ENERGÉTICOS ────────────────────────────────────────────────
    story += [Paragraph("Análisis Energético — Curva de Optimización SFR", s_h1)]

    t_kpis = Table([
        ["SFR Óptimo Energético", f"{sfr_opt}%",           "Máximo ahorro neto anual"],
        ["Ahorro máximo",         f"{pct_opt:.1f}%",       f"{neto_opt:,.0f} kWh/año"],
        ["SFR Óptimo Dual",       f"{sfr_dual}%" if sfr_dual else "—", "Confort + energía"],
        ["Consumo base",          f"{kwh_base:,.0f} kWh/año", "SFR=0% sin domos"],
    ], colWidths=[5*cm, 4*cm, 8*cm])
    t_kpis.setStyle(TableStyle([
        ('FONTNAME',       (0,0),(0,-1),'Helvetica-Bold'),
        ('FONTNAME',       (1,0),(1,-1),'Helvetica-Bold'),
        ('FONTSIZE',       (0,0),(-1,-1),9),
        ('TEXTCOLOR',      (0,0),(0,-1),ECO_AZUL),
        ('TEXTCOLOR',      (1,0),(1,-1),ECO_VERDE),
        ('TEXTCOLOR',      (2,0),(2,-1),ECO_GRIS),
        ('ROWBACKGROUNDS', (0,0),(-1,-1),[ECO_CLARO, white]),
        ('TOPPADDING',     (0,0),(-1,-1),5),
        ('BOTTOMPADDING',  (0,0),(-1,-1),5),
        ('LEFTPADDING',    (0,0),(-1,-1),6),
    ]))
    story += [t_kpis, Spacer(1,0.4*cm)]

    try:
        curva = generar_grafica_curva(df_curva, sfr_opt, sfr_dual, tipo_uso, ancho, largo)
        story.append(RLImage(io.BytesIO(curva), width=16*cm, height=8*cm, kind='proportional'))
    except Exception as e:
        story.append(Paragraph(f"[Gráfica no disponible: {e}]", s_small))

    story += [Spacer(1,0.3*cm), Paragraph("Tabla de Resultados por SFR", s_h2)]

    sem_map = {
        "Subiluminado (<150 lux)":      "Subiluminado",
        "Confort óptimo (ISO+IES)":      "Confort óptimo",
        "Límite UDI-Autonomous":         "Límite UDI",
        "Sobreiluminación UDI-Exceeded": "Sobreiluminación",
    }
    filas_t = [["SFR","Domos","Ah.Luz kWh","Pen.Cool kWh","Neto kWh","% Base","fc lux","Semáforo"]]
    for r in df_curva:
        filas_t.append([
            f"{r['sfr_pct']}%", str(r.get("n_domos","—")),
            f"{r.get('ah_luz',0):,.0f}", f"{r.get('pen_cool',0):,.0f}",
            f"{r.get('neto_kwh',0):,.0f}", f"{r.get('pct_base',0):.1f}%",
            f"{r.get('fc_lux',0):.0f}", sem_map.get(r.get("semaforo",""), r.get("semaforo","—")),
        ])
    t_res = Table(filas_t, colWidths=[1.5*cm,1.5*cm,2.8*cm,2.8*cm,2.5*cm,1.8*cm,1.8*cm,3.3*cm])
    t_res.setStyle(TableStyle([
        ('FONTNAME',       (0,0),(-1,0),'Helvetica-Bold'),
        ('FONTSIZE',       (0,0),(-1,-1),7.5),
        ('TEXTCOLOR',      (0,0),(-1,0),white),
        ('BACKGROUND',     (0,0),(-1,0),ECO_AZUL),
        ('ROWBACKGROUNDS', (0,1),(-1,-1),[ECO_CLARO, white]),
        ('ALIGN',          (0,0),(-1,-1),'CENTER'),
        ('TOPPADDING',     (0,0),(-1,-1),3),
        ('BOTTOMPADDING',  (0,0),(-1,-1),3),
        ('GRID',           (0,0),(-1,-1),0.3,ECO_LINEA),
    ]))
    story += [t_res, PageBreak()]

    # ── CONFORT VISUAL + RECOMENDACIÓN ────────────────────────────────────────
    story += [Paragraph("Confort Visual — Disponibilidad de Luz Natural", s_h1),
              Paragraph(f"Iluminancia promedio interior (SFR={sfr_dual or sfr_opt}%).", s_body),
              Spacer(1,0.3*cm)]

    epw_path = config.get("epw_path","")
    if epw_path and os.path.exists(epw_path):
        try:
            hm = generar_heatmap_luxes(epw_path, sfr_dual or sfr_opt, vlt, tipo_uso)
            story.append(RLImage(io.BytesIO(hm), width=16*cm, height=7*cm, kind='proportional'))
        except Exception as e:
            story.append(Paragraph(f"[Heatmap no disponible: {e}]", s_small))

    story += [Spacer(1,0.5*cm), Paragraph("Recomendación de Diseño SkyPlus", s_h1),
              Paragraph(resultado.get("recomendacion",""), s_body), Spacer(1,0.5*cm)]

    t_cta = Table([[
        Paragraph("<b>Estudio BEM Premium</b><br/>Simulación Radiance · LEED v4.1 · EDGE.",
                  s('C1', fontSize=9, textColor=ECO_AZUL)),
        Paragraph("<b>Proyecto Ejecutivo</b><br/>Layout · Especificaciones · ROI.",
                  s('C2', fontSize=9, textColor=ECO_VERDE)),
    ]], colWidths=[8.5*cm, 8.5*cm])
    t_cta.setStyle(TableStyle([
        ('BOX', (0,0),(0,0),1,ECO_AZUL), ('BOX', (1,0),(1,0),1,ECO_VERDE),
        ('TOPPADDING',(0,0),(-1,-1),8), ('BOTTOMPADDING',(0,0),(-1,-1),8),
        ('LEFTPADDING',(0,0),(-1,-1),10),
    ]))
    story += [t_cta, Spacer(1,0.8*cm),
              HRFlowable(width="100%", thickness=0.5, color=ECO_LINEA),
              Paragraph(
                  f"SkyPlus v22.2 · ECO Consultor · {fecha} · "
                  "EnergyPlus 23.2 (DOE) · ISO 8995-1 · ANSI/IES RP-7-21",
                  s_footer,
              )]

    doc.build(story)
    buf.seek(0)
    return buf.read()


# =============================================================================
# ENVIAR CORREO
# =============================================================================
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
        msg['Subject'] = f"Reporte SkyPlus — Nave {ancho:.0f}×{largo:.0f}m {tipo_uso}"

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
def registrar_sheets(lead, config):
    try:
        import google.auth
        from googleapiclient.discovery import build

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        sheet   = service.spreadsheets()

        fila = [[
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            lead.get("correo","").strip().lower(),
            lead.get("nombre",""),
            lead.get("empresa",""),
            lead.get("telefono",""),
            str(config.get("ancho","")),
            str(config.get("largo","")),
            lead.get("comentario",""),
            "1",
        ]]
        sheet.values().append(
            spreadsheetId=SHEETS_ID,
            range="Sheet1!A:I",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": fila},
        ).execute()
        logger.info(f"Lead registrado en Sheets: {lead.get('correo')}")
    except Exception as e:
        logger.error(f"Error registrando en Sheets: {e}")


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
    registrar_sheets(lead, config)

    logger.info("=== Cloud Run Job completado exitosamente ===")
    sys.exit(0)
