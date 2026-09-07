// Date formatting helpers shared by the history panel, bookmarks, freshness
// feed and share pages (no date library in the bundle).

/** Relative date in French, e.g. "il y a 2 h" (no library). */
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

/** Long French date, e.g. "2 septembre 2026" (falls back to the raw input). */
export function formatLongDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
}

/** Format a "YYYY-MM-DD" scenario date as a long French date. */
export function formatScenarioDate(isoDate: string): string {
  // Noon UTC anchors the day: a bare "YYYY-MM-DD" parses as UTC midnight and
  // can roll back a day in negative-offset timezones.
  return formatLongDate(`${isoDate}T12:00:00Z`);
}
