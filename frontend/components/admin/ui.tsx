// Shared building blocks for the admin dashboard tabs: cards, badges and
// tables styled after the /compte page (dark legal-tech theme).

export const INPUT_CLASS =
  "w-full rounded-lg border border-slate-600/60 bg-slate-900/60 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-law-cyan/60 focus:outline-none";

export const PRIMARY_BUTTON_CLASS =
  "inline-flex items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-law-cyan to-law-blue px-3 py-2 text-sm font-medium text-white shadow-glow-sm transition-all hover:shadow-glow disabled:cursor-not-allowed disabled:opacity-50";

export const SECONDARY_BUTTON_CLASS =
  "inline-flex items-center justify-center gap-2 rounded-lg border border-slate-600/60 bg-slate-800/60 px-3 py-2 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-700/60 disabled:cursor-not-allowed disabled:opacity-50";

export function formatNumber(n: number): string {
  return n.toLocaleString("fr-FR");
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" });
}

/**
 * Compact an infra/health check value: inside a "configured: <path>" part,
 * keep only the model name (strip everything up to the LAST "/"). Plain
 * values like "ok" pass through unchanged.
 *   "ok (configured: openrouter/qwen/qwen-2.5-72b-instruct)"
 *     → "ok (configured: qwen-2.5-72b-instruct)"
 */
export function formatCheckValue(value: string): string {
  const marker = "configured: ";
  const i = value.indexOf(marker);
  if (i === -1) return value;
  const start = i + marker.length;
  const end = value.indexOf(")", start);
  const model = value.slice(start, end === -1 ? undefined : end);
  const short = model.includes("/") ? model.slice(model.lastIndexOf("/") + 1) : model;
  return value.slice(0, start) + short + (end === -1 ? "" : value.slice(end));
}

/** Humanize a snake_case check name: "vector_store_probe" → "Vector store probe". */
const CHECK_NAME_ACRONYMS = new Set(["llm", "api", "url"]);

export function formatCheckName(name: string): string {
  const words = name.replace(/_/g, " ").toLowerCase().split(" ");
  const titled = words.map((word, i) =>
    CHECK_NAME_ACRONYMS.has(word)
      ? word.toUpperCase()
      : i === 0
        ? word.charAt(0).toUpperCase() + word.slice(1)
        : word,
  );
  return titled.join(" ");
}

interface SectionCardProps {
  title: string;
  /** Optional content rendered on the right of the section header. */
  actions?: React.ReactNode;
  children: React.ReactNode;
}

/** Bordered section card with an uppercase cyan-dotted title. */
export function SectionCard({ title, actions, children }: SectionCardProps) {
  return (
    <section className="rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-5 shadow-2xl backdrop-blur-xl sm:p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          <span className="h-1.5 w-1.5 rounded-full bg-law-cyan" />
          {title}
        </h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

interface StatCardProps {
  label: string;
  value: string;
  hint?: string;
  icon?: React.ComponentType<{ className?: string }>;
}

/** Compact KPI card used in the overview grids. */
export function StatCard({ label, value, hint, icon: Icon }: StatCardProps) {
  return (
    <div className="rounded-xl border border-slate-700/40 bg-slate-800/30 p-4">
      <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-400">
        {Icon && <Icon className="h-3.5 w-3.5 text-law-cyan" />}
        {label}
      </p>
      <p className="mt-1.5 truncate text-xl font-semibold text-white" title={value}>
        {value}
      </p>
      {hint && <p className="mt-0.5 text-[11px] text-slate-500">{hint}</p>}
    </div>
  );
}

/** Muted box for "nothing to show" states. */
export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-slate-700/40 bg-slate-800/30 p-4 text-center">
      <p className="text-xs text-slate-400">{message}</p>
    </div>
  );
}

/** Pill badge colored by an infra-style status string ("ok …", "degraded …"). */
export function StatusBadge({ value }: { value: string }) {
  const v = value.toLowerCase();
  const className =
    v.startsWith("ok") || v === "ready"
      ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
      : v.startsWith("degraded") || v.startsWith("missing") || v === "not_ready"
        ? "border-rose-400/30 bg-rose-400/10 text-rose-300"
        : "border-amber-400/30 bg-amber-400/10 text-amber-300";
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${className}`}>
      {value}
    </span>
  );
}

/** Horizontally scrollable table wrapper (desktop admin, wide tables). */
export function TableShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-700/40">
      <table className="w-full min-w-max text-left text-sm">{children}</table>
    </div>
  );
}

export function THead({ children }: { children: React.ReactNode }) {
  return (
    <thead className="border-b border-slate-700/40 bg-slate-800/40 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
      {children}
    </thead>
  );
}

export function Th({ children }: { children?: React.ReactNode }) {
  return <th className="whitespace-nowrap px-3 py-2.5">{children}</th>;
}

export function Td({
  children,
  className = "",
  title,
  colSpan,
}: {
  children?: React.ReactNode;
  className?: string;
  title?: string;
  colSpan?: number;
}) {
  return (
    <td
      className={`border-t border-slate-800/60 px-3 py-2.5 text-slate-300 ${className}`}
      title={title}
      colSpan={colSpan}
    >
      {children}
    </td>
  );
}
