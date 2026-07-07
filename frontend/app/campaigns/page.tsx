"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Megaphone } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Campaign,
  Contact,
  createCampaign,
  listCampaigns,
  listContacts,
} from "@/lib/api";

const statusVariant: Record<Campaign["status"], "secondary" | "default" | "outline" | "destructive"> = {
  draft: "secondary",
  running: "default",
  stopped: "destructive",
  completed: "outline",
};

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ name: "", goal: "", script_prompt: "" });
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    try {
      setCampaigns(await listCampaigns());
    } catch (e) {
      toast.error(`Failed to load campaigns: ${(e as Error).message}`);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async fetch, setState after await
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (dialogOpen) {
      listContacts().then(setContacts).catch(() => toast.error("Failed to load contacts"));
    }
  }, [dialogOpen]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleCreate() {
    if (!form.name.trim()) {
      toast.error("Campaign name is required");
      return;
    }
    if (selected.size === 0) {
      toast.error("Select at least one contact");
      return;
    }
    setSaving(true);
    try {
      await createCampaign({
        name: form.name.trim(),
        goal: form.goal.trim() || undefined,
        script_prompt: form.script_prompt.trim() || undefined,
        contact_ids: [...selected],
      });
      toast.success("Campaign created");
      setDialogOpen(false);
      setForm({ name: "", goal: "", script_prompt: "" });
      setSelected(new Set());
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="flex items-start justify-between">
        <PageHeader
          title="Campaigns"
          description="Outbound calling campaigns with goals, scripts, and progress."
        />
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger asChild>
            <Button>New campaign</Button>
          </DialogTrigger>
          <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>New campaign</DialogTitle>
            </DialogHeader>
            <div className="grid gap-4 py-2">
              <div className="grid gap-2">
                <Label htmlFor="cname">Name</Label>
                <Input
                  id="cname"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="goal">Goal</Label>
                <Input
                  id="goal"
                  placeholder="e.g. Book product demos"
                  value={form.goal}
                  onChange={(e) => setForm({ ...form, goal: e.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="script">Agent script / instructions</Label>
                <Textarea
                  id="script"
                  rows={4}
                  placeholder="What should the agent say and aim for on each call?"
                  value={form.script_prompt}
                  onChange={(e) => setForm({ ...form, script_prompt: e.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label>Contacts ({selected.size} selected)</Label>
                <div className="max-h-48 space-y-1 overflow-y-auto rounded-md border p-2">
                  {contacts.length === 0 && (
                    <p className="p-2 text-sm text-muted-foreground">
                      No contacts yet — add some on the Contacts page first.
                    </p>
                  )}
                  {contacts.map((c) => (
                    <label
                      key={c.id}
                      className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-accent"
                    >
                      <Checkbox
                        checked={selected.has(c.id)}
                        onCheckedChange={() => toggle(c.id)}
                      />
                      <span className="text-sm">{c.name}</span>
                      <span className="ml-auto font-mono text-xs text-muted-foreground">
                        {c.phone}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            </div>
            <DialogFooter>
              <Button onClick={handleCreate} disabled={saving}>
                {saving ? "Creating…" : "Create campaign"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {campaigns.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
            <Megaphone className="h-10 w-10 text-muted-foreground/50" />
            <p className="text-base font-medium">No campaigns yet</p>
            <p className="max-w-sm text-sm text-muted-foreground">
              Create a campaign, pick contacts, and the AI agent will call them and
              record outcomes.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Goal</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Progress</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {campaigns.map((c) => (
                <TableRow key={c.id}>
                  <TableCell className="font-medium">
                    <Link href={`/campaigns/${c.id}`} className="hover:underline">
                      {c.name}
                    </Link>
                  </TableCell>
                  <TableCell className="max-w-xs truncate text-muted-foreground">
                    {c.goal ?? ""}
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusVariant[c.status]}>{c.status}</Badge>
                  </TableCell>
                  <TableCell>
                    {c.called_contacts}/{c.total_contacts} called
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
