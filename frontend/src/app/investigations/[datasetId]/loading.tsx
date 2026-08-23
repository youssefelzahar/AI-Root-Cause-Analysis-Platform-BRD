import { CardSkeleton, TableSkeleton } from "@/components/ui";

/**
 * The "running the analysis" screen - and the prefetch boundary for this route.
 *
 * Next stops a dynamic-route `<Link>` prefetch at the nearest `loading` file, so
 * this is what keeps a link in the investigations list from creating an
 * investigation the moment it scrolls into view. Deleting it would turn hovering
 * the list into a write. The links also pass `prefetch={false}`, but this file is
 * the reason it works at all - do not remove it.
 */
export default function Loading() {
  return (
    <div>
      <div className="alert alert-info">
        <div>
          <strong>Running the analysis</strong>
          <p>
            Scanning the dataset, ranking contributors and building the evidence. A large file can
            take a minute or two.
          </p>
        </div>
      </div>
      <CardSkeleton count={4} />
      <TableSkeleton rows={5} columns={6} />
    </div>
  );
}
