bl_info = {
    "name": "Eljugenio 3D Maker",
    "author": "Eljugenio3D",
    "version": (5, 6, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Eljugenio 3D Maker",
    "description": "Suite Completa con validación de settings, iconos y tooltips descriptivos",
    "category": "Mesh",
}

import bpy
import bmesh
import os
import math
from mathutils import Vector, Quaternion

# --- FUNCIONES DE APOYO ---
def update_decimate_preview(self, context):
    obj = context.active_object
    if not obj or obj.type != 'MESH': return
    
    # Usamos el ratio decimal (ej: 0.1) que viene del slider
    ratio = context.scene.ej3d_preview_ratio
    
    mod_name = "EJ3D_Preview"
    mod = obj.modifiers.get(mod_name) or obj.modifiers.new(name=mod_name, type='DECIMATE')
    mod.decimate_type = 'COLLAPSE'
    mod.use_collapse_triangulate = True
    mod.ratio = ratio

def get_world_bounds(obj):
    matrix = obj.matrix_world
    corners = [matrix @ Vector(corner) for corner in obj.bound_box]
    res = {
        'min': Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners))),
        'max': Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners))),
    }
    res['center'] = (res['min'] + res['max']) / 2
    return res

def get_group_dimensions(context):
    selected_objs = [obj for obj in context.selected_objects if obj.type == 'MESH']
    if not selected_objs: return None
    unit_factor = context.scene.unit_settings.scale_length * 1000
    min_v = Vector((float('inf'),)*3); max_v = Vector((float('-inf'),)*3)
    for obj in selected_objs:
        for corner in [obj.matrix_world @ Vector(v) for v in obj.bound_box]:
            for i in range(3):
                min_v[i] = min(min_v[i], corner[i]); max_v[i] = max(max_v[i], corner[i])
    return (max_v - min_v) * unit_factor

# --- OPERADORES SETTINGS ---

class EJ3D_OT_setup_3dprint(bpy.types.Operator):
    bl_idname = "ej3d.setup_3dprint"
    bl_label = "Blender for 3D Print"
    bl_description = "Configura unidades a milímetros y ajusta la rejilla para trabajar a escala real de impresión 3D"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        scene.unit_settings.system = 'METRIC'
        scene.unit_settings.scale_length = 0.001
        scene.unit_settings.length_unit = 'MILLIMETERS'
        try:
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            space.overlay.grid_scale = 0.001
        except: pass
        self.report({'INFO'}, "Configurado para 3D Print (mm)")
        return {'FINISHED'}

# --- OPERADORES EXPLODE ---

class EJ3D_OT_explode_objects(bpy.types.Operator):
    bl_idname = "ej3d.explode_objects"
    bl_label = "Explosionar"
    bl_description = "Separa los objetos hacia afuera desde su centro común para inspeccionar ensamblajes internos"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        selected = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if len(selected) < 2:
            self.report({'WARNING'}, "Selecciona al menos 2 objetos")
            return {'CANCELLED'}
        sum_centers = Vector((0,0,0))
        for obj in selected:
            if "orig_loc" not in obj: obj["orig_loc"] = obj.location.copy()
            sum_centers += obj.location
        avg_center = sum_centers / len(selected)
        factor = context.scene.ej3d_explode_factor
        for obj in selected:
            direction = obj.location - avg_center
            if direction.length == 0: direction = Vector((0,0,1))
            obj.location = obj.location + (direction.normalized() * factor)
        return {'FINISHED'}

class EJ3D_OT_reset_explode(bpy.types.Operator):
    bl_idname = "ej3d.reset_explode"
    bl_label = "Retornar"
    bl_description = "Devuelve los objetos a su posición original guardada antes de la explosión"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        for obj in [o for o in context.selected_objects if o.type == 'MESH']:
            if "orig_loc" in obj:
                obj.location = Vector(obj["orig_loc"])
                del obj["orig_loc"]
        return {'FINISHED'}

class EJ3D_OT_auto_snap(bpy.types.Operator):
    bl_idname = "ej3d.auto_snap"
    bl_label = "Auto-Snap"
    bl_description = "Alinea el objeto seleccionado al objeto activo basado en la geometría más cercana"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        active = context.active_object
        selected = [obj for obj in context.selected_objects if obj != active and obj.type == 'MESH']
        
        if not active or not selected:
            self.report({'WARNING'}, "Selecciona dos objetos (uno activo)")
            return {'CANCELLED'}
        
        target_obj = active
        source_obj = selected[0]
        mode = context.scene.ej3d_snap_mode

        def get_closest_point(obj, mode):
            mw = obj.matrix_world
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            
            points = []
            if mode == 'VERTEX':
                points = [mw @ v.co for v in bm.verts]
            elif mode == 'EDGE':
                points = [mw @ ((e.verts[0].co + e.verts[1].co) / 2) for e in bm.edges]
            elif mode == 'FACE':
                points = [mw @ f.calc_center_median() for f in bm.faces]
            
            bm.free()
            return points

        source_points = get_closest_point(source_obj, mode)
        target_points = get_closest_point(target_obj, mode)

        if not source_points or not target_points:
            return {'CANCELLED'}

        # Encontrar el par de puntos con la distancia mínima
        min_dist = float('inf')
        best_pair = (None, None)

        for sp in source_points:
            for tp in target_points:
                dist = (sp - tp).length
                if dist < min_dist:
                    min_dist = dist
                    best_pair = (sp, tp)

        if best_pair[0] and best_pair[1]:
            # Calcular el desplazamiento necesario
            offset = best_pair[1] - best_pair[0]
            source_obj.location += offset
            self.report({'INFO'}, f"Snap completado usando {mode}")
        
        return {'FINISHED'}

# --- OPERADORES MESH ---

class EJ3D_OT_fill_holes(bpy.types.Operator):
    bl_idname = "ej3d.fill_holes"
    bl_label = "Sellar Hueco"
    bl_description = "Cierra automáticamente agujeros en la malla para asegurar que el objeto sea sólido (Manifold)"
    def execute(self, context):
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.fill_holes(sides=0)
        bpy.ops.object.mode_set(mode='OBJECT')
        return {'FINISHED'}

class EJ3D_OT_trim_tool(bpy.types.Operator):
    bl_idname = "ej3d.trim_tool"
    bl_label = "Corte"
    bl_description = "Activa la herramienta cuchillo para realizar cortes manuales rápidos en la geometría"
    def execute(self, context):
        if context.active_object and context.active_object.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.knife_tool('INVOKE_DEFAULT', use_occlude_geometry=False, only_selected=False)
        return {'FINISHED'}

class EJ3D_OT_separate_parts(bpy.types.Operator):
    bl_idname = "ej3d.separate_parts"
    bl_label = "Rip & Separar"
    bl_description = "Desvincula las piezas sueltas o cortadas en objetos de Blender independientes"
    def execute(self, context):
        bpy.ops.mesh.rip('INVOKE_DEFAULT')
        bpy.ops.mesh.separate(type='LOOSE')
        bpy.ops.object.mode_set(mode='OBJECT')
        return {'FINISHED'}

# --- OPERADORES ENCASTRES (PIN PRO) ---

class EJ3D_OT_create_pin(bpy.types.Operator):
    bl_idname = "ej3d.create_pin"
    bl_label = "Generar Pin"
    bl_description = "Crea un pin de unión paramétrico en la posición del Cursor 3D"
    def execute(self, context):
        s = context.scene; b, t, h = s.epro_base, s.epro_top, s.epro_height
        mesh = bpy.data.meshes.new("PinMesh")
        obj = bpy.data.objects.new("PIN_PRO", mesh)
        context.collection.objects.link(obj)
        bm = bmesh.new()
        z_min, z_max = -h/2, h/2
        v = [bm.verts.new((-b/2,-b/2, z_min)), bm.verts.new((b/2,-b/2, z_min)), bm.verts.new((b/2,b/2, z_min)), bm.verts.new((-b/2,b/2, z_min)),
             bm.verts.new((-t/2,-t/2, z_max)), bm.verts.new((t/2,-t/2, z_max)), bm.verts.new((t/2,t/2, z_max)), bm.verts.new((-t/2,t/2, z_max))]
        bm.faces.new(v[0:4][::-1]); bm.faces.new(v[4:8])
        for i in range(4): bm.faces.new([v[i], v[(i+1)%4], v[(i+1)%4+4], v[i+4]])
        bm.to_mesh(mesh); bm.free(); obj.location = s.cursor.location
        obj.rotation_quaternion = s.cursor.matrix.to_quaternion()
        context.view_layer.objects.active = obj; obj.select_set(True)
        return {'FINISHED'}

class EJ3D_OT_flip_pin(bpy.types.Operator):
    bl_idname = "ej3d.flip_pin"
    bl_label = "Invertir Pin"
    bl_description = "Gira el pin seleccionado 180 grados para cambiar su orientación de encastre"
    def execute(self, context):
        obj = context.active_object
        if obj and "PIN" in obj.name:
            obj.rotation_quaternion @= Quaternion((1, 0, 0), math.pi)
        return {'FINISHED'}

class EJ3D_OT_bool_diff(bpy.types.Operator):
    bl_idname = "ej3d.bool_diff"
    bl_label = "Hembra"
    bl_description = "Resta el Pin del objeto seleccionado para crear el hueco, aplicando la holgura configurada"
    def execute(self, context):
        pin = context.active_object; targets = [o for o in context.selected_objects if o != pin]
        if not targets: return {'CANCELLED'}
        orig_scale = pin.scale.copy(); pin.scale += Vector((context.scene.epro_tol,)*3); context.view_layer.update()
        for t in targets:
            mod = t.modifiers.new(name="EJ_Diff", type='BOOLEAN')
            mod.object = pin; mod.operation = 'DIFFERENCE'; mod.solver = 'EXACT'
            context.view_layer.objects.active = t; bpy.ops.object.modifier_apply(modifier=mod.name)
        pin.scale = orig_scale; context.view_layer.objects.active = pin
        return {'FINISHED'}

class EJ3D_OT_bool_union(bpy.types.Operator):
    bl_idname = "ej3d.bool_union"
    bl_label = "Macho"
    bl_description = "Fusiona el Pin con el objeto seleccionado para crear el saliente de unión"
    def execute(self, context):
        pin = context.active_object; targets = [o for o in context.selected_objects if o != pin]
        if not targets: return {'CANCELLED'}
        for t in targets:
            mod = t.modifiers.new(name="EJ_Union", type='BOOLEAN')
            mod.object = pin; mod.operation = 'UNION'; mod.solver = 'EXACT'
            context.view_layer.objects.active = t; bpy.ops.object.modifier_apply(modifier=mod.name)
        return {'FINISHED'}

# --- EXPORTACIÓN ---

class EJ3D_OT_export_stl(bpy.types.Operator):
    bl_idname = "ej3d.export_stl"
    bl_label = "Exportar STL"
    bl_description = "Exporta cada objeto seleccionado como un archivo .STL individual listo para el laminador"
    def execute(self, context):
        path = bpy.path.abspath(context.scene.eljugenio_export_path) or os.path.expanduser("~")
        count = 0
        for obj in [o for o in context.selected_objects if o.type == 'MESH']:
            bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True)
            bpy.ops.wm.stl_export(filepath=os.path.join(path, f"{obj.name}.stl"), export_selected_objects=True)
            count += 1
        self.report({'INFO'}, f"Exportados {count} STL(s)")
        return {'FINISHED'}

class EJ3D_OT_export_obj_vc(bpy.types.Operator):
    bl_idname = "ej3d.export_obj_vc"
    bl_label = "Exportar OBJ (Color)"
    bl_description = "Exporta los objetos en formato OBJ preservando la información de colores de vértices"
    def execute(self, context):
        path = bpy.path.abspath(context.scene.eljugenio_export_path) or os.path.expanduser("~")
        count = 0
        for obj in [o for o in context.selected_objects if o.type == 'MESH']:
            bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True)
            bpy.ops.wm.obj_export(filepath=os.path.join(path, f"{obj.name}.obj"), export_selected_objects=True, export_materials=False, export_colors=True)
            count += 1
        self.report({'INFO'}, f"Exportados {count} OBJ(s)")
        return {'FINISHED'}

# --- ALINEACIÓN / ESCALA / NORMALES ---

class EJ3D_OT_align_pro(bpy.types.Operator):
    bl_idname = "ej3d.align_pro"
    bl_label = "Alineación"
    bl_description = "Alinea los objetos seleccionados con respecto al objeto activo basándose en sus límites"
    mode: bpy.props.StringProperty()
    def execute(self, context):
        active_obj = context.active_object; selected_objs = [obj for obj in context.selected_objects if obj != active_obj]
        if not active_obj or not selected_objs: return {'CANCELLED'}
        t_bounds = get_world_bounds(active_obj)
        for obj in selected_objs:
            o_bounds = get_world_bounds(obj)
            if self.mode == 'LEFT': obj.location.x += (t_bounds['min'].x - o_bounds['min'].x)
            elif self.mode == 'RIGHT': obj.location.x += (t_bounds['max'].x - o_bounds['max'].x)
            elif self.mode == 'BOTTOM': obj.location.z += (t_bounds['min'].z - o_bounds['min'].z)
            elif self.mode == 'TOP': obj.location.z += (t_bounds['max'].z - o_bounds['max'].z)
            elif self.mode == 'CENTER_X': obj.location.x += (t_bounds['center'].x - o_bounds['center'].x)
            elif self.mode == 'CENTER_Z': obj.location.z += (t_bounds['center'].z - o_bounds['center'].z)
            elif self.mode == 'CENTER_Y': obj.location.y += (t_bounds['center'].y - o_bounds['center'].y)
        return {'FINISHED'}

class EJ3D_OT_flip_normals(bpy.types.Operator):
    bl_idname = "ej3d.flip_normals"
    bl_label = "Invertir"
    bl_description = "Invierte la orientación de las caras. Útil para corregir mallas con 'agujeros' visuales"
    def execute(self, context):
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.flip_normals()
        bpy.ops.object.mode_set(mode='OBJECT')
        return {'FINISHED'}

class EJ3D_OT_apply_scale(bpy.types.Operator):
    bl_idname = "ej3d.apply_scale"
    bl_label = "Aplicar"
    bl_description = "Establece la escala actual como 1.0 (Reset). Obligatorio antes de imprimir o hacer booleanos"
    def execute(self, context):
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        return {'FINISHED'}

class EJ3D_OT_scale_center(bpy.types.Operator):
    bl_idname = "ej3d.scale_from_center"
    bl_label = "Escalar"
    bl_description = "Escala proporcionalmente todo el grupo seleccionado para que la altura (Z) sea exactamente la indicada"
    def execute(self, context):
        selected = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not selected: return {'CANCELLED'}
        target_bu = context.scene.target_scale_mm / (context.scene.unit_settings.scale_length * 1000.0)
        min_v = Vector((float('inf'),)*3); max_v = Vector((float('-inf'),)*3)
        for obj in selected:
            for c in [obj.matrix_world @ Vector(v) for v in obj.bound_box]:
                for i in range(3): min_v[i] = min(min_v[i], c[i]); max_v[i] = max(max_v[i], c[i])
        center = (min_v + max_v) / 2
        height_z = max_v.z - min_v.z
        if height_z <= 0: return {'CANCELLED'}
        ratio = target_bu / height_z
        for obj in selected:
            obj.location = center + (obj.location - center) * ratio
            obj.scale *= ratio
            bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        return {'FINISHED'}

# --- OPERADORES BOOLEANAS DINÁMICAS ---

class EJ3D_OT_dynamic_bool(bpy.types.Operator):
    bl_idname = "ej3d.dynamic_bool"
    bl_label = "Operación Booleana"
    bl_description = "Añade un modificador booleano dinámico sin aplicarlo"
    operation: bpy.props.StringProperty()

    def execute(self, context):
        active = context.active_object
        selected = [obj for obj in context.selected_objects if obj != active]
        
        if not active or not selected:
            self.report({'WARNING'}, "Selecciona el objeto base (activo) y el objeto para operar")
            return {'CANCELLED'}
        
        target = selected[0]
        # Crear modificador
        mod = active.modifiers.new(name=f"EJ_Dyn_{self.operation}", type='BOOLEAN')
        mod.operation = self.operation
        mod.object = target
        mod.solver = 'EXACT'
        
        # Opcional: Ocultar el target para ver el resultado
        #target.display_type = 'WIRE'
        bpy.context.space_data.shading.show_xray = True
        
        return {'FINISHED'}

class EJ3D_OT_bool_control(bpy.types.Operator):
    bl_idname = "ej3d.bool_control"
    bl_label = "Control Booleano"
    action: bpy.props.StringProperty()

    def execute(self, context):
        active = context.active_object
        if not active: return {'CANCELLED'}
        
        # Buscamos modificadores que empiecen con nuestro prefijo "EJ_Dyn_"
        to_process = [m for m in active.modifiers if m.name.startswith("EJ_Dyn_")]
        
        for mod in to_process:
            if self.action == 'APPLY':
                bpy.ops.object.modifier_apply(modifier=mod.name)
            else:
                active.modifiers.remove(mod)
                
                
        return {'FINISHED'}


#HEMBRA LIVE
class EJ3D_OT_GenerarHembraLive(bpy.types.Operator):
    """Genera hembras con holgura no destructiva (Solidify vivo)"""
    bl_idname = "object.generar_hembra_live"
    bl_label = "Generar Hembra Dinámica"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        seleccionados = context.selected_objects
        base = context.active_object
        
        if len(seleccionados) < 2 or not base:
            self.report({'ERROR'}, "Selecciona el Macho y luego la Base")
            return {'CANCELLED'}
        
        macho = [obj for obj in seleccionados if obj != base][0]
        # Usamos la propiedad epro_tol que ya existe en tu script v5.4
        holgura_inicial = context.scene.epro_tol

        # 1. Crear el objeto cortante (Duplicado)
        cortante = macho.copy()
        cortante.data = macho.data.copy() 
        cortante.name = f"CORTANTE_{macho.name}"
        context.collection.objects.link(cortante)
        
        # 2. Configurar el Solidify (SIN APLICAR)
        mod_solid = cortante.modifiers.new(name="HOLGURA_DINAMICA", type='SOLIDIFY')
        mod_solid.thickness = holgura_inicial
        mod_solid.offset = 1.0 # Empuja hacia afuera respetando huecos
        mod_solid.use_rim = True
        
        # 3. Estética del cortante
        #cortante.display_type = 'WIRE'
        #cortante.hide_render = True # No sale en el render
        bpy.context.space_data.shading.show_xray = True
        
        # 4. Aplicar Booleano a la Base
        bool_mod = base.modifiers.new(name="HUECO_ENCASTRE", type='BOOLEAN')
        bool_mod.operation = 'DIFFERENCE'
        bool_mod.object = cortante
        bool_mod.solver = 'EXACT'
        
        # Ocultar el macho original para ver el hueco
        macho.hide_viewport = True
        
        self.report({'INFO'}, "Cortante dinámico creado. Ajusta el grosor en el panel de modificadores del objeto CORTANTE.")
        return {'FINISHED'}

# --- PANELES ---


class EJ3D_PT_settings(bpy.types.Panel):
    bl_label = "Settings"; bl_idname = "EJ3D_PT_settings"; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'Eljugenio 3D Maker'; bl_options = {'DEFAULT_CLOSED'}
    def draw_header(self, context): self.layout.label(text="", icon='SETTINGS')
    def draw(self, context):
        layout = self.layout; scene = context.scene
        col = layout.column(align=True)
        col.prop(scene.unit_settings, "system", text="Sistema")
        col.prop(scene.unit_settings, "scale_length", text="Escala Unidad")
        layout.operator("ej3d.setup_3dprint", icon='MOD_LENGTH')

class EJ3D_PT_normales(bpy.types.Panel):
    bl_label = "Normales"; bl_idname = "EJ3D_PT_normales"; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'Eljugenio 3D Maker'; bl_options = {'DEFAULT_CLOSED'}
    def draw_header(self, context): self.layout.label(text="", icon='NORMALS_FACE')
    def draw(self, context):
        layout = self.layout; col = layout.column(align=True)
        col.prop(context.space_data.overlay, "show_face_orientation", text="Ver Orientación", icon='FACESEL')
        row = col.row(align=True)
        row.operator("ej3d.flip_normals", icon='FORCE_VORTEX')
        row.operator("ej3d.apply_scale", icon='TRANSFORM_APPLY')

class EJ3D_PT_escala(bpy.types.Panel):
    bl_label = "Escala (Eje Z)"; bl_idname = "EJ3D_PT_escala"; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'Eljugenio 3D Maker'; bl_options = {'DEFAULT_CLOSED'}
    def draw_header(self, context): self.layout.label(text="", icon='FULLSCREEN_ENTER')
    def draw(self, context):
        dims = get_group_dimensions(context); layout = self.layout
        if dims: layout.label(text=f"X: {dims.x:.2f} | Y: {dims.y:.2f} | Z: {dims.z:.2f}", icon='ORIENTATION_LOCAL')
        layout.prop(context.scene, "target_scale_mm", text="Altura Target (mm)")
        layout.operator("ej3d.scale_from_center", icon='FULLSCREEN_ENTER')

class EJ3D_PT_alineacion(bpy.types.Panel):
    bl_label = "Alineación"; bl_idname = "EJ3D_PT_alineacion"; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'Eljugenio 3D Maker'; bl_options = {'DEFAULT_CLOSED'}
    def draw_header(self, context): self.layout.label(text="", icon='CENTER_ONLY')
    def draw(self, context):
        layout = self.layout; active = context.active_object
        if not active: layout.label(text="Selecciona objetos", icon='INFO'); return
        col = layout.column(align=True)
        row = col.row(align=True); row.scale_y = 1.2
        row.operator("ej3d.align_pro", text="Izq", icon='ALIGN_LEFT').mode = 'LEFT'
        row.operator("ej3d.align_pro", text="Centro", icon='ALIGN_CENTER').mode = 'CENTER_X'
        row.operator("ej3d.align_pro", text="Der", icon='ALIGN_RIGHT').mode = 'RIGHT'
        row = col.row(align=True); row.scale_y = 1.2
        row.operator("ej3d.align_pro", text="Arriba", icon='ALIGN_TOP').mode = 'TOP'
        row.operator("ej3d.align_pro", text="Centro", icon='ALIGN_MIDDLE').mode = 'CENTER_Z'
        row.operator("ej3d.align_pro", text="Abajo", icon='ALIGN_BOTTOM').mode = 'BOTTOM'

class EJ3D_PT_explode(bpy.types.Panel):
    bl_label = "Explode Objects"; bl_idname = "EJ3D_PT_explode"; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'Eljugenio 3D Maker'; bl_options = {'DEFAULT_CLOSED'}
    def draw_header(self, context): self.layout.label(text="", icon='PARTICLE_DATA')
    #def draw(self, context):
        #layout = self.layout; scene = context.scene
        #box = layout.box()
        #box.prop(scene, "ej3d_explode_factor", text="Fuerza")
        #row = box.row(align=True); row.scale_y = 1.4
        #row.operator("ej3d.explode_objects", text="Explode", icon='FULLSCREEN_ENTER')
        #sub = row.row(align=True); sub.alert = True
        #sub.operator("ej3d.reset_explode", text="Retornar", icon='FILE_REFRESH')
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # Controles de Explosión existentes
        layout.prop(scene, "ej3d_explode_factor", text="Fuerza")
        row = layout.row(align=True)
        row.operator("ej3d.explode_objects", icon='FULLSCREEN_ENTER')
        row.operator("ej3d.reset_explode", icon='FILE_REFRESH')
        
        layout.separator()
        
        # Nueva sección de Auto-Snap
        box = layout.box()
        box.label(text="Herramienta SNAP", icon='SNAP_ON')
        col = box.column(align=True)
        col.prop(scene, "ej3d_snap_mode", text="")
        col.operator("ej3d.auto_snap", text="Auto-Snap", icon='SNAP_FACE')




class EJ3D_PT_encastres(bpy.types.Panel):
    bl_label = "Encastres"; bl_idname = "EJ3D_PT_encastres"; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'Eljugenio 3D Maker'; bl_options = {'DEFAULT_CLOSED'}
    def draw_header(self, context): self.layout.label(text="", icon='MOD_BOOLEAN')
    def draw(self, context):
        layout = self.layout; s = context.scene
        col = layout.column(align=True)
        col.operator("ej3d.trim_tool", icon='LINE_DATA')
        col.operator("ej3d.separate_parts", icon='UV_ISLANDSEL')
        col.operator("ej3d.fill_holes", icon='MESH_DATA', text="Sellar Hueco")
        box = layout.box(); col = box.column(align=True); col.label(text="Configuración Pin Pro", icon='MESH_CUBE')
        col.prop(s, "epro_base", text="Base del Pin")
        col.prop(s, "epro_top", text="Punta del Pin")
        col.prop(s, "epro_height", text="Altura del Pin")
        box.operator("ej3d.create_pin", icon='ADD')
        box.operator("ej3d.flip_pin", icon='FILE_REFRESH')
        layout.separator()
        col = layout.column(align=True)
        col.prop(s, "epro_tol", text="Holgura")
        col.operator("ej3d.bool_diff", icon='REMOVE', text="Hembra (Resta)")
        col.operator("ej3d.bool_union", icon='ADD', text="Macho (Unión)")
        # Integración de Flexi Make (Holgura Dinámica)
        layout.separator()
        box_live = layout.box()
        box_live.label(text="Holgura Pro (Huecos)", icon='MOD_SOLIDIFY')
        box_live.prop(context.scene, "epro_tol", text="Margen mm")
        box_live.operator("object.generar_hembra_live", text="Crear Hembra LIVE", icon='MOD_BOOLEAN')


class EJ3D_PT_booleanas_dinamicas(bpy.types.Panel):
    bl_label = "Booleanas Dinámicas"
    bl_idname = "EJ3D_PT_booleanas_dinamicas"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Eljugenio 3D Maker'
    bl_options = {'DEFAULT_CLOSED'}
    
    def draw_header(self, context):
        self.layout.label(text="", icon='MOD_BOOLEAN')

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        
        # Fila de operaciones
        row = col.row(align=True)
        row.operator("ej3d.dynamic_bool", text="Unión", icon='PMARKER_SEL').operation = 'UNION'
        row.operator("ej3d.dynamic_bool", text="Diferencia", icon='REMOVE').operation = 'DIFFERENCE'
        row.operator("ej3d.dynamic_bool", text="Intersección", icon='SELECT_INTERSECT').operation = 'INTERSECT'
            
        layout.separator()
        
        # Fila de control
        row_ctrl = layout.row(align=True)
        row_ctrl.scale_y = 1.2
        row_ctrl.operator("ej3d.bool_control", text="Aplicar", icon='CHECKMARK').action = 'APPLY'
        row_ctrl.operator("ej3d.bool_control", text="Quitar", icon='X').action = 'REMOVE'

class EJ3D_PT_decimate_dynamic(bpy.types.Panel):
    bl_label = "Decimateado Dinámico"
    bl_idname = "EJ3D_PT_decimate_dynamic"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Eljugenio 3D Maker'
    
    def draw_header(self, context):
        self.layout.label(text="", icon='MOD_DECIM')

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = context.active_object
        
        if not obj or obj.type != 'MESH':
            layout.label(text="Selecciona una malla", icon='ERROR')
            return

        # --- LÓGICA DE DATOS ---
        # 1. Datos Originales (Malla base)
        orig_faces = len(obj.data.polygons)
        orig_tris = sum(len(p.vertices) - 2 for p in obj.data.polygons)

        # 2. Datos Actuales (Malla con modificadores aplicados en el visor)
        depsgraph = context.evaluated_depsgraph_get()
        obj_eval = obj.evaluated_get(depsgraph)
        mesh_eval = obj_eval.to_mesh()
        
        curr_faces = len(mesh_eval.polygons)
        curr_tris = sum(len(p.vertices) - 2 for p in mesh_eval.polygons)

        # --- DISEÑO ---
        col = layout.column(align=True)
        
        # Bloque Comparativo
        box = col.box()
        # Encabezados
        row = box.row()
        row.label(text="ORIGINAL", icon='MESH_DATA')
        row.label(text="ACTUAL", icon='MODIFIER')
        
        # Fila de Caras (F)
        row = box.row()
        row.label(text=f"Caras: {orig_faces}")
        row.label(text=f"Caras: {curr_faces}")
        
        # Fila de Triángulos (T)
        row = box.row()
        row.label(text=f"Tri: {orig_tris}")
        row.label(text=f"Tri: {curr_tris}")

        layout.separator()
        
        # Slider de Ratio (0.1, 0.5, etc)
        layout.prop(scene, "ej3d_preview_ratio", text="Ratio", slider=True)
        
        layout.separator()
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("object.modifier_apply", text="Aplicar", icon='CHECKMARK').modifier = "EJ3D_Preview"
        row.operator("object.modifier_remove", text="Quitar", icon='X').modifier = "EJ3D_Preview"
        
        # Liberar memoria de la malla temporal
        obj_eval.to_mesh_clear()

class EJ3D_PT_export(bpy.types.Panel):
    bl_label = "Exportación"; bl_idname = "EJ3D_PT_export"; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'Eljugenio 3D Maker'; bl_options = {'DEFAULT_CLOSED'}
    def draw_header(self, context): self.layout.label(text="", icon='EXPORT')
    def draw(self, context):
        layout = self.layout; scene = context.scene
        layout.prop(scene, "eljugenio_export_path", text="", icon='FOLDER_REDIRECT')
        col = layout.column(align=True)
        col.operator("ej3d.export_stl", icon='FILE_TICK')
        col.operator("ej3d.export_obj_vc", icon='SHADING_TEXTURE', text="Exportar OBJ (Color)")

# --- REGISTRO ---

classes = (

    EJ3D_OT_setup_3dprint, EJ3D_OT_explode_objects, EJ3D_OT_reset_explode, EJ3D_OT_auto_snap, 
    EJ3D_OT_fill_holes, EJ3D_OT_trim_tool, EJ3D_OT_separate_parts, 
    EJ3D_OT_create_pin, EJ3D_OT_flip_pin, EJ3D_OT_bool_diff, 
    EJ3D_OT_bool_union, EJ3D_OT_GenerarHembraLive, EJ3D_OT_export_stl, EJ3D_OT_export_obj_vc, 
    EJ3D_OT_align_pro, EJ3D_OT_flip_normals, EJ3D_OT_apply_scale, 
    EJ3D_OT_scale_center, EJ3D_OT_dynamic_bool, EJ3D_OT_bool_control,
    EJ3D_PT_settings, EJ3D_PT_normales, EJ3D_PT_escala, EJ3D_PT_alineacion, 
    EJ3D_PT_explode, EJ3D_PT_encastres, EJ3D_PT_booleanas_dinamicas, EJ3D_PT_decimate_dynamic, EJ3D_PT_export
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ej3d_explode_factor = bpy.props.FloatProperty(name="Fuerza", default=20.0)
    #bpy.types.Scene.ej3d_preview_faces = bpy.props.IntProperty(name="Caras", default=5000, update=update_decimate_preview)
    # Reemplaza ej3d_preview_faces por esto en register()
    bpy.types.Scene.ej3d_preview_ratio = bpy.props.FloatProperty(
        name="Ratio", 
        description="Porcentaje decimal de caras a mantener",
        default=1.0, 
        min=0.001, 
        max=1.0, 
        precision=3, 
        update=update_decimate_preview
    )
    #
    bpy.types.Scene.target_scale_mm = bpy.props.FloatProperty(name="Target mm", default=50.0)
    bpy.types.Scene.eljugenio_export_path = bpy.props.StringProperty(name="Ruta", subtype='DIR_PATH')
    bpy.types.Scene.epro_base = bpy.props.FloatProperty(name="Base del Pin", default=10.0)
    bpy.types.Scene.epro_top = bpy.props.FloatProperty(name="Punta del Pin", default=7.0)
    bpy.types.Scene.epro_height = bpy.props.FloatProperty(name="Altura del Pin", default=15.0)
    bpy.types.Scene.epro_tol = bpy.props.FloatProperty(name="Holgura", default=0.075)
    bpy.types.Scene.ej3d_snap_mode = bpy.props.EnumProperty(
        name="Modo Snap",
        description="Elegir qué elementos usar para el auto-snap",
        items=[
            ('VERTEX', "Vértice", "Snap basado en los vértices más cercanos"),
            ('EDGE', "Arista", "Snap basado en los centros de arista más cercanos"),
            ('FACE', "Cara", "Snap basado en los centros de cara más cercanos")
        ],
        default='FACE'
    )

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.ej3d_explode_factor
    #del bpy.types.Scene.ej3d_preview_faces
    #del bpy.types.Scene.ej3d_preview_ratio
    ##
    del bpy.types.Scene.target_scale_mm
    del bpy.types.Scene.eljugenio_export_path
    del bpy.types.Scene.epro_base
    del bpy.types.Scene.epro_top
    del bpy.types.Scene.epro_height
    del bpy.types.Scene.epro_tol

if __name__ == "__main__":
    register()