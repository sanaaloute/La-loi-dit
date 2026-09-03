// Corpus browser ("Explorer"): three-level in-screen flow —
//   1. document list grouped by corpus folder, with full-text search
//   2. article index of one document
//   3. full article reader
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  SectionList,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  getArticle,
  listArticles,
  listSources,
  searchEvidence,
  type ArticleIndexEntry,
  type ArticleLookupResponse,
  type EvidenceChunk,
  type SourceListItem,
} from "../../src/lib/api";
import { useAuth } from "../../src/lib/auth";
import { localLongDate } from "../../src/lib/format";
import type { ThemeColors } from "../../src/theme";
import { useTheme } from "../../src/theme-context";

const FOLDER_LABELS: Record<string, string> = {
  bf: "Burkina Faso",
  ohada: "OHADA",
  uemoa: "UEMOA",
  cima: "CIMA",
};

const FOLDER_ORDER = ["bf", "ohada", "uemoa", "cima", "autres"];

function folderLabel(folder: string): string {
  return FOLDER_LABELS[folder] ?? "Autres";
}

function folderKey(folder: string): string {
  return FOLDER_LABELS[folder] ? folder : "autres";
}

export default function ExplorerScreen() {
  const { token } = useAuth();
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);

  // Level 1: documents + search.
  const [sources, setSources] = useState<SourceListItem[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<EvidenceChunk[] | null>(null);
  const [searching, setSearching] = useState(false);

  // Level 2: article index of the selected document.
  const [doc, setDoc] = useState<SourceListItem | null>(null);
  const [articles, setArticles] = useState<ArticleIndexEntry[] | null>(null);
  const [articlesLoading, setArticlesLoading] = useState(false);
  const [articlesError, setArticlesError] = useState<string | null>(null);

  // Level 3: full article reader.
  const [article, setArticle] = useState<ArticleLookupResponse | null>(null);
  const [articleLoading, setArticleLoading] = useState(false);
  const [articleError, setArticleError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!token) return;
    setLoading(true);
    setLoadError(null);
    listSources()
      .then(setSources)
      .catch((err) =>
        setLoadError(err instanceof Error ? err.message : "Une erreur est survenue."),
      )
      .finally(() => setLoading(false));
  }, [token]);

  useFocusEffect(
    useCallback(() => {
      reload();
    }, [reload]),
  );

  // Debounced full-text search (min 2 chars); replaces the document list.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setResults(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    const t = setTimeout(() => {
      searchEvidence(q)
        .then((res) => setResults(res.results))
        .catch(() => setResults([]))
        .finally(() => setSearching(false));
    }, 400);
    return () => clearTimeout(t);
  }, [query]);

  const sections = useMemo(() => {
    if (!sources) return [];
    const groups = new Map<string, SourceListItem[]>();
    for (const s of sources) {
      const key = folderKey(s.folder);
      const list = groups.get(key);
      if (list) list.push(s);
      else groups.set(key, [s]);
    }
    return FOLDER_ORDER.filter((f) => groups.has(f)).map((f) => ({
      title: folderLabel(f),
      data: groups.get(f) ?? [],
    }));
  }, [sources]);

  function openDocument(item: SourceListItem) {
    setDoc(item);
    setArticles(null);
    setArticlesError(null);
    setArticlesLoading(true);
    listArticles(item.document_id)
      .then(setArticles)
      .catch((err) =>
        setArticlesError(err instanceof Error ? err.message : "Une erreur est survenue."),
      )
      .finally(() => setArticlesLoading(false));
  }

  function openArticle(documentId: string, articleNumber: string) {
    setArticle(null);
    setArticleError(null);
    setArticleLoading(true);
    getArticle(documentId, articleNumber)
      .then(setArticle)
      .catch((err) =>
        setArticleError(err instanceof Error ? err.message : "Une erreur est survenue."),
      )
      .finally(() => setArticleLoading(false));
  }

  function backFromArticle() {
    setArticle(null);
    setArticleError(null);
  }

  function backFromDocument() {
    setDoc(null);
    setArticles(null);
    setArticlesError(null);
  }

  // -------------------------------------------------------------------------
  // Level 3 — article reader
  // -------------------------------------------------------------------------
  if (doc || article || articleLoading || articleError) {
    if (article || articleLoading || articleError) {
      return (
        <SafeAreaView style={styles.safe} edges={["top"]}>
          <View style={styles.header}>
            <Pressable onPress={backFromArticle} style={styles.backButton} accessibilityLabel="Retour">
              <Ionicons name="chevron-back" size={18} color={colors.accent} />
              <Text style={styles.backText}>Retour</Text>
            </Pressable>
            <Text style={styles.headerTitle} numberOfLines={1}>
              {article ? `Article ${article.article}` : "Article"}
            </Text>
          </View>
          {articleLoading ? (
            <View style={styles.centerFill}>
              <ActivityIndicator color={colors.accent} />
              <Text style={styles.mutedText}>Chargement de l'article…</Text>
            </View>
          ) : articleError ? (
            <View style={styles.centerFill}>
              <Ionicons name="alert-circle-outline" size={32} color={colors.faint} />
              <Text style={styles.mutedText}>{articleError}</Text>
            </View>
          ) : article ? (
            <FlatList
              data={article.chunks}
              keyExtractor={(c) => c.chunk_id}
              contentContainerStyle={styles.readerList}
              ListHeaderComponent={
                <View style={styles.readerHeader}>
                  <Text style={styles.readerDoc} numberOfLines={2}>
                    {article.chunks[0]?.document_name ?? doc?.document_name ?? ""}
                  </Text>
                  {article.count > 1 && (
                    <Text style={styles.readerMeta}>
                      {article.count} extraits indexés pour cet article
                    </Text>
                  )}
                </View>
              }
              renderItem={({ item }) => (
                <View style={styles.chunkCard}>
                  {item.section ? <Text style={styles.chunkSection}>{item.section}</Text> : null}
                  <Text style={styles.chunkContent} selectable>
                    {item.content}
                  </Text>
                  {item.page != null && (
                    <Text style={styles.chunkPage}>Page {item.page}</Text>
                  )}
                </View>
              )}
            />
          ) : null}
        </SafeAreaView>
      );
    }

    // -----------------------------------------------------------------------
    // Level 2 — article index of one document
    // -----------------------------------------------------------------------
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <View style={styles.header}>
          <Pressable onPress={backFromDocument} style={styles.backButton} accessibilityLabel="Retour">
            <Ionicons name="chevron-back" size={18} color={colors.accent} />
            <Text style={styles.backText}>Retour</Text>
          </Pressable>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {doc?.document_name ?? "Document"}
          </Text>
        </View>
        {articlesLoading ? (
          <View style={styles.centerFill}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.mutedText}>Chargement des articles…</Text>
          </View>
        ) : articlesError ? (
          <View style={styles.centerFill}>
            <Ionicons name="alert-circle-outline" size={32} color={colors.faint} />
            <Text style={styles.mutedText}>{articlesError}</Text>
          </View>
        ) : !articles || articles.length === 0 ? (
          <View style={styles.centerFill}>
            <Ionicons name="document-outline" size={32} color={colors.faint} />
            <Text style={styles.mutedText}>Aucun article indexé pour ce document.</Text>
          </View>
        ) : (
          <FlatList
            data={articles}
            keyExtractor={(a) => a.article}
            contentContainerStyle={styles.list}
            renderItem={({ item }) => (
              <Pressable
                onPress={() => doc && openArticle(doc.document_id, item.article)}
                style={styles.articleRow}
              >
                <View style={styles.articleBadge}>
                  <Text style={styles.articleBadgeText} numberOfLines={1}>
                    {item.article}
                  </Text>
                </View>
                <View style={styles.articleTextBlock}>
                  {item.section ? (
                    <Text style={styles.articleSection} numberOfLines={1}>
                      {item.section}
                    </Text>
                  ) : null}
                  <Text style={styles.articlePreview} numberOfLines={2}>
                    {item.preview}
                  </Text>
                </View>
                <Ionicons name="chevron-forward" size={14} color={colors.faint} />
              </Pressable>
            )}
          />
        )}
      </SafeAreaView>
    );
  }

  // ---------------------------------------------------------------------------
  // Level 1 — document list + search
  // ---------------------------------------------------------------------------
  const searchActive = query.trim().length >= 2;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Explorer</Text>
      </View>
      <View style={styles.searchRow}>
        <Ionicons name="search" size={16} color={colors.faint} />
        <TextInput
          value={query}
          onChangeText={setQuery}
          placeholder="Rechercher dans les textes…"
          placeholderTextColor={colors.faint}
          autoCapitalize="none"
          autoCorrect={false}
          returnKeyType="search"
          style={styles.searchInput}
        />
        {query.length > 0 && (
          <Pressable onPress={() => setQuery("")} accessibilityLabel="Effacer la recherche">
            <Ionicons name="close-circle" size={16} color={colors.faint} />
          </Pressable>
        )}
      </View>

      {searchActive ? (
        searching && results === null ? (
          <View style={styles.centerFill}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.mutedText}>Recherche…</Text>
          </View>
        ) : !results || results.length === 0 ? (
          <View style={styles.centerFill}>
            {searching ? (
              <ActivityIndicator color={colors.accent} />
            ) : (
              <>
                <Ionicons name="search" size={32} color={colors.faint} />
                <Text style={styles.mutedText}>Aucun passage ne correspond à cette recherche.</Text>
              </>
            )}
          </View>
        ) : (
          <FlatList
            data={results}
            keyExtractor={(c) => c.chunk_id}
            contentContainerStyle={styles.list}
            keyboardShouldPersistTaps="handled"
            renderItem={({ item }) => {
              const openable = Boolean(item.document_id && item.article);
              return (
                <Pressable
                  onPress={
                    openable
                      ? () => openArticle(item.document_id as string, item.article as string)
                      : undefined
                  }
                  style={styles.resultCard}
                >
                  <View style={styles.resultHeader}>
                    <Ionicons name="document-text-outline" size={14} color={colors.accent} />
                    <Text style={styles.resultDoc} numberOfLines={1}>
                      {item.document_name}
                    </Text>
                    {item.article ? (
                      <View style={styles.resultArticleBadge}>
                        <Text style={styles.resultArticleText} numberOfLines={1}>
                          Art. {item.article}
                        </Text>
                      </View>
                    ) : null}
                  </View>
                  <Text style={styles.resultContent} numberOfLines={3}>
                    {item.content}
                  </Text>
                </Pressable>
              );
            }}
          />
        )
      ) : loading && sources === null ? (
        <View style={styles.centerFill}>
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.mutedText}>Chargement des textes…</Text>
        </View>
      ) : loadError ? (
        <View style={styles.centerFill}>
          <Ionicons name="alert-circle-outline" size={32} color={colors.faint} />
          <Text style={styles.mutedText}>{loadError}</Text>
          <Pressable onPress={reload} style={styles.retryButton}>
            <Text style={styles.retryText}>Réessayer</Text>
          </Pressable>
        </View>
      ) : !sources || sources.length === 0 ? (
        <View style={styles.centerFill}>
          <Ionicons name="library-outline" size={32} color={colors.faint} />
          <Text style={styles.mutedText}>Aucun texte indexé pour le moment.</Text>
        </View>
      ) : (
        <SectionList
          sections={sections}
          keyExtractor={(s) => s.document_id}
          contentContainerStyle={styles.list}
          stickySectionHeadersEnabled={false}
          refreshing={loading}
          onRefresh={reload}
          keyboardShouldPersistTaps="handled"
          renderSectionHeader={({ section }) => (
            <Text style={styles.sectionHeader}>{section.title}</Text>
          )}
          renderItem={({ item }) => (
            <Pressable onPress={() => openDocument(item)} style={styles.docRow}>
              <View style={styles.docTextBlock}>
                <Text style={styles.docTitle} numberOfLines={2}>
                  {item.document_name}
                </Text>
                <Text style={styles.docMeta}>
                  {item.chunk_count} passage{item.chunk_count > 1 ? "s" : ""}
                  {item.publication_date ? ` — ${localLongDate(item.publication_date)}` : ""}
                </Text>
              </View>
              {item.document_type ? (
                <View style={styles.typeBadge}>
                  <Text style={styles.typeBadgeText} numberOfLines={1}>
                    {item.document_type}
                  </Text>
                </View>
              ) : null}
              <Ionicons name="chevron-forward" size={14} color={colors.faint} />
            </Pressable>
          )}
        />
      )}
    </SafeAreaView>
  );
}

const makeStyles = (colors: ThemeColors) => StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
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
  headerTitle: { flex: 1, fontSize: 17, fontWeight: "700", color: colors.ink },
  backButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 2,
    paddingVertical: 4,
    paddingRight: 8,
  },
  backText: { fontSize: 15, color: colors.accent },
  centerFill: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8, padding: 24 },
  mutedText: { fontSize: 13, color: colors.muted, textAlign: "center" },
  retryButton: {
    borderWidth: 1,
    borderColor: colors.accent,
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 8,
    marginTop: 4,
  },
  retryText: { fontSize: 13, fontWeight: "500", color: colors.accent },
  searchRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    margin: 12,
    marginBottom: 4,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    backgroundColor: colors.surfaceElevated,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  searchInput: { flex: 1, fontSize: 14, color: colors.ink, padding: 0 },
  list: { padding: 12, gap: 6, paddingBottom: 24 },
  sectionHeader: {
    fontSize: 12,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: colors.muted,
    marginTop: 10,
    marginBottom: 2,
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
  },
  docTextBlock: { flex: 1 },
  docTitle: { fontSize: 14, fontWeight: "500", color: colors.ink },
  docMeta: { fontSize: 11, color: colors.muted, marginTop: 2 },
  typeBadge: {
    maxWidth: 110,
    borderWidth: 1,
    borderColor: colors.accent,
    backgroundColor: colors.accentLight,
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  typeBadgeText: { fontSize: 10, fontWeight: "500", color: colors.accent },
  resultCard: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    backgroundColor: colors.surfaceElevated,
    padding: 12,
    gap: 6,
  },
  resultHeader: { flexDirection: "row", alignItems: "center", gap: 6 },
  resultDoc: { flex: 1, fontSize: 12, fontWeight: "500", color: colors.inkSoft },
  resultArticleBadge: {
    borderWidth: 1,
    borderColor: colors.accent,
    backgroundColor: colors.accentLight,
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  resultArticleText: { fontSize: 10, fontWeight: "600", color: colors.accent },
  resultContent: { fontSize: 13, lineHeight: 19, color: colors.inkSoft },
  articleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    backgroundColor: colors.surfaceElevated,
    paddingHorizontal: 12,
    paddingVertical: 12,
  },
  articleBadge: {
    minWidth: 44,
    alignItems: "center",
    borderWidth: 1,
    borderColor: colors.accent,
    backgroundColor: colors.accentLight,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  articleBadgeText: { fontSize: 12, fontWeight: "600", color: colors.accent },
  articleTextBlock: { flex: 1 },
  articleSection: { fontSize: 11, fontWeight: "500", color: colors.muted },
  articlePreview: { fontSize: 12, lineHeight: 17, color: colors.inkSoft, marginTop: 2 },
  readerList: { padding: 16, gap: 10, paddingBottom: 32 },
  readerHeader: { gap: 4, marginBottom: 4 },
  readerDoc: { fontSize: 15, fontWeight: "600", color: colors.ink },
  readerMeta: { fontSize: 11, color: colors.muted },
  chunkCard: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    backgroundColor: colors.surfaceElevated,
    padding: 14,
    gap: 6,
  },
  chunkSection: { fontSize: 12, fontWeight: "600", color: colors.accent },
  chunkContent: { fontSize: 14, lineHeight: 21, color: colors.ink },
  chunkPage: { fontSize: 11, color: colors.faint },
});
