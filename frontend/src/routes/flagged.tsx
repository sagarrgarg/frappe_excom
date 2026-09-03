import { Lock } from "lucide-react";
import { EmptyState, Button } from "../components/primitives";
import { PageFrame } from "../components/shell/PageFrame";
import { useNavigate } from "react-router-dom";

/** P3 surfaces are registered so links and ⌘K entries exist, but render a flag notice until CRM data lands. */
export function FlaggedRoute({ name }: { name: string }) {
  const navigate = useNavigate();
  return (
    <PageFrame title={name} icon={<Lock />}>
      <EmptyState
        icon={<Lock />}
        title={`${name} arrives in P3`}
        hint="This surface needs pipeline_stage, next_action_at and gate flags from the native CRM phase. The route is registered so nothing breaks when it is unhidden."
        action={<Button size="sm" onClick={() => navigate("/inbox/today")}>Open Today's actions view</Button>}
      />
    </PageFrame>
  );
}
