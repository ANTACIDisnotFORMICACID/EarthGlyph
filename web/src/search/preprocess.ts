/**
 * Frontend sketch preprocessing.
 *
 * The normalization here MUST stay consistent with the Python
 * `earthglyph.geometry.normalize_contour` convention (same centering and RMS
 * radius scaling) so offline evaluation matches online retrieval.
 * See docs/RETRIEVAL_DESIGN.md §6.
 */

export type Point = readonly [x: number, y: number];

/**
 * Translate a closed contour to its centroid and scale by RMS radius,
 * making it translation- and scale-invariant.
 *
 * @throws Error if fewer than 3 points, or the contour is degenerate.
 */
export function normalizeContour(points: readonly Point[]): Point[] {
  if (points.length < 3) {
    throw new Error("a contour needs at least 3 points");
  }

  const n = points.length;
  let cx = 0;
  let cy = 0;
  for (const [x, y] of points) {
    cx += x;
    cy += y;
  }
  cx /= n;
  cy /= n;

  let sumSq = 0;
  for (const [x, y] of points) {
    sumSq += (x - cx) ** 2 + (y - cy) ** 2;
  }
  const rms = Math.sqrt(sumSq / n);
  if (rms <= 1e-12) {
    throw new Error("degenerate contour: zero RMS radius");
  }

  return points.map(([x, y]) => [(x - cx) / rms, (y - cy) / rms] as Point);
}
