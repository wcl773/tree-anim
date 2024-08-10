bl_info = {
    "name": "Vertex Animation",
    "author": "Joshua Bogart",
    "version": (1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Not Unreal Tools Tab",
    "description": "A tool for storing per frame vertex data for use in a vertex shader.",
    "warning": "",
    "doc_url": "",
    "category": "Not Unreal Tools",
}

import bpy
import bmesh
import os

def get_per_frame_mesh_data(context, data, objects):
    """Return a list of combined mesh data per frame"""
    meshes = []
    for i in frame_range(context.scene):
        context.scene.frame_set(i)
        depsgraph = context.evaluated_depsgraph_get()
        bm = bmesh.new()
        for ob in objects:
            eval_object = ob.evaluated_get(depsgraph)
            me = data.meshes.new_from_object(eval_object)
            me.transform(ob.matrix_world)
            bm.from_mesh(me)
            data.meshes.remove(me)
        me = data.meshes.new("mesh")
        bm.to_mesh(me)
        me.update()  # Ensure the mesh data is updated
        bm.free()
        meshes.append(me)
    return meshes

def create_export_mesh_object(context, data, me):
    """Return a mesh object with correct UVs"""
    while len(me.uv_layers) < 2:
        me.uv_layers.new()
    uv_layer = me.uv_layers[1]
    uv_layer.name = "vertex_anim"
    for loop in me.loops:
        uv_layer.data[loop.index].uv = ((loop.vertex_index + 0.5)/len(me.vertices), 0.0)
    ob = data.objects.new("export_mesh", me)
    context.scene.collection.objects.link(ob)
    return ob

def get_vertex_data(data, meshes):
    """Return lists of vertex offsets and normals from a list of mesh data"""
    original = meshes[0].vertices
    offsets = []
    normals = []
    for me in meshes:
        bm = bmesh.new()
        bm.from_mesh(me)
        for v in bm.verts:
            offset = v.co - original[v.index].co
            x, y, z = offset
            offsets.extend((x, -y, z, 1))
            x, y, z = v.normal
            normals.extend(((x + 1) * 0.5, (-y + 1) * 0.5, (z + 1) * 0.5, 1))
        bm.free()
    return offsets, normals

def frame_range(scene):
    """Return a range object with the scene's frame start, end, and step"""
    return range(scene.frame_start, scene.frame_end + 1, scene.frame_step)

def bake_vertex_data(context, data, offsets, normals, size):
    """Stores vertex offsets and normals in separate image textures"""
    width, height = size
    
    blend_path = bpy.data.filepath
    blend_path = os.path.dirname(bpy.path.abspath(blend_path))
    subfolder_path = os.path.join(blend_path, "vaexport")
    if not os.path.exists(subfolder_path):
        os.makedirs(subfolder_path)
    openexr_filepath = os.path.join(subfolder_path, "offsets.exr")
    png_filepath = os.path.join(subfolder_path, "normals.png")
    
    # Create a new scene for OpenEXR export
    openexr_export_scene = bpy.data.scenes.new(name='openexr_export_scene')
    openexr_export_scene.render.image_settings.color_depth = '16'
    openexr_export_scene.render.image_settings.color_mode = 'RGBA'
    openexr_export_scene.render.image_settings.file_format = 'OPEN_EXR'
    openexr_export_scene.render.image_settings.exr_codec = 'NONE'
    
    if 'offsets' in bpy.data.images:
        offset_tex = bpy.data.images['offsets']
        bpy.data.images.remove(offset_tex)
    
    offset_texture = data.images.new(
        name="offsets",
        width=width,
        height=height,
        alpha=True,
        float_buffer=True
    )
    
    offset_texture.file_format = 'OPEN_EXR'
    offset_texture.colorspace_settings.name = 'Non-Color'
    offset_texture.pixels = offsets
    offset_texture.save_render(openexr_filepath, scene=openexr_export_scene)
    bpy.data.scenes.remove(openexr_export_scene)
    
    # Create a new scene for PNG export
    png_export_scene = bpy.data.scenes.new(name='png_export_scene')
    png_export_scene.render.image_settings.color_depth = '8'
    png_export_scene.render.image_settings.color_mode = 'RGBA'
    png_export_scene.render.image_settings.file_format = 'PNG'
    png_export_scene.render.image_settings.compression = 15
    
    if 'normals' in bpy.data.images:
        normals_tex = bpy.data.images['normals']
        bpy.data.images.remove(normals_tex)
    
    normal_texture = data.images.new(
        name="normals",
        width=width,
        height=height,
        alpha=True
    )
    
    normal_texture.file_format = 'PNG'
    normal_texture.pixels = normals
    normal_texture.save_render(png_filepath, scene=png_export_scene)
    bpy.data.scenes.remove(png_export_scene)

class OBJECT_OT_ProcessAnimMeshes(bpy.types.Operator):
    """Store combined per frame vertex offsets and normals for all
    selected mesh objects into separate image textures"""
    bl_idname = "object.process_anim_meshes"
    bl_label = "Process Anim Meshes"

    @property
    def allowed_modifiers(self):
        return [
            'ARMATURE', 'CAST', 'CURVE', 'DISPLACE', 'HOOK',
            'LAPLACIANDEFORM', 'LATTICE', 'MESH_DEFORM',
            'SHRINKWRAP', 'SIMPLE_DEFORM', 'SMOOTH',
            'CORRECTIVE_SMOOTH', 'LAPLACIANSMOOTH',
            'SURFACE_DEFORM', 'WARP', 'WAVE',
        ]

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob and ob.type == 'MESH' and ob.mode == 'OBJECT'

    def execute(self, context):
        units = context.scene.unit_settings
        data = bpy.data
        objects = [ob for ob in context.selected_objects if ob.type == 'MESH']
        vertex_count = sum([len(ob.data.vertices) for ob in objects])
        frame_count = len(frame_range(context.scene))
        for ob in objects:
            for mod in ob.modifiers:
                if mod.type not in self.allowed_modifiers:
                    self.report(
                        {'ERROR'},
                        f"Objects with {mod.type.title()} modifiers are not allowed!"
                    )
                    return {'CANCELLED'}
        if vertex_count > 8192:
            self.report(
                {'ERROR'},
                f"Vertex count of {vertex_count :,}, exceeds limit of 8,192!"
            )
            return {'CANCELLED'}
        if frame_count > 8192:
            self.report(
                {'ERROR'},
                f"Frame count of {frame_count :,}, exceeds limit of 8,192!"
            )
            return {'CANCELLED'}
        meshes = get_per_frame_mesh_data(context, data, objects)
        export_mesh_data = meshes[0].copy()
        create_export_mesh_object(context, data, export_mesh_data)
        offsets, normals = get_vertex_data(data, meshes)
        texture_size = vertex_count, frame_count
        bake_vertex_data(context, data, offsets, normals, texture_size)
        return {'FINISHED'}

class VIEW3D_PT_VertexAnimation(bpy.types.Panel):
    """Creates a Panel in 3D Viewport"""
    bl_label = "Vertex Animation"
    bl_idname = "VIEW3D_PT_vertex_animation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Not Unreal Tools"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        scene = context.scene
        col = layout.column(align=True)
        col.prop(scene, "frame_start", text="Frame Start")
        col.prop(scene, "frame_end", text="End")
        col.prop(scene, "frame_step", text="Step")
        row = layout.row()
        row.operator("object.process_anim_meshes")

def register():
    bpy.utils.register_class(OBJECT_OT_ProcessAnimMeshes)
    bpy.utils.register_class(VIEW3D_PT_VertexAnimation)

def unregister():
    bpy.utils.unregister_class(OBJECT_OT_ProcessAnimMeshes)
    bpy.utils.unregister_class(VIEW3D_PT_VertexAnimation)

if __name__ == "__main__":
    register()
