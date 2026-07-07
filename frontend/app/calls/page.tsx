import { Phone } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { EmptyState } from "@/components/empty-state";

export default function CallsPage() {
  return (
    <div>
      <PageHeader
        title="Calls"
        description="Inbound and outbound call log with transcripts and dispositions."
      />
      <EmptyState
        icon={Phone}
        title="No calls yet"
        description="Once the voice agent is connected to your Twilio number, every call and its transcript lands here."
        phase="Phase 3"
      />
    </div>
  );
}
