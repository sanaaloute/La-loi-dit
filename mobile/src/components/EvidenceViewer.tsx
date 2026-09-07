import React, { useMemo, useState } from "react";
import { Linking, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import type { EvidenceChunk } from "../lib/api";
import type { ThemeColors } from "../theme";
import { useTheme } from "../theme-context";

const AUTHORITY_LABELS: Record<string, string> = {
  constitution: "Constitution",
  treaty_ohada: "Traité OHADA",
  amended_law: "Loi modifiée",
  law: "Loi",
  decree: "Décret",
  order: "Arrêté",
  ministerial_circular: "Circulaire",
  official_gazette: "Journal officiel",
  case_law: "Jurisprudence",
  official_press_release: "Communiqué officiel",
  official_news: "Actualité officielle",
  uploaded_document: "Document fourni",
  trusted_legal_site: "Site juridique",
  news: "Presse",
  blog: "Blog",
  unknown: "Inconnu",
};

function AuthorityBadge({ authority }: { authority: string }) {
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  return (
    <View style={styles.authorityBadge}>
      <Text style={styles.authorityBadgeText}>
        {AUTHORITY_LABELS[authority] ?? AUTHORITY_LABELS.unknown}
      </Text>
    </View>
  );
}

function Score({ label, value, dash }: { label: string; value: number; dash?: boolean }) {
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  return (
    <View style={styles.metaItem}>
      <Text style={styles.metaLabel}>{label}</Text>
      <Text style={[styles.metaValue, dash && styles.metaValueDash]}>{dash ? "—" : value.toFixed(2)}</Text>
    </View>
  );
}

function MetaRow({ label, value }: { label: string; value?: string | number | null }) {
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  if (value === undefined || value === null || value === "") return null;
  return (
    <View style={styles.metaItem}>
      <Text style={styles.metaLabel}>{label}</Text>
      <Text style={styles.metaValue}>{String(value)}</Text>
    </View>
  );
}

function EvidenceCard({ chunk, index }: { chunk: EvidenceChunk; index: number }) {
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [open, setOpen] = useState(false);
  const title = chunk.document_name || "Document inconnu";
  // Backend stamps metadata.expansion = "parent" on chunks expanded to their
  // parent context around a retrieved excerpt.
  const expandedParent = chunk.metadata?.expansion === "parent";
  // Defensive: a displayed chunk whose three scores are all 0 shows "—".
  const allScoresZero =
    chunk.confidence === 0 && chunk.retrieval_score === 0 && chunk.rerank_score === 0;

  return (
    <View style={styles.card}>
      <Pressable onPress={() => setOpen((v) => !v)} style={styles.cardHeader}>
        <View style={styles.cardTitleBlock}>
          <View style={styles.cardTitleRow}>
            <Ionicons name="document-text-outline" size={14} color={colors.muted} />
            <Text style={styles.cardTitle} numberOfLines={1}>
              {index + 1}. {title}
            </Text>
            {expandedParent && (
              <View style={styles.expandedBadge}>
                <Ionicons name="layers-outline" size={10} color={colors.ink} />
                <Text style={styles.expandedBadgeText}>Contexte élargi</Text>
              </View>
            )}
          </View>
          {chunk.article ? <Text style={styles.cardSubtitle}>Article {chunk.article}</Text> : null}
        </View>
        <View style={styles.cardHeaderRight}>
          <AuthorityBadge authority={chunk.authority} />
          <Ionicons name={open ? "chevron-up" : "chevron-down"} size={16} color={colors.muted} />
        </View>
      </Pressable>
      {open && (
        <View style={styles.cardBody}>
          <Text style={styles.content}>{chunk.content}</Text>
          <View style={styles.metaGrid}>
            <MetaRow label="Document" value={chunk.document_name} />
            <MetaRow label="Article" value={chunk.article} />
            <MetaRow label="Section" value={chunk.section} />
            <MetaRow label="Page" value={chunk.page} />
            <MetaRow label="Publication" value={chunk.publication_date} />
            <MetaRow label="Entrée en vigueur" value={chunk.effective_date} />
            <MetaRow label="Organe" value={chunk.government_body} />
            <MetaRow label="Type de source" value={chunk.source_kind} />
            <MetaRow label="Version" value={chunk.version} />
            <Score label="Confiance" value={chunk.confidence} dash={allScoresZero} />
            <Score label="Score recherche" value={chunk.retrieval_score} dash={allScoresZero} />
            <Score label="Score reclassement" value={chunk.rerank_score} dash={allScoresZero} />
          </View>
          {chunk.url ? (
            <Pressable onPress={() => void Linking.openURL(chunk.url as string).catch(() => {})}>
              <Text style={styles.url} numberOfLines={1}>
                {chunk.url}
              </Text>
            </Pressable>
          ) : null}
        </View>
      )}
    </View>
  );
}

function hasNonZeroScore(chunk: EvidenceChunk): boolean {
  return chunk.confidence !== 0 || chunk.retrieval_score !== 0 || chunk.rerank_score !== 0;
}

/**
 * Pure-noise entries: all three scores at 0 AND no child chunk carries a
 * non-zero score either. These are expanded parents the backend could not
 * backfill with a best-child score — hiding them keeps the list meaningful.
 */
function isNoise(chunk: EvidenceChunk): boolean {
  if (hasNonZeroScore(chunk)) return false;
  return !(chunk.child_chunks ?? []).some(hasNonZeroScore);
}

interface EvidenceViewerProps {
  evidence: EvidenceChunk[];
}

export default function EvidenceViewer({ evidence }: EvidenceViewerProps) {
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const visible = evidence.filter((chunk) => !isNoise(chunk));
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View style={[styles.headerDot, { backgroundColor: colors.ink }]} />
        <Text style={styles.headerText}>Preuves ({visible.length})</Text>
      </View>
      {visible.length === 0 ? (
        <View style={styles.empty}>
          <Text style={styles.emptyText}>Aucune preuve pour cette réponse.</Text>
        </View>
      ) : (
        visible.map((chunk, i) => <EvidenceCard key={chunk.chunk_id || i} chunk={chunk} index={i} />)
      )}
    </View>
  );
}

const makeStyles = (colors: ThemeColors) => StyleSheet.create({
  container: { padding: 16, gap: 10 },
  header: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 2 },
  headerDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.accent },
  headerText: {
    fontSize: 11,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: colors.muted,
  },
  empty: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 16,
  },
  emptyText: { fontSize: 12, color: colors.muted, textAlign: "center" },
  card: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 12,
    overflow: "hidden",
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 8,
    padding: 12,
  },
  cardTitleBlock: { flex: 1 },
  cardTitleRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  cardTitle: { fontSize: 13, fontWeight: "500", color: colors.inkSoft, flexShrink: 1 },
  cardSubtitle: { fontSize: 12, color: colors.muted, marginTop: 2, marginLeft: 20 },
  cardHeaderRight: { flexDirection: "row", alignItems: "center", gap: 6 },
  authorityBadge: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  authorityBadgeText: { fontSize: 10, fontWeight: "500", color: colors.muted },
  expandedBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    borderWidth: 1,
    borderColor: colors.inkSoft,
    backgroundColor: colors.surface,
    borderRadius: 999,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  expandedBadgeText: { fontSize: 9, fontWeight: "500", color: colors.ink },
  cardBody: { borderTopWidth: 1, borderTopColor: colors.border, padding: 12, gap: 10 },
  content: { fontSize: 12, lineHeight: 18, color: colors.inkSoft },
  metaGrid: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  metaItem: { minWidth: "28%" },
  metaLabel: {
    fontSize: 9,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: colors.faint,
  },
  metaValue: { fontSize: 12, fontWeight: "500", color: colors.inkSoft },
  metaValueDash: { color: colors.faint },
  url: { fontSize: 12, color: colors.accent },
});
