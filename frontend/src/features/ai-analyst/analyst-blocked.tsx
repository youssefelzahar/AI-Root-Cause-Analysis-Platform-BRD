import Link from "next/link";

import { Alert } from "@/components/ui";

/**
 * Errors that have a next step, rendered inline rather than thrown.
 *
 * Handled here rather than in `error.tsx` for the reason `investigation-blocked`
 * gives: Next.js sanitises server-component errors in production, so the code and
 * message do not survive the boundary. A mapping there would work in development
 * and quietly stop working once deployed.
 *
 * Each entry is a thing the reader can do about it, which is why the map carries a
 * link rather than only prose.
 */

type Blocked = {
  title: string;
  body: string;
  href?: string;
  label?: string;
};

export const BLOCKED: Record<string, Blocked> = {
  DATASET_NOT_ANALYSIS_READY: {
    title: "This dataset needs a KPI first",
    body:
      "The analyst answers questions about a configured KPI - which measure, over which time column, compared against what. Set one up and the dataset becomes Analysis Ready.",
    href: "/datasets",
    label: "Configure a KPI",
  },
  KPI_DEFINITION_NOT_FOUND: {
    title: "No KPI is configured",
    body: "Every question is answered against a KPI definition, and this dataset has none.",
    href: "/datasets",
    label: "Configure a KPI",
  },
  AI_DISABLED: {
    title: "The AI analyst is turned off",
    body:
      "This deployment has AI_ENABLED set to false. The investigations surface still works and produces the same analysis without the written explanation.",
    href: "/investigations",
    label: "Go to investigations",
  },
  LLM_UNAVAILABLE: {
    title: "The language model is not reachable",
    body:
      "Questions cannot be read without it. The investigations surface still produces the full analysis - it just will not write the paragraph.",
    href: "/investigations",
    label: "Go to investigations",
  },
  LLM_MODEL_NOT_INSTALLED: {
    title: "The configured model is not installed",
    body:
      "The model daemon is running but does not have the configured model. Pull it, or point OLLAMA_MODEL at one that is installed.",
  },
};

export function isBlockedCode(code: string | undefined): boolean {
  return Boolean(code && code in BLOCKED);
}

export function AnalystBlocked({ code }: { code: string }) {
  const blocked = BLOCKED[code];
  if (!blocked) return null;
  return (
    <Alert tone="warning" title={blocked.title}>
      {blocked.body}
      {blocked.href ? (
        <>
          {" "}
          <Link href={blocked.href}>{blocked.label ?? "Go"}</Link>
        </>
      ) : null}
    </Alert>
  );
}
