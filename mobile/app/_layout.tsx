import React from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { Stack, useRouter, useSegments } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { useEffect } from "react";
import { AuthProvider, useAuth } from "../src/lib/auth";
import { colors } from "../src/theme";

/** Auth gate: token in the secure store → tabs, otherwise the auth stack. */
function AuthGate() {
  const { status } = useAuth();
  const segments = useSegments();
  const router = useRouter();

  useEffect(() => {
    if (status === "loading") return;
    const inAuthGroup = segments[0] === "(auth)";
    if (status === "signedOut" && !inAuthGroup) {
      router.replace("/login");
    } else if (status === "signedIn" && inAuthGroup) {
      router.replace("/");
    }
  }, [status, segments, router]);

  if (status === "loading") {
    return (
      <View style={styles.splash}>
        <ActivityIndicator size="large" color={colors.accent} />
        <Text style={styles.splashText}>Yawoto</Text>
      </View>
    );
  }

  return <Stack screenOptions={{ headerShown: false }} />;
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <AuthProvider>
        <StatusBar style="dark" />
        <AuthGate />
      </AuthProvider>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  splash: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surface,
    gap: 12,
  },
  splashText: { fontSize: 20, fontWeight: "700", color: colors.ink },
});
