import { describe, expect, it } from "vitest";
import goldenInput from "./golden_contour.json";
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

describe("normalizeContour vs the Python golden vector", () => {
  it("reproduces earthglyph.geometry.normalize_contour on the committed input", () => {
    const golden = goldenInput as unknown as {
      input_points: Point[];
      input_centroid: [number, number];
      input_rms_radius: number;
      expected_output_points: Point[];
    };
    const out = normalizeContour(golden.input_points);
    // JSON round-trips doubles exactly, so the expected values are the Python
    // float64 outputs verbatim; the TS arithmetic may differ at summation order.
    const rel = (a: number, b: number): number =>
      Math.abs(a - b) / Math.max(1e-300, Math.abs(b));
    for (let i = 0; i < out.length; i++) {
      expect(rel(out[i][0], golden.expected_output_points[i][0])).toBeLessThan(1e-9);
      expect(rel(out[i][1], golden.expected_output_points[i][1])).toBeLessThan(1e-9);
    }
    expect(golden.input_centroid[0]).toBeCloseTo(
      golden.input_points.reduce((s, [x]) => s + x, 0) / golden.input_points.length,
      9,
    );
    expect(golden.input_rms_radius).toBeGreaterThan(0);
  });
});
