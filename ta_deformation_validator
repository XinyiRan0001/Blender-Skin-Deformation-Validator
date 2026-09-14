import bpy
import json


# ============================================================
# TA SKIN / DEFORMATION VALIDATOR
# ============================================================


# ============================================================
# SETTINGS
# ============================================================

# Weight validation
MAX_INFLUENCES = 4
TINY_RELATIVE_WEIGHT = 0.01
RAW_SUM_TOLERANCE = 0.01

# Local deformation score
AVERAGE_WEIGHT = 0.70
PEAK_WEIGHT = 0.30

# Problem score
PROBLEM_STRETCH_WEIGHT = 0.20
PROBLEM_COMPRESSION_WEIGHT = 0.30
PROBLEM_COLLAPSE_WEIGHT = 0.50
PROBLEM_INTERACTION_WEIGHT = 0.35

# Heatmap
HEATMAP_ATTRIBUTE = "TA_Distortion"
HEATMAP_MATERIAL = "TA_Distortion_Heatmap"

# Material backup
BACKUP_MATERIALS_PROP = "ta_original_material_names"
BACKUP_INDICES_PROP = "ta_original_material_indices"

# Region storage
REGIONS_PROP = "ta_problem_regions"


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

    depsgraph = (
        bpy.context.evaluated_depsgraph_get()
    )

    eval_obj = obj.evaluated_get(
        depsgraph
    )

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

            obj.data.vertices[
                index
            ].select = True

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

    deform_groups = (
        get_deform_group_indices(obj)
    )

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

            unweighted.append(
                vertex.index
            )

            continue

        total = sum(
            weight
            for _, weight in weights
        )

        if (
            abs(total - 1.0)
            > RAW_SUM_TOLERANCE
        ):

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
# REST / CURRENT POSE
# ============================================================

def get_rest_and_current_positions(obj):

    armature = get_armature(obj)

    if armature is None:

        return (
            None,
            None,
            "No Armature Modifier found."
        )

    original_state = (
        armature.data.pose_position
    )

    rest = None
    current = None

    try:

        armature.data.pose_position = 'REST'
        bpy.context.view_layer.update()

        rest = get_evaluated_positions(obj)

        armature.data.pose_position = 'POSE'
        bpy.context.view_layer.update()

        current = get_evaluated_positions(obj)

    finally:

        armature.data.pose_position = (
            original_state
        )

        bpy.context.view_layer.update()

    if rest is None or current is None:

        return (
            None,
            None,
            "Could not evaluate mesh."
        )

    if len(rest) != len(current):

        return (
            None,
            None,
            "Topology changes between Rest and Pose."
        )

    if len(rest) != len(obj.data.vertices):

        return (
            None,
            None,
            "Evaluated topology differs from base mesh."
        )

    return rest, current, None


# ============================================================
# SCORE HELPERS
# ============================================================

def blended_local_score(values):

    if not values:
        return 0.0

    average = (
        sum(values)
        / len(values)
    )

    peak = max(values)

    return (
        average * AVERAGE_WEIGHT
        +
        peak * PEAK_WEIGHT
    )


def triangle_area(a, b, c):

    return (
        (b - a).cross(
            c - a
        ).length
        * 0.5
    )


# ============================================================
# SURFACE COLLAPSE
# ============================================================

def calculate_surface_collapse(
    mesh,
    rest_positions,
    current_positions
):

    mesh.calc_loop_triangles()

    local_values = {
        vertex.index: []
        for vertex in mesh.vertices
    }

    for tri in mesh.loop_triangles:

        a, b, c = tri.vertices

        rest_area = triangle_area(
            rest_positions[a],
            rest_positions[b],
            rest_positions[c]
        )

        if rest_area <= 0.0000001:
            continue

        current_area = triangle_area(
            current_positions[a],
            current_positions[b],
            current_positions[c]
        )

        ratio = (
            current_area
            / rest_area
        )

        collapse = max(
            0.0,
            1.0 - ratio
        )

        collapse = min(
            collapse,
            1.0
        )

        local_values[a].append(
            collapse
        )

        local_values[b].append(
            collapse
        )

        local_values[c].append(
            collapse
        )

    scores = [
        0.0
        for _ in mesh.vertices
    ]

    for index, values in (
        local_values.items()
    ):

        scores[index] = (
            blended_local_score(
                values
            )
        )

    return scores


# ============================================================
# PROBLEM SCORE
# ============================================================

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
            stretch
            * PROBLEM_STRETCH_WEIGHT

            +

            compression
            * PROBLEM_COMPRESSION_WEIGHT

            +

            collapse
            * PROBLEM_COLLAPSE_WEIGHT
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
# DEFORMATION ANALYSIS
# ============================================================

def analyze_distortion(
    obj,
    threshold
):

    (
        rest_positions,
        current_positions,
        error
    ) = get_rest_and_current_positions(
        obj
    )

    if error:

        return {
            "error": error
        }

    mesh = obj.data

    neighbors = {
        vertex.index: set()
        for vertex in mesh.vertices
    }

    for edge in mesh.edges:

        a, b = edge.vertices

        neighbors[a].add(b)
        neighbors[b].add(a)

    count = len(mesh.vertices)

    combined_scores = [0.0] * count
    stretch_scores = [0.0] * count
    compression_scores = [0.0] * count

    # --------------------------------------------------------
    # Edge deformation
    # --------------------------------------------------------

    for vertex in mesh.vertices:

        index = vertex.index

        local_combined = []
        local_stretch = []
        local_compression = []

        for neighbor in neighbors[index]:

            rest_vec = (
                rest_positions[neighbor]
                -
                rest_positions[index]
            )

            current_vec = (
                current_positions[neighbor]
                -
                current_positions[index]
            )

            rest_length = (
                rest_vec.length
            )

            current_length = (
                current_vec.length
            )

            if rest_length <= 0.000001:
                continue

            ratio = (
                current_length
                / rest_length
            )

            stretch = 0.0
            compression = 0.0

            if ratio > 1.0:

                stretch = (
                    ratio - 1.0
                )

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

            local_stretch.append(
                stretch
            )

            local_compression.append(
                compression
            )

            local_combined.append(
                combined
            )

        stretch_scores[index] = (
            blended_local_score(
                local_stretch
            )
        )

        compression_scores[index] = (
            blended_local_score(
                local_compression
            )
        )

        combined_scores[index] = (
            blended_local_score(
                local_combined
            )
        )

    # --------------------------------------------------------
    # Surface collapse
    # --------------------------------------------------------

    collapse_scores = (
        calculate_surface_collapse(
            mesh,
            rest_positions,
            current_positions
        )
    )

    # --------------------------------------------------------
    # Problem score
    # --------------------------------------------------------

    problem_scores = (
        calculate_problem_scores(
            stretch_scores,
            compression_scores,
            collapse_scores
        )
    )

    def high(scores):

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
            sum(values)
            / len(values)
            if values
            else 0.0
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

        "high_combined":
            high(combined_scores),

        "high_stretch":
            high(stretch_scores),

        "high_compression":
            high(compression_scores),

        "high_collapse":
            high(collapse_scores),

        "high_problem":
            high(problem_scores),

        "max_combined":
            safe_max(combined_scores),

        "max_stretch":
            safe_max(stretch_scores),

        "max_compression":
            safe_max(compression_scores),

        "max_collapse":
            safe_max(collapse_scores),

        "max_problem":
            safe_max(problem_scores),

        "avg_combined":
            safe_average(combined_scores),

        "avg_stretch":
            safe_average(stretch_scores),

        "avg_compression":
            safe_average(compression_scores),

        "avg_collapse":
            safe_average(collapse_scores),

        "avg_problem":
            safe_average(problem_scores),
    }


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
        mapping['COMBINED']
    )

    return (
        results[keys[0]],
        results[keys[1]],
        results[keys[2]],
        results[keys[3]],
    )


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

    problem_vertices = set(
        index
        for index, score
        in enumerate(
            results["problem_scores"]
        )
        if score >= threshold
    )

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
                    neighbor
                    in problem_vertices
                    and
                    neighbor
                    not in visited
                ):

                    visited.add(
                        neighbor
                    )

                    stack.append(
                        neighbor
                    )

        if region_vertices:

            regions.append(
                region_vertices
            )

    # Ignore isolated single vertices.
    regions = [
        region
        for region in regions
        if len(region) >= 2
    ]

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

    # Compression + collapse together
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

    # Most serious first
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

    obj[
        REGIONS_PROP
    ] = json.dumps(
        regions
    )


def load_regions(obj):

    if REGIONS_PROP not in obj:
        return []

    try:

        return json.loads(
            obj[
                REGIONS_PROP
            ]
        )

    except Exception:

        return []


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

    if score <= threshold * 2.0:

        t = (
            score
            - threshold
        ) / threshold

        return (
            1.0,
            1.0 - 0.5 * t,
            0.0,
            1.0
        )

    t = min(
        (
            score
            - threshold * 2.0
        ) / threshold,
        1.0
    )

    return (
        1.0,
        0.5 * (
            1.0 - t
        ),
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

    obj[
        BACKUP_MATERIALS_PROP
    ] = json.dumps(
        names
    )

    obj[
        BACKUP_INDICES_PROP
    ] = json.dumps(
        indices
    )


def restore_original_materials(obj):

    if (
        BACKUP_MATERIALS_PROP
        not in obj
    ):

        return False

    try:

        names = json.loads(
            obj[
                BACKUP_MATERIALS_PROP
            ]
        )

        indices = json.loads(
            obj[
                BACKUP_INDICES_PROP
            ]
        )

    except Exception:

        return False

    mesh = obj.data

    mesh.materials.clear()

    for name in names:

        if not name:
            continue

        material = (
            bpy.data.materials.get(
                name
            )
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

    del obj[
        BACKUP_MATERIALS_PROP
    ]

    del obj[
        BACKUP_INDICES_PROP
    ]

    return True


def create_heatmap(
    obj,
    scores,
    threshold
):

    mesh = obj.data

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

    for index, score in enumerate(
        scores
    ):

        color_attribute.data[
            index
        ].color = (
            heatmap_color_from_score(
                score,
                threshold
            )
        )

    mesh.color_attributes.active_color = (
        color_attribute
    )

    backup_original_materials(
        obj
    )

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
            len(mesh.materials)
            - 1
        )

    for polygon in mesh.polygons:

        polygon.material_index = (
            material_index
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
            len(
                results["unweighted"]
            )
        )

        obj["ta_toomany_count"] = (
            len(
                results["too_many"]
            )
        )

        obj["ta_tiny_count"] = (
            len(
                results["tiny"]
            )
        )

        obj["ta_rawsum_count"] = (
            len(
                results["raw_sum"]
            )
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
# DEFORMATION OPERATORS
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
        ] = len(
            high_vertices
        )

        obj[
            "ta_max_distortion"
        ] = max_score

        obj[
            "ta_average_distortion"
        ] = avg_score

        obj[
            "ta_last_analysis_mode"
        ] = mode

        self.report(
            {'INFO'},
            (
                f"{mode}: "
                f"{len(high_vertices)} warnings"
            )
        )

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

        threshold = (
            context.scene
            .ta_distortion_threshold
        )

        mode = (
            context.scene
            .ta_analysis_mode
        )

        results = analyze_distortion(
            obj,
            threshold
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
            mode
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

        mode = (
            context.scene
            .ta_analysis_mode
        )

        results = analyze_distortion(
            obj,
            threshold
        )

        if results["error"]:
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

        create_heatmap(
            obj,
            scores,
            threshold
        )

        obj[
            "ta_high_distortion_count"
        ] = len(
            high_vertices
        )

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


class TA_OT_Restore(
    bpy.types.Operator
):

    bl_idname = "ta.restore_materials"
    bl_label = "Restore Original Materials"

    def execute(self, context):

        obj = get_active_mesh()

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
# REGION OPERATORS
# ============================================================

class TA_OT_FindRegions(
    bpy.types.Operator
):

    bl_idname = "ta.find_problem_regions"
    bl_label = "Find Problem Regions"

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
                f"Found "
                f"{len(regions)} "
                "problem regions."
            )
        )

        return {'FINISHED'}


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


class TA_OT_SelectRegion(
    bpy.types.Operator
):

    bl_idname = "ta.select_problem_region"
    bl_label = "Select Region"

    def execute(self, context):

        obj = get_active_mesh()

        if obj is None:
            return {'CANCELLED'}

        regions = load_regions(
            obj
        )

        if not regions:

            self.report(
                {'WARNING'},
                "Run Find Problem Regions first."
            )

            return {'CANCELLED'}

        index = (
            context.scene
            .ta_region_index
            - 1
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
            "ta_unweighted_count"
            in obj
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
        # DEFORMATION
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
            "ta_high_distortion_count"
            in obj
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
                obj[
                    "ta_region_count"
                ]
            )

            box.prop(
                context.scene,
                "ta_region_index",
                text="Region"
            )

            box.label(
                text=(
                    f"Regions Found: "
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

    TA_OT_FindRegions,
    TA_OT_SelectRegion,

    TA_PT_MainPanel,
)


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

    # --------------------------------------------------------
    # Clean old classes
    # --------------------------------------------------------

    for cls in classes:
        remove_old_class(cls)

    # --------------------------------------------------------
    # Clean old properties
    # --------------------------------------------------------

    for property_name in [
        "ta_distortion_threshold",
        "ta_analysis_mode",
        "ta_region_index",
    ]:

        if hasattr(
            bpy.types.Scene,
            property_name
        ):

            delattr(
                bpy.types.Scene,
                property_name
            )

    # --------------------------------------------------------
    # Distortion Threshold
    # --------------------------------------------------------

    bpy.types.Scene.ta_distortion_threshold = (
        bpy.props.FloatProperty(

            name="Distortion Threshold",

            description=(
                "Score above which vertices "
                "are flagged for inspection"
            ),

            default=0.20,

            min=0.05,

            max=1.50,

            step=5,

            precision=2
        )
    )

    # --------------------------------------------------------
    # Analysis Mode
    # --------------------------------------------------------

    bpy.types.Scene.ta_analysis_mode = (
        bpy.props.EnumProperty(

            name="Analysis Mode",

            items=[

                (
                    'COMBINED',
                    'Combined',
                    'Stretch and compression'
                ),

                (
                    'STRETCH',
                    'Stretch',
                    'Local edge stretching'
                ),

                (
                    'COMPRESSION',
                    'Compression',
                    'Local edge compression'
                ),

                (
                    'SURFACE_COLLAPSE',
                    'Surface Collapse',
                    'Surface area loss'
                ),

                (
                    'PROBLEM',
                    'Problem Areas',
                    (
                        'Combined diagnostic '
                        'problem score'
                    )
                ),
            ],

            default='PROBLEM'
        )
    )

    # --------------------------------------------------------
    # Region selector
    # --------------------------------------------------------

    bpy.types.Scene.ta_region_index = (
        bpy.props.IntProperty(

            name="Problem Region",

            default=1,

            min=1
        )
    )

    # --------------------------------------------------------
    # Register
    # --------------------------------------------------------

    for cls in classes:

        bpy.utils.register_class(
            cls
        )


def unregister():

    for cls in reversed(
        classes
    ):

        try:
            bpy.utils.unregister_class(
                cls
            )

        except Exception:
            pass

    for property_name in [
        "ta_distortion_threshold",
        "ta_analysis_mode",
        "ta_region_index",
    ]:

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