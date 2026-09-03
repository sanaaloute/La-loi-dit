import React, { useMemo, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { Link } from "expo-router";
import { login } from "../../src/lib/api";
import { useAuth } from "../../src/lib/auth";
import AuthShell from "../../src/components/AuthShell";
import { ErrorText, PasswordField, PrimaryButton, TextField } from "../../src/components/ui";
import type { ThemeColors } from "../../src/theme";
import { useTheme } from "../../src/theme-context";

export default function LoginScreen() {
  const { signIn } = useAuth();
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleLogin() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await login(username.trim(), password);
      setPassword("");
      await signIn(res);
      // The auth gate in app/_layout.tsx redirects to the tabs.
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec de connexion");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Se connecter" subtitle="Assistant juridique du Burkina Faso">
      <TextField
        label="E-mail, téléphone ou nom d'utilisateur"
        value={username}
        onChangeText={setUsername}
        placeholder="awa@example.com ou +226 70 00 00 00"
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="email-address"
      />
      <PasswordField label="Mot de passe" value={password} onChangeText={setPassword} />
      <ErrorText message={error} />
      <PrimaryButton title="Se connecter" onPress={() => void handleLogin()} busy={busy} />
      <View style={styles.links}>
        <Link href="/forgot-password" style={styles.link}>
          Mot de passe oublié ?
        </Link>
        <Text style={styles.footerText}>
          Pas de compte ?{" "}
          <Link href="/register" style={styles.link}>
            Créer un compte
          </Link>
        </Text>
      </View>
    </AuthShell>
  );
}

const makeStyles = (colors: ThemeColors) => StyleSheet.create({
  links: { alignItems: "center", gap: 10, marginTop: 4 },
  link: { color: colors.accent, fontSize: 13, fontWeight: "500" },
  footerText: { color: colors.muted, fontSize: 13 },
});
