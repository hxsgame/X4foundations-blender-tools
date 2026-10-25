# X4 Foundations Blender 工具链

Blender 4.2 组件放置插件 + 舰船 XML 补全脚本。用于给 X4 舰船/空间站模型摆放硬点（炮塔、引擎、护盾、泊位……）并生成游戏可读的连接数据。

> 非官方工具，与 Egosoft 无关。X4: Foundations 及其素材版权归 Egosoft 所有。

## 包含内容

| 文件 | 作用 |
|---|---|
| `X4_blender42_add_components_fixed_addon.py` | Blender 4.2 插件：**组件放置器** + **航路编辑器** |
| `X4_BlenToolOut_con_ImproveTool_easy_fixed.py` | 舰船 XML 后处理脚本：补 tags/group、生成镜像、补连接宏、清理航路 |

两者**不直接通信**，通过「物体命名」和「导出的 XML」两层约定衔接（见下文）。

## 安装

**插件**：Blender → `Edit > Preferences > Add-ons > Install from Disk` → 选择插件 py → 勾选启用。
面板位置：3D 视图按 `N` → 侧栏标签 **「组件放置-修正」**，内含两个面板：`组件放置器`、`航路编辑器`（后者默认折叠）。
插件自动跟随 Blender 界面语言（中文/英文）。

**脚本**：Python 3.9+，只用标准库，无需安装依赖。

```bash
python X4_BlenToolOut_con_ImproveTool_easy_fixed.py --dir "你的XML目录"
# 可选：--dry-run 只预览不写回；--no-backup 不备份
```

## 工作流程

1. **摆放硬点**：`组件放置器` 里选预设（炮塔/引擎/舰桥/机库/机平…）+ 尺寸 + 朝向 → 在网格面上点击放置；`X=0 镜像` 一键生成另一半。
2. **导出舰船 XML**：用 Blender 侧导出器导出（本工具不负责舰船骨架导出，只保证硬点命名符合下游解析）。
3. **跑补全脚本**：脚本对目录下所有 XML 依次做——类型→tags 映射、group 写入、`left`→`right` 镜像、新增连接宏引用、航路清理、source geometry 路径修复、连接布局规范化。每次运行自动备份（每个文件保留最近 20 份）。
4. **码头/航母的航路**：`航路编辑器` 导入现有 `waypoints.xml` → 在视口里增删改航点 → 导出 `waypoints.xml` → 把其中的 `<waypoints>` 段合并进舰船 XML。

## 命名规则

### 硬点（组件放置器）

完整名 = `组名` + `链接符号` + `组件key` + `名称`，末尾自动追加 `-NNN` 三位序号。

```
left_g_01 - up7con_ - turret_l - 001
  组名      链接符号    组件key    名称    序号
```

- **组名**：左右对称的分组轴，默认 `left_g_01`。含 `left` 的硬点，脚本会镜像出 `right` 版本。
- **组件key**：默认 `up7con_`，标识「本工具放置的硬点」。脚本在类型匹配和宏名生成前都会**剥离**它，所以它不会污染 tags 或 macro ref。
- **名称**：类型段，决定 tags。可用的关键类型见插件预设（`turret_l`、`engine_m`、`bridge`、`shipstorage`、`dockarea`、`waypoint_s`……）。
- **序号**：三位数字，保证同名唯一。脚本按 `组-名称-序号` 解析；无组名时为 `名称-序号`。

特殊标记（写在名称或组名里）：

| 标记 | 效果 |
|---|---|
| `left` | 脚本镜像生成对应的 `right` 节点 |
| `_nog` | 该节点不写 `group` 属性 |
| `_oneg` | 镜像节点沿用与原节点相同的组名 |
| `_nosym` | 镜像只翻转位置 x，保留原朝向（用于左右朝向本就不同的硬点） |

### 航点（航路编辑器）

完整名 = `组名` + `连接符` + `航点名` + `_NN` 两位序号。

```
mt_dock_01 --- mt_dock_01-waypoint_01
   组名     连接符        航点名
```

- **连接符** 是航路编辑器的全局设置，默认 `---`；**导入命名、新建、应用组名、导出截断四处共用同一个值**。
- 导出时从**第一个连接符**截断：左侧写 `group`，右侧写 `name`：

```xml
<waypoint name="mt_dock_01-waypoint_01" tags="dockpath start" group="mt_dock_01" ... >
```

- 物体名里**不含连接符 = 该航点无分组**（合法的，共享路径网就是这么用的）。
- 组名/航点名禁用连接符序列（会被自动替换为 `_`）；`dockingbay` 名称/组名含连接符序列时导入会报错并中止，需先在 XML 里改掉。

## 插件与脚本的互动约定

**约定的桥梁只有两样：物体名、导出的 XML。** 插件里的其他自定义属性只服务于 Blender 内部（放置、维护、预览、显示），脚本一概不读，也不会写进 XML。

| Blender 侧（插件） | → | XML 侧（脚本消费） |
|---|---|---|
| 空物体硬点名 `组-up7con_类型-NNN` | → | `<connection name="...">`，解析出 (group, 类型) 后写入 `tags` 与 `group` |
| 名称/组名含 `left` | → | 镜像生成 `right` 节点（位置 x→-x，四元数 qx/-qy/-qz/qw） |
| 航路物体的 `tags` / `links` / `group` / `lane` 属性 | → | 航路编辑器导出为游戏格式的 `<waypoint>` / `<connection>` 片段 |

插件写入的自定义属性（仅 Blender 内使用，不参与 XML）：

- `x4_comp_size` — 预设长宽高，供维护/预览/显示
- `x4_axis_normal` / `x4_axis_forward` / `x4_axis_up` — 朝向轴设置，批量平移按它判断面法线
- `x4_axis_show` — 三色局部轴常显标记
- `x4_preview_of` — 预览实体归属

航路对象额外携带：`hardpoint_type`（`waypoint` / `dockingbay`）、`group`、`tags`、`links`、`lane`。

## 航路编辑器导出格式

```xml
<?xml version="1.0" encoding="utf-8"?>
<waypoints>
  <waypoint name="mt_dock_02-waypoint_01" tags="dockpath start" group="mt_dock_02"
            x="-50" y="100" z="2700" qx="0" qy="0" qz="0" qw="1">
    <links>
      <link ref="mt_dock_02-waypoint_02"/>
    </links>
  </waypoint>
</waypoints>
<connections>
  <connection name="dockingbay_m_01" group="mt_dock_02"/>
</connections>
```

- `tags` 合法词汇（17 个）：`typenormal` `typedock` `dockpath` `entrypath` `exitpath` `start` `end` `blocklink` `closelink` `force` `sizeLevel0` `sizeLevel1` `speed_slow` `speed_normal` `speed_fast` `speed_faster` `dynamicmasstraffic`
- `lane` 控制四元数：**进站道**写恒等朝向、**出站道**写绕竖直轴 180°、**不设置**则跟随物体实际旋转。按行进方向分车道设置，与游戏原版 dockarea 的做法一致。
- 坐标：Blender 的 Z-up 换算为 X4 的 Y-up，位置取 `(x, z, y)`。
- `<connections>` 段里的 `dockingbay` 只写 `name` + `group`，供后续工具定位并同步组名，不含坐标。
- 文件是**两个根节点的片段**（`waypoints` + `connections`），设计用于合并进舰船 XML，不是独立合法 XML 文档。

## 常见问题

- **导出报「链接到不存在的目标」**：`links` 里的名字必须是场景中真实存在的硬点物体名。
- **导出报「没有可导出的硬点」**：物体需要有 `hardpoint_type`（航点）或 `tags` 属性。用「设为 Waypoint」补，或直接用「新建 Waypoint」。
- **游戏里船出港朝向反了**：该航点的 `lane` 设成了进站道；改成「出站道」再导出。
- **改了连接符但旧场景命名对不上**：连接符是全局设置，改动后旧物体名不会被重写；用「应用组名到选中」或改回原连接符。

## English (short)

**X4 Foundations Blender Toolchain** — a Blender 4.2 add-on plus an XML post-processor for authoring ship/station hardpoints.

- **Add-on**: place turrets/engines/shields/docks on the mesh surface, mirror across X=0, batch move/rotate, preview volumes, plus a Route Editor that imports, edits and exports waypoint chains.
- **Script**: run over a folder of exported ship XML — it maps hardpoint names to X4 `tags`, fills `group`, mirrors `left`→`right`, adds macro-reference connections, cleans waypoint data, fixes `source geometry` paths and normalizes connection order. Backups are kept per file (20 rotating copies). Flags: `--dir`, `--dry-run`, `--no-backup`.

Naming contract: hardpoints are `group-separator-key-type-NNN` (e.g. `left_g_01-up7con_turret_l-001`, key defaults to `up7con_` and is stripped before type matching); waypoints are `group---name_NN` (e.g. `mt_dock_01---mt_dock_01-waypoint_01`), exported as `name="..." group="..."`.

The add-on and the script never talk directly — only object names and the exported XML are shared. Custom properties like `x4_comp_size` / `x4_axis_*` are Blender-side only.

## 授权

自由使用、修改、再分发。如果你改进了它，欢迎回馈给社区。
