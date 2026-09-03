import React, { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import CitationPanel from "../../src/components/CitationPanel";
import Markdown from "../../src/components/Markdown";
import UpgradePanel from "../../src/components/UpgradePanel";
import { PrimaryButton, TextField } from "../../src/components/ui";
import {
  ApiError,
  createDraft,
  listDraftTemplates,
  me,
  type DraftField,
  type DraftResponse,
  type DraftTemplate,
  type UserProfile,
} from "../../src/lib/api";
import { shareDraftExport, type MenuFormat } from "../../src/lib/export";
import { getModel } from "../../src/lib/storage";
import type { ThemeColors } from "../../src/theme";
import { useTheme } from "../../src/theme-context";

type Step = "templates" | "form" | "result";

const CATEGORY_LABELS: Record<DraftTemplate["category"], string> = {
  contract: "Contrats",
  case: "Procédures",
};

const CATEGORY_ORDER: DraftTemplate["category"][] = ["contract", "case"];

const DRAFT_EXPORTS: { id: MenuFormat; label: string }[] = [
  { id: "pdf", label: "PDF" },
  { id: "word", label: "Word" },
  { id: "md", label: "Markdown" },
];

function isForbidden(err: unknown): boolean {
  if (err instanceof ApiError) return err.status === 403;
  return err instanceof Error && err.message.includes("403");
}

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: DraftField;
  value: string;
  onChange: (value: string) => void;
}) {
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  if (field.type === "select") {
    return (
      <View style={styles.selectWrap}>
        {field.options.map((option) => {
          const selected = value === option;
          return (
            <Pressable
              key={option}
              onPress={() => onChange(option)}
              style={[styles.selectOption, selected && styles.selectOptionActive]}
            >
              <Text style={[styles.selectOptionText, selected && styles.selectOptionTextActive]}>
                {option}
              </Text>
            </Pressable>
          );
        })}
      </View>
    );
  }
  return (
    <TextField
      label=""
      value={value}
      onChangeText={onChange}
      placeholder={field.placeholder || (field.type === "date" ? "AAAA-MM-JJ" : undefined)}
      multiline={field.type === "textarea"}
      keyboardType={field.type === "number" ? "numeric" : "default"}
      autoCapitalize={field.type === "text" || field.type === "textarea" ? "sentences" : "none"}
    />
  );
}

export default function DraftScreen() {
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [templates, setTemplates] = useState<DraftTemplate[] | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [step, setStep] = useState<Step>("templates");
  const [search, setSearch] = useState("");
  const [template, setTemplate] = useState<DraftTemplate | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [instructions, setInstructions] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [quotaError, setQuotaError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [exporting, setExporting] = useState<MenuFormat | null>(null);

  function load() {
    listDraftTemplates()
      .then((res) => setTemplates(res.templates))
      .catch((err) => {
        if (isForbidden(err)) setForbidden(true);
        else setLoadError(err instanceof Error ? err.message : "Une erreur est survenue.");
      });
    me()
      .then((p) => setProfile(p))
      .catch(() => {
        // Profil indisponible : les boutons d'export ne sont pas filtrés.
      });
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const grouped = useMemo(() => {
    const query = search.trim().toLowerCase();
    const filtered = (templates ?? []).filter(
      (t) =>
        query.length === 0 ||
        t.label.toLowerCase().includes(query) ||
        t.description.toLowerCase().includes(query),
    );
    return CATEGORY_ORDER.map((category) => ({
      category,
      label: CATEGORY_LABELS[category],
      templates: filtered.filter((t) => t.category === category),
    })).filter((group) => group.templates.length > 0);
  }, [templates, search]);

  function pickTemplate(t: DraftTemplate) {
    setTemplate(t);
    setValues({});
    setInstructions("");
    setFormError(null);
    setQuotaError(null);
    setStep("form");
  }

  async function handleGenerate() {
    if (!template || busy) return;
    const missing = template.fields.filter((f) => f.required && !(values[f.name] ?? "").trim());
    if (missing.length > 0) {
      setFormError(`Champs obligatoires manquants : ${missing.map((f) => f.label).join(", ")}`);
      return;
    }
    setBusy(true);
    setFormError(null);
    setQuotaError(null);
    try {
      const result = await createDraft({
        template_id: template.id,
        fields: values,
        instructions: instructions.trim() || undefined,
        model: getModel() ?? undefined,
      });
      setDraft(result);
      setStep("result");
    } catch (err) {
      if (isForbidden(err)) setForbidden(true);
      else if (err instanceof ApiError && err.status === 429) setQuotaError(err.message);
      else setFormError(err instanceof Error ? err.message : "Échec de la génération du document.");
    } finally {
      setBusy(false);
    }
  }

  async function handleExport(format: MenuFormat) {
    if (!draft || exporting) return;
    setExporting(format);
    try {
      await shareDraftExport(format, draft, template?.category ?? "contract");
    } catch (err) {
      Alert.alert("Échec de l'export", err instanceof Error ? err.message : "Une erreur est survenue.");
    } finally {
      setExporting(null);
    }
  }

  const allowedExports = profile?.features?.export;

  let body: React.ReactNode;
  if (forbidden) {
    body = (
      <UpgradePanel body="La génération de documents juridiques (contrats, actes de procédure) est réservée aux offres Pro et Cabinet. Contactez votre administrateur pour mettre à niveau votre compte." />
    );
  } else if (loadError) {
    body = (
      <View style={styles.centerBlock}>
        <Text style={styles.errorText}>{loadError}</Text>
        <PrimaryButton
          title="Réessayer"
          onPress={() => {
            setLoadError(null);
            setTemplates(null);
            load();
          }}
        />
      </View>
    );
  } else if (templates === null) {
    body = (
      <View style={styles.centerBlock}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.mutedText}>Chargement des modèles…</Text>
      </View>
    );
  } else if (step === "templates") {
    body = (
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.pageTitle}>Choisissez un modèle</Text>
        <Text style={styles.pageSubtitle}>
          Le document est généré à partir de vos réponses et de sources vérifiées.
        </Text>
        <View style={styles.searchField}>
          <TextField
            label=""
            value={search}
            onChangeText={setSearch}
            placeholder="Rechercher un modèle…"
            autoCapitalize="none"
            autoCorrect={false}
          />
        </View>
        {grouped.length === 0 ? (
          <Text style={styles.emptySearch}>Aucun modèle ne correspond à votre recherche.</Text>
        ) : (
          grouped.map((group) => (
            <View key={group.category} style={styles.group}>
              <View style={styles.groupHeader}>
                <View style={styles.groupDot} />
                <Text style={styles.groupLabel}>{group.label}</Text>
              </View>
              <View style={styles.groupGrid}>
                {group.templates.map((t) => (
                  <Pressable key={t.id} onPress={() => pickTemplate(t)} style={styles.templateCard}>
                    <View style={styles.templateIcon}>
                      <Ionicons name="create-outline" size={16} color={colors.accent} />
                    </View>
                    <Text style={styles.templateLabel}>{t.label}</Text>
                    <Text style={styles.templateDescription}>{t.description}</Text>
                  </Pressable>
                ))}
              </View>
            </View>
          ))
        )}
      </ScrollView>
    );
  } else if (step === "form" && template) {
    body = (
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          <Pressable onPress={() => setStep("templates")} style={styles.backRow}>
            <Ionicons name="arrow-back" size={13} color={colors.muted} />
            <Text style={styles.backText}>Tous les modèles</Text>
          </Pressable>
          {quotaError && (
            <View style={styles.quotaCard}>
              <View style={styles.quotaHeader}>
                <Ionicons name="alert-circle" size={15} color={colors.warnText} />
                <Text style={styles.quotaTitle}>Quota journalier atteint</Text>
              </View>
              <Text style={styles.quotaText}>{quotaError}</Text>
              <Text style={styles.quotaHint}>Passez à l'offre supérieure pour continuer.</Text>
            </View>
          )}
          <View style={styles.card}>
            <Text style={styles.cardTitle}>{template.label}</Text>
            <Text style={styles.cardSubtitle}>{template.description}</Text>
            <View style={styles.formFields}>
              {template.fields.map((field) => (
                <View key={field.name} style={styles.formField}>
                  <Text style={styles.fieldLabel}>
                    {field.label}
                    {field.required && <Text style={styles.requiredMark}> *</Text>}
                  </Text>
                  <FieldInput
                    field={field}
                    value={values[field.name] ?? ""}
                    onChange={(v) => setValues((prev) => ({ ...prev, [field.name]: v }))}
                  />
                </View>
              ))}
              <TextField
                label="Instructions complémentaires"
                optional
                value={instructions}
                onChangeText={setInstructions}
                placeholder="Précisions, clauses particulières, contexte…"
                multiline
              />
              {formError ? <Text style={styles.errorText}>{formError}</Text> : null}
              <PrimaryButton
                title={busy ? "Génération en cours…" : "Générer le document"}
                onPress={() => void handleGenerate()}
                busy={busy}
              />
            </View>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    );
  } else if (draft) {
    body = (
      <ScrollView contentContainerStyle={styles.scroll}>
        <View style={styles.resultActions}>
          <Pressable onPress={() => setStep("form")} style={styles.backRow}>
            <Ionicons name="arrow-back" size={13} color={colors.muted} />
            <Text style={styles.backText}>Modifier les informations</Text>
          </Pressable>
          <Pressable
            onPress={() => {
              setDraft(null);
              setTemplate(null);
              setStep("templates");
            }}
            style={styles.newDocButton}
          >
            <Ionicons name="create-outline" size={14} color={colors.inkSoft} />
            <Text style={styles.newDocText}>Nouveau document</Text>
          </Pressable>
        </View>

        <View style={styles.card}>
          <View style={styles.resultHeader}>
            <View style={styles.resultTitleBlock}>
              <Text style={styles.cardTitle}>{draft.title}</Text>
              <Text style={styles.resultLatency}>Généré en {draft.latency_ms.toFixed(0)} ms</Text>
            </View>
            {draft.requires_human_review && (
              <View style={styles.reviewBadge}>
                <Ionicons name="shield-half" size={11} color={colors.danger} />
                <Text style={styles.reviewBadgeText}>Révision humaine recommandée</Text>
              </View>
            )}
          </View>

          {draft.requires_human_review && (
            <View style={styles.reviewNotice}>
              <Text style={styles.reviewNoticeText}>
                Ce document doit être relu et validé par un juriste avant toute utilisation.
              </Text>
            </View>
          )}

          {draft.warnings.length > 0 && (
            <View style={styles.warningsCard}>
              <View style={styles.quotaHeader}>
                <Ionicons name="alert-circle" size={13} color={colors.warnText} />
                <Text style={styles.warningsTitle}>Avertissements</Text>
              </View>
              {draft.warnings.map((w, i) => (
                <Text key={i} style={styles.warningItem}>
                  • {w}
                </Text>
              ))}
            </View>
          )}

          <Markdown>{draft.draft_markdown}</Markdown>

          <View style={styles.exportRow}>
            {DRAFT_EXPORTS.filter((f) => !allowedExports || allowedExports.includes(f.id)).map((f) => (
              <Pressable
                key={f.id}
                onPress={() => void handleExport(f.id)}
                disabled={exporting !== null}
                style={[styles.exportButton, exporting !== null && { opacity: 0.5 }]}
              >
                {exporting === f.id ? (
                  <ActivityIndicator size={14} color={colors.accent} />
                ) : (
                  <Ionicons name="download-outline" size={14} color={colors.accent} />
                )}
                <Text style={styles.exportButtonText}>{f.label}</Text>
              </Pressable>
            ))}
          </View>
        </View>

        {draft.citations.length > 0 && (
          <View style={[styles.card, styles.citationsCard]}>
            <CitationPanel citations={draft.citations} />
          </View>
        )}
      </ScrollView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Rédaction</Text>
      </View>
      <View style={styles.flex}>{body}</View>
      <Text style={styles.disclaimer}>
        Avertissement : les documents générés sont des aides à la rédaction. Ils ne constituent pas
        un conseil juridique.
      </Text>
    </SafeAreaView>
  );
}

const makeStyles = (colors: ThemeColors) => StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  flex: { flex: 1 },
  header: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    backgroundColor: colors.surfaceElevated,
  },
  headerTitle: { fontSize: 17, fontWeight: "700", color: colors.ink },
  scroll: { padding: 16, paddingBottom: 24 },
  centerBlock: { flex: 1, alignItems: "center", justifyContent: "center", gap: 10, padding: 24 },
  mutedText: { fontSize: 13, color: colors.muted },
  errorText: { fontSize: 12, color: colors.danger },
  pageTitle: { fontSize: 20, fontWeight: "600", color: colors.ink },
  pageSubtitle: { fontSize: 13, color: colors.muted, marginTop: 4, marginBottom: 12 },
  searchField: { marginBottom: 16 },
  emptySearch: { fontSize: 13, color: colors.muted, textAlign: "center", paddingVertical: 32 },
  group: { marginBottom: 20 },
  groupHeader: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 10 },
  groupDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.accent },
  groupLabel: {
    fontSize: 11,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: colors.muted,
  },
  groupGrid: { gap: 10 },
  templateCard: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 12,
    padding: 14,
    gap: 6,
  },
  templateIcon: {
    width: 34,
    height: 34,
    borderRadius: 10,
    backgroundColor: colors.accentLight,
    alignItems: "center",
    justifyContent: "center",
  },
  templateLabel: { fontSize: 14, fontWeight: "500", color: colors.ink },
  templateDescription: { fontSize: 12, lineHeight: 17, color: colors.muted },
  backRow: { flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 4 },
  backText: { fontSize: 12, color: colors.muted },
  quotaCard: {
    borderWidth: 1,
    borderColor: colors.warnBorder,
    backgroundColor: colors.warnBg,
    borderRadius: 12,
    padding: 12,
    gap: 4,
    marginTop: 10,
  },
  quotaHeader: { flexDirection: "row", alignItems: "center", gap: 6 },
  quotaTitle: { fontSize: 13, fontWeight: "600", color: colors.warnText },
  quotaText: { fontSize: 12, color: colors.warnText },
  quotaHint: { fontSize: 11, color: colors.warnText, opacity: 0.8 },
  card: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 14,
    padding: 16,
    marginTop: 12,
    gap: 10,
  },
  cardTitle: { fontSize: 17, fontWeight: "600", color: colors.ink },
  cardSubtitle: { fontSize: 13, color: colors.muted },
  formFields: { gap: 12, marginTop: 4 },
  formField: { gap: 4 },
  fieldLabel: { fontSize: 12, fontWeight: "500", color: colors.inkSoft },
  requiredMark: { color: colors.danger },
  selectWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  selectOption: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  selectOptionActive: { borderColor: colors.accent, backgroundColor: colors.accentLight },
  selectOptionText: { fontSize: 13, color: colors.inkSoft },
  selectOptionTextActive: { color: colors.accent, fontWeight: "500" },
  resultActions: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  newDocButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  newDocText: { fontSize: 12, fontWeight: "500", color: colors.inkSoft },
  resultHeader: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 10,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    paddingBottom: 10,
  },
  resultTitleBlock: { flex: 1 },
  resultLatency: { fontSize: 10, color: colors.faint, marginTop: 4 },
  reviewBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 1,
    borderColor: colors.dangerBorder,
    backgroundColor: colors.dangerBg,
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  reviewBadgeText: { fontSize: 10, fontWeight: "600", color: colors.danger },
  reviewNotice: {
    borderWidth: 1,
    borderColor: colors.dangerBorder,
    backgroundColor: colors.dangerBg,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  reviewNoticeText: { fontSize: 12, color: colors.danger },
  warningsCard: {
    borderWidth: 1,
    borderColor: colors.warnBorder,
    backgroundColor: colors.warnBg,
    borderRadius: 10,
    padding: 12,
    gap: 4,
  },
  warningsTitle: {
    fontSize: 11,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: colors.warnText,
  },
  warningItem: { fontSize: 12, color: colors.warnText, paddingLeft: 4 },
  exportRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: 12,
  },
  exportButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  exportButtonText: { fontSize: 12, fontWeight: "500", color: colors.inkSoft },
  citationsCard: { padding: 0, overflow: "hidden" },
  disclaimer: {
    fontSize: 10,
    color: colors.faint,
    textAlign: "center",
    paddingHorizontal: 24,
    paddingVertical: 6,
    backgroundColor: colors.surfaceElevated,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
});
