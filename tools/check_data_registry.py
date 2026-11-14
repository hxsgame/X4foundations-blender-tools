"""数据源与引用注册表闭环校验。

用法：
    python tools/check_data_registry.py

检查规则（任何未登记的项都会使脚本以退出码 1 结束）：
  1. 插件内 `bpy.types.PropertyGroup` 子类的所有 `bpy.props.*Property` 字段。
  2. 物体自定义属性的字符串键（obj["..."] / obj.get("...")）。
  3. 两个工具文件的顶层 / 类级全大写常量。
  4. 所有 `xxx.get("键")` / `xxx.set("键", ...)` 的字符串键（含 XML 属性与配置键）。
  5. 匿名场景属性（context.scene["..."] 等）必须为 0 处。

「已登记」的判定：该标识符以反引号形式（`token`）出现在
`数据源与引用注册表.md` 中。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "数据源与引用注册表.md"
PLUGIN = ROOT / "X4_blender42_add_components_fixed_addon.py"
XMLTOOL = ROOT / "X4_BlenToolOut_con_ImproveTool_easy_fixed.py"

# 不视为数据源的通用访问器键（Python 内置 / UI 布局等）
IGNORED_KEYS = {"items", "name", "description", "default", "type", "poll"}


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def registry_tokens(text: str) -> set:
    """注册表中所有以反引号包裹的标识符。"""
    return set(re.findall(r"`([^`\s]+)`", text))


def property_group_fields(text: str):
    """收集 PropertyGroup 子类中的属性字段名 -> 行号列表。"""
    fields = {}
    in_group = False
    indent = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if re.match(r"^class\s+\w+\(bpy\.types\.PropertyGroup\)", stripped):
            in_group = True
            indent = len(line) - len(line.lstrip())
            continue
        if in_group:
            current = len(line) - len(line.lstrip())
            if stripped and current <= indent and stripped.startswith("class "):
                in_group = False
            elif re.match(r"^\s{4}\w+:\s*bpy\.props\.\w+Property", line):
                name = re.match(r"^\s{4}(\w+):", line).group(1)
                fields.setdefault(name, []).append(lineno)
    return fields


def custom_property_keys(text: str):
    """物体自定义属性键（限定常见物体变量名，避免误抓字典变量）。"""
    var = r"(?:obj|o|origin|new_obj|preview|source_obj|original_obj|empty_obj)"
    keys = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in re.finditer(var + r'\[["\']([A-Za-z_]\w*)["\']\]', line):
            keys.setdefault(m.group(1), []).append(lineno)
        for m in re.finditer(var + r'\.get\(["\']([A-Za-z_]\w*)["\']', line):
            keys.setdefault(m.group(1), []).append(lineno)
    return keys


def top_level_constants(text: str):
    """顶层与类级全大写常量名。"""
    consts = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^\s{0,4}([A-Z_][A-Z0-9_]{2,})\s*=", line)
        if m:
            consts.setdefault(m.group(1), []).append(lineno)
    return consts


def accessor_keys(text: str):
    """所有 .get("键") / .set("键", ...) 的字符串键。"""
    keys = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in re.finditer(r'\.(?:get|set)\(["\']([A-Za-z_]\w*)["\']', line):
            keys.setdefault(m.group(1), []).append(lineno)
    return keys


def anonymous_scene_props(text: str):
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if re.search(r'(?:context\.scene|bpy\.context\.scene|scene)\s*\[["\']', line):
            hits.append((lineno, line.strip()))
        if re.search(r'window_manager\s*\[["\']', line):
            hits.append((lineno, line.strip()))
    return hits


def line_ref_drift(md_text: str, plugin_text: str, xmltool_text: str):
    """校验注册表中的行号引用是否仍指向含该标识符的行。

    返回 (检查数, 失配列表)。失配仅告警，不判失败——符号名才是主索引。
    """
    src = plugin_text.splitlines() + xmltool_text.splitlines()
    skip = {"x4_preview_of", "x4_ship_mesh_organized"}  # 经常量间接引用
    checked, bad = 0, []
    for line in md_text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        m = re.fullmatch(r"`([A-Za-z_][A-Za-z0-9_]*)`", cells[0])
        if not m or m.group(1) in skip:
            continue
        name = m.group(1)
        nums = []
        for c in cells[1:]:
            nums += [int(x) for x in re.findall(r"`(\d{2,4})`", c)]
        for n in nums:
            if n < 1 or n > len(src):
                bad.append((name, n, "越界"))
                continue
            checked += 1
            if name not in src[n - 1]:
                bad.append((name, n, src[n - 1].strip()[:50]))
    return checked, bad


def report(title: str, missing: dict) -> int:
    if not missing:
        print(f"[PASS] {title}")
        return 0
    print(f"[FAIL] {title}：{len(missing)} 项未登记")
    for name, lines in sorted(missing.items()):
        where = ", ".join(str(n) for n in lines[:6])
        print(f"       - {name}  (line {where})")
    return 1


def main() -> int:
    for path in (REGISTRY, PLUGIN, XMLTOOL):
        if not path.is_file():
            print(f"[ERROR] 缺少文件：{path}")
            return 2

    tokens = registry_tokens(load_text(REGISTRY))
    plugin = load_text(PLUGIN)
    xmltool = load_text(XMLTOOL)
    failures = 0

    failures += report(
        "PropertyGroup 字段",
        {k: v for k, v in property_group_fields(plugin).items() if k not in tokens},
    )
    failures += report(
        "物体自定义属性键",
        {k: v for k, v in custom_property_keys(plugin).items() if k not in tokens},
    )
    failures += report(
        "常量",
        {
            k: v
            for k, v in {**top_level_constants(plugin), **top_level_constants(xmltool)}.items()
            if k not in tokens
        },
    )
    failures += report(
        "访问器字符串键（XML/配置）",
        {
            k: v
            for k, v in {**accessor_keys(plugin), **accessor_keys(xmltool)}.items()
            if k not in tokens and k not in IGNORED_KEYS
        },
    )

    anon = anonymous_scene_props(plugin) + anonymous_scene_props(xmltool)
    if anon:
        failures += 1
        print(f"[FAIL] 匿名场景/窗口属性：{len(anon)} 处")
        for lineno, line in anon:
            print(f"       - line {lineno}: {line}")
    else:
        print("[PASS] 无匿名场景/窗口属性")

    checked, drift = line_ref_drift(load_text(REGISTRY), plugin, xmltool)
    if drift:
        print(f"[WARN] 行号引用 {checked} 处中 {len(drift)} 处已漂移（建议按符号名复核注册表）")
        for name, n, ctx in drift[:5]:
            print(f"       - {name} @ {n}: {ctx}")
    else:
        print(f"[PASS] 行号引用 {checked} 处全部有效")

    print("-" * 56)
    if failures:
        print("结果：FAIL —— 请先在《数据源与引用注册表.md》登记后重试。")
        return 1
    print("结果：PASS —— 数据全有源，引用全可查。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
