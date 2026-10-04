import { EvalDashboard } from "@/components/app/eval-dashboard";

export const metadata = { title: "Quality" };
export const dynamic = "force-dynamic";

export default function EvalPage() {
  return <EvalDashboard />;
}
