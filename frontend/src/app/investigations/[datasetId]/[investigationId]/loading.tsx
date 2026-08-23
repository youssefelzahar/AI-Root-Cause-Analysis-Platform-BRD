import { CardSkeleton, TableSkeleton } from "@/components/ui";

/**
 * Reading a persisted investigation is a single indexed query, so this is only
 * ever a flash. No evidence skeleton: that band is below the fold.
 */
export default function Loading() {
  return (
    <div>
      <CardSkeleton count={4} />
      <TableSkeleton rows={5} columns={6} />
    </div>
  );
}
