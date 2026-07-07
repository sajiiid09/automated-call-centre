import type { LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export function EmptyState({
  icon: Icon,
  title,
  description,
  phase,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  phase: string;
}) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center gap-3 py-16 text-center">
        <Icon className="h-10 w-10 text-muted-foreground/50" />
        <p className="text-base font-medium">{title}</p>
        <p className="max-w-sm text-sm text-muted-foreground">{description}</p>
        <Badge variant="secondary">Coming in {phase}</Badge>
      </CardContent>
    </Card>
  );
}
