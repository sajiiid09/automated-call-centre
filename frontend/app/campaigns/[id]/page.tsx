"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Square } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CallWidget } from "@/components/call-widget";
import {
  CampaignDetail,
  getCampaign,
  startCampaign,
  stopCampaign,
} from "@/lib/api";
import { dispositionVariant, formatDisposition } from "@/lib/dispositions";

const contactStatusVariant: Record<string, "secondary" | "default" | "outline" | "destructive"> = {
  pending: "secondary",
  calling: "default",
  done: "outline",
  failed: "destructive",
};

/** What the live PSTN leg is doing right now. */
function callStatusLabel(callStatus: string | null, rowStatus: string): string {
  if (rowStatus === "pending") return "Queued";
  switch (callStatus) {
    case "initiated":
      return "Placing call…";
    case "ringing":
      return "Ringing…";
    case "in_progress":
      return "In progress";
    default:
      return "Connecting…";
  }
}

export default function CampaignDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [campaign, setCampaign] = useState<CampaignDetail | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setCampaign(await getCampaign(id));
    } catch (e) {
      toast.error(`Failed to load campaign: ${(e as Error).message}`);
    }
  }, [id]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async fetch, setState after await
    refresh();
    const interval = setInterval(refresh, 4000);
    return () => clearInterval(interval);
  }, [refresh]);

  async function handleStart(confirmReal = false) {
    try {
      await startCampaign(id, confirmReal ? { confirm_real: true } : undefined);
      toast.success(
        confirmReal
          ? "Campaign started — dialing contacts now"
          : "Campaign started — answer each contact below to simulate the call",
      );
      setConfirmOpen(false);
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function handleStop() {
    try {
      await stopCampaign(id);
      toast.success("Campaign stopped");
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  if (!campaign) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  const running = campaign.status === "running";
  const realDialing = campaign.dialing_mode === "twilio";
  // sequential dialer: only the first non-finished contact is actionable
  const nextRow = running
    ? campaign.contact_rows.find((r) => r.status === "pending" || r.status === "calling")
    : undefined;
  const undialable = campaign.contact_rows.filter((r) => r.dialable === false).length;

  return (
    <div>
      <Link
        href="/campaigns"
        className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> All campaigns
      </Link>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{campaign.name}</h1>
            <Badge>{campaign.status}</Badge>
          </div>
          {campaign.goal && (
            <p className="mt-1 text-sm text-muted-foreground">{campaign.goal}</p>
          )}
        </div>
        <div className="flex gap-2">
          {campaign.status === "draft" || campaign.status === "stopped" ? (
            <Button onClick={() => (realDialing ? setConfirmOpen(true) : handleStart())}>
              {realDialing ? "Start real campaign" : "Start campaign"}
            </Button>
          ) : running ? (
            <Button variant="destructive" onClick={handleStop}>
              <Square className="mr-1 h-4 w-4" /> Stop
            </Button>
          ) : null}
        </div>
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Place real phone calls?</DialogTitle>
            <DialogDescription>
              This dials {campaign.total_contacts} contact
              {campaign.total_contacts === 1 ? "" : "s"} one at a time over the phone
              network. Calls cost money and reach real people.
              {undialable > 0 &&
                ` ${undialable} of them are not on the outbound allowlist and will be skipped.`}
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-40 overflow-y-auto text-sm">
            <ul className="space-y-1">
              {campaign.contact_rows.map((r) => (
                <li key={r.contact.id} className="flex items-center justify-between gap-2">
                  <span>{r.contact.name}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {r.contact.phone}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => handleStart(true)}>
              Dial {campaign.total_contacts} contact
              {campaign.total_contacts === 1 ? "" : "s"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {running && nextRow && (
        <Card className="mb-6 border-primary/50">
          <CardHeader>
            <CardTitle className="text-sm">
              {realDialing ? "Now dialing" : "Next call"}: {nextRow.contact.name} (
              {nextRow.contact.phone})
            </CardTitle>
          </CardHeader>
          <CardContent className="flex items-center justify-between gap-4">
            {realDialing ? (
              <>
                <p className="max-w-md text-sm text-muted-foreground">
                  The agent is calling this contact over the phone network. The
                  outcome is tagged automatically and the queue advances on its own.
                </p>
                <Badge variant={nextRow.call_status === "in_progress" ? "default" : "secondary"}>
                  {callStatusLabel(nextRow.call_status, nextRow.status)}
                </Badge>
              </>
            ) : (
              <>
                <p className="max-w-md text-sm text-muted-foreground">
                  Real dialing is off, so answer this call in the browser and play
                  the contact — the agent follows the campaign script and the
                  outcome is tagged automatically.
                </p>
                <CallWidget
                  key={nextRow.contact.id}
                  label={`Answer as ${nextRow.contact.name}`}
                  context={{
                    direction: "outbound",
                    contact_id: nextRow.contact.id,
                    campaign_id: campaign.id,
                  }}
                  onCallEnded={refresh}
                />
              </>
            )}
          </CardContent>
        </Card>
      )}

      {campaign.script_prompt && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="text-sm">Agent script</CardTitle>
          </CardHeader>
          <CardContent className="whitespace-pre-wrap text-sm text-muted-foreground">
            {campaign.script_prompt}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Contacts ({campaign.called_contacts}/{campaign.total_contacts} called)
          </CardTitle>
        </CardHeader>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Phone</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Disposition</TableHead>
              <TableHead className="w-32" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {campaign.contact_rows.map((row) => (
              <TableRow key={row.contact.id}>
                <TableCell className="font-medium">{row.contact.name}</TableCell>
                <TableCell className="font-mono text-sm">
                  {row.contact.phone}
                  {row.dialable === false && (
                    <Badge variant="outline" className="ml-2 font-sans text-xs">
                      not allowlisted
                    </Badge>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant={contactStatusVariant[row.status] ?? "secondary"}>
                    {row.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {row.disposition ? (
                    <Badge variant={dispositionVariant(row.disposition)}>
                      {formatDisposition(row.disposition)}
                    </Badge>
                  ) : (
                    "—"
                  )}
                  {row.disposition_summary && (
                    <span className="block max-w-xs truncate text-xs">
                      {row.disposition_summary}
                    </span>
                  )}
                </TableCell>
                <TableCell>
                  {row.call_id && (
                    <Link
                      href={`/calls/${row.call_id}`}
                      className="text-sm hover:underline"
                    >
                      Transcript
                    </Link>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
