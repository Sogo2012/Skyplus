# motor/termico.py
# =============================================================================
# SKYPLUS — Motor Térmico v22
# Eco Consultor | Sunoptics LATAM
#
# Refactorización del notebook Colab (5 celdas) a módulo Python importable.
# Mantiene 100% de la lógica matemática y física del motor original.
#
# API pública:
#   configurar_proyecto(...) → dict de parámetros validados
#   calcular_curva_sfr(...)  → dict con tabla, figura Plotly y métricas
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
    Retorna (hb_model, sfr_real, sim_params).
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
    return hb_model, sfr_real, sim_params


# =============================================================================
# SECCIÓN 3 — TRADUCCIÓN A IDF Y EJECUCIÓN DE ENERGYPLUS
# =============================================================================

def _parchear_hvactemplate(idf_path_orig, sched_calef_id, sched_enfriam_id):
    """Reemplaza HVACTemplate por objetos nativos. Agrega grid ASHRAE de sensores."""
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
        zonas.append({"zona": c[1] if len(c)>1 else "Zona", "tstat": c[2] if len(c)>2 and c[2] else list(termostatos.keys())[0]})

    idf_limpio = texto
    for tipo in ["HVACTemplate:Thermostat", "HVACTemplate:Zone:IdealLoadsAirSystem"]:
        for b in _objs(idf_limpio, tipo):
            idf_limpio = idf_limpio.replace(b, "")
    for b in _objs(idf_limpio, "Output:Variable"):
        if "Zone Ideal Loads Cooling Energy" in b or "Zone Ideal Loads Heating Energy" in b:
            idf_limpio = idf_limpio.replace(b, "")
    idf_limpio = re.sub(r"Output:IlluminanceMap,.*?;\s*\n", "", idf_limpio, flags=re.DOTALL)

    nativos = [
        "",
        "!- === objetos nativos ===",
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
        nativos += [
            f"ThermostatSetpoint:DualSetpoint,","  DualSP_{tag},",f"  {ts['heat']},",f"  {ts['cool']};","",
            f"ZoneControl:Thermostat,","  ZCtrl_{tag},",f"  {zona},","  Sched_DualSetpoint,","  ThermostatSetpoint:DualSetpoint,",f"  DualSP_{tag};","",
            f"ZoneHVAC:IdealLoadsAirSystem,","  IdealAir_{tag},","  ,",f"  Supply_{tag},",f"  Exhaust_{tag};","",
            f"ZoneHVAC:EquipmentList,","  EqList_{tag},","  SequentialLoad,","  ZoneHVAC:IdealLoadsAirSystem,",f"  IdealAir_{tag},","  1,","  1;","",
            f"ZoneHVAC:EquipmentConnections,",f"  {zona},",f"  EqList_{tag},",f"  Supply_{tag},",f"  Exhaust_{tag},",f"  ZAir_{tag},",f"  Return_{tag};","",
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

        nx = max(1, int(50 / 7.14))
        ny = max(1, int(100 / 7.14))
        fraccion = 1.0 / (nx * ny)
        dx_g, dy_g = 50/nx, 100/ny

        rp_names, rp_blocks = [], []
        for ix in range(nx):
            for iy in range(ny):
                cx = (ix*dx_g) + dx_g/2
                cy = (iy*dy_g) + dy_g/2
                rp_name = f"ASHRAE_RP_{len(rp_names):03d}"
                rp_names.append(rp_name)
                rp_blocks.append(f"Daylighting:ReferencePoint,\n  {rp_name},\n  {nombre_zona_dl},\n  {cx:.4f},\n  {cy:.4f},\n  0.8;\n")

        lineas_ctrl = [
            "Daylighting:Controls,","  NAVE_ASHRAE_DAYLIGHTING,",f"  {nombre_zona_dl},",
            "  SplitFlux,","  ,","  Continuous,","  0.0,","  0.0,","  1,","  1.0,","  ,","  180.0,","  22.0,","  ,",
        ]
        for i, rp_name in enumerate(rp_names):
            es_ultimo = (i == len(rp_names)-1)
            lineas_ctrl += [f"  {rp_name},", f"  {fraccion:.6f},", f"  {setpoint_ajustado:.1f}{',' if not es_ultimo else ';'}"]

        nativos += rp_blocks + lineas_ctrl + [""]

    idf_patched = idf_path_orig.replace(".idf", "_patched.idf")
    with open(idf_patched, "w") as f:
        f.write(idf_limpio)
        f.write("\n".join(nativos))
        f.write("\n")
    return idf_patched


def traducir_y_simular(hb_model, epw_path, sim_params, carpeta, nombre,
                       sched_calef_id="Sched_Heat", sched_enfriam_id="Sched_Cool"):
    """Traduce HBModel → IDF, parchea HVAC, ejecuta EnergyPlus. Retorna ruta del .sql."""
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

    idf_path = _parchear_hvactemplate(idf_path, sched_calef_id, sched_enfriam_id)

    ep_exec = _detectar_energyplus()
    subprocess.run([ep_exec, "-w", epw_path, "-d", carpeta_caso, idf_path],
                   capture_output=True, text=True, timeout=600)

    err_texto, hay_fatal = _leer_err(carpeta_caso)
    sql_path = os.path.join(carpeta_caso, "eplusout.sql")
    if not os.path.exists(sql_path):
        for root, _, files in os.walk(carpeta_caso):
            for fn in files:
                if fn == "eplusout.sql":
                    sql_path = os.path.join(root, fn)

    if not os.path.exists(sql_path):
        raise RuntimeError(f"EnergyPlus falló para '{nombre}'.\n{err_texto[-600:]}")

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
        res  = pd.read_sql_query(f"SELECT SUM(Value) as total FROM ReportData WHERE ReportDataDictionaryIndex IN ({idxs})", conn)
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
    horas_luz_vals = [illum_8760[i] for i in range(8760) if i%24 in horas_occ_idx and illum_8760[i] > 0]

    umbral_p25 = float(np.percentile(horas_luz_vals, 25)) if horas_luz_vals else 10000.0
    cu = 0.736  # Validado: WE=0.777, GF=0.85, LightWell Sunoptics 800MD

    fc_lux, sem_txt, sem_color = [], [], []
    for sfr_pct in sfr_vals:
        sfr      = sfr_pct / 100.0
        transmis = sfr * domo_vlt * cu
        vals = [illum_8760[i] * transmis for i in range(8760) if i%24 in horas_occ_idx and illum_8760[i] >= umbral_p25]
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
                        sfr_curva=None,
                        domo_vlt=DOMO_VLT_DEFAULT, domo_shgc=DOMO_SHGC_DEFAULT,
                        domo_u=DOMO_U_DEFAULT, domo_ancho_m=DOMO_ANCHO_M,
                        domo_largo_m=DOMO_LARGO_M,
                        cop_cooling=COP_COOLING_DEFAULT, eff_heating=EFF_HEATING_DEFAULT,
                        carpeta_sims=None):
    """
    Valida y empaqueta parámetros del proyecto.
    Retorna dict de configuración para pasar a calcular_curva_sfr().

    Uso en app.py:
        config = configurar_proyecto(
            ancho=ancho_nave, largo=largo_nave, altura=alto_nave,
            tipo_uso=tipo_uso, epw_path=st.session_state.epw_path
        )
        resultado = calcular_curva_sfr(config, callback=mi_progress_bar)
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
        "tipo_uso": tipo_uso, "epw_path": epw_path, "sfr_curva": sfr_curva,
        "domo_vlt": domo_vlt, "domo_shgc": domo_shgc, "domo_u": domo_u,
        "domo_ancho_m": domo_ancho_m, "domo_largo_m": domo_largo_m,
        "cop_cooling": cop_cooling, "eff_heating": eff_heating,
        "carpeta_sims": carpeta_sims,
        "zona_climatica": _detectar_zona_climatica(epw_path),
    }


def calcular_curva_sfr(config, callback=None):
    """
    Función principal pública. Orquesta el motor SkyPlus v22 completo.

    Args:
        config   : dict de configurar_proyecto()
        callback : fn(paso_actual, total, mensaje) para barra de progreso Streamlit

    Retorna dict con:
        tabla        : pd.DataFrame  — todos los SFRs con métricas
        figura       : go.Figure     — gráfica Plotly dual-eje (st.plotly_chart)
        sfr_opt      : int           — SFR óptimo energético (%)
        sfr_dual     : int|None      — SFR óptimo dual energía+confort (%)
        neto_opt     : float         — kWh/año ahorrados en sfr_opt
        pct_opt      : float         — % de ahorro sobre caso base
        kwh_base     : float         — kWh/año consumo total caso base
        fc_lux       : list          — iluminancia analítica por SFR
        semaforo_txt : list          — texto semáforo por SFR
        semaforo_color: list         — color hex semáforo por SFR
        recomendacion: str           — texto ejecutivo para reporte PDF
        df_curva_raw : list[dict]    — datos crudos para exportar a CSV/PDF
        error        : str|None      — mensaje de error si algo falló
    """
    def _cb(paso, total, msg):
        if callback: callback(paso, total, msg)

    ancho         = config["ancho"]
    largo         = config["largo"]
    altura        = config["altura"]
    tipo_uso      = config["tipo_uso"]
    epw_path      = config["epw_path"]
    sfr_curva_pct = config["sfr_curva"]
    domo_vlt      = config["domo_vlt"]
    domo_shgc     = config["domo_shgc"]
    domo_u        = config["domo_u"]
    domo_ancho_m  = config["domo_ancho_m"]
    domo_largo_m  = config["domo_largo_m"]
    cop_cooling   = config["cop_cooling"]
    eff_heating   = config["eff_heating"]
    carpeta_sims  = config["carpeta_sims"]
    n_sims        = len(sfr_curva_pct)

    resultados_curva = []

    try:
        for i, sfr_pct in enumerate(sfr_curva_pct):
            sfr    = sfr_pct / 100.0
            sufijo = f"sfr_{sfr_pct:02d}pct" if sfr_pct > 0 else "base"
            _cb(i, n_sims, f"Simulando SFR={sfr_pct}% ({i+1}/{n_sims})...")

            hb_model, sfr_real, sim_params = construir_modelo(
                ancho=ancho, largo=largo, altura=altura,
                tipo_uso=tipo_uso, epw_path=epw_path, sfr=sfr,
                domo_vlt=domo_vlt, domo_shgc=domo_shgc, domo_u=domo_u,
                domo_ancho_m=domo_ancho_m, domo_largo_m=domo_largo_m,
                sufijo=sufijo,
            )

            sql_path = traducir_y_simular(
                hb_model, epw_path, sim_params,
                carpeta_sims, f"caso_{sufijo}",
            )

            res = leer_kwh_sql(sql_path)
            kwh_luz  = res["kwh_iluminacion"]
            kwh_cool = res["kwh_cooling_demand"] / cop_cooling
            kwh_heat = res["kwh_heating_demand"] / eff_heating
            n_domos  = math.ceil(ancho * largo * sfr / (domo_ancho_m * domo_largo_m)) if sfr > 0 else 0

            resultados_curva.append({
                "sfr_pct": sfr_pct, "sfr_real_pct": round(sfr_real*100, 2),
                "n_domos": n_domos, "kwh_luz": kwh_luz,
                "kwh_cooling": kwh_cool, "kwh_heating": kwh_heat,
                "kwh_total": kwh_luz + kwh_cool + kwh_heat,
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

    # Motor 2: iluminancia analítica EPW
    luz = calcular_iluminancia_analitica(epw_path, tipo_uso, sfr_vals, domo_vlt)
    fc_lux, sem_txt, sem_color = luz["fc_lux"], luz["semaforo_txt"], luz["semaforo_color"]

    sfr_dual = None
    for i, sfr_pct in enumerate(sfr_vals):
        if sem_color[i] in (COLORES_SEM["VERDE"], COLORES_SEM["AZUL"]):
            sfr_dual = sfr_pct

    lux_setpoint = PERFILES_ASHRAE.get(tipo_uso, PERFILES_ASHRAE["Warehouse"])["lux"]

    # DataFrame
    df_tabla = pd.DataFrame({
        "SFR %":            sfr_vals,
        "Domos":            [r["n_domos"]  for r in resultados_curva],
        "Ah. Luz (kWh)":    [round(v) for v in ah_luz_vals],
        "Pen. Cool (kWh)":  [round(-v) for v in pen_cool_vals],
        "Ah. Heat (kWh)":   [round(v) for v in ah_heat_vals],
        "NETO (kWh)":       [round(v) for v in neto_vals],
        "% Base":           [round(n/kwh_base_total*100, 1) for n in neto_vals],
        "fc (lux)":         fc_lux,
        "Semáforo":         sem_txt,
    })

    # Figura Plotly dual-eje
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Scatter(x=sfr_vals, y=ah_luz_vals, name="Ahorro Iluminación",
        line=dict(color="#3498db", dash="dash", width=2),
        hovertemplate="SFR %{x:.0f}%<br>Ahorro luz: %{y:,.0f} kWh<extra></extra>"), secondary_y=False)

    fig.add_trace(go.Scatter(x=sfr_vals, y=[-v for v in pen_cool_vals], name="Penalización Cooling",
        line=dict(color="#e74c3c", width=2),
        hovertemplate="SFR %{x:.0f}%<br>Pen. cooling: %{y:,.0f} kWh<extra></extra>"), secondary_y=False)

    if any(v != 0 for v in ah_heat_vals):
        fig.add_trace(go.Scatter(x=sfr_vals, y=ah_heat_vals, name="Ahorro Heating",
            line=dict(color="#f39c12", dash="dot", width=2)), secondary_y=False)

    fig.add_trace(go.Scatter(x=sfr_vals, y=neto_vals, name="Ahorro Neto Total",
        line=dict(color="#2ecc71", width=4),
        hovertemplate="SFR %{x:.0f}%<br>Ahorro neto: %{y:,.0f} kWh<extra></extra>"), secondary_y=False)

    fig.add_trace(go.Scatter(x=[sfr_opt], y=[neto_opt],
        name=f"Óptimo Energético SFR={sfr_opt}%", mode="markers",
        marker=dict(color="#2ecc71", size=14, symbol="star", line=dict(color="white", width=2)),
        hovertemplate=f"ÓPTIMO ENERGÍA: {sfr_opt}%<br>Ahorro: {neto_opt:,.0f} kWh/año<extra></extra>"), secondary_y=False)

    if sfr_dual is not None:
        idx_d = sfr_vals.index(sfr_dual)
        fig.add_trace(go.Scatter(x=[sfr_dual], y=[neto_vals[idx_d]],
            name=f"Óptimo Dual SFR={sfr_dual}%", mode="markers",
            marker=dict(color="#f39c12", size=14, symbol="diamond", line=dict(color="white", width=2)),
            hovertemplate=f"ÓPTIMO DUAL: {sfr_dual}%<br>Ahorro: {neto_vals[idx_d]:,.0f} kWh<br>fc: {fc_lux[idx_d]:.0f} lux<br>{sem_txt[idx_d]}<extra></extra>"), secondary_y=False)

    fig.add_trace(go.Scatter(x=sfr_vals, y=fc_lux, name="Iluminancia Promedio EPW (lux)",
        line=dict(color="#9b59b6", dash="dot", width=2),
        hovertemplate="SFR %{x:.0f}%<br>fc: %{y:.0f} lux<extra></extra>"), secondary_y=True)

    bar_h = max(fc_lux) * 0.08 if max(fc_lux) > 0 else 50
    fig.add_trace(go.Bar(x=sfr_vals, y=[bar_h]*len(sfr_vals), name="Confort visual",
        marker_color=sem_color, opacity=0.45, showlegend=False,
        hovertemplate=[f"{s}% - {sem_txt[j]}" for j, s in enumerate(sfr_vals)]), secondary_y=True)

    for lux_ref, col, lbl in [
        (lux_setpoint,  "#27ae60", f"Setpoint ({lux_setpoint:.0f} lux)"),
        (UMBRAL_OPTIMO, "#f39c12", f"Límite IES RP-7 ({UMBRAL_OPTIMO:.0f} lux)"),
        (UMBRAL_LIMITE, "#e74c3c", f"UDI-Exceeded ({UMBRAL_LIMITE:.0f} lux)"),
    ]:
        fig.add_hline(y=lux_ref, line_dash="dash", line_color=col, opacity=0.55,
                      annotation_text=lbl, annotation_position="right", secondary_y=True)

    fig.update_layout(
        title=dict(text=f"SkyPlus — Curva de Diseño Óptimo | {tipo_uso} {ancho:.0f}×{largo:.0f}m", font=dict(size=15)),
        xaxis=dict(title="SFR (%)", ticksuffix="%", dtick=1, gridcolor="#f0f0f0"),
        yaxis=dict(title="Energía (kWh/año)", gridcolor="#f0f0f0"),
        hovermode="x unified", template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="right", x=1),
        height=560, margin=dict(b=60, r=130),
        annotations=[dict(
            x=sfr_opt, y=neto_opt,
            text=f"Óptimo energía: {sfr_opt}% | {neto_opt:,.0f} kWh/año ({pct_opt:.1f}%)",
            showarrow=True, arrowhead=2, arrowcolor="#2ecc71",
            font=dict(size=11, color="#2ecc71"),
            bgcolor="white", bordercolor="#2ecc71", borderwidth=1, ay=-45,
        )],
    )
    fig.update_yaxes(title_text="Iluminancia Promedio horas sol útil (lux)",
                     secondary_y=True, showgrid=False, rangemode="tozero")

    # Texto ejecutivo de recomendación
    sfr_rec = sfr_dual if sfr_dual is not None else sfr_opt
    idx_rec = sfr_vals.index(sfr_rec)
    kwh_rec = neto_vals[idx_rec]
    lux_rec = fc_lux[idx_rec]
    pct_rec = kwh_rec / kwh_base_total * 100

    if sfr_dual is not None and sfr_dual != sfr_opt:
        recomendacion = (
            f"**Recomendación SkyPlus: SFR = {sfr_rec}% — Óptimo Dual**\n\n"
            f"Ahorro garantizado: **{kwh_rec:,.0f} kWh/año ({pct_rec:.1f}%)**  \n"
            f"Confort visual: **{lux_rec:.0f} lux** promedio zona — ISO 8995-1 + IES RP-7 ✅\n\n"
            f"*SFR={sfr_opt}% maximiza el ahorro ({neto_opt:,.0f} kWh/año, {pct_opt:.1f}%) "
            f"pero puede requerir verificación espacial de confort. "
            f"Solicita un estudio BEM para validar distribución punto por punto.*"
        )
    else:
        recomendacion = (
            f"**Recomendación SkyPlus: SFR = {sfr_rec}% — Óptimo Energético**\n\n"
            f"Ahorro proyectado: **{kwh_rec:,.0f} kWh/año ({pct_rec:.1f}%)**  \n"
            f"Iluminancia promedio zona: **{lux_rec:.0f} lux**\n\n"
            f"*Resultados generados por EnergyPlus 23.2 (DOE). "
            f"Para validación LEED o certificaciones, solicita un estudio BEM detallado.*"
        )

    _cb(n_sims, n_sims, "¡Simulación completa!")

    return {
        "tabla":           df_tabla,
        "figura":          fig,
        "sfr_opt":         sfr_opt,
        "sfr_dual":        sfr_dual,
        "neto_opt":        neto_opt,
        "pct_opt":         pct_opt,
        "kwh_base":        kwh_base_total,
        "fc_lux":          fc_lux,
        "semaforo_txt":    sem_txt,
        "semaforo_color":  sem_color,
        "recomendacion":   recomendacion,
        "df_curva_raw":    resultados_curva,
        "error":           None,
    }
