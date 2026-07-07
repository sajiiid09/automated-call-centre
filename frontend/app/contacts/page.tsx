import { Users } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";

export default function ContactsPage() {
  return (
    <div>
      <div className="flex items-start justify-between">
        <PageHeader
          title="Contacts"
          description="People the agent can call, with phone numbers and call history."
        />
        <div className="flex gap-2">
          <Button variant="outline" disabled>
            Import CSV
          </Button>
          <Button disabled>Add contact</Button>
        </div>
      </div>
      <EmptyState
        icon={Users}
        title="No contacts yet"
        description="Add contacts one by one or import a CSV. Each contact keeps its full call history."
        phase="Phase 2"
      />
    </div>
  );
}
