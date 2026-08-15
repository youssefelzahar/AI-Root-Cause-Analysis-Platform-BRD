"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { getDatasetStatus } from "@/lib/api/datasets";
import { IN_PROGRESS_STATUSES } from "@/types/api";

const FAST_INTERVAL_MS = 2000;
const SLOW_INTERVAL_MS = 5000;
const FAST_WINDOW_MS = 30_000;
const GIVE_UP_MS = 10 * 60 * 1000;

/**
 * Polls while a dataset is still being processed, then refreshes the server
 * component once so the page renders real data.
 *
 * Deliberately bounded: it backs off, stops at a deadline, pauses in a hidden
 * tab, and stops on error rather than hammering a failing API forever.
 */
export function DatasetStatusWatcher({ datasetId }: { datasetId: string }) {
  const router = useRouter();
  const [message, setMessage] = useState("Processing this dataset…");

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const startedAt = Date.now();

    const tick = async () => {
      if (cancelled) return;

      if (typeof document !== "undefined" && document.visibilityState !== "visible") {
        timer = setTimeout(tick, SLOW_INTERVAL_MS);
        return;
      }

      try {
        const status = await getDatasetStatus(datasetId);
        if (cancelled) return;

        if (!IN_PROGRESS_STATUSES.includes(status.status)) {
          router.refresh();
          return;
        }
      } catch {
        // Stop polling on error instead of looping against a failing API.
        if (!cancelled) setMessage("Lost contact with the API. Refresh to check the status.");
        return;
      }

      const elapsed = Date.now() - startedAt;
      if (elapsed > GIVE_UP_MS) {
        setMessage("Still processing. Refresh the page to check again.");
        return;
      }

      timer = setTimeout(tick, elapsed < FAST_WINDOW_MS ? FAST_INTERVAL_MS : SLOW_INTERVAL_MS);
    };

    timer = setTimeout(tick, FAST_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [datasetId, router]);

  return (
    <div className="alert alert-info">
      <div>
        <strong>
          <Loader2 size={14} style={{ verticalAlign: "-2px", marginRight: 6 }} />
          Working
        </strong>
        <p>{message}</p>
      </div>
    </div>
  );
}
