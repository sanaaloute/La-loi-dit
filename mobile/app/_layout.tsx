import React from "react";
import { ActivityIndicator, Linking, StyleSheet, Text, View } from "react-native";
import { Stack, useRouter, useSegments } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import * as Notifications from "expo-notifications";
import { useEffect, useMemo } from "react";
import { AuthProvider, useAuth } from "../src/lib/auth";
import { getPreferences } from "../src/lib/api";
import { chatEngine } from "../src/lib/chat";
import { hasOnboarded } from "../src/lib/persona";
import { syncPushToken } from "../src/lib/push";
import { setRefreshToken, setToken } from "../src/lib/storage";
import * as Device from "expo-device";
import { File, Paths } from "expo-file-system";
import { UpdateChecker } from "../src/components/UpdateChecker";
import type { ThemeColors } from "../src/theme";
import { ThemeProvider, useTheme } from "../src/theme-context";

// Checked once per login (not per navigation): the persona modal opens a
// single time after sign-in. Reset when the session ends.
let onboardingChecked = false;

/** Auth gate: token in the secure store → tabs, otherwise the auth stack. */
function AuthGate() {
  const { status } = useAuth();
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const segments = useSegments();
  const router = useRouter();

  useEffect(() => {
    if (status === "loading") return;
    const inAuthGroup = segments[0] === "(auth)";
    if (status === "signedOut") {
      onboardingChecked = false;
      if (!inAuthGroup) router.replace("/login");
    } else if (status === "signedIn" && inAuthGroup) {
      router.replace("/");
    }
  }, [status, segments, router]);

  // TEMPORARY screenshot automation (simulator only — never active on devices):
  // reads Documents/screenshot_state.json ({token, refresh, route, sessionId}).
  // TODO: revert before the next production build.
  useEffect(() => {
    if (Device.isDevice || status === "loading") return;
    void (async () => {
      try {
        const file = new File(Paths.document, "screenshot_state.json");
        if (!file.exists) return;
        const state = (await file.json()) as { token?: string; refresh?: string; route?: string; sessionId?: string };
        if (state.token) {
          setToken(state.token);
          if (state.refresh) setRefreshToken(state.refresh);
        }
        if (state.sessionId) {
          setTimeout(() => void chatEngine.loadSession(state.sessionId as string), 4000);
        }
        if (state.route) setTimeout(() => router.replace(state.route as never), 3000);
      } catch {
        // No state file — normal launch.
      }
    })();
  }, [status, router]);

  // Register the device for legal-news push alerts once signed in — covers
  // both app start with a stored session and a fresh login/register.
  // Fire-and-forget: push must never block or break the UI.
  useEffect(() => {
    if (status !== "signedIn") return;
    void syncPushToken().catch(() => {});
  }, [status]);

  // A tap on a notification (including the one that cold-started the app):
  // an answer-ready push opens its conversation on the chat tab; a legal-news
  // alert opens the source article.
  useEffect(() => {
    const redirect = (response: Notifications.NotificationResponse | null) => {
      if (!response) return;
      const data = response.notification.request.content.data as Record<string, unknown>;
      if (data?.kind === "chat_answer" && typeof data.session_id === "string") {
        // loadSession is a no-op while a run is busy (the resume recovery is
        // already polling this very session); the navigation alone lands the
        // user on the chat tab either way.
        void chatEngine.loadSession(data.session_id).then(() => router.navigate("/"));
        return;
      }
      if (typeof data?.url === "string" && data.url) {
        void Linking.openURL(data.url).catch(() => {});
        return;
      }
      router.push("/history");
    };
    redirect(Notifications.getLastNotificationResponse());
    const subscription = Notifications.addNotificationResponseReceivedListener(redirect);
    return () => subscription.remove();
  }, [router]);

  // First-launch persona onboarding: the SecureStore flag is absent AND the
  // server preferences carry no `persona` yet → open the modal once.
  useEffect(() => {
    if (status !== "signedIn" || onboardingChecked) return;
    if (segments[0] !== "(tabs)" || segments.includes("onboarding")) return;
    let cancelled = false;
    void (async () => {
      try {
        if (await hasOnboarded()) return;
        const prefs = await getPreferences();
        if (cancelled || prefs.persona) return;
        router.push("/onboarding");
      } catch {
        // Preferences unreadable (offline…): retry on the next app run.
      }
    })();
    onboardingChecked = true;
    return () => {
      cancelled = true;
    };
  }, [status, segments, router]);

  if (status === "loading") {
    return (
      <View style={styles.splash}>
        <ActivityIndicator size="large" color={colors.accent} />
        <Text style={styles.splashText}>Yawoto</Text>
      </View>
    );
  }

  return (
    <Stack screenOptions={{ headerShown: false }}>
      <Stack.Screen name="onboarding" options={{ presentation: "modal" }} />
    </Stack>
  );
}

/** Status bar follows the active theme. */
function ThemedStatusBar() {
  const { isDark } = useTheme();
  return <StatusBar style={isDark ? "light" : "dark"} />;
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <AuthProvider>
          <ThemedStatusBar />
          <AuthGate />
          <UpdateChecker />
        </AuthProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}

const makeStyles = (colors: ThemeColors) => StyleSheet.create({
  splash: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surface,
    gap: 12,
  },
  splashText: { fontSize: 20, fontWeight: "700", color: colors.ink },
});
