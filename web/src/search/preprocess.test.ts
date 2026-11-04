import { describe, expect, it } from "vitest";
import { normalizeContour, type Point } from "./preprocess";

const square = (scale = 1, ox = 0, oy = 0): Point[] =>
  [
    [0, 0],
    [1, 0],
    [1, 1],
    [0, 1],
  ].map(([x, y]) => [x * scale + ox, y * scale + oy] as Point);

const rms = (pts: Point[]): number =>
  Math.sqrt(pts.reduce((s, [x, y]) => s + x * x + y * y, 0) / pts.length);

const centroid = (pts: Point[]): Point => [
  pts.reduce((s, [x]) => s + x, 0) / pts.length,
  pts.reduce((s, [, y]) => s + y, 0) / pts.length,
];

describe("normalizeContour", () => {
  it("centers at origin with unit RMS radius", () => {
    const out = normalizeContour(square());
    const [cx, cy] = centroid(out);
    expect(cx).toBeCloseTo(0, 9);
    expect(cy).toBeCloseTo(0, 9);
    expect(rms(out)).toBeCloseTo(1, 9);
  });

  it("is translation invariant", () => {
    const a = normalizeContour(square());
    const b = normalizeContour(square(1, 123, -456));
    a.forEach(([x, y], i) => {
      expect(x).toBeCloseTo(b[i][0], 9);
      expect(y).toBeCloseTo(b[i][1], 9);
    });
  });

  it("is scale invariant", () => {
    const a = normalizeContour(square());
    const b = normalizeContour(square(17.5));
    a.forEach(([x, y], i) => {
      expect(x).toBeCloseTo(b[i][0], 9);
      expect(y).toBeCloseTo(b[i][1], 9);
    });
  });

  it("rejects too few points", () => {
    expect(() => normalizeContour([[0, 0], [1, 1]])).toThrow();
  });

  it("rejects degenerate contours", () => {
    expect(() =>
      normalizeContour([
        [1, 1],
        [1, 1],
        [1, 1],
      ]),
    ).toThrow();
  });
});
