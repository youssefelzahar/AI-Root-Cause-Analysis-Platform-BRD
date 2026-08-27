import { CardSkeleton } from "@/components/ui";

/**
 * This route only fetches dataset context - it analyses nothing until a question
 * is typed - so the wait here is short and this file is ordinary decoration rather
 * than the prefetch guard that `investigations/[datasetId]/loading.tsx` is.
 *
 * It still earns its place: without it the segment falls back to the nearest
 * ancestor boundary, and the whole page blanks instead of the panel.
 */
export default function Loading() {
  return <CardSkeleton count={2} />;
}
