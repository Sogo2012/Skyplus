# motor/termico.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from i18n import T, fmt_energy, fmt_illuminance, fmt_dims, get_compliance_label, CONVERSION
    _I18N_OK = True
except ImportError:
    _I18N_OK = False
    def T(k, lang="ES"): return k
    def fmt_energy(v, u): return f"{v:,.0f} kWh/año"
    def fmt_illuminance(v, u, d=0): return f"{v:.{d}f} lux"
    def fmt_dims(a,l,h,u): return f"{a:.0f}×{l:.0f}×{h:.0f} m"
    def get_compliance_label(k, lang): return k
    CONVERSION = {"m_to_ft": 3.28084, "kwh_to_kbtu": 3.41214, "lux_to_fc": 0.09290}
# =============================================================================
# SKYPLUS — Motor Térmico v22.2
# Eco Consultor | Sunoptics LATAM
#
# CAMBIOS v22.1 vs v22 (correcciones Docker):
#   BUG 1 FIX: _parchear_hvactemplate() ahora elimina el
#              ZoneHVAC:EquipmentConnections que Honeybee pre-genera,
#              evitando el conflicto de nombres con EQLIST_NAVE_BASE.
#   BUG 2 FIX: construir_modelo() retorna los IDs reales de schedules
#              de calefacción/enfriamiento; traducir_y_simular() los
#              recibe y los pasa al parche correctamente.
#   BUG 3 FIX: Grid ASHRAE de sensores usa ancho/largo reales de la nave,
#              no valores hardcodeados de 50m×100m.
#
# NUEVA API (flujo SaaS 2 etapas):
#   Etapa 1 — simular_caso_diseno(config)
#             2 simulaciones: Base + Diseño → resultado rápido ~2-4 min
#             Impresiona al cliente con su nave específica.
#   Etapa 2 — calcular_curva_sfr(config)
#             7 simulaciones: SFR 0%→6% → curva óptima + tabla paramétrica
#             Se activa cuando el cliente pide el PDF.
#
# Arquitectura híbrida:
#   Motor 1: EnergyPlus 23.2 → kWh iluminación, cooling, heating
#   Motor 2: EPW col20 × 10  → fc_prom (lux) + semáforo normativo
#
# Normativa: ISO 8995-1:2002 | ANSI/IES RP-7-21 | UDI Mardaljevic 2006
# =============================================================================

import os
import re
import json
import math
import shutil
import sqlite3
import tempfile
import warnings
import subprocess

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# CONSTANTES PÚBLICAS
# ---------------------------------------------------------------------------
DOMO_ANCHO_M = 1.3279
DOMO_LARGO_M = 2.5465
DOMO_VLT_DEFAULT  = 0.67
DOMO_SHGC_DEFAULT = 0.48
DOMO_U_DEFAULT    = 3.20
COP_COOLING_DEFAULT = 3.5
EFF_HEATING_DEFAULT = 0.85

CURB_ALTURA_M   = 14 * 0.0254
DOMO_ANCHO_ID_M = 52.25 * 0.0254
DOMO_LARGO_ID_M = 100.25 * 0.0254
DIRT_FACTORS = {
    "Warehouse": 0.85, "Manufacturing": 0.80,
    "Retail": 0.90, "SuperMarket": 0.90, "MediumOffice": 0.90,
}

UMBRAL_SUBILUM = 150.0
UMBRAL_OPTIMO  = 750.0
UMBRAL_LIMITE  = 2000.0

# UDI-e espacial v22.2 (IES LM-83 / CIBSE LG10)
UDI_LUX_UMBRAL   = 2500   # lux — umbral sobreiluminación
UDI_PCT_HORA_MAX = 5.0    # % horas que un sensor puede exceder el umbral
UDI_AREA_MAX     = 10.0   # % del área total — límite normativo
ILLUM_MAP_NX     = 10     # puntos X
ILLUM_MAP_NY     = 20     # puntos Y
ILLUM_MAP_N      = ILLUM_MAP_NX * ILLUM_MAP_NY  # 200 sensores

COLORES_SEM = {
    "AZUL": "#3498db", "VERDE": "#2ecc71",
    "AMARILLO": "#f39c12", "ROJO": "#e74c3c",
}

PERFILES_ASHRAE = {
    "Warehouse":    {"lpd": 6.5, "eq": 4.0,  "m2p":100.0, "inf":0.0003,"vent":0.3,"lux":300,"calef":15.6,"enfriam":26.7},
    "Manufacturing":{"lpd":12.0, "eq":20.0,  "m2p": 30.0, "inf":0.0003,"vent":0.6,"lux":500,"calef":15.6,"enfriam":26.7},
    "Retail":       {"lpd":14.0, "eq": 3.8,  "m2p":  5.0, "inf":0.0002,"vent":0.6,"lux":500,"calef":21.1,"enfriam":23.9},
    "SuperMarket":  {"lpd":23.0, "eq":10.8,  "m2p":  5.0, "inf":0.0002,"vent":0.6,"lux":500,"calef":21.1,"enfriam":23.9},
    "MediumOffice": {"lpd":10.8, "eq":10.8,  "m2p": 10.0, "inf":0.0002,"vent":0.6,"lux":500,"calef":21.1,"enfriam":23.9},
}

U_TECHO_ASHRAE = {t: {2:0.317,3:0.220,4:0.161,5:0.144} for t in PERFILES_ASHRAE}
U_TECHO_ASHRAE["MediumOffice"] = {2:0.220,3:0.220,4:0.161,5:0.119}

HORARIOS_ASHRAE = {
    "Warehouse": {
        "luces":    {"weekday":[0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,0,0],"saturday":[0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,0,0,0,0,0,0,0,0],"sunday":[0]*24},
        "equipos":  {"weekday":[0.1,0.1,0.1,0.1,0.1,0.1,0.5,1,1,1,1,1,1,1,1,1,1,1,1,0.5,0.1,0.1,0.1,0.1],"saturday":[0.1,0.1,0.1,0.1,0.1,0.1,0.5,1,1,1,1,1,1,0.5,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1],"sunday":[0.1]*24},
        "ocupacion":{"weekday":[0,0,0,0,0,0,1,1,1,1,1,1,0.5,1,1,1,1,1,1,1,0,0,0,0],"saturday":[0,0,0,0,0,0,1,1,1,1,1,1,0.5,0.5,0,0,0,0,0,0,0,0,0,0],"sunday":[0]*24},
        "hvac":     {"weekday":[0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,0,0,0],"saturday":[0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,0,0,0,0,0,0,0,0,0],"sunday":[0]*24},
        "setback_cool":32.0,"setback_heat":10.0,
    },
    "Manufacturing": {
        "luces":    {"weekday":[0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,0,0],"saturday":[0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,0,0,0,0,0,0,0,0],"sunday":[0]*24},
        "equipos":  {"weekday":[0.1,0.1,0.1,0.1,0.1,0.3,1,1,1,1,1,1,1,1,1,1,1,1,1,1,0.3,0.1,0.1,0.1],"saturday":[0.1]*6+[1]*9+[0.1]*9,"sunday":[0.1]*24},
        "ocupacion":{"weekday":[0,0,0,0,0,0,1,1,1,1,1,1,0.5,1,1,1,1,1,1,1,0,0,0,0],"saturday":[0]*6+[1]*8+[0]*10,"sunday":[0]*24},
        "hvac":     {"weekday":[0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,0,0,0],"saturday":[0]*5+[1]*10+[0]*9,"sunday":[0]*24},
        "setback_cool":32.0,"setback_heat":12.0,
    },
    "Retail": {
        "luces":    {"weekday":[0]*8+[1]*13+[0]*3,"saturday":[0]*8+[1]*13+[0]*3,"sunday":[0]*9+[1]*10+[0]*5},
        "equipos":  {"weekday":[0.1]*8+[1]*13+[0.1]*3,"saturday":[0.1]*8+[1]*13+[0.1]*3,"sunday":[0.1]*9+[1]*10+[0.1]*5},
        "ocupacion":{"weekday":[0]*8+[1]*13+[0]*3,"saturday":[0]*8+[1]*13+[0]*3,"sunday":[0]*9+[1]*10+[0]*5},
        "hvac":     {"weekday":[0]*7+[1]*14+[0]*3,"saturday":[0]*7+[1]*14+[0]*3,"sunday":[0]*8+[1]*12+[0]*4},
        "setback_cool":29.0,"setback_heat":15.0,
    },
    "SuperMarket": {
        "luces":    {"weekday":[0]*6+[1]*17+[0]*1,"saturday":[0]*6+[1]*17+[0]*1,"sunday":[0]*7+[1]*15+[0]*2},
        "equipos":  {"weekday":[0.5]*6+[1]*17+[0.5]*1,"saturday":[0.5]*6+[1]*17+[0.5]*1,"sunday":[0.5]*7+[1]*15+[0.5]*2},
        "ocupacion":{"weekday":[0]*6+[1]*17+[0]*1,"saturday":[0]*6+[1]*17+[0]*1,"sunday":[0]*7+[1]*15+[0]*2},
        "hvac":     {"weekday":[1]*24,"saturday":[1]*24,"sunday":[1]*24},
        "setback_cool":24.0,"setback_heat":18.0,
    },
    "MediumOffice": {
        "luces":    {"weekday":[0]*7+[1]*13+[0]*4,"saturday":[0]*8+[1]*6+[0]*10,"sunday":[0]*24},
        "equipos":  {"weekday":[0.1]*7+[1]*13+[0.1]*4,"saturday":[0.1]*8+[1]*6+[0.1]*10,"sunday":[0.1]*24},
        "ocupacion":{"weekday":[0]*7+[1]*13+[0]*4,"saturday":[0]*8+[0.5]*6+[0]*10,"sunday":[0]*24},
        "hvac":     {"weekday":[0]*6+[1]*15+[0]*3,"saturday":[0]*7+[1]*7+[0]*10,"sunday":[0]*24},
        "setback_cool":29.0,"setback_heat":15.6,
    },
}


# =============================================================================
# SECCIÓN 1 — UTILIDADES INTERNAS
# =============================================================================

def _detectar_zona_climatica(epw_path):
    stat_path = epw_path.replace(".epw", ".stat")
    if os.path.exists(stat_path):
        try:
            with open(stat_path, "r", errors="ignore") as f:
                contenido = f.read()
            match = re.search(r'Climate type\s+"(\d+[A-C]?)".*ASHRAE', contenido)
            if match:
                zona_raw = match.group(1)
                return max(1, min(8, int(re.match(r"(\d+)", zona_raw).group(1))))
        except Exception:
            pass
    try:
        temps = []
        with open(epw_path, "r", errors="ignore") as f:
            for linea in f.readlines()[8:]:
                partes = linea.strip().split(",")
                if len(partes) >= 7:
                    try: temps.append(float(partes[6]))
                    except ValueError: pass
        if len(temps) >= 8760:
            temps = temps[:8760]
            hdd18 = sum(max(0, 18 - t) for t in temps) / 24
            cdd10 = sum(max(0, t - 10) for t in temps) / 24
            if   cdd10 > 5000: return 1
            elif cdd10 > 3500: return 2
            elif cdd10 > 2000: return 3
            elif hdd18 < 2000: return 3
            elif hdd18 < 3000: return 4
            elif hdd18 < 4000: return 5
            elif hdd18 < 5000: return 6
            elif hdd18 < 7000: return 7
            else:              return 8
    except Exception:
        pass
    return 4


def _make_schedule(identifier, values_wd, values_sa, values_su, type_limit=None):
    from honeybee_energy.schedule.ruleset import ScheduleRuleset
    from honeybee_energy.schedule.day import ScheduleDay
    from honeybee_energy.schedule.rule import ScheduleRule
    from ladybug.dt import Time

    times = [Time(i, 0) for i in range(24)]
    day_wd = ScheduleDay(f"{identifier}_WD", values_wd, times)
    sched  = ScheduleRuleset(identifier, day_wd, schedule_type_limit=type_limit)

    day_sa = ScheduleDay(f"{identifier}_SA", values_sa, times)
    rule_sa = ScheduleRule(day_sa)
    rule_sa.apply_saturday = True
    sched.add_rule(rule_sa)

    day_su = ScheduleDay(f"{identifier}_SU", values_su, times)
    rule_su = ScheduleRule(day_su)
    rule_su.apply_sunday = True
    sched.add_rule(rule_su)
    return sched


def _calcular_cu_lightwell(tipo_uso, ancho, largo, altura):
    wcr = (2.5 * CURB_ALTURA_M * (DOMO_ANCHO_ID_M + DOMO_LARGO_ID_M) /
           (DOMO_ANCHO_ID_M * DOMO_LARGO_ID_M))
    if   wcr <= 0.5: we = 1.0 - 0.24 * (wcr / 0.5)
    elif wcr <= 1.0: we = 0.88 - 0.10 * ((wcr - 0.5) / 0.5)
    else:            we = max(0.50, 0.78 - 0.15 * (wcr - 1.0))

    gf  = DIRT_FACTORS.get(tipo_uso, 0.85)
    rcr = 5.0 * altura * (ancho + largo) / (ancho * largo)
    cu  = 0.85 * math.exp(-0.12 * rcr)

    factor_total = we * gf * cu
    lux_sp  = PERFILES_ASHRAE.get(tipo_uso, PERFILES_ASHRAE["Warehouse"])["lux"]
    lux_sensor = min(lux_sp / factor_total, lux_sp * 3.0)
    return cu, we, gf, factor_total, lux_sensor


def _leer_err(carpeta_caso):
    err_path = os.path.join(carpeta_caso, "eplusout.err")
    if not os.path.exists(err_path):
        return "(eplusout.err no encontrado)", False
    with open(err_path, "r", errors="ignore") as f:
        lineas = f.readlines()
    hay_fatal = any("Fatal" in l or "FATAL" in l for l in lineas)
    utiles = [l.rstrip() for l in lineas
              if any(k in l for k in ["Warning","Severe","Fatal","FATAL","SEVERE","Completed","Terminated","Run Time"])]
    return "\n".join(utiles), hay_fatal


def _detectar_energyplus():
    for ruta in ["energyplus","/usr/local/energyplus","/usr/local/bin/energyplus","/usr/local/EnergyPlus-23-2-0/energyplus"]:
        if os.path.exists(ruta) or shutil.which(ruta):
            return ruta
    raise RuntimeError("EnergyPlus no encontrado. Instala desde https://github.com/NREL/EnergyPlus/releases/tag/v23.2.0")


# =============================================================================
# SECCIÓN 2 — CONSTRUCCIÓN DEL MODELO HONEYBEE
# =============================================================================

def construir_modelo(ancho, largo, altura, tipo_uso, epw_path, sfr,
                     domo_vlt=DOMO_VLT_DEFAULT, domo_shgc=DOMO_SHGC_DEFAULT,
                     domo_u=DOMO_U_DEFAULT, domo_ancho_m=DOMO_ANCHO_M,
                     domo_largo_m=DOMO_LARGO_M, sufijo=""):
    """
    Construye el modelo Honeybee para un SFR dado.
    sfr=0 → caso base con apertura simbólica opaca.
    sfr>0 → cuadrícula real de domos Sunoptics.

    Retorna (hb_model, sfr_real, sim_params, sched_heat_id, sched_cool_id).

    BUG 2 FIX: ahora retorna los IDs reales de los schedules de temperatura
    para que _parchear_hvactemplate() los inyecte correctamente en el IDF.
    """
    from honeybee_energy.schedule.ruleset import ScheduleRuleset
    from honeybee_energy.lib.scheduletypelimits import schedule_type_limit_by_identifier
    from honeybee_energy.load.lighting import Lighting
    from honeybee_energy.load.equipment import ElectricEquipment
    from honeybee_energy.load.people import People
    from honeybee_energy.load.infiltration import Infiltration
    from honeybee_energy.load.ventilation import Ventilation
    from honeybee_energy.load.setpoint import Setpoint
    from honeybee_energy.load.daylight import DaylightingControl
    from honeybee_energy.programtype import ProgramType
    from honeybee_energy.material.glazing import EnergyWindowMaterialSimpleGlazSys
    from honeybee_energy.material.opaque import EnergyMaterial
    from honeybee_energy.construction.window import WindowConstruction
    from honeybee_energy.construction.opaque import OpaqueConstruction
    from honeybee_energy.hvac.idealair import IdealAirSystem
    from honeybee_energy.simulation.parameter import SimulationParameter
    from honeybee_energy.simulation.output import SimulationOutput
    from ladybug_geometry.geometry3d.pointvector import Point3D
    from ladybug_geometry.geometry3d.face import Face3D
    from dragonfly.model import Model as DFModel
    from dragonfly.building import Building
    from dragonfly.story import Story
    from dragonfly.room2d import Room2D
    from honeybee.aperture import Aperture
    from honeybee.boundarycondition import Outdoors

    _tipo = tipo_uso if tipo_uso in PERFILES_ASHRAE else "Warehouse"
    p = PERFILES_ASHRAE[_tipo]
    h = HORARIOS_ASHRAE[_tipo]

    zona_num = max(2, min(5, _detectar_zona_climatica(epw_path)))
    u_techo  = U_TECHO_ASHRAE[_tipo].get(zona_num, U_TECHO_ASHRAE[_tipo][4])

    try:
        frac_limit = schedule_type_limit_by_identifier("Fractional")
        temp_limit = schedule_type_limit_by_identifier("Temperature")
    except Exception:
        frac_limit = temp_limit = None

    # Schedules reales ASHRAE
    sched_luces     = _make_schedule(f"Luces_{_tipo}_{sufijo}",    h["luces"]["weekday"],    h["luces"]["saturday"],    h["luces"]["sunday"],    frac_limit)
    sched_equipos   = _make_schedule(f"Equip_{_tipo}_{sufijo}",    h["equipos"]["weekday"],  h["equipos"]["saturday"],  h["equipos"]["sunday"],  frac_limit)
    sched_ocupacion = _make_schedule(f"Ocup_{_tipo}_{sufijo}",     h["ocupacion"]["weekday"],h["ocupacion"]["saturday"],h["ocupacion"]["sunday"], frac_limit)
    sched_hvac      = _make_schedule(f"HVAC_{_tipo}_{sufijo}",     h["hvac"]["weekday"],     h["hvac"]["saturday"],     h["hvac"]["sunday"],     frac_limit)
    sched_actividad = ScheduleRuleset.from_constant_value(f"Act_{sufijo}", 120.0)

    cal_v, enf_v = p["calef"], p["enfriam"]
    sb_cool, sb_heat = h["setback_cool"], h["setback_heat"]

    cool_wd = [enf_v if v > 0 else sb_cool for v in h["hvac"]["weekday"]]
    cool_sa = [enf_v if v > 0 else sb_cool for v in h["hvac"]["saturday"]]
    cool_su = [enf_v if v > 0 else sb_cool for v in h["hvac"]["sunday"]]
    heat_wd = [cal_v if v > 0 else sb_heat for v in h["hvac"]["weekday"]]
    heat_sa = [cal_v if v > 0 else sb_heat for v in h["hvac"]["saturday"]]
    heat_su = [cal_v if v > 0 else sb_heat for v in h["hvac"]["sunday"]]

    sched_cool = _make_schedule(f"Cool_{_tipo}_{sufijo}", cool_wd, cool_sa, cool_su, temp_limit)
    sched_heat = _make_schedule(f"Heat_{_tipo}_{sufijo}", heat_wd, heat_sa, heat_su, temp_limit)

    # BUG 2 FIX: guardamos los IDs reales para devolverlos al caller
    sched_heat_id = sched_heat.identifier
    sched_cool_id = sched_cool.identifier

    # ProgramType completo
    lighting_obj = Lighting(f"Ilum_{sufijo}", p["lpd"], sched_luces, radiant_fraction=0.32, visible_fraction=0.25)
    equip_obj    = ElectricEquipment(f"Eq_{sufijo}", p["eq"], sched_equipos, radiant_fraction=0.5)
    people_obj   = People(f"Ppl_{sufijo}", 1.0/p["m2p"], sched_ocupacion, sched_actividad)
    infil_obj    = Infiltration(f"Inf_{sufijo}", p["inf"], ScheduleRuleset.from_constant_value(f"Infil_{sufijo}", 1.0, frac_limit))
    vent_obj     = Ventilation(f"Vent_{sufijo}", p["vent"]/1000.0)
    setpoint_obj = Setpoint(f"SP_{sufijo}", sched_heat, sched_cool)  # CRÍTICO

    prog = ProgramType(f"Prog_{sufijo}")
    prog.lighting           = lighting_obj
    prog.electric_equipment = equip_obj
    prog.people             = people_obj
    prog.infiltration       = infil_obj
    prog.ventilation        = vent_obj
    prog.setpoint           = setpoint_obj

    mat_domo    = EnergyWindowMaterialSimpleGlazSys(f"MatDomo_{sufijo}", u_factor=domo_u, shgc=domo_shgc, vt=domo_vlt)
    constr_domo = WindowConstruction(f"ConstrDomo_{sufijo}", [mat_domo])

    # Geometría
    pts_piso = [Point3D(0,0,0), Point3D(ancho,0,0), Point3D(ancho,largo,0), Point3D(0,largo,0)]
    room_df  = Room2D(f"Nave_{sufijo}", Face3D(pts_piso), floor_to_ceiling_height=altura)
    story    = Story(f"Niv_{sufijo}", room_2ds=[room_df])
    building = Building(f"Planta_{sufijo}", unique_stories=[story])
    hb_model = DFModel(f"Modelo_{sufijo}", buildings=[building]).to_honeybee(object_per_model="Building")[0]
    hb_room  = hb_model.rooms[0]

    hb_room.properties.energy.program_type = prog
    hvac = IdealAirSystem(f"HVAC_{sufijo}", economizer_type="DifferentialDryBulb")
    hb_room.properties.energy.hvac = hvac

    techo = next(f for f in hb_room.faces if f.type.name == "RoofCeiling")
    techo.boundary_condition = Outdoors()

    r_cond  = max((1.0/u_techo) - 0.17, 0.05)
    mat_techo    = EnergyMaterial(f"Techo_{sufijo}", 0.1, 0.1/r_cond, 30.0, 840.0)
    constr_techo = OpaqueConstruction(f"CTecho_{sufijo}", [mat_techo])
    techo.properties.energy.construction = constr_techo

    area_domo = domo_ancho_m * domo_largo_m
    area_piso = ancho * largo

    if sfr <= 0:
        cx, cy = ancho/2, largo/2
        ap_sim = Aperture(f"DomoSim_{sufijo}", Face3D([
            Point3D(cx-domo_ancho_m/2, cy-domo_largo_m/2, altura),
            Point3D(cx+domo_ancho_m/2, cy-domo_largo_m/2, altura),
            Point3D(cx+domo_ancho_m/2, cy+domo_largo_m/2, altura),
            Point3D(cx-domo_ancho_m/2, cy+domo_largo_m/2, altura),
        ]))
        mat_op  = EnergyWindowMaterialSimpleGlazSys(f"MatOp_{sufijo}", u_factor=domo_u, shgc=0.001, vt=0.001)
        ap_sim.properties.energy.construction = WindowConstruction(f"COP_{sufijo}", [mat_op])
        techo.add_aperture(ap_sim)
        sfr_real = area_domo / area_piso
    else:
        num_domos = max(1, math.ceil((area_piso * sfr) / area_domo))
        cols  = max(1, round((num_domos * (ancho/largo))**0.5))
        filas = max(1, math.ceil(num_domos / cols))
        dx, dy = ancho/cols, largo/filas
        cnt = 0
        for i in range(cols):
            for j in range(filas):
                cx = (i*dx) + dx/2
                cy = (j*dy) + dy/2
                ap = Aperture(f"Domo_{cnt}_{sufijo}", Face3D([
                    Point3D(cx-domo_ancho_m/2, cy-domo_largo_m/2, altura),
                    Point3D(cx+domo_ancho_m/2, cy-domo_largo_m/2, altura),
                    Point3D(cx+domo_ancho_m/2, cy+domo_largo_m/2, altura),
                    Point3D(cx-domo_ancho_m/2, cy+domo_largo_m/2, altura),
                ]))
                ap.properties.energy.construction = constr_domo
                techo.add_aperture(ap)
                cnt += 1
        sfr_real = (cnt * area_domo) / area_piso

    # DaylightingControl con setpoint ajustado LightWell + CU
    cu, we, gf, factor_total, lux_sensor = _calcular_cu_lightwell(_tipo, ancho, largo, altura)
    sensor_pos = Point3D(ancho/2, largo/2, 0.8)
    daylight_ctrl = DaylightingControl(
        sensor_position=sensor_pos,
        illuminance_setpoint=lux_sensor,
        control_fraction=1.0,
        min_power_input=0.0,
        min_light_output=0.0,
        off_at_minimum=False,
    )
    hb_room.properties.energy.daylighting_control = daylight_ctrl

    # SimulationParameter
    sim_params = SimulationParameter()
    sim_params.simulation_control.do_zone_sizing   = True
    sim_params.simulation_control.do_system_sizing = True
    sim_params.simulation_control.do_plant_sizing  = True
    sim_params.output = SimulationOutput(
        include_sqlite=True, include_html=False,
        reporting_frequency="Hourly",
        outputs=[
            "Zone Ideal Loads Cooling Energy",
            "Zone Ideal Loads Heating Energy",
            "Zone Lights Electricity Energy",
            "Daylighting Reference Point 1 Illuminance",
        ]
    )

    # BUG 2 FIX: retornamos 5 valores incluyendo IDs reales de schedules
    return hb_model, sfr_real, sim_params, sched_heat_id, sched_cool_id


# =============================================================================
# SECCIÓN 3 — TRADUCCIÓN A IDF Y EJECUCIÓN DE ENERGYPLUS
# =============================================================================

def _parchear_hvactemplate(idf_path_orig, sched_calef_id, sched_enfriam_id, ancho, largo):
    """
    Reemplaza HVACTemplate por objetos nativos. Agrega grid ASHRAE de sensores.

    BUG 1 FIX: Elimina ZoneHVAC:EquipmentConnections pre-generado por Honeybee
               antes de crear el nuestro, evitando el conflicto de nombres
               que causaba 'ZoneHVAC:EquipmentList not found = EQLIST_NAVE_BASE'.

    BUG 3 FIX: Usa ancho/largo reales de la nave en vez de 50/100 hardcodeados.
    """
    with open(idf_path_orig, "r", errors="ignore") as f:
        texto = f.read()

    if "HVACTemplate:" not in texto:
        return idf_path_orig

    def _objs(txt, tipo):
        patron = re.compile(r"^\s*" + re.escape(tipo) + r"\s*,", re.MULTILINE | re.IGNORECASE)
        bloques = []
        for m in patron.finditer(txt):
            pos = m.start()
            while pos < len(txt):
                nl = txt.find("\n", pos)
                if nl == -1: nl = len(txt)
                ll = re.sub(r"!.*", "", txt[pos:nl])
                if ";" in ll:
                    bloques.append(txt[m.start(): pos + ll.index(";") + 1])
                    break
                pos = nl + 1
        return bloques

    def _campos(b):
        return [p.strip() for p in re.split(r"[,;]", re.sub(r"!.*", "", b))]

    tstats    = _objs(texto, "HVACTemplate:Thermostat")
    idealairs = _objs(texto, "HVACTemplate:Zone:IdealLoadsAirSystem")
    if not idealairs:
        return idf_path_orig

    termostatos = {}
    for b in tstats:
        c = _campos(b)
        nombre = c[1] if len(c) > 1 else "__default__"
        hs = next((v for v in c[2:] if v), sched_calef_id)
        cs = next((v for v in c[4:] if v), sched_enfriam_id)
        termostatos[nombre] = {"heat": hs, "cool": cs}
    if not termostatos:
        termostatos["__default__"] = {"heat": sched_calef_id, "cool": sched_enfriam_id}

    zonas = []
    for b in idealairs:
        c = _campos(b)
        zonas.append({
            "zona": c[1] if len(c) > 1 else "Zona",
            "tstat": c[2] if len(c) > 2 and c[2] else list(termostatos.keys())[0]
        })

    idf_limpio = texto

    # Eliminar HVACTemplate
    for tipo in ["HVACTemplate:Thermostat", "HVACTemplate:Zone:IdealLoadsAirSystem"]:
        for b in _objs(idf_limpio, tipo):
            idf_limpio = idf_limpio.replace(b, "")

    # BUG 1 FIX: Eliminar ZoneHVAC:EquipmentConnections pre-generados por Honeybee.
    # Honeybee a veces genera estos objetos con nombres como EQLIST_NAVE_BASE
    # que no coinciden con los que crea nuestro parche (EqList_NAVE_BASE).
    # Al eliminarlos aquí, nuestros objetos son los únicos que quedan.
    for b in _objs(idf_limpio, "ZoneHVAC:EquipmentConnections"):
        idf_limpio = idf_limpio.replace(b, "")
    for b in _objs(idf_limpio, "ZoneHVAC:EquipmentList"):
        idf_limpio = idf_limpio.replace(b, "")

    # Limpiar Output:Variable duplicados de HVAC
    for b in _objs(idf_limpio, "Output:Variable"):
        if "Zone Ideal Loads Cooling Energy" in b or "Zone Ideal Loads Heating Energy" in b:
            idf_limpio = idf_limpio.replace(b, "")

    # Limpiar IlluminanceMap previo
    idf_limpio = re.sub(r"Output:IlluminanceMap,.*?;\s*\n", "", idf_limpio, flags=re.DOTALL)

    nativos = [
        "",
        "!- === objetos nativos SkyPlus v22.1 ===",
        "ScheduleTypeLimits,","  CtrlType_Limits,","  0,","  4,","  DISCRETE;","",
        "Schedule:Constant,","  Sched_DualSetpoint,","  CtrlType_Limits,","  4;","",
        "Output:Variable,","  *,","  Zone Ideal Loads Supply Air Sensible Cooling Energy,","  Hourly;","",
        "Output:Variable,","  *,","  Zone Ideal Loads Supply Air Sensible Heating Energy,","  Hourly;","",
        "Output:Variable,","  *,","  Zone Ideal Loads Supply Air Latent Cooling Energy,","  Hourly;","",
        "Output:Variable,","  *,","  Zone Ideal Loads Supply Air Latent Heating Energy,","  Hourly;","",
        "Output:Variable,","  *,","  Daylighting Reference Point 1 Illuminance,","  Hourly;","",
    ]

    for info in zonas:
        zona = info["zona"]
        ts   = termostatos.get(info["tstat"], list(termostatos.values())[0])
        tag  = re.sub(r"[^A-Za-z0-9]", "_", zona)[:18]

        # BUG 2 FIX: usamos los IDs reales de schedules (sched_calef_id / sched_enfriam_id)
        # que fueron generados en construir_modelo() para ESTE modelo específico.
        # Ya no usamos los defaults "Sched_Heat" / "Sched_Cool" que no existen en el IDF.
        nativos += [
            f"!- --- Zona: {zona} ---",
            f"ThermostatSetpoint:DualSetpoint,",
            f"  DualSP_{tag},",
            f"  {ts['heat']},",
            f"  {ts['cool']};",
            "",
            f"ZoneControl:Thermostat,",
            f"  ZCtrl_{tag},",
            f"  {zona},",
            "  Sched_DualSetpoint,",
            "  ThermostatSetpoint:DualSetpoint,",
            f"  DualSP_{tag};",
            "",
            f"ZoneHVAC:IdealLoadsAirSystem,",
            f"  IdealAir_{tag},",
            "  ,",
            f"  Supply_{tag},",
            f"  Exhaust_{tag};",
            "",
            f"ZoneHVAC:EquipmentList,",
            f"  EqList_{tag},",
            "  SequentialLoad,",
            "  ZoneHVAC:IdealLoadsAirSystem,",
            f"  IdealAir_{tag},",
            "  1,",
            "  1;",
            "",
            f"ZoneHVAC:EquipmentConnections,",
            f"  {zona},",
            f"  EqList_{tag},",
            f"  Supply_{tag},",
            f"  Exhaust_{tag},",
            f"  ZAir_{tag},",
            f"  Return_{tag};",
            "",
        ]

    # Grid ASHRAE de sensores de daylighting
    bloque_ctrl = re.search(r"Daylighting:Controls\s*,.*?;", idf_limpio, re.DOTALL | re.IGNORECASE)
    if bloque_ctrl:
        texto_ctrl = bloque_ctrl.group()
        setpoint_ajustado = 300.0
        for tok in re.sub(r"!.*", "", texto_ctrl).split(","):
            tok = tok.replace(";", "").strip()
            try:
                val = float(tok)
                if 100 <= val <= 5000:
                    setpoint_ajustado = val
                    break
            except ValueError:
                continue

        cps = [c.strip() for c in re.sub(r"!.*", "", texto_ctrl).split(",")]
        nombre_zona_dl = cps[2] if len(cps) > 2 else (zonas[0]["zona"] if zonas else "ZONA")

        idf_limpio = idf_limpio.replace(texto_ctrl, "")
        for blq in re.findall(r"Daylighting:ReferencePoint\s*,.*?;", idf_limpio, re.DOTALL | re.IGNORECASE):
            idf_limpio = idf_limpio.replace(blq, "")

        # BUG 3 FIX: usar ancho/largo reales de la nave, no 50/100 hardcodeados
        nx = max(1, int(ancho / 7.14))
        ny = max(1, int(largo / 7.14))
        fraccion = 1.0 / (nx * ny)
        dx_g, dy_g = ancho / nx, largo / ny

        rp_names, rp_blocks = [], []
        for ix in range(nx):
            for iy in range(ny):
                cx = (ix * dx_g) + dx_g / 2
                cy = (iy * dy_g) + dy_g / 2
                rp_name = f"ASHRAE_RP_{len(rp_names):03d}"
                rp_names.append(rp_name)
                rp_blocks.append(
                    f"Daylighting:ReferencePoint,\n"
                    f"  {rp_name},\n"
                    f"  {nombre_zona_dl},\n"
                    f"  {cx:.4f},\n"
                    f"  {cy:.4f},\n"
                    f"  0.8;\n"
                )

        lineas_ctrl = [
            "Daylighting:Controls,","  NAVE_ASHRAE_DAYLIGHTING,",
            f"  {nombre_zona_dl},",
            "  SplitFlux,","  ,","  Continuous,","  0.0,","  0.0,","  1,","  1.0,","  ,","  180.0,","  22.0,","  ,",
        ]
        for i, rp_name in enumerate(rp_names):
            es_ultimo = (i == len(rp_names) - 1)
            lineas_ctrl += [
                f"  {rp_name},",
                f"  {fraccion:.6f},",
                f"  {setpoint_ajustado:.1f}{',' if not es_ultimo else ';'}"
            ]

        nativos += rp_blocks + lineas_ctrl + [""]

        # v22.2: Agregar Output:IlluminanceMap 5mx5m fijo (200 sensores UDI-e)
        # Formato EnergyPlus: Name, Zone, Z, Xmin, Xmax, Nx, Ymin, Ymax, Ny
        nativos += [
            f"!- === v22.2: IlluminanceMap {ILLUM_MAP_NX}x{ILLUM_MAP_NY} — {ILLUM_MAP_N} sensores UDI-e ===",
            f"Output:IlluminanceMap,",
            f"  SKYPLUS_ILLUM_MAP_{tag},",
            f"  {nombre_zona_dl},",
            f"  0.8,",
            f"  0.0,",
            f"  {ancho:.2f},",
            f"  {ILLUM_MAP_NX},",
            f"  0.0,",
            f"  {largo:.2f},",
            f"  {ILLUM_MAP_NY};",
            "",
        ]

    idf_patched = idf_path_orig.replace(".idf", "_patched.idf")
    with open(idf_patched, "w") as f:
        f.write(idf_limpio)
        f.write("\n".join(nativos))
        f.write("\n")
    return idf_patched


def traducir_y_simular(hb_model, epw_path, sim_params, carpeta, nombre,
                       sched_calef_id, sched_enfriam_id, ancho, largo):
    """
    Traduce HBModel → IDF, parchea HVAC, ejecuta EnergyPlus. Retorna ruta del .sql.

    BUG 2 FIX: sched_calef_id y sched_enfriam_id son ahora parámetros
    obligatorios (sin defaults incorrectos). Se obtienen de construir_modelo().

    BUG 3 FIX: ancho y largo se pasan al parche para el grid ASHRAE correcto.
    """
    carpeta_caso = os.path.join(carpeta, nombre)
    os.makedirs(carpeta_caso, exist_ok=True)

    hbjson_path = os.path.join(carpeta_caso, "modelo.hbjson")
    with open(hbjson_path, "w") as f:
        json.dump(hb_model.to_dict(), f)

    sp_path = os.path.join(carpeta_caso, "sim_params.json")
    with open(sp_path, "w") as f:
        json.dump(sim_params.to_dict(), f)

    idf_path = os.path.join(carpeta_caso, "modelo.idf")
    r_tr = subprocess.run(
        ["honeybee-energy", "translate", "model-to-idf",
         hbjson_path, "--output-file", idf_path, "--sim-par-json", sp_path],
        capture_output=True, text=True
    )

    if not os.path.exists(idf_path):
        try:
            from honeybee_energy.run import to_idf
            idf_path = to_idf(hbjson_path, sim_par_json=sp_path, folder=carpeta_caso)
        except Exception as e:
            raise RuntimeError(f"Fallo traducción IDF '{nombre}'. CLI: {r_tr.stderr[-400:]} API: {e}")

    # Parchear con los 3 fixes aplicados
    idf_path = _parchear_hvactemplate(idf_path, sched_calef_id, sched_enfriam_id, ancho, largo)

    ep_exec = _detectar_energyplus()
    r_sim = subprocess.run(
        [ep_exec, "-w", epw_path, "-d", carpeta_caso, idf_path],
        capture_output=True, text=True, timeout=600
    )

    err_texto, hay_fatal = _leer_err(carpeta_caso)

    sql_path = os.path.join(carpeta_caso, "eplusout.sql")
    if not os.path.exists(sql_path):
        for root, _, files in os.walk(carpeta_caso):
            for fn in files:
                if fn == "eplusout.sql":
                    sql_path = os.path.join(root, fn)

    if not os.path.exists(sql_path):
        raise RuntimeError(
            f"EnergyPlus falló para '{nombre}'.\n"
            f"Últimos errores:\n{err_texto[-800:]}"
        )

    return sql_path


# =============================================================================
# SECCIÓN 4 — LECTURA DE RESULTADOS SQL
# =============================================================================

def leer_kwh_sql(sql_path):
    """Lee .sql de EnergyPlus y retorna dict con kWh anuales."""
    conn   = sqlite3.connect(sql_path)
    df_dic = pd.read_sql_query("SELECT ReportDataDictionaryIndex, Name FROM ReportDataDictionary", conn)

    def kwh_anual(patron):
        rows = df_dic[df_dic["Name"].str.contains(patron, case=False, na=False)]
        if rows.empty: return 0.0
        idxs = ",".join(map(str, rows["ReportDataDictionaryIndex"].tolist()))
        res  = pd.read_sql_query(
            f"SELECT SUM(Value) as total FROM ReportData WHERE ReportDataDictionaryIndex IN ({idxs})", conn
        )
        return float(res["total"].iloc[0] or 0.0) / 3_600_000.0

    result = {
        "kwh_iluminacion":    kwh_anual("Lights Electricity Energy"),
        "kwh_cooling_demand": kwh_anual("Ideal Loads Supply Air Sensible Cooling Energy") + kwh_anual("Ideal Loads Supply Air Latent Cooling Energy"),
        "kwh_heating_demand": kwh_anual("Ideal Loads Supply Air Sensible Heating Energy") + kwh_anual("Ideal Loads Supply Air Latent Heating Energy"),
    }
    conn.close()
    return result


# =============================================================================
# SECCIÓN 5 — MOTOR 2: ILUMINANCIA ANALÍTICA EPW + SEMÁFORO NORMATIVO
# =============================================================================


def extraer_udi_e(sql_path):
    """
    v22.2: Lee DaylightMapHourlyData y calcula UDI-e espacial (IES LM-83).
    Un sensor falla si supera UDI_LUX_UMBRAL en >UDI_PCT_HORA_MAX% de sus horas con luz.
    UDI-e = % del area total donde sensores fallan.
    Retorna dict con: udi_e_pct, lux_max, lux_p95, n_sensores_ok, n_sensores_mal
    """
    conn = sqlite3.connect(sql_path)
    cur  = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM DaylightMapHourlyData;")
        total = cur.fetchone()[0]
    except Exception:
        conn.close()
        return {"udi_e_pct": None, "lux_max": 0, "lux_p95": 0,
                "n_sensores_ok": 0, "n_sensores_mal": 0}
    if total == 0:
        conn.close()
        return {"udi_e_pct": None, "lux_max": 0, "lux_p95": 0,
                "n_sensores_ok": 0, "n_sensores_mal": 0}
    cur.execute("SELECT X, Y, Illuminance FROM DaylightMapHourlyData WHERE Illuminance > 0")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return {"udi_e_pct": 0.0, "lux_max": 0, "lux_p95": 0,
                "n_sensores_ok": ILLUM_MAP_N, "n_sensores_mal": 0}
    df_udi = pd.DataFrame(rows, columns=["X", "Y", "Illuminance"])
    lux_max = float(df_udi["Illuminance"].max())
    lux_p95 = float(df_udi["Illuminance"].quantile(0.95))
    df_udi["sobre"] = df_udi["Illuminance"] > UDI_LUX_UMBRAL
    por_sensor = df_udi.groupby(["X", "Y"]).agg(
        horas_totales=("Illuminance", "count"),
        horas_sobre=("sobre", "sum")
    ).reset_index()
    por_sensor["pct_sobre"] = por_sensor["horas_sobre"] / por_sensor["horas_totales"] * 100
    por_sensor["falla"]     = por_sensor["pct_sobre"] > UDI_PCT_HORA_MAX
    n_mal     = int(por_sensor["falla"].sum())
    n_ok      = len(por_sensor) - n_mal
    udi_e_pct = n_mal / len(por_sensor) * 100 if len(por_sensor) > 0 else 0.0
    return {
        "udi_e_pct":      round(udi_e_pct, 2),
        "lux_max":        round(lux_max, 0),
        "lux_p95":        round(lux_p95, 1),
        "n_sensores_ok":  n_ok,
        "n_sensores_mal": n_mal,
    }


def calcular_iluminancia_analitica(epw_path, tipo_uso, sfr_vals, domo_vlt=DOMO_VLT_DEFAULT):
    """
    Lee EPW col20 × 10 → iluminancia real en lux.
    Aplica: fc_interior = E_ext_lux × (SFR × VLT × CU)
    Retorna dict con fc_lux, semaforo_txt, semaforo_color, umbral_p25.
    """
    illum_8760 = []
    with open(epw_path, "r", errors="ignore") as f:
        lineas_epw = f.readlines()[8:]
    for linea in lineas_epw:
        partes = linea.strip().split(",")
        if len(partes) >= 20:
            try: illum_8760.append(float(partes[19]) * 10.0)
            except ValueError: illum_8760.append(0.0)
        else: illum_8760.append(0.0)
    illum_8760 = np.array(illum_8760[:8760])

    h = HORARIOS_ASHRAE.get(tipo_uso, HORARIOS_ASHRAE["Warehouse"])
    horas_occ_idx = {hr for hr in range(24) if h["ocupacion"]["weekday"][hr] > 0}
    horas_luz_vals = [illum_8760[i] for i in range(8760) if i % 24 in horas_occ_idx and illum_8760[i] > 0]

    umbral_p25 = float(np.percentile(horas_luz_vals, 25)) if horas_luz_vals else 10000.0
    cu = 0.736  # Validado: WE=0.777, GF=0.85, LightWell Sunoptics 800MD

    fc_lux, sem_txt, sem_color = [], [], []
    for sfr_pct in sfr_vals:
        sfr      = sfr_pct / 100.0
        transmis = sfr * domo_vlt * cu
        vals = [illum_8760[i] * transmis for i in range(8760)
                if i % 24 in horas_occ_idx and illum_8760[i] >= umbral_p25]
        fp = round(float(np.mean(vals)) if vals else 0.0, 1)
        fc_lux.append(fp)

        if fp < UMBRAL_SUBILUM:
            sem_txt.append("Subiluminado (<150 lux)");          sem_color.append(COLORES_SEM["AZUL"])
        elif fp <= UMBRAL_OPTIMO:
            sem_txt.append("Confort óptimo (ISO+IES)");         sem_color.append(COLORES_SEM["VERDE"])
        elif fp <= UMBRAL_LIMITE:
            sem_txt.append("Límite UDI-Autonomous");            sem_color.append(COLORES_SEM["AMARILLO"])
        else:
            sem_txt.append("Sobreiluminación UDI-Exceeded");    sem_color.append(COLORES_SEM["ROJO"])

    return {"fc_lux": fc_lux, "semaforo_txt": sem_txt, "semaforo_color": sem_color, "umbral_p25": umbral_p25}


# =============================================================================
# SECCIÓN 6 — API PÚBLICA
# =============================================================================

def configurar_proyecto(ancho=50.0, largo=100.0, altura=8.0,
                        tipo_uso="Warehouse", epw_path=None,
                        sfr_diseno=0.04, sfr_curva=None,
                        domo_vlt=DOMO_VLT_DEFAULT, domo_shgc=DOMO_SHGC_DEFAULT,
                        domo_u=DOMO_U_DEFAULT, domo_ancho_m=DOMO_ANCHO_M,
                        domo_largo_m=DOMO_LARGO_M,
                        cop_cooling=COP_COOLING_DEFAULT, eff_heating=EFF_HEATING_DEFAULT,
                        carpeta_sims=None):
    """
    Valida y empaqueta parámetros del proyecto.
    Retorna dict de configuración para pasar a simular_caso_diseno() o calcular_curva_sfr().

    sfr_diseno : SFR específico del cliente (Etapa 1 — simulación rápida)
    sfr_curva  : lista de SFRs para la curva completa (Etapa 2 — PDF)
    """
    if sfr_curva is None:
        sfr_curva = [0, 1, 2, 3, 4, 5, 6]
    if tipo_uso not in PERFILES_ASHRAE:
        raise ValueError(f"tipo_uso debe ser uno de: {list(PERFILES_ASHRAE.keys())}")
    if epw_path is None or not os.path.exists(epw_path):
        raise FileNotFoundError(f"EPW no encontrado: {epw_path}")
    if ancho * largo > 10_000:
        raise ValueError("Área supera 10,000 m². Proyectos de mayor escala requieren servicio BEM premium.")
    if carpeta_sims is None:
        carpeta_sims = tempfile.mkdtemp(prefix="skyplus_sims_")

    return {
        "ancho": ancho, "largo": largo, "altura": altura,
        "tipo_uso": tipo_uso, "epw_path": epw_path,
        "sfr_diseno": sfr_diseno, "sfr_curva": sfr_curva,
        "domo_vlt": domo_vlt, "domo_shgc": domo_shgc, "domo_u": domo_u,
        "domo_ancho_m": domo_ancho_m, "domo_largo_m": domo_largo_m,
        "cop_cooling": cop_cooling, "eff_heating": eff_heating,
        "carpeta_sims": carpeta_sims,
        "zona_climatica": _detectar_zona_climatica(epw_path),
    }


# -----------------------------------------------------------------------------
# ETAPA 1 — simular_caso_diseno()
# 2 simulaciones: Base (SFR=0%) + Diseño (SFR del cliente)
# Tiempo estimado: ~2-4 minutos en Cloud Run
# -----------------------------------------------------------------------------

def simular_caso_diseno(config, callback=None):
    """
    Etapa 1 del flujo SaaS. Corre solo 2 simulaciones EnergyPlus:
      - Caso base (SFR=0%, sin domos)
      - Caso diseño (SFR configurado por el cliente en los sliders)

    Args:
        config   : dict de configurar_proyecto()
        callback : fn(paso, total, mensaje) para barra de progreso Streamlit

    Retorna dict con:
        kwh_base         : float  — consumo total caso base (kWh/año)
        kwh_diseno       : float  — consumo total caso diseño (kWh/año)
        ahorro_neto      : float  — kWh/año ahorrados
        pct_ahorro       : float  — % ahorro sobre caso base
        desglose_base    : dict   — {iluminacion, cooling, heating} kWh base
        desglose_diseno  : dict   — {iluminacion, cooling, heating} kWh diseño
        sfr_real         : float  — SFR real alcanzado (%)
        n_domos          : int    — número de domos generados
        fc_lux           : float  — iluminancia promedio (lux) para este SFR
        semaforo_txt     : str    — texto semáforo normativo
        semaforo_color   : str    — color hex semáforo
        figura           : go.Figure — gráfica comparativa base vs diseño
        recomendacion    : str    — texto ejecutivo
        error            : str|None
    """
    _L  = config.get("lang",  "ES")
    _U  = config.get("units", "metric")
    _FT = 3.28084

    def _cb(paso, total, msg):
        if callback: callback(paso, total, msg)

    ancho        = config["ancho"]
    largo        = config["largo"]
    altura       = config["altura"]
    tipo_uso     = config["tipo_uso"]
    epw_path     = config["epw_path"]
    sfr_diseno   = config["sfr_diseno"]
    domo_vlt     = config["domo_vlt"]
    domo_shgc    = config["domo_shgc"]
    domo_u       = config["domo_u"]
    domo_ancho_m = config["domo_ancho_m"]
    domo_largo_m = config["domo_largo_m"]
    cop_cooling  = config["cop_cooling"]
    eff_heating  = config["eff_heating"]
    carpeta_sims = config["carpeta_sims"]

    try:
        # --- Caso Base ---
        _cb(0, 2, ("Simulating baseline (SFR=0%)..." if _L=="EN" else "Simulando caso base (SFR=0%)..."))
        hb_base, sfr_real_base, sp_base, sh_id_base, sc_id_base = construir_modelo(
            ancho=ancho, largo=largo, altura=altura,
            tipo_uso=tipo_uso, epw_path=epw_path, sfr=0,
            domo_vlt=domo_vlt, domo_shgc=domo_shgc, domo_u=domo_u,
            domo_ancho_m=domo_ancho_m, domo_largo_m=domo_largo_m,
            sufijo="base",
        )
        sql_base = traducir_y_simular(
            hb_base, epw_path, sp_base, carpeta_sims, "caso_base",
            sched_calef_id=sh_id_base, sched_enfriam_id=sc_id_base,
            ancho=ancho, largo=largo,
        )
        res_base = leer_kwh_sql(sql_base)

        # --- Caso Diseño ---
        sfr_pct = round(sfr_diseno * 100, 1)
        sufijo_d = f"sfr_{int(sfr_pct):02d}pct"
        _cb(1, 2, (f"Simulating design case (SFR={sfr_pct}%)..." if _L=="EN" else f"Simulando caso diseño (SFR={sfr_pct}%)..."))
        hb_dis, sfr_real_dis, sp_dis, sh_id_dis, sc_id_dis = construir_modelo(
            ancho=ancho, largo=largo, altura=altura,
            tipo_uso=tipo_uso, epw_path=epw_path, sfr=sfr_diseno,
            domo_vlt=domo_vlt, domo_shgc=domo_shgc, domo_u=domo_u,
            domo_ancho_m=domo_ancho_m, domo_largo_m=domo_largo_m,
            sufijo=sufijo_d,
        )
        sql_dis = traducir_y_simular(
            hb_dis, epw_path, sp_dis, carpeta_sims, f"caso_{sufijo_d}",
            sched_calef_id=sh_id_dis, sched_enfriam_id=sc_id_dis,
            ancho=ancho, largo=largo,
        )
        res_dis = leer_kwh_sql(sql_dis)

    except Exception as e:
        return {"error": str(e), "figura": None}

    _cb(2, 2, ("Calculating results..." if _L=="EN" else "Calculando resultados..."))

    # Split-Flux
    kwh_luz_b  = res_base["kwh_iluminacion"]
    kwh_cool_b = res_base["kwh_cooling_demand"] / cop_cooling
    kwh_heat_b = res_base["kwh_heating_demand"] / eff_heating
    kwh_base   = kwh_luz_b + kwh_cool_b + kwh_heat_b

    kwh_luz_d  = res_dis["kwh_iluminacion"]
    kwh_cool_d = res_dis["kwh_cooling_demand"] / cop_cooling
    kwh_heat_d = res_dis["kwh_heating_demand"] / eff_heating
    kwh_dis    = kwh_luz_d + kwh_cool_d + kwh_heat_d

    ahorro_luz   = kwh_luz_b  - kwh_luz_d
    penal_cool   = kwh_cool_d - kwh_cool_b
    ahorro_heat  = kwh_heat_b - kwh_heat_d
    ahorro_neto  = ahorro_luz - penal_cool + ahorro_heat
    pct_ahorro   = (ahorro_neto / kwh_base * 100) if kwh_base > 0 else 0.0

    n_domos = math.ceil(ancho * largo * sfr_diseno / (domo_ancho_m * domo_largo_m))

    # Motor 2: iluminancia analítica
    luz = calcular_iluminancia_analitica(epw_path, tipo_uso, [0, sfr_pct], domo_vlt)
    fc_lux_dis  = luz["fc_lux"][1]
    sem_txt_dis = luz["semaforo_txt"][1]
    sem_col_dis = luz["semaforo_color"][1]

    # Gráfica comparativa Base vs Diseño
    categorias = (["Lighting", "Cooling", "Heating"] if _L == "EN" else ["Iluminación", "Cooling", "Heating"])
    vals_base   = [kwh_luz_b, kwh_cool_b, kwh_heat_b]
    vals_dis    = [kwh_luz_d, kwh_cool_d, kwh_heat_d]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name=("Baseline (No skylights)" if _L=="EN" else "Caso Base (Sin domos)"),
        x=categorias, y=vals_base,
        marker_color="#e74c3c",
        text=[f"{v:,.0f}" for v in vals_base], textposition="auto",
    ))
    fig.add_trace(go.Bar(
        name=(f"Design case (SFR={sfr_pct}%)" if _L=="EN" else f"Caso Diseño (SFR={sfr_pct}%)"),
        x=categorias, y=vals_dis,
        marker_color="#2ecc71",
        text=[f"{v:,.0f}" for v in vals_dis], textposition="auto",
    ))
    fig.update_layout(
        barmode="group",
        title=dict(
            text=(f"SkyPlus — Energy Results | {tipo_uso} {ancho:.0f}×{largo:.0f}m | SFR={sfr_pct}%" if _L=="EN" else f"SkyPlus — Resultado Energético | {tipo_uso} {ancho:.0f}×{largo:.0f}m | SFR={sfr_pct}%"),
            font=dict(size=15)
        ),
        yaxis_title=("Energy (kBtu/yr)" if _L=="EN" else "Energía (kWh/año)"),
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=420,
        annotations=[dict(
            x=0.5, y=1.15, xref="paper", yref="paper",
            text=(f"<b>Net savings: {ahorro_neto*CONVERSION['kwh_to_kbtu']:,.0f} kBtu/yr ({pct_ahorro:.1f}%)</b>" if _L=="EN" else f"<b>Ahorro neto: {ahorro_neto:,.0f} kWh/año ({pct_ahorro:.1f}%)</b>"),
            showarrow=False, font=dict(size=14, color="#2ecc71"),
            bgcolor="white", bordercolor="#2ecc71", borderwidth=1,
        )],
    )

    # Texto de recomendación
    lux_setpoint = PERFILES_ASHRAE.get(tipo_uso, PERFILES_ASHRAE["Warehouse"])["lux"]
    _dim_str = (f"{_ancho_usr:.0f}×{_largo_usr:.0f} ft" if _U=="imperial"
               else f"{ancho:.0f}×{largo:.0f}m")
    _ancho_usr = ancho * _FT if _U=="imperial" else ancho
    _largo_usr = largo * _FT if _U=="imperial" else largo
    _dim_str   = f"{_ancho_usr:.0f}×{_largo_usr:.0f} {'ft' if _U=='imperial' else 'm'}"
    _sky_word  = "skylights" if _L=="EN" else "domos"
    _lux_str   = fmt_illuminance(fc_lux_dis, _U, 0)
    _nrg_str   = fmt_energy(ahorro_neto, _U)
    _sem_str   = get_compliance_label(sem_txt_dis, _L)
    if _L == "EN":
        recomendacion = (
            f"**Your {_dim_str} facility with SFR={sfr_pct}% saves {_nrg_str} ({pct_ahorro:.1f}%)**\n\n"
            f"With **{n_domos} {_sky_word}** installed, average illuminance reaches "
            f"**{_lux_str}** — {_sem_str}.\n\n"
            f"*Results generated by EnergyPlus 23.2 (DOE). "
            f"Want to see the full SFR 0%→6% curve and optimization table? "
            f"Request the complete PDF report.*"
        )
    else:
        recomendacion = (
            f"**Tu nave de {_dim_str} con SFR={sfr_pct}% ahorra {_nrg_str} ({pct_ahorro:.1f}%)**\n\n"
            f"Con **{n_domos} {_sky_word}** instalados, la iluminancia promedio alcanza "
            f"**{_lux_str}** — {_sem_str}.\n\n"
            f"*Resultados generados por EnergyPlus 23.2 (DOE oficial). "
            f"¿Quieres ver la curva completa SFR 0%→6% y la tabla de optimización? "
            f"Solicita el reporte PDF completo.*"
        )

    return {
        "kwh_base":        kwh_base,
        "kwh_diseno":      kwh_dis,
        "ahorro_neto":     ahorro_neto,
        "pct_ahorro":      pct_ahorro,
        "desglose_base":   {"iluminacion": kwh_luz_b, "cooling": kwh_cool_b, "heating": kwh_heat_b},
        "desglose_diseno": {"iluminacion": kwh_luz_d, "cooling": kwh_cool_d, "heating": kwh_heat_d},
        "sfr_real":        round(sfr_real_dis * 100, 2),
        "n_domos":         n_domos,
        "fc_lux":          fc_lux_dis,
        "semaforo_txt":    sem_txt_dis,
        "semaforo_color":  sem_col_dis,
        "figura":          fig,
        "recomendacion":   recomendacion,
        "sql_base":        sql_base,   # guardado para reutilizar en Etapa 2
        "error":           None,
    }


# -----------------------------------------------------------------------------
# ETAPA 2 — calcular_curva_sfr()
# 7 simulaciones: SFR 0%→6% → curva óptima completa
# Se activa cuando el cliente solicita el PDF
# OPTIMIZACIÓN: si sql_base ya existe (de Etapa 1), lo reutiliza
# -----------------------------------------------------------------------------

def calcular_curva_sfr(config, callback=None, sql_base_existente=None):
    """
    Etapa 2 del flujo SaaS. Corre 7 simulaciones (SFR 0%→6%).

    Args:
        config             : dict de configurar_proyecto()
        callback           : fn(paso, total, mensaje) para barra de progreso
        sql_base_existente : ruta del sql base de Etapa 1 (evita re-simular SFR=0%)

    Retorna dict con:
        tabla        : pd.DataFrame
        figura       : go.Figure (dual-eje)
        sfr_opt      : int
        sfr_dual     : int|None
        neto_opt     : float
        pct_opt      : float
        kwh_base     : float
        fc_lux       : list
        semaforo_txt : list
        semaforo_color: list
        recomendacion: str
        df_curva_raw : list[dict]
        error        : str|None
    """
    _L  = config.get("lang",  "ES")
    _U  = config.get("units", "metric")
    _FT = 3.28084

    def _cb(paso, total, msg):
        if callback: callback(paso, total, msg)

    ancho        = config["ancho"]
    largo        = config["largo"]
    altura       = config["altura"]
    tipo_uso     = config["tipo_uso"]
    epw_path     = config["epw_path"]
    sfr_curva_pct = config["sfr_curva"]
    domo_vlt     = config["domo_vlt"]
    domo_shgc    = config["domo_shgc"]
    domo_u       = config["domo_u"]
    domo_ancho_m = config["domo_ancho_m"]
    domo_largo_m = config["domo_largo_m"]
    cop_cooling  = config["cop_cooling"]
    eff_heating  = config["eff_heating"]
    carpeta_sims = config["carpeta_sims"]
    n_sims       = len(sfr_curva_pct)

    resultados_curva = []

    try:
        for i, sfr_pct in enumerate(sfr_curva_pct):
            sfr    = sfr_pct / 100.0
            sufijo = f"sfr_{sfr_pct:02d}pct" if sfr_pct > 0 else "base"
            _cb(i, n_sims, (f"Simulating SFR={sfr_pct}% ({i+1}/{n_sims})..." if _L=="EN" else f"Simulando SFR={sfr_pct}% ({i+1}/{n_sims})..."))

            # Reutilizar sql_base de Etapa 1 si existe
            if sfr_pct == 0 and sql_base_existente and os.path.exists(sql_base_existente):
                _cb(i, n_sims, ("SFR=0%: reusing baseline..." if _L=="EN" else "SFR=0%: reutilizando caso base ya simulado..."))
                sql_path = sql_base_existente
                sfr_real = 0.0
            else:
                hb_model, sfr_real, sim_params, sh_id, sc_id = construir_modelo(
                    ancho=ancho, largo=largo, altura=altura,
                    tipo_uso=tipo_uso, epw_path=epw_path, sfr=sfr,
                    domo_vlt=domo_vlt, domo_shgc=domo_shgc, domo_u=domo_u,
                    domo_ancho_m=domo_ancho_m, domo_largo_m=domo_largo_m,
                    sufijo=sufijo,
                )
                sql_path = traducir_y_simular(
                    hb_model, epw_path, sim_params,
                    carpeta_sims, f"caso_{sufijo}",
                    sched_calef_id=sh_id, sched_enfriam_id=sc_id,
                    ancho=ancho, largo=largo,
                )

            res = leer_kwh_sql(sql_path)
            kwh_luz  = res["kwh_iluminacion"]
            kwh_cool = res["kwh_cooling_demand"] / cop_cooling
            kwh_heat = res["kwh_heating_demand"] / eff_heating
            n_domos  = math.ceil(ancho * largo * sfr / (domo_ancho_m * domo_largo_m)) if sfr > 0 else 0

            resultados_curva.append({
                "sfr_pct": sfr_pct, "sfr_real_pct": round(sfr_real * 100, 2),
                "n_domos": n_domos, "kwh_luz": kwh_luz,
                "kwh_cooling": kwh_cool, "kwh_heating": kwh_heat,
                "kwh_total": kwh_luz + kwh_cool + kwh_heat,
                "sql_path": sql_path,
            })

    except Exception as e:
        return {"error": str(e), "tabla": None, "figura": None}

    _cb(n_sims, n_sims, "Calculando ahorros y semáforo...")

    # Split-Flux
    base           = next(r for r in resultados_curva if r["sfr_pct"] == 0)
    kwh_base_total = base["kwh_total"]
    sfr_vals       = [r["sfr_pct"]    for r in resultados_curva]
    ah_luz_vals    = [base["kwh_luz"]     - r["kwh_luz"]       for r in resultados_curva]
    pen_cool_vals  = [r["kwh_cooling"]    - base["kwh_cooling"] for r in resultados_curva]
    ah_heat_vals   = [base["kwh_heating"] - r["kwh_heating"]   for r in resultados_curva]
    neto_vals      = [ah_luz_vals[i] - pen_cool_vals[i] + ah_heat_vals[i] for i in range(len(sfr_vals))]

    idx_opt  = neto_vals.index(max(neto_vals))
    sfr_opt  = sfr_vals[idx_opt]
    neto_opt = neto_vals[idx_opt]
    pct_opt  = (neto_opt / kwh_base_total) * 100

    # Motor 2: iluminancia analítica EPW (para gráfica y tabla)
    luz = calcular_iluminancia_analitica(epw_path, tipo_uso, sfr_vals, domo_vlt)
    fc_lux, sem_txt, sem_color = luz["fc_lux"], luz["semaforo_txt"], luz["semaforo_color"]

    # v22.2: sfr_dual usa UDI-e ESPACIAL desde IlluminanceMap (IES LM-83)
    # Más preciso que el semáforo analítico — mismo método que el Colab v22
    udi_e_por_sfr = []
    for r in resultados_curva:
        sql = os.path.join(carpeta_sims, f"caso_{'sfr_%02dpct' % r['sfr_pct'] if r['sfr_pct'] > 0 else 'base'}", "eplusout.sql")
        if os.path.exists(sql):
            udi = extraer_udi_e(sql)
            udi_e_por_sfr.append(udi["udi_e_pct"])
        else:
            udi_e_por_sfr.append(None)

    sfr_dual = None
    for i, sfr_pct in enumerate(sfr_vals):
        udi = udi_e_por_sfr[i]
        if udi is not None and udi <= UDI_AREA_MAX:
            sfr_dual = sfr_pct

    lux_setpoint = PERFILES_ASHRAE.get(tipo_uso, PERFILES_ASHRAE["Warehouse"])["lux"]

    # DataFrame
    df_tabla = pd.DataFrame({
        "SFR %":            sfr_vals,
        "Domos":            [r["n_domos"]  for r in resultados_curva],
        ("Lighting sav. (kBtu)" if _L=="EN" else "Ah. Luz (kWh)"):    [round(v) for v in ah_luz_vals],
        ("Cool penalty (kBtu)" if _L=="EN" else "Pen. Cool (kWh)"):  [round(-v) for v in pen_cool_vals],
        ("Heat sav. (kBtu)" if _L=="EN" else "Ah. Heat (kWh)"):   [round(v) for v in ah_heat_vals],
        ("NET (kBtu)" if _L=="EN" else "NETO (kWh)"):       [round(v) for v in neto_vals],
        "% Base":           [round(n / kwh_base_total * 100, 1) for n in neto_vals],
        ("fc (fc)" if _L=="EN" else "fc (lux)"):         fc_lux,
        ("Compliance" if _L=="EN" else "Semáforo"):         sem_txt,
    })

    # Figura Plotly dual-eje
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Scatter(x=sfr_vals, y=ah_luz_vals, name=("Lighting savings" if _L=="EN" else "Ahorro Iluminación"),
        line=dict(color="#3498db", dash="dash", width=2),
        hovertemplate=("SFR %{x:.0f}%<br>Lighting: %{y:,.0f} kWh<extra></extra>" if _L=="EN" else "SFR %{x:.0f}%<br>Ahorro luz: %{y:,.0f} kWh<extra></extra>")), secondary_y=False)

    fig.add_trace(go.Scatter(x=sfr_vals, y=[-v for v in pen_cool_vals], name=("Cooling penalty" if _L=="EN" else "Penalización Cooling"),
        line=dict(color="#e74c3c", width=2),
        hovertemplate=("SFR %{x:.0f}%<br>Cooling penalty: %{y:,.0f} kWh<extra></extra>" if _L=="EN" else "SFR %{x:.0f}%<br>Pen. cooling: %{y:,.0f} kWh<extra></extra>")), secondary_y=False)

    if any(v != 0 for v in ah_heat_vals):
        fig.add_trace(go.Scatter(x=sfr_vals, y=ah_heat_vals, name=("Heating savings" if _L=="EN" else "Ahorro Heating"),
            line=dict(color="#f39c12", dash="dot", width=2)), secondary_y=False)

    fig.add_trace(go.Scatter(x=sfr_vals, y=neto_vals, name=("Net total savings" if _L=="EN" else "Ahorro Neto Total"),
        line=dict(color="#2ecc71", width=4),
        hovertemplate=("SFR %{x:.0f}%<br>Net savings: %{y:,.0f} kWh<extra></extra>" if _L=="EN" else "SFR %{x:.0f}%<br>Ahorro neto: %{y:,.0f} kWh<extra></extra>")), secondary_y=False)

    fig.add_trace(go.Scatter(x=[sfr_opt], y=[neto_opt],
        name=(f"Energy Optimal SFR={sfr_opt}%" if _L=="EN" else f"Óptimo Energético SFR={sfr_opt}%"), mode="markers",
        marker=dict(color="#2ecc71", size=14, symbol="star", line=dict(color="white", width=2)),
        hovertemplate=f"ÓPTIMO ENERGÍA: {sfr_opt}%<br>Ahorro: {neto_opt:,.0f} kWh/año<extra></extra>"), secondary_y=False)

    if sfr_dual is not None:
        idx_d = sfr_vals.index(sfr_dual)
        fig.add_trace(go.Scatter(x=[sfr_dual], y=[neto_vals[idx_d]],
            name=(f"Dual Optimal SFR={sfr_dual}%" if _L=="EN" else f"Óptimo Dual SFR={sfr_dual}%"), mode="markers",
            marker=dict(color="#f39c12", size=14, symbol="diamond", line=dict(color="white", width=2)),
            hovertemplate=f"ÓPTIMO DUAL: {sfr_dual}%<br>Ahorro: {neto_vals[idx_d]:,.0f} kWh<br>fc: {fc_lux[idx_d]:.0f} lux<br>{sem_txt[idx_d]}<extra></extra>"), secondary_y=False)

    fig.add_trace(go.Scatter(x=sfr_vals, y=fc_lux, name=("Average illuminance EPW" if _L=="EN" else "Iluminancia Promedio EPW (lux)"),
        line=dict(color="#9b59b6", dash="dot", width=2),
        hovertemplate=("SFR %{x:.0f}%<br>fc: %{y:.0f} fc<extra></extra>" if _L=="EN" else "SFR %{x:.0f}%<br>fc: %{y:.0f} lux<extra></extra>")), secondary_y=True)

    bar_h = max(fc_lux) * 0.08 if max(fc_lux) > 0 else 50
    fig.add_trace(go.Bar(x=sfr_vals, y=[bar_h] * len(sfr_vals), name=("Visual comfort" if _L=="EN" else "Confort visual"),
        marker_color=sem_color, opacity=0.45, showlegend=False,
        hovertemplate=[f"{s}% - {sem_txt[j]}" for j, s in enumerate(sfr_vals)]), secondary_y=True)

    for lux_ref, col, lbl in [
        (lux_setpoint,  "#27ae60", f"Setpoint ({lux_setpoint*CONVERSION['lux_to_fc']:.0f} fc)" if _L=="EN" else f"Setpoint ({lux_setpoint:.0f} lux)"),
        (UMBRAL_OPTIMO, "#f39c12", f"IES RP-7 limit ({UMBRAL_OPTIMO*CONVERSION['lux_to_fc']:.0f} fc)" if _L=="EN" else f"Límite IES RP-7 ({UMBRAL_OPTIMO:.0f} lux)"),
        (UMBRAL_LIMITE, "#e74c3c", f"UDI-Exceeded ({UMBRAL_LIMITE*CONVERSION['lux_to_fc']:.0f} fc)" if _L=="EN" else f"UDI-Exceeded ({UMBRAL_LIMITE:.0f} lux)"),
    ]:
        fig.add_hline(y=lux_ref, line_dash="dash", line_color=col, opacity=0.55,
                      annotation_text=lbl, annotation_position="right", secondary_y=True)

    fig.update_layout(
        title=dict(text=(f"SkyPlus — Optimal Design Curve | {tipo_uso} {ancho:.0f}×{largo:.0f}m" if _L=="EN" else f"SkyPlus — Curva de Diseño Óptimo | {tipo_uso} {ancho:.0f}×{largo:.0f}m"), font=dict(size=15)),
        xaxis=dict(title="SFR (%)", ticksuffix="%", dtick=1, gridcolor="#f0f0f0"),
        yaxis=dict(title=("Energy (kBtu/yr)" if _L=="EN" else "Energía (kWh/año)"), gridcolor="#f0f0f0"),
        hovermode="x unified", template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="right", x=1),
        height=560, margin=dict(b=60, r=130),
        annotations=[dict(
            x=sfr_opt, y=neto_opt,
            text=(f"Energy optimal: {sfr_opt}% | {neto_opt*CONVERSION['kwh_to_kbtu']:,.0f} kBtu/yr ({pct_opt:.1f}%)" if _L=="EN" else f"Óptimo energía: {sfr_opt}% | {neto_opt:,.0f} kWh/año ({pct_opt:.1f}%)"),
            showarrow=True, arrowhead=2, arrowcolor="#2ecc71",
            font=dict(size=11, color="#2ecc71"),
            bgcolor="white", bordercolor="#2ecc71", borderwidth=1, ay=-45,
        )],
    )
    fig.update_yaxes(title_text=("Average illuminance — daylight hours (fc)" if _L=="EN" else "Iluminancia Promedio horas sol útil (lux)"),
                     secondary_y=True, showgrid=False, rangemode="tozero")

    # Texto ejecutivo
    sfr_rec = sfr_dual if sfr_dual is not None else sfr_opt
    idx_rec = sfr_vals.index(sfr_rec)
    kwh_rec = neto_vals[idx_rec]
    lux_rec = fc_lux[idx_rec]
    pct_rec = kwh_rec / kwh_base_total * 100

    _nrg_rec = fmt_energy(kwh_rec, _U)
    _nrg_opt = fmt_energy(neto_opt, _U)
    _lux_rec = fmt_illuminance(lux_rec, _U, 0)
    if sfr_dual is not None and sfr_dual != sfr_opt:
        if _L == "EN":
            recomendacion = (
                f"**SkyPlus recommendation: SFR = {sfr_rec}% — Dual Optimal**\n\n"
                f"Guaranteed savings: **{_nrg_rec} ({pct_rec:.1f}%)**  \n"
                f"Visual comfort: **{_lux_rec}** avg workplane — ISO 8995-1 + IES RP-7 ✅\n\n"
                f"*SFR={sfr_opt}% maximizes savings ({_nrg_opt}, {pct_opt:.1f}%) "
                f"but may require spatial comfort verification. "
                f"Request a BEM study to validate point-by-point distribution.*"
            )
        else:
            recomendacion = (
                f"**Recomendación SkyPlus: SFR = {sfr_rec}% — Óptimo Dual**\n\n"
                f"Ahorro garantizado: **{_nrg_rec} ({pct_rec:.1f}%)**  \n"
                f"Confort visual: **{_lux_rec}** promedio zona — ISO 8995-1 + IES RP-7 ✅\n\n"
                f"*SFR={sfr_opt}% maximiza el ahorro ({_nrg_opt}, {pct_opt:.1f}%) "
                f"pero puede requerir verificación espacial de confort. "
                f"Solicita un estudio BEM para validar distribución punto por punto.*"
            )
    else:
        if _L == "EN":
            recomendacion = (
                f"**SkyPlus recommendation: SFR = {sfr_rec}% — Energy Optimal**\n\n"
                f"Projected savings: **{_nrg_rec} ({pct_rec:.1f}%)**  \n"
                f"Average workplane illuminance: **{_lux_rec}**\n\n"
                f"*Results generated by EnergyPlus 23.2 (DOE). "
                f"For LEED validation or certifications, request a detailed BEM study.*"
            )
        else:
            recomendacion = (
                f"**Recomendación SkyPlus: SFR = {sfr_rec}% — Óptimo Energético**\n\n"
                f"Ahorro proyectado: **{_nrg_rec} ({pct_rec:.1f}%)**  \n"
                f"Iluminancia promedio zona: **{_lux_rec}**\n\n"
                f"*Resultados generados por EnergyPlus 23.2 (DOE). "
                f"Para validación LEED o certificaciones, solicita un estudio BEM detallado.*"
            )

    _cb(n_sims, n_sims, ("Simulation complete!" if _L=="EN" else "¡Simulación completa!"))

    return {
        "tabla":            df_tabla,
        "figura":           fig,
        "sfr_opt":          sfr_opt,
        "sfr_dual":         sfr_dual,
        "neto_opt":         neto_opt,
        "pct_opt":          pct_opt,
        "kwh_base":         kwh_base_total,
        "fc_lux":           fc_lux,
        "semaforo_txt":     sem_txt,
        "semaforo_color":   sem_color,
        "recomendacion":    recomendacion,
        "df_curva_raw":     resultados_curva,
        "error":            None,
    }
