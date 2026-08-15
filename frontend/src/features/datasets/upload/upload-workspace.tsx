"use client";

import { CheckCircle2, CircleAlert, FileUp, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";

import { Alert, Panel, ProgressBar } from "@/components/ui";
import { getDatasetStatus } from "@/lib/api/datasets";
import { toUserMessage } from "@/lib/api/errors";
import { uploadDatasetFile } from "@/lib/api/uploads";
import { cn, formatBytes } from "@/lib/format";
import { IN_PROGRESS_STATUSES, type DatasetStatus } from "@/types/api";

import { ACCEPTED_EXTENSIONS, validateFile } from "./validate-file";

type Phase = "idle" | "uploading" | "processing" | "done" | "failed";

const STAGES = [
  { key: "upload", label: "Upload file" },
  { key: "validate", label: "Validate schema" },
  { key: "profile", label: "Generate profile" },
  { key: "ready", label: "Ready" },
] as const;

const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

function stageStateFor(phase: Phase, status: DatasetStatus | null, stage: string) {
  if (phase === "failed") {
    if (stage === "upload" && status === "upload_failed") return "failed";
    if (stage !== "upload" && status !== "upload_failed") return "failed";
    return "";
  }
  if (phase === "uploading") return stage === "upload" ? "active" : "";
  if (phase === "done") return "done";
  if (phase === "processing") {
    if (stage === "upload") return "done";
    if (stage === "validate") return status === "profiling" ? "done" : "active";
    if (stage === "profile") return status === "profiling" ? "active" : "";
  }
  return "";
}

export function UploadWorkspace() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [dragging, setDragging] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState({ loaded: 0, total: 0 });
  const [status, setStatus] = useState<DatasetStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [datasetId, setDatasetId] = useState<string | null>(null);

  const pollUntilTerminal = useCallback(
    async (id: string) => {
      const deadline = Date.now() + POLL_TIMEOUT_MS;

      while (Date.now() < deadline) {
        // Do not hammer the API from a background tab.
        if (typeof document !== "undefined" && document.visibilityState !== "visible") {
          await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
          continue;
        }

        const current = await getDatasetStatus(id);
        setStatus(current.status);

        if (!IN_PROGRESS_STATUSES.includes(current.status)) {
          if (current.status === "profiling_failed" || current.status === "upload_failed") {
            setPhase("failed");
            setError(current.error_message ?? "Processing failed.");
            return;
          }
          setPhase("done");
          router.push(`/datasets/${id}`);
          return;
        }

        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
      }

      setPhase("failed");
      setError("Still processing after 10 minutes. Open the dataset to check its status.");
    },
    [router],
  );

  const startUpload = useCallback(
    async (selected: File) => {
      const check = validateFile(selected);
      if (!check.ok) {
        setError(check.message);
        setPhase("failed");
        setFile(selected);
        return;
      }

      setFile(selected);
      setError(null);
      setStatus(null);
      setPhase("uploading");
      setProgress({ loaded: 0, total: selected.size });

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const response = await uploadDatasetFile(selected, {
          onProgress: setProgress,
          signal: controller.signal,
        });
        setDatasetId(response.dataset.id);
        setPhase("processing");
        setStatus(response.dataset.status);
        await pollUntilTerminal(response.dataset.id);
      } catch (uploadError) {
        setPhase("failed");
        setError(toUserMessage(uploadError));
      } finally {
        abortRef.current = null;
      }
    },
    [pollUntilTerminal],
  );

  const percent = progress.total ? (progress.loaded / progress.total) * 100 : 0;
  const busy = phase === "uploading" || phase === "processing";

  return (
    <div className="content-grid">
      <Panel>
        <div
          className={cn("dropzone", dragging && "dragging")}
          onDragEnter={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            const dropped = event.dataTransfer.files;
            if (dropped.length > 1) {
              setError("Drop a single file at a time.");
              setPhase("failed");
              return;
            }
            if (dropped[0]) void startUpload(dropped[0]);
          }}
          aria-describedby="upload-constraints"
        >
          <FileUp size={32} className="muted" />
          <div>
            <p>
              <strong>Drag a file here</strong>
            </p>
            <p className="muted" id="upload-constraints">
              CSV, TSV or TXT up to 200 MB · XLSX up to 25 MB
            </p>
          </div>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
          >
            Choose a file
          </button>
          <input
            ref={inputRef}
            className="visually-hidden"
            type="file"
            accept={ACCEPTED_EXTENSIONS.join(",")}
            disabled={busy}
            onChange={(event) => {
              const selected = event.target.files?.[0];
              if (selected) void startUpload(selected);
              event.target.value = "";
            }}
          />
        </div>

        {file && phase !== "idle" ? (
          <div style={{ marginTop: 20 }}>
            <div className="toolbar">
              <strong>{file.name}</strong>
              <span className="muted">{formatBytes(file.size)}</span>
            </div>

            {phase === "uploading" ? (
              <>
                <ProgressBar value={percent} label="Upload progress" />
                <p className="muted" style={{ marginTop: 6, fontSize: 13 }}>
                  {formatBytes(progress.loaded)} of {formatBytes(progress.total)} ·{" "}
                  {Math.round(percent)}%
                </p>
              </>
            ) : null}

            {phase === "processing" ? (
              <ProgressBar indeterminate label="Processing" />
            ) : null}

            <ol className="stage-list">
              {STAGES.map((stage) => {
                const state = stageStateFor(phase, status, stage.key);
                return (
                  <li key={stage.key} className={cn("stage", state)}>
                    {state === "done" ? (
                      <CheckCircle2 size={16} />
                    ) : state === "failed" ? (
                      <CircleAlert size={16} />
                    ) : state === "active" ? (
                      <Loader2 size={16} />
                    ) : (
                      <span style={{ width: 16 }} />
                    )}
                    {stage.label}
                  </li>
                );
              })}
            </ol>

            {phase === "uploading" ? (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                style={{ marginTop: 12 }}
                onClick={() => abortRef.current?.abort()}
              >
                Cancel upload
              </button>
            ) : null}
          </div>
        ) : null}

        {error ? (
          <div style={{ marginTop: 16 }}>
            <Alert tone="danger" title="Upload failed">
              {error}
            </Alert>
            {datasetId ? (
              <a className="btn btn-secondary btn-sm" href={`/datasets/${datasetId}`}>
                Open dataset
              </a>
            ) : null}
          </div>
        ) : null}
      </Panel>

      <Panel>
        <h2>What happens next</h2>
        <ol className="actions-list" style={{ marginTop: 12 }}>
          <li>The file streams to storage under a generated UUID key — the original filename is kept only as metadata.</li>
          <li>The schema is validated and reported as PASS, WARNING or BLOCKED.</li>
          <li>A full profile is generated: types, nulls, duplicates, statistics and outliers.</li>
          <li>KPI candidates are detected so you can configure the analysis.</li>
        </ol>
      </Panel>
    </div>
  );
}
