# ENVIRONMENT — 实际开发环境、版本与安装结果

> 记录初始化时**实际检测到 / 安装**的环境。版本随开发更新，以本文件为准。
> 最后更新：2026-11-04（P0-0 数据依赖安装并实测验证）。

## 1. 系统

| 项 | 值 |
|----|----|
| 操作系统 | Windows 10（Build 19045），x64 |
| Shell | Git Bash（MINGW64_NT-10.0-19045，MSYS 3.6.10） |
| 工作目录 | `E:/EarthGlyph` |

## 2. 工具链版本（实测）

| 工具 | 版本 | 状态 |
|------|------|------|
| Git | 2.56.0.windows.4 | 已存在 |
| Python | 3.12.10 | 已存在（`python`） |
| Node.js | v26.13.0（LTS） | **本次安装**（winget `OpenJS.NodeJS.LTS`） |
| npm | 11.20.0 | 随 Node.js 安装 |
| npx | 11.20.0 | 随 Node.js 安装 |
| winget | v1.29.380 | 已存在（用于安装 Node.js） |

**Node.js 安装说明：** 初始化前系统无 node/npm/npx（PATH 中不存在，常见安装目录亦无）。通过 `winget install OpenJS.NodeJS.LTS --silent` 安装官方 LTS（MSI，安装时触发 UAC 管理员授权）。安装路径 `C:/Program Files/nodejs`。

> **PATH 注意：** 当前 Git Bash 会话的 PATH 未自动包含 Node 安装目录。在新 shell 中运行 node/npm/npx 前需 `export PATH="/c/Program Files/nodejs:$PATH"`，或重开终端使系统 PATH 生效。

## 3. Python 依赖（venv 实测）

虚拟环境：`E:/EarthGlyph/.venv`（Python 3.12.10）。
安装命令：`pip install -e ".[dev]"`，P0-0 追加 `pip install -e ".[data,cv]"`。

| 包 | 版本 | 备注 |
|----|------|------|
| numpy | 2.5.4 | 核心 |
| scipy | 1.18.1 | 核心 |
| shapely | 2.2.0 | 内含 GEOS 3.14.1 |
| pyproj | 3.8.0 | 内含 PROJ 9.8.1 |
| geopandas | 1.2.0 | **P0-0 安装**（数据读取） |
| pandas | 3.1.0 | 随 geopandas 安装 |
| pyarrow | 26.0.1 | **P0-0 安装**（Parquet） |
| pyogrio | 0.13.0 | 随 geopandas 安装，**内含 GDAL 3.12.4** |
| duckdb | 2.0.0 | **P0-0 安装**（远端 GeoParquet range 读取） |
| overturemaps | 1.0.2 | **P0-0 安装**（官方 CLI / release 解析） |
| matplotlib | 3.11.2 | **P0-0 安装**（数据预览图） |
| opencv-python-headless | 5.0.0.93 | **P0-0 安装**（P0-2 Hu 矩基线） |
| pytest | 9.2.0 | 开发 / 测试 |
| pytest-cov | 7.1.0 | 开发 / 测试 |
| ruff | 0.17.3 | Lint |
| mypy | 2.4.1 | 类型检查（strict） |
| earthglyph | 0.1.0 | 本项目（editable） |

**P0-0 可用性验证（不只凭安装成功判定）：**

| 检查 | 命令 | 结果 |
|------|------|------|
| 导入 | `python -c "import geopandas, shapely, pyproj, pyarrow, duckdb, matplotlib, cv2, overturemaps"` | 通过 |
| 底层引擎版本 | `shapely.geos_version` / `pyproj.proj_version_str` / `pyogrio.__gdal_version__` | GEOS 3.14.1 / PROJ 9.8.1 / GDAL 3.12.4 |
| 真实 GeoParquet 读取 | `geopandas.read_parquet("data/raw/wisconsin_water.parquet")` | 6,543 行，CRS EPSG:4326，几何可解析 |
| 投影与面积 | `pyproj.Transformer` → `+proj=aeqd` | 通过（面积量级合理，见 DATA_PIPELINE） |
| 绘图 | `matplotlib`（Agg 后端） | `scripts/preview_data.py` 正常出图 |
| 单元测试 | `pytest` | **65 passed**，无 UserWarning（`-W error::UserWarning`） |
| 类型检查 | `mypy earthglyph/`（strict） | no issues found in 7 source files |

> **pip 代理提示：** 安装时 pip 曾尝试经 `127.0.0.1` 代理并出现短暂连接告警，但主依赖安装成功。若后续安装失败，检查 `HTTP(S)_PROXY` 环境变量或 pip 配置。
>
> **网络环境限制（P0-1 实测）：** 系统代理 `127.0.0.1:7890`（注册表 ProxyEnable=1）下，`git clone github.com:443` 被阻断；pyarrow 的 AWS S3 SDK 直连 Overture 数据桶会出现响应截断，urllib 偶发 `WinError 10048`（临时端口冲突）。DuckDB httpfs 的 HTTPS range 读取不受影响 —— 数据获取路径据此选择，见 [`DECISIONS.md`](DECISIONS.md) D13。

## 4. Web 依赖（实测）

目录：`E:/EarthGlyph/web`。安装命令：`npm install`（156 包）。

| 工具 | 版本 |
|------|------|
| react | 18.3.1 |
| typescript | 5.9.3（strict） |
| vite | 5.4.21 |
| vitest | 2.1.9（jsdom 环境） |

**验证结果：** `npm run test` → 5 passed；`npm run typecheck` → 通过；`npm run build` → 成功（dist 已 gitignore）。
`npm audit` 报告 6 个漏洞（均在 vite/vitest 开发工具链的传递依赖中，非生产运行时）；初始化阶段未执行 `audit fix --force` 以免破坏版本锁定，P1 前复评。

**尚未安装（P1 按需）：** `maplibre-gl`（地图）、Playwright（E2E）、Web Worker 相关无需额外依赖。

## 5. 系统级 GIS 工具

| 工具 | 状态 |
|------|------|
| GDAL CLI（gdalinfo/ogr2ogr 等） | **未安装**（非阻塞）。P0-1 的读取 / 投影 / 面积计算走 Python 栈：geopandas + pyogrio（自带 GDAL 3.12.4）+ shapely（GEOS 3.14.1）+ pyproj（PROJ 9.8.1），已实测可用。 |
| `ogrinfo` 直接检查 GeoParquet | 需要系统 GDAL CLI；当前用 `scripts/inspect_data.py` 输出等价信息（列、类型、有效性、分布、面积）。首次真正需要 CLI 时按官方方式安装（winget `GDAL.GDAL` 或 conda-forge），不阻塞 P0。 |

## 6. Agent Skills

见 [`SKILLS.md`](SKILLS.md)。三个 Skill（gdal / shapely / earthglyph-retrieval）均已导入 `.qoder/skills/` 并被 Qoder 动态发现加载。

## 7. 复现环境的命令

```bash
# Python
python -m venv .venv
source .venv/Scripts/activate          # Git Bash
pip install -e ".[dev]"                # 核心 + 开发
pip install -e ".[data,cv]"            # P0 数据 / OpenCV
pytest                                 # 65 passed（全部离线，无网络）
mypy earthglyph/                       # strict

# Web
cd web
npm install
npm run test && npm run typecheck && npm run build
```
