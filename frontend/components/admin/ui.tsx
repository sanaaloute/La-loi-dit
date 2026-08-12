// Shared building blocks for the admin dashboard tabs: cards, badges and
// tables in the light legal-tech theme (white surfaces, navy accent).

export const INPUT_CLASS =
  "w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:border-accent/60 focus:outline-none";

export const PRIMARY_BUTTON_CLASS =
  "inline-flex items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50";

export const SECONDARY_BUTTON_CLASS =
  "inline-flex items-center justify-center gap-2 rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50";

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

/** Bordered section card with an uppercase accent-dotted title. */
export function SectionCard({ title, actions, children }: SectionCardProps) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-2xl backdrop-blur-xl sm:p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
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
    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
      <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-gray-500">
        {Icon && <Icon className="h-3.5 w-3.5 text-accent" />}
        {label}
      </p>
      <p className="mt-1.5 truncate text-xl font-semibold text-gray-900" title={value}>
        {value}
      </p>
      {hint && <p className="mt-0.5 text-[11px] text-gray-500">{hint}</p>}
    </div>
  );
}

/** Muted box for "nothing to show" states. */
export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 text-center">
      <p className="text-xs text-gray-500">{message}</p>
    </div>
  );
}

/** Pill badge colored by an infra-style status string ("ok …", "degraded …"). */
export function StatusBadge({ value }: { value: string }) {
  const v = value.toLowerCase();
  const className =
    v.startsWith("ok") || v === "ready"
      ? "border-accent/30 bg-accent/10 text-accent"
      : v.startsWith("degraded") || v.startsWith("missing") || v === "not_ready"
        ? "border-red-700/30 bg-red-700/10 text-red-700"
        : "border-warn-border/60 bg-warn-bg text-warn-text";
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${className}`}>
      {value}
    </span>
  );
}

/** Horizontally scrollable table wrapper (desktop admin, wide tables). */
export function TableShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-gray-200">
      <table className="w-full min-w-max text-left text-sm">{children}</table>
    </div>
  );
}

export function THead({ children }: { children: React.ReactNode }) {
  return (
    <thead className="border-b border-gray-200 bg-gray-50 text-[11px] font-semibold uppercase tracking-wide text-gray-500">
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
      className={`border-t border-gray-200 px-3 py-2.5 text-gray-600 ${className}`}
      title={title}
      colSpan={colSpan}
    >
      {children}
    </td>
  );
}
