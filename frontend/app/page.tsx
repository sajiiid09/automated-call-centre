import { PhoneCall, Megaphone, Users, Timer } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const stats = [
  { label: "Total calls", icon: PhoneCall },
  { label: "Active campaigns", icon: Megaphone },
  { label: "Contacts", icon: Users },
  { label: "Avg call duration", icon: Timer },
];

export default function DashboardPage() {
  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="Overview of calls, campaigns, and contacts."
      />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map(({ label, icon: Icon }) => (
          <Card key={label}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                {label}
              </CardTitle>
              <Icon className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-semibold">—</div>
              <p className="text-xs text-muted-foreground">No data yet</p>
            </CardContent>
          </Card>
        ))}
      </div>
      <div className="mt-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent calls</CardTitle>
          </CardHeader>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Call activity will appear here once the voice agent is live
            (Phase 3).
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
