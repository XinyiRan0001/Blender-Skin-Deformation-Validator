import bpy
import json
from bpy.app.handlers import persistent


# ============================================================
# TA SKIN / DEFORMATION VALIDATOR
# LIVE ANALYSIS VERSION
# ============================================================


# ============================================================
# SETTINGS
# ============================================================

MAX_INFLUENCES = 4
TINY_RELATIVE_WEIGHT = 0.01
RAW_SUM_TOLERANCE = 0.01

AVERAGE_WEIGHT = 0.70
PEAK_WEIGHT = 0.30

PROBLEM_STRETCH_WEIGHT = 0.20
PROBLEM_COMPRESSION_WEIGHT = 0.30
PROBLEM_COLLAPSE_WEIGHT = 0.50
PROBLEM_INTERACTION_WEIGHT = 0.35

HEATMAP_ATTRIBUTE = "TA_Distortion"
HEATMAP_MATERIAL = "TA_Distortion_Heatmap"

BACKUP_MATERIALS_PROP = "ta_original_material_names"
BACKUP_INDICES_PROP = "ta_original_material_indices"

REGIONS_PROP = "ta_problem_regions"


# ============================================================
# LIVE CACHE
# ============================================================

# Runtime only.
# Nothing here is permanently stored in the .blend.
TA_LIVE_CACHE = {}

TA_LIVE_UPDATING = False


# ============================================================
# BASIC HELPERS
# ============================================================

def get_active_mesh():

    obj = bpy.context.active_object

    if obj is None:
        return None

    if obj.type != 'MESH':
        return None

    return obj


def get_armature(obj):

    if obj is None:
        return None

    for modifier in obj.modifiers:

        if (
            modifier.type == 'ARMATURE'
            and modifier.object is not None
        ):
            return modifier.object

    return None


def get_deform_group_indices(obj):

    armature = get_armature(obj)

    if armature is None:
        return set()

    deform_names = {
        bone.name
        for bone in armature.data.bones
        if bone.use_deform
    }

    return {
        group.index
        for group in obj.vertex_groups
        if group.name in deform_names
    }


def get_evaluated_positions(obj):

    depsgraph = bpy.context.evaluated_depsgraph_get()

    eval_obj = obj.evaluated_get(depsgraph)

    eval_mesh = eval_obj.to_mesh()

    try:

        return [
            vertex.co.copy()
            for vertex in eval_mesh.vertices
        ]

    finally:

        eval_obj.to_mesh_clear()


def select_vertices(obj, indices):

    if obj.mode != 'OBJECT':

        bpy.ops.object.mode_set(
            mode='OBJECT'
        )

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    for vertex in obj.data.vertices:
        vertex.select = False

    for index in indices:

        if 0 <= index < len(obj.data.vertices):
            obj.data.vertices[index].select = True

    bpy.ops.object.mode_set(
        mode='EDIT'
    )


# ============================================================
# WEIGHT VALIDATION
# ============================================================

def scan_weights(obj):

    unweighted = []
    raw_sum = []
    too_many = []
    tiny = []

    deform_groups = get_deform_group_indices(obj)

    if not deform_groups:

        return {
            "error": "No deform bone groups found.",
            "unweighted": [],
            "raw_sum": [],
            "too_many": [],
            "tiny": [],
        }

    for vertex in obj.data.vertices:

        weights = []

        for element in vertex.groups:

            if element.group not in deform_groups:
                continue

            if element.weight <= 0.0:
                continue

            weights.append(
                (
                    element.group,
                    element.weight
                )
            )

        if not weights:

            unweighted.append(vertex.index)
            continue

        total = sum(
            weight
            for _, weight in weights
        )

        if abs(total - 1.0) > RAW_SUM_TOLERANCE:

            raw_sum.append(
                (
                    vertex.index,
                    total
                )
            )

        if total <= 0.0:
            continue

        relative = [
            (
                group,
                weight / total
            )
            for group, weight in weights
        ]

        meaningful = [
            weight
            for _, weight in relative
            if weight >= TINY_RELATIVE_WEIGHT
        ]

        if len(meaningful) > MAX_INFLUENCES:

            too_many.append(
                vertex.index
            )

        if any(
            0.0 < weight < TINY_RELATIVE_WEIGHT
            for _, weight in relative
        ):

            tiny.append(
                vertex.index
            )

    return {
        "error": None,
        "unweighted": unweighted,
        "raw_sum": raw_sum,
        "too_many": too_many,
        "tiny": tiny,
    }


# ============================================================
# SCORE HELPERS
# ============================================================

def blended_local_score(values):

    if not values:
        return 0.0

    average = sum(values) / len(values)

    peak = max(values)

    return (
        average * AVERAGE_WEIGHT
        +
        peak * PEAK_WEIGHT
    )


def triangle_area(a, b, c):

    return (
        (b - a).cross(c - a).length
        * 0.5
    )


def calculate_problem_scores(
    stretch_scores,
    compression_scores,
    collapse_scores
):

    scores = []

    for stretch, compression, collapse in zip(
        stretch_scores,
        compression_scores,
        collapse_scores
    ):

        base = (
            stretch * PROBLEM_STRETCH_WEIGHT
            +
            compression * PROBLEM_COMPRESSION_WEIGHT
            +
            collapse * PROBLEM_COLLAPSE_WEIGHT
        )

        interaction = (
            min(
                compression,
                collapse
            )
            * PROBLEM_INTERACTION_WEIGHT
        )

        scores.append(
            base + interaction
        )

    return scores


# ============================================================
# REST CACHE
# ============================================================

def build_rest_cache(obj):

    armature = get_armature(obj)

    if armature is None:

        return (
            None,
            "No Armature Modifier found."
        )

    if obj.mode != 'OBJECT':

        try:

            bpy.ops.object.mode_set(
                mode='OBJECT'
            )

        except Exception:
            pass

    original_pose_position = (
        armature.data.pose_position
    )

    try:

        armature.data.pose_position = 'REST'

        bpy.context.view_layer.update()

        rest_positions = (
            get_evaluated_positions(obj)
        )

    finally:

        armature.data.pose_position = (
            original_pose_position
        )

        bpy.context.view_layer.update()

    mesh = obj.data

    if len(rest_positions) != len(mesh.vertices):

        return (
            None,
            (
                "Evaluated topology differs from "
                "the base mesh."
            )
        )

    # --------------------------------------------------------
    # Cache edges and rest lengths
    # --------------------------------------------------------

    edges = []

    for edge in mesh.edges:

        a, b = edge.vertices

        rest_length = (
            rest_positions[b]
            -
            rest_positions[a]
        ).length

        if rest_length <= 0.000001:
            continue

        edges.append(
            (
                a,
                b,
                rest_length
            )
        )

    # --------------------------------------------------------
    # Cache triangles and rest areas
    # --------------------------------------------------------

    mesh.calc_loop_triangles()

    triangles = []

    for tri in mesh.loop_triangles:

        a, b, c = tri.vertices

        rest_area = triangle_area(
            rest_positions[a],
            rest_positions[b],
            rest_positions[c]
        )

        if rest_area <= 0.0000001:
            continue

        triangles.append(
            (
                a,
                b,
                c,
                rest_area
            )
        )

    cache = {

        "object_name":
            obj.name,

        "vertex_count":
            len(mesh.vertices),

        "edges":
            edges,

        "triangles":
            triangles,

        "rest_positions":
            rest_positions,

        "last_frame":
            None,
    }

    return cache, None


# ============================================================
# FAST CURRENT-POSE ANALYSIS
# ============================================================

def calculate_current_pose_scores(
    obj,
    cache
):

    current_positions = (
        get_evaluated_positions(obj)
    )

    vertex_count = (
        cache["vertex_count"]
    )

    if len(current_positions) != vertex_count:

        return {
            "error":
                (
                    "Evaluated topology changed. "
                    "Rebuild the Live Cache."
                )
        }

    stretch_local = [
        []
        for _ in range(vertex_count)
    ]

    compression_local = [
        []
        for _ in range(vertex_count)
    ]

    combined_local = [
        []
        for _ in range(vertex_count)
    ]

    collapse_local = [
        []
        for _ in range(vertex_count)
    ]

    # ========================================================
    # EDGE DEFORMATION
    # ========================================================

    for (
        a,
        b,
        rest_length
    ) in cache["edges"]:

        current_length = (
            current_positions[b]
            -
            current_positions[a]
        ).length

        ratio = (
            current_length
            /
            rest_length
        )

        stretch = 0.0
        compression = 0.0

        if ratio > 1.0:

            stretch = ratio - 1.0

        elif ratio < 1.0:

            compression = (
                1.0
                /
                max(
                    ratio,
                    0.000001
                )
            ) - 1.0

        combined = max(
            stretch,
            compression
        )

        stretch_local[a].append(
            stretch
        )

        stretch_local[b].append(
            stretch
        )

        compression_local[a].append(
            compression
        )

        compression_local[b].append(
            compression
        )

        combined_local[a].append(
            combined
        )

        combined_local[b].append(
            combined
        )

    # ========================================================
    # SURFACE COLLAPSE
    # ========================================================

    for (
        a,
        b,
        c,
        rest_area
    ) in cache["triangles"]:

        current_area = triangle_area(
            current_positions[a],
            current_positions[b],
            current_positions[c]
        )

        ratio = (
            current_area
            /
            rest_area
        )

        collapse = max(
            0.0,
            1.0 - ratio
        )

        collapse = min(
            collapse,
            1.0
        )

        collapse_local[a].append(
            collapse
        )

        collapse_local[b].append(
            collapse
        )

        collapse_local[c].append(
            collapse
        )

    # ========================================================
    # BLENDED VERTEX SCORES
    # ========================================================

    stretch_scores = [
        blended_local_score(values)
        for values in stretch_local
    ]

    compression_scores = [
        blended_local_score(values)
        for values in compression_local
    ]

    combined_scores = [
        blended_local_score(values)
        for values in combined_local
    ]

    collapse_scores = [
        blended_local_score(values)
        for values in collapse_local
    ]

    problem_scores = (
        calculate_problem_scores(
            stretch_scores,
            compression_scores,
            collapse_scores
        )
    )

    return {

        "error": None,

        "combined_scores":
            combined_scores,

        "stretch_scores":
            stretch_scores,

        "compression_scores":
            compression_scores,

        "collapse_scores":
            collapse_scores,

        "problem_scores":
            problem_scores,
    }


# ============================================================
# FULL ANALYSIS
# ============================================================

def analyze_distortion(
    obj,
    threshold
):

    cache, error = (
        build_rest_cache(obj)
    )

    if error:

        return {
            "error": error
        }

    results = (
        calculate_current_pose_scores(
            obj,
            cache
        )
    )

    if results["error"]:
        return results

    def get_high(scores):

        return [
            index
            for index, score
            in enumerate(scores)
            if score >= threshold
        ]

    def safe_max(values):

        return (
            max(values)
            if values
            else 0.0
        )

    def safe_average(values):

        return (
            sum(values) / len(values)
            if values
            else 0.0
        )

    for mode_name in [
        "combined",
        "stretch",
        "compression",
        "collapse",
        "problem",
    ]:

        scores = results[
            f"{mode_name}_scores"
        ]

        results[
            f"high_{mode_name}"
        ] = get_high(scores)

        results[
            f"max_{mode_name}"
        ] = safe_max(scores)

        results[
            f"avg_{mode_name}"
        ] = safe_average(scores)

    return results


# ============================================================
# MODE HELPER
# ============================================================

def get_mode_scores(
    results,
    mode
):

    mapping = {

        'COMBINED': (
            "combined_scores",
            "high_combined",
            "max_combined",
            "avg_combined"
        ),

        'STRETCH': (
            "stretch_scores",
            "high_stretch",
            "max_stretch",
            "avg_stretch"
        ),

        'COMPRESSION': (
            "compression_scores",
            "high_compression",
            "max_compression",
            "avg_compression"
        ),

        'SURFACE_COLLAPSE': (
            "collapse_scores",
            "high_collapse",
            "max_collapse",
            "avg_collapse"
        ),

        'PROBLEM': (
            "problem_scores",
            "high_problem",
            "max_problem",
            "avg_problem"
        ),
    }

    keys = mapping.get(
        mode,
        mapping["COMBINED"]
    )

    return (
        results[keys[0]],
        results[keys[1]],
        results[keys[2]],
        results[keys[3]],
    )


def get_live_mode_scores(
    results,
    mode
):

    mapping = {

        'COMBINED':
            "combined_scores",

        'STRETCH':
            "stretch_scores",

        'COMPRESSION':
            "compression_scores",

        'SURFACE_COLLAPSE':
            "collapse_scores",

        'PROBLEM':
            "problem_scores",
    }

    key = mapping.get(
        mode,
        "combined_scores"
    )

    return results[key]


# ============================================================
# PROBLEM REGIONS
# ============================================================

def build_mesh_neighbors(mesh):

    neighbors = {
        vertex.index: set()
        for vertex in mesh.vertices
    }

    for edge in mesh.edges:

        a, b = edge.vertices

        neighbors[a].add(b)
        neighbors[b].add(a)

    return neighbors


def cluster_problem_regions(
    obj,
    results,
    threshold
):

    problem_vertices = {
        index
        for index, score
        in enumerate(
            results["problem_scores"]
        )
        if score >= threshold
    }

    if not problem_vertices:
        return []

    neighbors = (
        build_mesh_neighbors(
            obj.data
        )
    )

    visited = set()
    regions = []

    for start in problem_vertices:

        if start in visited:
            continue

        stack = [start]

        visited.add(start)

        region_vertices = []

        while stack:

            current = stack.pop()

            region_vertices.append(
                current
            )

            for neighbor in neighbors[current]:

                if (
                    neighbor in problem_vertices
                    and
                    neighbor not in visited
                ):

                    visited.add(neighbor)

                    stack.append(
                        neighbor
                    )

        if len(region_vertices) >= 2:

            regions.append(
                region_vertices
            )

    return regions


def average_for_region(
    scores,
    vertices
):

    if not vertices:
        return 0.0

    return (
        sum(
            scores[index]
            for index in vertices
        )
        /
        len(vertices)
    )


def peak_for_region(
    scores,
    vertices
):

    if not vertices:
        return 0.0

    return max(
        scores[index]
        for index in vertices
    )


def determine_primary_cause(
    stretch,
    compression,
    collapse
):

    if (
        compression >= 0.15
        and
        collapse >= 0.15
    ):

        if (
            min(
                compression,
                collapse
            )
            >= stretch * 0.75
        ):

            return (
                "Compression + Surface Collapse"
            )

    values = {
        "Stretch": stretch,
        "Compression": compression,
        "Surface Collapse": collapse,
    }

    return max(
        values,
        key=values.get
    )


def risk_from_peak(
    peak,
    threshold
):

    if peak >= threshold * 3.0:
        return "SEVERE"

    if peak >= threshold * 2.0:
        return "HIGH"

    return "MEDIUM"


def create_region_data(
    obj,
    results,
    threshold
):

    clusters = (
        cluster_problem_regions(
            obj,
            results,
            threshold
        )
    )

    regions = []

    for vertices in clusters:

        problem_peak = (
            peak_for_region(
                results[
                    "problem_scores"
                ],
                vertices
            )
        )

        problem_average = (
            average_for_region(
                results[
                    "problem_scores"
                ],
                vertices
            )
        )

        stretch_average = (
            average_for_region(
                results[
                    "stretch_scores"
                ],
                vertices
            )
        )

        compression_average = (
            average_for_region(
                results[
                    "compression_scores"
                ],
                vertices
            )
        )

        collapse_average = (
            average_for_region(
                results[
                    "collapse_scores"
                ],
                vertices
            )
        )

        cause = (
            determine_primary_cause(
                stretch_average,
                compression_average,
                collapse_average
            )
        )

        risk = risk_from_peak(
            problem_peak,
            threshold
        )

        regions.append({

            "vertices":
                vertices,

            "vertex_count":
                len(vertices),

            "peak":
                problem_peak,

            "average":
                problem_average,

            "stretch":
                stretch_average,

            "compression":
                compression_average,

            "collapse":
                collapse_average,

            "cause":
                cause,

            "risk":
                risk,
        })

    regions.sort(
        key=lambda region:
            region["peak"],
        reverse=True
    )

    return regions


def save_regions(
    obj,
    regions
):

    obj[REGIONS_PROP] = (
        json.dumps(regions)
    )


def load_regions(obj):

    if REGIONS_PROP not in obj:
        return []

    try:

        return json.loads(
            obj[REGIONS_PROP]
        )

    except Exception:

        return []


def update_region_inspector(
    obj,
    region
):

    obj["ta_region_vertex_count"] = (
        region["vertex_count"]
    )

    obj["ta_region_peak"] = (
        region["peak"]
    )

    obj["ta_region_average"] = (
        region["average"]
    )

    obj["ta_region_stretch"] = (
        region["stretch"]
    )

    obj["ta_region_compression"] = (
        region["compression"]
    )

    obj["ta_region_collapse"] = (
        region["collapse"]
    )

    obj["ta_region_risk"] = (
        region["risk"]
    )

    obj["ta_region_cause"] = (
        region["cause"]
    )


# ============================================================
# HEATMAP
# ============================================================

def heatmap_color_from_score(
    score,
    threshold
):

    threshold = max(
        threshold,
        0.0001
    )

    # Blue -> Cyan
    if score <= threshold * 0.5:

        t = (
            score
            /
            (threshold * 0.5)
        )

        return (
            0.0,
            t,
            1.0,
            1.0
        )

    # Cyan -> Yellow
    if score <= threshold:

        t = (
            score
            - threshold * 0.5
        ) / (
            threshold * 0.5
        )

        return (
            t,
            1.0,
            1.0 - t,
            1.0
        )

    # Yellow -> Orange
    if score <= threshold * 2.0:

        t = (
            score - threshold
        ) / threshold

        return (
            1.0,
            1.0 - 0.5 * t,
            0.0,
            1.0
        )

    # Orange -> Red
    t = min(
        (
            score
            - threshold * 2.0
        ) / threshold,
        1.0
    )

    return (
        1.0,
        0.5 * (1.0 - t),
        0.0,
        1.0
    )


def get_or_create_heatmap_material():

    material = (
        bpy.data.materials.get(
            HEATMAP_MATERIAL
        )
    )

    if material is None:

        material = (
            bpy.data.materials.new(
                name=HEATMAP_MATERIAL
            )
        )

    material.use_nodes = True

    nodes = (
        material.node_tree.nodes
    )

    links = (
        material.node_tree.links
    )

    nodes.clear()

    output = nodes.new(
        "ShaderNodeOutputMaterial"
    )

    output.location = (
        400,
        0
    )

    emission = nodes.new(
        "ShaderNodeEmission"
    )

    emission.location = (
        150,
        0
    )

    emission.inputs[
        "Strength"
    ].default_value = 1.0

    attribute = nodes.new(
        "ShaderNodeAttribute"
    )

    attribute.location = (
        -150,
        0
    )

    attribute.attribute_name = (
        HEATMAP_ATTRIBUTE
    )

    links.new(
        attribute.outputs["Color"],
        emission.inputs["Color"]
    )

    links.new(
        emission.outputs["Emission"],
        output.inputs["Surface"]
    )

    return material


def ensure_heatmap_attribute(mesh):

    color_attribute = (
        mesh.color_attributes.get(
            HEATMAP_ATTRIBUTE
        )
    )

    if color_attribute is not None:

        if (
            color_attribute.domain != 'POINT'
            or
            color_attribute.data_type
            != 'FLOAT_COLOR'
        ):

            mesh.color_attributes.remove(
                color_attribute
            )

            color_attribute = None

    if color_attribute is None:

        color_attribute = (
            mesh.color_attributes.new(
                name=HEATMAP_ATTRIBUTE,
                type='FLOAT_COLOR',
                domain='POINT'
            )
        )

    mesh.color_attributes.active_color = (
        color_attribute
    )

    return color_attribute


# ============================================================
# MATERIAL BACKUP
# ============================================================

def backup_original_materials(obj):

    if BACKUP_MATERIALS_PROP in obj:
        return

    names = []

    for material in obj.data.materials:

        if material is None:
            names.append("")
        else:
            names.append(
                material.name
            )

    indices = [
        polygon.material_index
        for polygon in obj.data.polygons
    ]

    obj[BACKUP_MATERIALS_PROP] = (
        json.dumps(names)
    )

    obj[BACKUP_INDICES_PROP] = (
        json.dumps(indices)
    )


def assign_heatmap_material(obj):

    mesh = obj.data

    backup_original_materials(obj)

    material = (
        get_or_create_heatmap_material()
    )

    material_index = None

    for index, existing in enumerate(
        mesh.materials
    ):

        if existing == material:

            material_index = index
            break

    if material_index is None:

        mesh.materials.append(
            material
        )

        material_index = (
            len(mesh.materials) - 1
        )

    for polygon in mesh.polygons:

        polygon.material_index = (
            material_index
        )


def restore_original_materials(obj):

    if BACKUP_MATERIALS_PROP not in obj:
        return False

    try:

        names = json.loads(
            obj[BACKUP_MATERIALS_PROP]
        )

        indices = json.loads(
            obj[BACKUP_INDICES_PROP]
        )

    except Exception:

        return False

    mesh = obj.data

    mesh.materials.clear()

    for name in names:

        if not name:
            continue

        material = (
            bpy.data.materials.get(name)
        )

        if material is not None:

            mesh.materials.append(
                material
            )

    for index, polygon in enumerate(
        mesh.polygons
    ):

        if index >= len(indices):
            continue

        if len(mesh.materials) == 0:

            polygon.material_index = 0

        else:

            polygon.material_index = min(
                indices[index],
                len(mesh.materials) - 1
            )

    del obj[BACKUP_MATERIALS_PROP]
    del obj[BACKUP_INDICES_PROP]

    return True


def update_heatmap_colors(
    obj,
    scores,
    threshold,
    assign_material=False
):

    mesh = obj.data

    attribute = (
        ensure_heatmap_attribute(mesh)
    )

    if len(attribute.data) != len(scores):

        return False

    for index, score in enumerate(scores):

        attribute.data[index].color = (
            heatmap_color_from_score(
                score,
                threshold
            )
        )

    if assign_material:
        assign_heatmap_material(obj)

    mesh.update()

    return True


def create_heatmap(
    obj,
    scores,
    threshold
):

    return update_heatmap_colors(
        obj,
        scores,
        threshold,
        assign_material=True
    )


# ============================================================
# LIVE ANALYSIS
# ============================================================

def live_cache_key(obj):

    return obj.name_full


def rebuild_live_cache(obj):

    key = live_cache_key(obj)

    cache, error = (
        build_rest_cache(obj)
    )

    if error:

        return None, error

    TA_LIVE_CACHE[key] = cache

    return cache, None


def get_live_object(scene):

    object_name = (
        scene.ta_live_object_name
    )

    if not object_name:
        return None

    obj = bpy.data.objects.get(
        object_name
    )

    if obj is None:
        return None

    if obj.type != 'MESH':
        return None

    return obj


def update_live_analysis(
    scene,
    force=False
):

    global TA_LIVE_UPDATING

    if TA_LIVE_UPDATING:
        return

    if not scene.ta_live_analysis:
        return

    obj = get_live_object(scene)

    if obj is None:

        scene.ta_live_analysis = False

        print(
            "TA Live Analysis stopped: "
            "mesh object not found."
        )

        return

    interval = max(
        1,
        scene.ta_live_update_interval
    )

    key = live_cache_key(obj)

    cache = TA_LIVE_CACHE.get(key)

    if cache is None:

        cache, error = (
            rebuild_live_cache(obj)
        )

        if error:

            scene.ta_live_analysis = False

            print(
                "TA Live Analysis error:",
                error
            )

            return

    current_frame = (
        scene.frame_current
    )

    last_frame = (
        cache.get("last_frame")
    )

    if (
        not force
        and
        last_frame is not None
        and
        abs(
            current_frame
            -
            last_frame
        ) < interval
    ):
        return

    TA_LIVE_UPDATING = True

    try:

        results = (
            calculate_current_pose_scores(
                obj,
                cache
            )
        )

        if results["error"]:

            scene.ta_live_analysis = False

            print(
                "TA Live Analysis error:",
                results["error"]
            )

            return

        mode = (
            scene.ta_analysis_mode
        )

        threshold = (
            scene.ta_distortion_threshold
        )

        scores = (
            get_live_mode_scores(
                results,
                mode
            )
        )

        update_heatmap_colors(
            obj,
            scores,
            threshold,
            assign_material=True
        )

        high_count = sum(
            1
            for score in scores
            if score >= threshold
        )

        max_score = (
            max(scores)
            if scores
            else 0.0
        )

        average_score = (
            sum(scores) / len(scores)
            if scores
            else 0.0
        )

        obj[
            "ta_high_distortion_count"
        ] = high_count

        obj[
            "ta_max_distortion"
        ] = max_score

        obj[
            "ta_average_distortion"
        ] = average_score

        obj[
            "ta_last_analysis_mode"
        ] = mode

        cache["last_frame"] = (
            current_frame
        )

    finally:

        TA_LIVE_UPDATING = False


@persistent
def ta_live_frame_handler(
    scene,
    depsgraph=None
):

    update_live_analysis(
        scene,
        force=False
    )


# ============================================================
# WEIGHT OPERATORS
# ============================================================

class TA_OT_ScanWeights(
    bpy.types.Operator
):

    bl_idname = "ta.scan_weights"
    bl_label = "Scan Skin Weights"

    def execute(self, context):

        obj = get_active_mesh()

        if obj is None:

            self.report(
                {'ERROR'},
                "Please select a mesh."
            )

            return {'CANCELLED'}

        results = scan_weights(obj)

        if results["error"]:

            self.report(
                {'ERROR'},
                results["error"]
            )

            return {'CANCELLED'}

        obj["ta_unweighted_count"] = (
            len(results["unweighted"])
        )

        obj["ta_toomany_count"] = (
            len(results["too_many"])
        )

        obj["ta_tiny_count"] = (
            len(results["tiny"])
        )

        obj["ta_rawsum_count"] = (
            len(results["raw_sum"])
        )

        self.report(
            {'INFO'},
            "Weight scan complete."
        )

        return {'FINISHED'}


class TA_OT_SelectUnweighted(
    bpy.types.Operator
):

    bl_idname = "ta.select_unweighted"
    bl_label = "Select Unweighted"

    def execute(self, context):

        obj = get_active_mesh()

        if obj is None:
            return {'CANCELLED'}

        results = scan_weights(obj)

        if results["error"]:
            return {'CANCELLED'}

        select_vertices(
            obj,
            results["unweighted"]
        )

        return {'FINISHED'}


class TA_OT_SelectTooMany(
    bpy.types.Operator
):

    bl_idname = "ta.select_too_many"
    bl_label = "Select >4 Influences"

    def execute(self, context):

        obj = get_active_mesh()

        if obj is None:
            return {'CANCELLED'}

        results = scan_weights(obj)

        if results["error"]:
            return {'CANCELLED'}

        select_vertices(
            obj,
            results["too_many"]
        )

        return {'FINISHED'}


class TA_OT_SelectTiny(
    bpy.types.Operator
):

    bl_idname = "ta.select_tiny"
    bl_label = "Select Tiny Influences"

    def execute(self, context):

        obj = get_active_mesh()

        if obj is None:
            return {'CANCELLED'}

        results = scan_weights(obj)

        if results["error"]:
            return {'CANCELLED'}

        select_vertices(
            obj,
            results["tiny"]
        )

        return {'FINISHED'}


# ============================================================
# STANDARD ANALYSIS OPERATORS
# ============================================================

class TA_OT_AnalyzePose(
    bpy.types.Operator
):

    bl_idname = "ta.analyze_pose"
    bl_label = "Analyze Current Pose"

    def execute(self, context):

        obj = get_active_mesh()

        if obj is None:

            self.report(
                {'ERROR'},
                "Please select a mesh."
            )

            return {'CANCELLED'}

        threshold = (
            context.scene
            .ta_distortion_threshold
        )

        mode = (
            context.scene
            .ta_analysis_mode
        )

        results = (
            analyze_distortion(
                obj,
                threshold
            )
        )

        if results["error"]:

            self.report(
                {'ERROR'},
                results["error"]
            )

            return {'CANCELLED'}

        (
            scores,
            high_vertices,
            max_score,
            avg_score
        ) = get_mode_scores(
            results,
            mode
        )

        obj[
            "ta_high_distortion_count"
        ] = len(high_vertices)

        obj[
            "ta_max_distortion"
        ] = max_score

        obj[
            "ta_average_distortion"
        ] = avg_score

        obj[
            "ta_last_analysis_mode"
        ] = mode

        return {'FINISHED'}


class TA_OT_SelectHigh(
    bpy.types.Operator
):

    bl_idname = (
        "ta.select_high_distortion"
    )

    bl_label = (
        "Select High Distortion"
    )

    def execute(self, context):

        obj = get_active_mesh()

        if obj is None:
            return {'CANCELLED'}

        results = analyze_distortion(
            obj,
            context.scene
            .ta_distortion_threshold
        )

        if results["error"]:
            return {'CANCELLED'}

        (
            _,
            high_vertices,
            _,
            _
        ) = get_mode_scores(
            results,
            context.scene.ta_analysis_mode
        )

        select_vertices(
            obj,
            high_vertices
        )

        return {'FINISHED'}


class TA_OT_Heatmap(
    bpy.types.Operator
):

    bl_idname = "ta.create_heatmap"
    bl_label = "Create Distortion Heatmap"

    def execute(self, context):

        obj = get_active_mesh()

        if obj is None:
            return {'CANCELLED'}

        threshold = (
            context.scene
            .ta_distortion_threshold
        )

        results = analyze_distortion(
            obj,
            threshold
        )

        if results["error"]:

            self.report(
                {'ERROR'},
                results["error"]
            )

            return {'CANCELLED'}

        (
            scores,
            high_vertices,
            max_score,
            avg_score
        ) = get_mode_scores(
            results,
            context.scene.ta_analysis_mode
        )

        create_heatmap(
            obj,
            scores,
            threshold
        )

        obj[
            "ta_high_distortion_count"
        ] = len(high_vertices)

        obj[
            "ta_max_distortion"
        ] = max_score

        obj[
            "ta_average_distortion"
        ] = avg_score

        obj[
            "ta_last_analysis_mode"
        ] = context.scene.ta_analysis_mode

        return {'FINISHED'}


class TA_OT_Restore(
    bpy.types.Operator
):

    bl_idname = "ta.restore_materials"
    bl_label = "Restore Original Materials"

    def execute(self, context):

        context.scene.ta_live_analysis = False

        obj = get_active_mesh()

        if obj is None:

            obj = get_live_object(
                context.scene
            )

        if obj is None:
            return {'CANCELLED'}

        if not restore_original_materials(
            obj
        ):

            self.report(
                {'WARNING'},
                "No material backup found."
            )

            return {'CANCELLED'}

        return {'FINISHED'}


# ============================================================
# LIVE OPERATORS
# ============================================================

class TA_OT_StartLiveAnalysis(
    bpy.types.Operator
):

    bl_idname = (
        "ta.start_live_analysis"
    )

    bl_label = (
        "Start Live Analysis"
    )

    def execute(self, context):

        obj = get_active_mesh()

        if obj is None:

            self.report(
                {'ERROR'},
                "Select the skinned mesh first."
            )

            return {'CANCELLED'}

        cache, error = (
            rebuild_live_cache(obj)
        )

        if error:

            self.report(
                {'ERROR'},
                error
            )

            return {'CANCELLED'}

        context.scene.ta_live_object_name = (
            obj.name
        )

        context.scene.ta_live_analysis = (
            True
        )

        update_live_analysis(
            context.scene,
            force=True
        )

        self.report(
            {'INFO'},
            (
                "Live deformation analysis "
                "started."
            )
        )

        return {'FINISHED'}


class TA_OT_StopLiveAnalysis(
    bpy.types.Operator
):

    bl_idname = (
        "ta.stop_live_analysis"
    )

    bl_label = (
        "Stop Live Analysis"
    )

    def execute(self, context):

        context.scene.ta_live_analysis = (
            False
        )

        self.report(
            {'INFO'},
            "Live analysis stopped."
        )

        return {'FINISHED'}


class TA_OT_RebuildLiveCache(
    bpy.types.Operator
):

    bl_idname = (
        "ta.rebuild_live_cache"
    )

    bl_label = (
        "Rebuild Live Cache"
    )

    def execute(self, context):

        obj = get_live_object(
            context.scene
        )

        if obj is None:

            obj = get_active_mesh()

        if obj is None:

            self.report(
                {'ERROR'},
                "No mesh selected."
            )

            return {'CANCELLED'}

        cache, error = (
            rebuild_live_cache(obj)
        )

        if error:

            self.report(
                {'ERROR'},
                error
            )

            return {'CANCELLED'}

        context.scene.ta_live_object_name = (
            obj.name
        )

        if (
            context.scene
            .ta_live_analysis
        ):

            update_live_analysis(
                context.scene,
                force=True
            )

        self.report(
            {'INFO'},
            "Live cache rebuilt."
        )

        return {'FINISHED'}


class TA_OT_UpdateLiveNow(
    bpy.types.Operator
):

    bl_idname = "ta.update_live_now"
    bl_label = "Refresh Live Heatmap"

    def execute(self, context):

        if not (
            context.scene
            .ta_live_analysis
        ):

            self.report(
                {'WARNING'},
                "Live Analysis is not running."
            )

            return {'CANCELLED'}

        update_live_analysis(
            context.scene,
            force=True
        )

        return {'FINISHED'}


# ============================================================
# REGION OPERATORS
# ============================================================

class TA_OT_FindRegions(
    bpy.types.Operator
):

    bl_idname = (
        "ta.find_problem_regions"
    )

    bl_label = (
        "Find Problem Regions"
    )

    def execute(self, context):

        obj = get_active_mesh()

        if obj is None:

            obj = get_live_object(
                context.scene
            )

        if obj is None:
            return {'CANCELLED'}

        threshold = (
            context.scene
            .ta_distortion_threshold
        )

        results = analyze_distortion(
            obj,
            threshold
        )

        if results["error"]:

            self.report(
                {'ERROR'},
                results["error"]
            )

            return {'CANCELLED'}

        regions = create_region_data(
            obj,
            results,
            threshold
        )

        save_regions(
            obj,
            regions
        )

        obj[
            "ta_region_count"
        ] = len(regions)

        if regions:

            context.scene.ta_region_index = 1

            update_region_inspector(
                obj,
                regions[0]
            )

        self.report(
            {'INFO'},
            (
                f"Found {len(regions)} "
                "problem regions."
            )
        )

        return {'FINISHED'}


class TA_OT_SelectRegion(
    bpy.types.Operator
):

    bl_idname = (
        "ta.select_problem_region"
    )

    bl_label = (
        "Select Region"
    )

    def execute(self, context):

        obj = get_active_mesh()

        if obj is None:

            obj = get_live_object(
                context.scene
            )

        if obj is None:
            return {'CANCELLED'}

        regions = load_regions(obj)

        if not regions:

            self.report(
                {'WARNING'},
                (
                    "Run Find Problem "
                    "Regions first."
                )
            )

            return {'CANCELLED'}

        index = (
            context.scene
            .ta_region_index - 1
        )

        index = max(
            0,
            min(
                index,
                len(regions) - 1
            )
        )

        region = regions[index]

        update_region_inspector(
            obj,
            region
        )

        select_vertices(
            obj,
            region["vertices"]
        )

        return {'FINISHED'}


# ============================================================
# PANEL
# ============================================================

class TA_PT_MainPanel(
    bpy.types.Panel
):

    bl_label = "TA Tools"
    bl_idname = "TA_PT_main_panel"

    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "TA Tools"

    def draw(self, context):

        layout = self.layout

        obj = get_active_mesh()

        if obj is None:

            obj = get_live_object(
                context.scene
            )

        # ====================================================
        # WEIGHT VALIDATION
        # ====================================================

        box = layout.box()

        box.label(
            text="Weight Validation",
            icon='MOD_VERTEX_WEIGHT'
        )

        box.operator(
            "ta.scan_weights",
            icon='VIEWZOOM'
        )

        box.operator(
            "ta.select_unweighted"
        )

        box.operator(
            "ta.select_too_many"
        )

        box.operator(
            "ta.select_tiny"
        )

        if (
            obj is not None
            and
            "ta_unweighted_count" in obj
        ):

            box.separator()

            box.label(
                text=(
                    "Unweighted: "
                    f"{obj['ta_unweighted_count']}"
                )
            )

            box.label(
                text=(
                    ">4 Influences: "
                    f"{obj['ta_toomany_count']}"
                )
            )

            box.label(
                text=(
                    "Tiny Influences: "
                    f"{obj['ta_tiny_count']}"
                )
            )

            box.label(
                text=(
                    "Raw Sum Warnings: "
                    f"{obj['ta_rawsum_count']}"
                )
            )

        # ====================================================
        # DEFORMATION ANALYSIS
        # ====================================================

        box = layout.box()

        box.label(
            text="Deformation Analysis",
            icon='ARMATURE_DATA'
        )

        box.label(
            text="Reference: Armature Rest Pose"
        )

        box.prop(
            context.scene,
            "ta_analysis_mode",
            text="Mode"
        )

        box.prop(
            context.scene,
            "ta_distortion_threshold",
            text="Threshold"
        )

        box.operator(
            "ta.analyze_pose",
            icon='VIEWZOOM'
        )

        box.operator(
            "ta.select_high_distortion"
        )

        box.operator(
            "ta.create_heatmap",
            icon='GROUP_VCOL'
        )

        box.operator(
            "ta.restore_materials",
            icon='LOOP_BACK'
        )

        if (
            obj is not None
            and
            "ta_high_distortion_count" in obj
        ):

            box.separator()

            box.label(
                text=(
                    "Last Mode: "
                    f"{obj.get('ta_last_analysis_mode', 'N/A')}"
                )
            )

            box.label(
                text=(
                    "High Distortion: "
                    f"{obj['ta_high_distortion_count']}"
                )
            )

            box.label(
                text=(
                    "Maximum: "
                    f"{obj['ta_max_distortion']:.3f}"
                )
            )

            box.label(
                text=(
                    "Average: "
                    f"{obj['ta_average_distortion']:.3f}"
                )
            )

        # ====================================================
        # LIVE ANALYSIS
        # ====================================================

        box = layout.box()

        box.label(
            text="Live Deformation Analysis",
            icon='PLAY'
        )

        if (
            context.scene
            .ta_live_analysis
        ):

            box.label(
                text="LIVE",
                icon='REC'
            )

            if (
                context.scene
                .ta_live_object_name
            ):

                box.label(
                    text=(
                        "Mesh: "
                        +
                        context.scene
                        .ta_live_object_name
                    )
                )

            box.operator(
                "ta.stop_live_analysis",
                icon='PAUSE'
            )

            box.operator(
                "ta.update_live_now",
                icon='FILE_REFRESH'
            )

        else:

            box.operator(
                "ta.start_live_analysis",
                icon='PLAY'
            )

        box.prop(
            context.scene,
            "ta_live_update_interval",
            text="Update Every"
        )

        box.label(
            text="frame(s)"
        )

        box.operator(
            "ta.rebuild_live_cache",
            icon='FILE_REFRESH'
        )

        box.separator()

        box.label(
            text=(
                "Play the Timeline to watch "
                "the heatmap update."
            )
        )

        # ====================================================
        # PROBLEM REGIONS
        # ====================================================

        box = layout.box()

        box.label(
            text="Problem Regions",
            icon='ERROR'
        )

        box.operator(
            "ta.find_problem_regions",
            icon='VIEWZOOM'
        )

        if (
            obj is not None
            and
            obj.get(
                "ta_region_count",
                0
            ) > 0
        ):

            region_count = (
                obj["ta_region_count"]
            )

            box.prop(
                context.scene,
                "ta_region_index",
                text="Region"
            )

            box.label(
                text=(
                    "Regions Found: "
                    f"{region_count}"
                )
            )

            box.operator(
                "ta.select_problem_region",
                icon='RESTRICT_SELECT_OFF'
            )

            box.separator()

            box.label(
                text="Region Inspector"
            )

            box.label(
                text=(
                    "Vertices: "
                    f"{obj.get('ta_region_vertex_count', 0)}"
                )
            )

            box.label(
                text=(
                    "Risk: "
                    f"{obj.get('ta_region_risk', 'N/A')}"
                )
            )

            box.label(
                text=(
                    "Peak Score: "
                    f"{obj.get('ta_region_peak', 0.0):.3f}"
                )
            )

            box.label(
                text=(
                    "Average: "
                    f"{obj.get('ta_region_average', 0.0):.3f}"
                )
            )

            box.separator()

            box.label(
                text=(
                    "Stretch: "
                    f"{obj.get('ta_region_stretch', 0.0):.3f}"
                )
            )

            box.label(
                text=(
                    "Compression: "
                    f"{obj.get('ta_region_compression', 0.0):.3f}"
                )
            )

            box.label(
                text=(
                    "Collapse: "
                    f"{obj.get('ta_region_collapse', 0.0):.3f}"
                )
            )

            box.separator()

            box.label(
                text="Primary Cause:"
            )

            box.label(
                text=(
                    obj.get(
                        "ta_region_cause",
                        "N/A"
                    )
                )
            )


# ============================================================
# REGISTRATION
# ============================================================

classes = (

    TA_OT_ScanWeights,
    TA_OT_SelectUnweighted,
    TA_OT_SelectTooMany,
    TA_OT_SelectTiny,

    TA_OT_AnalyzePose,
    TA_OT_SelectHigh,
    TA_OT_Heatmap,
    TA_OT_Restore,

    TA_OT_StartLiveAnalysis,
    TA_OT_StopLiveAnalysis,
    TA_OT_RebuildLiveCache,
    TA_OT_UpdateLiveNow,

    TA_OT_FindRegions,
    TA_OT_SelectRegion,

    TA_PT_MainPanel,
)


def remove_existing_handlers():

    handlers = (
        bpy.app.handlers
        .frame_change_post
    )

    for handler in list(handlers):

        if (
            getattr(
                handler,
                "__name__",
                ""
            )
            ==
            "ta_live_frame_handler"
        ):

            handlers.remove(handler)


def remove_old_class(cls):

    old = getattr(
        bpy.types,
        cls.__name__,
        None
    )

    if old is not None:

        try:

            bpy.utils.unregister_class(
                old
            )

        except Exception:
            pass


def register():

    global TA_LIVE_CACHE

    TA_LIVE_CACHE.clear()

    remove_existing_handlers()

    for cls in classes:
        remove_old_class(cls)

    property_names = [

        "ta_distortion_threshold",
        "ta_analysis_mode",
        "ta_region_index",

        "ta_live_analysis",
        "ta_live_update_interval",
        "ta_live_object_name",
    ]

    for property_name in property_names:

        if hasattr(
            bpy.types.Scene,
            property_name
        ):

            delattr(
                bpy.types.Scene,
                property_name
            )

    # --------------------------------------------------------
    # Threshold
    # --------------------------------------------------------

    bpy.types.Scene.ta_distortion_threshold = (
        bpy.props.FloatProperty(

            name="Distortion Threshold",

            description=(
                "Score above which vertices "
                "are flagged for inspection"
            ),

            default=0.20,

            min=0.01,

            max=1.50,

            step=5,

            precision=2
        )
    )

    # --------------------------------------------------------
    # Analysis mode
    # --------------------------------------------------------

    bpy.types.Scene.ta_analysis_mode = (
        bpy.props.EnumProperty(

            name="Analysis Mode",

            items=[

                (
                    'COMBINED',
                    'Combined',
                    (
                        "Stretch and "
                        "compression"
                    )
                ),

                (
                    'STRETCH',
                    'Stretch',
                    (
                        "Local edge "
                        "stretching"
                    )
                ),

                (
                    'COMPRESSION',
                    'Compression',
                    (
                        "Local edge "
                        "compression"
                    )
                ),

                (
                    'SURFACE_COLLAPSE',
                    'Surface Collapse',
                    (
                        "Triangle surface "
                        "area loss"
                    )
                ),

                (
                    'PROBLEM',
                    'Problem Areas',
                    (
                        "Combined diagnostic "
                        "problem score"
                    )
                ),
            ],

            default='PROBLEM'
        )
    )

    # --------------------------------------------------------
    # Problem Region
    # --------------------------------------------------------

    bpy.types.Scene.ta_region_index = (
        bpy.props.IntProperty(

            name="Problem Region",

            default=1,

            min=1
        )
    )

    # --------------------------------------------------------
    # Live
    # --------------------------------------------------------

    bpy.types.Scene.ta_live_analysis = (
        bpy.props.BoolProperty(

            name="Live Analysis",

            default=False
        )
    )

    bpy.types.Scene.ta_live_update_interval = (
        bpy.props.IntProperty(

            name="Live Update Interval",

            description=(
                "Update heatmap every N frames"
            ),

            default=2,

            min=1,

            max=10
        )
    )

    bpy.types.Scene.ta_live_object_name = (
        bpy.props.StringProperty(

            name="Live Mesh",

            default=""
        )
    )

    # --------------------------------------------------------
    # Classes
    # --------------------------------------------------------

    for cls in classes:

        bpy.utils.register_class(
            cls
        )

    # --------------------------------------------------------
    # Handler
    # --------------------------------------------------------

    bpy.app.handlers.frame_change_post.append(
        ta_live_frame_handler
    )


def unregister():

    remove_existing_handlers()

    for cls in reversed(classes):

        try:

            bpy.utils.unregister_class(
                cls
            )

        except Exception:
            pass

    property_names = [

        "ta_distortion_threshold",
        "ta_analysis_mode",
        "ta_region_index",

        "ta_live_analysis",
        "ta_live_update_interval",
        "ta_live_object_name",
    ]

    for property_name in property_names:

        if hasattr(
            bpy.types.Scene,
            property_name
        ):

            delattr(
                bpy.types.Scene,
                property_name
            )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    register()