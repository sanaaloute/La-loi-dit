// French date/time formatting (no library), ported from the web app.

/** Relative date in French, e.g. "il y a 2 h" (HistoryPanel). */
export function relativeDate(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.floor((Date.now() - then) / 60000);
  if (minutes < 1) return "à l'instant";
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `il y a ${hours} h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `il y a ${days} j`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `il y a ${weeks} sem.`;
  const months = Math.floor(days / 30);
  if (months < 12) return `il y a ${months} mois`;
  const years = Math.floor(days / 365);
  return `il y a ${years} an${years > 1 ? "s" : ""}`;
}

/** Chat timeline timestamp: HH:MM today, DD/MM HH:MM for older messages. */
export function formatMessageTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const time = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return time;
  return `${d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" })} ${time}`;
}

/** Short French date for the usage chart, e.g. "02/09". */
export function shortDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
}

/** ISO date → "2 septembre 2026" for the subscription card. */
export function longDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
}

/**
 * Date-only string ("2026-09-02") → "2 septembre 2026", parsed as a LOCAL
 * date so the day never shifts with the device timezone (unlike longDate,
 * which parses ISO strings as UTC midnight).
 */
export function localLongDate(ymd: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(ymd.trim());
  if (!m) return ymd;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  if (Number.isNaN(d.getTime())) return ymd;
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
}
