import type { FreshnessEvent } from "@/lib/api";

/** Authorities whose publications are actual legal texts (laws, decrees,
 * gazettes, constitutions, case law). Press feeds (`official_news`) and the
 * OHADA news RSS are excluded from the veille juridique. */
const LEGAL_AUTHORITIES = new Set([
  "law",
  "official_gazette",
  "constitution",
  "case_law",
  "regulation",
]);

export function isLegalTextEvent(ev: FreshnessEvent): boolean {
  const authority = ev.metadata?.authority;
  return typeof authority === "string" && LEGAL_AUTHORITIES.has(authority);
}
