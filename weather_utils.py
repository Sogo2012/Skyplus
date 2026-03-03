
import json
import requests
import os
import zipfile
import urllib.request
from geopy.distance import geodesic
from geopy.geocoders import Nominatim, Photon
import pandas as pd
from ladybug.epw import EPW
import shutil
import tempfile
from bs4 import BeautifulSoup
import urllib.parse
import re
import random
import time

# Load mapping of countries to OneBuilding URLs
try:
    with open("onebuilding_mapping.json", "r") as f:
        ONEBUILDING_MAPPING = json.load(f)
except FileNotFoundError:
    ONEBUILDING_MAPPING = {}

def get_location_info(lat, lon):
    """Robust reverse geocoding to identify country and city."""
    user_agents = [f"skycalc_explorer_{random.randint(100, 999)}", "Mozilla/5.0", "SkyCalc/2.0"]

    # Try Photon first
    try:
        geolocator = Photon(user_agent=random.choice(user_agents))
        location = geolocator.reverse(f"{lat}, {lon}", timeout=10)
        if location and 'properties' in location.raw:
            props = location.raw['properties']
            country = props.get('country')
            city = props.get('city') or props.get('name')
            if country:
                return country, city
    except:
        pass

    # Try Nominatim as fallback
    try:
        geolocator = Nominatim(user_agent=random.choice(user_agents))
        location = geolocator.reverse(f"{lat}, {lon}", language='en', timeout=10)
        if location and 'address' in location.raw:
            addr = location.raw['address']
            country = addr.get('country')
            city = addr.get('city') or addr.get('town') or addr.get('village')
            return country, city
    except:
        pass

    return None, None

def geocode_name(name):
    """Geocodes a city/country name into coordinates."""
    user_agents = [f"skycalc_search_{random.randint(100, 999)}"]
    try:
        geolocator = Photon(user_agent=random.choice(user_agents))
        location = geolocator.geocode(name, timeout=10)
        if location:
            return location.latitude, location.longitude
    except:
        pass

    try:
        geolocator = Nominatim(user_agent=random.choice(user_agents))
        location = geolocator.geocode(name, timeout=10)
        if location:
            return location.latitude, location.longitude
    except:
        pass

    return None, None

def normalize_text(text):
    if not text: return ""
    res = text.lower().replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u').replace('ñ', 'n')
    mappings = {
        "espana": "spain",
        "mexico": "mexico",
        "estados unidos": "usa",
        "united states": "usa",
        "brasil": "brazil",
        "costa rica": "costa_rica"
    }
    for k, v in mappings.items():
        if k in res:
            return v
    return res

def extract_city_from_filename(filename):
    name = filename.split('/')[-1].replace('.zip', '')
    name = re.sub(r'\.7\d{5}.*', '', name)
    name = re.sub(r'_TMYx.*', '', name)
    parts = name.split('_')
    if len(parts) >= 3:
        city = parts[2]
    elif len(parts) == 2:
        city = parts[1]
    else:
        city = parts[0]
    return city.replace('.', ' ').replace('-', ' ')

def obtener_estaciones_cercanas(lat, lon, top_n=5):
    country, city_target = get_location_info(lat, lon)
    if not country:
        # Fallback default
        country = "Mexico"

    norm_country = normalize_text(country)
    country_url = None
    for name, url in ONEBUILDING_MAPPING.items():
        if norm_country in normalize_text(name) or normalize_text(name) in norm_country:
            country_url = url
            break

    if not country_url:
        return pd.DataFrame()

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        resp = requests.get(country_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return pd.DataFrame()

        soup = BeautifulSoup(resp.text, 'html.parser')
        links = soup.find_all('a', href=True)

        estaciones = []
        seen_base_names = set()

        for link in links:
            href = link['href']
            if href.endswith('.zip') and 'TMYx' in href:
                base_name = re.sub(r'\.\d{4}-\d{4}', '', href)
                if base_name in seen_base_names: continue
                seen_base_names.add(base_name)

                full_url = urllib.parse.urljoin(country_url, href)
                city_name = extract_city_from_filename(href)

                estaciones.append({
                    'Estación': base_name.replace('.zip', '').split('/')[-1],
                    'URL_ZIP': full_url,
                    'City_Search': city_name
                })

        if not estaciones:
            return pd.DataFrame()

        df = pd.DataFrame(estaciones)

        # Heuristic: Search for city in names or just geocode first few
        candidatos = []
        if city_target:
            mask = df['City_Search'].str.contains(city_target, case=False, na=False)
            candidatos = df[mask].head(10).to_dict('records')

        if len(candidatos) < 3:
            existing_urls = [c['URL_ZIP'] for c in candidatos]
            for _, row in df.head(10).iterrows():
                if row['URL_ZIP'] not in existing_urls:
                    candidatos.append(row.to_dict())

        geolocator = Photon(user_agent=f"skycalc_v{random.randint(100,999)}")
        verified_estaciones = []

        for cand in candidatos[:8]:
            try:
                query = f"{cand['City_Search']}, {country}"
                loc = geolocator.geocode(query, timeout=5)
                if loc:
                    dist = geodesic((lat, lon), (loc.latitude, loc.longitude)).km
                    verified_estaciones.append({
                        'Estación': cand['Estación'],
                        'name': cand['Estación'],
                        'distancia_km': round(dist, 2),
                        'URL_ZIP': cand['URL_ZIP'],
                        'lat': loc.latitude,
                        'lon': loc.longitude
                    })
                time.sleep(0.5)
            except:
                continue

        if verified_estaciones:
            return pd.DataFrame(verified_estaciones).sort_values('distancia_km').head(top_n)

        return pd.DataFrame()

    except Exception as e:
        print(f"Error: {e}")
        return pd.DataFrame()

def descargar_y_extraer_epw(url_zip):
    temp_dir = tempfile.mkdtemp(prefix="epw_")
    zip_fn = os.path.join(temp_dir, "clima.zip")
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        req = urllib.request.Request(url_zip, headers=headers)
        with urllib.request.urlopen(req) as response, open(zip_fn, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        
        with zipfile.ZipFile(zip_fn, 'r') as z:
            z.extractall(temp_dir)

        for root, _, files in os.walk(temp_dir):
            for f in files:
                if f.endswith('.epw'):
                    target_path = os.path.join(tempfile.gettempdir(), f"skycalc_{random.randint(1000,9999)}.epw")
                    shutil.copy(os.path.join(root, f), target_path)
                    return target_path
    except Exception as e:
        print(f"Error: {e}")
        return None
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

def procesar_datos_clima(epw_path):
    """Usa Ladybug para extraer vectores completos: luz, viento, humedad y geolocalización."""
    from ladybug.epw import EPW
    try:
        epw = EPW(epw_path)
        return {
            'metadata': {
                'ciudad': epw.location.city,
                'pais': epw.location.country,
                'lat': epw.location.latitude,
                'lon': epw.location.longitude,
                'tz': epw.location.time_zone,
                'elevacion': epw.location.elevation
            },
            'temp_seca': epw.dry_bulb_temperature.values,
            'rad_directa': epw.direct_normal_radiation.values,
            'rad_dif': epw.diffuse_horizontal_radiation.values,
            # NUEVOS DATOS PARA HVAC Y VISUALES
            'hum_relativa': epw.relative_humidity.values,
            'vel_viento': epw.wind_speed.values,
            'dir_viento': epw.wind_direction.values,
            # 🟢 NUEVO DATO: Nubosidad (0 a 10)
            'nubes': epw.total_sky_cover.values
        }
    except Exception as e:
        print(f"Error con Ladybug EPW: {e}")
        return None
