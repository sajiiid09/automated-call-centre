"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { CallDetail, getCall } from "@/lib/api";

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export default function CallDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [call, setCall] = useState<CallDetail | null>(null);

  const refresh = useCallback(async () => {
    try {
      setCall(await getCall(id));
    } catch (e) {
      toast.error(`Failed to load call: ${(e as Error).message}`);
    }
  }, [id]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async fetch, setState after await
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (call?.status === "in_progress") {
      const interval = setInterval(refresh, 3000);
      return () => clearInterval(interval);
    }
  }, [call?.status, refresh]);

  if (!call) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="max-w-3xl">
      <Link
        href="/calls"
        className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> All calls
      </Link>

      <div className="mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            {call.direction === "inbound" ? "Inbound call" : "Outbound call"}
            {call.contact_name ? ` · ${call.contact_name}` : ""}
          </h1>
          <Badge>{call.status}</Badge>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {call.started_at ? new Date(call.started_at).toLocaleString() : ""}
          {" · "}
          {formatDuration(call.duration_seconds)}
          {call.campaign_name ? ` · Campaign: ${call.campaign_name}` : ""}
        </p>
      </div>

      {call.disposition && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="text-sm">Disposition</CardTitle>
          </CardHeader>
          <CardContent>
            <Badge className="mb-2">{call.disposition}</Badge>
            {call.disposition_summary && (
              <p className="text-sm text-muted-foreground">
                {call.disposition_summary}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Transcript</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {call.turns.length === 0 && (
            <p className="text-sm text-muted-foreground">No transcript recorded.</p>
          )}
          {call.turns.map((turn) => (
            <div
              key={turn.id}
              className={cn(
                "flex",
                turn.role === "agent" ? "justify-start" : "justify-end"
              )}
            >
              <div
                className={cn(
                  "max-w-[80%] rounded-lg px-3 py-2 text-sm",
                  turn.role === "agent"
                    ? "bg-muted text-foreground"
                    : "bg-primary text-primary-foreground"
                )}
              >
                <p className="mb-0.5 text-xs font-medium opacity-70">
                  {turn.role === "agent" ? "Agent" : "Caller"}
                </p>
                {turn.content}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
