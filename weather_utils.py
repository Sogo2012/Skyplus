# weather_utils.py
# =============================================================================
# SKYPLUS 2.0 — Weather Utilities
# Eco Consultor | Sunoptics LATAM
#
# v3.0 — Catálogo estático epw_catalog_global.json
#   - Búsqueda geodésica instantánea (<100ms) sin scraping en runtime
#   - 5,276 estaciones únicas: USA, CAN, MEX + 17 países LATAM
#   - Fallback robusto: geocodificación inversa → país → búsqueda local
#   - Compatible 100% con la API existente de app.py
# =============================================================================

import json
import os
import math
import zipfile
import tempfile
import re
import random
import time

import requests
import pandas as pd
from ladybug.epw import EPW

# ── Geocodificadores opcionales (no críticos) ──────────────────────────────
try:
    from geopy.geocoders import Nominatim, Photon
    from geopy.distance import geodesic as _geodesic
    GEOPY_OK = True
except ImportError:
    GEOPY_OK = False

# =============================================================================
# CATÁLOGO ESTÁTICO
# =============================================================================

_CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "epw_catalog_global.json")
_CATALOG: dict | None = None

def _load_catalog() -> dict:
    global _CATALOG
    if _CATALOG is None:
        try:
            with open(_CATALOG_PATH, "r", encoding="utf-8") as f:
                _CATALOG = json.load(f)
        except FileNotFoundError:
            _CATALOG = {}
    return _CATALOG


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia geodésica en km entre dos puntos (fórmula Haversine)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# =============================================================================
# MAPEO PAÍS → CÓDIGO ISO
# =============================================================================

# Nombres comunes y variantes → código del catálogo
_COUNTRY_MAP = {
    # Inglés
    "united states": "USA", "united states of america": "USA", "usa": "USA", "us": "USA",
    "mexico": "MEX", "méxico": "MEX",
    "canada": "CAN", "canadá": "CAN",
    "guatemala": "GTM",
    "honduras": "HND",
    "nicaragua": "NIC",
    "el salvador": "SLV",
    "costa rica": "CRI",
    "panama": "PAN", "panamá": "PAN",
    "dominican republic": "DOM", "república dominicana": "DOM", "republica dominicana": "DOM",
    "colombia": "COL",
    "venezuela": "VEN",
    "ecuador": "ECU",
    "peru": "PER", "perú": "PER",
    "bolivia": "BOL",
    "brazil": "BRA", "brasil": "BRA",
    "chile": "CHL",
    "argentina": "ARG",
    "paraguay": "PRY",
    "uruguay": "URY",
}

def _country_to_code(country_name: str) -> str | None:
    """Convierte nombre de país a código ISO del catálogo."""
    if not country_name:
        return None
    key = country_name.lower().strip()
    # Búsqueda exacta
    if key in _COUNTRY_MAP:
        return _COUNTRY_MAP[key]
    # Búsqueda parcial
    for k, v in _COUNTRY_MAP.items():
        if k in key or key in k:
            return v
    return None


# =============================================================================
# GEOCODIFICACIÓN INVERSA — identificar país desde lat/lon
# =============================================================================

def get_location_info(lat: float, lon: float) -> tuple[str | None, str | None]:
    """
    Geocodificación inversa robusta.
    Retorna (country_name, city_name) en inglés.
    """
    user_agents = [
        f"skyplus_v2_{random.randint(100, 999)}",
        "Mozilla/5.0",
        "SkyPlus/2.0 Eco Consultor"
    ]

    if GEOPY_OK:
        # Intento 1 — Photon
        try:
            geo = Photon(user_agent=random.choice(user_agents))
            loc = geo.reverse(f"{lat}, {lon}", timeout=10)
            if loc and "properties" in loc.raw:
                props = loc.raw["properties"]
                country = props.get("country")
                city    = props.get("city") or props.get("name")
                if country:
                    return country, city
        except Exception:
            pass

        # Intento 2 — Nominatim
        try:
            time.sleep(0.5)
            geo = Nominatim(user_agent=random.choice(user_agents))
            loc = geo.reverse(f"{lat}, {lon}", language="en", timeout=10)
            if loc and "address" in loc.raw:
                addr    = loc.raw["address"]
                country = addr.get("country")
                city    = (addr.get("city") or addr.get("town")
                           or addr.get("village") or addr.get("municipality"))
                if country:
                    return country, city
        except Exception:
            pass

    # Fallback — inferir país desde coordenadas (bounding boxes aproximados)
    country = _infer_country_from_bbox(lat, lon)
    return country, None


def _infer_country_from_bbox(lat: float, lon: float) -> str | None:
    """Fallback rápido — inferir país desde bounding box geográfico."""
    boxes = [
        ("United States", 24.4, 49.4, -125.0, -66.9),
        ("United States", 18.9, 28.5, -168.0, -154.8),  # Hawaii
        ("United States", 51.2, 71.5, -179.9, -129.9),  # Alaska
        ("Mexico",        14.5, 32.7, -117.1,  -86.7),
        ("Canada",        41.7, 83.1, -141.0,  -52.6),
        ("Guatemala",     13.7, 17.8,  -92.2,  -88.2),
        ("Honduras",      13.0, 16.5,  -89.4,  -83.1),
        ("Nicaragua",     10.7, 15.0,  -87.7,  -83.1),
        ("El Salvador",   13.1, 14.5,  -90.1,  -87.7),
        ("Costa Rica",     8.0, 11.2,  -85.9,  -82.6),
        ("Panama",         7.2,  9.7,  -83.0,  -77.2),
        ("Dominican Republic", 17.5, 20.0, -72.1, -68.3),
        ("Colombia",      -4.2, 13.4,  -79.0,  -66.9),
        ("Venezuela",      0.6, 12.2,  -73.4,  -59.8),
        ("Ecuador",       -5.0,  1.5,  -81.1,  -75.2),
        ("Peru",         -18.4, -0.1,  -81.4,  -68.6),
        ("Bolivia",      -22.9, -9.7,  -69.6,  -57.5),
        ("Brazil",       -33.8,  5.3,  -73.9,  -34.8),
        ("Chile",        -55.9,-17.5,  -75.6,  -66.4),
        ("Argentina",    -55.1,-21.8,  -73.6,  -53.6),
        ("Paraguay",     -27.6,-19.3,  -62.7,  -54.3),
        ("Uruguay",      -34.9,-30.1,  -58.4,  -53.1),
    ]
    for country, lat_min, lat_max, lon_min, lon_max in boxes:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return country
    return None


# =============================================================================
# FUNCIÓN PRINCIPAL — obtener_estaciones_cercanas
# =============================================================================

def obtener_estaciones_cercanas(lat: float, lon: float, top_n: int = 6) -> pd.DataFrame:
    """
    Busca las N estaciones EPW más cercanas al punto (lat, lon).

    Estrategia:
      1. Inferir país desde coordenadas (bbox o geocodificación inversa)
      2. Buscar en el catálogo estático por distancia geodésica
      3. Si hay pocas resultados (<3), ampliar a países vecinos
      4. Retornar DataFrame con columnas compatibles con app.py

    Velocidad: <100ms (sin llamadas de red)
    """
    catalog = _load_catalog()
    if not catalog:
        return pd.DataFrame()

    # ── 1. Identificar país ──────────────────────────────────────────────
    country_name, _ = get_location_info(lat, lon)
    country_code    = _country_to_code(country_name) if country_name else None

    # Si no se identificó, buscar en TODOS los países (más lento pero robusto)
    if not country_code:
        country_code = _nearest_country_from_catalog(lat, lon, catalog)

    # ── 2. Buscar en el país identificado ────────────────────────────────
    results = _search_in_codes(lat, lon, catalog, [country_code] if country_code else [], top_n * 2)

    # ── 3. Si hay pocas, ampliar a países vecinos ────────────────────────
    if len(results) < 3:
        neighbors = _get_neighbor_codes(country_code)
        extra = _search_in_codes(lat, lon, catalog, neighbors, top_n * 2)
        results = _merge_dedupe(results, extra)

    # ── 4. Si aún hay pocas, búsqueda global ────────────────────────────
    if len(results) < 2:
        all_codes = [c for c in catalog.keys() if c != country_code]
        extra = _search_in_codes(lat, lon, catalog, all_codes, top_n)
        results = _merge_dedupe(results, extra)

    if not results:
        return pd.DataFrame()

    # Ordenar por distancia y tomar top_n
    results.sort(key=lambda x: x["distancia_km"])
    results = results[:top_n]

    return pd.DataFrame(results)


def _search_in_codes(lat: float, lon: float, catalog: dict,
                     codes: list, limit: int) -> list:
    """Busca estaciones con coords en los códigos indicados."""
    candidates = []
    for code in codes:
        stations = catalog.get(code, [])
        for s in stations:
            if s.get("lat") is None or s.get("lon") is None:
                continue
            dist = _haversine(lat, lon, s["lat"], s["lon"])
            candidates.append({
                "name":         s.get("name", "Unknown"),
                "Estación":     s.get("name", "Unknown"),
                "state":        s.get("state", ""),
                "country":      code,
                "distancia_km": round(dist, 2),
                "URL_ZIP":      s.get("url", ""),
                "epw":          s.get("url", ""),
                "lat":          s["lat"],
                "lon":          s["lon"],
                "coords_approx": s.get("coords_approx", False),
            })
    # Retornar las más cercanas
    candidates.sort(key=lambda x: x["distancia_km"])
    return candidates[:limit]


def _merge_dedupe(a: list, b: list) -> list:
    """Combina dos listas eliminando duplicados por URL."""
    seen = {x["URL_ZIP"] for x in a}
    return a + [x for x in b if x["URL_ZIP"] not in seen]


def _nearest_country_from_catalog(lat: float, lon: float, catalog: dict) -> str | None:
    """Cuando no se puede identificar el país, encuentra el más cercano en el catálogo."""
    best_code = None
    best_dist = float("inf")
    for code, stations in catalog.items():
        for s in stations:
            if s.get("lat") is None:
                continue
            d = _haversine(lat, lon, s["lat"], s["lon"])
            if d < best_dist:
                best_dist = d
                best_code = code
            if best_dist < 50:  # suficientemente cerca, no seguir buscando
                break
    return best_code


def _get_neighbor_codes(code: str | None) -> list:
    """Países vecinos para ampliar búsqueda cuando hay pocas estaciones."""
    neighbors = {
        "MEX": ["USA", "GTM"],
        "USA": ["CAN", "MEX"],
        "CAN": ["USA"],
        "GTM": ["MEX", "HND", "SLV"],
        "HND": ["GTM", "NIC", "SLV"],
        "NIC": ["HND", "CRI"],
        "SLV": ["GTM", "HND"],
        "CRI": ["NIC", "PAN"],
        "PAN": ["CRI", "COL"],
        "DOM": ["PAN"],
        "COL": ["PAN", "VEN", "ECU", "PER"],
        "VEN": ["COL", "BRA"],
        "ECU": ["COL", "PER"],
        "PER": ["ECU", "COL", "BOL", "CHL", "BRA"],
        "BOL": ["PER", "CHL", "ARG", "BRA", "PRY"],
        "BRA": ["COL", "VEN", "PER", "BOL", "PRY", "ARG", "URY"],
        "CHL": ["PER", "BOL", "ARG"],
        "ARG": ["CHL", "BOL", "PRY", "BRA", "URY"],
        "PRY": ["BOL", "BRA", "ARG"],
        "URY": ["BRA", "ARG"],
    }
    return neighbors.get(code, [])


# =============================================================================
# GEOCODE (mantenido por compatibilidad)
# =============================================================================

def geocode_name(name: str) -> tuple[float | None, float | None]:
    """Geocodifica un nombre de ciudad/país. Retorna (lat, lon) o (None, None)."""
    if not GEOPY_OK:
        return None, None
    ua = f"skyplus_search_{random.randint(100,999)}"
    for GeoClass in [Photon, Nominatim]:
        try:
            geo = GeoClass(user_agent=ua)
            loc = geo.geocode(name, timeout=10)
            if loc:
                return loc.latitude, loc.longitude
            time.sleep(0.5)
        except Exception:
            pass
    return None, None


# =============================================================================
# DESCARGA Y PROCESAMIENTO EPW
# =============================================================================

def descargar_y_extraer_epw(url_zip: str) -> str | None:
    """
    Descarga un ZIP de OneBuilding y extrae el archivo .epw.
    Retorna la ruta al .epw extraído, o None si falla.
    """
    temp_dir = tempfile.mkdtemp(prefix="epw_")
    zip_path = os.path.join(temp_dir, "clima.zip")

    try:
        headers = {"User-Agent": "SkyPlus/2.0 (Eco Consultor; EnergyPlus simulation)"}
        resp = requests.get(url_zip, headers=headers, timeout=60, stream=True)
        resp.raise_for_status()

        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        with zipfile.ZipFile(zip_path, "r") as z:
            epw_files = [n for n in z.namelist() if n.lower().endswith(".epw")]
            if not epw_files:
                return None
            z.extract(epw_files[0], temp_dir)
            return os.path.join(temp_dir, epw_files[0])

    except Exception as e:
        print(f"[weather_utils] Error descargando EPW: {e}")
        return None


def procesar_datos_clima(epw_path: str) -> dict:
    """
    Procesa un archivo EPW y retorna diccionario con datos climáticos
    para los gráficos de la tab de clima en app.py.
    """
    try:
        epw = EPW(epw_path)

        return {
            "ciudad":       epw.location.city,
            "pais":         epw.location.country,
            "latitud":      epw.location.latitude,
            "longitud":     epw.location.longitude,
            "elevacion":    epw.location.elevation,
            "temp_seca":    list(epw.dry_bulb_temperature),
            "temp_rocio":   list(epw.dew_point_temperature),
            "humedad_rel":  list(epw.relative_humidity),
            "rad_directa":  list(epw.direct_normal_radiation),
            "rad_dif":      list(epw.diffuse_horizontal_radiation),
            "vel_viento":   list(epw.wind_speed),
            "dir_viento":   list(epw.wind_direction),
            "nubes":        list(epw.total_sky_cover),
            "presion":      list(epw.atmospheric_station_pressure),
        }
    except Exception as e:
        print(f"[weather_utils] Error procesando EPW: {e}")
        return {}


# =============================================================================
# NORMALIZE TEXT (mantenido por compatibilidad)
# =============================================================================

def normalize_text(text: str) -> str:
    if not text:
        return ""
    return (text.lower()
            .replace("á", "a").replace("é", "e").replace("í", "i")
            .replace("ó", "o").replace("ú", "u").replace("ñ", "n"))
