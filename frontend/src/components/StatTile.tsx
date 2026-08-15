type StatTileProps = {
  label: string;
  value: string;
  tone?: "neutral" | "positive" | "negative";
};

export function StatTile({ label, value, tone = "neutral" }: StatTileProps) {
  return (
    <div className={`stat-tile ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
