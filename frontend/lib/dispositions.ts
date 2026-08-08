/**
 * Disposition vocabularies, shared by every screen that renders one.
 *
 * Inbound and outbound calls are labelled from different sets — a support call
 * is never "not_interested" — so the two are listed separately and merged only
 * for display. See backend/app/services/disposition.py.
 */

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

export const OUTBOUND_DISPOSITIONS = [
  "interested",
  "not_interested",
  "callback",
  "voicemail",
  "failed",
] as const;

export const INBOUND_DISPOSITIONS = [
  "resolved",
  "needs_followup",
  "complaint",
  "enquiry",
  "abandoned",
] as const;

const VARIANTS: Record<string, BadgeVariant> = {
  // good outcomes
  interested: "default",
  resolved: "default",
  // bad outcomes
  not_interested: "destructive",
  complaint: "destructive",
  // needs a human
  callback: "secondary",
  needs_followup: "secondary",
  enquiry: "secondary",
  // nothing happened
  voicemail: "outline",
  abandoned: "outline",
  failed: "outline",
};

export function dispositionVariant(disposition: string | null): BadgeVariant {
  return (disposition && VARIANTS[disposition]) || "outline";
}

/** "needs_followup" → "Needs follow-up" */
export function formatDisposition(disposition: string | null): string {
  if (!disposition) return "—";
  const words = disposition.replace(/_/g, " ");
  const label = words.charAt(0).toUpperCase() + words.slice(1);
  return label.replace("Needs followup", "Needs follow-up");
}
