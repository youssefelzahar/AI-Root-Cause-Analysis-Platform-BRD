import Link from "next/link";
import type { ReactNode } from "react";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  backHref,
  backLabel,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
  backHref?: string;
  backLabel?: string;
}) {
  return (
    <header className="topbar">
      <div>
        {backHref ? (
          <Link className="back-link" href={backHref}>
            ← {backLabel ?? "Back"}
          </Link>
        ) : null}
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        {description ? <p className="subtitle">{description}</p> : null}
      </div>
      {actions ? <div className="form-actions">{actions}</div> : null}
    </header>
  );
}
