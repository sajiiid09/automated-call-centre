"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
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
import { CampaignDetail, getCampaign } from "@/lib/api";

const contactStatusVariant: Record<string, "secondary" | "default" | "outline" | "destructive"> = {
  pending: "secondary",
  calling: "default",
  done: "outline",
  failed: "destructive",
};

export default function CampaignDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [campaign, setCampaign] = useState<CampaignDetail | null>(null);

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
  }, [refresh]);

  if (!campaign) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

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
          {/* Start/Stop wired in Stage C */}
          <Button disabled>Start campaign</Button>
        </div>
      </div>

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
                <TableCell className="font-mono text-sm">{row.contact.phone}</TableCell>
                <TableCell>
                  <Badge variant={contactStatusVariant[row.status] ?? "secondary"}>
                    {row.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {row.disposition ?? "—"}
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
