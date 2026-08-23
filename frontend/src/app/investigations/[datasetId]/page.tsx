import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import {
  InvestigationBlocked,
  isBlockedCode,
} from "@/features/rca/investigation-blocked";
import { ApiError } from "@/lib/api/errors";
import { createInvestigation } from "@/lib/api/investigations";
import type { Investigation } from "@/types/investigation";

export const dynamic = "force-dynamic";

/**
 * Run an investigation, then hand over to its permalink.
 *
 * This route is the verb; `[investigationId]` is the noun. Keeping them apart is
 * what makes a link to an investigation a snapshot: refreshing the permalink
 * re-reads a persisted row instead of recomputing, so the numbers cannot drift
 * under a reader.
 *
 * The links that point here are unchanged - they still mean "run analysis" - so
 * the redirect is invisible.
 */
export default async function RunInvestigationPage({
  params,
}: {
  params: Promise<{ datasetId: string }>;
}) {
  const { datasetId } = await params;

  // Expected, actionable outcomes are rendered inline; anything else propagates
  // to error.tsx rather than being flattened into an empty-looking result.
  let created: Investigation;
  try {
    created = await createInvestigation({ dataset_id: datasetId });
  } catch (error) {
    if (error instanceof ApiError && isBlockedCode(error.code)) {
      return (
        <div>
          <PageHeader
            eyebrow="Root cause analysis"
            title="Cannot analyse this dataset yet"
            backHref="/investigations"
            backLabel="Investigations"
          />
          <InvestigationBlocked
            code={error.code}
            datasetId={datasetId}
            message={error.message}
          />
        </div>
      );
    }
    throw error;
  }

  // Outside the try/catch: redirect() signals by throwing, so calling it inside
  // would hand a control-flow exception to the ApiError branch above.
  redirect(`/investigations/${datasetId}/${created.id}`);
}
