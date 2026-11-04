# EarthGlyph Web

React + TypeScript + Vite 前端。当前为初始化骨架，完整绘图画布 / 搜索 / 地图 UI 在 **P1** 实现（见 `../docs/ROADMAP.md`）。

## 技术栈

- React 18 + TypeScript（strict）+ Vite
- Vitest（单元 / 逻辑测试，jsdom 环境）
- 规划中：MapLibre GL JS（地图）、Web Worker（检索）、Playwright（E2E）

## 开发

```bash
npm install
npm run dev        # 本地开发服务器
npm run test       # 运行 Vitest
npm run typecheck  # TypeScript 类型检查
npm run build      # 生产构建
```

## 目录

```
src/
├── App.tsx               # 占位页面（P1 替换为完整 UI）
├── main.tsx
├── search/
│   ├── preprocess.ts     # 草图标准化（须与 Python earthglyph.geometry 约定一致）
│   └── preprocess.test.ts
└── vite-env.d.ts
```

## 约定

`src/search/preprocess.ts` 的归一化必须与 Python `earthglyph/geometry` 保持一致（同采样点数、同平移 / 缩放定义），保证离线评价与在线检索行为一致。见 `../docs/RETRIEVAL_DESIGN.md` §6。

## 部署

V1 采用静态优先方案，目标平台为 Qoder Sites。P0 需先验证 Qoder Sites 对 React/Vite 静态项目的实际发布流程。
