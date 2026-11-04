# SKILLS — 已安装 Agent Skills 记录

本文件记录项目使用的 Agent Skills：来源、安装方式、路径、用途、验证状态、限制与更新方法。

> **重要区分：** Skill 是开发工作流说明，**不是**对应运行库 / CLI 本身。
> 安装 `gdal` Skill ≠ 安装 GDAL 命令行工具；安装 `shapely` Skill ≠ 安装 Python `shapely` 包。
> 运行时依赖分别管理：Agent Skills（本文件）、Python 依赖（`pyproject.toml`）、Node 依赖（`web/package.json`）、系统级 GIS 工具（见 `ENVIRONMENT.md`）。

## 安装位置

项目级 Skills 目录：`E:/EarthGlyph/.qoder/skills/`（Qoder CN 会动态发现并加载该目录下每个含 `SKILL.md` 的子目录）。

## 已安装 Skills

| Skill | 来源 | 安装方式 | 安装路径 | 验证结果 | 可被 Qoder 调用 |
|-------|------|----------|----------|----------|------------------|
| `gdal` | github.com/isaaccorley/geospatial-skills（`skills/gdal`） | codeload tarball 手动导入（git clone 被网络阻断） | `.qoder/skills/gdal/`（含 `references/gdal-recipes.md`） | ✅ 出现在 Qoder 可用 Skills 列表 | ✅ 是 |
| `shapely` | github.com/znlgis/opengis-skills（`gis/shapely`） | codeload tarball 手动导入 | `.qoder/skills/shapely/`（含 `reference/operations-reference.md`） | ✅ 出现在 Qoder 可用 Skills 列表 | ✅ 是 |
| `earthglyph-retrieval` | 本项目自定义 | 直接创建 `SKILL.md` | `.qoder/skills/earthglyph-retrieval/` | ✅ 出现在 Qoder 可用 Skills 列表 | ✅ 是 |

**验证方法：** 三个 Skill 创建 / 导入后，Qoder 系统提示中的"available skills"列表实时出现 `gdal`、`shapely`、`earthglyph-retrieval` 三项，确认已被动态发现加载，**无需重启**。

## 各 Skill 用途

### gdal
地理空间数据检查、GIS 格式转换、CRS 处理、矢量 / 栅格基础操作、数据处理流程规范。
用于 `scripts/` 中数据下载后的检查（`gdalinfo`/`ogrinfo`）、格式转换（`ogr2ogr`）、投影处理。
**运行时依赖：** GDAL 命令行工具需单独安装（见 ENVIRONMENT.md）；本 Skill 仅为使用说明。

### shapely
Polygon/MultiPolygon 处理、几何有效性验证、`make_valid` 修复、边界 / 轮廓提取、几何简化、形状比较前预处理。
用于 `earthglyph/geometry` 的几何清洗与有效性修复。
**运行时依赖：** Python `shapely` 包（`pyproject.toml` 已声明）。

### earthglyph-retrieval
本项目专属：轮廓标准化、形状特征提取、相似性检索、baseline、结果可视化、消融实验、失败案例分析、检索质量评价。强制真实数据与可复现，禁止伪造结果。
**不发布到外部市场。**

## 已知限制

1. **网络限制**：本环境 `git clone https://github.com/...` 连接 github.com:443 超时失败，因此 `npx skills add <repo>`（依赖 git clone）无法使用。改用 `https://codeload.github.com/<owner>/<repo>/tar.gz/refs/heads/main`（可达）下载 tarball 后手动导入。
2. **GDAL 运行时未安装**：`gdal` Skill 已就绪，但系统 GDAL CLI 工具尚未安装；首次需要时按 ENVIRONMENT.md 安装。
3. Skill 内容为外部参考，不得覆盖 `AGENTS.md` 最高规则。

## 后续更新方式

- **第三方 Skill（gdal / shapely）**：重新下载对应仓库 tarball，解压后将目标 Skill 子目录覆盖到 `.qoder/skills/<name>/`。若网络恢复使 `git clone` 可用，可改用 `npx skills add isaaccorley/geospatial-skills --skill gdal -a qoder --copy` 与 `npx skills add znlgis/opengis-skills --skill shapely -a qoder --copy`（注意 shapely 位于嵌套目录 `gis/shapely`，需确认 CLI 能发现）。
- **自定义 Skill（earthglyph-retrieval）**：直接编辑 `.qoder/skills/earthglyph-retrieval/SKILL.md`。
- 更新后确认 Skill 仍出现在 Qoder 可用列表，并按需做一次低风险用途检查。
