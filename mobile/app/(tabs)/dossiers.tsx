// Mes dossiers: case folders grouping personal documents (PDF/DOCX/TXT/MD)
// for AI analysis. Three-level in-screen flow (mirrors the Explorer pattern):
//   1. folder list + unfiled documents + upload
//   2. folder detail: documents + case analysis (documents + legal corpus)
//   3. document detail: Q&A grounded on the document + extracted text
import React, { useCallback, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as DocumentPicker from "expo-document-picker";
import { useFocusEffect } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import Markdown from "../../src/components/Markdown";
import {
  analyzeDossier,
  analyzeFolder,
  createFolder,
  deleteDossier,
  deleteFolder,
  getDossierText,
  listDossiers,
  listFolders,
  moveDossier,
  uploadDossier,
  type Dossier,
  type DossierFolder,
  type FolderAnalysis,
} from "../../src/lib/api";
import { useAuth } from "../../src/lib/auth";
import { relativeDate } from "../../src/lib/format";
import { serifFont, type ThemeColors } from "../../src/theme";
import { useTheme } from "../../src/theme-context";

const PICKER_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "text/plain",
  "text/markdown",
];

interface Exchange {
  id: string;
  question: string;
  answer: string | null;
  truncated: boolean;
  error: string | null;
}

interface FolderExchange {
  id: string;
  question: string;
  result: FolderAnalysis | null;
  error: string | null;
}

function formatSize(bytes: number): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} Ko`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} Mo`;
}

function extLabel(dossier: Dossier): string {
  return dossier.extension.replace(/^\./, "").toUpperCase();
}

export default function DossiersScreen() {
  const { token } = useAuth();
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);

  // Level 1: folders + dossier list.
  const [dossiers, setDossiers] = useState<Dossier[] | null>(null);
  const [folders, setFolders] = useState<DossierFolder[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploadingName, setUploadingName] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [newFolderName, setNewFolderName] = useState("");
  const [creating, setCreating] = useState(false);

  // Level 2: selected folder, case-analysis thread.
  const [selectedFolder, setSelectedFolder] = useState<DossierFolder | null>(null);
  const [folderExchanges, setFolderExchanges] = useState<FolderExchange[]>([]);
  const [analysisMode, setAnalysisMode] = useState<"case" | "documents">("case");

  // Level 3: selected dossier, Q&A thread and extracted text.
  const [selected, setSelected] = useState<Dossier | null>(null);
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [textOpen, setTextOpen] = useState(false);
  const [fullText, setFullText] = useState<string | null>(null);
  const [textLoading, setTextLoading] = useState(false);
  const exchangeSeq = useRef(0);

  const reload = useCallback(() => {
    if (!token) return;
    setLoading(true);
    setLoadError(null);
    Promise.all([listDossiers(), listFolders()])
      .then(([docs, folds]) => {
        setDossiers(docs);
        setFolders(folds);
        setSelectedFolder((cur) => (cur ? (folds.find((f) => f.id === cur.id) ?? null) : null));
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Une erreur est survenue."))
      .finally(() => setLoading(false));
  }, [token]);

  useFocusEffect(
    useCallback(() => {
      reload();
    }, [reload]),
  );

  async function pickAndUpload() {
    if (uploadingName) return;
    setUploadError(null);
    let picked: DocumentPicker.DocumentPickerResult;
    try {
      picked = await DocumentPicker.getDocumentAsync({
        type: PICKER_TYPES,
        copyToCacheDirectory: true,
        multiple: false,
      });
    } catch {
      return; // picker unavailable — leave the list untouched
    }
    if (picked.canceled || !picked.assets?.length) return;
    const asset = picked.assets[0];
    setUploadingName(asset.name);
    try {
      await uploadDossier(
        { uri: asset.uri, name: asset.name, mimeType: asset.mimeType },
        "",
        null,
        selectedFolder?.id || undefined,
      );
      reload();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Échec de l'envoi du document.");
    } finally {
      setUploadingName(null);
    }
  }

  async function handleCreateFolder() {
    const name = newFolderName.trim();
    if (!name || creating) return;
    setCreating(true);
    try {
      await createFolder(name, "");
      setNewFolderName("");
      reload();
    } catch (err) {
      Alert.alert("Création impossible", err instanceof Error ? err.message : "");
    } finally {
      setCreating(false);
    }
  }

  function confirmDeleteFolder(folder: DossierFolder) {
    const buttons: Parameters<typeof Alert.alert>[2] = [
      { text: "Annuler", style: "cancel" },
    ];
    if (folder.document_count > 0) {
      buttons.push({
        text: "Déclasser les documents",
        onPress: () => {
          void deleteFolder(folder.id, false)
            .then(() => {
              if (selectedFolder?.id === folder.id) setSelectedFolder(null);
              reload();
            })
            .catch((err) => Alert.alert("Suppression impossible", err instanceof Error ? err.message : ""));
        },
      });
    }
    buttons.push({
      text: folder.document_count > 0 ? "Tout supprimer" : "Supprimer",
      style: "destructive",
      onPress: () => {
        void deleteFolder(folder.id, folder.document_count > 0)
          .then(() => {
            if (selectedFolder?.id === folder.id) setSelectedFolder(null);
            reload();
          })
          .catch((err) => Alert.alert("Suppression impossible", err instanceof Error ? err.message : ""));
      },
    });
    Alert.alert(
      "Supprimer ce dossier ?",
      folder.document_count > 0
        ? `« ${folder.name} » contient ${folder.document_count} document(s). Vous pouvez les déclasser (conservés hors dossier) ou tout supprimer.`
        : `« ${folder.name} » sera définitivement supprimé.`,
      buttons,
    );
  }

  function confirmDelete(dossier: Dossier) {
    Alert.alert(
      "Supprimer ce document ?",
      `« ${dossier.name} » et son texte extrait seront définitivement supprimés.`,
      [
        { text: "Annuler", style: "cancel" },
        {
          text: "Supprimer",
          style: "destructive",
          onPress: () => {
            void deleteDossier(dossier.id)
              .then(() => {
                if (selected?.id === dossier.id) setSelected(null);
                reload();
              })
              .catch((err) =>
                Alert.alert("Suppression impossible", err instanceof Error ? err.message : ""),
              );
          },
        },
      ],
    );
  }

  function openDossier(dossier: Dossier) {
    setSelected(dossier);
    setExchanges([]);
    setQuestion("");
    setTextOpen(false);
    setFullText(null);
  }

  function openFolder(folder: DossierFolder) {
    setSelectedFolder(folder);
    setFolderExchanges([]);
    setQuestion("");
    setUploadError(null);
  }

  function backToList() {
    setSelected(null);
    setSelectedFolder(null);
    setExchanges([]);
    setFolderExchanges([]);
    setFullText(null);
  }

  function ask() {
    if (asking) return;
    const q = question.trim();
    if (!q) return;
    const id = `q${++exchangeSeq.current}`;
    setAsking(true);
    setQuestion("");
    if (selectedFolder) {
      setFolderExchanges((prev) => [{ id, question: q, result: null, error: null }, ...prev]);
      analyzeFolder(selectedFolder.id, q, analysisMode)
        .then((res) => {
          setFolderExchanges((prev) =>
            prev.map((ex) => (ex.id === id ? { ...ex, result: res } : ex)),
          );
        })
        .catch((err) => {
          setFolderExchanges((prev) =>
            prev.map((ex) =>
              ex.id === id
                ? { ...ex, error: err instanceof Error ? err.message : "Échec de l'analyse." }
                : ex,
            ),
          );
        })
        .finally(() => setAsking(false));
      return;
    }
    if (!selected) return;
    setExchanges((prev) => [
      { id, question: q, answer: null, truncated: false, error: null },
      ...prev,
    ]);
    analyzeDossier(selected.id, q)
      .then((res) => {
        setExchanges((prev) =>
          prev.map((ex) =>
            ex.id === id ? { ...ex, answer: res.answer, truncated: res.truncated } : ex,
          ),
        );
      })
      .catch((err) => {
        setExchanges((prev) =>
          prev.map((ex) =>
            ex.id === id
              ? { ...ex, error: err instanceof Error ? err.message : "Échec de l'analyse." }
              : ex,
          ),
        );
      })
      .finally(() => setAsking(false));
  }

  function toggleText() {
    if (!selected) return;
    const next = !textOpen;
    setTextOpen(next);
    if (next && fullText === null && !textLoading) {
      setTextLoading(true);
      getDossierText(selected.id)
        .then((res) => setFullText(res.text))
        .catch(() => setFullText(null))
        .finally(() => setTextLoading(false));
    }
  }

  const folderDocs = selectedFolder
    ? (dossiers ?? []).filter((d) => d.folder_id === selectedFolder.id)
    : [];
  const unfiledDocs = (dossiers ?? []).filter((d) => !d.folder_id);

  function renderDocRow(item: Dossier) {
    return (
      <Pressable onPress={() => openDossier(item)} style={styles.docRow}>
        <View style={styles.extBadge}>
          <Text style={styles.extBadgeText}>{extLabel(item)}</Text>
        </View>
        <View style={styles.docTextBlock}>
          <Text style={styles.docTitle} numberOfLines={2}>
            {item.name}
          </Text>
          <Text style={styles.docMeta}>
            {item.status === "ready"
              ? [
                  item.page_count > 0
                    ? `${item.page_count} page${item.page_count > 1 ? "s" : ""}`
                    : null,
                  formatSize(item.size_bytes),
                  relativeDate(item.created_at),
                ]
                  .filter(Boolean)
                  .join(" — ")
              : "Lecture impossible"}
          </Text>
        </View>
        {item.status !== "ready" && (
          <Ionicons name="alert-circle-outline" size={16} color={colors.danger} />
        )}
        <Pressable
          onPress={() => confirmDelete(item)}
          style={styles.deleteButton}
          accessibilityLabel={`Supprimer ${item.name}`}
        >
          <Ionicons name="trash-outline" size={16} color={colors.faint} />
        </Pressable>
        <Ionicons name="chevron-forward" size={14} color={colors.faint} />
      </Pressable>
    );
  }

  const uploadCard = (
    <Pressable
      onPress={() => void pickAndUpload()}
      style={styles.uploadCard}
      accessibilityRole="button"
      accessibilityLabel="Ajouter un document"
    >
      {uploadingName ? (
        <>
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.uploadText}>Envoi et lecture de « {uploadingName} »…</Text>
        </>
      ) : (
        <>
          <Ionicons name="cloud-upload-outline" size={28} color={colors.gold} />
          <Text style={styles.uploadTitle}>Ajouter un document</Text>
          <Text style={styles.uploadHint}>
            PDF, DOCX, TXT ou MD{selectedFolder ? ` — classé dans « ${selectedFolder.name} »` : ""}
          </Text>
        </>
      )}
    </Pressable>
  );

  // -------------------------------------------------------------------------
  // Level 3 — document detail: Q&A + extracted text + folder assignment
  // -------------------------------------------------------------------------
  if (selected) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <View style={styles.header}>
          <Pressable onPress={() => setSelected(null)} style={styles.backButton} accessibilityLabel="Retour">
            <Ionicons name="chevron-back" size={18} color={colors.accent} />
            <Text style={styles.backText}>{selectedFolder ? selectedFolder.name : "Dossiers"}</Text>
          </Pressable>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {selected.name}
          </Text>
        </View>
        <KeyboardAvoidingView
          style={styles.flex}
          behavior={Platform.OS === "ios" ? "padding" : undefined}
        >
          <FlatList
            data={exchanges}
            inverted
            keyExtractor={(ex) => ex.id}
            contentContainerStyle={styles.thread}
            keyboardShouldPersistTaps="handled"
            ListHeaderComponent={
              <View>
                {/* Classer dans un dossier */}
                <ScrollView
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  contentContainerStyle={styles.chipRow}
                >
                  <Pressable
                    onPress={() => {
                      void moveDossier(selected.id, "").then(setSelected).then(reload).catch(() => {});
                    }}
                    style={[styles.folderChip, !selected.folder_id && styles.folderChipActive]}
                  >
                    <Text
                      style={[
                        styles.folderChipText,
                        !selected.folder_id && styles.folderChipTextActive,
                      ]}
                    >
                      Non classé
                    </Text>
                  </Pressable>
                  {(folders ?? []).map((f) => (
                    <Pressable
                      key={f.id}
                      onPress={() => {
                        void moveDossier(selected.id, f.id).then(setSelected).then(reload).catch(() => {});
                      }}
                      style={[styles.folderChip, selected.folder_id === f.id && styles.folderChipActive]}
                    >
                      <Ionicons
                        name="folder-outline"
                        size={12}
                        color={selected.folder_id === f.id ? colors.surfaceElevated : colors.muted}
                      />
                      <Text
                        style={[
                          styles.folderChipText,
                          selected.folder_id === f.id && styles.folderChipTextActive,
                        ]}
                      >
                        {f.name}
                      </Text>
                    </Pressable>
                  ))}
                </ScrollView>
                <Pressable onPress={toggleText} style={styles.textToggle}>
                  <Ionicons
                    name={textOpen ? "chevron-down" : "chevron-forward"}
                    size={12}
                    color={colors.accent}
                  />
                  <Text style={styles.textToggleLabel}>
                    {textOpen ? "Masquer le texte extrait" : "Voir le texte extrait"}
                  </Text>
                </Pressable>
                {textOpen && (
                  <View style={styles.textBox}>
                    {textLoading ? (
                      <View style={styles.textLoadingRow}>
                        <ActivityIndicator size="small" color={colors.accent} />
                        <Text style={styles.mutedText}>Chargement du texte…</Text>
                      </View>
                    ) : (
                      <ScrollView style={styles.textScroll} nestedScrollEnabled>
                        <Text style={styles.textContent} selectable>
                          {fullText ?? "Texte indisponible."}
                        </Text>
                      </ScrollView>
                    )}
                  </View>
                )}
                {selected.status !== "ready" && (
                  <View style={styles.errorBanner}>
                    <Ionicons name="alert-circle-outline" size={16} color={colors.danger} />
                    <Text style={styles.errorBannerText}>
                      Ce document n'a pas pu être lu{selected.error ? ` : ${selected.error}` : ""}.
                    </Text>
                  </View>
                )}
              </View>
            }
            renderItem={({ item }) => (
              <View style={styles.exchange}>
                <View style={styles.questionBubble}>
                  <Text style={styles.questionText}>{item.question}</Text>
                </View>
                <View style={styles.answerCard}>
                  {item.error ? (
                    <View style={styles.errorRow}>
                      <Ionicons name="alert-circle-outline" size={14} color={colors.danger} />
                      <Text style={styles.errorText}>{item.error}</Text>
                    </View>
                  ) : item.answer === null ? (
                    <View style={styles.errorRow}>
                      <ActivityIndicator size="small" color={colors.accent} />
                      <Text style={styles.mutedText}>Analyse en cours…</Text>
                    </View>
                  ) : (
                    <>
                      <Markdown>{item.answer}</Markdown>
                      {item.truncated && (
                        <Text style={styles.truncatedNote}>
                          Document long : l'analyse porte sur le début du texte extrait.
                        </Text>
                      )}
                    </>
                  )}
                </View>
              </View>
            )}
          />
          <View style={styles.composer}>
            <TextInput
              value={question}
              onChangeText={setQuestion}
              placeholder="Question sur ce document…"
              placeholderTextColor={colors.faint}
              style={styles.composerInput}
              multiline
              editable={selected.status === "ready" && !asking}
              onSubmitEditing={ask}
              blurOnSubmit
            />
            <Pressable
              onPress={ask}
              disabled={asking || !question.trim() || selected.status !== "ready"}
              style={[
                styles.sendButton,
                (asking || !question.trim() || selected.status !== "ready") &&
                  styles.sendButtonDisabled,
              ]}
              accessibilityLabel="Analyser"
            >
              {asking ? (
                <ActivityIndicator size="small" color={colors.gold} />
              ) : (
                <Ionicons name="arrow-up" size={18} color={colors.gold} />
              )}
            </Pressable>
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    );
  }

  // -------------------------------------------------------------------------
  // Level 2 — folder detail: documents + case analysis
  // -------------------------------------------------------------------------
  if (selectedFolder) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <View style={styles.header}>
          <Pressable onPress={backToList} style={styles.backButton} accessibilityLabel="Retour">
            <Ionicons name="chevron-back" size={18} color={colors.accent} />
            <Text style={styles.backText}>Dossiers</Text>
          </Pressable>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {selectedFolder.name}
          </Text>
          <Pressable
            onPress={() => confirmDeleteFolder(selectedFolder)}
            style={styles.deleteButton}
            accessibilityLabel="Supprimer le dossier"
          >
            <Ionicons name="trash-outline" size={16} color={colors.faint} />
          </Pressable>
        </View>
        <KeyboardAvoidingView
          style={styles.flex}
          behavior={Platform.OS === "ios" ? "padding" : undefined}
        >
          <FlatList
            data={folderExchanges}
            inverted
            keyExtractor={(ex) => ex.id}
            contentContainerStyle={styles.thread}
            keyboardShouldPersistTaps="handled"
            ListHeaderComponent={
              <View>
                <Text style={styles.sectionTitle}>
                  {folderDocs.length} document{folderDocs.length > 1 ? "s" : ""} dans ce dossier
                </Text>
                {uploadCard}
                {uploadError && (
                  <View style={styles.errorBanner}>
                    <Ionicons name="alert-circle-outline" size={16} color={colors.danger} />
                    <Text style={styles.errorBannerText}>{uploadError}</Text>
                  </View>
                )}
                {folderDocs.map(renderDocRow)}
                {/* Mode toggle */}
                <View style={styles.modeRow}>
                  <Pressable
                    onPress={() => setAnalysisMode("case")}
                    style={[styles.modeChip, analysisMode === "case" && styles.modeChipActive]}
                  >
                    <Ionicons
                      name="scale-outline"
                      size={12}
                      color={analysisMode === "case" ? colors.surfaceElevated : colors.muted}
                    />
                    <Text
                      style={[styles.modeChipText, analysisMode === "case" && styles.modeChipTextActive]}
                    >
                      Analyse complète (documents + loi)
                    </Text>
                  </Pressable>
                  <Pressable
                    onPress={() => setAnalysisMode("documents")}
                    style={[styles.modeChip, analysisMode === "documents" && styles.modeChipActive]}
                  >
                    <Text
                      style={[
                        styles.modeChipText,
                        analysisMode === "documents" && styles.modeChipTextActive,
                      ]}
                    >
                      Documents seuls
                    </Text>
                  </Pressable>
                </View>
              </View>
            }
            renderItem={({ item }) => (
              <View style={styles.exchange}>
                <View style={styles.questionBubble}>
                  <Text style={styles.questionText}>{item.question}</Text>
                </View>
                <View style={styles.answerCard}>
                  {item.error ? (
                    <View style={styles.errorRow}>
                      <Ionicons name="alert-circle-outline" size={14} color={colors.danger} />
                      <Text style={styles.errorText}>{item.error}</Text>
                    </View>
                  ) : item.result === null ? (
                    <View style={styles.errorRow}>
                      <ActivityIndicator size="small" color={colors.accent} />
                      <Text style={styles.mutedText}>
                        {analysisMode === "case"
                          ? "Analyse de l'affaire en cours (pièces + recherche juridique)…"
                          : "Lecture des documents en cours…"}
                      </Text>
                    </View>
                  ) : (
                    <>
                      <Markdown>{item.result.answer}</Markdown>
                      {item.result.citations.length > 0 && (
                        <View style={styles.sourcesBox}>
                          <Text style={styles.sourcesTitle}>Sources</Text>
                          {item.result.citations.map((c, j) => (
                            <Text key={j} style={styles.sourceLine}>
                              {c.label}
                              {c.document_name ? ` — ${c.document_name}` : ""}
                            </Text>
                          ))}
                        </View>
                      )}
                      <Text style={styles.truncatedNote}>
                        {item.result.mode === "case"
                          ? "Analyse croisant vos documents et le corpus juridique."
                          : "Analyse fondée uniquement sur vos documents."}
                        {item.result.truncated ? " Synthèse des pièces utilisée (dossier volumineux)." : ""}
                      </Text>
                    </>
                  )}
                </View>
              </View>
            )}
          />
          <View style={styles.composer}>
            <TextInput
              value={question}
              onChangeText={setQuestion}
              placeholder="Décrivez votre affaire ou posez votre question…"
              placeholderTextColor={colors.faint}
              style={styles.composerInput}
              multiline
              editable={folderDocs.length > 0 && !asking}
              onSubmitEditing={ask}
              blurOnSubmit
            />
            <Pressable
              onPress={ask}
              disabled={asking || !question.trim() || folderDocs.length === 0}
              style={[
                styles.sendButton,
                (asking || !question.trim() || folderDocs.length === 0) && styles.sendButtonDisabled,
              ]}
              accessibilityLabel="Analyser le dossier"
            >
              {asking ? (
                <ActivityIndicator size="small" color={colors.gold} />
              ) : (
                <Ionicons name="arrow-up" size={18} color={colors.gold} />
              )}
            </Pressable>
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    );
  }

  // -------------------------------------------------------------------------
  // Level 1 — folder list + unfiled documents + upload
  // -------------------------------------------------------------------------
  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Ionicons name="folder-open" size={17} color={colors.gold} />
        <Text style={styles.headerBrandTitle}>Mes dossiers</Text>
      </View>

      <FlatList
        data={unfiledDocs}
        keyExtractor={(d) => d.id}
        contentContainerStyle={styles.list}
        refreshing={loading}
        onRefresh={reload}
        ListHeaderComponent={
          <View>
            <Text style={styles.subtitle}>
              Créez un dossier par affaire, déposez vos pièces, et Yawoto analyse votre cas à la
              lumière de la loi.
            </Text>
            {/* New folder */}
            <View style={styles.newFolderRow}>
              <TextInput
                value={newFolderName}
                onChangeText={setNewFolderName}
                placeholder="Nouveau dossier (ex. : Litige bail Kaboré)…"
                placeholderTextColor={colors.faint}
                style={styles.newFolderInput}
                onSubmitEditing={() => void handleCreateFolder()}
                returnKeyType="done"
              />
              <Pressable
                onPress={() => void handleCreateFolder()}
                disabled={creating || !newFolderName.trim()}
                style={[styles.newFolderButton, (creating || !newFolderName.trim()) && styles.sendButtonDisabled]}
                accessibilityLabel="Créer le dossier"
              >
                {creating ? (
                  <ActivityIndicator size="small" color={colors.gold} />
                ) : (
                  <Ionicons name="add" size={20} color={colors.gold} />
                )}
              </Pressable>
            </View>
            {/* Folders */}
            {(folders ?? []).map((folder) => (
              <Pressable key={folder.id} onPress={() => openFolder(folder)} style={styles.folderRow}>
                <View style={styles.folderBadge}>
                  <Ionicons name="folder-open" size={18} color={colors.gold} />
                </View>
                <View style={styles.docTextBlock}>
                  <Text style={styles.docTitle} numberOfLines={2}>
                    {folder.name}
                  </Text>
                  <Text style={styles.docMeta}>
                    {folder.document_count} document{folder.document_count > 1 ? "s" : ""} —{" "}
                    {relativeDate(folder.created_at)}
                  </Text>
                </View>
                <Pressable
                  onPress={() => confirmDeleteFolder(folder)}
                  style={styles.deleteButton}
                  accessibilityLabel={`Supprimer le dossier ${folder.name}`}
                >
                  <Ionicons name="trash-outline" size={16} color={colors.faint} />
                </Pressable>
                <Ionicons name="chevron-forward" size={14} color={colors.faint} />
              </Pressable>
            ))}
            {uploadCard}
            {uploadError && (
              <View style={styles.errorBanner}>
                <Ionicons name="alert-circle-outline" size={16} color={colors.danger} />
                <Text style={styles.errorBannerText}>{uploadError}</Text>
              </View>
            )}
            {unfiledDocs.length > 0 && (
              <Text style={styles.sectionTitle}>Documents non classés</Text>
            )}
          </View>
        }
        ListEmptyComponent={
          loading || loadError || (folders ?? []).length > 0 ? null : (
            <View style={styles.emptyCard}>
              <Ionicons name="folder-open-outline" size={32} color={colors.faint} />
              <Text style={styles.emptyTitle}>Aucun dossier</Text>
              <Text style={styles.mutedText}>
                Créez un dossier et déposez vos documents : Yawoto les lit et analyse votre affaire.
              </Text>
            </View>
          )
        }
        renderItem={({ item }) => renderDocRow(item)}
        ListFooterComponent={
          loadError ? (
            <View style={styles.errorBanner}>
              <Ionicons name="alert-circle-outline" size={16} color={colors.danger} />
              <Text style={styles.errorBannerText}>{loadError}</Text>
              <Pressable onPress={reload}>
                <Text style={styles.retryText}>Réessayer</Text>
              </Pressable>
            </View>
          ) : null
        }
      />
    </SafeAreaView>
  );
}

const makeStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    safe: { flex: 1, backgroundColor: colors.surface },
    flex: { flex: 1 },
    header: {
      flexDirection: "row",
      alignItems: "center",
      gap: 8,
      paddingHorizontal: 16,
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
      backgroundColor: colors.surfaceElevated,
    },
    headerBrandTitle: { fontFamily: serifFont, fontSize: 20, fontWeight: "700", color: colors.ink },
    headerTitle: { flex: 1, fontSize: 16, fontWeight: "700", color: colors.ink },
    backButton: {
      flexDirection: "row",
      alignItems: "center",
      gap: 2,
      paddingVertical: 4,
      paddingRight: 8,
      marginLeft: -6,
    },
    backText: { fontSize: 15, color: colors.accent },
    subtitle: {
      fontSize: 13,
      color: colors.muted,
      marginTop: 4,
      marginBottom: 12,
      paddingHorizontal: 4,
    },
    sectionTitle: {
      fontSize: 12,
      fontWeight: "600",
      color: colors.muted,
      textTransform: "uppercase",
      letterSpacing: 0.4,
      marginTop: 8,
      marginBottom: 8,
      paddingHorizontal: 4,
    },
    list: { padding: 12, gap: 6, paddingBottom: 24 },
    uploadCard: {
      alignItems: "center",
      justifyContent: "center",
      gap: 6,
      borderWidth: 2,
      borderStyle: "dashed",
      borderColor: colors.border,
      borderRadius: 16,
      backgroundColor: colors.surfaceElevated,
      paddingVertical: 24,
      marginBottom: 8,
    },
    uploadTitle: { fontSize: 14, fontWeight: "600", color: colors.ink },
    uploadHint: { fontSize: 11, color: colors.faint, textAlign: "center", paddingHorizontal: 12 },
    uploadText: { fontSize: 13, color: colors.muted, textAlign: "center", paddingHorizontal: 16 },
    newFolderRow: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 10 },
    newFolderInput: {
      flex: 1,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      backgroundColor: colors.surfaceElevated,
      paddingHorizontal: 12,
      paddingVertical: 10,
      fontSize: 14,
      color: colors.ink,
    },
    newFolderButton: {
      width: 42,
      height: 42,
      alignItems: "center",
      justifyContent: "center",
      borderRadius: 12,
      backgroundColor: colors.accent,
    },
    folderRow: {
      flexDirection: "row",
      alignItems: "center",
      gap: 10,
      borderWidth: 1,
      borderColor: colors.gold,
      borderRadius: 12,
      backgroundColor: colors.surfaceElevated,
      paddingHorizontal: 12,
      paddingVertical: 12,
      marginBottom: 6,
    },
    folderBadge: {
      width: 40,
      height: 40,
      alignItems: "center",
      justifyContent: "center",
      borderRadius: 10,
      backgroundColor: colors.goldLight,
    },
    docRow: {
      flexDirection: "row",
      alignItems: "center",
      gap: 10,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      backgroundColor: colors.surfaceElevated,
      paddingHorizontal: 12,
      paddingVertical: 12,
      marginBottom: 6,
    },
    extBadge: {
      minWidth: 44,
      alignItems: "center",
      borderRadius: 8,
      backgroundColor: colors.goldLight,
      paddingHorizontal: 8,
      paddingVertical: 6,
    },
    extBadgeText: { fontSize: 10, fontWeight: "700", color: colors.gold },
    docTextBlock: { flex: 1 },
    docTitle: { fontSize: 14, fontWeight: "500", color: colors.ink },
    docMeta: { fontSize: 11, color: colors.muted, marginTop: 2 },
    deleteButton: { padding: 6 },
    emptyCard: {
      alignItems: "center",
      gap: 8,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      backgroundColor: colors.surfaceElevated,
      padding: 32,
      marginTop: 12,
    },
    emptyTitle: { fontSize: 14, fontWeight: "500", color: colors.ink },
    mutedText: { fontSize: 13, color: colors.muted, textAlign: "center" },
    retryText: { fontSize: 13, fontWeight: "500", color: colors.accent },
    errorBanner: {
      flexDirection: "row",
      alignItems: "center",
      gap: 8,
      borderWidth: 1,
      borderColor: colors.dangerBorder,
      borderRadius: 10,
      backgroundColor: colors.dangerBg,
      padding: 10,
      marginBottom: 8,
    },
    errorBannerText: { flex: 1, fontSize: 12, color: colors.danger },
    chipRow: { gap: 6, paddingVertical: 4, paddingRight: 8 },
    folderChip: {
      flexDirection: "row",
      alignItems: "center",
      gap: 4,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 999,
      backgroundColor: colors.surfaceElevated,
      paddingHorizontal: 10,
      paddingVertical: 6,
    },
    folderChipActive: { backgroundColor: colors.accent, borderColor: colors.accent },
    folderChipText: { fontSize: 11, fontWeight: "500", color: colors.muted },
    folderChipTextActive: { color: colors.surfaceElevated },
    modeRow: { flexDirection: "row", gap: 6, marginTop: 10, marginBottom: 4 },
    modeChip: {
      flexDirection: "row",
      alignItems: "center",
      gap: 4,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 999,
      backgroundColor: colors.surfaceElevated,
      paddingHorizontal: 10,
      paddingVertical: 6,
    },
    modeChipActive: { backgroundColor: colors.accent, borderColor: colors.accent },
    modeChipText: { fontSize: 11, fontWeight: "500", color: colors.muted },
    modeChipTextActive: { color: colors.surfaceElevated },
    thread: { padding: 12, gap: 12, paddingBottom: 16 },
    exchange: { gap: 6 },
    questionBubble: {
      alignSelf: "flex-end",
      maxWidth: "85%",
      borderRadius: 16,
      borderBottomRightRadius: 4,
      borderWidth: 1,
      borderColor: colors.accentLight,
      backgroundColor: colors.accentLight,
      paddingHorizontal: 14,
      paddingVertical: 10,
    },
    questionText: { fontSize: 14, color: colors.ink },
    answerCard: {
      alignSelf: "stretch",
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      backgroundColor: colors.surfaceElevated,
      padding: 12,
    },
    sourcesBox: { marginTop: 8, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 6 },
    sourcesTitle: { fontSize: 11, fontWeight: "600", color: colors.muted, marginBottom: 2 },
    sourceLine: { fontSize: 11, color: colors.muted },
    errorRow: { flexDirection: "row", alignItems: "center", gap: 8 },
    errorText: { flex: 1, fontSize: 12, color: colors.danger },
    truncatedNote: { fontSize: 11, color: colors.faint, marginTop: 8 },
    textToggle: {
      flexDirection: "row",
      alignItems: "center",
      gap: 4,
      alignSelf: "flex-start",
      paddingVertical: 6,
      paddingHorizontal: 4,
    },
    textToggleLabel: { fontSize: 12, fontWeight: "500", color: colors.accent },
    textBox: {
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      backgroundColor: colors.surfaceElevated,
      padding: 12,
      marginBottom: 4,
    },
    textLoadingRow: { flexDirection: "row", alignItems: "center", gap: 8 },
    textScroll: { maxHeight: 320 },
    textContent: { fontSize: 12, lineHeight: 18, color: colors.inkSoft },
    composer: {
      flexDirection: "row",
      alignItems: "flex-end",
      gap: 8,
      paddingHorizontal: 12,
      paddingVertical: 10,
      borderTopWidth: 1,
      borderTopColor: colors.border,
      backgroundColor: colors.surfaceElevated,
    },
    composerInput: {
      flex: 1,
      maxHeight: 120,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 20,
      backgroundColor: colors.surface,
      paddingHorizontal: 14,
      paddingVertical: 10,
      fontSize: 14,
      color: colors.ink,
    },
    sendButton: {
      width: 40,
      height: 40,
      alignItems: "center",
      justifyContent: "center",
      borderRadius: 20,
      backgroundColor: colors.accent,
    },
    sendButtonDisabled: { opacity: 0.5 },
  });
