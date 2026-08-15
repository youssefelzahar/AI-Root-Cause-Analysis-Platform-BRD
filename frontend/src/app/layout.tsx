import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Sidebar } from "@/components/layout/sidebar";

import "./globals.css";

export const metadata: Metadata = {
  title: "AI RCA Platform",
  description:
    "Upload data, profile it, validate its schema, and configure a KPI for root cause analysis.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <Sidebar />
          <main className="workspace">{children}</main>
        </div>
      </body>
    </html>
  );
}
