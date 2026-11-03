import { Search } from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export function useReady() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setReady(true), 240);
    return () => window.clearTimeout(id);
  }, []);
  return ready;
}

export function CardSkeleton() {
  return <div className="h-[5.5rem] animate-pulse rounded-xl border border-border bg-card" />;
}

export function TableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card" aria-hidden="true">
      <div className="h-8 border-b border-border bg-card" />
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="border-b border-border px-3 py-2 last:border-0">
          <div className="h-3.5 w-[70%] animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

export function EmptyState({ sentence, action, onAction }: { sentence: string; action: string; onAction: () => void }) {
  return (
    <div className="px-4 py-8 text-center">
      <p className="text-sm text-muted-foreground">{sentence}</p>
      <Button variant="outline" size="sm" className="mt-3" onClick={onAction}>{action}</Button>
    </div>
  );
}

export function LoadError({ message, onRetry, busy = false }: { message: string; onRetry: () => void; busy?: boolean }) {
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card px-4 py-4 text-sm">
      <p>{message}</p>
      <Button variant="outline" size="sm" disabled={busy} onClick={onRetry}>Try again</Button>
    </div>
  );
}

export function SearchField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const id = useId();
  return (
    <div className="relative w-full max-w-md">
      <label htmlFor={id} className="sr-only">{label}</label>
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
      <Input id={id} type="search" autoComplete="off" value={value} placeholder={label} onChange={(event) => onChange(event.target.value)} className="pl-9" />
    </div>
  );
}

export function TableScroll({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("max-h-[min(34rem,calc(100vh-12rem))] overflow-auto rounded-xl border border-border bg-card", className)}>
      <table className="w-full min-w-[720px] border-collapse text-[13px]">{children}</table>
    </div>
  );
}

export const headCell = "sticky top-0 z-10 bg-card px-3 py-1.5 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground shadow-[inset_0_-1px_0_var(--border)]";
export const bodyCell = "px-3 py-1.5 align-middle";
