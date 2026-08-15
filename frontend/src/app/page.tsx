import { redirect } from "next/navigation";

export default function Home() {
  // Phase 1 is the data foundation, so the dataset list is the entry point.
  redirect("/datasets");
}
