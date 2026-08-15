import { CardSkeleton, TableSkeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div>
      <CardSkeleton count={6} />
      <TableSkeleton rows={8} columns={8} />
    </div>
  );
}
