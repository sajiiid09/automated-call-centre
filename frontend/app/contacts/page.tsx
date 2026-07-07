"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Users, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Card, CardContent } from "@/components/ui/card";
import {
  Contact,
  createContact,
  deleteContact,
  importContacts,
  listContacts,
} from "@/lib/api";

export default function ContactsPage() {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [search, setSearch] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState({ name: "", phone: "", notes: "" });
  const [saving, setSaving] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async (q = "") => {
    try {
      setContacts(await listContacts(q));
    } catch (e) {
      toast.error(`Failed to load contacts: ${(e as Error).message}`);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => refresh(search), 250);
    return () => clearTimeout(t);
  }, [search, refresh]);

  async function handleCreate() {
    if (!form.name.trim() || !form.phone.trim()) {
      toast.error("Name and phone are required");
      return;
    }
    setSaving(true);
    try {
      await createContact({
        name: form.name.trim(),
        phone: form.phone.trim(),
        notes: form.notes.trim() || undefined,
      });
      toast.success("Contact added");
      setDialogOpen(false);
      setForm({ name: "", phone: "", notes: "" });
      refresh(search);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(contact: Contact) {
    try {
      await deleteContact(contact.id);
      toast.success(`Deleted ${contact.name}`);
      refresh(search);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function handleImport(file: File) {
    try {
      const result = await importContacts(file);
      toast.success(
        `Imported ${result.imported}, skipped ${result.skipped} duplicate(s)` +
          (result.errors.length ? `, ${result.errors.length} error(s)` : "")
      );
      refresh(search);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  return (
    <div>
      <div className="flex items-start justify-between">
        <PageHeader
          title="Contacts"
          description="People the agent can call, with phone numbers and call history."
        />
        <div className="flex gap-2">
          <input
            ref={fileInput}
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleImport(f);
              e.target.value = "";
            }}
          />
          <Button variant="outline" onClick={() => fileInput.current?.click()}>
            <Upload className="mr-1 h-4 w-4" /> Import CSV
          </Button>
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <DialogTrigger asChild>
              <Button>Add contact</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Add contact</DialogTitle>
              </DialogHeader>
              <div className="grid gap-4 py-2">
                <div className="grid gap-2">
                  <Label htmlFor="name">Name</Label>
                  <Input
                    id="name"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="phone">Phone (E.164, e.g. +447700900123)</Label>
                  <Input
                    id="phone"
                    value={form.phone}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="notes">Notes</Label>
                  <Textarea
                    id="notes"
                    value={form.notes}
                    onChange={(e) => setForm({ ...form, notes: e.target.value })}
                  />
                </div>
              </div>
              <DialogFooter>
                <Button onClick={handleCreate} disabled={saving}>
                  {saving ? "Saving…" : "Save contact"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      <div className="mb-4 max-w-sm">
        <Input
          placeholder="Search name or phone…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {contacts.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
            <Users className="h-10 w-10 text-muted-foreground/50" />
            <p className="text-base font-medium">No contacts found</p>
            <p className="max-w-sm text-sm text-muted-foreground">
              Add contacts one by one or import a CSV with columns name, phone, notes.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Phone</TableHead>
                <TableHead>Notes</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {contacts.map((c) => (
                <TableRow key={c.id}>
                  <TableCell className="font-medium">{c.name}</TableCell>
                  <TableCell className="font-mono text-sm">{c.phone}</TableCell>
                  <TableCell className="max-w-md truncate text-muted-foreground">
                    {c.notes ?? ""}
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${c.name}`}
                      onClick={() => handleDelete(c)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
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
