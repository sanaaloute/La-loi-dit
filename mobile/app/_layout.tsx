import React from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { Stack, useRouter, useSegments } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import * as Notifications from "expo-notifications";
import { useEffect, useMemo } from "react";
import { AuthProvider, useAuth } from "../src/lib/auth";
import { getPreferences } from "../src/lib/api";
import { hasOnboarded } from "../src/lib/persona";
import { syncPushToken } from "../src/lib/push";
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

  // Register the device for legal-news push alerts once signed in — covers
  // both app start with a stored session and a fresh login/register.
  // Fire-and-forget: push must never block or break the UI.
  useEffect(() => {
    if (status !== "signedIn") return;
    void syncPushToken().catch(() => {});
  }, [status]);

  // A tap on a notification (including the one that cold-started the app)
  // opens the History tab, where the "Nouveautés" card lives.
  useEffect(() => {
    const redirect = () => router.push("/history");
    if (Notifications.getLastNotificationResponse()) redirect();
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
