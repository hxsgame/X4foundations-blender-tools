import os
import re
import argparse
import traceback
import xml.etree.ElementTree as ET
import shutil
from datetime import datetime

# 注意，命名规则 组-名称-序号，目前的函数，可以兼容没有 组 ，但是不能没有 序号~

# 生成的宏文件内容，不包括 舰船储存 ，这个由xml创建py实现添加了，不想改代码了~

# v2 修复清单：
#   1. 解析时保留源文件 XML 注释（原默认解析会静默丢弃全部注释）
#   2. 删除 clean_waypoints 中无效的"自闭合转换"步骤（ET 序列化空元素本就自动自闭合）
#   3. 备份轮换：每个文件只保留最新 BACKUP_KEEP 份，防止 backup 目录无限膨胀
#   4. 类型匹配先精确、再回落子串，避免 engine_s 抢中 engine_storage 类组合名
#   5. 镜像副本追加到原节点所在的 connections 分组，不再全部落进同一容器
#   6. 镜像重名检查改为大小写不敏感，避免已存在 Right 变体时重复创建
#   7. 删除死代码 get_connection_type；优先使用标准库 ET.indent（3.9+）
#   8. 增加 argparse：--dir / --dry-run / --no-backup
#   9. 新增连接布局规范化（第七步）：同名 connection 去重 + 硬点统一移到 part 结构
#      连接之后——对齐原版布局（头部 space/position/container → 结构 <parts> → 硬点），
#      修复导出器把硬点写进结构区、同名硬点重复写两类问题
#  10. 组件key（COMPONENT_KEY，默认 up7con_，对应 Blender 插件"组件key"栏）：
#      类型匹配前从类型段剥离；生成 7 类功能点 macro ref 前也剥离——否则宏名被污染
#      （up7con_bridge_bor_l_01_macro 死链，真实宏名是 bridge_bor_l_01_macro）

# 全局变量定义 - 移到函数外部，确保所有函数都能访问
type_mapping = {
    "player_control": "playercontrol",
    "playercontrol": "playercontrol",
    "cockpit": "cockpit cockpit_visible",
    "bridge": "cockpit cockpit_visible",
    "storage_p": "storage",
    "room": "window dynamicroom",
    "buildmodule": "buildmodule",
    "dock_": "dock_xs",
    "shipstorage": "dockingbay",
    "docking_bay": "dockingbay",
    "dockingbay": "dockingbay",
    "dockarea": "dockarea",
    "launchtube": "dockingbay",
    "cargo_tube": "cargotube",
    "cargotube": "cargotube",
    "countermeasures": "countermeasures",
    "shield_vro": "ishield shield unhittable ishield_dam_l_providence",
    "shield_exl": "extra extralarge shield hittable standard advanced",
    "shield_xl": "extralarge shield hittable component standard advanced",
    "shield_l": "large shield hittable standard advanced",
    "shield_m": "medium shield hittable standard advanced",
    "shield_s": "small shield standard advanced",
    "engine_exl": "extra engine extralarge standard advanced",
    "engine_xl": "engine extralarge standard advanced",
    "engine_l": "engine large standard advanced",
    "engine_m": "engine medium standard advanced",
    "engine_s": "engine small standard advanced",
    "turret_exl": "turret extra extralarge hittable standard combat advanced highpower missile boron",
    "turret_xl": "turret extralarge hittable standard combat advanced highpower missile boron",
    "turret_l_h": "turret large hittable standard combat advanced highpower missile boron",
    "turret_l_mh": "turret large hittable standard combat advanced highpower missile boron",
    "turret_l": "turret large hittable standard combat advanced highpower missile boron",
    "turret_m_h": "turret medium hittable standard combat advanced highpower missile boron",
    "turret_m_mh": "turret medium hittable standard combat advanced highpower missile boron",
    "turret_m": "turret medium hittable standard combat advanced highpower missile boron",
    "turret_s": "turret small hittable standard combat advanced highpower missile boron",
    "weapon_xl": "weapon extralarge standard combat advanced highpower missile boronhigh boron",
    "weapon_l": "weapon large standard combat advanced highpower missile boronhigh boron",
    "weapon_m": "weapon medium standard combat advanced highpower missile boronhigh boron",
    "weapon_s": "weapon small standard combat advanced highpower missile boronhigh boron",
    "waypoint": "typenormal sizeLevel1 dynamicmasstraffic",
    "waypoint_s": "sizeLevel1 typedock start dynamicmasstraffic",
    "waypoint_e": "sizeLevel1 typedock end dynamicmasstraffic",
    "waypoint_c": "typenormal sizeLevel1 dynamicmasstraffic closelink",
}

# 镜像标签配置 - 移到模块级别作用域
mirror_label = 'left'
mirror_pairing = 'right'

# 组件key：Blender 放置插件为硬点名称插入的识别标记（默认 "up7con_"，与插件"组件key"栏对应）。
# 处理时仅从"类型段"中剥离该标记再做 tags 匹配——组名段（如 left_con_g_01）
# 与镜像判定均使用原始名称，不受影响。设为空串可停用剥离。改 key 时需与插件端保持一致。
COMPONENT_KEY = "up7con_"

# 统一镜像变换：基于 X4 原版舰船实测
# 镜像平面 = x=0（对称中心实测精确为 0），反射公式：位置 (x→-x, y/z 不变)，四元数 (qx,-qy,-qz,qw)
# 所有硬点的左右对称都是这一个统一变换，与顶/底/前/后无关（原版 ship_atf_xl_battleship_01 实证）

# 跳过四元数镜像标签：若 connection 的 name 或 group 包含此标签，镜像时只翻转位置 x、
# 保留原四元数不变。用于"人工摆放朝向"的连接（如部分左右朝向不同的护盾），避免镜像改坏朝向。
SKIP_QUAT_TAG = "_nosym"

# 新增组名标签检测变量
no_group_tag = '_nog'  # 如果组名包含此标签，则不创建group属性
one_group_tag = '_oneg'  # 如果组名包含此标签，则镜像组名使用与原节点相同的组名

# 新增：需要添加新连接的节点名称关键字
# 匹配顺序按列表顺序；注意关键字应放在能被更长名字匹配的合适位置
target_connection_keywords = ['cockpit', 'bridge', 'buildmodule', 'dock_', 'shipstorage', 'storage_p', 'storage', 'dockarea', 'launchtube']

# 新增：连接类型映射字典
connection_type_mapping = {
    'cockpit': 'con_cockpit',
    'bridge': 'con_cockpit',
    'buildmodule': 'object',
    'dock_': 'Connection_component',
    'shipstorage': 'object',
    'storage_p': 'ShipConnection',
    'storage': 'ShipConnection',
    'dockarea': 'Connection01',
    'launchtube': 'Connection02'
}

# 在函数外部定义可自定义的变量
# clean_waypoints(root, group_keywords=None, group_separator=None, tag_replace_old=None, tag_replace_new=None):
GROUP_KEYWORDS = ["dock"]  # 用于检测group的关键词列表
GROUP_SEPARATOR = "-"  # 用于分割name的分隔符
TAG_REPLACE_OLD = "speed_normal"  # 要替换的旧tags字符
TAG_REPLACE_NEW = "speed_fast"  # 替换后的新tags字符

# source geometry 路径修复配置：
# 检测所有 <source geometry="..."/> 的 geometry 值，若以 SOURCE_BAD_PREFIX 开头，
# 则去掉该前缀。用于修复工具导出时多出的 "X4\" 前缀。
SOURCE_BAD_PREFIX = "X4\\"          # 需要被去除的错误前缀（含反斜杠）
SOURCE_TARGET_START = "extensions\\" # 修复后的正确起始（用于确认）

BACKUP_KEEP = 20  # 每个 XML 文件最多保留的备份份数，超出后自动删除最旧的

# 连接布局规范化配置（原版实证：ship_arg_l_destroyer_01 等）：
# 头部连接固定排在 part 结构连接之前，其余硬点/macro 引用连接全部排在结构之后
HEADER_CONN_NAMES = {'space', 'position', 'container'}

def find_connection_by_name_ci(root, name):
    """按名称查找 connection（大小写不敏感）。返回第一个匹配元素或 None"""
    target = name.lower()
    for conn in root.findall('.//connection'):
        if conn.get('name', '').lower() == target:
            return conn
    return None


def extract_prolog_comments(file_path):
    """提取根元素之前的注释（prolog 注释，如文件头说明）。

    ET 的 insert_comments 无法承载根外注释，只能在解析前从原始文本手工抽取，
    写回时再拼接回去。返回注释文本列表（含 <!-- --> 定界符）。
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError:
        return []
    # 第一个元素标签起始位置（<?xml 声明的 <? 不会被 <\w 匹配到）
    m = re.search(r'<\w', text)
    if not m:
        return []
    prolog = text[:m.start()]
    return re.findall(r'<!--.*?-->', prolog, re.DOTALL)


def normalize_connection_order(root):
    """规范化 connections 容器布局（第七步），对齐原版规则：
    头部连接(space/position/container) → part 结构连接(含 <parts>) → 全部硬点/macro 引用。

    1) 同名 connection 去重：保留信息更完整的一份（有 offset/quaternion 优先，同级保留首个）
    2) 重排：硬点统一移到结构连接之后，三区内各自保持原有相对顺序；
       注释节点归入硬点区（多为 macro 引用区的说明注释）
    无 <parts> 结构连接的容器（如纯硬点容器）跳过，不误排。
    返回 (去重数, 移动数)"""
    dedup_count = 0
    move_count = 0

    def is_structure(el):
        return el.tag is not ET.Comment and el.find('parts') is not None

    for container in root.findall('.//connections'):
        children = list(container)
        if len(children) < 2 or not any(is_structure(el) for el in children):
            continue

        # 1) 同名去重
        winners = {}
        for el in children:
            if el.tag is ET.Comment:
                continue
            name = el.get('name')
            if not name:
                continue
            score = (el.find('offset') is not None) + (el.find('.//quaternion') is not None)
            if name not in winners or score > winners[name][0]:
                winners[name] = (score, el)
        keep_ids = {id(w[1]) for w in winners.values()}
        for el in children:
            if el.tag is ET.Comment:
                continue
            name = el.get('name')
            if name and id(el) not in keep_ids:
                container.remove(el)
                dedup_count += 1
                print(f"去重: 删除重复 connection '{name}'")

        # 2) 重排为 头部 → 结构 → 硬点
        children = list(container)
        headers, structs, hardpoints = [], [], []
        for el in children:
            if el.tag is ET.Comment:
                hardpoints.append(el)
            elif is_structure(el):
                structs.append(el)
            elif el.get('name', '') in HEADER_CONN_NAMES:
                headers.append(el)
            else:
                hardpoints.append(el)
        new_order = headers + structs + hardpoints
        if [id(e) for e in new_order] != [id(e) for e in children]:
            for el in new_order:
                container.remove(el)
            for el in new_order:
                container.append(el)
            move_count += 1
            print(f"重排: {len(headers)} 头部 + {len(structs)} 结构前置，"
                  f"{len(hardpoints)} 个硬点/引用统一后置")

    return dedup_count, move_count


def fix_source_geometry(root):
    """检测并修复 <source geometry="..."> 的路径前缀。

    仅当 geometry 值以 SOURCE_BAD_PREFIX 开头时才去除该前缀；
    不硬删、不无差别修改。返回修复数量。
    """
    fixed_count = 0
    for source in root.findall('.//source'):
        geom = source.get('geometry', '')
        if geom.startswith(SOURCE_BAD_PREFIX):
            new_geom = geom[len(SOURCE_BAD_PREFIX):]
            source.set('geometry', new_geom)
            fixed_count += 1
            print(f"修复source geometry: '{geom}' -> '{new_geom}'")
    return fixed_count


def process_xml_file(file_path, dry_run=False, no_backup=False):
    """处理XML文件，修改connection节点的tags和group，并创建镜像节点

    dry_run: 只解析和计算修改，不备份不写回
    no_backup: 处理时跳过备份
    """
    try:
        # 根外注释（prolog）需在解析前从原始文本手工抽取（ET 模型无法承载）
        prolog_comments = extract_prolog_comments(file_path)

        if not dry_run and not no_backup:
            backup_file(file_path)

        # insert_comments=True 保留根内注释
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = ET.parse(file_path, parser=parser)
        root = tree.getroot()

        modified_count = 0
        mirror_count = 0
        left_connections = []

        # 查找所有connection节点
        connections = root.findall('.//connection')
        print(f"找到 {len(connections)} 个connection节点")

        # 第一步：修改现有connection的tags和group
        # 先精确匹配类型段，再回落子串匹配（按 key 长度降序），
        # 避免 engine_s 抢中 engine_storage 类组合名
        sorted_keys = sorted(type_mapping.keys(), key=len, reverse=True)
        unmatched = []

        for conn in connections:
            name = conn.get('name', '')
            if not name:
                continue

            group, conn_type = parse_connection_name(name)

            # 剥离组件key（如 _con_）：key 仅标识"本工具放置的硬点"，不参与类型匹配。
            # 只处理类型段——组名段 left_con_g_01 本身含 _con_，全名剥离会破坏 group
            if COMPONENT_KEY and COMPONENT_KEY in conn_type:
                conn_type = conn_type.replace(COMPONENT_KEY, "")

            if conn_type in type_mapping:
                matched_key = conn_type
            else:
                matched_key = None
                for key in sorted_keys:
                    if key in conn_type:  # 检查key是否包含在conn_type中
                        matched_key = key
                        break

            if matched_key:
                tags = type_mapping[matched_key]
                # 规则：没有分组的护盾去掉 tags 中的 hittable
                # 没有分组 = 解析出的组名为空，或组名含 _nog（最终会被删除 group 属性）
                if matched_key.startswith('shield') and 'hittable' in tags:
                    has_group = bool(group) and no_group_tag not in group
                    if not has_group:
                        tags = re.sub(r'\s*hittable\s*', ' ', tags).strip()
                        print(f"  无分组护盾去掉 hittable: {name}")
                conn.set('tags', tags)
                if group and not conn.get('group'):
                    conn.set('group', group)
                modified_count += 1
                print(f"修改节点: {name} -> {tags}")
            else:
                unmatched.append(name)

            # 记录左侧节点
            if mirror_label in name.lower():
                left_connections.append(conn)

        if unmatched:
            print(f"警告: {len(unmatched)} 个节点未匹配到类型，未设置tags: {unmatched}")

        # 第二步：创建镜像副本
        # 每个镜像追加到原节点所在的 connections 分组（id→容器映射，兼容多个分组）；
        # 查重按大小写不敏感，避免已存在 Right 变体时重复创建
        if left_connections:
            parent_map = {}
            for conn_group in root.findall('.//connections'):
                for child in conn_group.findall('connection'):
                    parent_map[id(child)] = conn_group

            for left_conn in left_connections:
                # 镜像名：与 create_mirror_connection 内部一致，忽略大小写
                mirror_name = re.sub(
                    mirror_label, mirror_pairing, left_conn.get('name', ''), flags=re.IGNORECASE
                )
                if find_connection_by_name_ci(root, mirror_name) is not None:
                    continue

                mirror_conn = create_mirror_connection(left_conn)
                parent = parent_map.get(id(left_conn))
                if parent is None:
                    parent = root.find('.//connections')
                if parent is not None:
                    parent.append(mirror_conn)
                    mirror_count += 1
                    print(f"创建镜像: {left_conn.get('name')} -> {mirror_name}")

        # 第三步：删除所有包含_nog标签的原节点的group属性
        nog_nodes = []
        for conn in connections:
            group = conn.get('group', '')
            if group and no_group_tag in group:
                nog_nodes.append(conn)

        for conn in nog_nodes:
            if 'group' in conn.attrib:
                del conn.attrib['group']
                print(f"删除原节点 {conn.get('name', '')} 的group属性")

        # 第四步：添加新的connections节点（新增功能）
        file_base = os.path.splitext(os.path.basename(file_path))[0]  # 文件名不含扩展名
        add_new_connections(root, connections, file_base)

        # 第五步：清理waypoints标签（新增功能）
        clean_waypoints(root)  # ← 添加这行

        # 第六步：修复source geometry路径前缀（去除多出的 "X4\"）
        src_fixed = fix_source_geometry(root)
        if src_fixed:
            print(f"修复 {src_fixed} 个 source geometry 路径")

        # 第七步：规范化连接布局（同名去重 + 硬点统一移到 part 结构之后，对齐原版）
        dedup_n, move_n = normalize_connection_order(root)
        if dedup_n or move_n:
            print(f"布局规范化: 去重 {dedup_n} 个同名连接, 重排 {move_n} 个容器")

        # 格式化并保存XML（优先标准库 ET.indent，3.9+；旧版回落自定义实现）
        try:
            ET.indent(tree)
        except AttributeError:
            indent_xml(root)
        tree.write(file_path, encoding='utf-8', xml_declaration=True)

        # 拼回根外注释（prolog）：紧跟 XML 声明之后
        if prolog_comments:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            decl_end = content.find('?>')
            if decl_end != -1:
                decl_end += 2
                new_content = (content[:decl_end] + '\n'
                               + '\n'.join(prolog_comments) + '\n'
                               + content[decl_end:].lstrip('\r\n'))
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)

        if dry_run:
            print(f"[DRY-RUN] 处理完成（未备份、未写回）: {file_path}")
        else:
            print(f"处理完成: {file_path}")
        print(f"修改节点: {modified_count}, 镜像节点: {mirror_count}")
        return True

    except Exception as e:
        print(f"处理错误: {e}")
        traceback.print_exc()
        return False


def add_new_connections(root, connections, file_base=""):
    """添加新的connections节点（带去重，避免重复运行累积）

    正确位置：新连接追加到 ship component 内部的 <connections> 容器末尾，
    而不是追加到根 <components> 之下（否则会落到 </component> 外部，结构错误）。

    file_base: 当前处理的 XML 文件名（不含扩展名），用于 storage 类连接的 macro ref 命名。
    """
    # 找到 ship component（含 name 且非空，即主体）内的第一个 connections 容器
    target_conn = None
    for comp in root.findall('.//component'):
        conn_cont = comp.find('connections')
        if conn_cont is not None:
            target_conn = conn_cont
            break
    # 兜底：找不到则用根下的第一个 connections
    if target_conn is None:
        target_conn = root.find('.//connections')
    if target_conn is None:
        print("警告: 未找到 connections 容器，跳过新增连接节点")
        return

    # 收集所有已存在的"连接宏引用"（ref 属性）用于去重。
    # 注意：原始硬点用 name 属性，连接宏用 ref 属性，两者不同。
    # 这里只收集 ref —— 即已有连接宏的引用名，避免把原始硬点自身的 name 误判为"已存在"而漏生成。
    existing_refs = set()
    for conn in root.findall('.//connection'):
        r = conn.get('ref')
        if r:
            existing_refs.add(r)

    # 计数器，用于跟踪已处理的节点数量
    processed_count = 0
    skipped_count = 0

    # 本次新增的连接节点
    new_conns = []

    # 遍历所有connection节点，查找匹配的节点
    for conn in connections:
        name = conn.get('name', '')
        if not name:
            continue

        # 检查节点名称是否包含目标关键字
        for keyword in target_connection_keywords:
            if keyword in name:
                # 获取连接类型
                conn_type = connection_type_mapping.get(keyword, 'object')

                # 按"-"切分name，只保留包含指定关键字的部分
                base_name = extract_keyword_part(name, target_connection_keywords)

                # 剥离组件key：macro ref 必须指向游戏中的真实宏名（如 bridge_bor_l_01_macro）。
                # key（如 up7con_）与关键字在同一整段内（up7con_bridge_bor_l_01），
                # extract_keyword_part 不会拆开，不剥离会生成错误宏名（up7con_bridge_..._macro 死链）。
                # connection 的 ref 属性保持完整名称不动（它按 name 精确引用硬点）。
                if COMPONENT_KEY and COMPONENT_KEY in base_name:
                    base_name = base_name.replace(COMPONENT_KEY, "")

                # 若该 ref/name 已存在，跳过，避免重复
                if name in existing_refs:
                    skipped_count += 1
                    break

                # 创建新的connection节点
                new_conn = ET.Element('connection')
                new_conn.set('ref', f"{name}")
                existing_refs.add(name)

                # 创建macro子节点
                macro = ET.Element('macro')

                if keyword in ('storage_p', 'storage') and file_base:
                    # storage 类连接：macro ref 基于文件名生成。
                    # 规则：把文件名开头的 "ship" 改为 "storage"，其余不变，末尾加 "_macro"。
                    # 例: ship_bor_l_megathron.xml -> storage_bor_l_megathron_macro
                    base = file_base
                    if base.lower().startswith('ship'):
                        base = 'storage' + base[4:]
                    macro_ref = f"{base}_macro"
                else:
                    # 其它类型：从节点名提取关键字部分 + _macro
                    if base_name.endswith('_macro'):
                        macro_ref = base_name
                    else:
                        macro_ref = f"{base_name}_macro"

                macro.set('ref', macro_ref)
                macro.set('connection', conn_type)

                new_conn.append(macro)
                new_conns.append(new_conn)
                processed_count += 1
                print(f"为新连接创建节点: {name} -> {conn_type}, macro ref: {macro_ref}")
                break  # 找到一个匹配的关键字就跳出循环

    # 仅当确有新节点时才追加，避免空容器累积
    if new_conns:
        # 注释放在 connections 容器内开头，新连接追加到容器末尾
        comment_elem = ET.Comment(" New connections for specific node types ")
        target_conn.append(comment_elem)
        for new_conn in new_conns:
            target_conn.append(new_conn)

    print(f"已添加 {processed_count} 个新连接节点, 跳过 {skipped_count} 个重复节点")


def extract_keyword_part(name, keywords):
    """
    按"-"切分name，只保留包含指定关键字的部分
    使用字符串分割方法，将name按分隔符拆分后筛选出包含关键字的片段[3,7](@ref)
    """
    if not name or '-' not in name:
        return name

    # 使用split方法按"-"分割字符串[1,4](@ref)
    parts = name.split('-')

    # 筛选包含关键字的片段
    keyword_parts = []
    for part in parts:
        for keyword in keywords:
            if keyword in part:
                keyword_parts.append(part)
                break  # 找到一个匹配的关键字就跳出内层循环

    # 如果没有找到包含关键字的片段，返回原始名称
    if not keyword_parts:
        return name

    # 将包含关键字的片段用"-"重新连接[5](@ref)
    return '-'.join(keyword_parts)


def backup_file(file_path):
    """备份XML文件，并按 BACKUP_KEEP 轮换清理最旧的备份，防止目录无限膨胀"""
    backup_dir = os.path.join(os.path.dirname(file_path), "backup")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.basename(file_path)
    stem, ext = os.path.splitext(filename)
    backup_filename = f"{stem}_{timestamp}{ext}"
    backup_path = os.path.join(backup_dir, backup_filename)

    shutil.copy2(file_path, backup_path)
    print(f"已备份文件到: {backup_path}")

    # 轮换：仅保留该文件最新的 BACKUP_KEEP 份备份
    backups = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir)
               if f.startswith(stem + "_") and f.endswith(ext)]
    backups.sort(key=os.path.getmtime, reverse=True)
    for old in backups[BACKUP_KEEP:]:
        try:
            os.remove(old)
            print(f"清理旧备份: {os.path.basename(old)}")
        except OSError:
            pass

    return backup_path


def mirror_transform(position, quaternion, mirror_quat=True):
    """
    统一镜像变换（基于 X4 原版舰船实测确认的反射公式）。

    镜像平面 = x=0，关于该平面的反射（det=-1，手性反转）：
        位置：  (x, y, z)  ->  (-x, y, z)
        四元数： (qx,qy,qz,qw) -> (qx, -qy, -qz, qw)

    该变换对任意位置的硬点（顶/底/前/后/侧）统一成立，不需要按位置区分算法。

    参数:
        position:    原始位置 [x, y, z]
        quaternion:  原始四元数 [qx, qy, qz, qw]
        mirror_quat: True=同时镜像位置和四元数；False=只镜像位置、保留原四元数
                     （用于人工朝向的连接，如 SKIP_QUAT_TAG 标记的节点）
    """
    x, y, z = position
    mirrored_pos = [-x, y, z]

    if mirror_quat:
        qx, qy, qz, qw = quaternion
        mirrored_quat = [qx, -qy, -qz, qw]  # X4 标准镜像
    else:
        mirrored_quat = list(quaternion)     # 保留原朝向

    return mirrored_pos, mirrored_quat


def format_float(value):
    """格式化浮点数，保留精度"""
    if abs(value) < 1e-12:
        return "0"
    elif abs(value) < 0.001 or abs(value) > 10000:
        return f"{value:.12g}"
    else:
        formatted = f"{value:.10f}"
        if '.' in formatted:
            formatted = formatted.rstrip('0').rstrip('.')
            if not formatted.replace('.', '').replace('-', ''):
                formatted = "0"
        return formatted


def parse_connection_name(name):
    """解析connection名称"""
    if not name or '-' not in name:
        return None, name

    parts = name.split('-')
    if len(parts) >= 3 and parts[-1].isdigit():
        # 组-名称-序号
        return parts[0], '-'.join(parts[1:-1])
    elif len(parts) == 2:
        # 名称-序号（无组名），首段即类型
        return None, parts[0]
    else:
        # 其他多段（如无序号或末位非数字），去掉首段后视为类型
        return None, '-'.join(parts[1:-1])


def extract_transform_data(connection):
    """提取变换数据"""
    offset = connection.find('offset')
    if offset is None:
        return None, None

    pos_elem = offset.find('position')
    if pos_elem is None:
        return None, None

    try:
        # 提取位置
        x = float(pos_elem.get('x', 0))
        y = float(pos_elem.get('y', 0))
        z = float(pos_elem.get('z', 0))
        position = [x, y, z]

        # 提取旋转
        quat_elem = offset.find('quaternion')
        if quat_elem is not None:
            qx = float(quat_elem.get('qx', 0))
            qy = float(quat_elem.get('qy', 0))
            qz = float(quat_elem.get('qz', 0))
            qw = float(quat_elem.get('qw', 1))
            quaternion = [qx, qy, qz, qw]
        else:
            quaternion = [0, 0, 0, 1]

        return position, quaternion
    except (ValueError, TypeError) as e:
        print(f"提取变换数据错误: {e}")
        return None, None


def create_mirror_connection(original_conn):
    """创建镜像connection"""
    mirror_conn = ET.Element('connection')

    # 复制属性
    for attr, value in original_conn.attrib.items():
        mirror_conn.set(attr, value)

    # 修改名称和组名
    name = original_conn.get('name', '')
    if mirror_label in name.lower():
        # 使用忽略大小写的正则替换，避免 GUN-LEFT 这类大写名漏改
        mirror_name = re.sub(mirror_label, mirror_pairing, name, flags=re.IGNORECASE)
        mirror_conn.set('name', mirror_name)

    # 获取原始组名
    original_group = original_conn.get('group', '')

    # 优化组名命名逻辑
    if original_group:
        # 检测组名是否包含_nog标签
        if no_group_tag in original_group:
            # 不创建group属性（删除已复制的group属性）
            if 'group' in mirror_conn.attrib:
                del mirror_conn.attrib['group']
            print(f"检测到组名标签 '{no_group_tag}'，镜像节点不创建group属性")

        # 检测组名是否包含_oneg标签
        elif one_group_tag in original_group:
            # 镜像组名使用与原节点相同的组名
            mirror_conn.set('group', original_group)
            print(f"检测到组名标签 '{one_group_tag}'，镜像节点使用相同组名: {original_group}")

        # 其他情况：按原逻辑处理（将left替换为right，忽略大小写）
        elif mirror_label in original_group.lower():
            mirror_group = re.sub(mirror_label, mirror_pairing, original_group, flags=re.IGNORECASE)
            mirror_conn.set('group', mirror_group)

    # 处理变换数据
    position, quaternion = extract_transform_data(original_conn)
    if position and quaternion:
        # 检测是否跳过四元数镜像（人工朝向的连接，如 SKIP_QUAT_TAG 标记的护盾）
        mirror_quat = True
        conn_name = original_conn.get('name', '')
        conn_group = original_conn.get('group', '')
        if SKIP_QUAT_TAG in conn_name.lower() or SKIP_QUAT_TAG in conn_group.lower():
            mirror_quat = False
            print(f"检测到跳过四元数标签 '{SKIP_QUAT_TAG}'，镜像时保留原朝向")

        # 使用统一反射变换（X4 标准镜像）
        mirrored_pos, mirrored_quat = mirror_transform(position, quaternion, mirror_quat)
        # 创建offset元素
        offset = ET.Element('offset')

        # 创建position元素
        pos_elem = ET.Element('position')
        pos_elem.set('x', format_float(mirrored_pos[0]))
        pos_elem.set('y', format_float(mirrored_pos[1]))
        pos_elem.set('z', format_float(mirrored_pos[2]))
        offset.append(pos_elem)

        # 创建quaternion元素
        quat_elem = ET.Element('quaternion')
        quat_elem.set('qx', format_float(mirrored_quat[0]))
        quat_elem.set('qy', format_float(mirrored_quat[1]))
        quat_elem.set('qz', format_float(mirrored_quat[2]))
        quat_elem.set('qw', format_float(mirrored_quat[3]))
        offset.append(quat_elem)

        mirror_conn.append(offset)

    else:
        # 原节点无 offset/position 时，镜像节点将缺少变换数据，需明确提示
        print(f"警告: 镜像节点 {mirror_conn.get('name', '')} 缺少 offset 变换数据（原节点无 position）")

    return mirror_conn


def indent_xml(elem, level=0):
    """递归添加缩进"""
    indent = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = indent
        for child in elem:
            indent_xml(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = indent
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = indent


def clean_waypoints(root, group_keywords=None, group_separator=None, tag_replace_old=None, tag_replace_new=None):
    """
    清理waypoints标签：删除空链接、空links节点，并优化waypoint标签格式
    同时为包含特定关键词的waypoint添加group属性，并替换tags中的特定字符

    Args:
        root: XML根节点
        group_keywords: 用于检测group的关键词列表，如果为None则使用全局GROUP_KEYWORDS
        group_separator: 用于分割name的分隔符，如果为None则使用全局GROUP_SEPARATOR
        tag_replace_old: 要替换的旧tags字符，如果为None则使用全局TAG_REPLACE_OLD
        tag_replace_new: 替换后的新tags字符，如果为None则使用全局TAG_REPLACE_NEW
    """

    # 使用参数或全局变量
    if group_keywords is None:
        group_keywords = GROUP_KEYWORDS
    if group_separator is None:
        group_separator = GROUP_SEPARATOR
    if tag_replace_old is None:
        tag_replace_old = TAG_REPLACE_OLD
    if tag_replace_new is None:
        tag_replace_new = TAG_REPLACE_NEW

    # 查找waypoints节点
    waypoints_elem = root.find('.//waypoints')
    if waypoints_elem is None:
        print("未找到waypoints节点")
        return

    waypoint_count = 0
    empty_link_removed = 0
    empty_links_removed = 0
    group_added = 0
    tags_replaced = 0

    # 查找所有waypoint子节点
    waypoints = waypoints_elem.findall('waypoint')
    print(f"找到 {len(waypoints)} 个waypoint节点")

    for waypoint in waypoints:
        waypoint_count += 1
        waypoint_name = waypoint.get('name', '未知')

        # 1. 处理链接和links节点
        links_elem = waypoint.find('links')
        if links_elem is not None:
            # 查找所有link子节点
            link_elems = links_elem.findall('link')
            empty_links = []
            valid_links = []

            # 筛选空链接和非空链接
            for link in link_elems:
                ref_value = link.get('ref', '')
                if ref_value == '':
                    empty_links.append(link)
                else:
                    valid_links.append(link)

            # 删除空链接
            for empty_link in empty_links:
                links_elem.remove(empty_link)
                empty_link_removed += 1
                print(f"删除空链接: {waypoint_name}")

            # 如果links节点下没有有效链接，删除整个links节点
            if len(valid_links) == 0:
                waypoint.remove(links_elem)
                empty_links_removed += 1
                print(f"删除空links节点: {waypoint_name}")

        # 2. 检查是否需要添加group属性
        should_process = False
        for keyword in group_keywords:
            if keyword in waypoint_name:
                should_process = True
                break

        if should_process:
            # 2.1 添加group属性
            name_parts = waypoint_name.split(group_separator)
            if name_parts:
                group_value = name_parts[0]

                # 如果group_value以分隔符结尾，去掉分隔符
                if group_value.endswith(group_separator):
                    group_value = group_value[:-1]

                # 添加或更新group属性
                waypoint.set('group', group_value)
                group_added += 1
                print(f"添加group属性 '{group_value}' 到: {waypoint_name}")

            # 2.2 替换tags属性中的特定字符
            tags_value = waypoint.get('tags', '')
            if tag_replace_old in tags_value:
                new_tags = tags_value.replace(tag_replace_old, tag_replace_new)
                waypoint.set('tags', new_tags)
                tags_replaced += 1
                print(f"替换tags: '{tags_value}' -> '{new_tags}' 在: {waypoint_name}")

        # （原第3步"转换为自闭合标签"已删除：ET 序列化空元素本就自动输出自闭合，该步骤无效）

    print(f"清理完成: 处理{waypoint_count}个waypoint, 删除{empty_link_removed}个空link, "
          f"删除{empty_links_removed}个空links节点, "
          f"添加{group_added}个group属性, 替换{tags_replaced}个tags属性")


def main():
    """主函数"""
    ap = argparse.ArgumentParser(description="X4 舰船 XML 连接补全工具（v2）")
    ap.add_argument('--dir', default=os.path.dirname(os.path.abspath(__file__)),
                    help='要处理的目录（默认为脚本所在目录）')
    ap.add_argument('--dry-run', action='store_true',
                    help='只解析和计算修改，不备份不写回')
    ap.add_argument('--no-backup', action='store_true',
                    help='处理时不创建备份')
    args = ap.parse_args()

    current_dir = args.dir
    if not os.path.isdir(current_dir):
        print(f"目录不存在: {current_dir}")
        return

    xml_files = [f for f in os.listdir(current_dir)
                 if f.lower().endswith('.xml') and os.path.isfile(os.path.join(current_dir, f))]

    if not xml_files:
        print("未找到XML文件")
        return

    print(f"处理 {len(xml_files)} 个XML文件" + ("（DRY-RUN 预览模式）" if args.dry_run else ""))
    print(f"组名控制标签: {no_group_tag} (不创建group属性), {one_group_tag} (使用相同组名)")
    print(f"跳过四元数镜像标签: {SKIP_QUAT_TAG} (name/group含此标签的连接只镜像位置、保留原朝向)")
    print(f"目标连接关键字: {target_connection_keywords}")
    print("镜像变换: 统一反射 (x→-x, 四元数 qx不变/-qy/-qz/qw不变)，基于X4原版舰船实测")

    failed = []
    for xml_file in xml_files:
        file_path = os.path.join(current_dir, xml_file)
        print(f"\n处理: {xml_file}")
        if not process_xml_file(file_path, dry_run=args.dry_run, no_backup=args.no_backup):
            failed.append(xml_file)

    if failed:
        print(f"\n以下文件处理失败: {failed}")
    else:
        print(f"\n全部 {len(xml_files)} 个文件处理完成")


if __name__ == "__main__":
    main()