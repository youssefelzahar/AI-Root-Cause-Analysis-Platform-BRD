import { demoRequest, fallbackResult } from "@/lib/demo-data";
import type { InvestigationResult } from "@/types/rca";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function runDemoInvestigation(): Promise<InvestigationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/investigations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(demoRequest),
      cache: "no-store",
    });

    if (!response.ok) {
      throw new Error(`API returned ${response.status}`);
    }

    return (await response.json()) as InvestigationResult;
  } catch {
    return fallbackResult;
  }
}
