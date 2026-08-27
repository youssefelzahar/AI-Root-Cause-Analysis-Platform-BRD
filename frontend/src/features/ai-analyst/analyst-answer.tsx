import { ExternalLink, FileSearch, Info, Sparkles } from "lucide-react";
import Link from "next/link";

import { Alert, Panel, PanelHeading, Pill } from "@/components/ui";
import { cn, formatPercent, formatStat } from "@/lib/format";
import type { AnalystDriver, AnalyzeResponse } from "@/types/ai";

import { AnalystSteps } from "./analyst-steps";

/**
 * One answered question.
 *
 * A server component: it renders a response the island already has, and holds no
 * state of its own. Only the island above it needs to be a client component.
 *
 * The ordering is the argument. Prose first because that is what was asked for,
 * then the drivers as a table because a number in a table can be checked and a
 * number in a sentence cannot, then the evidence links, then the caveats. A
 * reader who trusts the answer stops after the first paragraph; a reader who does
 * not can follow it all the way down to the SQL.
 */

const CLASSIFICATION_TONE: Record<string, "success" | "warning" | "danger" | "neutral" | "info"> = {
  primary: "danger",
  secondary: "warning",
  offsetting: "info",
  immaterial: "neutral",
  residual: "neutral",
};

function DriverRow({ driver }: { driver: AnalystDriver }) {
  const change = driver.absolute_change ?? 0;
  return (
    <tr>
      <td>
        <span className="muted">{driver.dimension}</span>{" "}
        <strong>{driver.value || "(no value)"}</strong>
        {/* NEW and GONE are defined on rows, not on a value reaching zero, so
            they are stated rather than implied by a -100% that never appears. */}
        {driver.is_lost_segment ? <span className="muted"> — gone</span> : null}
        {driver.is_new_segment ? <span className="muted"> — new</span> : null}
      </td>
      <td className={cn("numeric", change < 0 ? "negative-text" : "positive-text")}>
        {formatStat(driver.absolute_change)}
      </td>
      <td className="numeric">
        {driver.contribution_percentage === null
          ? "—"
          : formatPercent(driver.contribution_percentage, 0)}
      </td>
      <td>
        <Pill tone={CLASSIFICATION_TONE[driver.classification] ?? "neutral"}>
          {driver.classification}
        </Pill>
      </td>
      <td>
        {driver.evidence_id ? (
          <span className="muted analyst-evidence-ref">
            <FileSearch size={13} aria-hidden="true" /> evidence
          </span>
        ) : null}
      </td>
    </tr>
  );
}

function DriverTable({ drivers, caption }: { drivers: AnalystDriver[]; caption: string }) {
  if (drivers.length === 0) return null;
  return (
    <div className="table-wrap" style={{ marginTop: 12 }}>
      <table className="data-table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th>Segment</th>
            <th className="numeric">Change</th>
            <th className="numeric">Share</th>
            <th>Role</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {drivers.map((driver) => (
            <DriverRow key={`${driver.dimension}-${driver.value}`} driver={driver} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AnalystAnswer({ response }: { response: AnalyzeResponse }) {
  const { evidence, clarification } = response;

  // A clarification is the whole response: nothing ran, so there is nothing else
  // to show. Rendering an empty driver table under it would imply otherwise.
  if (response.status === "clarification" && clarification) {
    return (
      <Panel>
        <PanelHeading eyebrow="Needs a decision" title="Which one did you mean?" />
        <Alert tone="info">{clarification.message}</Alert>
        {clarification.options.length > 0 ? (
          <ul className="analyst-options">
            {clarification.options.map((option) => (
              <li key={option}>{option}</li>
            ))}
          </ul>
        ) : null}
      </Panel>
    );
  }

  return (
    <Panel>
      <PanelHeading
        eyebrow="Answer"
        title={evidence?.kpi_name ?? "Analysis"}
        actions={
          response.investigation_id ? (
            <Link
              className="btn btn-secondary btn-sm"
              href={`/investigations/${response.investigation_id}`}
            >
              <ExternalLink size={15} aria-hidden="true" /> Full investigation
            </Link>
          ) : undefined
        }
      />

      {response.answer ? <p className="analyst-answer">{response.answer}</p> : null}

      {/* Never presented as the model's work when it is not. */}
      {response.answer_is_template ? (
        <p className="muted analyst-provenance">
          <Info size={13} aria-hidden="true" /> Written from the evidence rather than
          generated.
        </p>
      ) : (
        <p className="muted analyst-provenance">
          <Sparkles size={13} aria-hidden="true" /> Written by {response.model ?? "the model"} from
          the evidence below. Every figure was checked against it.
        </p>
      )}

      {evidence ? (
        <dl className="analyst-headline">
          <div>
            <dt>Previous</dt>
            <dd>{formatStat(evidence.previous_value)}</dd>
          </div>
          <div>
            <dt>Current</dt>
            <dd>{formatStat(evidence.current_value)}</dd>
          </div>
          <div>
            <dt>Change</dt>
            <dd
              className={cn(
                (evidence.absolute_change ?? 0) < 0 ? "negative-text" : "positive-text",
              )}
            >
              {formatStat(evidence.absolute_change)}
              {evidence.percentage_change === null
                ? ""
                : ` (${formatPercent(evidence.percentage_change, 1)})`}
            </dd>
          </div>
          {evidence.previous_period && evidence.current_period ? (
            <div className="analyst-periods">
              <dt>Compared</dt>
              <dd className="muted">
                {evidence.current_period} against {evidence.previous_period}
              </dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      <DriverTable drivers={response.drivers} caption="Contributing segments" />

      {response.offsetting_factors.length > 0 ? (
        <>
          <p className="eyebrow" style={{ marginTop: 16 }}>
            Moved the other way
          </p>
          <DriverTable
            drivers={response.offsetting_factors}
            caption="Segments that moved against the KPI"
          />
        </>
      ) : null}

      {evidence && evidence.drill_path.length > 0 ? (
        <p className="analyst-drill">
          <span className="eyebrow">Concentrates in</span>{" "}
          {evidence.drill_path.join(" → ")}
          {evidence.drill_stop_reason ? (
            <span className="muted"> — not broken down further: {evidence.drill_stop_reason}</span>
          ) : null}
        </p>
      ) : null}

      <AnalystSteps steps={response.steps} running={false} />

      {/* Assumptions before limitations: an assumption changes how to read the
          answer above, a limitation only bounds it. */}
      {response.assumptions.length > 0 ? (
        <div style={{ marginTop: 16 }}>
          {/* A raw div rather than <Alert>, which wraps children in a <p> and so
              cannot hold a list. Same workaround as the investigation page. */}
          <div className="alert alert-info">
            <strong>What I assumed</strong>
            <ul className="issue-list">
              {response.assumptions.map((note) => (
                <li key={note} className="issue info">
                  {note}
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}

      {response.limitations.length > 0 ? (
        <div style={{ marginTop: 12 }}>
          <div className="alert alert-warning">
            <strong>What this does not cover</strong>
            <ul className="issue-list">
              {response.limitations.map((note) => (
                <li key={note} className="issue warning">
                  {note}
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}

      <p className="muted analyst-footnote">
        These are contributions to a measured change, not proven causes.
        {evidence?.attribution_basis
          ? ` Shares are computed on the ${evidence.attribution_basis.replace(/_/g, " ")} basis.`
          : ""}
        {evidence?.evidence_quality
          ? ` Evidence quality: ${evidence.evidence_quality}.`
          : ""}
      </p>
    </Panel>
  );
}
