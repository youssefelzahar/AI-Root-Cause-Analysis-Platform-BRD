import { CardSkeleton, TableSkeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div>
      <CardSkeleton count={4} />
      <TableSkeleton rows={5} columns={4} />
    </div>
  );
}
