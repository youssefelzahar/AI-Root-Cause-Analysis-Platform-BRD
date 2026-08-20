/**
 * A diverging bar growing left (negative) or right (positive) from a centre line.
 *
 * aria-hidden: the number it depicts is always rendered as text in the adjacent
 * cell, so announcing the bar as well would just repeat it.
 */
export function ContributionBar({ value, scale }: { value: number | null; scale: number }) {
  const magnitude = value === null ? 0 : Math.abs(value);
  const width = scale > 0 ? Math.min(100, (magnitude / scale) * 100) : 0;
  const negative = (value ?? 0) < 0;

  return (
    <span className="rca-bar" aria-hidden="true">
      <span className="rca-bar-neg" style={{ width: negative ? `${width}%` : 0 }} />
      <span className="rca-bar-pos" style={{ width: negative ? 0 : `${width}%` }} />
    </span>
  );
}
