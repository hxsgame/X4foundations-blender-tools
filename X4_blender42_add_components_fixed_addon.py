import bpy
import bmesh
import math
import os
import numpy as np
import xml.etree.ElementTree as ET
from mathutils import Vector, Matrix, Quaternion

# ============================ v9.1 修复说明 ============================
# 1. 旋转矩阵三个分支统一为列向量 (X,Y,Z) 右手系构造，修复 align=Y 的反射矩阵(det=-1)
# 2. align_axis 现在真正把对应的局部轴对齐到面法线（原先三档都把局部 Z 放到法线上）
# 3. 法向抬升改用与法线对齐的那根轴对应的尺寸（原先固定用 Z 尺寸）
# 4. 三角形长轴改为取最长边方向（原"中轴"在细长三角形下指向短轴，差 90°）
# 5. 编辑模式 bmesh 只读使用、一次获取全程传递，移除所有对 from_edit_mesh 结果的 free()
# 6. "自定义"预设不再把占位名 customize 写入组件名
# 7. 放置后选中新组件并设为活动物体（便于立即平移），删除未使用的恢复选择死代码
#    【v9.2.1 起改为：放置后不选中新组件，自动切回主物体编辑模式，便于连续放置】
# 8. 枚举 items 改为动态回调、面板文案实时取词，切换 Blender 语言无需重载插件
# 9. 法线变换改用逆转置矩阵（兼容非均匀缩放）；反向面加权法线趋零时回退到最大面法线
# 10. 对称协方差矩阵改用 np.linalg.eigh；Scene 散属性收进 TranslationSettingsFixed

def get_blender_language():
    """获取Blender当前语言设置"""
    try:
        # Blender 4.2+ 的语言设置方式
        if hasattr(bpy.context, 'preferences') and bpy.context.preferences:
            language = bpy.context.preferences.view.language
            if language and language != 'auto':
                return language
    except Exception as e:
        print(f"语言检测错误: {e}")
    return 'en_US'  # 默认英文

def get_supported_language():
    """获取插件支持的语言"""
    current_lang = get_blender_language()

    # 支持的语言映射
    language_map = {
        'zh_CN': 'zh_CN',      # 简体中文
        'zh_HANS': 'zh_CN',    # 简体中文（变体）
        'zh_TW': 'zh_CN',      # 繁体中文使用简体中文
        'zh_HANT': 'zh_CN',    # 繁体中文使用简体中文
        'en_US': 'en_US',      # 美式英语
        'en_GB': 'en_US',      # 英式英语使用美式英语
    }

    # 检查语言代码前缀
    lang_prefix = current_lang.split('_')[0] if '_' in current_lang else current_lang
    for supported_prefix in ['zh', 'en']:
        if lang_prefix.startswith(supported_prefix):
            # 查找对应的完整语言代码
            for bl_lang, plugin_lang in language_map.items():
                if bl_lang.startswith(lang_prefix):
                    return plugin_lang

    return 'en_US'  # 默认英语

bl_info = {
    "name": "智能组件放置器-修正版(独立)",
    "version": (9, 16, 51),  # 曲面模式精简：删除曲线路径与BVH最近点，采纳面合并算法并改名为曲面路径（接触面积）
    "blender": (4, 2, 0),
    "location": "View3D > UI > 组件放置",
    "description": "使用几何算法精确计算长边方向的组件放置器，支持预设组件",
    "category": "Object",
}

# ============================ 默认值配置 ============================
# 所有面板参数的默认值集中管理，方便统一调整
DEFAULT_VALUES = {
    "COMP_NAME": "g_01-turret_l",
    "COMP_SIZE": (60.0, 60.0, 20.0),
    "PLACEMENT_MODE": "SINGLE",
    "ALIGN_AXIS": "Z",
    "FORWARD_AXIS": "Y+",
    "ARRANGE_DIRECTION": "LONG_EDGE",
    "SPACING": 78.0,  # = max(COMP_SIZE) * 1.3 = 60 * 1.3
    "AUTO_COUNT": False,
    "MANUAL_COUNT": 3,
    "SURFACE_OFFSET": 0.0,
    "FACE_X_OFFSET": 0.0,
    "FACE_Y_OFFSET": 0.0,
    "MOVE_X": 0.0,
    "MOVE_Y": 0.0,
    "MOVE_Z": 0.0
}

# ============================ 预设数据配置 ============================
# x，y，z 宽、长、高
COMPONENT_PRESETS = [
    ("自定义", (50.0, 50.0, 20.0), "customize"),
    ("航点", (0.1, 0.1, 0.1), "waypoint"),
    ("诱导发射点", (0.1, 0.1, 0.1), "countermeasures"),
    ("玩家控制点-p", (0.1, 0.1, 0.1), "playercontrol"),
    ("储存-p", (0.1, 0.1, 0.1), "storage_p"),
    ("房间", (17.0, 20.0, 7.0), "room"),
    ("生产模块-航母-p", (0.1, 0.1, 0.1), "buildmodule_gen_carrier_macro"),
    ("生产模块-补给舰-p", (0.1, 0.1, 0.1), "buildmodule_gen_resupplier_macro"),
    ("舰桥-l-par", (17.0, 25.0, 7.0), "bridge_par_l_expeditionary_01_macro"),
    ("舰桥-xl-par", (17.0, 25.0, 7.0), "bridge_par_xl_01_macro"),
    ("舰桥-l-bor", (17.0, 25.0, 7.0), "bridge_bor_l_01_macro"),
    ("舰桥-xl-bor", (17.0, 25.0, 7.0), "bridge_bor_xl_01_macro"),
    ("舰桥-l-ter", (17.0, 25.0, 7.0), "bridge_ter_l_02_macro"),
    ("舰桥-xl-ter", (17.0, 25.0, 7.0), "bridge_ter_xl_01_macro"),
    ("舰桥-l-spl", (17.0, 25.0, 7.0), "bridge_spl_l_01_macro"),
    ("舰桥-xl-spl", (17.0, 25.0, 7.0), "bridge_spl_xl_01_macro"),
    ("机库-xs-10", (0.1, 0.1, 0.1), "shipstorage_gen_xs_01_macro"),
    ("机库-s-15", (0.1, 0.1, 0.1), "shipstorage_disc_s_01_macro"),
    ("机库-s-28", (0.1, 0.1, 0.1), "shipstorage_gen_s_twentyeight_macro"),
    ("机库-s-72", (0.1, 0.1, 0.1), "shipstorage_gen_s_seventytwo_macro"),
    ("机库-s-100", (0.1, 0.1, 0.1), "shipstorage_gen_s_02_macro"),
    ("机库-m-10", (0.1, 0.1, 0.1), "shipstorage_gen_m_01_macro"),
    ("机库-m-30", (0.1, 0.1, 0.1), "shipstorage_gen_m_02_macro"),
    ("机库-sm-64", (0.1, 0.1, 0.1), "shipstorage_gen_sm_hidden_sixtyfour_macro"),
    ("机平-xs-2", (3.0, 3.0, 3.0), "dock_gen_xs_ship_02_macro"),
    ("机平-xs-flat", (3.0, 3.0, 3.0), "dock_gen_xs_ship_flat_macro"),
    ("机平-sh", (40.0, 45.0, 35.0), "dockarea_arg_s_ship_01_hightech_macro"),
    ("机平-mh", (115.0, 185.0, 115.0), "dockarea_arg_m_ship_01_hightech_macro"),
    ("机平-长-18sh", (120.0, 270.0, 35.0), "dockarea_ter_xl_carrier_01_macro"),
    ("机平-长-21s", (120.0, 315.0, 35.0), "dockarea_spl_xl_carrier_01_macro"),
    ("机平-长-1m2s", (115.0, 225.0, 115.0), "dockarea_tel_xl_carrier_01_macro"),
    ("机平-长-1m3s", (120.0, 230.0, 115.0), "dockarea_atf_xl_battleship_01_macro"),
    ("机平-长-2m2s", (115.0, 410.0, 115.0), "dockarea_arg_xl_resupplier_01_macro"),
    ("机平-长方-2m8s", (185.0, 323.0, 115.0), "dockarea_par_xl_resupplier_01_macro"),
    ("机平-长-spl-2m8s", (115.0, 550.0, 115.0), "dockarea_spl_xl_resupplier_01_macro"),
    ("机平-长-ter-2m8s", (115.0, 550.0, 115.0), "dockarea_ter_xl_resupplier_01_macro"),
    ("机平-长-4m", (185.0, 460.0, 115.0), "dockarea_arg_xl_carrier_01_macro"),
    ("机平-方-4m8s", (320.0, 370.0, 115.0), "dockarea_par_xl_carrier_01_macro"),
    ("主武器XL", (170.0, 950.0, 100.0), "weapon_xl"),
    ("主武器L", (25.0, 100.0, 15.0), "weapon_l"),
    ("超大型炮塔", (160.0, 160.0, 55.0), "turret_xl"),
    ("大型炮塔", (60.0, 60.0, 20.0), "turret_l"),
    ("中型炮塔", (20.0, 20.0, 5.0), "turret_m"),
    ("小型炮塔", (5.0, 5.0, 2.0), "turret_s"),
    ("超大型护盾", (200.0, 70.0, 15.0), "shield_xl"),
    ("大型护盾", (65.0, 25.0, 5.5), "shield_l"),
    ("中型护盾", (17.0, 5.0, 1.7), "shield_m"),
    ("小型护盾", (1.0, 1.0, 1.0), "shield_s"),
    ("超大型引擎", (150.0, 310.0, 150.0), "engine_xl"),
    ("大型引擎", (50.0, 150.0, 50.0), "engine_l"),
    ("中型引擎", (10.0, 50.0, 10.0), "engine_m"),
    ("小型引擎", (25.0, 13.0, 25.0), "engine_s"),
    ("可通过代码扩展", (0.1, 0.1, 0.1), "Can_be_extended_through_code")
]

# 占位组件名：这些值只用于内部标记，不应写入 comp_name（如"自定义"预设）
PLACEHOLDER_COMP_NAMES = {"customize"}

def _build_preset_items():
    """生成过滤后的预设列表（注册时静态生成；动态回调在部分 4.2 环境注册失败）"""
    items = []
    for i, preset in enumerate(COMPONENT_PRESETS):
        name, size, comp_name = preset
        # 过滤空名称的预设
        if name and name.strip():
            desc = t("PRESET_DESC", size[0], size[1], size[2], comp_name)
            items.append((str(i), name, desc))

    # 确保列表不为空
    if not items:
        items.append(("0", "默认", "默认预设"))

    return items

# ============================ 多语言文本定义 ============================
# 所有UI文本集中定义在这里，便于本地化
MULTI_LANGUAGE_TEXTS = {
    'zh_CN': {  # 简体中文
        # 基础设置
        "COMP_NAME": "组件名称",
        "COMP_NAME_DESC": "生成的组件对象的名称",
        "COMP_NAME_DEFAULT": DEFAULT_VALUES["COMP_NAME"],

        "COMP_SIZE": "组件尺寸",
        "COMP_SIZE_DESC": "组件的长宽高尺寸",

        "PLACEMENT_MODE": "放置模式",
        "PLACEMENT_SINGLE": "单个放置",
        "PLACEMENT_SINGLE_DESC": "在选中面的中心放置单个组件",
        "PLACEMENT_MULTI": "多物体排列",
        "PLACEMENT_MULTI_DESC": "沿选中面范围排列多个组件",
        "PLACEMENT_MODE_DESC": "选择组件放置模式",

        # 预设设置
        "PRESET_SELECTION": "组件预设",
        "PRESET_SELECTION_DESC": "选择预定义的组件尺寸和名称",
        "PRESET_DESC": "尺寸: {0}x{1}x{2}, 组件: {3}",

        # 朝向设置
        "ALIGN_AXIS": "法向轴",
        "ALIGN_Z": "Z+",
        "ALIGN_Z_DESC": "局部 +Z 轴对齐面法线正方向",
        "ALIGN_Y": "Y+",
        "ALIGN_Y_DESC": "局部 +Y 轴对齐面法线正方向",
        "ALIGN_X": "X+",
        "ALIGN_X_DESC": "局部 +X 轴对齐面法线正方向",
        "ALIGN_Z_MINUS_DESC": "局部 +Z 轴对齐面法线反方向（组件里外翻转）",
        "ALIGN_Y_MINUS_DESC": "局部 +Y 轴对齐面法线反方向（组件里外翻转）",
        "ALIGN_X_MINUS_DESC": "局部 +X 轴对齐面法线反方向（组件里外翻转）",
        "ALIGN_AXIS_DESC": "哪根局部轴对齐面法线：正向=轴指向法线正方向（朝外），负向=指向法线反方向（朝内/翻转）",

        "FORWARD_AXIS": "朝向轴",
        "FORWARD_Y_PLUS": "Y+",
        "FORWARD_Y_PLUS_DESC": "局部 +Y 对齐世界前向(Y+)在面平面上的投影",
        "FORWARD_Y_MINUS": "Y-",
        "FORWARD_Y_MINUS_DESC": "局部 -Y 对齐世界前向(Y+)在面平面上的投影",
        "FORWARD_X_PLUS": "X+",
        "FORWARD_X_PLUS_DESC": "局部 +X 对齐世界前向(Y+)在面平面上的投影",
        "FORWARD_X_MINUS": "X-",
        "FORWARD_X_MINUS_DESC": "局部 -X 对齐世界前向(Y+)在面平面上的投影",
        "FORWARD_Z_PLUS": "Z+",
        "FORWARD_Z_PLUS_DESC": "局部 +Z 对齐世界前向(Y+)在面平面上的投影",
        "FORWARD_Z_MINUS": "Z-",
        "FORWARD_Z_MINUS_DESC": "局部 -Z 对齐世界前向(Y+)在面平面上的投影",
        "FORWARD_AXIS_DESC": "决定组件绕法线的朝向：所选局部轴对齐世界前向(Y+)在面平面上的投影方向",
        "UP_AXIS": "向上轴",
        "UP_AXIS_DESC": "法向轴与朝向轴为同一轴时生效，指定哪根局部轴对齐世界上向(Z+)在面平面上的投影；其余情况自动推导",
        "NAME_GROUP": "组名",
        "NAME_GROUP_DESC": "硬点名称的组前缀（组-类型-序号 命名规则的组段），留空则不加链接符",
        "NAME_SEPARATOR": "链接符号",
        "NAME_SEPARATOR_DESC": "组名与名称之间的连接符号",
        "NAME_KEY": "组件key",
        "NAME_KEY_DESC": "标识本工具放置的硬点，插在链接符与名称之间；膨胀/收缩以它识别硬点，XML 补全工具剥离它后再做类型匹配",
        "NAME_KEY_NOTE": "膨胀/收缩、XML补全等功能按此 key 识别硬点",
        "ERROR_COMP_KEY_EMPTY": "组件key为空，膨胀/收缩无法识别硬点；请在组件名称区填写组件key（默认 up7con_）",
        "MAINTAIN_PROPS": "维护硬点属性",
        "MAINTAIN_FILTER": "筛选文本",
        "MAINTAIN_FILTER_DESC": "按筛选批量维护：名称包含该文本的空物体统一按当前面板设置维护属性（不依赖选中）",
        "MAINTAIN_FILTERED": "按筛选维护",
        "MAINTAIN_PROPS_DESC": "按当前 组件预设/组件尺寸/朝向 设置，为选中的空物体添加（缺失）或更新（已有）尺寸与朝向自定义属性",
        "ERROR_NO_SELECTION": "请先选中要维护的空物体硬点",
        "INFO_MAINTAINED": "维护完成：新增 {} 个、更新 {} 个、跳过非空物体 {} 个",
        "ORGANIZE_SCENE": "整理场景",
        "ORGANIZE_SCENE_DESC": "把场景中带组件类型的硬点空物体按类型归入 Connections 集合层级（Engines/Turrets/Shields/Docks…，炮塔按组名建子集合），不动父子与变换",
        "ERROR_NO_HARDPOINTS_ORG": "场景中没有带 x4_comp_name 的硬点空物体可整理",
        "INFO_ORGANIZED": "已整理 {} 个硬点到 {} 个集合",
        "SHIP_MESH_ORGANIZE": "整理舰船网格",
        "SHIP_MESH_ORGANIZE_DESC": "将选中的 EVE 舰船网格整理为 X4 命名规范：重命名为 part_main，UV→uv1，复制出 collision/LOD1-3/wreck，支持按比例减面",
        "SHIP_MESH_PREFIX": "材质前缀",
        "SHIP_MESH_PREFIX_DESC": "材质名称前缀，留空则无前缀",
        "SHIP_MESH_MAT_NAME": "材质名称",
        "SHIP_MESH_MAT_NAME_DESC": "主体材质基础名，自动补后缀 _a/_b/_c…（应用于 part_main 与 LOD1-3）",
        "SHIP_MESH_WRECK_MAT": "Wreck 材质名",
        "SHIP_MESH_WRECK_MAT_DESC": "wreck 网格使用的材质名称（清空原有材质后新建并应用）",
        "SHIP_MESH_LOD_MODE": "LOD 生成方式",
        "SHIP_MESH_LOD_MODE_DESC": "LOD1/2/3 的生成方式：直接复制或按比例减面",
        "SHIP_MESH_WRECK_MODE": "Wreck 生成方式",
        "SHIP_MESH_WRECK_MODE_DESC": "wreck 的生成方式：直接复制或按比例减面",
        "SHIP_MESH_MODE_COPY": "复制",
        "SHIP_MESH_MODE_COPY_DESC": "直接复制原网格（面数不变）",
        "SHIP_MESH_MODE_DECIMATE": "按比例减面",
        "SHIP_MESH_MODE_DECIMATE_DESC": "用 Decimate 修改器按比例减少面数",
        "SHIP_MESH_LOD1_RATIO": "LOD1 减面比例",
        "SHIP_MESH_LOD2_RATIO": "LOD2 减面比例",
        "SHIP_MESH_LOD3_RATIO": "LOD3 减面比例",
        "SHIP_MESH_WRECK_RATIO": "Wreck 减面比例",
        "SHIP_MESH_LOD_RATIO_DESC": "保留面数比例（0.01~1.0），仅在「按比例减面」模式下生效",
        "SHIP_MESH_CREATE_COLLISION": "生成 Collision",
        "SHIP_MESH_CREATE_COLLISION_DESC": "勾选时额外生成 part_main.collision（不附材质），默认不生成",
        "ERROR_NO_MESH": "场景中未找到网格物体，请先选中或导入一个 MESH",
        "ERROR_ALREADY_ORGANIZED": "该网格已整理过（含 x4_ship_mesh_organized 标记），已跳过。如需重新整理请先删除该自定义属性。",
        "INFO_SHIP_MESH_DONE": "舰船网格整理完成，已生成 {} 个衍生物体",
        "PREVIEW_TITLE": "预览实体化",
        "PREVIEW_SHOW": "实体化预览",
        "PREVIEW_SHOW_DESC": "按自定义属性记录的组件 xyz 尺寸，为选中的空物体创建同位线框长方体预览（父级跟随移动，不修改空物体本身）",
        "PREVIEW_HIDE": "去除实体化",
        "PREVIEW_HIDE_DESC": "移除选中空物体（或直接选中的预览体）的预览；不选任何物体时清理场景中全部预览体",
        "INFO_PREVIEW_CREATED": "预览新建 {} 个（已刷新 {} 个，无尺寸属性 {} 个）",
        "INFO_PREVIEW_REMOVED": "已移除预览 {} 个",
        "ERROR_NO_PREVIEW": "场景中没有可移除的预览体",
        "INFLATE_TITLE": "膨胀/收缩",
        "INFLATE_SCOPE": "绑定范围",
        "INFLATE_SCOPE_ALL": "关联全部组件",
        "INFLATE_SCOPE_SELECTED": "关联选定组件",
        "INFLATE_PARENT": "建立父子绑定",
        "INFLATE_PARENT_DESC": "将选中的名称含组件key的硬点空物体父子绑定到当前选中的舰船（保持世界位置不变），随后缩放舰船即可实时预览硬点跟随",
        "INFLATE_APPLY_MOVE": "应用·仅位移",
        "INFLATE_APPLY_FULL": "应用·位移+缩放",
        "INFLATE_APPLY_FULL_DESC": "解除父子并保留缩放（硬点变换完整跟随舰船，含缩放）",
        "ERROR_INFLATE_NO_SHIP": "请先选中舰船作为父级（活动物体，不能是空物体）",
        "ERROR_NO_HARDPOINTS": "场景中没有名称含组件key的空物体硬点",
        "ERROR_NOT_PARENTED": "没有处于父子绑定中的硬点（先点\"建立父子绑定\"）",
        "INFO_PARENTED": "已绑定 {} 个硬点",
        "INFO_INFLATE_APPLIED": "已应用 {} 个硬点（{}）",

        # 排列设置
        "ARRANGE_DIRECTION": "排列方向",
        "ARRANGE_X": "X轴方向",
        "ARRANGE_X_DESC": "沿面的局部X轴方向排列",
        "ARRANGE_Y": "Y轴方向",
        "ARRANGE_Y_DESC": "沿面的局部Y轴方向排列",
        "ARRANGE_LONG_EDGE": "沿长边中心线",
        "ARRANGE_LONG_EDGE_DESC": "沿所选面边界框的长边中心线排列",
        "ARRANGE_DIRECTION_DESC": "多物体排列的方向",

        "SPACING": "组件间距",
        "SPACING_DESC": "多物体排列时的间距（默认自动跟随组件最长边×1.3）",
        "RESET_SPACING_DESC": "重置间距为组件最长边×1.3，并恢复自动跟随",

        "AUTO_COUNT": "自动计算数量",
        "AUTO_COUNT_DESC": "根据面范围自动计算组件数量",

        "MANUAL_COUNT": "手动数量",
        "MANUAL_COUNT_DESC": "手动设置组件数量（最多50）",

        # 曲面放置模式
        "SURFACE_MODE": "贴合模式",
        "SURFACE_MODE_DESC": "多物体排列时如何贴合选中曲面",
        "SURFACE_MODE_PLANE": "平面（平均法线）",
        "SURFACE_MODE_PLANE_DESC": "把选中面当作一个平面，所有组件同朝向直线排列",
        "SURFACE_MODE_FACE_CENTER": "面中心（逐面法线）",
        "SURFACE_MODE_FACE_CENTER_DESC": "沿排列轴选 N 个面，每个组件放在面中心并用该面法线朝向",
        "SURFACE_MODE_FACE_MERGE": "曲面路径（接触面积）",
        "SURFACE_MODE_FACE_MERGE_DESC": "以放置位置为中心收集附近面直到累计面积≈组件接触面积，用合并面平均法线，位置投影到合并平面并贴合曲面",
        "FORWARD_MODE": "朝向参考",
        "FORWARD_MODE_DESC": "组件朝向轴的参考方向",
        "FORWARD_MODE_SETTING": "朝向设置（世界Y+）",
        "FORWARD_MODE_SETTING_DESC": "用朝向轴设置，参考世界前向(Y+)",
        "FORWARD_MODE_ARRANGE": "沿排列方向",
        "FORWARD_MODE_ARRANGE_DESC": "组件朝向轴对齐排列方向（排队朝向一致）",

        # 偏移设置
        "SURFACE_OFFSET": "表面偏移",
        "SURFACE_OFFSET_DESC": "组件相对于表面的偏移距离",
        "OFFSET_AXIS": "联动方向",
        "OFFSET_AXIS_DESC": "表面偏移联动取组件尺寸的哪个方向",
        "OFFSET_PERCENT": "联动百分比",
        "OFFSET_PERCENT_DESC": "表面偏移 = 组件尺寸联动方向长度 × 百分比（改动尺寸/方向/百分比时自动填值）",
        "DISPLAY_MODE": "空物体显示模式",
        "DISPLAY_MODE_DESC": "新放置硬点的空物体显示样式",
        "APPLY_DISPLAY_MODE": "应用显示模式到全部硬点",
        "INFO_DISPLAY_APPLIED": "已将 {} 个空物体改为该显示模式",
        "DISPLAY_PLAIN_AXES": "纯轴",
        "DISPLAY_ARROWS": "箭头",
        "DISPLAY_SINGLE_ARROW": "单箭头",
        "DISPLAY_CIRCLE": "圆环",
        "DISPLAY_CUBE": "立方体",
        "DISPLAY_SPHERE": "球体",
        "DISPLAY_CONE": "圆锥",

        "FACE_X_OFFSET": "面内X偏移",
        "FACE_X_OFFSET_DESC": "在面局部X轴方向的偏移",

        "FACE_Y_OFFSET": "面内Y偏移",
        "FACE_Y_OFFSET_DESC": "在面局部Y轴方向的偏移",

        # 操作器文本
        "PLACER_OPERATOR": "放置组件",
        "PLACER_OPERATOR_DESC": "组件放置操作器",

        "MOVE_OPERATOR": "沿面平移",
        "MOVE_OPERATOR_DESC": "表面平移操作器",

        "QUICK_MOVE_OPERATOR": "快速平移",
        "QUICK_MOVE_OPERATOR_DESC": "快速平移操作器",

        # 方向文本
        "MOVE_DIRECTION": "平移方向",
        "DIRECTION_LEFT": "左",
        "DIRECTION_RIGHT": "右",
        "DIRECTION_FORWARD": "前",
        "DIRECTION_BACKWARD": "后",
        "DIRECTION_UP": "上",
        "DIRECTION_DOWN": "下",

        # 面板文本
        "PANEL_TITLE": "组件放置器",
        "BASIC_SETTINGS": "基础设置",
        "ORIENTATION_SETTINGS": "朝向设置",
        "MULTI_OBJECT_ARRANGEMENT": "多物体排列",
        "POSITION_OFFSETS": "位置偏移",
        "TRANSLATION_TOOLS": "平移工具",

        "EXECUTE_PLACEMENT": "执行放置",
        "PRECISE_TRANSLATION": "精确平移",
        "QUICK_TRANSLATION": "快捷平移",
        "APPLY_PRECISE_TRANSLATION": "应用精确平移",

        # 名称替换
        "REPLACE_TITLE": "名称替换",
        "FIND_TEXT": "查找",
        "REPLACE_TEXT": "替换为",
        "EXECUTE_REPLACE": "执行替换",
        "ERROR_FIND_EMPTY": "查找值不能为空",
        "INFO_REPLACED": "已替换 {} 个物体的名称",

        # 镜像工具
        "MIRROR_TITLE": "镜像工具",
        "APPLY_MIRROR": "X=0 镜像选中硬点",
        "INFO_MIRRORED": "已镜像 {} 个硬点",

        # 旋转工具
        "ROTATE_TITLE": "旋转工具",
        "APPLY_PRECISE_ROTATION": "应用精确旋转",
        "ROTATE_STEP": "旋转步长",
        "INFO_ROTATED": "已旋转 {} 个组件",

        # 批量平移
        "BATCH_MOVE": "批量平移",
        "DIRECTION_SURFACE_OUT": "远离面",
        "DIRECTION_SURFACE_IN": "贴近面",
        "DIRECTION_SURFACE_DESC": "按每个硬点记录的放置朝向（x4_axis_normal）自适应判断法线方向；无记录的老硬点按 Z+ 处理",
        "BATCH_FILTER": "筛选文本",
        "BATCH_FILTER_DESC": "名称包含该文本的空物体将被批量平移",
        "INFO_BATCH_MOVED": "已沿局部轴平移 {} 个空物体",
        "INFO_BATCH_MOVED_FALLBACK": "已平移 {} 个空物体（{} 个无朝向记录，按 Z+ 处理；可用维护硬点属性补齐）",
        "ERROR_BATCH_FILTER_EMPTY": "请先输入批量筛选文本",
        "ERROR_BATCH_NO_MATCH": "没有名称包含该文本的空物体",
        "BATCH_STEP": "平移步长",

        # 轴常显
        "TOGGLE_AXIS": "切换轴常显（场景全部硬点）",
        "INFO_AXIS_ENABLED": "已开启 {} 个空物体的三色轴常显",
        "INFO_AXIS_DISABLED": "已关闭 {} 个空物体的三色轴常显",
        "ERROR_SELECT_EMPTY": "场景中没有空物体硬点",

        # 平移步长
        "QUICK_STEP": "快捷步长",
        "QUICK_STEP_DESC": "快捷平移操作的单次步长",

        # 错误和提示信息
        "ERROR_EDIT_MODE": "请先进入编辑模式并选择一个网格物体",
        "ERROR_SELECT_FACE": "请在编辑模式下选择至少一个面",
        "ERROR_DATA_CALCULATION": "数据计算过程中出错: {}",
        "ERROR_OBJECT_CREATION": "创建物体过程中出错: {}",
        "ERROR_SELECT_COMPONENTS": "请先选择要平移的组件",
        "ERROR_NO_NORMAL_INFO": "选中的物体没有关联的面的法线信息",

        "INFO_PLACEMENT_COMPLETE": "组件放置完成: {}",
        "INFO_TRANSLATED_OBJECTS": "已平移 {} 个物体",

        "SELECTED_OBJECTS_COUNT": "已选中 {} 个物体",
        "TRANSLATABLE_COMPONENTS": "✓ {} 个可平移组件",
        "NON_TRANSLATABLE_OBJECTS": "✗ 选中的物体无法平移",
        "NO_OBJECTS_SELECTED": "未选中任何物体",

        # 平移属性
        "MOVE_X": "X",
        "MOVE_X_DESC": "沿物体局部X轴平移量",
        "MOVE_Y": "Y",
        "MOVE_Y_DESC": "沿物体局部Y轴平移量",
        "MOVE_Z": "Z",
        "ERROR_NO_HARDPOINT": "选中的物体中没有硬点（组件）",

        # 航路点导入
        "WP_PANEL_TITLE": "航路编辑器",
        "WP_XML_PATH": "XML 文件路径",
        "WP_XML_PATH_DESC": "X4 舰船/机坪 XML 文件路径",
        "WP_BASE_NAME": "清除前缀",
        "WP_NAME_GROUP": "组名",
        "WP_NAME_GROUP_DESC": "新建 waypoint 的组名前缀（同时写入 group 自定义属性供泊位绑定）；留空则不带组",
        "WP_NAME_SEPARATOR": "连接符",
        "WP_NAME_BASE": "航点名",
        "WP_HP_TYPE": "硬点类型",
        "WP_DEFAULT_HP_TYPE": "未识别名称按",
        "WP_DEFAULT_HP_TYPE_DESC": "导入时名称中既不含 dockingbay 也不含 waypoint 的硬点，按此类型处理",
        "WP_APPLY_GROUP": "应用组名到选中",
        "WP_APPLY_GROUP_DESC": "把选中硬点名称的第一个 --- 左侧替换为上方组名（即修改其 group），并同步 group 自定义属性",
        "WP_APPLY_GROUP_DONE": "已更新 {} 个硬点的组名为 '{}'",
        "WP_IMPORT_ERR_DASH3": "dockingbay '{}' 的名称或组名含 '{}'（与连接符冲突），请先在 XML 中改为 '_' 后重新导入",
        "WP_SEP_NOTE": "编辑器通用：导入 / 新建 / 应用组名 / 导出共用",
        "WP_EXPORT_WARN_GROUP_NO_DOCK": "waypoint 组 '{}' 没有对应的 dockingbay 组",
        "WP_EXPORT_WARN_DOCK_NO_WP": "dockingbay 组 '{}' 没有对应的 waypoint 组",
        "WP_SECTION_CREATE": "新建命名（组名+连接符+航点名）",
        "PLACEMENT_MODULE": "放置设置",
        "WP_BASE_NAME_DESC": "导入前清除场景中以此名称开头的已有对象",
        "WP_S_SIZE": "S级正方体边长",
        "WP_S_SIZE_DESC": "名称含 _s_ 的 dockingbay 正方体边长",
        "WP_M_LENGTH": "M级长度(Y)",
        "WP_M_LENGTH_DESC": "名称含 _m_ 的 dockingbay 长方体 Y 轴长度",
        "WP_M_WIDTH": "M级宽度(X)",
        "WP_M_WIDTH_DESC": "名称含 _m_ 的 dockingbay 长方体 X 轴宽度",
        "WP_M_HEIGHT": "M级高度(Z)",
        "WP_M_HEIGHT_DESC": "名称含 _m_ 的 dockingbay 长方体 Z 轴高度",
        "WP_DEFAULT_SIZE": "默认空物体大小",
        "WP_DEFAULT_SIZE_DESC": "waypoint 空物体的默认显示大小",
        "WP_CLEAR_EXISTING": "清除已有航路点",
        "WP_CLEAR_EXISTING_DESC": "导入前删除场景中已有的同名航路点对象",
        "WP_IMPORT": "导入航路点与停靠湾",
        "WP_IMPORT_DESC": "从 XML 导入 waypoint 和 dockingbay 连接点并建立朝向约束",
        "WP_INFO_IMPORTED": "导入完成: {} 个航路点, {} 个停靠湾连接点, {} 个朝向约束",
        "WP_ERROR_XML": "XML 加载失败: {}",
        "WP_ERROR_NO_XML": "请先指定 XML 文件路径",

        # 航路点编辑 / 导出（对齐 x4_wp_export.py 格式）
        "WP_EDIT_TITLE": "选中 Waypoint 编辑",
        "WP_TAGS": "Tags",
        "WP_TAGS_DESC": "tags 自定义属性（空格分隔，词汇表内）",
        "WP_LINKS": "Links",
        "WP_LINKS_DESC": "出边目标 waypoint 名（空格分隔）",
        "WP_GROUP": "组 (group)",
        "WP_GROUP_DESC": "waypoint 分组名，导出为 group 属性；游戏靠它与泊位 dockingbay 绑定",
        "WP_EXPORT_WARN_NO_GROUP": "waypoint '{}' 带 dockpath 但无 group：本文件存在其他分组，多泊位并行链需 group 一一绑定泊位",
        "WP_MAKE_WAYPOINT": "设为 Waypoint",
        "WP_MAKE_WAYPOINT_DESC": "给选中物体添加 tags/links 自定义属性",
        "WP_MAKE_WAYPOINT_DONE": "已将 {} 个物体设为 waypoint",
        "WP_ADD": "新建 Waypoint",
        "WP_ADD_DESC": "在 3D 光标处新建一个 waypoint 空物体",
        "WP_ADD_LINKS_FROM_SEL": "添加选中为链接",
        "WP_ADD_LINKS_FROM_SEL_DESC": "把选中的其他物体名称追加到 links（去重，不覆盖已输入内容）",
        "WP_CLEAR_LINKS": "清除链接",
        "WP_LINKS_ADDED": "已添加链接: {}",
        "WP_LINKS_NO_NEW": "没有新的链接可添加",
        "WP_REBUILD_CONSTRAINTS": "重建约束",
        "WP_CONSTRAINTS_REBUILT": "已按 links 重建 Track To 约束",
        "WP_NO_SELECTED_WAYPOINT": "未选中带 tags 属性的 waypoint 物体",
        "WP_EXPORT": "导出 Waypoints",
        "WP_EXPORT_DESC": "导出为 x4_wp_export.py 所需的 XML 格式",
        "WP_EXPORT_PATH": "导出路径",
        "WP_EXPORT_PATH_DESC": "导出 waypoints XML 的目标文件路径",
        "WP_EXPORT_DONE": "已导出 {} waypoint + {} dockingbay（{} 条警告）→ {}",
        "WP_EXPORT_EMPTY": "没有可导出的硬点（选中或当前集合内无带 hardpoint_type/tags 属性的物体）",
        "WP_EXPORT_ERR_DASH3": "dockingbay '{}' 的名称或组名含 '{}'（与连接符冲突），请先改为 '_' 后重新导出",
        "WP_ERROR_NO_EXPORT_PATH": "请先指定导出文件路径",
        "WP_TAG_CAT_TYPE": "类型",
        "WP_TAG_CAT_ENDPOINT": "端点",
        "WP_TAG_CAT_GROUP": "分组",
        "WP_TAG_CAT_SIZE": "尺寸",
        "WP_TAG_CAT_SPEED": "速度",
        "WP_TAG_CAT_OTHER": "其他",
        "WP_LANE": "车道",
        "WP_LANE_INBOUND": "进站道",
        "WP_LANE_OUTBOUND": "出站道",
        "WP_LANE_MANUAL": "手动旋转",
        "WP_EXPORT_ERRORS": "导出失败：发现 {} 个错误",
        "WP_EXPORT_WARN_DOCK": "警告：没有 typedock waypoint",
        "WP_EXPORT_WARN_BLOCK": "警告：blocklink start/end 不成对（start={}, end={}）",
        "WP_EXPORT_WARN_SIZE": "警告：没有 sizeLevel1 waypoint",
        "WP_EXPORT_WARN_NORMAL": "警告：typenormal waypoint '{}' 无出边且非 end/dynamicmasstraffic",
        "WP_EXPORT_ERR_DUP": "waypoint 名称重复: {}",
        "WP_EXPORT_ERR_EMPTY_TAGS": "waypoint '{}' 的 tags 为空",
        "WP_EXPORT_ERR_BAD_TAG": "waypoint '{}' 含非法 tag '{}'",
        "WP_EXPORT_ERR_LINK": "waypoint '{}' 链接到不存在的目标 '{}'",
        "WP_EXPORT_WARN_LINK_OUTSIDE": "waypoint '{}' 的链接目标 '{}' 在场景中存在但未包含在本次导出范围",

        # 应用选中组件设定
        "APPLY_SELECTED_NAME": "应用选中组件设定",
        "APPLY_SELECTED_NAME_DESC": "将选中组件的名称解构回填到组名/组件名称栏",
        "ERROR_NO_SELECTION": "未选中任何组件",
        "ERROR_NAME_FORMAT": "选中组件名称格式不符：必须以 '-' 为连接符、以 'up7con_' 为组件key",
        "INFO_NAME_APPLIED": "已回填组名 '{}'、组件名称 '{}'",
    },

    'en_US': {  # 英文
        # 基础设置
        "COMP_NAME": "Component Name",
        "COMP_NAME_DESC": "Name of the generated component object",
        "COMP_NAME_DEFAULT": DEFAULT_VALUES["COMP_NAME"],

        "COMP_SIZE": "Component Size",
        "COMP_SIZE_DESC": "Length, width and height of the component",

        "PLACEMENT_MODE": "Placement Mode",
        "PLACEMENT_SINGLE": "Single Placement",
        "PLACEMENT_SINGLE_DESC": "Place a single component at the center of selected face",
        "PLACEMENT_MULTI": "Multiple Objects Arrangement",
        "PLACEMENT_MULTI_DESC": "Arrange multiple components along selected face area",
        "PLACEMENT_MODE_DESC": "Select component placement mode",

        # 预设设置
        "PRESET_SELECTION": "Component Preset",
        "PRESET_SELECTION_DESC": "Select predefined component size and name",
        "PRESET_DESC": "Size: {0}x{1}x{2}, Component: {3}",

        # 朝向设置
        "ALIGN_AXIS": "Normal Axis",
        "ALIGN_Z": "Z+",
        "ALIGN_Z_DESC": "Local +Z aligns with face normal direction",
        "ALIGN_Y": "Y+",
        "ALIGN_Y_DESC": "Local +Y aligns with face normal direction",
        "ALIGN_X": "X+",
        "ALIGN_X_DESC": "Local +X aligns with face normal direction",
        "ALIGN_Z_MINUS_DESC": "Local +Z aligns against face normal (component flipped inward)",
        "ALIGN_Y_MINUS_DESC": "Local +Y aligns against face normal (component flipped inward)",
        "ALIGN_X_MINUS_DESC": "Local +X aligns against face normal (component flipped inward)",
        "ALIGN_AXIS_DESC": "Which local axis aligns to face normal: positive = along normal (outward), negative = against normal (inward/flipped)",

        "FORWARD_AXIS": "Facing Axis",
        "FORWARD_Y_PLUS": "Y+",
        "FORWARD_Y_PLUS_DESC": "Local +Y aligns to the projection of world forward (Y+) onto the face plane",
        "FORWARD_Y_MINUS": "Y-",
        "FORWARD_Y_MINUS_DESC": "Local -Y aligns to the projection of world forward (Y+) onto the face plane",
        "FORWARD_X_PLUS": "X+",
        "FORWARD_X_PLUS_DESC": "Local +X aligns to the projection of world forward (Y+) onto the face plane",
        "FORWARD_X_MINUS": "X-",
        "FORWARD_X_MINUS_DESC": "Local -X aligns to the projection of world forward (Y+) onto the face plane",
        "FORWARD_Z_PLUS": "Z+",
        "FORWARD_Z_PLUS_DESC": "Local +Z aligns to the projection of world forward (Y+) onto the face plane",
        "FORWARD_Z_MINUS": "Z-",
        "FORWARD_Z_MINUS_DESC": "Local -Z aligns to the projection of world forward (Y+) onto the face plane",
        "FORWARD_AXIS_DESC": "Facing around the normal: the chosen local axis aligns to the projection of world forward (Y+) onto the face plane",
        "UP_AXIS": "Up Axis",
        "UP_AXIS_DESC": "Active only when normal and facing axes share the same letter; the chosen local axis aligns to the projection of world up (Z+) onto the face plane; otherwise auto-derived",
        "NAME_GROUP": "Group Name",
        "NAME_GROUP_DESC": "Group prefix of the hardpoint name (group part of the group-type-index naming rule); empty means no separator",
        "NAME_SEPARATOR": "Separator",
        "NAME_SEPARATOR_DESC": "Symbol between group name and the name part",
        "NAME_KEY": "Component Key",
        "NAME_KEY_DESC": "Marks hardpoints placed by this tool, inserted between separator and name; inflate/shrink uses it to find hardpoints, the XML tool strips it before type matching",
        "NAME_KEY_NOTE": "Inflate/Shrink and XML tools identify hardpoints by this key",
        "ERROR_COMP_KEY_EMPTY": "Component key is empty, inflate/shrink cannot find hardpoints; fill the key in the name section (default up7con_)",
        "MAINTAIN_PROPS": "Maintain Hardpoint Props",
        "MAINTAIN_FILTER": "Filter Text",
        "MAINTAIN_FILTER_DESC": "Batch maintain: all empties whose name contains this text get current panel settings (no selection needed)",
        "MAINTAIN_FILTERED": "Maintain by Filter",
        "MAINTAIN_PROPS_DESC": "Add (missing) or update (existing) size and orientation custom properties on selected empties, from current preset/size/orientation settings",
        "ERROR_NO_SELECTION": "Select hardpoint empties to maintain first",
        "INFO_MAINTAINED": "Maintained: {} added, {} updated, {} non-empties skipped",
        "ORGANIZE_SCENE": "Organize Scene",
        "ORGANIZE_SCENE_DESC": "Group hardpoint empties by component type into a Connections collection hierarchy (Engines/Turrets/Shields/Docks…, turrets get sub-collections by group). Does not touch parent or transforms.",
        "ERROR_NO_HARDPOINTS_ORG": "No hardpoint empties with x4_comp_name found in the scene",
        "INFO_ORGANIZED": "Organized {} hardpoints into {} collections",
        "SHIP_MESH_ORGANIZE": "Organize Ship Mesh",
        "SHIP_MESH_ORGANIZE_DESC": "Convert selected EVE ship mesh to X4 naming: rename to part_main, UV→uv1, spawn collision/LOD1-3/wreck, optional decimate",
        "SHIP_MESH_PREFIX": "Material Prefix",
        "SHIP_MESH_PREFIX_DESC": "Prefix for material names; leave empty for no prefix",
        "SHIP_MESH_MAT_NAME": "Material Name",
        "SHIP_MESH_MAT_NAME_DESC": "Base material name; auto-suffixed _a/_b/_c… (applied to part_main and LOD1-3)",
        "SHIP_MESH_WRECK_MAT": "Wreck Material",
        "SHIP_MESH_WRECK_MAT_DESC": "Material name for the wreck mesh (clears original materials, creates and applies this one)",
        "SHIP_MESH_LOD_MODE": "LOD Mode",
        "SHIP_MESH_LOD_MODE_DESC": "How LOD1/2/3 are generated: copy or proportional decimate",
        "SHIP_MESH_WRECK_MODE": "Wreck Mode",
        "SHIP_MESH_WRECK_MODE_DESC": "How wreck is generated: copy or proportional decimate",
        "SHIP_MESH_MODE_COPY": "Copy",
        "SHIP_MESH_MODE_COPY_DESC": "Copy the original mesh (unchanged face count)",
        "SHIP_MESH_MODE_DECIMATE": "Decimate",
        "SHIP_MESH_MODE_DECIMATE_DESC": "Reduce face count proportionally with the Decimate modifier",
        "SHIP_MESH_LOD1_RATIO": "LOD1 Ratio",
        "SHIP_MESH_LOD2_RATIO": "LOD2 Ratio",
        "SHIP_MESH_LOD3_RATIO": "LOD3 Ratio",
        "SHIP_MESH_WRECK_RATIO": "Wreck Ratio",
        "SHIP_MESH_LOD_RATIO_DESC": "Ratio of faces to keep (0.01~1.0), only used in Decimate mode",
        "SHIP_MESH_CREATE_COLLISION": "Create Collision",
        "SHIP_MESH_CREATE_COLLISION_DESC": "Also generate part_main.collision (no material) when checked. Off by default.",
        "ERROR_NO_MESH": "No mesh object found. Select or import a MESH first.",
        "ERROR_ALREADY_ORGANIZED": "This mesh was already organized (x4_ship_mesh_organized flag set). Skipped. Remove the custom property to re-organize.",
        "INFO_SHIP_MESH_DONE": "Ship mesh organized, {} derivative objects created",
        "PREVIEW_TITLE": "Solidify Preview",
        "PREVIEW_SHOW": "Solidify Preview",
        "PREVIEW_SHOW_DESC": "Create a co-located wireframe box for selected empties from recorded xyz size (parents follow movement; empties untouched)",
        "PREVIEW_HIDE": "Remove Solidify",
        "PREVIEW_HIDE_DESC": "Remove previews of selected empties (or selected preview boxes); with nothing selected, clean all previews in the scene",
        "INFO_PREVIEW_CREATED": "Created {} previews ({} refreshed, {} without size property)",
        "INFO_PREVIEW_REMOVED": "Removed {} previews",
        "ERROR_NO_PREVIEW": "No preview boxes to remove in the scene",
        "INFLATE_TITLE": "Inflate/Shrink",
        "INFLATE_SCOPE": "Parenting Scope",
        "INFLATE_SCOPE_ALL": "All Matching Components",
        "INFLATE_SCOPE_SELECTED": "Selected Components Only",
        "INFLATE_PARENT": "Parent to Ship",
        "INFLATE_PARENT_DESC": "Parent selected hardpoint empties whose name contains the component key to the selected ship (world positions kept); then scale the ship to preview hardpoints following live",
        "INFLATE_APPLY_MOVE": "Apply · Move Only",
        "INFLATE_APPLY_FULL": "Apply · Move + Scale",
        "INFLATE_APPLY_FULL_DESC": "Unparent and keep scale (hardpoint transforms fully follow the ship, including scale)",
        "ERROR_INFLATE_NO_SHIP": "Select a ship as parent first (active object, not an empty)",
        "ERROR_NO_HARDPOINTS": "No hardpoint empties with the component key in name in the scene",
        "ERROR_NOT_PARENTED": "No hardpoints are parented (click \"Parent to Ship\" first)",
        "INFO_PARENTED": "Parented {} hardpoints",
        "INFO_INFLATE_APPLIED": "Applied {} hardpoints ({})",

        # 排列设置
        "ARRANGE_DIRECTION": "Arrangement Direction",
        "ARRANGE_X": "X Axis Direction",
        "ARRANGE_X_DESC": "Arrange along face's local X axis",
        "ARRANGE_Y": "Y Axis Direction",
        "ARRANGE_Y_DESC": "Arrange along face's local Y axis",
        "ARRANGE_LONG_EDGE": "Along Long Edge Centerline",
        "ARRANGE_LONG_EDGE_DESC": "Arrange along the long edge centerline of selected face bounding box",
        "ARRANGE_DIRECTION_DESC": "Multiple objects arrangement direction",

        "SPACING": "Component Spacing",
        "SPACING_DESC": "Spacing between components in multi-object arrangement (auto-follows longest edge ×1.3 by default)",
        "RESET_SPACING_DESC": "Reset spacing to component longest edge ×1.3 and re-enable auto-follow",

        "AUTO_COUNT": "Auto Calculate Count",
        "AUTO_COUNT_DESC": "Automatically calculate component count based on face area",

        "MANUAL_COUNT": "Manual Count",
        "MANUAL_COUNT_DESC": "Manually set component count (max 50)",

        # 曲面放置模式
        "SURFACE_MODE": "Surface Mode",
        "SURFACE_MODE_DESC": "How multi-placement conforms to selected surface",
        "SURFACE_MODE_PLANE": "Plane (avg normal)",
        "SURFACE_MODE_PLANE_DESC": "Treat selected faces as one plane; all components share orientation in a straight line",
        "SURFACE_MODE_FACE_CENTER": "Face Center (per-face normal)",
        "SURFACE_MODE_FACE_CENTER_DESC": "Pick N faces along arrange axis; each component sits on a face center using that face's normal",
        "SURFACE_MODE_FACE_MERGE": "Surface Path (contact area)",
        "SURFACE_MODE_FACE_MERGE_DESC": "Collect nearby faces around each position until accumulated area ≈ component contact area; use merged average normal; position projected onto merged plane and conformed to surface",
        "FORWARD_MODE": "Forward Ref",
        "FORWARD_MODE_DESC": "Reference direction for the forward axis",
        "FORWARD_MODE_SETTING": "Forward Setting (World Y+)",
        "FORWARD_MODE_SETTING_DESC": "Use forward axis setting, reference world forward (Y+)",
        "FORWARD_MODE_ARRANGE": "Along Arrange Direction",
        "FORWARD_MODE_ARRANGE_DESC": "Forward axis aligns with arrange direction (components face the same way in line)",

        # 偏移设置
        "SURFACE_OFFSET": "Surface Offset",
        "SURFACE_OFFSET_DESC": "Component offset distance relative to surface",
        "OFFSET_AXIS": "Linked Axis",
        "OFFSET_AXIS_DESC": "Which component size axis drives the surface offset",
        "OFFSET_PERCENT": "Linked Percent",
        "OFFSET_PERCENT_DESC": "Surface offset = component size on linked axis × percent (auto-filled when size/axis/percent changes)",
        "DISPLAY_MODE": "Empty Display Mode",
        "DISPLAY_MODE_DESC": "Viewport display style for newly placed hardpoints",
        "APPLY_DISPLAY_MODE": "Apply Mode to All Hardpoints",
        "INFO_DISPLAY_APPLIED": "Changed display mode for {} empties",
        "DISPLAY_PLAIN_AXES": "Plain Axes",
        "DISPLAY_ARROWS": "Arrows",
        "DISPLAY_SINGLE_ARROW": "Single Arrow",
        "DISPLAY_CIRCLE": "Circle",
        "DISPLAY_CUBE": "Cube",
        "DISPLAY_SPHERE": "Sphere",
        "DISPLAY_CONE": "Cone",

        "FACE_X_OFFSET": "Face X Offset",
        "FACE_X_OFFSET_DESC": "Offset in face's local X axis direction",

        "FACE_Y_OFFSET": "Face Y Offset",
        "FACE_Y_OFFSET_DESC": "Offset in face's local Y axis direction",

        # 操作器文本
        "PLACER_OPERATOR": "Place Component",
        "PLACER_OPERATOR_DESC": "Component placement operator",

        "MOVE_OPERATOR": "Translate on Surface",
        "MOVE_OPERATOR_DESC": "Surface translation operator",

        "QUICK_MOVE_OPERATOR": "Quick Translate",
        "QUICK_MOVE_OPERATOR_DESC": "Quick translation operator",

        # 方向文本
        "MOVE_DIRECTION": "Move Direction",
        "DIRECTION_LEFT": "Left",
        "DIRECTION_RIGHT": "Right",
        "DIRECTION_FORWARD": "Forward",
        "DIRECTION_BACKWARD": "Backward",
        "DIRECTION_UP": "Up",
        "DIRECTION_DOWN": "Down",

        # 面板文本
        "PANEL_TITLE": "Component Placer",
        "BASIC_SETTINGS": "Basic Settings",
        "ORIENTATION_SETTINGS": "Orientation Settings",
        "MULTI_OBJECT_ARRANGEMENT": "Multi-Object Arrangement",
        "POSITION_OFFSETS": "Position Offsets",
        "TRANSLATION_TOOLS": "Translation Tools",

        "EXECUTE_PLACEMENT": "Execute Placement",
        "PRECISE_TRANSLATION": "Precise Translation",
        "QUICK_TRANSLATION": "Quick Translation",
        "APPLY_PRECISE_TRANSLATION": "Apply Precise Translation",

        # Name replace
        "REPLACE_TITLE": "Name Replace",
        "FIND_TEXT": "Find",
        "REPLACE_TEXT": "Replace With",
        "EXECUTE_REPLACE": "Execute Replace",
        "ERROR_FIND_EMPTY": "Find text cannot be empty",
        "INFO_REPLACED": "Replaced names of {} objects",

        # Mirror tool
        "MIRROR_TITLE": "Mirror Tool",
        "APPLY_MIRROR": "Mirror Selected Hardpoints (X=0)",
        "INFO_MIRRORED": "Mirrored {} hardpoints",

        # Rotate tool
        "ROTATE_TITLE": "Rotate Tool",
        "APPLY_PRECISE_ROTATION": "Apply Precise Rotation",
        "ROTATE_STEP": "Rotate Step",
        "INFO_ROTATED": "Rotated {} components",

        # Batch move
        "BATCH_MOVE": "Batch Move",
        "DIRECTION_SURFACE_OUT": "Away from Surface",
        "DIRECTION_SURFACE_IN": "Toward Surface",
        "DIRECTION_SURFACE_DESC": "Auto-detect each hardpoint's normal direction from its recorded placement orientation (x4_axis_normal); unrecorded ones are treated as Z+",
        "BATCH_FILTER": "Filter Text",
        "BATCH_FILTER_DESC": "Empties whose names contain this text will be batch moved",
        "INFO_BATCH_MOVED": "Moved {} empties along local axes",
        "INFO_BATCH_MOVED_FALLBACK": "Moved {} empties ({} without facing record, treated as Z+; use Maintain Hardpoint Props to backfill)",
        "ERROR_BATCH_FILTER_EMPTY": "Batch filter text is empty",
        "ERROR_BATCH_NO_MATCH": "No empties match the filter text",
        "BATCH_STEP": "Move Step",

        # Axis display
        "TOGGLE_AXIS": "Toggle Axes (All Hardpoints)",
        "INFO_AXIS_ENABLED": "Enabled axis overlay for {} empties",
        "INFO_AXIS_DISABLED": "Disabled axis overlay for {} empties",
        "ERROR_SELECT_EMPTY": "No empty hardpoints in the scene",

        # 平移步长
        "QUICK_STEP": "Quick Step",
        "QUICK_STEP_DESC": "Step distance for quick translation",

        # 错误和提示信息
        "ERROR_EDIT_MODE": "Please enter edit mode and select a mesh object",
        "ERROR_SELECT_FACE": "Please select at least one face in edit mode",
        "ERROR_DATA_CALCULATION": "Error during data calculation: {}",
        "ERROR_OBJECT_CREATION": "Error during object creation: {}",
        "ERROR_SELECT_COMPONENTS": "Please select components to translate",
        "ERROR_NO_NORMAL_INFO": "Selected objects have no associated face normal information",

        "INFO_PLACEMENT_COMPLETE": "Component placement complete: {}",
        "INFO_TRANSLATED_OBJECTS": "Translated {} objects",

        "SELECTED_OBJECTS_COUNT": "Selected {} objects",
        "TRANSLATABLE_COMPONENTS": "✓ {} translatable components",
        "NON_TRANSLATABLE_OBJECTS": "✗ Selected objects cannot be translated",
        "NO_OBJECTS_SELECTED": "No objects selected",

        # 平移属性
        "MOVE_X": "X",
        "MOVE_X_DESC": "Translation along object's local X axis",
        "MOVE_Y": "Y",
        "MOVE_Y_DESC": "Translation along object's local Y axis",
        "MOVE_Z": "Z",
        "ERROR_NO_HARDPOINT": "No hardpoints (components) in the selection",

        # Waypoint import
        "WP_PANEL_TITLE": "Route Editor",
        "WP_XML_PATH": "XML File Path",
        "WP_XML_PATH_DESC": "Path to X4 ship/dockarea XML file",
        "WP_BASE_NAME": "Clear Prefix",
        "WP_NAME_GROUP": "Group Name",
        "WP_NAME_GROUP_DESC": "Group prefix for new waypoints (also written to the group custom property for berth binding); empty means no group",
        "WP_NAME_SEPARATOR": "Separator",
        "WP_NAME_BASE": "Waypoint Name",
        "WP_HP_TYPE": "Hardpoint Type",
        "WP_DEFAULT_HP_TYPE": "Unrecognized As",
        "WP_DEFAULT_HP_TYPE_DESC": "Imported hardpoints whose names contain neither dockingbay nor waypoint are treated as this type",
        "WP_APPLY_GROUP": "Apply Group to Selected",
        "WP_APPLY_GROUP_DESC": "Replace the part left of the first --- in selected hardpoints' names with the group name above (i.e. change their group), syncing the group custom property",
        "WP_APPLY_GROUP_DONE": "Updated {} hardpoint(s) to group '{}'",
        "WP_IMPORT_ERR_DASH3": "dockingbay '{}' name or group contains '{}' (conflicts with the separator); change it to '_' in the XML and re-import",
        "WP_SEP_NOTE": "Panel-wide: shared by Import / Create / Apply-Group / Export",
        "WP_EXPORT_WARN_GROUP_NO_DOCK": "waypoint group '{}' has no matching dockingbay group",
        "WP_EXPORT_WARN_DOCK_NO_WP": "dockingbay group '{}' has no matching waypoint group",
        "WP_SECTION_CREATE": "Create Naming (group+separator+name)",
        "PLACEMENT_MODULE": "Placement",
        "WP_BASE_NAME_DESC": "Clear existing objects starting with this name before import",
        "WP_S_SIZE": "S-size Cube Side",
        "WP_S_SIZE_DESC": "Side length of cube for dockingbay with _s_ in name",
        "WP_M_LENGTH": "M-size Length (Y)",
        "WP_M_LENGTH_DESC": "Y-axis length of box for dockingbay with _m_ in name",
        "WP_M_WIDTH": "M-size Width (X)",
        "WP_M_WIDTH_DESC": "X-axis width of box for dockingbay with _m_ in name",
        "WP_M_HEIGHT": "M-size Height (Z)",
        "WP_M_HEIGHT_DESC": "Z-axis height of box for dockingbay with _m_ in name",
        "WP_DEFAULT_SIZE": "Default Empty Size",
        "WP_DEFAULT_SIZE_DESC": "Default display size of waypoint empties",
        "WP_CLEAR_EXISTING": "Clear Existing Waypoints",
        "WP_CLEAR_EXISTING_DESC": "Delete existing waypoint objects with same name before import",
        "WP_IMPORT": "Import Waypoints & Dockingbays",
        "WP_IMPORT_DESC": "Import waypoints and dockingbay connections from XML and build orientation constraints",
        "WP_INFO_IMPORTED": "Import complete: {} waypoints, {} dockingbay connections, {} orientation constraints",
        "WP_ERROR_XML": "Failed to load XML: {}",
        "WP_ERROR_NO_XML": "Please specify XML file path first",

        # Waypoint edit / export (aligned with x4_wp_export.py format)
        "WP_EDIT_TITLE": "Edit Selected Waypoint",
        "WP_TAGS": "Tags",
        "WP_TAGS_DESC": "tags custom property (space-separated, from vocabulary)",
        "WP_LINKS": "Links",
        "WP_LINKS_DESC": "outgoing target waypoint names (space-separated)",
        "WP_GROUP": "Group",
        "WP_GROUP_DESC": "waypoint group name, exported as group attribute; the game binds it to the dockingbay",
        "WP_EXPORT_WARN_NO_GROUP": "waypoint '{}' has dockpath but no group: other groups exist in this file; parallel per-berth chains need group binding",
        "WP_MAKE_WAYPOINT": "Make Waypoint",
        "WP_MAKE_WAYPOINT_DESC": "Add tags/links custom properties to selected objects",
        "WP_MAKE_WAYPOINT_DONE": "Made {} object(s) into waypoints",
        "WP_ADD": "New Waypoint",
        "WP_ADD_DESC": "Create a new waypoint empty at the 3D cursor",
        "WP_ADD_LINKS_FROM_SEL": "Add Selected as Links",
        "WP_ADD_LINKS_FROM_SEL_DESC": "Append selected objects' names to links (deduplicated, keeps existing)",
        "WP_CLEAR_LINKS": "Clear Links",
        "WP_LINKS_ADDED": "Added links: {}",
        "WP_LINKS_NO_NEW": "No new links to add",
        "WP_REBUILD_CONSTRAINTS": "Rebuild Constraints",
        "WP_CONSTRAINTS_REBUILT": "Rebuilt Track To constraints from links",
        "WP_NO_SELECTED_WAYPOINT": "No waypoint object with tags property selected",
        "WP_EXPORT": "Export Waypoints",
        "WP_EXPORT_DESC": "Export to XML format required by x4_wp_export.py",
        "WP_EXPORT_PATH": "Export Path",
        "WP_EXPORT_PATH_DESC": "Destination file path for the waypoints XML",
        "WP_EXPORT_DONE": "Exported {} waypoint(s) + {} dockingbay(s) ({} warning(s)) → {}",
        "WP_EXPORT_EMPTY": "No hardpoints to export (no selected or in-collection object with hardpoint_type/tags property)",
        "WP_EXPORT_ERR_DASH3": "dockingbay '{}' name or group contains '{}' (conflicts with the separator); change it to '_' and re-export",
        "WP_ERROR_NO_EXPORT_PATH": "Please specify export file path first",
        "WP_TAG_CAT_TYPE": "Type",
        "WP_TAG_CAT_ENDPOINT": "Endpoint",
        "WP_TAG_CAT_GROUP": "Group",
        "WP_TAG_CAT_SIZE": "Size",
        "WP_TAG_CAT_SPEED": "Speed",
        "WP_TAG_CAT_OTHER": "Other",
        "WP_LANE": "Lane",
        "WP_LANE_INBOUND": "Inbound",
        "WP_LANE_OUTBOUND": "Outbound",
        "WP_LANE_MANUAL": "Manual Rot",
        "WP_EXPORT_ERRORS": "Export failed: {} error(s) found",
        "WP_EXPORT_WARN_DOCK": "Warning: no typedock waypoint",
        "WP_EXPORT_WARN_BLOCK": "Warning: blocklink start/end unpaired (start={}, end={})",
        "WP_EXPORT_WARN_SIZE": "Warning: no sizeLevel1 waypoint",
        "WP_EXPORT_WARN_NORMAL": "Warning: typenormal waypoint '{}' has no outgoing links and is not end/dynamicmasstraffic",
        "WP_EXPORT_ERR_DUP": "Waypoint name duplicated: {}",
        "WP_EXPORT_ERR_EMPTY_TAGS": "Waypoint '{}' has empty tags",
        "WP_EXPORT_ERR_BAD_TAG": "Waypoint '{}' has invalid tag '{}'",
        "WP_EXPORT_ERR_LINK": "Waypoint '{}' links to non-existent target '{}'",
        "WP_EXPORT_WARN_LINK_OUTSIDE": "Waypoint '{}' link target '{}' exists in the scene but is outside this export scope",

        # Apply selected component name
        "APPLY_SELECTED_NAME": "Apply Selected Component Name",
        "APPLY_SELECTED_NAME_DESC": "Parse selected component's name and fill back group/name fields",
        "ERROR_NO_SELECTION": "No component selected",
        "ERROR_NAME_FORMAT": "Selected component name format invalid: must use '-' as separator and 'up7con_' as component key",
        "INFO_NAME_APPLIED": "Filled group '{}' and component name '{}'",
    }
}

def t(key, *format_args):
    """自适应语言文本翻译函数"""
    # 获取当前语言
    current_lang = get_supported_language()

    # 获取对应语言的文本字典
    text_dict = MULTI_LANGUAGE_TEXTS.get(current_lang, MULTI_LANGUAGE_TEXTS['en_US'])

    # 获取文本，如果不存在则使用键名作为回退
    text = text_dict.get(key, key)

    # 格式化文本
    if format_args:
        try:
            return text.format(*format_args)
        except Exception:
            return text
    return text

# ============================ 枚举 items 静态列表 ============================
# 注：EnumProperty 的 items 回调函数在部分 Blender 4.2 环境下注册期校验失败
# （"EnumProperty could not register"），因此改回注册时静态生成。
# 枚举显示语言在插件启用时确定；面板标签在 draw() 里实时取词，不受此影响。

_PLACEMENT_MODE_ITEMS = [
    ('SINGLE', t("PLACEMENT_SINGLE"), t("PLACEMENT_SINGLE_DESC")),
    ('MULTI', t("PLACEMENT_MULTI"), t("PLACEMENT_MULTI_DESC")),
]

# v9.13.2：法向轴扩为六向。正向项沿用旧标识符 'X'/'Y'/'Z'（显示文本 X+/Y+/Z+），
# 老场景存储的值直接有效、零迁移；负向为新增标识符。逻辑侧统一 align[0] 取轴字母、
# endswith('-') 取负号。
_ALIGN_AXIS_ITEMS = [
    ('Z', "Z+", t("ALIGN_Z_DESC")),
    ('Y', "Y+", t("ALIGN_Y_DESC")),
    ('X', "X+", t("ALIGN_X_DESC")),
    ('Z-', "Z-", t("ALIGN_Z_MINUS_DESC")),
    ('Y-', "Y-", t("ALIGN_Y_MINUS_DESC")),
    ('X-', "X-", t("ALIGN_X_MINUS_DESC")),
]

_FORWARD_AXIS_ITEMS = [
    ('Y+', t("FORWARD_Y_PLUS"), t("FORWARD_Y_PLUS_DESC")),
    ('Y-', t("FORWARD_Y_MINUS"), t("FORWARD_Y_MINUS_DESC")),
    ('X+', t("FORWARD_X_PLUS"), t("FORWARD_X_PLUS_DESC")),
    ('X-', t("FORWARD_X_MINUS"), t("FORWARD_X_MINUS_DESC")),
    ('Z+', t("FORWARD_Z_PLUS"), t("FORWARD_Z_PLUS_DESC")),
    ('Z-', t("FORWARD_Z_MINUS"), t("FORWARD_Z_MINUS_DESC")),
]

_UP_AXIS_ITEMS = [
    ('Z+', "Z+", t("UP_AXIS_DESC")),
    ('Z-', "Z-", t("UP_AXIS_DESC")),
    ('Y+', "Y+", t("UP_AXIS_DESC")),
    ('Y-', "Y-", t("UP_AXIS_DESC")),
    ('X+', "X+", t("UP_AXIS_DESC")),
    ('X-', "X-", t("UP_AXIS_DESC")),
]

_ARRANGE_DIRECTION_ITEMS = [
    ('X', t("ARRANGE_X"), t("ARRANGE_X_DESC")),
    ('Y', t("ARRANGE_Y"), t("ARRANGE_Y_DESC")),
    ('LONG_EDGE', t("ARRANGE_LONG_EDGE"), t("ARRANGE_LONG_EDGE_DESC")),
]

_DIRECTION_ITEMS = [
    ('LEFT', t("DIRECTION_LEFT"), ""),
    ('RIGHT', t("DIRECTION_RIGHT"), ""),
    ('FORWARD', t("DIRECTION_FORWARD"), ""),
    ('BACKWARD', t("DIRECTION_BACKWARD"), ""),
    ('UP', t("DIRECTION_UP"), ""),
    ('DOWN', t("DIRECTION_DOWN"), ""),
]

# 批量平移专用方向：贴近/远离面置顶（v9.14.2 用户指定，自适应法向最常用）+ 六向局部轴。
# 贴近/远离面按每个硬点记录的 x4_axis_normal（放置时的法向轴，含负号）逐物体判断，
# 无记录的老硬点回退 Z+；快捷平移仍用上面的 _DIRECTION_ITEMS，不受影响。
_BATCH_DIRECTION_ITEMS = [
    ('SURFACE_OUT', t("DIRECTION_SURFACE_OUT"), t("DIRECTION_SURFACE_DESC")),
    ('SURFACE_IN', t("DIRECTION_SURFACE_IN"), t("DIRECTION_SURFACE_DESC")),
] + _DIRECTION_ITEMS

_OFFSET_AXIS_ITEMS = [
    ('X', "X", ""),
    ('Y', "Y", ""),
    ('Z', "Z", ""),
]

_EMPTY_DISPLAY_ITEMS = [
    ('ARROWS', t("DISPLAY_ARROWS"), t("DISPLAY_MODE_DESC")),
    ('PLAIN_AXES', t("DISPLAY_PLAIN_AXES"), t("DISPLAY_MODE_DESC")),
    ('SINGLE_ARROW', t("DISPLAY_SINGLE_ARROW"), t("DISPLAY_MODE_DESC")),
    ('CIRCLE', t("DISPLAY_CIRCLE"), t("DISPLAY_MODE_DESC")),
    ('CUBE', t("DISPLAY_CUBE"), t("DISPLAY_MODE_DESC")),
    ('SPHERE', t("DISPLAY_SPHERE"), t("DISPLAY_MODE_DESC")),
    ('CONE', t("DISPLAY_CONE"), t("DISPLAY_MODE_DESC")),
]

def _update_surface_offset(self, context):
    """表面偏移联动：表面偏移 = 组件尺寸联动方向长度 × 百分比"""
    axis_index = {'X': 0, 'Y': 1, 'Z': 2}[self.offset_axis]
    self.surface_offset = self.comp_size[axis_index] * self.offset_percent / 100.0
    # 间距自动跟随：组件最长边 × 1.3（仅在自动模式下）
    if getattr(self, 'spacing_auto', True):
        global _spacing_updating
        _spacing_updating = True
        self.spacing = max(self.comp_size) * 1.3
        _spacing_updating = False


def _update_spacing_manual(self, context):
    """用户手动改 spacing 时，停止自动跟随尺寸（程序自动设置时不触发）"""
    global _spacing_updating
    if not _spacing_updating:
        self.spacing_auto = False


# 组件名称四栏默认值：组名 + 链接符 + 组件key + 名称
# 组名按用户要求默认 left_g_01（9.14.4 起，曾用 left_con_g_01）；key 标识本工具放置的硬点（膨胀/收缩识别 + XML 工具剥离）
# 名称段取 DEFAULT_VALUES["COMP_NAME"] 的名称部分（数据块原值保留不动）
_DEFAULT_NAME_GROUP = "left_g_01"
_DEFAULT_NAME_SEPARATOR = "-"
_DEFAULT_COMP_KEY = "up7con_"
_DEFAULT_NAME_BASE = DEFAULT_VALUES["COMP_NAME"].partition("-")[2] or DEFAULT_VALUES["COMP_NAME"]
_DEFAULT_COMP_NAME_FULL = (
    _DEFAULT_NAME_GROUP + _DEFAULT_NAME_SEPARATOR + _DEFAULT_COMP_KEY + _DEFAULT_NAME_BASE
)

_COMP_NAME_SYNC = [False]  # 四栏 ↔ 完整名称 双向同步的防循环标记


def _update_name_parts(self, context):
    """组名/链接符号/组件key/名称 任一栏变化 → 拼接为完整组件名称
    拼接 = 组名 + 链接符 + 组件key + 名称，空栏按空字符串处理"""
    if _COMP_NAME_SYNC[0]:
        return
    _COMP_NAME_SYNC[0] = True
    try:
        full = f"{self.name_group}{self.name_separator}{self.name_key}{self.name_base}"
        if self.comp_name != full:
            self.comp_name = full
    finally:
        _COMP_NAME_SYNC[0] = False


def _sync_name_parts(self, context):
    """完整组件名称变化（如选择预设写入）→ 只回填名称栏。
    组名/链接符/组件key 是用户自由输入（可含任意字符包括链接符本身），永不反向覆盖：
    仅当 comp_name 以"组名+链接符+key"已知前缀开头时按前缀剥离，否则整体归入名称栏"""
    if _COMP_NAME_SYNC[0]:
        return
    _COMP_NAME_SYNC[0] = True
    try:
        base = self.comp_name
        prefix = self.name_group + self.name_separator + self.name_key \
            if (self.name_group or self.name_separator or self.name_key) else ""
        if prefix and self.comp_name.startswith(prefix):
            base = self.comp_name[len(prefix):]
        if self.name_base != base:
            self.name_base = base
    finally:
        _COMP_NAME_SYNC[0] = False

# 预设列表（必须在 MULTI_LANGUAGE_TEXTS 定义之后构建）
_PRESET_ITEMS = _build_preset_items()

# ============================ 主代码开始 ============================

class ComponentSettingsFixed(bpy.types.PropertyGroup):
    """组件设置属性组"""

    # 预设选择属性
    preset_index: bpy.props.EnumProperty(
        name=t("PRESET_SELECTION"),
        description=t("PRESET_SELECTION_DESC"),
        items=_PRESET_ITEMS,
        update=lambda self, context: self.apply_preset()
    )

    comp_name: bpy.props.StringProperty(
        name=t("COMP_NAME"),
        default=_DEFAULT_COMP_NAME_FULL,  # 三栏默认拼接值（DEFAULT_VALUES 原值保留，仅名称段被引用）
        update=_sync_name_parts,  # 预设等直接写入完整名称时反向拆分到三栏
        description=t("COMP_NAME_DESC")
    )

    name_group: bpy.props.StringProperty(
        name=t("NAME_GROUP"),
        default=_DEFAULT_NAME_GROUP,
        update=_update_name_parts,
        description=t("NAME_GROUP_DESC")
    )

    name_separator: bpy.props.StringProperty(
        name=t("NAME_SEPARATOR"),
        default=_DEFAULT_NAME_SEPARATOR,
        update=_update_name_parts,
        description=t("NAME_SEPARATOR_DESC")
    )

    name_key: bpy.props.StringProperty(
        name=t("NAME_KEY"),
        default=_DEFAULT_COMP_KEY,
        update=_update_name_parts,
        description=t("NAME_KEY_DESC")
    )

    inflate_scope: bpy.props.EnumProperty(
        name=t("INFLATE_SCOPE"),
        items=[
            ('ALL', t("INFLATE_SCOPE_ALL"), "Parent every hardpoint empty whose name contains the component key"),
            ('SELECTED', t("INFLATE_SCOPE_SELECTED"), "Parent only the selected hardpoint empties"),
        ],
        default='ALL',
        description="Scope of hardpoints to parent when clicking 建立父子绑定",
    )

    name_base: bpy.props.StringProperty(
        name=t("COMP_NAME"),
        default=_DEFAULT_NAME_BASE,
        update=_update_name_parts,
        description=t("COMP_NAME_DESC")
    )

    maintain_filter: bpy.props.StringProperty(
        name=t("MAINTAIN_FILTER"),
        default="",
        description=t("MAINTAIN_FILTER_DESC")
    )

    # ===== 航路点导入配置 =====
    wp_xml_path: bpy.props.StringProperty(
        name=t("WP_XML_PATH"),
        default="",
        subtype='FILE_PATH',
        description=t("WP_XML_PATH_DESC")
    )

    wp_base_name: bpy.props.StringProperty(
        name=t("WP_BASE_NAME"),
        default="waypoint",
        description=t("WP_BASE_NAME_DESC")
    )

    wp_name_group: bpy.props.StringProperty(
        name=t("WP_NAME_GROUP"),
        default="mt_dock_01",
        description=t("WP_NAME_GROUP_DESC")
    )

    wp_name_separator: bpy.props.StringProperty(
        name=t("WP_NAME_SEPARATOR"),
        default="---"
    )

    wp_name_base: bpy.props.StringProperty(
        name=t("WP_NAME_BASE"),
        default="waypoint"
    )

    wp_default_hp_type: bpy.props.EnumProperty(
        name=t("WP_DEFAULT_HP_TYPE"),
        items=[("waypoint", "waypoint", ""), ("dockingbay", "dockingbay", "")],
        default="waypoint",
        description=t("WP_DEFAULT_HP_TYPE_DESC")
    )

    wp_s_size: bpy.props.FloatProperty(
        name=t("WP_S_SIZE"),
        default=20.0,
        min=0.1,
        description=t("WP_S_SIZE_DESC")
    )

    wp_m_length: bpy.props.FloatProperty(
        name=t("WP_M_LENGTH"),
        default=91.0,
        min=0.1,
        description=t("WP_M_LENGTH_DESC")
    )

    wp_m_width: bpy.props.FloatProperty(
        name=t("WP_M_WIDTH"),
        default=57.0,
        min=0.1,
        description=t("WP_M_WIDTH_DESC")
    )

    wp_m_height: bpy.props.FloatProperty(
        name=t("WP_M_HEIGHT"),
        default=20.0,
        min=0.1,
        description=t("WP_M_HEIGHT_DESC")
    )

    wp_default_size: bpy.props.FloatProperty(
        name=t("WP_DEFAULT_SIZE"),
        default=20.0,
        min=0.1,
        description=t("WP_DEFAULT_SIZE_DESC")
    )

    wp_clear_existing: bpy.props.BoolProperty(
        name=t("WP_CLEAR_EXISTING"),
        default=True,
        description=t("WP_CLEAR_EXISTING_DESC")
    )

    wp_export_path: bpy.props.StringProperty(
        name=t("WP_EXPORT_PATH"),
        default="",
        subtype='FILE_PATH',
        description=t("WP_EXPORT_PATH_DESC")
    )

    comp_size: bpy.props.FloatVectorProperty(
        name=t("COMP_SIZE"),
        default=DEFAULT_VALUES["COMP_SIZE"],
        subtype='XYZ',
        min=0.1,
        update=_update_surface_offset,  # 尺寸变化时联动刷新表面偏移
        description=t("COMP_SIZE_DESC")
    )

    placement_mode: bpy.props.EnumProperty(
        name=t("PLACEMENT_MODE"),
        items=_PLACEMENT_MODE_ITEMS,
        default=DEFAULT_VALUES["PLACEMENT_MODE"],
        description=t("PLACEMENT_MODE_DESC")
    )

    align_axis: bpy.props.EnumProperty(
        name=t("ALIGN_AXIS"),
        items=_ALIGN_AXIS_ITEMS,
        default=DEFAULT_VALUES["ALIGN_AXIS"],
        description=t("ALIGN_AXIS_DESC")
    )

    forward_axis: bpy.props.EnumProperty(
        name=t("FORWARD_AXIS"),
        items=_FORWARD_AXIS_ITEMS,
        default=DEFAULT_VALUES["FORWARD_AXIS"],
        description=t("FORWARD_AXIS_DESC")
    )

    up_axis: bpy.props.EnumProperty(
        name=t("UP_AXIS"),
        items=_UP_AXIS_ITEMS,
        default='Z+',
        description=t("UP_AXIS_DESC")
    )

    arrange_direction: bpy.props.EnumProperty(
        name=t("ARRANGE_DIRECTION"),
        items=_ARRANGE_DIRECTION_ITEMS,
        default=DEFAULT_VALUES["ARRANGE_DIRECTION"],
        description=t("ARRANGE_DIRECTION_DESC")
    )

    spacing: bpy.props.FloatProperty(
        name=t("SPACING"),
        default=DEFAULT_VALUES["SPACING"],
        min=0.5,
        update=_update_spacing_manual,
        description=t("SPACING_DESC")
    )

    spacing_auto: bpy.props.BoolProperty(default=True)  # 隐藏属性：间距是否自动跟随组件尺寸

    auto_count: bpy.props.BoolProperty(
        name=t("AUTO_COUNT"),
        default=DEFAULT_VALUES["AUTO_COUNT"],
        description=t("AUTO_COUNT_DESC")
    )

    manual_count: bpy.props.IntProperty(
        name=t("MANUAL_COUNT"),
        default=DEFAULT_VALUES["MANUAL_COUNT"],
        min=2,
        max=50,
        description=t("MANUAL_COUNT_DESC")
    )

    placement_surface_mode: bpy.props.EnumProperty(
        name=t("SURFACE_MODE"),
        items=[
            ('PLANE', t("SURFACE_MODE_PLANE"), t("SURFACE_MODE_PLANE_DESC")),
            ('FACE_MERGE', t("SURFACE_MODE_FACE_MERGE"), t("SURFACE_MODE_FACE_MERGE_DESC")),
            ('FACE_CENTER', t("SURFACE_MODE_FACE_CENTER"), t("SURFACE_MODE_FACE_CENTER_DESC")),
        ],
        default='PLANE',
        description=t("SURFACE_MODE_DESC")
    )

    placement_forward_mode: bpy.props.EnumProperty(
        name=t("FORWARD_MODE"),
        items=[
            ('SETTING', t("FORWARD_MODE_SETTING"), t("FORWARD_MODE_SETTING_DESC")),
            ('ARRANGE', t("FORWARD_MODE_ARRANGE"), t("FORWARD_MODE_ARRANGE_DESC")),
        ],
        default='SETTING',
        description=t("FORWARD_MODE_DESC")
    )

    surface_offset: bpy.props.FloatProperty(
        name=t("SURFACE_OFFSET"),
        default=DEFAULT_VALUES["SURFACE_OFFSET"],
        description=t("SURFACE_OFFSET_DESC")
    )

    offset_axis: bpy.props.EnumProperty(
        name=t("OFFSET_AXIS"),
        items=_OFFSET_AXIS_ITEMS,
        default='Z',
        update=_update_surface_offset,
        description=t("OFFSET_AXIS_DESC")
    )

    offset_percent: bpy.props.FloatProperty(
        name=t("OFFSET_PERCENT"),
        default=-50.0,  # 默认负值：组件默认内嵌表面一半高度（用户指定）
        min=-100.0,
        max=1000.0,
        subtype='PERCENTAGE',
        update=_update_surface_offset,
        description=t("OFFSET_PERCENT_DESC")
    )

    empty_display_mode: bpy.props.EnumProperty(
        name=t("DISPLAY_MODE"),
        items=_EMPTY_DISPLAY_ITEMS,
        default='ARROWS',
        description=t("DISPLAY_MODE_DESC")
    )

    face_x_offset: bpy.props.FloatProperty(
        name=t("FACE_X_OFFSET"),
        default=DEFAULT_VALUES["FACE_X_OFFSET"],
        description=t("FACE_X_OFFSET_DESC")
    )

    face_y_offset: bpy.props.FloatProperty(
        name=t("FACE_Y_OFFSET"),
        default=DEFAULT_VALUES["FACE_Y_OFFSET"],
        description=t("FACE_Y_OFFSET_DESC")
    )

    def apply_preset(self):
        """应用预设的回调函数"""
        # 防止递归调用
        if hasattr(self, '_applying_preset') and self._applying_preset:
            return

        self._applying_preset = True
        try:
            preset_index = int(self.preset_index)
            if 0 <= preset_index < len(COMPONENT_PRESETS):
                name, size, comp_name = COMPONENT_PRESETS[preset_index]

                # 应用预设尺寸（自定义预设也统一应用，便于用户快速改回预设值）
                self.comp_size = size

                # 应用非空且非占位的名称（"自定义"预设的 customize 只是内部标记）
                # 预设名称只取"名称段"；回填规则（v9.13.6 修正）：
                #   组名与链接符"同时为空"→ 回填该组默认值（9.12.2 起保护"组名空+链接符
                #   前缀模式"，故两栏作为一组判断）；组件key 单独为空 → 回填默认 key。
                #   key 不得参与组名/链接符的组合条件——key 栏恒有默认值，纳入会导致
                #   回填永不触发（9.13.0 回归）
                if comp_name and comp_name.strip() and comp_name not in PLACEHOLDER_COMP_NAMES:
                    _COMP_NAME_SYNC[0] = True  # 回填期间锁定联动，避免中间态串写
                    try:
                        if not self.name_group.strip() and not self.name_separator.strip():
                            self.name_separator = _DEFAULT_NAME_SEPARATOR
                            self.name_group = _DEFAULT_NAME_GROUP
                        if not self.name_key.strip():
                            self.name_key = _DEFAULT_COMP_KEY
                    finally:
                        _COMP_NAME_SYNC[0] = False
                    base = comp_name.partition("-")[2] or comp_name
                    self.comp_name = f"{self.name_group}{self.name_separator}{self.name_key}{base}"  # 触发反向拆分，四栏同步

                print(f"预设应用: {name}, 尺寸: {size}, 组件: {comp_name}")

        except (ValueError, IndexError) as e:
            print(f"预设应用错误: {e}")
        finally:
            self._applying_preset = False

    # ===== 整理舰船网格（EVE→X4 命名规范） =====
    ship_mesh_prefix: bpy.props.StringProperty(
        name=t("SHIP_MESH_PREFIX"),
        default="",
        description=t("SHIP_MESH_PREFIX_DESC")
    )

    ship_mesh_mat_name: bpy.props.StringProperty(
        name=t("SHIP_MESH_MAT_NAME"),
        default="evemate",
        description=t("SHIP_MESH_MAT_NAME_DESC")
    )

    ship_mesh_wreck_mat: bpy.props.StringProperty(
        name=t("SHIP_MESH_WRECK_MAT"),
        default="p2generic.gen_p2_wreck_01",
        description=t("SHIP_MESH_WRECK_MAT_DESC")
    )

    ship_mesh_lod_mode: bpy.props.EnumProperty(
        name=t("SHIP_MESH_LOD_MODE"),
        items=[
            ('COPY', t("SHIP_MESH_MODE_COPY"), t("SHIP_MESH_MODE_COPY_DESC")),
            ('DECIMATE', t("SHIP_MESH_MODE_DECIMATE"), t("SHIP_MESH_MODE_DECIMATE_DESC")),
        ],
        default='COPY',
        description=t("SHIP_MESH_LOD_MODE_DESC")
    )

    ship_mesh_lod1_ratio: bpy.props.FloatProperty(
        name=t("SHIP_MESH_LOD1_RATIO"),
        default=0.6, min=0.01, max=1.0,
        description=t("SHIP_MESH_LOD_RATIO_DESC")
    )

    ship_mesh_lod2_ratio: bpy.props.FloatProperty(
        name=t("SHIP_MESH_LOD2_RATIO"),
        default=0.3, min=0.01, max=1.0,
        description=t("SHIP_MESH_LOD_RATIO_DESC")
    )

    ship_mesh_lod3_ratio: bpy.props.FloatProperty(
        name=t("SHIP_MESH_LOD3_RATIO"),
        default=0.1, min=0.01, max=1.0,
        description=t("SHIP_MESH_LOD_RATIO_DESC")
    )

    ship_mesh_wreck_mode: bpy.props.EnumProperty(
        name=t("SHIP_MESH_WRECK_MODE"),
        items=[
            ('COPY', t("SHIP_MESH_MODE_COPY"), t("SHIP_MESH_MODE_COPY_DESC")),
            ('DECIMATE', t("SHIP_MESH_MODE_DECIMATE"), t("SHIP_MESH_MODE_DECIMATE_DESC")),
        ],
        default='COPY',
        description=t("SHIP_MESH_WRECK_MODE_DESC")
    )

    ship_mesh_wreck_ratio: bpy.props.FloatProperty(
        name=t("SHIP_MESH_WRECK_RATIO"),
        default=0.5, min=0.01, max=1.0,
        description=t("SHIP_MESH_LOD_RATIO_DESC")
    )

    ship_mesh_create_collision: bpy.props.BoolProperty(
        name=t("SHIP_MESH_CREATE_COLLISION"),
        default=False,
        description=t("SHIP_MESH_CREATE_COLLISION_DESC")
    )


class TranslationSettingsFixed(bpy.types.PropertyGroup):
    """平移工具设置属性组（收拢原先散挂在 Scene 上的属性）"""

    move_fx: bpy.props.FloatProperty(
        name=t("MOVE_X"),
        default=DEFAULT_VALUES["MOVE_X"],
        step=0.1,
        description=t("MOVE_X_DESC")
    )
    move_fy: bpy.props.FloatProperty(
        name=t("MOVE_Y"),
        default=DEFAULT_VALUES["MOVE_Y"],
        step=0.1,
        description=t("MOVE_Y_DESC")
    )
    move_fz: bpy.props.FloatProperty(
        name=t("MOVE_Z"),
        default=DEFAULT_VALUES["MOVE_Z"],
        step=0.1,
        description=t("MOVE_Z_DESC")
    )
    quick_step_fixed: bpy.props.FloatProperty(
        name=t("QUICK_STEP"),
        default=0.5,
        min=0.05,
        step=0.1,
        description=t("QUICK_STEP_DESC")
    )

    # 批量平移（筛选文本 + 方向 + 独立步长）
    batch_move_filter: bpy.props.StringProperty(default="", name=t("BATCH_FILTER"), description=t("BATCH_FILTER_DESC"))
    batch_move_direction: bpy.props.EnumProperty(name=t("MOVE_DIRECTION"), items=_BATCH_DIRECTION_ITEMS, default='UP')
    batch_move_step: bpy.props.FloatProperty(name=t("BATCH_STEP"), default=0.5, min=0.001, step=0.1)

    # 名称替换
    replace_find: bpy.props.StringProperty(default="", name=t("FIND_TEXT"))
    replace_with: bpy.props.StringProperty(default="", name=t("REPLACE_TEXT"))

    # 精确旋转角度（度）
    rotate_fx: bpy.props.FloatProperty(default=0.0, name="X")
    rotate_fy: bpy.props.FloatProperty(default=0.0, name="Y")
    rotate_fz: bpy.props.FloatProperty(default=0.0, name="Z")

    # 快捷旋转步长（度）
    rotate_step: bpy.props.FloatProperty(default=15.0, min=0.1, name=t("ROTATE_STEP"))

# ============================ 组件命名工具 ============================

def strip_sequence_suffix(base_name):
    """仅剥离自动生成的"-三位数字"序号后缀（如 turret-001），
    避免把组件名本身的数字结尾（如 turret-2）误拆"""
    parts = base_name.split('-')
    if len(parts) > 1 and parts[-1].isdigit() and len(parts[-1]) == 3:
        return '-'.join(parts[:-1])
    return base_name

def get_next_available_sequence(base_name, scene):
    """获取下一个可用的序号"""
    max_sequence = 0
    prefix = base_name + "-"

    for obj in scene.objects:
        if obj.name.startswith(prefix):
            suffix = obj.name[len(prefix):]
            if suffix.isdigit():
                max_sequence = max(max_sequence, int(suffix))
            else:
                # 处理可能的后缀（如 .001, .002 等）
                base_suffix = suffix.split('.')[0]
                if base_suffix.isdigit():
                    max_sequence = max(max_sequence, int(base_suffix))

    return max_sequence + 1

# ============================ 几何算法函数 ============================

def _rotating_test_dir(angle_deg, face_normal):
    """生成面上给定角度(度)的测试方向，旋转到面平面"""
    angle_rad = math.radians(angle_deg)
    test_dir = Vector((math.cos(angle_rad), math.sin(angle_rad), 0))
    if abs(face_normal.dot(Vector((0, 0, 1)))) < 0.9:
        rotation_axis = face_normal.cross(Vector((0, 0, 1))).normalized()
        rotation_angle = face_normal.angle(Vector((0, 0, 1)))
        rotation_matrix = Matrix.Rotation(rotation_angle, 4, rotation_axis)
        test_dir = (rotation_matrix.to_3x3() @ test_dir.to_3d()).normalized()
    # 投影到面平面
    test_dir = test_dir - face_normal * test_dir.dot(face_normal)
    if test_dir.length < 0.001:
        return None
    return test_dir.normalized()

def _rotating_box_area(test_dir, face_normal, points):
    """计算点在给定方向系下的包围矩形面积，返回(面积, 长边方向)"""
    perpendicular = test_dir.cross(face_normal).normalized()
    dots_parallel = [Vector(p).dot(test_dir) for p in points]
    dots_perp = [Vector(p).dot(perpendicular) for p in points]
    width = max(dots_parallel) - min(dots_parallel)
    height = max(dots_perp) - min(dots_perp)
    area = width * height
    direction = test_dir if width >= height else perpendicular
    return area, direction

def calculate_min_bbox_direction(points, face_normal):
    """两阶段角度扫描逼近最小面积包围盒的主轴方向（粗扫0-180°/2°步长 + 细扫±1.5°/0.5°步长）
    注：未做凸包预处理，直接对全部点扫描，结果等价（包围盒只由凸包顶点决定）"""
    if len(points) < 3:
        return None
    points_array = np.array([(p.x, p.y, p.z) for p in points])

    # 阶段1：粗扫 0-180°，2°步长找到全局最优角
    min_area = float('inf')
    best_direction = None
    best_angle = 0.0
    for angle_deg in range(0, 180, 2):
        test_dir = _rotating_test_dir(angle_deg, face_normal)
        if test_dir is None:
            continue
        area, direction = _rotating_box_area(test_dir, face_normal, points_array)
        if area < min_area:
            min_area = area
            best_direction = direction
            best_angle = angle_deg

    # 阶段2：在最优角附近细扫 ±1.5°，0.5°步长精化
    if best_direction is not None:
        refined_start = best_angle - 1.5
        refined_end = best_angle + 1.5
        angle_deg = refined_start
        while angle_deg <= refined_end:
            test_dir = _rotating_test_dir(angle_deg, face_normal)
            if test_dir is not None:
                area, direction = _rotating_box_area(test_dir, face_normal, points_array)
                if area < min_area:
                    min_area = area
                    best_direction = direction
            angle_deg += 0.5

    return best_direction

def calculate_pca_direction(points, face_normal):
    """主成分分析 - 计算点云的主要延伸方向"""
    if len(points) < 3:
        return None

    points_array = np.array([(p.x, p.y, p.z) for p in points])
    centroid = np.mean(points_array, axis=0)
    centered_points = points_array - centroid

    # 计算协方差矩阵（实对称矩阵，用 eigh 避免 eig 的复数特征向量风险）
    cov_matrix = np.cov(centered_points.T)
    eigenvalues, eigenvectors = np.linalg.eigh(cov_matrix)

    # 找到最大特征值对应的特征向量
    main_idx = np.argmax(eigenvalues)
    main_direction = Vector(eigenvectors[:, main_idx])

    # 投影到面平面
    main_direction = main_direction - face_normal * main_direction.dot(face_normal)

    if main_direction.length < 0.001:
        return None

    return main_direction.normalized()

def calculate_quadrilateral_medial_axis(verts_world, face_normal):
    """计算四边形的中轴/中心线"""
    if len(verts_world) != 4:
        return None

    # 计算对边中点连线
    mid1 = (verts_world[0] + verts_world[1]) / 2
    mid2 = (verts_world[2] + verts_world[3]) / 2
    mid3 = (verts_world[1] + verts_world[2]) / 2
    mid4 = (verts_world[3] + verts_world[0]) / 2

    # 计算两条中线
    midline1 = mid2 - mid1
    midline2 = mid4 - mid3

    # 选择较长的中线
    if midline1.length >= midline2.length:
        medial_axis = midline1
    else:
        medial_axis = midline2

    # 投影到面平面
    medial_axis = medial_axis - face_normal * medial_axis.dot(face_normal)

    if medial_axis.length < 0.001:
        return None

    return medial_axis.normalized()

def calculate_triangle_long_axis(verts_world, face_normal):
    """计算三角形的长轴方向：取最长边方向（投影到面平面）。
    注：不能用"顶点→最长边中点"的中线——细长三角形下该中线指向短轴，与长边方向差 90°"""
    if len(verts_world) != 3:
        return None

    edges = [
        verts_world[1] - verts_world[0],
        verts_world[2] - verts_world[1],
        verts_world[0] - verts_world[2],
    ]

    longest_edge = max(edges, key=lambda e: e.length)

    # 投影到面平面
    axis = longest_edge - face_normal * longest_edge.dot(face_normal)

    if axis.length < 0.001:
        return None

    return axis.normalized()

def calculate_improved_long_edge_direction(obj, face_normal, bm):
    """改进的长边方向检测 - 使用多层几何算法
    bm 由调用方传入（编辑模式 bmesh 归 Blender 所有，此处只读，不 free）"""
    selected_faces = [face for face in bm.faces if face.select]

    if not selected_faces:
        return None

    # 收集所有顶点（世界坐标）
    all_verts_world = []
    face_verts_map = {}

    for i, face in enumerate(selected_faces):
        verts_world = [obj.matrix_world @ vert.co for vert in face.verts]
        all_verts_world.extend(verts_world)
        face_verts_map[i] = verts_world

    if len(all_verts_world) < 3:
        return None

    # 方法1: 针对特定面类型的专用算法
    if len(selected_faces) == 1:
        face = selected_faces[0]
        verts_world = face_verts_map[0]

        if len(face.verts) == 4:  # 四边形
            direction = calculate_quadrilateral_medial_axis(verts_world, face_normal)
            if direction:
                return direction
        elif len(face.verts) == 3:  # 三角形
            direction = calculate_triangle_long_axis(verts_world, face_normal)
            if direction:
                return direction

    # 方法2: 最小包围盒主轴扫描
    direction = calculate_min_bbox_direction(all_verts_world, face_normal)
    if direction:
        return direction

    # 方法3: 主成分分析
    direction = calculate_pca_direction(all_verts_world, face_normal)
    if direction:
        return direction

    # 方法4: 边界框回退
    min_co = Vector(all_verts_world[0])
    max_co = Vector(all_verts_world[0])

    for vert in all_verts_world:
        min_co.x = min(min_co.x, vert.x)
        min_co.y = min(min_co.y, vert.y)
        min_co.z = min(min_co.z, vert.z)
        max_co.x = max(max_co.x, vert.x)
        max_co.y = max(max_co.y, vert.y)
        max_co.z = max(max_co.z, vert.z)

    bbox_size = max_co - min_co
    if bbox_size.x >= bbox_size.y and bbox_size.x >= bbox_size.z:
        direction = Vector((1, 0, 0))
    elif bbox_size.y >= bbox_size.x and bbox_size.y >= bbox_size.z:
        direction = Vector((0, 1, 0))
    else:
        direction = Vector((0, 0, 1))

    # 投影到面平面
    direction = direction - face_normal * direction.dot(face_normal)

    return direction.normalized() if direction.length > 0.001 else None

def get_selected_faces_data(obj, bm):
    """获取选中面的几何数据
    bm 由调用方传入（编辑模式 bmesh 归 Blender 所有，此处只读，不 free）
    返回 dict：center(平均中心世界坐标), normal(平均法线世界坐标), face_count,
               faces=[{'center':世界中心, 'normal':世界法线, 'area':面积}, ...]"""
    selected_faces = [face for face in bm.faces if face.select]

    if not selected_faces:
        return None

    matrix_world = obj.matrix_world
    normal_matrix = matrix_world.to_3x3().inverted_safe().transposed()

    # 计算加权中心点和法线
    total_area = 0.0
    weighted_center = Vector()
    weighted_normal = Vector()
    face_list = []

    for face in selected_faces:
        area = face.calc_area()
        face_center_local = face.calc_center_median()
        face_normal_local = face.normal

        # 转换为世界坐标
        face_center_world = matrix_world @ face_center_local
        face_normal_world = normal_matrix @ face_normal_local
        if face_normal_world.length < 1e-6:
            face_normal_world = matrix_world.to_3x3() @ face_normal_local
        face_normal_world = face_normal_world.normalized()

        total_area += area
        weighted_center += face_center_local * area
        weighted_normal += face_normal_local * area

        face_list.append({
            'center': face_center_world,
            'normal': face_normal_world,
            'area': area,
        })

    if total_area > 0:
        avg_center_local = weighted_center / total_area
        avg_normal_local = weighted_normal / total_area
    else:
        centers = [face.calc_center_median() for face in selected_faces]
        normals = [face.normal for face in selected_faces]
        avg_center_local = sum(centers, Vector()) / len(centers)
        avg_normal_local = sum(normals, Vector())

    # 法线加权和可能因朝向相反的面相互抵消而趋零，回退到面积最大的面的法线
    if avg_normal_local.length < 1e-6:
        biggest_face = max(selected_faces, key=lambda f: f.calc_area())
        avg_normal_local = biggest_face.normal.copy()
    else:
        avg_normal_local = avg_normal_local.normalized()

    # 转换为世界坐标：点用完整矩阵；法线用逆转置矩阵（兼容非均匀缩放）
    avg_center_world = matrix_world @ avg_center_local
    avg_normal_world = normal_matrix @ avg_normal_local
    if avg_normal_world.length < 1e-6:
        avg_normal_world = matrix_world.to_3x3() @ avg_normal_local
    avg_normal_world = avg_normal_world.normalized()

    return {
        'center': avg_center_world,
        'normal': avg_normal_world,
        'face_count': len(selected_faces),
        'faces': face_list,
    }

class COMPONENT_OT_placer(bpy.types.Operator):
    """组件放置操作器"""
    bl_idname = "component_fixed.placer"
    bl_label = t("PLACER_OPERATOR")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        settings = scene.component_settings_fixed
        obj = context.active_object

        if not obj or obj.mode != 'EDIT':
            self.report({'ERROR'}, t("ERROR_EDIT_MODE"))
            return {'CANCELLED'}

        # 在切换到对象模式前获取所有必要数据
        # 编辑模式 bmesh 由 Blender 持有：只读使用、一次获取全程传递、不 free
        try:
            bm = bmesh.from_edit_mesh(obj.data)

            # 获取面数据
            face_data = get_selected_faces_data(obj, bm)
            if not face_data:
                self.report({'ERROR'}, t("ERROR_SELECT_FACE"))
                return {'CANCELLED'}

            # 如果是多物体排列且需要长边数据，提前计算
            bbox_data = None
            if settings.placement_mode == 'MULTI' and settings.arrange_direction == 'LONG_EDGE':
                bbox_data = calculate_improved_long_edge_direction(obj, face_data['normal'], bm)

            # 计算位置 + 每个位置的法线列表
            if settings.placement_mode == 'SINGLE':
                positions = [self.calculate_single_position(face_data, settings)]
                normals = [face_data['normal']]
                count = 1
                arrange_axis = None
            else:
                positions, normals, count, arrange_axis = self.calculate_multiple_positions(
                    face_data, settings, obj, bm, bbox_data)

        except Exception as e:
            self.report({'ERROR'}, t("ERROR_DATA_CALCULATION", str(e)))
            return {'CANCELLED'}

        # 现在切换到对象模式
        bpy.ops.object.mode_set(mode='OBJECT')

        try:
            # 创建组件对象（每个组件用自己的法线+旋转）
            component_objs = self._create_components(
                context, positions, normals, arrange_axis, settings)
        except Exception as e:
            self.report({'ERROR'}, t("ERROR_OBJECT_CREATION", str(e)))
            return {'CANCELLED'}

        # v9.2.1：放置后不选中新组件，直接切回主物体的编辑模式
        # （Blender 保留编辑模式的面选择；需要平移/旋转组件时再手动选中硬点）
        bpy.ops.object.mode_set(mode='EDIT')

        return {'FINISHED'}

    def _create_components(self, context, positions, normals, arrange_axis, settings):
        """创建全部组件空物体（每个组件用自己的法线计算旋转与抬升）"""
        size_index = {'X': 0, 'Y': 1, 'Z': 2}[settings.align_axis[0]]
        half_size = settings.comp_size[size_index] / 2

        # 朝向参考方向：SETTING 用世界Y+，ARRANGE 用排列方向
        forward_ref = None
        if settings.placement_forward_mode == 'ARRANGE' and arrange_axis is not None:
            forward_ref = arrange_axis.normalized()

        # 获取基础名称（剥离自动序号后缀）
        base_name = strip_sequence_suffix(settings.comp_name)

        # 获取下一个可用的序号
        next_sequence = get_next_available_sequence(base_name, context.scene)

        component_objs = []
        for i, (base_pos, normal) in enumerate(zip(positions, normals)):
            normal = normal.normalized()
            # 法向抬升：组件半高 + 表面偏移
            position = base_pos + normal * (half_size + settings.surface_offset)

            # 为每个组件单独计算旋转矩阵
            rotation, axis_data = self.calculate_rotation_matrix(normal, settings, forward_ref)

            # 生成唯一名称：基础名称-三位序号
            obj_name = f"{base_name}-{next_sequence + i:03d}"

            component_obj = self.create_component_object(
                context, obj_name, position, rotation, axis_data, settings)
            component_objs.append(component_obj)

        return component_objs

    def calculate_rotation_matrix(self, normal, settings, forward_ref=None):
        """计算旋转矩阵
        X4 坐标系约定：世界 Y+ 为前向，世界 Z+ 为上向。
        - align_axis（法向轴）：指定哪根局部轴对齐面法线（'X'/'Y'/'Z' 正向沿法线外，'-' 反向）
        - forward_axis（朝向轴）：指定哪根局部轴对齐世界前向(Y+)在面平面上的投影；
          选项字母仅决定局部轴，参考方向恒为世界 Y+（forward_ref 可覆盖为自定义方向，如排列方向）
        - up_axis（向上轴）：仅当朝向轴与法向轴同字母时生效，指定哪根局部轴对齐
          世界上向(Z+)在面平面上的投影
        三根局部轴按右手系 (X×Y=Z) 构造，保证 det=+1"""
        normal = normal.normalized()
        align = settings.align_axis
        align_letter = align[0]
        align_sign = -1 if align.endswith('-') else 1
        fwd = settings.forward_axis
        fwd_letter = fwd[0]
        fwd_sign = 1 if fwd.endswith('+') else -1

        # X4 固定参考方向：前向 = 世界 Y+，上向 = 世界 Z+
        FORWARD_WORLD = forward_ref if forward_ref is not None else Vector((0, 1, 0))
        UP_WORLD = Vector((0, 0, 1))

        # 朝向轴与法向轴为同一轴时：向上轴生效，由向上轴定义面内第二根轴；
        # 否则由朝向轴定义面内第二根轴
        if fwd_letter != align_letter:
            axes = {align_letter: align_sign * normal.copy(),
                    fwd_letter: fwd_sign * self.calculate_perpendicular_axis(normal, FORWARD_WORLD)}
        else:
            up_letter = settings.up_axis[0]
            up_sign = 1 if settings.up_axis.endswith('+') else -1
            if up_letter != align_letter:
                axes = {align_letter: align_sign * normal.copy(),
                        up_letter: up_sign * self.calculate_perpendicular_axis(normal, UP_WORLD)}
            else:
                # 向上轴也与法向同轴，回退：朝向轴改用另一根局部轴对齐前向
                fwd_letter = 'X' if align_letter == 'Y' else 'Y'
                axes = {align_letter: align_sign * normal.copy(),
                        fwd_letter: fwd_sign * self.calculate_perpendicular_axis(normal, FORWARD_WORLD)}

        # 补齐第三根轴，保持 X×Y=Z 右手系
        remaining = ({'X', 'Y', 'Z'} - set(axes.keys())).pop()
        if remaining == 'Z':
            axes['Z'] = axes['X'].cross(axes['Y']).normalized()
        elif remaining == 'X':
            axes['X'] = axes['Y'].cross(axes['Z']).normalized()
        else:  # remaining == 'Y'
            axes['Y'] = axes['Z'].cross(axes['X']).normalized()

        # 列向量为局部轴的世界方向（局部X→axes['X']，以此类推）
        rot_matrix = Matrix((axes['X'], axes['Y'], axes['Z'])).transposed()

        return rot_matrix.to_euler(), (axes['X'], axes['Y'], axes['Z'])

    def calculate_perpendicular_axis(self, normal, reference):
        """计算与法线垂直的轴向量"""
        perpendicular = reference - normal * reference.dot(normal)
        if perpendicular.length < 0.001:
            if abs(normal.dot(Vector((0, 1, 0)))) < 0.9:
                perpendicular = Vector((0, 1, 0)) - normal * Vector((0, 1, 0)).dot(normal)
            else:
                perpendicular = Vector((0, 0, 1)) - normal * Vector((0, 0, 1)).dot(normal)

        return perpendicular.normalized()

    def _project_to_plane(self, axis, normal, fallback):
        """将方向向量投影到面平面；若与法线平行则用备用轴"""
        projected = axis - normal * axis.dot(normal)
        if projected.length < 0.001:
            projected = fallback - normal * fallback.dot(normal)
        return projected.normalized()

    def _get_face_span_on_axis(self, obj, axis, bm):
        """计算选中面顶点在指定轴方向上的投影跨度
        bm 由调用方传入（只读，不 free）"""
        matrix_world = obj.matrix_world
        projections = []
        for face in bm.faces:
            if face.select:
                for vert in face.verts:
                    world = matrix_world @ vert.co
                    projections.append(world.dot(axis))
        if not projections:
            return 0.0
        return max(projections) - min(projections)

    def calculate_single_position(self, face_data, settings):
        """计算单个组件位置（含面内偏移）"""
        normal = face_data['normal'].normalized()
        # 构建面的局部 X/Y 方向作为偏移坐标系
        local_x = self.calculate_perpendicular_axis(normal, Vector((1, 0, 0)))
        local_y = normal.cross(local_x).normalized()
        position = (face_data['center']
                    + local_x * settings.face_x_offset
                    + local_y * settings.face_y_offset)
        return position

    def calculate_multiple_positions(self, face_data, settings, obj, bm, bbox_data=None):
        """计算多物体排列位置
        返回 (positions, normals, count, arrange_axis)
        - positions: 每个组件的世界坐标位置
        - normals: 每个组件对应的面法线（用于各自计算旋转与抬升）
        - count: 组件数量
        - arrange_axis: 排列方向（供朝向模式 ARRANGE 使用）"""
        normal = face_data['normal']
        surface_mode = settings.placement_surface_mode

        # 排列轴：平面模式投影到面平面；曲面模式（面中心/曲线路径）直接用世界轴，不投影
        if settings.arrange_direction == 'X':
            world_axis = Vector((1, 0, 0))
            arrange_axis = (self._project_to_plane(world_axis, normal, Vector((0, 1, 0)))
                            if surface_mode == 'PLANE' else world_axis.copy())
        elif settings.arrange_direction == 'Y':
            world_axis = Vector((0, 1, 0))
            arrange_axis = (self._project_to_plane(world_axis, normal, Vector((1, 0, 0)))
                            if surface_mode == 'PLANE' else world_axis.copy())
        else:  # LONG_EDGE
            if bbox_data is not None:
                arrange_axis = bbox_data
            else:
                world_axis = Vector((1, 0, 0))
                arrange_axis = (self._project_to_plane(world_axis, normal, Vector((0, 1, 0)))
                                if surface_mode == 'PLANE' else world_axis.copy())

        arrange_axis.normalize()

        # 计算组件数量
        if settings.auto_count:
            span = self._get_face_span_on_axis(obj, arrange_axis, bm)
            if span > 0:
                count = int(span / max(settings.spacing, 0.5)) + 1
            else:
                count = settings.manual_count
            count = max(2, min(count, 50))
        else:
            count = settings.manual_count

        surface_mode = settings.placement_surface_mode

        if surface_mode == 'PLANE':
            # 平面模式：在平均平面上直线排列，共用平均法线
            total_length = (count - 1) * settings.spacing
            start_pos = face_data['center'] - arrange_axis * (total_length / 2)
            positions = [start_pos + arrange_axis * (i * settings.spacing) for i in range(count)]
            normals = [face_data['normal']] * count
            return positions, normals, count, arrange_axis

        elif surface_mode == 'FACE_CENTER':
            # 面中心模式：沿排列轴方向选 N 个面，每个组件放在面中心，用该面法线
            return self._positions_face_center(face_data, count, arrange_axis)

        else:
            # 曲面路径（接触面积）模式：
            # 位置沿排列轴等距分布不变，垂直方向贴合局部曲面
            return self._positions_surface(face_data, count, arrange_axis, settings, bm)

    def _positions_surface(self, face_data, count, arrange_axis, settings, bm):
        """曲面路径（接触面积）：位置沿排列轴等距分布（排列轴方向坐标不变），
        仅垂直于排列轴的方向贴合局部曲面；法线=合并面面积加权平均法线"""
        faces = face_data['faces']
        if not faces:
            return [], [], 0, arrange_axis

        center = face_data['center']
        spacing = settings.spacing

        # 基础位置：沿排列轴等距直线分布
        total_length = (count - 1) * spacing
        start_pos = center - arrange_axis * (total_length / 2)
        base_positions = [start_pos + arrange_axis * (i * spacing) for i in range(count)]

        positions = []
        normals = []

        for base_pos in base_positions:
            # 收集附近面直到累计面积 >= 组件接触面积，得到合并面平均法线与中心
            merged_normal, merged_center = self._merge_faces_normal(base_pos, faces, settings)
            # 位置：基础位置沿合并法线投影到合并平面，再保持排列轴坐标不变
            offset = (base_pos - merged_center).dot(merged_normal)
            proj_pos = base_pos - merged_normal * offset
            t_base = base_pos.dot(arrange_axis)
            t_proj = proj_pos.dot(arrange_axis)
            conformed_proj = proj_pos + arrange_axis * (t_base - t_proj)
            positions.append(conformed_proj)
            normals.append(merged_normal)

        return positions, normals, count, arrange_axis

    def _merge_faces_normal(self, base_pos, faces, settings):
        """以 base_pos 为中心收集附近面，直到累计面积 >= 组件接触面积
        返回 (平均法线, 面积加权中心)"""
        # 接触面积 = 组件法向轴之外两轴的乘积（近似接触面积）
        size_index = {'X': 0, 'Y': 1, 'Z': 2}[settings.align_axis[0]]
        others = [v for i, v in enumerate(settings.comp_size) if i != size_index]
        contact_area = max(others[0] * others[1], 1.0)

        # 按到 base_pos 的距离排序
        sorted_faces = sorted(faces, key=lambda f: (f['center'] - base_pos).length)

        accumulated = 0.0
        weighted_normal = Vector()
        weighted_center = Vector()
        for f in sorted_faces:
            weighted_normal += f['normal'] * f['area']
            weighted_center += f['center'] * f['area']
            accumulated += f['area']
            if accumulated >= contact_area:
                break

        if accumulated < 1e-8:
            return faces[0]['normal'], faces[0]['center']

        avg_normal = (weighted_normal / accumulated).normalized()
        avg_center = weighted_center / accumulated
        return avg_normal, avg_center

    def _positions_face_center(self, face_data, count, arrange_axis):
        """面中心模式：把选中面按排列轴投影排序，等距选 count 个面"""
        faces = face_data['faces']
        if not faces:
            return [], [], 0, arrange_axis

        # 按面中心在排列轴上的投影排序
        sorted_faces = sorted(faces, key=lambda f: f['center'].dot(arrange_axis))

        n = len(sorted_faces)
        positions = []
        normals = []

        if count >= n:
            # 面数不够，每个面放一个，多余的重复最后一个面
            for f in sorted_faces:
                positions.append(f['center'])
                normals.append(f['normal'])
            # 不足的用最后一个面填充
            last = sorted_faces[-1]
            while len(positions) < count:
                positions.append(last['center'])
                normals.append(last['normal'])
        else:
            # 等距采样：在 [0, n-1] 区间取 count 个点
            for i in range(count):
                idx = int(round(i * (n - 1) / max(count - 1, 1)))
                f = sorted_faces[idx]
                positions.append(f['center'])
                normals.append(f['normal'])

        return positions, normals, count, arrange_axis

    def create_component_object(self, context, name, position, rotation, axis_data, settings):
        """创建组件对象"""
        empty_obj = bpy.data.objects.new(name, None)
        empty_obj.empty_display_type = settings.empty_display_mode  # 面板可选，默认箭头
        # 显示尺寸 = 法向轴之外另两轴的最长轴值 ÷ 2（empty_display_size 是半径语义）
        _others = [v for letter, v in zip('XYZ', settings.comp_size) if letter != settings.align_axis[0]]
        empty_obj.empty_display_size = max(_others) / 2

        # 自定义属性：记录轴向设置与预设尺寸（镜像继承、显示与导出核对用）
        # v9.13.5：法向轴记录统一带符号（'Z'→'Z+'）；读取端规则"带-为负、否则为正"，兼容旧记录 'Z'
        _align = settings.align_axis
        empty_obj["x4_axis_normal"] = _align if _align.endswith('-') else _align + '+'
        empty_obj["x4_axis_forward"] = settings.forward_axis
        # 向上轴仅在"法向轴与朝向轴同轴"时生效，禁用时记录空值避免残留（法向轴六向后按轴字母比较，v9.13.2）
        empty_obj["x4_axis_up"] = settings.up_axis if settings.forward_axis[0] == settings.align_axis[0] else ""
        empty_obj["x4_comp_size"] = list(settings.comp_size)
        empty_obj["x4_comp_name"] = settings.name_base  # 组件名称段（如 turret_l，不拼接组名/key，v9.14.1）
        # 轴常显标记：由 GPU 覆盖层按 empty_display_size 画三色局部轴（任意模式可见，无需选中）
        empty_obj["x4_axis_show"] = True

        empty_obj.location = position
        empty_obj.rotation_euler = rotation

        # 若已执行过"整理场景"，自动归入对应 Connections 集合（按类型+组名）；否则放场景根集合
        target_col_name = _resolve_organize_collection(settings.name_base, settings.name_group)
        if target_col_name and target_col_name in bpy.data.collections:
            bpy.data.collections[target_col_name].objects.link(empty_obj)
        else:
            context.scene.collection.objects.link(empty_obj)

        # 存储局部轴的世界方向信息用于平移（与旋转矩阵列向量一致）
        empty_obj["face_normal"] = tuple(axis_data[2])
        empty_obj["x_axis"] = tuple(axis_data[0])
        empty_obj["y_axis"] = tuple(axis_data[1])
        empty_obj["base_position"] = tuple(position)

        return empty_obj

class COMPONENT_OT_move_on_surface(bpy.types.Operator):
    """表面平移操作器"""
    bl_idname = "component_fixed.move_on_surface"
    bl_label = t("MOVE_OPERATOR")
    bl_options = {'REGISTER', 'UNDO'}

    move_x: bpy.props.FloatProperty(default=DEFAULT_VALUES["MOVE_X"])
    move_y: bpy.props.FloatProperty(default=DEFAULT_VALUES["MOVE_Y"])
    move_z: bpy.props.FloatProperty(default=DEFAULT_VALUES["MOVE_Z"])

    def execute(self, context):
        selected_objects = context.selected_objects

        if not selected_objects:
            self.report({'ERROR'}, t("ERROR_SELECT_COMPONENTS"))
            return {'CANCELLED'}

        moved_count = 0
        for obj in selected_objects:
            if "face_normal" in obj and "x_axis" in obj and "y_axis" in obj:
                face_normal = Vector(obj["face_normal"])
                x_axis = Vector(obj["x_axis"])
                y_axis = Vector(obj["y_axis"])

                displacement = (x_axis * self.move_x +
                               y_axis * self.move_y +
                               face_normal * self.move_z)

                obj.location += displacement
                moved_count += 1

        if moved_count == 0:
            self.report({'ERROR'}, t("ERROR_NO_NORMAL_INFO"))
            return {'CANCELLED'}

        self.report({'INFO'}, t("INFO_TRANSLATED_OBJECTS", moved_count))
        return {'FINISHED'}

class COMPONENT_OT_quick_move(bpy.types.Operator):
    """快速平移操作器"""
    bl_idname = "component_fixed.quick_move"
    bl_label = t("QUICK_MOVE_OPERATOR")
    bl_options = {'REGISTER', 'UNDO'}

    direction: bpy.props.EnumProperty(
        name=t("MOVE_DIRECTION"),
        items=_DIRECTION_ITEMS
    )
    step: bpy.props.FloatProperty(
        name=t("QUICK_STEP"),
        default=0.5,
        min=0.05,
        description=t("QUICK_STEP_DESC")
    )

    def execute(self, context):
        # 若未显式传入 step，则使用场景设置里的快捷步长
        translation_settings = getattr(context.scene, "component_translation_fixed", None)
        default_step = translation_settings.quick_step_fixed if translation_settings else 0.5
        step = self.step if self.step > 0 else default_step

        if self.direction == 'LEFT':
            move_x, move_y, move_z = -step, 0.0, 0.0
        elif self.direction == 'RIGHT':
            move_x, move_y, move_z = step, 0.0, 0.0
        elif self.direction == 'FORWARD':
            move_x, move_y, move_z = 0.0, step, 0.0
        elif self.direction == 'BACKWARD':
            move_x, move_y, move_z = 0.0, -step, 0.0
        elif self.direction == 'UP':
            move_x, move_y, move_z = 0.0, 0.0, step
        else:
            move_x, move_y, move_z = 0.0, 0.0, -step

        result = bpy.ops.component_fixed.move_on_surface(move_x=move_x, move_y=move_y, move_z=move_z)
        # 透传内部操作器的执行结果
        return {'FINISHED'} if 'FINISHED' in result else {'CANCELLED'}

class COMPONENT_OT_batch_move(bpy.types.Operator):
    """批量平移：名称包含筛选文本的空物体按指定方向平移。
    六向 = 各自局部轴（与快捷平移一致）；贴近/远离面 = 按每个硬点记录的
    x4_axis_normal（放置时的法向轴，含负号）自适应判断法线方向（v9.13.3）"""
    bl_idname = "component_fixed.batch_move"
    bl_label = t("BATCH_MOVE")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        ts = context.scene.component_translation_fixed
        filter_text = ts.batch_move_filter.strip()
        if not filter_text:
            self.report({'WARNING'}, t("ERROR_BATCH_FILTER_EMPTY"))
            return {'CANCELLED'}

        direction = ts.batch_move_direction
        step = ts.batch_move_step  # 批量平移独立步长（不与快捷平移共用）

        if direction in ('SURFACE_OUT', 'SURFACE_IN'):
            # 自适应法向：局部法向轴正方向 × 记录符号 = 该硬点的"远离面"方向；贴近面取反。
            # 读物体实时旋转矩阵列，镜像/旋转/膨胀后的当前朝向自动正确（记录的是局部轴，非世界向量）
            surface_sign = 1.0 if direction == 'SURFACE_OUT' else -1.0
            axis_unit = {'X': Vector((1, 0, 0)), 'Y': Vector((0, 1, 0)), 'Z': Vector((0, 0, 1))}
            moved_count = 0
            fallback_count = 0
            for obj in context.scene.objects:
                if obj.type != 'EMPTY' or filter_text not in obj.name:
                    continue
                recorded = obj.get("x4_axis_normal", "")
                if recorded:
                    letter = recorded[0]
                    sign = -1.0 if recorded.endswith('-') else 1.0
                else:
                    letter, sign = 'Z', 1.0  # 无记录的老硬点按默认 Z+ 处理
                    fallback_count += 1
                local_vec = axis_unit[letter] * (sign * surface_sign * step)
                obj.location += obj.matrix_world.to_3x3() @ local_vec
                moved_count += 1

            if moved_count == 0:
                self.report({'WARNING'}, t("ERROR_BATCH_NO_MATCH"))
                return {'CANCELLED'}
            if fallback_count:
                self.report({'INFO'}, t("INFO_BATCH_MOVED_FALLBACK", moved_count, fallback_count))
            else:
                self.report({'INFO'}, t("INFO_BATCH_MOVED", moved_count))
            return {'FINISHED'}

        # 与快捷平移一致的方向语义：左右=局部X、前后=局部Y、上下=局部Z(法线)
        if direction == 'LEFT':
            mx, my, mz = -step, 0.0, 0.0
        elif direction == 'RIGHT':
            mx, my, mz = step, 0.0, 0.0
        elif direction == 'FORWARD':
            mx, my, mz = 0.0, step, 0.0
        elif direction == 'BACKWARD':
            mx, my, mz = 0.0, -step, 0.0
        elif direction == 'UP':
            mx, my, mz = 0.0, 0.0, step
        else:  # DOWN
            mx, my, mz = 0.0, 0.0, -step

        moved_count = 0
        for obj in context.scene.objects:
            if obj.type != 'EMPTY' or filter_text not in obj.name:
                continue
            # 局部轴取物体当前旋转矩阵列向量（与存储的 x_axis/y_axis/face_normal 一致）
            matrix = obj.matrix_world.to_3x3()
            obj.location += matrix.col[0] * mx + matrix.col[1] * my + matrix.col[2] * mz
            moved_count += 1

        if moved_count == 0:
            self.report({'WARNING'}, t("ERROR_BATCH_NO_MATCH"))
            return {'CANCELLED'}

        self.report({'INFO'}, t("INFO_BATCH_MOVED", moved_count))
        return {'FINISHED'}

class COMPONENT_OT_replace_names(bpy.types.Operator):
    """名称替换操作器：把场景中所有物体名称的 A 部分替换为 B 部分"""
    bl_idname = "component_fixed.replace_names"
    bl_label = t("REPLACE_TITLE")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        translation_settings = context.scene.component_translation_fixed
        find_text = translation_settings.replace_find.strip()
        replace_text = translation_settings.replace_with

        if not find_text:
            self.report({'WARNING'}, t("ERROR_FIND_EMPTY"))
            return {'CANCELLED'}

        replaced_count = 0
        for obj in context.scene.objects:
            if find_text in obj.name:
                obj.name = obj.name.replace(find_text, replace_text)
                replaced_count += 1

        self.report({'INFO'}, t("INFO_REPLACED", replaced_count))
        return {'FINISHED'}


class COMPONENT_OT_mirror_x(bpy.types.Operator):
    """X=0 平面镜像选中的硬点组件（X4 官方标准镜像）"""
    bl_idname = "component_fixed.mirror_x"
    bl_label = t("MIRROR_TITLE")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from mathutils import Quaternion

        scene = context.scene
        settings = scene.component_settings_fixed
        translation_settings = scene.component_translation_fixed
        find_text = translation_settings.replace_find.strip()
        replace_text = translation_settings.replace_with

        valid_components = [obj for obj in context.selected_objects
                            if "face_normal" in obj and "x_axis" in obj and "y_axis" in obj]

        if not valid_components:
            self.report({'WARNING'}, t("ERROR_SELECT_COMPONENTS"))
            return {'CANCELLED'}

        mirror_count = 0
        for obj in valid_components:
            # 位置镜像：x -> -x
            loc = obj.location.copy()
            loc.x = -loc.x

            # 旋转镜像（X4 官方标准）：四元数 (qx, qy, qz, qw) -> (qx, -qy, -qz, qw)
            # 注意 mathutils 的 Quaternion 构造顺序为 (w, x, y, z)
            # v9.8.1 修复：放置写的是欧拉角（rotation_mode='XYZ'），直接读
            # rotation_quaternion 会拿到陈旧的单位四元数导致镜像旋转丢失——
            # 改从世界矩阵提取实际旋转，与 rotation_mode 无关
            q = obj.matrix_world.to_3x3().to_quaternion()
            mirrored_q = Quaternion((q.w, q.x, -q.y, -q.z))

            # 从镜像后的旋转矩阵重算三根轴的世界方向（与旋转工具的维护方式一致）
            rot_matrix = mirrored_q.to_matrix()

            # 命名：与放置规则一致（剥离序号取基础名，分配下一个可用序号）
            base_name = strip_sequence_suffix(obj.name)
            if find_text and find_text in base_name:
                base_name = base_name.replace(find_text, replace_text)
            seq = get_next_available_sequence(base_name, scene)
            mirror_name = f"{base_name}-{seq:03d}"

            # 创建镜像副本（不选中，保持当前选择便于连续镜像）
            new_obj = bpy.data.objects.new(mirror_name, None)
            new_obj.empty_display_type = obj.empty_display_type
            new_obj.empty_display_size = obj.empty_display_size
            # 继承轴常显标记（旧版硬点无此标记时默认开启）
            new_obj["x4_axis_show"] = obj.get("x4_axis_show", True)
            # 轴向与尺寸自定义属性随镜像继承
            for _key in ("x4_axis_normal", "x4_axis_forward", "x4_axis_up", "x4_comp_size", "x4_comp_name"):
                if _key in obj:
                    new_obj[_key] = obj[_key]
            new_obj.location = loc
            new_obj.rotation_mode = 'QUATERNION'  # 四元数镜像结果直接写入，避免欧拉转换误差
            new_obj.rotation_quaternion = mirrored_q

            # 若已执行过"整理场景"，镜像副本自动归入对应 Connections 集合
            mirror_comp_type = str(new_obj.get("x4_comp_name", ""))
            mirror_group = _org_extract_group(mirror_name, settings.name_separator, settings.name_key)
            target_col_name = _resolve_organize_collection(mirror_comp_type, mirror_group)
            if target_col_name and target_col_name in bpy.data.collections:
                bpy.data.collections[target_col_name].objects.link(new_obj)
            else:
                scene.collection.objects.link(new_obj)

            # 存储轴向数据（镜像后重新计算的世界方向）
            new_obj["face_normal"] = tuple(rot_matrix.col[2])
            new_obj["x_axis"] = tuple(rot_matrix.col[0])
            new_obj["y_axis"] = tuple(rot_matrix.col[1])
            new_obj["base_position"] = tuple(loc)

            mirror_count += 1

        self.report({'INFO'}, t("INFO_MIRRORED", mirror_count))
        return {'FINISHED'}


class COMPONENT_OT_rotate_local(bpy.types.Operator):
    """绕组件自身局部轴精确旋转"""
    bl_idname = "component_fixed.rotate_local"
    bl_label = t("ROTATE_TITLE")
    bl_options = {'REGISTER', 'UNDO'}

    rot_x: bpy.props.FloatProperty(default=0.0)
    rot_y: bpy.props.FloatProperty(default=0.0)
    rot_z: bpy.props.FloatProperty(default=0.0)

    def execute(self, context):
        import math
        from mathutils import Matrix

        valid_components = [obj for obj in context.selected_objects
                            if "face_normal" in obj and "x_axis" in obj and "y_axis" in obj]

        if not valid_components:
            self.report({'WARNING'}, t("ERROR_SELECT_COMPONENTS"))
            return {'CANCELLED'}

        # 构造绕局部轴的旋转矩阵（度 -> 弧度）
        local_rot = (Matrix.Rotation(math.radians(self.rot_x), 3, 'X')
                     @ Matrix.Rotation(math.radians(self.rot_y), 3, 'Y')
                     @ Matrix.Rotation(math.radians(self.rot_z), 3, 'Z'))

        rotated_count = 0
        for obj in valid_components:
            # 右乘 = 绕物体自身局部轴旋转
            new_matrix = obj.matrix_world.to_3x3() @ local_rot
            obj.rotation_euler = new_matrix.to_quaternion().to_euler()

            # 同步更新存储的轴向数据（保证平移工具轴向一致）
            obj["x_axis"] = tuple(new_matrix.col[0])
            obj["y_axis"] = tuple(new_matrix.col[1])
            obj["face_normal"] = tuple(new_matrix.col[2])

            rotated_count += 1

        self.report({'INFO'}, t("INFO_ROTATED", rotated_count))
        return {'FINISHED'}


class COMPONENT_OT_quick_rotate(bpy.types.Operator):
    """按步长绕组件自身局部轴快捷旋转"""
    bl_idname = "component_fixed.quick_rotate"
    bl_label = t("ROTATE_TITLE")
    bl_options = {'REGISTER', 'UNDO'}

    axis: bpy.props.EnumProperty(items=[('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")])
    direction: bpy.props.EnumProperty(items=[('POS', "+", ""), ('NEG', "-", "")])
    step: bpy.props.FloatProperty(default=15.0, min=0.1)

    def execute(self, context):
        import math
        from mathutils import Matrix

        valid_components = [obj for obj in context.selected_objects
                            if "face_normal" in obj and "x_axis" in obj and "y_axis" in obj]

        if not valid_components:
            self.report({'WARNING'}, t("ERROR_SELECT_COMPONENTS"))
            return {'CANCELLED'}

        angle = self.step if self.direction == 'POS' else -self.step
        local_rot = Matrix.Rotation(math.radians(angle), 3, self.axis)

        rotated_count = 0
        for obj in valid_components:
            new_matrix = obj.matrix_world.to_3x3() @ local_rot
            obj.rotation_euler = new_matrix.to_quaternion().to_euler()

            obj["x_axis"] = tuple(new_matrix.col[0])
            obj["y_axis"] = tuple(new_matrix.col[1])
            obj["face_normal"] = tuple(new_matrix.col[2])

            rotated_count += 1

        self.report({'INFO'}, t("INFO_ROTATED", rotated_count))
        return {'FINISHED'}


class COMPONENT_OT_toggle_axis(bpy.types.Operator):
    """批量切换场景内全部空物体硬点的三色轴常显（GPU 覆盖层）"""
    bl_idname = "component_fixed.toggle_axis"
    bl_label = t("TOGGLE_AXIS")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        targets = [obj for obj in context.scene.objects if obj.type == 'EMPTY']
        if not targets:
            self.report({'WARNING'}, t("ERROR_SELECT_EMPTY"))
            return {'CANCELLED'}

        # 任一未开启则全部开启，否则全部关闭
        enable = any(not obj.get("x4_axis_show", False) for obj in targets)
        for obj in targets:
            obj["x4_axis_show"] = enable

        if enable:
            self.report({'INFO'}, t("INFO_AXIS_ENABLED", len(targets)))
        else:
            self.report({'INFO'}, t("INFO_AXIS_DISABLED", len(targets)))
        return {'FINISHED'}


class COMPONENT_OT_apply_display_mode(bpy.types.Operator):
    """把场景内全部空物体硬点的显示模式改为面板当前选定"""
    bl_idname = "component_fixed.apply_display_mode"
    bl_label = t("APPLY_DISPLAY_MODE")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.component_settings_fixed
        targets = [obj for obj in context.scene.objects if obj.type == 'EMPTY']
        if not targets:
            self.report({'WARNING'}, t("ERROR_SELECT_EMPTY"))
            return {'CANCELLED'}

        for obj in targets:
            obj.empty_display_type = settings.empty_display_mode

        self.report({'INFO'}, t("INFO_DISPLAY_APPLIED", len(targets)))
        return {'FINISHED'}


# ============================ 膨胀/收缩（舰船整体缩放时硬点跟随） ============================

def _get_comp_key(context):
    """读取当前组件key（膨胀/收缩等按名称识别硬点的功能使用）；空值返回 None 并由调用方提示"""
    settings = getattr(context.scene, "component_settings_fixed", None)
    if settings is None:
        return None
    return settings.name_key


class COMPONENT_OT_inflate_parent(bpy.types.Operator):
    """将名称含组件key的硬点父子绑定到选中的舰船（保持世界位置不变），随后缩放舰船即可实时预览硬点跟随"""
    bl_idname = "component_fixed.inflate_parent"
    bl_label = t("INFLATE_PARENT")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        ship = context.active_object
        if ship is None or ship.type == 'EMPTY' or not ship.select_get():
            self.report({'WARNING'}, t("ERROR_INFLATE_NO_SHIP"))
            return {'CANCELLED'}

        comp_key = _get_comp_key(context)
        if not comp_key or not comp_key.strip():
            self.report({'WARNING'}, t("ERROR_COMP_KEY_EMPTY"))
            return {'CANCELLED'}

        parented = 0
        parent_inv = ship.matrix_world.inverted()
        scope = getattr(context.scene.component_settings_fixed, "inflate_scope", 'ALL')
        if scope == 'SELECTED':
            # 仅处理选中的硬点（排除舰船本身）
            candidates = [o for o in context.selected_objects
                          if o != ship and o.type == 'EMPTY' and comp_key in o.name]
        else:
            # 关联全部：场景中所有名称含组件key的空物体硬点
            candidates = [o for o in context.scene.objects
                          if o.type == 'EMPTY' and comp_key in o.name]
        for obj in candidates:
            if obj.parent is ship:
                continue  # 已绑定，防重复
            # 世界 = parent @ parent_inverse @ basis；parent_inverse = ship⁻¹
            # ⇒ 绑定瞬间世界变换不变，此后自动跟随舰船
            obj.parent = ship
            obj.matrix_parent_inverse = parent_inv
            parented += 1

        if parented == 0:
            self.report({'WARNING'}, t("ERROR_NO_HARDPOINTS"))
            return {'CANCELLED'}
        self.report({'INFO'}, t("INFO_PARENTED", parented))
        return {'FINISHED'}


class COMPONENT_OT_inflate_apply(bpy.types.Operator):
    """解除硬点父子并固化跟随结果：仅位移=恢复缩放1（默认推荐）；位移+缩放=保留缩放"""
    bl_idname = "component_fixed.inflate_apply"
    bl_label = t("INFLATE_TITLE")
    bl_options = {'REGISTER', 'UNDO'}

    keep_scale: bpy.props.BoolProperty(
        name="Keep Scale",
        description=t("INFLATE_APPLY_FULL_DESC"),
        default=False,  # 默认仅位移
    )

    def execute(self, context):
        comp_key = _get_comp_key(context)
        if not comp_key or not comp_key.strip():
            self.report({'WARNING'}, t("ERROR_COMP_KEY_EMPTY"))
            return {'CANCELLED'}

        targets = [obj for obj in context.scene.objects
                   if obj.type == 'EMPTY' and comp_key in obj.name
                   and obj.parent is not None]
        if not targets:
            self.report({'WARNING'}, t("ERROR_NOT_PARENTED"))
            return {'CANCELLED'}

        for obj in targets:
            world = obj.matrix_world.copy()
            obj.parent = None  # Python 解父会保留本地 basis（会跳变），必须随即回写世界矩阵
            if self.keep_scale:
                obj.matrix_world = world  # 本地=世界，位置/旋转/缩放完整保留
            else:
                # 仅位移：位置与旋转保留，缩放恢复 1（等比舰船缩放下两者结果位置一致）
                obj.matrix_world = Matrix.LocRotScale(
                    world.translation, world.to_quaternion(), (1.0, 1.0, 1.0)
                )

        mode_text = t("INFLATE_APPLY_FULL") if self.keep_scale else t("INFLATE_APPLY_MOVE")
        self.report({'INFO'}, t("INFO_INFLATE_APPLIED", len(targets), mode_text))
        return {'FINISHED'}


# ============================ 预览实体化（同位长方体预览） ============================

_PREVIEW_OF_PROP = "x4_preview_of"  # 预览体上记录所属空物体名称的自定义属性

_preview_cube_mesh = None  # 共享 2×2×2 立方体网格缓存（所有预览体缩放引用同一份数据）


def _get_preview_cube_mesh():
    """获取（或创建）共享的 2×2×2 立方体网格，预览体通过缩放得到目标尺寸"""
    global _preview_cube_mesh
    _need_rebuild = False
    if _preview_cube_mesh is None:
        _need_rebuild = True
    else:
        try:
            # 底层 Mesh 可能已被 Blender 销毁（文件重载/删除数据块/清理孤儿），
            # 此时 Python 包装器仍指向死亡的 StructRNA，访问 .users 会抛 ReferenceError
            if _preview_cube_mesh.users == 0:
                _need_rebuild = True
        except ReferenceError:
            _preview_cube_mesh = None
            _need_rebuild = True
    if _need_rebuild:
        import bmesh
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=2.0)
        _preview_cube_mesh = bpy.data.meshes.new("X4_HardpointPreviewCube")
        bm.to_mesh(_preview_cube_mesh)
        bm.free()
    return _preview_cube_mesh


class COMPONENT_OT_preview_show(bpy.types.Operator):
    """按自定义属性记录的组件 xyz 尺寸，为选中的空物体创建同位线框长方体预览（不修改空物体本身）"""
    bl_idname = "component_fixed.preview_show"
    bl_label = t("PREVIEW_SHOW")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not context.selected_objects:
            self.report({'WARNING'}, t("ERROR_NO_SELECTION"))
            return {'CANCELLED'}

        created = 0
        refreshed = 0
        no_size = 0
        mesh = _get_preview_cube_mesh()
        for obj in context.selected_objects:
            if obj.type != 'EMPTY':
                continue
            size = obj.get("x4_comp_size")
            if not size:
                no_size += 1  # 老硬点先用"维护硬点属性"补齐尺寸记录
                continue
            # 已有预览则复用并刷新尺寸/位置；没有则新建——避免"改了尺寸还得先去除再预览"的重复操作
            preview = next((o for o in context.scene.objects if o.get(_PREVIEW_OF_PROP) == obj.name), None)
            if preview is not None:
                # 防止父级被手动改过或重名导致归属错位，重新绑定父级
                if preview.parent != obj:
                    preview.parent = obj
                    preview.matrix_parent_inverse = Matrix.Identity(4)
                # 预览体引用的 mesh 可能已被 Blender 销毁（文件重载/清理孤儿），重新绑定有效 mesh
                _data_ok = False
                try:
                    if preview.data is not None and preview.data.users > 0:
                        _data_ok = True
                except ReferenceError:
                    pass
                if not _data_ok:
                    preview.data = mesh
                refreshed += 1
            else:
                preview = bpy.data.objects.new(obj.name + "_预览", mesh)
                preview.parent = obj  # 父级跟随：空物体平移/旋转时预览同步
                preview.matrix_parent_inverse = Matrix.Identity(4)
                preview[_PREVIEW_OF_PROP] = obj.name  # 反查归属，供去除实体化定位
                context.collection.objects.link(preview)
                created += 1
            _comp_name = str(obj.get("x4_comp_name", "")).lower()
            _size = list(size)
            # 护盾（shield）特例：预设尺寸按 (长, 厚, 高) 记在 xyz，实体长轴应沿局部 Y（+Y 同向），
            # 预览时交换 X/Y 两轴长度
            if "shield" in _comp_name or "shield" in obj.name.lower():
                _size[0], _size[1] = _size[1], _size[0]
            preview.scale = (_size[0] / 2.0, _size[1] / 2.0, _size[2] / 2.0)  # 2m 立方体 → 目标 xyz 尺寸
            # 原点贴接触面（v9.14.3）：X4 语义下组件原点在连接面、组件沿法线方向生长，
            # 故预览盒沿"记录的法向轴×符号"偏移半个法向尺寸，使底面贴住硬点原点而非居中
            recorded = obj.get("x4_axis_normal", "")
            _letter = recorded[0] if recorded else 'Z'  # 无记录回退 Z+
            _sign = -1.0 if recorded.endswith('-') else 1.0
            # 机坪（dockarea）特例：X4 机坪主体沉在连接面内侧（负法线方向），
            # 与普通组件"沿法线生长"相反——预览盒反向偏移，画在面下方
            if "dockarea" in _comp_name or "dockarea" in obj.name.lower():
                _sign = -_sign
            _idx = {'X': 0, 'Y': 1, 'Z': 2}[_letter]
            _loc = [0.0, 0.0, 0.0]
            _loc[_idx] = _sign * _size[_idx] / 2.0
            preview.location = _loc
            preview.display_type = 'BOUNDS'  # 线框包围盒，不遮挡模型
            preview.hide_render = True  # 预览体不参与渲染/导出

        self.report({'INFO'}, t("INFO_PREVIEW_CREATED", created, refreshed, no_size))
        return {'FINISHED'}


class COMPONENT_OT_preview_hide(bpy.types.Operator):
    """移除选中空物体的预览体（直接选中预览体亦可）；不选任何物体时清理场景中全部预览体"""
    bl_idname = "component_fixed.preview_hide"
    bl_label = t("PREVIEW_HIDE")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if context.selected_objects:
            # 选空物体→按归属名定位；直接选预览体→属性里就是归属名，两种选法通吃
            names = {o.get(_PREVIEW_OF_PROP) or o.name for o in context.selected_objects}
        else:
            names = None  # 全场景清理

        removed = 0
        for preview in list(context.scene.objects):
            owner = preview.get(_PREVIEW_OF_PROP)
            if owner is None:
                continue
            if names is not None and owner not in names:
                continue
            bpy.data.objects.remove(preview)
            removed += 1

        if removed == 0:
            self.report({'WARNING'}, t("ERROR_NO_PREVIEW"))
            return {'CANCELLED'}
        self.report({'INFO'}, t("INFO_PREVIEW_REMOVED", removed))
        return {'FINISHED'}


def _maintain_one_hardpoint(obj, settings):
    """对单个物体执行属性维护（v9.15.0 抽取，选中模式与筛选模式共用）。
    返回 'added'（无属性补齐）/ 'updated'（已有更新）/ 'skipped'（非空物体）"""
    if obj.type != 'EMPTY':
        return 'skipped'
    had_size = "x4_comp_size" in obj
    obj["x4_comp_size"] = list(settings.comp_size)
    obj["x4_comp_name"] = settings.name_base  # 组件名称段（如 turret_l，不拼接组名/key，v9.14.1）
    # 法向轴记录统一带符号（'Z'→'Z+'），读取端兼容旧记录 'Z'（v9.13.5）
    _align = settings.align_axis
    obj["x4_axis_normal"] = _align if _align.endswith('-') else _align + '+'
    obj["x4_axis_forward"] = settings.forward_axis
    # 向上轴仅在"法向轴与朝向轴同轴"时生效（与放置逻辑一致，禁用不残留；按轴字母比较，v9.13.2）
    obj["x4_axis_up"] = settings.up_axis if settings.forward_axis[0] == settings.align_axis[0] else ""
    if "x4_axis_show" not in obj:
        obj["x4_axis_show"] = True  # 缺失时补上，三色轴常显立即可用
    # 显示尺寸同步重算：法向轴之外另两轴的最长轴值 ÷ 2（同放置，半径语义，v9.14.0）
    _others = [v for letter, v in zip('XYZ', settings.comp_size) if letter != settings.align_axis[0]]
    obj.empty_display_size = max(_others) / 2
    return 'updated' if had_size else 'added'


class COMPONENT_OT_maintain_props(bpy.types.Operator):
    """按当前 组件预设/组件尺寸/朝向 设置，为选中的空物体添加（缺失）或更新（已有）
    尺寸、组件名称、朝向自定义属性，并按放置规则重算显示尺寸（支持多选）"""
    bl_idname = "component_fixed.maintain_props"
    bl_label = t("MAINTAIN_PROPS")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.component_settings_fixed

        if not context.selected_objects:
            self.report({'WARNING'}, t("ERROR_NO_SELECTION"))
            return {'CANCELLED'}

        added = 0
        updated = 0
        skipped = 0
        for obj in context.selected_objects:
            r = _maintain_one_hardpoint(obj, settings)
            if r == 'added':
                added += 1
            elif r == 'updated':
                updated += 1
            else:
                skipped += 1

        self.report({'INFO'}, t("INFO_MAINTAINED", added, updated, skipped))
        return {'FINISHED'}


class COMPONENT_OT_maintain_props_filtered(bpy.types.Operator):
    """按筛选文本批量维护：名称包含文本的空物体统一按当前面板设置维护属性
    （场景级、不依赖选中，交互同批量平移，v9.15.0）"""
    bl_idname = "component_fixed.maintain_props_filtered"
    bl_label = t("MAINTAIN_FILTERED")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.component_settings_fixed
        filter_text = settings.maintain_filter.strip()
        if not filter_text:
            self.report({'WARNING'}, t("ERROR_BATCH_FILTER_EMPTY"))
            return {'CANCELLED'}

        added = 0
        updated = 0
        skipped = 0
        for obj in context.scene.objects:
            if filter_text not in obj.name:
                continue
            r = _maintain_one_hardpoint(obj, settings)
            if r == 'added':
                added += 1
            elif r == 'updated':
                updated += 1
            else:
                skipped += 1

        if added + updated + skipped == 0:
            self.report({'WARNING'}, t("ERROR_BATCH_NO_MATCH"))
            return {'CANCELLED'}

        self.report({'INFO'}, t("INFO_MAINTAINED", added, updated, skipped))
        return {'FINISHED'}


# ============================ 场景整理（Connections 集合层级） ============================

# x4_comp_name 前缀 → 目标集合名（顺序即 Connections 下的排列顺序）
_ORGANIZE_TYPE_MAP = [
    ("engine",          "Engines"),
    ("thruster",        "Thrusters"),
    ("shield",          "Shields"),
    ("weapon",          "Weapons"),
    ("turret",          "Turrets"),
    ("dockarea",        "Dockareas"),
    ("launchtube",      "Launchtubes"),
    ("room",            "Rooms"),
    ("countermeasures", "Countermeasures"),
    ("trailemitter",    "Trailemitters"),
    # 以下归入 Empties 下的类型子集合（元组形式：(父集合, 子集合)）
    ("bridge",          ("Empties", "cockpit")),
    ("cockpit",         ("Empties", "cockpit")),
    ("playercontrol",   ("Empties", "playercontrol")),
    ("storage",         ("Empties", "storage")),
    ("dock_",           ("Empties", "docks")),
    ("shipstorage",     ("Empties", "shipstorage")),
    ("container",       ("Empties", "container")),
    ("space",           ("Empties", "space")),
    ("buildmodule",     ("Empties", "buildmodule")),
]
# 场景根集合（Scene Collection）下的集合排列顺序
_ORGANIZE_ROOT_ORDER = [
    "Mesh", "Collisions", "Hulls", "Waypoints", "Lights", "Connections",
]
# Connections 下的集合排列顺序
_ORGANIZE_COL_ORDER = [
    "Groups", "Engines", "Thrusters", "Shields", "Weapons", "Turrets",
    "Dockareas", "Launchtubes", "Rooms", "Countermeasures",
    "Trailemitters", "Empties",
]
# Empties 下预创建的类型子集合（按约定顺序排列，不按字母序）
_ORGANIZE_EMPTIES_SUBS = [
    "cockpit", "playercontrol", "storage", "docks",
    "shipstorage", "buildmodule", "container", "space",
]


def _org_get_or_create_collection(name, parent=None):
    """获取或新建集合并挂到 parent（默认场景根集合）下。"""
    if name in bpy.data.collections:
        col = bpy.data.collections[name]
    else:
        col = bpy.data.collections.new(name)
    root = parent or bpy.context.scene.collection
    if col.name not in root.children.keys():
        root.children.link(col)
    return col


def _org_sort_children_alphabetically(parent_col):
    """把 parent_col 下的所有子集合按名称升序重排。"""
    children = sorted(parent_col.children.keys(), key=lambda n: n.lower())
    for child_name in children:
        if child_name in parent_col.children.keys():
            col = parent_col.children[child_name]
            parent_col.children.unlink(col)
            parent_col.children.link(col)


def _org_move_to_collection(obj, col_name):
    """把 obj 从当前所有集合移出，放进 col_name。"""
    target = bpy.data.collections.get(col_name)
    if target is None:
        return
    if obj.name in target.objects:
        return
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
    target.objects.link(obj)


def _org_infer_comp_type(obj, settings):
    """推断硬点的组件类型字符串（小写），优先 x4_comp_name，回退从名称解析。"""
    comp_name = obj.get("x4_comp_name")
    if comp_name:
        return str(comp_name).lower()
    # 回退：从物体名解析（组-链接符-key-名称-NNN），去掉 key 与尾部序号
    name = obj.name
    key = settings.name_key or ""
    if key and key in name:
        import re
        after = name.split(key, 1)[1]
        after = re.sub(r'-\d+$', '', after)
        return after.lower()
    return name.lower()


def _org_type_to_collection(comp_type):
    """按组件类型返回 (父集合名, 子集合名或None)。
    顶层类型（如 Engines）返回 ("Engines", None)；
    Empties 子类型（如 bridge）返回 ("Empties", "cockpit")；
    未命中返回 None。"""
    ct = comp_type.lower() if isinstance(comp_type, str) else ""
    for prefix, target in _ORGANIZE_TYPE_MAP:
        if ct.startswith(prefix):
            if isinstance(target, tuple):
                return target
            return (target, None)
    return None


def _org_extract_group(name, separator, key):
    """从物体名提取组名。命名约定：[group][sep][key][base]-[NNN]
    找到 [sep][key] 标记则取其前的部分为组名；找不到（名称以 key 开头）则无组名。"""
    import re
    base = re.sub(r'-\d+$', '', name)
    marker = (separator or "-") + (key or "")
    if marker and marker in base:
        idx = base.find(marker)
        return base[:idx] if idx > 0 else ""
    return ""


def _resolve_organize_collection(comp_type, group_name=""):
    """若 Connections 下已存在对应集合，返回该集合名用于放置时自动归入；否则返回 None。

    新分组规则：
    - 有组名 → Connections/Groups/{组名}（不分组件种类）
    - 无组名 → Connections/{type}/{sub} 或 Connections/Empties/{sub}
    """
    connections = bpy.data.collections.get("Connections")
    if connections is None:
        return None
    # 有组名优先找 Groups/{组名}
    if group_name:
        groups_col = connections.children.get("Groups")
        if groups_col and group_name in groups_col.children:
            return groups_col.children[group_name].name
        return None
    # 无组名 → 按类型查找
    target = _org_type_to_collection(comp_type)
    if target is None:
        empties_col = connections.children.get("Empties")
        if empties_col and comp_type and comp_type in empties_col.children:
            return empties_col.children[comp_type].name
        return None
    parent_name, sub_name = target
    parent_col = connections.children.get(parent_name)
    if parent_col is None:
        return None
    if sub_name:
        sub_col = parent_col.children.get(sub_name)
        if sub_col is None:
            return parent_col.name
        return sub_col.name
    return parent_col.name


def _org_sort_children_by_order(parent_col, order_list):
    """按 order_list 的顺序重排 parent_col 下的子集合（不在列表中的追加到末尾）。"""
    existing = list(parent_col.children.keys())
    ordered = [n for n in order_list if n in existing]
    rest = [n for n in existing if n not in order_list]
    for child_name in (ordered + rest):
        if child_name in parent_col.children.keys():
            col = parent_col.children[child_name]
            parent_col.children.unlink(col)
            parent_col.children.link(col)


class COMPONENT_OT_organize_scene(bpy.types.Operator):
    """按组名/组件类型把场景硬点空物体归入 Connections 集合层级。有组名→Groups/{组名}（不分类型）；无组名→按类型分（Engines/Turrets/...，Empties 按类型子集合）。不动父子与变换。"""
    bl_idname = "component_fixed.organize_scene"
    bl_label = t("ORGANIZE_SCENE")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.component_settings_fixed
        separator = settings.name_separator or "-"
        key = settings.name_key or ""
        scene = context.scene

        connections_col = _org_get_or_create_collection("Connections")

        # 两阶段：只处理场景根集合（scene.collection）里的物体，已在子集合里的不碰
        # （避免覆盖 ego 工具放入 Hulls 等集合的物体分类）
        scene_col = scene.collection
        root_objs = list(scene_col.objects)
        hardpoints = []   # 空物体硬点
        meshes = []       # 网格物体 → Mesh / Collisions
        lights = []       # 灯光 → Lights
        for o in root_objs:
            # 排除航点（有 hardpoint_type）和预览体（有 x4_preview_of 或名称含"预览"）
            if o.get("hardpoint_type"):
                continue
            if o.get("x4_preview_of") or "预览" in o.name:
                continue
            if o.type == 'EMPTY':
                hardpoints.append(o)
            elif o.type == 'MESH':
                meshes.append(o)
            elif o.type == 'LIGHT':
                lights.append(o)

        if not (hardpoints or meshes or lights):
            self.report({'WARNING'}, t("ERROR_NO_HARDPOINTS_ORG"))
            return {'CANCELLED'}

        # 预建顶层集合：Mesh / Collisions / Lights / Waypoints / Hulls
        _org_get_or_create_collection("Mesh")
        _org_get_or_create_collection("Collisions")
        _org_get_or_create_collection("Lights")

        # 预建 Groups、Empties 及其已知类型子集合（按约定顺序）
        groups_col = _org_get_or_create_collection("Groups", parent=connections_col)
        empties_col = _org_get_or_create_collection("Empties", parent=connections_col)
        for sub_name in _ORGANIZE_EMPTIES_SUBS:
            _org_get_or_create_collection(sub_name, parent=empties_col)

        moved = 0
        used_cols = set()

        # —— 空物体硬点：有组名→Groups/{组名}；无组名→按类型分 ——
        for obj in hardpoints:
            comp_type = _org_infer_comp_type(obj, settings)
            group = _org_extract_group(obj.name, separator, key)

            if group:
                grp_col = _org_get_or_create_collection(group, parent=groups_col)
                _org_move_to_collection(obj, grp_col.name)
                used_cols.add("Groups")
                used_cols.add(group)
            else:
                target = _org_type_to_collection(comp_type)
                if target is not None:
                    parent_name, sub_name = target
                    parent_col = _org_get_or_create_collection(parent_name, parent=connections_col)
                    used_cols.add(parent_name)
                    if sub_name:
                        sub_col = _org_get_or_create_collection(sub_name, parent=parent_col)
                        used_cols.add(sub_name)
                        _org_move_to_collection(obj, sub_col.name)
                    else:
                        _org_move_to_collection(obj, parent_col.name)
                else:
                    type_sub = comp_type if comp_type else "unknown"
                    sub_col = _org_get_or_create_collection(type_sub, parent=empties_col)
                    _org_move_to_collection(obj, sub_col.name)
                    used_cols.add(type_sub)
                    used_cols.add("Empties")
            moved += 1

        # —— 网格物体：含 collision → Collisions；否则 → Mesh ——
        for obj in meshes:
            name_l = obj.name.lower()
            if "collision" in name_l:
                target_name = "Collisions"
            else:
                target_name = "Mesh"
            _org_move_to_collection(obj, target_name)
            used_cols.add(target_name)
            moved += 1

        # —— 灯光 → Lights ——
        for obj in lights:
            _org_move_to_collection(obj, "Lights")
            used_cols.add("Lights")
            moved += 1

        # 场景根集合排序：Mesh / Collisions / Hulls / Waypoints / Lights / Connections
        _org_sort_children_by_order(scene.collection, _ORGANIZE_ROOT_ORDER)

        # 按 _ORGANIZE_COL_ORDER 重排 Connections 下的子集合
        for col_name in reversed(_ORGANIZE_COL_ORDER):
            if col_name in connections_col.children.keys():
                col = connections_col.children[col_name]
                connections_col.children.unlink(col)
                connections_col.children.link(col)

        # 子集合排序：Groups 按字母序；Empties 按约定顺序；其余类型集合按字母序
        _org_sort_children_alphabetically(groups_col)
        _org_sort_children_by_order(empties_col, _ORGANIZE_EMPTIES_SUBS)
        for col_name in connections_col.children.keys():
            if col_name not in ("Groups", "Empties"):
                sub_parent = connections_col.children[col_name]
                _org_sort_children_alphabetically(sub_parent)

        self.report({'INFO'}, t("INFO_ORGANIZED", moved, len(used_cols)))
        return {'FINISHED'}


def _shipmesh_decimate(obj, ratio):
    """为物体添加 Decimate(COLLAPSE) 修改器并应用，ratio 为保留面数比例。"""
    mod = obj.modifiers.new(name="Decimate", type='DECIMATE')
    mod.decimate_type = 'COLLAPSE'
    mod.ratio = ratio
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)


class COMPONENT_OT_organize_ship_mesh(bpy.types.Operator):
    """将选中的 EVE 舰船网格整理为 X4 命名规范：重命名为 part_main，UV 层→uv1，材质按 {前缀}{材质名}_a/b/c 重命名，复制出 collision / LOD1 / LOD2 / LOD3 / wreck，LOD 与 wreck 可选按比例减面。"""
    bl_idname = "component_fixed.organize_ship_mesh"
    bl_label = t("SHIP_MESH_ORGANIZE")
    bl_options = {'REGISTER', 'UNDO'}

    _SHIP_MESH_TAG = "x4_ship_mesh_organized"

    def invoke(self, context, event):
        # 破坏性操作，每次都要求确认
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        settings = context.scene.component_settings_fixed
        prefix = settings.ship_mesh_prefix.strip()
        mat_base = settings.ship_mesh_mat_name.strip() or "evemate"
        wreck_mat_name = settings.ship_mesh_wreck_mat.strip() or "p2generic.gen_p2_wreck_01"
        lod_mode = settings.ship_mesh_lod_mode
        wreck_mode = settings.ship_mesh_wreck_mode

        # 1. 获取原始网格物体：优先选中，否则场景第一个 MESH
        original_obj = context.active_object
        if not original_obj or original_obj.type != 'MESH':
            original_obj = next((o for o in context.scene.objects if o.type == 'MESH'), None)
        if not original_obj:
            self.report({'WARNING'}, t("ERROR_NO_MESH"))
            return {'CANCELLED'}

        # 0. 检测是否已整理过（避免重复整理破坏数据）
        if original_obj.get(self._SHIP_MESH_TAG):
            self.report({'WARNING'}, t("ERROR_ALREADY_ORGANIZED"))
            return {'CANCELLED'}

        # 2. UV 层：第一个改名为 uv1，没有则新建
        mesh = original_obj.data
        if len(mesh.uv_layers) > 0:
            mesh.uv_layers[0].name = "uv1"
        else:
            mesh.uv_layers.new(name="uv1")

        # 3. 重命名为 part_main
        original_obj.name = "part_main"
        original_obj.data.name = "part_main"

        # 4. 重命名 part_main 的材质：{prefix}{mat_base}_a / _b / _c ...
        part_main_materials = []
        for i, mat in enumerate(original_obj.data.materials):
            if mat is None:
                continue
            mat.name = f"{prefix}{mat_base}_{chr(ord('a') + i)}"
            part_main_materials.append(mat)

        col = context.collection
        created = []

        # 5. 复制衍生物体
        targets = [
            ("part_main.LOD1", lod_mode, settings.ship_mesh_lod1_ratio, part_main_materials),
            ("part_main.LOD2", lod_mode, settings.ship_mesh_lod2_ratio, part_main_materials),
            ("part_main.LOD3", lod_mode, settings.ship_mesh_lod3_ratio, part_main_materials),
            ("part_main.wreck", wreck_mode, settings.ship_mesh_wreck_ratio, None),  # wreck：单独材质
        ]

        # collision 可选（默认不创建），不附材质
        if settings.ship_mesh_create_collision:
            targets.insert(0, ("part_main.collision", None, None, None))

        for name, mode, ratio, mats in targets:
            new_obj = original_obj.copy()
            new_obj.data = original_obj.data.copy()
            new_obj.name = name
            new_obj.data.name = name
            col.objects.link(new_obj)

            # 材质处理
            if name == "part_main.collision":
                # collision 不附材质，清空原有材质槽
                new_obj.data.materials.clear()
            elif name == "part_main.wreck":
                w_mat = bpy.data.materials.new(name=wreck_mat_name)
                new_obj.data.materials.clear()
                new_obj.data.materials.append(w_mat)
            else:
                # LOD：复用 part_main 材质
                new_obj.data.materials.clear()
                for m in mats:
                    new_obj.data.materials.append(m)

            # 减面
            if mode == 'DECIMATE' and ratio is not None:
                _shipmesh_decimate(new_obj, ratio)

            # 标记已整理
            new_obj[self._SHIP_MESH_TAG] = True
            created.append(name)

        # 标记原始物体已整理
        original_obj[self._SHIP_MESH_TAG] = True

        self.report({'INFO'}, t("INFO_SHIP_MESH_DONE", len(created)))
        return {'FINISHED'}


class COMPONENT_OT_reset_spacing(bpy.types.Operator):
    """重置组件间距为最长边×1.3 并恢复自动跟随"""
    bl_idname = "component_fixed.reset_spacing"
    bl_label = "Reset Spacing"
    bl_description = "Reset spacing to component longest edge ×1.3 and re-enable auto-follow"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.component_settings_fixed
        global _spacing_updating
        s.spacing_auto = True
        _spacing_updating = True
        s.spacing = max(s.comp_size) * 1.3
        _spacing_updating = False
        return {'FINISHED'}


class COMPONENT_OT_apply_selected_name(bpy.types.Operator):
    """将选中组件的名称解构回填到组名/组件名称栏"""
    bl_idname = "component_fixed.apply_selected_name"
    bl_label = t("APPLY_SELECTED_NAME")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.component_settings_fixed
        selected = context.selected_objects
        if not selected:
            self.report({'WARNING'}, t("ERROR_NO_SELECTION"))
            return {'CANCELLED'}

        obj = selected[0]
        name = obj.name

        # 必须以 '-' 为连接符、以 'up7con_' 为组件key（连接符与 key 连写出现）
        anchor = _DEFAULT_NAME_SEPARATOR + _DEFAULT_COMP_KEY  # "-up7con_"
        if anchor not in name:
            self.report({'ERROR'}, t("ERROR_NAME_FORMAT"))
            return {'CANCELLED'}

        idx = name.index(anchor)
        group = name[:idx]
        rest = name[idx + len(anchor):]  # 名称段-序号 或 名称段

        # 剥离末尾三位自动序号（如 turret_l-001 -> turret_l）
        base = strip_sequence_suffix(rest)

        # 回填四栏（锁联动，避免中间态串写）
        _COMP_NAME_SYNC[0] = True
        try:
            settings.name_separator = _DEFAULT_NAME_SEPARATOR
            settings.name_key = _DEFAULT_COMP_KEY
            settings.name_group = group
            settings.name_base = base
        finally:
            _COMP_NAME_SYNC[0] = False

        self.report({'INFO'}, t("INFO_NAME_APPLIED", group, base))
        return {'FINISHED'}


class COMPONENT_OT_import_waypoints(bpy.types.Operator):
    """从 X4 XML 导入 waypoint 与 dockingbay 连接点，在场景中创建空物体并建立朝向约束"""
    bl_idname = "component_fixed.import_waypoints"
    bl_label = t("WP_IMPORT")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.component_settings_fixed
        xml_path = bpy.path.abspath(settings.wp_xml_path)

        if not xml_path or not os.path.isfile(xml_path):
            self.report({'WARNING'}, t("WP_ERROR_NO_XML"))
            return {'CANCELLED'}

        # 确保在对象模式
        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        base_name = settings.wp_base_name
        s_size = settings.wp_s_size
        m_length = settings.wp_m_length
        m_width = settings.wp_m_width
        m_height = settings.wp_m_height
        default_size = settings.wp_default_size

        # 清除已有对象
        if settings.wp_clear_existing:
            for obj in list(context.scene.objects):
                if (obj.name.startswith(base_name) or
                        obj.name.startswith("XU_Waypoint") or
                        "dockingbay" in obj.name.lower()):
                    bpy.data.objects.remove(obj, do_unlink=True)

        # 加载 XML
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except Exception as e:
            self.report({'ERROR'}, t("WP_ERROR_XML", str(e)))
            return {'CANCELLED'}

        # 获取 waypoints
        waypoints_elem = root.find(".//waypoints")
        waypoints = waypoints_elem.findall("waypoint") if waypoints_elem is not None else []

        # 获取 dockingbay connections
        dockingbay_connections = []
        connections_elem = root.find(".//connections")
        if connections_elem is not None:
            for conn in connections_elem.findall("connection"):
                if "dockingbay" in conn.get("tags", ""):
                    dockingbay_connections.append(conn)

        # 连接符全局设置：本操作符内所有命名/冲突检查/兼容替换均以此为准（空则回退 ---）
        sep = settings.wp_name_separator or "---"

        # dockingbay 名称/组名含连接符序列：报错提示并中止，由用户先改 XML
        # （waypoint 的冲突序列在创建时自动替换为 '_'，见下）
        dash3_errors = [c.get("name", "dockingbay") for c in dockingbay_connections
                        if sep in c.get("name", "") or sep in c.get("group", "")]
        if dash3_errors:
            for n in dash3_errors:
                self.report({'ERROR'}, t("WP_IMPORT_ERR_DASH3", n, sep))
            return {'CANCELLED'}

        def _x4_to_blender(x, y, z):
            return Vector((x, z, y))

        def _set_dockingbay_size(obj, name):
            name_lower = name.lower()
            if '_s_' in name_lower:
                obj.empty_display_size = s_size
                obj.scale = (1.0, 1.0, 1.0)
            elif '_m_' in name_lower:
                obj.empty_display_size = 1.0
                obj.scale = (m_width, m_length, m_height)
            else:
                obj.empty_display_size = default_size
                obj.scale = (1.0, 1.0, 1.0)

        waypoint_objects = {}
        waypoint_links = {}

        # 硬点类型识别：名称含 dockingbay/waypoint 自动判定，看不出来按用户指定类型
        def _detect_hp_type(xml_name, fallback):
            nl = xml_name.lower()
            if "dockingbay" in nl:
                return "dockingbay"
            if "waypoint" in nl:
                return "waypoint"
            return fallback

        # 命名：group + 连接符 + name，作为物体名称（sep 已在上方统一定义）
        def _hp_obj_name(xml_name, group):
            return f"{group}{sep}{xml_name}" if group else xml_name

        # 第一遍：创建所有 waypoint 对象
        for wp in waypoints:
            # 旧文件 name/group 含连接符序列自动替换为 '_'，仅针对 waypoint
            name = wp.get("name", "waypoint").replace(sep, "_")
            tags = wp.get("tags", "").split()
            group = wp.get("group", "").replace(sep, "_")

            x = float(wp.get("x", 0))
            y = float(wp.get("y", 0))
            z = float(wp.get("z", 0))
            location = _x4_to_blender(x, y, z)

            if "start" in tags:
                empty_type = 'SPHERE'
            elif "end" in tags:
                empty_type = 'CUBE'
            else:
                empty_type = 'CONE'

            obj_name = _hp_obj_name(name, group)
            obj = bpy.data.objects.new(obj_name, None)
            obj.empty_display_size = default_size
            obj.empty_display_type = empty_type
            obj.location = location
            context.scene.collection.objects.link(obj)

            for tag in tags:
                if tag:
                    obj[tag] = True
            # 同时写入 tags 字符串属性，供 x4_wp_export.py 格式导出使用
            obj["tags"] = " ".join(t for t in tags if t)
            # 分组名（游戏靠 group 属性把 dockpath 绑定到泊位 dockingbay）
            obj["group"] = group
            # 硬点类型（waypoint / dockingbay）
            obj["hardpoint_type"] = _detect_hp_type(name, settings.wp_default_hp_type)
            # 车道朝向：按 XML 四元数推断（原版规律：qy≈±1 绕竖直轴 180° = 出站道，qw≈±1 恒等 = 进站道）
            qy = float(wp.get("qy", 0))
            qw = float(wp.get("qw", 1))
            if abs(qy) > 0.9 and abs(qw) < 0.1:
                obj["lane"] = "outbound"
            elif abs(qw) > 0.9:
                obj["lane"] = "inbound"

            waypoint_objects[name] = obj

            links_elem = wp.find("links")
            if links_elem is not None:
                link_refs = [le.get("ref") for le in links_elem.findall("link") if le.get("ref")]
                if link_refs:
                    waypoint_links[name] = link_refs
            obj["links"] = ""

        # 将 links 目标映射为实际物体名（导入时物体名带 group 前缀）
        for wp_name, target_refs in waypoint_links.items():
            source_obj = waypoint_objects.get(wp_name)
            if source_obj is None:
                continue
            actual_refs = [waypoint_objects[r].name for r in target_refs if r in waypoint_objects]
            source_obj["links"] = " ".join(actual_refs)

        # 第二遍：创建 dockingbay 连接点
        for conn in dockingbay_connections:
            name = conn.get("name", "dockingbay")
            group = conn.get("group", "")
            offset_elem = conn.find("offset")
            if offset_elem is None:
                continue
            position_elem = offset_elem.find("position")
            if position_elem is None:
                continue

            x = float(position_elem.get("x", 0))
            y = float(position_elem.get("y", 0))
            z = float(position_elem.get("z", 0))
            location = _x4_to_blender(x, y, z)

            obj_name = _hp_obj_name(name, group)
            obj = bpy.data.objects.new(obj_name, None)
            obj.empty_display_type = 'CUBE'
            obj.empty_display_size = 1.0
            obj.location = location
            context.scene.collection.objects.link(obj)

            _set_dockingbay_size(obj, name)

            for tag in conn.get("tags", "").split():
                if tag:
                    obj[tag] = True
            # dockingbay 只记类型与组名（模型相关，不修改名称和数量，后续只更新 group）
            obj["hardpoint_type"] = _detect_hp_type(name, "dockingbay")
            obj["group"] = group

        # 官方 ego 方式：物体旋转保持 0；对每个 link 创建一个 Track To 约束（追踪轴 Y，向上 X）
        constraint_count = 0
        for wp_name, target_names in waypoint_links.items():
            source_obj = waypoint_objects.get(wp_name)
            if source_obj is None:
                continue
            for target_name in target_names:
                target_obj = waypoint_objects.get(target_name)
                if target_obj is None:
                    continue
                constraint = source_obj.constraints.new(type='TRACK_TO')
                constraint.target = target_obj
                constraint.track_axis = 'TRACK_Y'
                constraint.up_axis = 'UP_X'
                constraint_count += 1

        self.report({'INFO'}, t("WP_INFO_IMPORTED", len(waypoints), len(dockingbay_connections), constraint_count))
        return {'FINISHED'}


# ============================ Waypoint 词汇表（与 x4_wp_export.py 一致） ============================
WP_TAG_VOCAB = {
    "typenormal", "typedock", "dockpath", "entrypath", "exitpath",
    "start", "end", "blocklink", "closelink", "force",
    "sizeLevel0", "sizeLevel1",
    "speed_slow", "speed_normal", "speed_fast", "speed_faster",
    "dynamicmasstraffic",
}

WP_TAG_CATEGORIES = [
    ("WP_TAG_CAT_TYPE",     ["typenormal", "typedock", "dockpath", "entrypath", "exitpath"]),
    ("WP_TAG_CAT_ENDPOINT", ["start", "end"]),
    ("WP_TAG_CAT_GROUP",    ["blocklink", "closelink", "force"]),
    ("WP_TAG_CAT_SIZE",     ["sizeLevel0", "sizeLevel1"]),
    ("WP_TAG_CAT_SPEED",    ["speed_slow", "speed_normal", "speed_fast", "speed_faster"]),
    ("WP_TAG_CAT_OTHER",    ["dynamicmasstraffic"]),
]

# Blender Z-up → X4 Y-up 的坐标换算常量（与 x4_wp_export.py 完全一致）
_WP_C = Quaternion((0.70710678, -0.70710678, 0.0, 0.0))
_WP_C_INV = _WP_C.inverted()


def _wp_fmt(v):
    # 浮点残渣吸附：|v| < 1e-6 视为 0（原版导出也有 7.5e-08 这类残渣，写 0 更干净；
    # 阈值远低于 waypoint 坐标/四元数的最小有效量级，不会误吸合法值）
    if abs(v) < 1e-6:
        v = 0.0
    s = "%.7g" % v
    if s in ("-0", "-0.0"):
        s = "0"
    return s


def _apply_wp_display_type(obj):
    """根据 start/end tag 设置空物体显示形状：start→球，end→方，否则锥"""
    tags = set(str(obj.get("tags", "")).split())
    if "start" in tags:
        obj.empty_display_type = 'SPHERE'
    elif "end" in tags:
        obj.empty_display_type = 'CUBE'
    else:
        obj.empty_display_type = 'CONE'


def _sync_wp_tag_props(obj):
    """把 tags 字符串同步为词汇表内各 tag 的独立布尔自定义属性（与导入行为一致）"""
    tags = set(str(obj.get("tags", "")).split())
    for tg in WP_TAG_VOCAB:
        if tg in obj and tg not in tags:
            del obj[tg]
    for tg in tags:
        if tg in WP_TAG_VOCAB:
            obj[tg] = True


def _sync_wp_constraints(obj):
    """按 links 字符串重建 Track To 约束（TRACK_Y / UP_X），与导入逻辑一致"""
    # 先清除已有的 Track To 约束（避免重复/过时）
    for c in list(obj.constraints):
        if c.type == 'TRACK_TO':
            obj.constraints.remove(c)
    link_names = str(obj.get("links", "")).split()
    for name in link_names:
        target = bpy.data.objects.get(name)
        if target is None:
            continue
        constraint = obj.constraints.new(type='TRACK_TO')
        constraint.target = target
        constraint.track_axis = 'TRACK_Y'
        constraint.up_axis = 'UP_X'


class COMPONENT_OT_make_waypoint(bpy.types.Operator):
    """给选中物体添加 tags/links 自定义属性，使其成为可导出的 waypoint"""
    bl_idname = "component_fixed.make_waypoint"
    bl_label = t("WP_MAKE_WAYPOINT")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not context.selected_objects:
            self.report({'WARNING'}, t("ERROR_NO_SELECTION"))
            return {'CANCELLED'}
        count = 0
        for obj in context.selected_objects:
            if "tags" not in obj:
                obj["tags"] = "typenormal"
            if "links" not in obj:
                obj["links"] = ""
            if "group" not in obj:
                obj["group"] = ""
            if "hardpoint_type" not in obj:
                obj["hardpoint_type"] = "waypoint"
            if "lane" not in obj:
                obj["lane"] = "inbound"
            _sync_wp_tag_props(obj)
            _apply_wp_display_type(obj)
            count += 1
        self.report({'INFO'}, t("WP_MAKE_WAYPOINT_DONE", count))
        return {'FINISHED'}


class COMPONENT_OT_toggle_waypoint_tag(bpy.types.Operator):
    """切换当前激活 waypoint 的某个 tag"""
    bl_idname = "component_fixed.toggle_wp_tag"
    bl_label = "Toggle Waypoint Tag"
    bl_options = {'REGISTER', 'UNDO'}

    tag: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.get("tags") is None:
            self.report({'WARNING'}, t("WP_NO_SELECTED_WAYPOINT"))
            return {'CANCELLED'}
        tags = set(obj["tags"].split())
        if self.tag in tags:
            tags.remove(self.tag)
        else:
            tags.add(self.tag)
            # 端点互斥：选 start 则移除 end，选 end 则移除 start
            if self.tag == "start":
                tags.discard("end")
            elif self.tag == "end":
                tags.discard("start")
        # 保持稳定顺序便于阅读
        obj["tags"] = " ".join(sorted(tags))
        _sync_wp_tag_props(obj)
        _apply_wp_display_type(obj)
        return {'FINISHED'}


class COMPONENT_OT_set_wp_lane(bpy.types.Operator):
    """设置当前激活 waypoint 的车道朝向（lane 属性控制导出四元数）"""
    bl_idname = "component_fixed.set_wp_lane"
    bl_label = "Set Waypoint Lane"
    bl_options = {'REGISTER', 'UNDO'}

    lane: bpy.props.StringProperty()  # inbound / outbound / ""（清除=跟随物体实际旋转）

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.get("tags") is None:
            self.report({'WARNING'}, t("WP_NO_SELECTED_WAYPOINT"))
            return {'CANCELLED'}
        if self.lane and obj.get("lane") == self.lane:
            del obj["lane"]  # 再点一次同值 = 清除，回到手动旋转模式
        elif self.lane:
            obj["lane"] = self.lane
        elif "lane" in obj:
            del obj["lane"]
        return {'FINISHED'}


class COMPONENT_OT_add_waypoint(bpy.types.Operator):
    """在 3D 光标处新建一个 waypoint 空物体（带 tags/links 自定义属性）"""
    bl_idname = "component_fixed.add_waypoint"
    bl_label = t("WP_ADD")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.component_settings_fixed
        loc = context.scene.cursor.location.copy()

        # 命名：组名+连接符+航点名+序号（同放置器命名规则），如 mt_dock_01---waypoint_01
        # 组名/航点名禁含连接符序列（输出按第一个连接符截断解析 group/name），自动替换为 _
        sep = settings.wp_name_separator or "---"
        group = settings.wp_name_group.strip().replace(sep, "_")
        base_setting = settings.wp_name_base.replace(sep, "_")
        prefix = (group + sep + base_setting) if group else base_setting
        max_idx = 0
        for o in bpy.data.objects:
            if o.name.startswith(prefix + "_"):
                suffix = o.name[len(prefix) + 1:]
                num = suffix.split(".")[0]  # 忽略 Blender 自动重名的 .001 后缀
                if num.isdigit():
                    max_idx = max(max_idx, int(num))
        obj = bpy.data.objects.new(f"{prefix}_{max_idx + 1:02d}", None)
        obj.empty_display_type = 'CONE'
        obj.empty_display_size = settings.wp_default_size
        obj.location = loc
        obj["tags"] = "dockpath"
        obj["links"] = ""
        obj["group"] = group
        obj["hardpoint_type"] = "waypoint"
        obj["lane"] = "inbound"  # 车道朝向：默认进站道（恒等），出站道导出时转 180°
        _sync_wp_tag_props(obj)
        _apply_wp_display_type(obj)
        context.scene.collection.objects.link(obj)
        # 选中新建物体
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {'FINISHED'}


class COMPONENT_OT_apply_wp_group(bpy.types.Operator):
    """把选中硬点名称的第一个 - 左侧替换为组名设置（即修改其 group），不动名称其余部分和数量"""
    bl_idname = "component_fixed.apply_wp_group"
    bl_label = t("WP_APPLY_GROUP")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.component_settings_fixed
        sep = settings.wp_name_separator or "---"
        group = settings.wp_name_group.strip().replace(sep, "_")
        count = 0
        for obj in context.selected_objects:
            if obj.get("hardpoint_type") is None and obj.get("tags") is None:
                continue
            # 只替换名称第一个连接符左侧前缀（即改组），名称其余部分与数量不动
            base = obj.name.split(sep, 1)[1] if sep in obj.name else obj.name
            obj.name = f"{group}{sep}{base}" if group else base
            obj["group"] = group
            count += 1
        if count == 0:
            self.report({'WARNING'}, t("WP_NO_SELECTED_WAYPOINT"))
            return {'CANCELLED'}
        self.report({'INFO'}, t("WP_APPLY_GROUP_DONE", count, group))
        return {'FINISHED'}


class COMPONENT_OT_add_links_from_selection(bpy.types.Operator):
    """把选中的其他物体名称追加到激活 waypoint 的 links（去重，不覆盖已输入内容）"""
    bl_idname = "component_fixed.add_wp_links_from_selection"
    bl_label = t("WP_ADD_LINKS_FROM_SEL")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.get("tags") is None:
            self.report({'WARNING'}, t("WP_NO_SELECTED_WAYPOINT"))
            return {'CANCELLED'}
        existing = set(str(obj.get("links", "")).split())
        added = []
        for sel in context.selected_objects:
            if sel is obj:
                continue
            if sel.name not in existing:
                existing.add(sel.name)
                added.append(sel.name)
        obj["links"] = " ".join(sorted(existing))
        _sync_wp_constraints(obj)
        if added:
            self.report({'INFO'}, t("WP_LINKS_ADDED", ", ".join(added)))
        else:
            self.report({'INFO'}, t("WP_LINKS_NO_NEW"))
        return {'FINISHED'}


class COMPONENT_OT_clear_links(bpy.types.Operator):
    """清除激活 waypoint 的 links"""
    bl_idname = "component_fixed.clear_wp_links"
    bl_label = t("WP_CLEAR_LINKS")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.get("tags") is None:
            self.report({'WARNING'}, t("WP_NO_SELECTED_WAYPOINT"))
            return {'CANCELLED'}
        obj["links"] = ""
        _sync_wp_constraints(obj)
        return {'FINISHED'}


class COMPONENT_OT_rebuild_wp_constraints(bpy.types.Operator):
    """按当前 links 字符串重建激活 waypoint 的 Track To 约束"""
    bl_idname = "component_fixed.rebuild_wp_constraints"
    bl_label = t("WP_REBUILD_CONSTRAINTS")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.get("tags") is None:
            self.report({'WARNING'}, t("WP_NO_SELECTED_WAYPOINT"))
            return {'CANCELLED'}
        _sync_wp_constraints(obj)
        self.report({'INFO'}, t("WP_CONSTRAINTS_REBUILT"))
        return {'FINISHED'}


def _detect_hp_type(obj):
    """硬点类型：自定义属性 hardpoint_type → 名称含 dockingbay/waypoint → waypoint"""
    hp = str(obj.get("hardpoint_type") or "").strip().lower()
    if hp in ("dockingbay", "waypoint"):
        return hp
    nl = obj.name.lower()
    if "dockingbay" in nl:
        return "dockingbay"
    if "waypoint" in nl:
        return "waypoint"
    return "waypoint"


class COMPONENT_OT_export_waypoints(bpy.types.Operator):
    """导出 waypoint 为 x4_wp_export.py 所需的 XML 格式"""
    bl_idname = "component_fixed.export_waypoints"
    bl_label = t("WP_EXPORT")
    bl_options = {'REGISTER', 'UNDO'}

    @staticmethod
    def _is_hardpoint(o):
        return o.get("hardpoint_type") is not None or o.get("tags") is not None

    @staticmethod
    def _collect(context):
        # 默认输出全部硬点（waypoint + dockingbay）；有选中硬点时只导出选中
        # 用场景根集合递归（scene.collection.all_objects），硬点在任何集合都不会漏
        selected = [o for o in context.selected_objects
                    if COMPONENT_OT_export_waypoints._is_hardpoint(o)]
        if selected:
            return selected
        return [o for o in context.scene.collection.all_objects
                if COMPONENT_OT_export_waypoints._is_hardpoint(o)]

    @staticmethod
    def _convert(obj):
        m = obj.matrix_world
        p = m.translation
        # 位置：导入 (x, z, y) 的逆映射，与游戏原文件一致（不再 z 取反）
        x, y, z = p.x, p.z, p.y
        # 旋转：lane 属性优先——inbound=恒等朝向，outbound=绕竖直轴 180°（原版 dockarea 车道规律：
        # 进站道恒等、回程道 qy=±1/qw≈0，与泊位 offset 四元数互证）；
        # 未设置 lane 时回退到物体实际旋转（rotation_quaternion，Track To 约束不参与导出）
        lane = obj.get("lane")
        if lane == "inbound":
            return x, y, z, 0.0, 0.0, 0.0, 1.0
        if lane == "outbound":
            return x, y, z, 0.0, -1.0, 0.0, 0.0
        q = obj.rotation_quaternion
        qq = _WP_C @ q @ _WP_C_INV
        # 四元数符号规范化：q 与 -q 是同一旋转（双覆盖），统一 qw >= 0 写法
        #（原版进站道写 qw=-1 是异号残渣，导出统一为 qw=+1，语义相同）
        if qq.w < 0:
            qq.negate()
        return x, y, z, qq.x, qq.y, qq.z, qq.w

    @staticmethod
    def _validate(wps, sep, scene_wp_names):
        # wps 元组：(hp_type, export_name, group, tags_str, links_list, pos_rot)
        # 校验只针对 waypoint 类型；dockingbay 只校验 name/group 不含连接符（冲突时报错由用户处理）
        # 链接目标：在导出范围内 → 正常；在场景中存在但未导出 → 警告；场景中不存在 → 错误
        wp_entries = [w for w in wps if w[0] == "waypoint"]
        names = [w[1] for w in wp_entries]
        name_set = set()
        errors, warnings = [], []

        for hp_type, name, group, _tags, _links, _pos in wps:
            if hp_type == "dockingbay" and (sep in name or sep in group):
                errors.append(t("WP_EXPORT_ERR_DASH3", name, sep))

        for n in names:
            if n in name_set:
                errors.append(t("WP_EXPORT_ERR_DUP", n))
            name_set.add(n)

        for _t, name, _group, tags_str, _links, _pos in wp_entries:
            if not tags_str.strip():
                errors.append(t("WP_EXPORT_ERR_EMPTY_TAGS", name))
                continue
            for tg in tags_str.split():
                if tg not in WP_TAG_VOCAB:
                    errors.append(t("WP_EXPORT_ERR_BAD_TAG", name, tg))

        for _t, name, _group, tags_str, links_list, _pos in wp_entries:
            for ln in links_list:
                if ln in name_set:
                    continue
                if ln in scene_wp_names:
                    warnings.append(t("WP_EXPORT_WARN_LINK_OUTSIDE", name, ln))
                else:
                    errors.append(t("WP_EXPORT_ERR_LINK", name, ln))

        has_dock = any("typedock" in w[3].split() for w in wp_entries)
        if not has_dock:
            warnings.append(t("WP_EXPORT_WARN_DOCK"))

        block_starts = sum(1 for w in wp_entries if "blocklink" in w[3].split() and "start" in w[3].split())
        block_ends = sum(1 for w in wp_entries if "blocklink" in w[3].split() and "end" in w[3].split())
        if block_starts != block_ends:
            warnings.append(t("WP_EXPORT_WARN_BLOCK", block_starts, block_ends))

        has_size = any("sizeLevel1" in w[3].split() for w in wp_entries)
        if not has_size:
            warnings.append(t("WP_EXPORT_WARN_SIZE"))

        # group 规则：共享路径网（typedock 走廊式）waypoint 不写 group 完全合法；
        # 每泊位专用跑道（dockpath 并行链）多泊位时才必须 group 一一绑定。
        # 故仅当本文件已存在分组（说明在做多泊位）时，才对缺组的 dockpath 给警告。
        any_group = any(w[2] for w in wp_entries)
        for _t, name, group, tags_str, links_list, _pos in wp_entries:
            ts = tags_str.split()
            if "typenormal" in ts and not links_list and "end" not in ts and "dynamicmasstraffic" not in ts:
                warnings.append(t("WP_EXPORT_WARN_NORMAL", name))
            if any_group and not group and "dockpath" in ts and "typedock" not in ts:
                warnings.append(t("WP_EXPORT_WARN_NO_GROUP", name))

        # 组对应检查：waypoint 的组名集合与 dockingbay 的组名集合应一一对应，否则警告（不阻断输出）
        wp_groups = {w[2] for w in wp_entries if w[2]}
        dock_groups = {w[2] for w in wps if w[0] == "dockingbay" and w[2]}
        for g in sorted(wp_groups - dock_groups):
            warnings.append(t("WP_EXPORT_WARN_GROUP_NO_DOCK", g))
        for g in sorted(dock_groups - wp_groups):
            warnings.append(t("WP_EXPORT_WARN_DOCK_NO_WP", g))

        return errors, warnings

    @staticmethod
    def _build_xml(wps):
        lines = ['<?xml version="1.0" encoding="utf-8"?>', "<waypoints>"]
        # waypoint 类型：完整属性输出
        for hp_type, name, group, tags_str, links_list, pos_rot in wps:
            if hp_type != "waypoint":
                continue
            x, y, z, qx, qy, qz, qw = pos_rot
            group_attr = f' group="{group}"' if group else ""
            links_xml = ""
            if links_list:
                links_inner = "".join(f'<link ref="{ln}"/>' for ln in links_list)
                links_xml = f"\n    <links>{links_inner}</links>"
            lines.append(
                f'  <waypoint name="{name}" tags="{tags_str}"{group_attr} '
                f'x="{_wp_fmt(x)}" y="{_wp_fmt(y)}" z="{_wp_fmt(z)}" '
                f'qx="{_wp_fmt(qx)}" qy="{_wp_fmt(qy)}" qz="{_wp_fmt(qz)}" qw="{_wp_fmt(qw)}">{links_xml}\n  </waypoint>'
            )
        lines.append("</waypoints>")
        # dockingbay 类型：输出到 <connections> 段，只含 name/group，供后续工具定位并更新 group 值
        docks = [(name, group) for hp_type, name, group, _t, _l, _p in wps if hp_type == "dockingbay"]
        if docks:
            lines.append("<connections>")
            for name, group in docks:
                group_attr = f' group="{group}"' if group else ""
                lines.append(f'  <connection name="{name}"{group_attr}/>')
            lines.append("</connections>")
        return "\n".join(lines)

    def execute(self, context):
        settings = context.scene.component_settings_fixed
        export_path = bpy.path.abspath(settings.wp_export_path)
        if not export_path:
            self.report({'WARNING'}, t("WP_ERROR_NO_EXPORT_PATH"))
            return {'CANCELLED'}

        # 若填的是目录（已存在或是以分隔符结尾），自动补默认文件名
        if os.path.isdir(export_path) or export_path.endswith(('/', '\\')):
            export_path = os.path.join(export_path.rstrip('/\\'), "waypoints.xml")
        # 确保父目录存在
        parent = os.path.dirname(export_path)
        if parent and not os.path.isdir(parent):
            try:
                os.makedirs(parent, exist_ok=True)
            except Exception as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}

        objs = self._collect(context)
        if not objs:
            self.report({'WARNING'}, t("WP_EXPORT_EMPTY"))
            return {'CANCELLED'}

        # 全场景硬点名称表：links 目标的映射与存在性判断以全场景为准，
        # 避免"只导出选中子集时，指向未选中 waypoint 的链接被误报不存在"
        sep = settings.wp_name_separator or "---"
        scene_map = {}      # 物体名 -> 导出基础名（全场景）
        scene_wp_names = set()  # 全场景 waypoint 导出基础名集合
        for o in context.scene.collection.all_objects:
            if not self._is_hardpoint(o):
                continue
            hp = _detect_hp_type(o)
            b = o.name.split(sep, 1)[1] if sep in o.name else o.name
            if hp == "waypoint":
                b = b.replace(sep, "_")
                scene_wp_names.add(b)
            scene_map[o.name] = b

        # 第一遍：解析每个硬点的导出名、组名与类型。
        # 规则：物体名从左起第一个连接符截断，左边是 group，右边是 name；不含连接符说明没有分组
        name_map = {}  # 物体名 -> 导出基础名（把 links 里的物体名映射回导出名）
        resolved = []  # (obj, hp_type, export_name, group)
        for o in objs:
            hp_type = _detect_hp_type(o)
            if sep in o.name:
                group_raw, base_raw = o.name.split(sep, 1)
            else:
                group_raw, base_raw = "", o.name
            # 旧文件兼容：waypoint 的 group/name 残留连接符自动替换为 _；
            # dockingbay 保持原样，由 _validate 报错让用户处理
            if hp_type == "waypoint":
                group = group_raw.replace(sep, "_")
                base = base_raw.replace(sep, "_")
            else:
                group, base = group_raw, base_raw
            name_map[o.name] = base
            resolved.append((o, hp_type, base, group))

        wps = []
        for o, hp_type, export_name, group in resolved:
            tags_str = o.get("tags", "") or ""
            links_str = o.get("links", "") or ""
            # links 里存的是 Blender 物体名（带 group+连接符前缀），按全场景名称表映射回基础名
            links_list = [scene_map.get(ln, name_map.get(ln, ln.split(sep, 1)[1] if sep in ln else ln))
                          for ln in links_str.split() if ln]
            pos_rot = self._convert(o)
            wps.append((hp_type, export_name, group, tags_str, links_list, pos_rot))

        errors, warnings = self._validate(wps, sep, scene_wp_names)
        if errors:
            for e in errors:
                self.report({'ERROR'}, e)
            self.report({'ERROR'}, t("WP_EXPORT_ERRORS", len(errors)))
            return {'CANCELLED'}
        for w in warnings:
            self.report({'WARNING'}, w)

        xml = self._build_xml(wps)
        try:
            with open(export_path, "w", encoding="utf-8") as f:
                f.write(xml)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        n_wp = sum(1 for w in wps if w[0] == "waypoint")
        n_db = sum(1 for w in wps if w[0] == "dockingbay")
        self.report({'INFO'}, t("WP_EXPORT_DONE", n_wp, n_db, len(warnings), export_path))
        return {'FINISHED'}


class COMPONENT_FIXED_PT_placer_panel(bpy.types.Panel):
    """组件放置器主面板"""
    bl_label = t("PANEL_TITLE")
    bl_idname = "COMPONENT_FIXED_PT_placer_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "组件放置-修正"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.component_settings_fixed
        translation_settings = scene.component_translation_fixed

        # ========== 面板级通用设置：组件key（膨胀/收缩、XML补全等功能按此识别硬点，航路编辑器连接符同位置） ==========
        row = layout.row(align=True)
        row.label(text="", icon='KEYINGSET')  # 组件key图标
        row.prop(settings, "name_key", text=t("NAME_KEY"))
        layout.label(text=t("NAME_KEY_NOTE"), icon='INFO')
        layout.separator(factor=1.7)

        # ========== 场景整理：按组件类型归入 Connections 集合层级（放面板最前面，先建层级再放置） ==========
        box = layout.box()
        box.label(text=t("ORGANIZE_SCENE"), icon='OUTLINER_COLLECTION')
        box.operator("component_fixed.organize_scene", text=t("ORGANIZE_SCENE"), icon='OUTLINER_COLLECTION')
        layout.separator(factor=1.7)

        box = layout.box()
        box.label(text=t("BASIC_SETTINGS"), icon='SETTINGS')

        # 预设选择
        row = box.row()
        row.label(text=t("PRESET_SELECTION") + ":")
        row.prop(settings, "preset_index", text="")

        # 应用选中组件设定：解构选中组件名称回填组名/组件名称
        box.operator("component_fixed.apply_selected_name",
                     text=t("APPLY_SELECTED_NAME"), icon='EYEDROPPER')

        # 组件名称三栏：组名 + 链接符号 + 名称，放置时拼接为完整组件名称（组件key已移至面板顶部统一设置）
        box.prop(settings, "name_group", text=t("NAME_GROUP"))
        box.prop(settings, "name_separator", text=t("NAME_SEPARATOR"))
        box.prop(settings, "name_base", text=t("COMP_NAME"))
        box.prop(settings, "comp_size", text=t("COMP_SIZE"))

        # 名称替换
        box.separator()
        box.label(text=t("REPLACE_TITLE") + ":")
        box.prop(translation_settings, "replace_find", text=t("FIND_TEXT"))
        box.prop(translation_settings, "replace_with", text=t("REPLACE_TEXT"))
        box.operator("component_fixed.replace_names", text=t("EXECUTE_REPLACE"), icon='FILE_REFRESH')

        # ========== 放置模块：放置模式 + 朝向设置 + 位置偏移 + 执行放置（同一深色背景） ==========
        layout.separator(factor=1.7)  # 模块间隔加宽 70%
        box = layout.box()
        box.label(text=t("PLACEMENT_MODULE"), icon='MOD_ARRAY')

        box.label(text=t("PLACEMENT_MODE") + ":")
        box.prop(settings, "placement_mode", text="")
        if settings.placement_mode == 'MULTI':
            box.prop(settings, "arrange_direction")
            row = box.row(align=True)
            row.prop(settings, "spacing", text=t("SPACING"))
            row.operator("component_fixed.reset_spacing", text="", icon='FILE_REFRESH')

            row = box.row()
            row.prop(settings, "auto_count", text=t("AUTO_COUNT"))
            if not settings.auto_count:
                box.prop(settings, "manual_count", text=t("MANUAL_COUNT"))

            box.separator()
            box.prop(settings, "placement_surface_mode", text=t("SURFACE_MODE"))
            box.prop(settings, "placement_forward_mode", text=t("FORWARD_MODE"))

        box.separator()
        box.label(text=t("ORIENTATION_SETTINGS") + ":")

        row = box.row()
        row.label(text=t("ALIGN_AXIS") + ":")
        row.prop(settings, "align_axis", text="")

        row = box.row()
        row.label(text=t("FORWARD_AXIS") + ":")
        row.prop(settings, "forward_axis", text="")

        # 向上轴：仅当法向轴与朝向轴为同一轴时启用，否则自动推导（置灰不处理；按轴字母比较，v9.13.2）
        row = box.row(align=True)
        row.label(text=t("UP_AXIS") + ":")
        up_sub = row.row(align=True)
        up_sub.prop(settings, "up_axis", text="")
        up_sub.enabled = settings.forward_axis[0] == settings.align_axis[0]

        box.separator()
        box.label(text=t("POSITION_OFFSETS") + ":")

        if settings.placement_mode == 'SINGLE':
            box.prop(settings, "face_x_offset", text=t("FACE_X_OFFSET"))
            box.prop(settings, "face_y_offset", text=t("FACE_Y_OFFSET"))

        # 表面偏移联动：表面偏移 = 组件尺寸联动方向长度 × 百分比（改动任一项自动填值）
        row = box.row(align=True)
        row.label(text=t("OFFSET_AXIS"))
        row.prop(settings, "offset_axis", expand=True)
        box.prop(settings, "offset_percent", text=t("OFFSET_PERCENT"))
        box.prop(settings, "surface_offset", text=t("SURFACE_OFFSET"))

        box.separator()
        box.operator("component_fixed.placer", text=t("EXECUTE_PLACEMENT"), icon='OBJECT_DATA')

        # 镜像工具（放置后顺手镜像）
        layout.separator(factor=1.7)
        box = layout.box()
        box.label(text=t("MIRROR_TITLE"), icon='MOD_MIRROR')
        box.operator("component_fixed.mirror_x", text=t("APPLY_MIRROR"))

        layout.separator(factor=1.7)
        box = layout.box()
        box.label(text=t("TRANSLATION_TOOLS"), icon='ORIENTATION_GIMBAL')

        selected_count = len(context.selected_objects)
        if selected_count > 0:
            box.label(text=t("SELECTED_OBJECTS_COUNT", selected_count), icon='OBJECT_DATA')

            valid_components = sum(1 for obj in context.selected_objects
                                 if "face_normal" in obj and "x_axis" in obj and "y_axis" in obj)
            if valid_components > 0:
                box.label(text=t("TRANSLATABLE_COMPONENTS", valid_components), icon='CHECKMARK')
            else:
                box.label(text=t("NON_TRANSLATABLE_OBJECTS"), icon='CANCEL')
        else:
            box.label(text=t("NO_OBJECTS_SELECTED"), icon='INFO')

        box.label(text=t("PRECISE_TRANSLATION") + ":")
        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(translation_settings, "move_fx", text=t("MOVE_X"))
        row.prop(translation_settings, "move_fy", text=t("MOVE_Y"))
        row.prop(translation_settings, "move_fz", text=t("MOVE_Z"))

        op = col.operator("component_fixed.move_on_surface", text=t("APPLY_PRECISE_TRANSLATION"))
        op.move_x = translation_settings.move_fx
        op.move_y = translation_settings.move_fy
        op.move_z = translation_settings.move_fz

        box.label(text=t("QUICK_TRANSLATION") + ":")
        box.prop(translation_settings, "quick_step_fixed", text=t("QUICK_STEP"))
        grid = box.grid_flow(columns=3, align=True)

        op = grid.operator("component_fixed.quick_move", text=t("DIRECTION_LEFT"))
        op.direction = 'LEFT'
        op.step = translation_settings.quick_step_fixed

        op = grid.operator("component_fixed.quick_move", text=t("DIRECTION_RIGHT"))
        op.direction = 'RIGHT'
        op.step = translation_settings.quick_step_fixed

        op = grid.operator("component_fixed.quick_move", text=t("DIRECTION_FORWARD"))
        op.direction = 'FORWARD'
        op.step = translation_settings.quick_step_fixed

        op = grid.operator("component_fixed.quick_move", text=t("DIRECTION_BACKWARD"))
        op.direction = 'BACKWARD'
        op.step = translation_settings.quick_step_fixed

        op = grid.operator("component_fixed.quick_move", text=t("DIRECTION_UP"))
        op.direction = 'UP'
        op.step = translation_settings.quick_step_fixed

        op = grid.operator("component_fixed.quick_move", text=t("DIRECTION_DOWN"))
        op.direction = 'DOWN'
        op.step = translation_settings.quick_step_fixed

        # 批量平移：名称包含筛选文本的空物体，沿各自局部轴平移（独立步长）
        box.separator()
        box.label(text=t("BATCH_MOVE") + ":")
        box.prop(translation_settings, "batch_move_filter", text=t("BATCH_FILTER"))
        box.prop(translation_settings, "batch_move_direction", text=t("MOVE_DIRECTION"))
        box.prop(translation_settings, "batch_move_step", text=t("BATCH_STEP"))
        box.operator("component_fixed.batch_move", text=t("BATCH_MOVE"), icon='ARROW_LEFTRIGHT')

        # ========== 旋转工具（从平移工具独立成模块；编辑模式下也可用，绕组件自身局部轴） ==========
        layout.separator(factor=1.7)
        box = layout.box()
        box.label(text=t("ROTATE_TITLE"), icon='MOD_SCREW')

        box.prop(translation_settings, "rotate_step", text=t("ROTATE_STEP"))
        rgrid = box.grid_flow(columns=3, align=True)
        for axis_id, neg_label, pos_label in (('X', "X-", "X+"), ('Y', "Y-", "Y+"), ('Z', "Z-", "Z+")):
            op = rgrid.operator("component_fixed.quick_rotate", text=neg_label)
            op.axis = axis_id
            op.direction = 'NEG'
            op.step = translation_settings.rotate_step

            op = rgrid.operator("component_fixed.quick_rotate", text=pos_label)
            op.axis = axis_id
            op.direction = 'POS'
            op.step = translation_settings.rotate_step

        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(translation_settings, "rotate_fx", text="X")
        row.prop(translation_settings, "rotate_fy", text="Y")
        row.prop(translation_settings, "rotate_fz", text="Z")
        op = col.operator("component_fixed.rotate_local", text=t("APPLY_PRECISE_ROTATION"))
        op.rot_x = translation_settings.rotate_fx
        op.rot_y = translation_settings.rotate_fy
        op.rot_z = translation_settings.rotate_fz

        # 膨胀/收缩（倒数第三模块）：舰船整体缩放时硬点跟随
        layout.separator(factor=1.7)
        box = layout.box()
        box.label(text=t("INFLATE_TITLE"), icon='FULLSCREEN_ENTER')
        box.prop(settings, "inflate_scope", text=t("INFLATE_SCOPE"), expand=True)
        box.operator("component_fixed.inflate_parent", text=t("INFLATE_PARENT"), icon='LINKED')
        box.operator("component_fixed.inflate_apply", text=t("INFLATE_APPLY_MOVE"), icon='CHECKMARK').keep_scale = False
        box.operator("component_fixed.inflate_apply", text=t("INFLATE_APPLY_FULL"), icon='FULLSCREEN_ENTER').keep_scale = True

        # 预览实体化（显示模式上方）：按 x4_comp_size 生成同位线框长方体，不改动空物体
        layout.separator(factor=1.7)
        box = layout.box()
        box.label(text=t("PREVIEW_TITLE"), icon='MESH_CUBE')
        box.operator("component_fixed.preview_show", text=t("PREVIEW_SHOW"), icon='MESH_CUBE')
        box.operator("component_fixed.preview_hide", text=t("PREVIEW_HIDE"), icon='X')

        # 空物体显示模式（新放置默认采用；一键应用到存量硬点）
        layout.separator(factor=1.7)
        box = layout.box()
        box.label(text=t("DISPLAY_MODE"), icon='RESTRICT_VIEW_ON')
        box.prop(settings, "empty_display_mode", text="")
        box.operator("component_fixed.apply_display_mode", text=t("APPLY_DISPLAY_MODE"), icon='OUTLINER_OB_EMPTY')
        # 轴常显开关（存量硬点批量处理；新放置/镜像的硬点自动开启）
        box.operator("component_fixed.toggle_axis", text=t("TOGGLE_AXIS"), icon='AXIS_TOP')

        # 维护老硬点：按当前 预设/尺寸/朝向 设置补齐或更新自定义属性
        layout.separator(factor=1.7)
        box = layout.box()
        box.label(text=t("MAINTAIN_PROPS"), icon='PROPERTIES')
        box.operator("component_fixed.maintain_props", text=t("MAINTAIN_PROPS"), icon='PROPERTIES')
        # 筛选批量维护（v9.15.0，交互同批量平移：文本+执行，不依赖选中）
        row = box.row(align=True)
        row.prop(settings, "maintain_filter", text="")
        row.operator("component_fixed.maintain_props_filtered", text=t("MAINTAIN_FILTERED"), icon='FILTER')


class COMPONENT_FIXED_PT_waypoint_panel(bpy.types.Panel):
    """航路编辑器面板（第二个可折叠面板）"""
    bl_label = t("WP_PANEL_TITLE")
    bl_idname = "COMPONENT_FIXED_PT_waypoint_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "组件放置-修正"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.component_settings_fixed

        # ========== 面板级通用设置（导入命名/新建命名/应用组名/导出截断共用，不属于任何模块） ==========
        row = layout.row(align=True)
        row.label(text="", icon='LINKED')  # 连接符图标
        row.prop(settings, "wp_name_separator")
        layout.label(text=t("WP_SEP_NOTE"), icon='INFO')  # 备注：编辑器通用
        layout.separator(factor=1.7)

        # ========== 导入模块 ==========
        box = layout.box()
        box.label(text=t("WP_IMPORT"), icon='IMPORT')
        box.prop(settings, "wp_xml_path", text="")

        box.prop(settings, "wp_base_name")
        box.prop(settings, "wp_clear_existing")
        box.prop(settings, "wp_default_hp_type")

        box.separator()
        box.label(text=t("WP_S_SIZE") + ":")
        box.prop(settings, "wp_s_size")

        box.separator()
        box.label(text=t("WP_M_LENGTH") + "/" + t("WP_M_WIDTH") + "/" + t("WP_M_HEIGHT") + ":")
        row = box.row(align=True)
        row.prop(settings, "wp_m_length", text="L")
        row.prop(settings, "wp_m_width", text="W")
        row.prop(settings, "wp_m_height", text="H")

        box.separator()
        box.prop(settings, "wp_default_size")

        box.separator()
        box.operator("component_fixed.import_waypoints", text=t("WP_IMPORT"), icon='IMPORT')

        # ========== 新建命名（组名+连接符+航点名） ==========
        layout.separator(factor=1.7)  # 模块间隔加宽 70%
        box = layout.box()
        box.label(text=t("WP_SECTION_CREATE"), icon='EMPTY_AXIS')
        box.prop(settings, "wp_name_group")
        box.prop(settings, "wp_name_base")
        box.operator("component_fixed.add_waypoint", text=t("WP_ADD"), icon='EMPTY_AXIS')
        # 修改选中硬点的组名（重命名前缀，dockingbay 只改组不改名称其余部分和数量）
        box.operator("component_fixed.apply_wp_group", text=t("WP_APPLY_GROUP"), icon='GROUP')

        # ========== Waypoint 编辑（选中带 tags/hardpoint_type 属性的物体） ==========
        layout.separator(factor=1.7)
        obj = context.active_object
        if obj is not None and (obj.get("tags") is not None or obj.get("hardpoint_type") is not None):
            box = layout.box()
            box.label(text=t("WP_EDIT_TITLE"), icon='OUTLINER_OB_EMPTY')
            if "hardpoint_type" in obj:
                box.prop(obj, '["hardpoint_type"]', text=t("WP_HP_TYPE"))
            if "group" in obj:
                box.prop(obj, '["group"]', text=t("WP_GROUP"))
            if obj.get("tags") is not None:
                box.prop(obj, '["tags"]', text=t("WP_TAGS"))
                box.prop(obj, '["links"]', text=t("WP_LINKS"))
                row = box.row(align=True)
                row.operator("component_fixed.add_wp_links_from_selection",
                             text=t("WP_ADD_LINKS_FROM_SEL"), icon='LINKED')
                row.operator("component_fixed.rebuild_wp_constraints",
                             text=t("WP_REBUILD_CONSTRAINTS"), icon='CONSTRAINT')
                row.operator("component_fixed.clear_wp_links",
                             text=t("WP_CLEAR_LINKS"), icon='X')

                # 车道朝向（lane 属性控制导出四元数；清除=跟随物体实际旋转）
                row = box.row(align=True)
                row.label(text=t("WP_LANE") + ":")
                cur_lane = obj.get("lane")
                op = row.operator("component_fixed.set_wp_lane", text=t("WP_LANE_INBOUND"),
                                  depress=(cur_lane == "inbound"))
                op.lane = "inbound"
                op = row.operator("component_fixed.set_wp_lane", text=t("WP_LANE_OUTBOUND"),
                                  depress=(cur_lane == "outbound"))
                op.lane = "outbound"
                op = row.operator("component_fixed.set_wp_lane", text=t("WP_LANE_MANUAL"),
                                  depress=(cur_lane is None), icon='MOD_SCREW')
                op.lane = ""

                box.separator()
                current_tags = set(str(obj.get("tags", "")).split())
                for cat_key, tag_list in WP_TAG_CATEGORIES:
                    row = box.row(align=True)
                    row.label(text=t(cat_key) + ":")
                    for tg in tag_list:
                        op = row.operator("component_fixed.toggle_wp_tag", text=tg,
                                          depress=(tg in current_tags))
                        op.tag = tg
        else:
            box = layout.box()
            box.operator("component_fixed.make_waypoint", text=t("WP_MAKE_WAYPOINT"), icon='ADD')
            box.label(text=t("WP_NO_SELECTED_WAYPOINT"), icon='INFO')

        # ========== 导出模块 ==========
        layout.separator(factor=1.7)
        box = layout.box()
        box.label(text=t("WP_EXPORT"), icon='EXPORT')
        box.prop(settings, "wp_export_path", text="")
        box.operator("component_fixed.export_waypoints", text=t("WP_EXPORT"), icon='EXPORT')


# ============================ 硬点三色轴常显（GPU 覆盖层） ============================
# Blender 原生 show_axis 画的是屏幕等大的 gizmo，在舰船（数十米）尺度下小如针尖，等于不可见。
# 改用 gpu 绘制回调按每个硬点自身的 empty_display_size 画 RGB 局部轴（与 Egosoft 官方
# ego_Connection_customGeom 的连线绘制同一技术路线），任意模式、无需选中即可见。
try:
    import gpu
    from gpu_extras.batch import batch_for_shader
    _GPU_AVAILABLE = True
except ImportError:  # 后台模式等无 gpu 模块环境
    _GPU_AVAILABLE = False

_axis_draw_handler = None
_spacing_updating = False  # 标记 spacing 是否由程序自动设置（避免误触手动回调）

_AXIS_COLORS = (
    (0.95, 0.15, 0.15, 1.0),  # X 红
    (0.15, 0.90, 0.15, 1.0),  # Y 绿
    (0.20, 0.35, 0.95, 1.0),  # Z 蓝
)


def _hardpoint_axis_visible(obj):
    """空物体是否绘制三色轴（由自定义属性 x4_axis_show 标记，随 .blend 保存）"""
    return bool(obj.get("x4_axis_show", False))


def _draw_hardpoint_axes():
    """为所有标记的空物体在世界空间画三色局部轴（透视深度忽略=X光，舱体内也可见）"""
    try:
        coords_by_axis = ([], [], [])
        for obj in bpy.context.scene.objects:
            if obj.type != 'EMPTY' or not _hardpoint_axis_visible(obj):
                continue
            if obj.hide_get() or obj.hide_viewport:
                continue
            origin = obj.matrix_world.translation
            length = obj.empty_display_size
            matrix = obj.matrix_world.to_3x3()
            for i, axis_vec in enumerate(matrix.col):
                end = origin + axis_vec.normalized() * length
                coords_by_axis[i].extend((origin, end))

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('ALWAYS')
        gpu.state.line_width_set(2.0)
        for color, coords in zip(_AXIS_COLORS, coords_by_axis):
            if not coords:
                continue
            batch = batch_for_shader(shader, 'LINES', {"pos": coords})
            shader.bind()
            shader.uniform_float("color", color)
            batch.draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('NONE')
    except Exception:
        pass  # 绘制异常绝不能中断视口重绘


def _ensure_axis_draw_handler():
    global _axis_draw_handler
    if _GPU_AVAILABLE and _axis_draw_handler is None:
        _axis_draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            _draw_hardpoint_axes, (), 'WINDOW', 'POST_VIEW')


def _remove_axis_draw_handler():
    global _axis_draw_handler
    if _axis_draw_handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_axis_draw_handler, 'WINDOW')
        _axis_draw_handler = None


class COMPONENT_FIXED_PT_ship_mesh_panel(bpy.types.Panel):
    """整理舰船网格面板（第三个独立面板）"""
    bl_label = t("SHIP_MESH_ORGANIZE")
    bl_idname = "COMPONENT_FIXED_PT_ship_mesh_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "组件放置-修正"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.component_settings_fixed

        box = layout.box()
        box.prop(settings, "ship_mesh_prefix")
        box.prop(settings, "ship_mesh_mat_name")
        box.prop(settings, "ship_mesh_wreck_mat")

        box.separator()
        box.prop(settings, "ship_mesh_create_collision")
        box.prop(settings, "ship_mesh_lod_mode", text=t("SHIP_MESH_LOD_MODE"))
        if settings.ship_mesh_lod_mode == 'DECIMATE':
            box.prop(settings, "ship_mesh_lod1_ratio")
            box.prop(settings, "ship_mesh_lod2_ratio")
            box.prop(settings, "ship_mesh_lod3_ratio")

        box.separator()
        box.prop(settings, "ship_mesh_wreck_mode", text=t("SHIP_MESH_WRECK_MODE"))
        if settings.ship_mesh_wreck_mode == 'DECIMATE':
            box.prop(settings, "ship_mesh_wreck_ratio")

        box.operator("component_fixed.organize_ship_mesh", text=t("SHIP_MESH_ORGANIZE"), icon='MESH_DATA')


classes = (
    ComponentSettingsFixed,
    TranslationSettingsFixed,
    COMPONENT_OT_placer,
    COMPONENT_OT_move_on_surface,
    COMPONENT_OT_quick_move,
    COMPONENT_OT_batch_move,
    COMPONENT_OT_replace_names,
    COMPONENT_OT_mirror_x,
    COMPONENT_OT_rotate_local,
    COMPONENT_OT_quick_rotate,
    COMPONENT_OT_toggle_axis,
    COMPONENT_OT_apply_display_mode,
    COMPONENT_OT_inflate_parent,
    COMPONENT_OT_inflate_apply,
    COMPONENT_OT_preview_show,
    COMPONENT_OT_preview_hide,
    COMPONENT_OT_maintain_props,
    COMPONENT_OT_maintain_props_filtered,
    COMPONENT_OT_organize_scene,
    COMPONENT_OT_organize_ship_mesh,
    COMPONENT_OT_reset_spacing,
    COMPONENT_OT_apply_selected_name,
    COMPONENT_OT_import_waypoints,
    COMPONENT_OT_make_waypoint,
    COMPONENT_OT_toggle_waypoint_tag,
    COMPONENT_OT_set_wp_lane,
    COMPONENT_OT_add_waypoint,
    COMPONENT_OT_apply_wp_group,
    COMPONENT_OT_add_links_from_selection,
    COMPONENT_OT_clear_links,
    COMPONENT_OT_rebuild_wp_constraints,
    COMPONENT_OT_export_waypoints,
    COMPONENT_FIXED_PT_placer_panel,
    COMPONENT_FIXED_PT_waypoint_panel,
    COMPONENT_FIXED_PT_ship_mesh_panel
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.component_settings_fixed = bpy.props.PointerProperty(type=ComponentSettingsFixed)
    bpy.types.Scene.component_translation_fixed = bpy.props.PointerProperty(type=TranslationSettingsFixed)

    _ensure_axis_draw_handler()

def unregister():
    _remove_axis_draw_handler()

    for prop_name in ("component_settings_fixed", "component_translation_fixed"):
        try:
            delattr(bpy.types.Scene, prop_name)
        except AttributeError:
            pass

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
