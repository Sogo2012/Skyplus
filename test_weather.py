
from weather_utils import obtener_estaciones_cercanas
import pandas as pd

def test_find_stations():
    # Test coordinates for Madrid, Spain
    lat, lon = 40.4168, -3.7038
    df = obtener_estaciones_cercanas(lat, lon)
    print("Stations near Madrid:")
    print(df)
    assert not df.empty, "Should find stations near Madrid"

if __name__ == "__main__":
    try:
        test_find_stations()
        print("Test passed!")
    except Exception as e:
        print(f"Test failed: {e}")
