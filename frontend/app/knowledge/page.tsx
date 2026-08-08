"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, FileText, Search, Trash2, RefreshCw, Upload, Zap } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import {
  AgentProfile,
  Faq,
  KbDocument,
  KnowledgeSearchResult,
  createFaq,
  deleteFaq,
  deleteKbDocument,
  getAgentProfile,
  listFaqs,
  listKbDocuments,
  reindexKbDocument,
  searchKnowledge,
  updateAgentProfile,
  updateFaq,
  uploadKbDocument,
} from "@/lib/api";

const STATUS_VARIANT: Record<KbDocument["status"], "default" | "secondary" | "destructive" | "outline"> = {
  ready: "default",
  processing: "secondary",
  pending: "secondary",
  failed: "destructive",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function KnowledgePage() {
  const [profile, setProfile] = useState<AgentProfile | null>(null);
  const [form, setForm] = useState({
    company_name: "",
    greeting_template: "",
    persona: "",
    faq_threshold: "0.82",
  });
  const [savingProfile, setSavingProfile] = useState(false);

  const [faqs, setFaqs] = useState<Faq[]>([]);
  const [faqDialogOpen, setFaqDialogOpen] = useState(false);
  const [faqForm, setFaqForm] = useState({ question: "", answer: "" });
  const [savingFaq, setSavingFaq] = useState(false);

  const [documents, setDocuments] = useState<KbDocument[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const [query, setQuery] = useState("");
  const [result, setResult] = useState<KnowledgeSearchResult | null>(null);
  const [searching, setSearching] = useState(false);

  const loadProfile = useCallback(async () => {
    try {
      const p = await getAgentProfile();
      setProfile(p);
      setForm({
        company_name: p.company_name,
        greeting_template: p.greeting_template,
        persona: p.persona ?? "",
        faq_threshold: String(p.faq_threshold),
      });
    } catch (e) {
      toast.error(`Failed to load agent profile: ${(e as Error).message}`);
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [f, d] = await Promise.all([listFaqs(), listKbDocuments()]);
      setFaqs(f);
      setDocuments(d);
    } catch (e) {
      toast.error(`Failed to load knowledge base: ${(e as Error).message}`);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async fetch, setState after await
    loadProfile();
    refresh();
  }, [loadProfile, refresh]);

  // documents index in the background, so poll while any is still working
  const indexing = documents.some((d) => d.status === "pending" || d.status === "processing");
  useEffect(() => {
    if (!indexing) return;
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, [indexing, refresh]);

  async function handleSaveProfile() {
    const threshold = Number(form.faq_threshold);
    if (Number.isNaN(threshold) || threshold < 0 || threshold > 1) {
      toast.error("Match threshold must be between 0 and 1");
      return;
    }
    setSavingProfile(true);
    try {
      const updated = await updateAgentProfile({
        company_name: form.company_name.trim(),
        greeting_template: form.greeting_template,
        persona: form.persona.trim(),
        faq_threshold: threshold,
      });
      setProfile(updated);
      toast.success("Agent profile saved");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSavingProfile(false);
    }
  }

  async function handleCreateFaq() {
    if (!faqForm.question.trim() || !faqForm.answer.trim()) {
      toast.error("Question and answer are both required");
      return;
    }
    setSavingFaq(true);
    try {
      const faq = await createFaq({
        question: faqForm.question.trim(),
        answer: faqForm.answer.trim(),
      });
      if (!faq.indexed) {
        toast.warning("Saved, but it could not be indexed — check the embeddings API");
      } else {
        toast.success("FAQ added");
      }
      setFaqDialogOpen(false);
      setFaqForm({ question: "", answer: "" });
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSavingFaq(false);
    }
  }

  async function handleToggleFaq(faq: Faq) {
    try {
      await updateFaq(faq.id, { enabled: !faq.enabled });
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function handleDeleteFaq(faq: Faq) {
    try {
      await deleteFaq(faq.id);
      toast.success("FAQ deleted");
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function handleUpload(file: File) {
    setUploading(true);
    try {
      await uploadKbDocument(file);
      toast.success(`Uploaded ${file.name} — indexing`);
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setUploading(false);
    }
  }

  async function handleReindex(document: KbDocument) {
    try {
      await reindexKbDocument(document.id);
      toast.success(`Reindexing ${document.title}`);
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function handleDeleteDocument(document: KbDocument) {
    try {
      await deleteKbDocument(document.id);
      toast.success(`Deleted ${document.title}`);
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function handleSearch() {
    if (!query.trim()) return;
    setSearching(true);
    try {
      setResult(await searchKnowledge(query.trim()));
    } catch (e) {
      setResult(null);
      toast.error((e as Error).message);
    } finally {
      setSearching(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Knowledge"
        description="What the agent knows: who it says it is, the answers it reads out verbatim, and the documents it reasons from."
      />

      <div className="grid gap-6">
        {/* --- identity --- */}
        <Card>
          <CardHeader>
            <CardTitle>Agent identity</CardTitle>
            <CardDescription>
              The greeting is spoken exactly as written, with no LLM involved, so callers hear it
              instantly and identically every time.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-2 sm:max-w-md">
              <Label htmlFor="company_name">Company name</Label>
              <Input
                id="company_name"
                value={form.company_name}
                onChange={(e) => setForm({ ...form, company_name: e.target.value })}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="greeting_template">Greeting</Label>
              <Textarea
                id="greeting_template"
                rows={2}
                value={form.greeting_template}
                onChange={(e) => setForm({ ...form, greeting_template: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                <code>$company_name</code> and <code>$contact_name</code> are filled in.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="persona">What the agent should know</Label>
              <Textarea
                id="persona"
                rows={4}
                placeholder="What you do, who you serve, tone, when to escalate to a human…"
                value={form.persona}
                onChange={(e) => setForm({ ...form, persona: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                Added to every call&apos;s system prompt. Keep policies and specifics in documents
                below instead — this is for standing instructions.
              </p>
            </div>
            <div className="grid gap-2 sm:max-w-[14rem]">
              <Label htmlFor="faq_threshold">FAQ match threshold</Label>
              <Input
                id="faq_threshold"
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={form.faq_threshold}
                onChange={(e) => setForm({ ...form, faq_threshold: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                Higher is stricter. Use Test search below to see real scores.
              </p>
            </div>
            <div>
              <Button onClick={handleSaveProfile} disabled={savingProfile || !profile}>
                {savingProfile ? "Saving…" : "Save"}
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* --- FAQs --- */}
        <Card>
          <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div>
              <CardTitle>Instant answers</CardTitle>
              <CardDescription>
                On a close match the answer is spoken word for word and the LLM is skipped
                entirely — faster, and it can never be paraphrased into something wrong.
              </CardDescription>
            </div>
            <Dialog open={faqDialogOpen} onOpenChange={setFaqDialogOpen}>
              <DialogTrigger asChild>
                <Button>Add answer</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Add an instant answer</DialogTitle>
                </DialogHeader>
                <div className="grid gap-4 py-2">
                  <div className="grid gap-2">
                    <Label htmlFor="question">Question</Label>
                    <Input
                      id="question"
                      placeholder="What are your opening hours?"
                      value={faqForm.question}
                      onChange={(e) => setFaqForm({ ...faqForm, question: e.target.value })}
                    />
                    <p className="text-xs text-muted-foreground">
                      Callers won&apos;t say it exactly; close paraphrases still match.
                    </p>
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="answer">Spoken answer</Label>
                    <Textarea
                      id="answer"
                      rows={3}
                      placeholder="We're open nine to five, Monday to Friday."
                      value={faqForm.answer}
                      onChange={(e) => setFaqForm({ ...faqForm, answer: e.target.value })}
                    />
                    <p className="text-xs text-muted-foreground">
                      Read aloud verbatim — write it the way you would say it.
                    </p>
                  </div>
                </div>
                <DialogFooter>
                  <Button onClick={handleCreateFaq} disabled={savingFaq}>
                    {savingFaq ? "Saving…" : "Save answer"}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </CardHeader>
          <CardContent>
            {faqs.length === 0 ? (
              <div className="flex flex-col items-center gap-3 py-10 text-center">
                <Zap className="h-8 w-8 text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">
                  No instant answers yet. Add the questions you get most often.
                </p>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-16">On</TableHead>
                    <TableHead>Question</TableHead>
                    <TableHead>Spoken answer</TableHead>
                    <TableHead className="w-20">Used</TableHead>
                    <TableHead className="w-12" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {faqs.map((f) => (
                    <TableRow key={f.id}>
                      <TableCell>
                        <Checkbox
                          checked={f.enabled}
                          onCheckedChange={() => handleToggleFaq(f)}
                          aria-label={`Enable ${f.question}`}
                        />
                      </TableCell>
                      <TableCell className="font-medium">
                        {f.question}
                        {!f.indexed && (
                          <Badge variant="destructive" className="ml-2">
                            not indexed
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="max-w-md text-muted-foreground">{f.answer}</TableCell>
                      <TableCell className="tabular-nums text-muted-foreground">
                        {f.hit_count}
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Delete ${f.question}`}
                          onClick={() => handleDeleteFaq(f)}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* --- documents --- */}
        <Card>
          <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div>
              <CardTitle>Documents</CardTitle>
              <CardDescription>
                Anything not covered by an instant answer is answered from these. PDF, TXT and MD.
              </CardDescription>
            </div>
            <div>
              <input
                ref={fileInput}
                type="file"
                accept=".pdf,.txt,.md"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleUpload(f);
                  e.target.value = "";
                }}
              />
              <Button
                variant="outline"
                disabled={uploading}
                onClick={() => fileInput.current?.click()}
              >
                <Upload className="mr-1 h-4 w-4" />
                {uploading ? "Uploading…" : "Upload"}
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {documents.length === 0 ? (
              <div className="flex flex-col items-center gap-3 py-10 text-center">
                <FileText className="h-8 w-8 text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">
                  No documents yet. Upload a handbook, price list, or policy document.
                </p>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Document</TableHead>
                    <TableHead className="w-24">Size</TableHead>
                    <TableHead className="w-32">Status</TableHead>
                    <TableHead className="w-24">Sections</TableHead>
                    <TableHead className="w-24" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {documents.map((d) => (
                    <TableRow key={d.id}>
                      <TableCell>
                        <div className="font-medium">{d.title}</div>
                        <div className="text-xs text-muted-foreground">{d.filename}</div>
                        {d.error && (
                          <div className="mt-1 text-xs text-destructive">{d.error}</div>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {formatBytes(d.size_bytes)}
                      </TableCell>
                      <TableCell>
                        <Badge variant={STATUS_VARIANT[d.status]}>{d.status}</Badge>
                      </TableCell>
                      <TableCell className="tabular-nums text-muted-foreground">
                        {d.chunk_count}
                      </TableCell>
                      <TableCell>
                        <div className="flex gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Reindex ${d.title}`}
                            onClick={() => handleReindex(d)}
                          >
                            <RefreshCw className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Delete ${d.title}`}
                            onClick={() => handleDeleteDocument(d)}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* --- search probe --- */}
        <Card>
          <CardHeader>
            <CardTitle>Test search</CardTitle>
            <CardDescription>
              Ask what a caller would ask. This is the whole retrieval path without a phone call.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="flex gap-2">
              <Input
                placeholder="What are your opening hours?"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSearch();
                }}
              />
              <Button onClick={handleSearch} disabled={searching || !query.trim()}>
                <Search className="mr-1 h-4 w-4" />
                {searching ? "Searching…" : "Search"}
              </Button>
            </div>

            {result && (
              <div className="grid gap-4">
                <div className="rounded-md border p-3">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="text-sm font-medium">Best instant answer</span>
                    {result.faq && (
                      <Badge variant={result.would_bypass_llm ? "default" : "outline"}>
                        {result.faq.score.toFixed(3)} vs {result.threshold.toFixed(2)}
                      </Badge>
                    )}
                  </div>
                  {result.faq ? (
                    <>
                      <p className="text-sm text-muted-foreground">{result.faq.question}</p>
                      <p className="mt-1 text-sm">{result.faq.answer}</p>
                      <p className="mt-2 text-xs text-muted-foreground">
                        {result.would_bypass_llm
                          ? "Spoken verbatim — the LLM is skipped."
                          : "Below threshold, so the LLM answers using the sections below."}
                      </p>
                    </>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      No instant answers to match against yet.
                    </p>
                  )}
                </div>

                <div>
                  <p className="mb-2 text-sm font-medium">
                    Document sections given to the agent ({result.chunks.length})
                  </p>
                  {result.chunks.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      Nothing relevant found — the agent would say it will pass this to the team.
                    </p>
                  ) : (
                    <div className="grid gap-2">
                      {result.chunks.map((c, i) => (
                        <div key={i} className="rounded-md border p-3">
                          <div className="mb-1 flex items-center gap-2">
                            <BookOpen className="h-3.5 w-3.5 text-muted-foreground" />
                            <span className="text-xs font-medium">{c.title}</span>
                            <Badge variant="outline">{c.score.toFixed(3)}</Badge>
                          </div>
                          <p className="text-sm text-muted-foreground">{c.content}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
