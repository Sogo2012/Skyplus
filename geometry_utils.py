# geometry_utils.py
import math
import pathlib
from ladybug_geometry.geometry3d.pointvector import Point3D
from ladybug_geometry.geometry3d.face import Face3D
from dragonfly.model import Model as DFModel
from dragonfly.building import Building
from dragonfly.story import Story
from dragonfly.room2d import Room2D
from honeybee.aperture import Aperture
from honeybee.boundarycondition import Outdoors 
from honeybee_vtk.model import Model as VTKModel

# Importar Vector3D es CRÍTICO para mover el sol
from ladybug_geometry.geometry3d.pointvector import Vector3D

# Nuevas rutas para el Motor Solar
from ladybug_display.visualization import VisualizationSet as LBDVS
from honeybee_display.model import model_to_vis_set
from ladybug_vtk.visualization_set import VisualizationSet as VTKVS
import ladybug_display.extension.sunpath
def _extraer_datos_vis_seguro(v_set):
    """Extrae objetos de visualización probando todos los nombres posibles de 2024 a 2026."""
    for attr in ['display_objects', 'objects', 'data', 'geometries']:
        if hasattr(v_set, attr):
            return getattr(v_set, attr)
    try: return list(v_set) # Plan B: Intentar iterar directamente
    except: return []
def generar_nave_3d_vtk(ancho, largo, altura, sfr_objetivo, domo_ancho_m, domo_largo_m, lat=None, lon=None):
    try:
        # 1. Crear piso y volumen
        puntos_piso = [Point3D(0, 0, 0), Point3D(ancho, 0, 0), Point3D(ancho, largo, 0), Point3D(0, largo, 0)]
        room_df = Room2D('Nave_Principal', Face3D(puntos_piso), floor_to_ceiling_height=altura)
        story = Story('Nivel_0', room_2ds=[room_df])
        building = Building('Planta_Industrial', unique_stories=[story])
        
        # 2. Pasar a Honeybee
        hb_model = DFModel('Modelo_Nave', buildings=[building]).to_honeybee(object_per_model='Building')[0]
        hb_room = hb_model.rooms[0]
        
        # 3. Fix de Boundary Condition (Indispensable para añadir Apertures)
        techo = [f for f in hb_room.faces if f.type.name == 'RoofCeiling'][0]
        techo.boundary_condition = Outdoors() 
        
        # 4. Algoritmo de Cuadrícula (Homologado a la versión 2D impecable)
        area_domo = domo_ancho_m * domo_largo_m
        area_nave = ancho * largo
        num_domos_teoricos = max(1, math.ceil((area_nave * sfr_objetivo) / area_domo))
        
        cols = max(1, round((num_domos_teoricos * (ancho / largo)) ** 0.5))
        filas = max(1, math.ceil(num_domos_teoricos / cols))
        
        dx, dy = ancho / cols, largo / filas
        contador = 1
        
        # LA MAGIA: Eliminamos el 'break' para forzar la simetría completa (cols * filas)
        for i in range(cols):
            for j in range(filas):
                cx = (i * dx) + (dx / 2)
                cy = (j * dy) + (dy / 2)
                
                pt1 = Point3D(cx - domo_ancho_m/2, cy - domo_largo_m/2, altura)
                pt2 = Point3D(cx + domo_ancho_m/2, cy - domo_largo_m/2, altura)
                pt3 = Point3D(cx + domo_ancho_m/2, cy + domo_largo_m/2, altura)
                pt4 = Point3D(cx - domo_ancho_m/2, cy + domo_largo_m/2, altura)
                
                cara_domo = Face3D([pt1, pt2, pt3, pt4])
                techo.add_aperture(Aperture(f"Domo_{contador}", cara_domo))
                contador += 1

        # ==========================================
        # 5. VALIDACIÓN OFICIAL PARA LBT (EnergyPlus / Radiance)
        # ==========================================
        reporte_validacion = hb_model.check_all()
        if reporte_validacion:
            print(f"⚠️ El modelo tiene problemas LBT: {reporte_validacion}")
        else:
            print("✅ GEOMETRÍA PERFECTA: Modelo LBT 100% válido para simulación.")

       # 6. EXPORTAR A VTK (Nave + Sunpath Blindado)
        vtk_file = pathlib.Path('data', 'nave_industrial.vtkjs')
        vtk_file.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Convertimos la nave a formato visual
            vis_set_nave = model_to_vis_set(hb_model)
            
            # NUEVO: Guardamos una versión "Limpia" (Solo Nave) para el Toggle
            vtk_solo = VTKVS.from_visualization_set(vis_set_nave)
            vtk_solo.to_vtkjs(folder=str(vtk_file.parent), name=f"{vtk_file.stem}_solo")
            
            if lat is not None and lon is not None:
                from ladybug.sunpath import Sunpath
                
                # A) Crear Sunpath y escalarlo manualmente (evita el TypeError)
                sp = Sunpath(latitude=lat, longitude=lon)
                sp_vis_set = sp.to_vis_set()
                
                radio = (max(ancho, largo) * 1.5) / 100.0
                sp_vis_set.scale(radio)
                sp_vis_set.move(Vector3D(ancho/2, largo/2, altura/2))
                
                # B) Fusión usando la Sonda Detective
                objs_nave = _extraer_datos_vis_seguro(vis_set_nave)
                objs_sol = _extraer_datos_vis_seguro(sp_vis_set)
                todo = list(objs_nave) + list(objs_sol)
                
                # C) Crear set final (Plan A o B de Colab)
                try:
                    vis_set_final = LBDVS(todo, identifier='EscenaSolar')
                except TypeError:
                    vis_set_final = LBDVS(identifier='EscenaSolar', geometry=todo)
                    
                vtk_final = VTKVS.from_visualization_set(vis_set_final)
                vtk_final.to_vtkjs(folder=str(vtk_file.parent), name=vtk_file.stem)
            else:
                # Renderizado simple si no hay ubicación
                VTKModel(hb_model).to_vtkjs(folder=str(vtk_file.parent), name=vtk_file.stem)
                
        except Exception as e:
            print(f"Aviso Sunpath: Falló el motor avanzado ({e}). Usando renderizado de emergencia.")
            try:
                vtk_model = VTKModel(hb_model)
                vtk_model.to_vtkjs(folder=str(vtk_file.parent), name=vtk_file.stem)
            except Exception as e2:
                print(f"Error crítico al generar VTK: {e2}")
                return None, 0, 0
        
        # Cálculo final de métricas 
        area_domos_total = sum([ap.area for ap in techo.apertures])
        sfr_real = (area_domos_total / techo.area)
        
        return str(vtk_file), len(techo.apertures), sfr_real

    except Exception as e:
        print(f"Error en geometría: {e}")
        return None, 0, 0
