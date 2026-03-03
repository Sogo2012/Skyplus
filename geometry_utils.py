# geometry_utils.py
import math
import pathlib

# Imports base (ladybug-geometry y honeybee-core — siempre disponibles)
from ladybug_geometry.geometry3d.pointvector import Point3D, Vector3D
from ladybug_geometry.geometry3d.face import Face3D
from dragonfly.model import Model as DFModel
from dragonfly.building import Building
from dragonfly.story import Story
from dragonfly.room2d import Room2D
from honeybee.aperture import Aperture
from honeybee.boundarycondition import Outdoors

# NOTA: Los imports de VTK y Display se hacen LAZY (dentro de la función)
# para evitar que un ModuleNotFoundError en ladybug_display_schema
# rompa toda la app al arrancar. Si VTK no está disponible, se usa
# el fallback de renderizado simple.
def _extraer_datos_vis_seguro(v_set):
    """Extrae objetos de visualización probando todos los nombres posibles de 2024 a 2026."""
    for attr in ['display_objects', 'objects', 'data', 'geometries']:
        if hasattr(v_set, attr):
            return getattr(v_set, attr)
    try:
        return list(v_set)
    except Exception:
        return []
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

        # 3. Fix Boundary Condition
        techo = [f for f in hb_room.faces if f.type.name == 'RoofCeiling'][0]
        techo.boundary_condition = Outdoors()

        # 4. Cuadrícula de domos
        area_domo = domo_ancho_m * domo_largo_m
        area_nave = ancho * largo
        num_domos_teoricos = max(1, math.ceil((area_nave * sfr_objetivo) / area_domo))

        cols = max(1, round((num_domos_teoricos * (ancho / largo)) ** 0.5))
        filas = max(1, math.ceil(num_domos_teoricos / cols))
        dx, dy = ancho / cols, largo / filas
        contador = 1

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

        # 5. Validación LBT
        reporte_validacion = hb_model.check_all()
        if reporte_validacion:
            print(f"⚠️ Modelo con problemas LBT: {reporte_validacion}")
        else:
            print("✅ GEOMETRÍA PERFECTA: Modelo LBT 100% válido.")

        # 6. Exportar VTK — lazy imports para no romper arranque de la app
        vtk_file = pathlib.Path('data', 'nave_industrial.vtkjs')
        vtk_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Lazy imports VTK/display
            from honeybee_vtk.model import Model as VTKModel
            from honeybee_display.model import model_to_vis_set
            from ladybug_vtk.visualization_set import VisualizationSet as VTKVS
            from ladybug_display.visualization import VisualizationSet as LBDVS
            import ladybug_display.extension.sunpath  # noqa: activa extensión

            vis_set_nave = model_to_vis_set(hb_model)

            # Versión sin sunpath (para el toggle)
            vtk_solo = VTKVS.from_visualization_set(vis_set_nave)
            vtk_solo.to_vtkjs(folder=str(vtk_file.parent), name=f"{vtk_file.stem}_solo")

            if lat is not None and lon is not None:
                from ladybug.sunpath import Sunpath
                sp = Sunpath(latitude=lat, longitude=lon)
                sp_vis_set = sp.to_vis_set()
                radio = (max(ancho, largo) * 1.5) / 100.0
                sp_vis_set.scale(radio)
                sp_vis_set.move(Vector3D(ancho/2, largo/2, altura/2))

                objs_nave = _extraer_datos_vis_seguro(vis_set_nave)
                objs_sol  = _extraer_datos_vis_seguro(sp_vis_set)
                todo = list(objs_nave) + list(objs_sol)

                try:
                    vis_set_final = LBDVS(todo, identifier='EscenaSolar')
                except TypeError:
                    vis_set_final = LBDVS(identifier='EscenaSolar', geometry=todo)

                vtk_final = VTKVS.from_visualization_set(vis_set_final)
                vtk_final.to_vtkjs(folder=str(vtk_file.parent), name=vtk_file.stem)
            else:
                VTKModel(hb_model).to_vtkjs(folder=str(vtk_file.parent), name=vtk_file.stem)

        except Exception as e:
            print(f"Aviso Sunpath: Falló motor avanzado ({e}). Usando renderizado de emergencia.")
            try:
                from honeybee_vtk.model import Model as VTKModel
                VTKModel(hb_model).to_vtkjs(folder=str(vtk_file.parent), name=vtk_file.stem)
            except Exception as e2:
                print(f"Error crítico al generar VTK: {e2}")
                return None, 0, 0

        # Métricas finales
        area_domos_total = sum([ap.area for ap in techo.apertures])
        sfr_real = area_domos_total / techo.area

        return str(vtk_file), len(techo.apertures), sfr_real

    except Exception as e:
        raise RuntimeError(f"Fallo profundo en el motor 3D: {e}")
