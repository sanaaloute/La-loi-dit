// Export helpers: fetch the binary from /export/*, write it to the cache
// directory with expo-file-system, then hand it to the OS share sheet.
import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";
import {
  exportAnswer,
  exportDraft,
  exportMarkdown,
  type ChatResponse,
  type DraftResponse,
  type ExportFormat,
  type ExportItem,
  type ExportRequest,
} from "./api";

export type MenuFormat = ExportFormat | "md";

const MIME: Record<MenuFormat, string> = {
  pdf: "application/pdf",
  word: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  csv: "text/csv",
  md: "text/markdown",
};

export const FORMAT_EXTENSIONS: Record<MenuFormat, string> = {
  pdf: "pdf",
  word: "docx",
  csv: "csv",
  md: "md",
};

async function shareBytes(bytes: ArrayBuffer, filename: string, mimeType: string): Promise<void> {
  const file = new File(Paths.cache, filename);
  try {
    if (file.exists) file.delete();
  } catch {
    // Nothing to delete.
  }
  file.create();
  file.write(new Uint8Array(bytes));
  if (await Sharing.isAvailableAsync()) {
    await Sharing.shareAsync(file.uri, { mimeType, dialogTitle: filename });
  } else {
    throw new Error("Le partage de fichiers n'est pas disponible sur cet appareil.");
  }
}

/** Export one answer or a whole conversation, then open the share sheet. */
export async function shareAnswerExport(
  format: MenuFormat,
  response: ChatResponse,
  query: string,
  items?: ExportItem[],
): Promise<void> {
  const payload: ExportRequest = {
    query,
    answer: response.answer,
    items: items && items.length > 0 ? items : undefined,
    session_id: response.session_id,
    latency_ms: response.latency_ms,
  };
  const bytes = format === "md" ? await exportMarkdown(payload) : await exportAnswer(format, payload);
  const prefix = items && items.length > 1 ? "conversation-juridique" : "reponse-juridique";
  await shareBytes(bytes, `${prefix}-${response.session_id.slice(0, 8)}.${FORMAT_EXTENSIONS[format]}`, MIME[format]);
}

/** Export a generated draft, then open the share sheet. */
export async function shareDraftExport(
  format: MenuFormat,
  draft: DraftResponse,
  category: string,
): Promise<void> {
  const bytes = await exportDraft(draft, format);
  const prefix = category === "case" ? "procedure" : "contrat";
  await shareBytes(bytes, `${prefix}-${draft.template_id}.${FORMAT_EXTENSIONS[format]}`, MIME[format]);
}
