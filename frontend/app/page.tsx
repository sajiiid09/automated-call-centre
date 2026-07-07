"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { PhoneCall, Megaphone, Users, Timer } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { CallWidget } from "@/components/call-widget";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Call, Stats, getStats, listCalls } from "@/lib/api";

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [recent, setRecent] = useState<Call[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [s, calls] = await Promise.all([getStats(), listCalls()]);
      setStats(s);
      setRecent(calls.slice(0, 8));
    } catch (e) {
      toast.error(`Failed to load dashboard: ${(e as Error).message}`);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async fetch, setState after await
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  const cards = [
    { label: "Total calls", icon: PhoneCall, value: stats?.total_calls ?? "—" },
    {
      label: "Active campaigns",
      icon: Megaphone,
      value: stats?.active_campaigns ?? "—",
    },
    { label: "Contacts", icon: Users, value: stats?.total_contacts ?? "—" },
    {
      label: "Avg call duration",
      icon: Timer,
      value: stats ? formatDuration(stats.avg_duration_seconds) : "—",
    },
  ];

  return (
    <div>
      <div className="flex items-start justify-between">
        <PageHeader
          title="Dashboard"
          description="Overview of calls, campaigns, and contacts."
        />
        {/* simulates an inbound caller ringing the agent */}
        <CallWidget context={{ direction: "inbound" }} onCallEnded={refresh} />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map(({ label, icon: Icon, value }) => (
          <Card key={label}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                {label}
              </CardTitle>
              <Icon className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-semibold">{value}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      {stats && Object.keys(stats.dispositions).length > 0 && (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle className="text-base">Dispositions</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {Object.entries(stats.dispositions).map(([d, n]) => (
              <Badge key={d} variant="secondary" className="text-sm">
                {d}: {n}
              </Badge>
            ))}
          </CardContent>
        </Card>
      )}

      <Card className="mt-6">
        <CardHeader>
          <CardTitle className="text-base">Recent calls</CardTitle>
        </CardHeader>
        <CardContent>
          {recent.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No calls yet — click “Call agent” above to try the voice agent.
            </p>
          ) : (
            <ul className="divide-y">
              {recent.map((c) => (
                <li key={c.id} className="flex items-center gap-3 py-2 text-sm">
                  <Badge variant="secondary">{c.direction}</Badge>
                  <Link href={`/calls/${c.id}`} className="hover:underline">
                    {c.contact_name ?? "Unknown caller"}
                  </Link>
                  <span className="text-muted-foreground">
                    {c.started_at ? new Date(c.started_at).toLocaleString() : ""}
                  </span>
                  <span className="ml-auto text-muted-foreground">
                    {c.disposition ?? c.status} · {formatDuration(c.duration_seconds)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
