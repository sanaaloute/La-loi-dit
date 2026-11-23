/**
 * Corpus document names often come from downloaded filenames
 * ("LOI_013-2026_Code_du_travail.pdf"). This is the display form for UI:
 * extension stripped, underscores turned into spaces, whitespace collapsed,
 * and the bulk-download crawler prefixes ("assemblee assemblee 1001 …",
 * "legiburkina …") dropped — they are provenance noise, not part of the title.
 */
export function displayDocName(raw: string): string {
  return raw
    .trim()
    .replace(/\.(pdf|txt|md|docx?|html?|json)$/i, "")
    .replace(/_+/g, " ")
    .replace(/\s{2,}/g, " ")
    .replace(/^assemblee\s+assemblee\s+\d+\s+/i, "")
    .replace(/^legiburkina\s+/i, "")
    .trim();
}
