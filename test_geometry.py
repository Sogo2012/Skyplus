# test_geometry.py
from geometry_utils import generar_nave_industrial

# Simulamos que el usuario ingresó estos datos en la barra lateral:
ANCHO_NAVE = 50.0  # metros
LARGO_NAVE = 100.0 # metros
ALTURA_NAVE = 8.0  # metros
SFR = 0.05         # 5% de domos

print("Iniciando prueba del motor geométrico...\n")

modelo, habitacion = generar_nave_industrial(ANCHO_NAVE, LARGO_NAVE, ALTURA_NAVE, SFR)

if modelo and habitacion:
    print("\n--- REPORTE DE GEOMETRÍA ---")
    print(f"Volumen de la nave: {habitacion.volume:.1f} m³")
    print(f"Área del piso: {habitacion.floor_area:.1f} m²")
    print(f"Área exterior (Paredes + Techo): {habitacion.exterior_area:.1f} m²")
    
    # Vamos a contar cuántas caras tiene y si el techo tiene aperturas
    techo = [f for f in habitacion.faces if f.type.name == 'RoofCeiling'][0]
    print(f"Área del techo: {techo.area:.1f} m²")
    print(f"Cantidad de domos generados: {len(techo.apertures)}")
    
    area_domos = sum([ap.area for ap in techo.apertures])
    print(f"Área total de domos: {area_domos:.1f} m²")
    print(f"SFR Real alcanzado: {(area_domos / techo.area) * 100:.2f}%")
else:
    print("Falló la generación del modelo.")
