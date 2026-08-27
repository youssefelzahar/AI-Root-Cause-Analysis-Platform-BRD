"use client";

import { Send, Sparkles } from "lucide-react";
import { useState } from "react";

import { Alert, Panel, PanelHeading } from "@/components/ui";
import { analyze } from "@/lib/api/ai";
import { ApiError, toUserMessage } from "@/lib/api/errors";
import type { AnalystTurn } from "@/types/ai";

import { AnalystAnswer } from "./analyst-answer";
import { AnalystBlocked, isBlockedCode } from "./analyst-blocked";
import { AnalystSteps } from "./analyst-steps";

/**
 * The one client island on this surface.
 *
 * Shaped like `sql-workspace`, which is the closest thing the app already has to a
 * composer: a textarea, Ctrl/⌘+Enter to submit, three pieces of state, one
 * try/catch/finally, and response panels appended below rather than a scrolling
 * transcript pinned to the viewport.
 *
 * The conversation lives in component state and nowhere else. §24 asks for
 * short-term context only, and the durable artifact is the investigation each
 * answer links to - so a reload losing the thread costs nothing, while a stored
 * transcript would be a second record of the same analysis, free to drift from it.
 */

const SUGGESTIONS = [
  "Why did it change?",
  "Which segment contributed most?",
  "Was the latest period unusual?",
] as const;

let turnCounter = 0;

export function AnalystWorkspace({
  datasetId,
  kpiName,
}: {
  datasetId: string;
  kpiName: string | null;
}) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<AnalystTurn[]>([]);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blockedCode, setBlockedCode] = useState<string | null>(null);

  /**
   * The investigation the last successful answer used, so a follow-up continues it
   * instead of recomputing. Read from the thread rather than held separately -
   * one source of truth for what "the current investigation" means.
   */
  const investigationId =
    [...turns].reverse().find((turn) => turn.response?.investigation_id)?.response
      ?.investigation_id ?? null;

  const ask = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;

    setAsking(true);
    setError(null);
    setBlockedCode(null);
    try {
      const response = await analyze({
        question: trimmed,
        dataset_id: datasetId,
        investigation_id: investigationId,
      });
      turnCounter += 1;
      setTurns((current) => [
        ...current,
        { id: `turn-${turnCounter}`, question: trimmed, response, error: null },
      ]);
      setQuestion("");
    } catch (askError) {
      // A blocked code has a next step, so it is rendered as guidance rather than
      // as a failure. Everything else is a plain error message.
      const code = askError instanceof ApiError ? askError.code : undefined;
      if (isBlockedCode(code)) {
        setBlockedCode(code!);
      } else {
        setError(toUserMessage(askError));
      }
    } finally {
      setAsking(false);
    }
  };

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Panel>
        <PanelHeading
          eyebrow="Ask"
          title={kpiName ? `Ask about ${kpiName}` : "Ask a question"}
        />
        <textarea
          className="textarea"
          value={question}
          rows={3}
          spellCheck
          aria-label="Your question"
          placeholder={
            kpiName
              ? `Why did ${kpiName} change?`
              : "Why did the KPI change last period?"
          }
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            // Ctrl/Cmd+Enter submits, matching the SQL editor. Plain Enter inserts
            // a newline: a question is prose and a stray Enter should not send it.
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
              event.preventDefault();
              void ask(question);
            }
          }}
        />

        <div className="form-actions" style={{ marginTop: 12 }}>
          <button
            type="button"
            className="btn"
            onClick={() => void ask(question)}
            disabled={asking || !question.trim()}
          >
            <Send size={16} aria-hidden="true" />
            {asking ? "Analysing…" : "Ask"}
          </button>
          <span className="muted" style={{ alignSelf: "center", fontSize: 12 }}>
            Ctrl/⌘ + Enter
          </span>
        </div>

        {turns.length === 0 && !asking ? (
          <div className="analyst-suggestions">
            <span className="muted">Try:</span>
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => void ask(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        ) : null}

        {blockedCode ? (
          <div style={{ marginTop: 16 }}>
            <AnalystBlocked code={blockedCode} />
          </div>
        ) : null}

        {error ? (
          <div style={{ marginTop: 16 }}>
            <Alert tone="danger" title="The question could not be answered">
              {error}
            </Alert>
          </div>
        ) : null}
      </Panel>

      {asking ? (
        <Panel>
          <PanelHeading eyebrow="Working" title="Running the analysis" />
          <AnalystSteps steps={[]} running />
          <p className="muted" style={{ marginTop: 12 }}>
            The first question on a dataset scans the file; later ones reuse the
            investigation and return quickly.
          </p>
        </Panel>
      ) : null}

      {/* Newest first: an answer that has just arrived should not require
          scrolling past the ones already read. */}
      {[...turns].reverse().map((turn) => (
        <div key={turn.id} className="analyst-turn">
          <p className="analyst-question">
            <Sparkles size={14} aria-hidden="true" /> {turn.question}
          </p>
          {turn.response ? <AnalystAnswer response={turn.response} /> : null}
        </div>
      ))}
    </div>
  );
}
