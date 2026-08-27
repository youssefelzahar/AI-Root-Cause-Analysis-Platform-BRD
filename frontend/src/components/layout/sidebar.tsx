"use client";

import { Activity, Brain, Database, LineChart, Server, Sparkles } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/datasets", label: "Datasets", icon: Database },
  { href: "/sql", label: "SQL Server", icon: Server },
  // Anomalies before investigations: "is this unusual?" is the question that
  // comes before "why did it change?".
  { href: "/anomalies", label: "Anomalies", icon: Activity },
  { href: "/investigations", label: "Investigations", icon: LineChart },
  // Last because it is the way in for someone who does not yet know which of the
  // three surfaces above answers their question.
  { href: "/ai-analyst", label: "Ask", icon: Sparkles },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      <div className="brand">
        <Brain size={26} />
        <div>
          <strong>AI RCA</strong>
          <span>Data Foundation</span>
        </div>
      </div>

      <nav className="side-nav" aria-label="Main">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link key={href} href={href} aria-current={active ? "page" : undefined}>
              <Icon size={17} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Phase 1 ships without authentication - say so rather than showing
          an account menu that does nothing. */}
      <p className="side-note">
        Phase 1 — Data Foundation. No authentication is configured; do not expose this deployment
        publicly.
      </p>
    </aside>
  );
}
