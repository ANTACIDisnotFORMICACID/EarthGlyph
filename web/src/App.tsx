/**
 * Minimal placeholder app for the initialization phase.
 *
 * The full sketch canvas / search / map UI is built in P1 per docs/ROADMAP.md.
 * This file only proves the Vite + React + TypeScript (strict) toolchain runs.
 */
export default function App(): JSX.Element {
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
      <h1>EarthGlyph</h1>
      <p>画一个形状，去地球上找它。</p>
      <p>
        <strong>当前阶段：P0（真实数据与检索可行性验证）。</strong>
        绘图画布、搜索与地图将在 P1 实现。
      </p>
    </main>
  );
}
