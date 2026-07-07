"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Phone } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Call, listCalls } from "@/lib/api";

export function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

const statusVariant: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  in_progress: "default",
  completed: "outline",
  failed: "destructive",
};

export default function CallsPage() {
  const [calls, setCalls] = useState<Call[]>([]);

  const refresh = useCallback(async () => {
    try {
      setCalls(await listCalls());
    } catch (e) {
      toast.error(`Failed to load calls: ${(e as Error).message}`);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async fetch, setState after await
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  return (
    <div>
      <PageHeader
        title="Calls"
        description="Inbound and outbound call log with transcripts and dispositions."
      />
      {calls.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
            <Phone className="h-10 w-10 text-muted-foreground/50" />
            <p className="text-base font-medium">No calls yet</p>
            <p className="max-w-sm text-sm text-muted-foreground">
              Start a web call from the Dashboard, or run a campaign — every call
              and its transcript lands here.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>When</TableHead>
                <TableHead>Direction</TableHead>
                <TableHead>Contact</TableHead>
                <TableHead>Campaign</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Disposition</TableHead>
                <TableHead>Duration</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {calls.map((c) => (
                <TableRow key={c.id}>
                  <TableCell>
                    <Link href={`/calls/${c.id}`} className="hover:underline">
                      {c.started_at
                        ? new Date(c.started_at).toLocaleString()
                        : "—"}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{c.direction}</Badge>
                  </TableCell>
                  <TableCell>{c.contact_name ?? "Unknown"}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {c.campaign_name ?? "—"}
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusVariant[c.status] ?? "secondary"}>
                      {c.status}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {c.disposition ?? "—"}
                  </TableCell>
                  <TableCell>{formatDuration(c.duration_seconds)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
