// Veille juridique: newest-first feed of publications detected on the
// official sources (JOBF, LégiBurkina, Assemblée…), grouped by day.
// Port of the web /veille page; only actual legal texts are shown (press
// feeds and news RSS are excluded, web parity).
import React, { useCallback, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Linking,
  Pressable,
  SectionList,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { listFreshnessEvents, type FreshnessEvent } from "../../src/lib/api";
import { useAuth } from "../../src/lib/auth";
import { localLongDate, relativeDate } from "../../src/lib/format";
import { serifFont, type ThemeColors } from "../../src/theme";
import { useTheme } from "../../src/theme-context";

const EVENTS_LIMIT = 100;

/** Authorities whose publications are actual legal texts (laws, decrees,
 * gazettes, constitutions, case law) — mirrors frontend/lib/veille.ts. */
const LEGAL_AUTHORITIES = new Set([
  "law",
  "official_gazette",
  "constitution",
  "case_law",
  "regulation",
]);

function isLegalTextEvent(ev: FreshnessEvent): boolean {
  const authority = ev.metadata?.authority;
  return typeof authority === "string" && LEGAL_AUTHORITIES.has(authority);
}

interface DayGroup {
  title: string;
  data: FreshnessEvent[];
}

/** Groups events by calendar day, newest first; day keys are French labels. */
function groupByDay(events: FreshnessEvent[]): DayGroup[] {
  const groups = new Map<string, FreshnessEvent[]>();
  for (const ev of events) {
    const d = new Date(ev.detected_at);
    const dayKey = Number.isNaN(d.getTime()) ? "Date inconnue" : d.toISOString().slice(0, 10);
    const list = groups.get(dayKey) ?? [];
    list.push(ev);
    groups.set(dayKey, list);
  }
  const today = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 24 * 3600 * 1000).toISOString().slice(0, 10);
  return [...groups.entries()].map(([day, items]) => ({
    title:
      day === today
        ? "Aujourd'hui"
        : day === yesterday
          ? "Hier"
          : day === "Date inconnue"
            ? day
            : localLongDate(day),
    data: items,
  }));
}

export default function VeilleScreen() {
  const { token } = useAuth();
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);

  const [events, setEvents] = useState<FreshnessEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!token) return;
    setLoading(true);
    setError(null);
    listFreshnessEvents(EVENTS_LIMIT)
      .then((rows) => setEvents(rows.filter(isLegalTextEvent)))
      .catch((err) => setError(err instanceof Error ? err.message : "Une erreur est survenue."))
      .finally(() => setLoading(false));
  }, [token]);

  useFocusEffect(
    useCallback(() => {
      reload();
    }, [reload]),
  );

  const stats = useMemo(() => {
    const now = Date.now();
    const all = events ?? [];
    return {
      week: all.filter((e) => now - Date.parse(e.detected_at) < 7 * 24 * 3600 * 1000).length,
      month: all.filter((e) => now - Date.parse(e.detected_at) < 30 * 24 * 3600 * 1000).length,
      sources: new Set(all.map((e) => e.source_name)).size,
    };
  }, [events]);

  const groups = useMemo(() => groupByDay(events ?? []), [events]);

  function openEvent(ev: FreshnessEvent) {
    if (!ev.url) return;
    void Linking.openURL(ev.url).catch(() => {});
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Ionicons name="notifications" size={17} color={colors.gold} />
        <Text style={styles.headerBrandTitle}>Veille juridique</Text>
      </View>

      {error ? (
        <View style={styles.centerFill}>
          <Ionicons name="alert-circle-outline" size={32} color={colors.faint} />
          <Text style={styles.mutedText}>{error}</Text>
          <Pressable onPress={reload} style={styles.retryButton}>
            <Text style={styles.retryText}>Réessayer</Text>
          </Pressable>
        </View>
      ) : events === null ? (
        <View style={styles.centerFill}>
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.mutedText}>Chargement de la veille…</Text>
        </View>
      ) : (
        <SectionList
          sections={groups}
          keyExtractor={(ev, i) => `${ev.source_name}-${ev.detected_at}-${i}`}
          contentContainerStyle={styles.list}
          stickySectionHeadersEnabled={false}
          refreshing={loading}
          onRefresh={reload}
          ListHeaderComponent={
            <View>
              <Text style={styles.subtitle}>
                Les dernières publications détectées sur les sources officielles du Burkina Faso.
              </Text>
              <View style={styles.statsRow}>
                {[
                  { value: stats.week, label: "Nouveaux textes (7 j)" },
                  { value: stats.month, label: "Ce mois-ci" },
                  { value: stats.sources, label: "Sources détectées" },
                ].map((stat) => (
                  <View key={stat.label} style={styles.statCard}>
                    <Text style={styles.statValue}>{stat.value}</Text>
                    <Text style={styles.statLabel}>{stat.label}</Text>
                  </View>
                ))}
              </View>
            </View>
          }
          ListEmptyComponent={
            <View style={styles.emptyCard}>
              <Ionicons name="library-outline" size={32} color={colors.faint} />
              <Text style={styles.emptyTitle}>Aucune publication détectée</Text>
              <Text style={styles.mutedText}>
                Dès qu'une nouvelle publication est repérée sur le JOBF, LégiBurkina ou
                l'Assemblée, elle apparaît ici.
              </Text>
            </View>
          }
          renderSectionHeader={({ section }) => (
            <Text style={styles.sectionHeader}>{section.title}</Text>
          )}
          renderItem={({ item }) => (
            <Pressable onPress={() => openEvent(item)} style={styles.eventCard}>
              <View style={styles.eventIcon}>
                <Ionicons name="document-text" size={16} color={colors.gold} />
              </View>
              <View style={styles.eventTextBlock}>
                <Text style={styles.eventSource}>{item.source_name}</Text>
                {item.detail ? <Text style={styles.eventDetail}>{item.detail}</Text> : null}
                <Text style={styles.eventDate}>{relativeDate(item.detected_at)}</Text>
              </View>
              <Ionicons name="open-outline" size={14} color={colors.faint} />
            </Pressable>
          )}
          ListFooterComponent={
            <Text style={styles.disclaimer}>
              Veille basée sur les publications détectées sur les sources officielles — seule la
              version au Journal officiel fait foi.
            </Text>
          }
        />
      )}
    </SafeAreaView>
  );
}

const makeStyles = (colors: ThemeColors) =>
  StyleSheet.create({
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
    headerBrandTitle: { fontFamily: serifFont, fontSize: 20, fontWeight: "700", color: colors.ink },
    subtitle: {
      fontSize: 13,
      color: colors.muted,
      marginTop: 4,
      marginBottom: 12,
      paddingHorizontal: 4,
    },
    statsRow: { flexDirection: "row", gap: 8, marginBottom: 8 },
    statCard: {
      flex: 1,
      alignItems: "center",
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      backgroundColor: colors.surfaceElevated,
      paddingVertical: 12,
      paddingHorizontal: 4,
    },
    statValue: { fontSize: 20, fontWeight: "700", color: colors.accent },
    statLabel: { fontSize: 10, lineHeight: 14, color: colors.muted, marginTop: 2, textAlign: "center" },
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
    eventCard: {
      flexDirection: "row",
      alignItems: "flex-start",
      gap: 10,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      backgroundColor: colors.surfaceElevated,
      paddingHorizontal: 12,
      paddingVertical: 12,
    },
    eventIcon: {
      width: 34,
      height: 34,
      alignItems: "center",
      justifyContent: "center",
      borderRadius: 8,
      backgroundColor: colors.goldLight,
    },
    eventTextBlock: { flex: 1 },
    eventSource: { fontSize: 14, fontWeight: "500", color: colors.ink },
    eventDetail: { fontSize: 12, lineHeight: 17, color: colors.muted, marginTop: 2 },
    eventDate: { fontSize: 11, color: colors.faint, marginTop: 4 },
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
    disclaimer: {
      fontSize: 11,
      lineHeight: 16,
      color: colors.faint,
      textAlign: "center",
      marginTop: 16,
      paddingHorizontal: 12,
    },
  });
