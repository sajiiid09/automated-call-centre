import { Megaphone } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";

export default function CampaignsPage() {
  return (
    <div>
      <div className="flex items-start justify-between">
        <PageHeader
          title="Campaigns"
          description="Outbound calling campaigns with goals, scripts, and progress."
        />
        <Button disabled>New campaign</Button>
      </div>
      <EmptyState
        icon={Megaphone}
        title="No campaigns yet"
        description="Create a campaign, pick contacts, and the AI agent will call them and record outcomes."
        phase="Phase 2"
      />
    </div>
  );
}
