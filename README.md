# EarthGlyph｜地球图形猎人

> 画一个形状，去地球上找它。

EarthGlyph 是一个基于手绘草图的真实地理景观检索平台：用户在网页上绘制闭合轮廓，系统从开放地理数据中检索轮廓相似的真实湖泊 / 水库 / 池塘，并在地图上展示真实位置、轮廓叠加与可分享结果。

**目标赛事：** 2026 Qoder 高校创造力大赛（LifeHack 赛道）
**MVP 截止：** 2026-11-30 前可公开访问
**当前阶段：** P0（真实数据与检索可行性验证）——P0-0 环境 / P0-1 真实数据 / P0-2 几何清洗与标准轮廓库已完成，检索可行性待验证

## 核心原则

真搜索，非预设匹配；不夸大能力；不在网站运行大模型；先完成真实数据 PoC，再实现网站。详见 [`AGENTS.md`](AGENTS.md)。

## 版本范围

- **V1（本次比赛目标）**：闭合轮廓检索（湖泊 / 水库 / 池塘）。
- **V2（未来）**：道路、河流、建筑群等线条 / 组合图案。
- **V3（未来）**：草图与遥感图像的跨模态检索。

当前所有设计以 V1 可实现性为优先，不为未来扩展过度设计。

## 技术架构（概览）

- **Python 算法与数据层**（`earthglyph/`、`scripts/`）：数据下载清洗、轮廓提取、形状标准化、特征计算、检索、索引构建、实验评价。核心算法独立于前端运行。
- **Web 层**（`web/`）：React + TypeScript + Vite，Canvas 绘图、MapLibre 地图、Web Worker 检索。
- **系统边界（V1）**：Python 离线构建索引 → 静态数据文件 → 浏览器端检索。暂不引入后端 / 登录 / 云数据库。

详见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 环境要求

- Python 3.11+（当前环境 3.12.10）
- Node.js LTS（当前环境 v26.13.0）、npm、npx
- 详见 [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md)

## 快速开始

```bash
# Python：创建虚拟环境并安装核心 + 开发依赖
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# Git Bash / Linux:   source .venv/Scripts/activate
pip install -e ".[dev]"

# 运行测试
pytest
```

Web 端初始化见 [`web/README.md`](web/README.md)（P1 阶段）。

## 项目状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| 初始化 | 环境 / Skills / 结构 / 规范 / 测试链路 | ✅ 完成 |
| P0 | 真实数据获取与检索可行性验证 | 🟡 进行中：P0-0 环境 ✅、P0-1 真实数据 ✅（威斯康星 6,543 地物 / 1,984 可用闭合轮廓）、P0-2 几何清洗 ✅（839 个标准轮廓候选 + manifest）；P0-3～P0-8 检索可行性待验证 |
| P1 | 基础 Web 产品（画布 / 搜索 / 地图） | ⬜ |
| P2 | 检索与交互优化 | ⬜ |
| P3 | 分享与完整体验 | ⬜ |
| P4 | Qoder Sites 部署与比赛提交 | ⬜ |

当前任务与下一步见 [`docs/TASKS.md`](docs/TASKS.md)。

## 目录结构

```
EarthGlyph/
├── AGENTS.md            # 最高 Agent 规则
├── README.md
├── pyproject.toml       # Python 包与依赖
├── .qoder/skills/       # 项目级 Skills（gdal / shapely / earthglyph-retrieval）
├── earthglyph/          # Python 算法与数据层
│   ├── geometry/        # 轮廓提取、标准化、重采样
│   ├── features/        # 形状特征
│   ├── retrieval/       # 粗筛 + 精排 + 去重
│   └── data/            # 数据加载与清单
├── scripts/             # 可复现数据 / 索引 / 评价脚本
├── tests/               # pytest
├── data/                # raw / processed / index（默认不入 Git）
├── experiments/         # 实验脚本、日志、结果图
├── web/                 # React + TS + Vite 前端（P1）
└── docs/                # 规范与设计文档
```

## 数据与许可

首版数据优先采用 Overture Maps Base / Water（主要源自 OSM，受 ODbL 等适用许可约束）。展示来源署名，保留数据版本与许可说明。详见 [`docs/DATA_PIPELINE.md`](docs/DATA_PIPELINE.md)。

## 许可

代码：MIT。地理数据遵循其各自来源许可。
