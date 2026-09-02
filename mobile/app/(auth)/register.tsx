import React, { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Link } from "expo-router";
import { register } from "../../src/lib/api";
import { useAuth } from "../../src/lib/auth";
import AuthShell from "../../src/components/AuthShell";
import { ErrorText, PasswordField, PrimaryButton, TextField } from "../../src/components/ui";
import { colors } from "../../src/theme";

type IdentifierKind = "email" | "phone";

export default function RegisterScreen() {
  const { signIn } = useAuth();
  const [kind, setKind] = useState<IdentifierKind>("email");
  const [identifier, setIdentifier] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRegister() {
    if (busy) return;
    setError(null);
    const id = identifier.trim();
    if (!id) {
      setError(kind === "email" ? "Saisissez votre adresse e-mail." : "Saisissez votre numéro de téléphone.");
      return;
    }
    if (password.length < 8) {
      setError("Le mot de passe doit contenir au moins 8 caractères.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Les mots de passe ne correspondent pas.");
      return;
    }
    setBusy(true);
    try {
      const res = await register(
        kind === "email" ? { email: id } : { phone: id },
        password,
        name.trim() || undefined,
      );
      setPassword("");
      setConfirmPassword("");
      await signIn(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec de l'inscription");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Créer un compte" subtitle="Un identifiant suffit : e-mail ou numéro de téléphone.">
      {/* E-mail XOR téléphone (même comportement que l'application web). */}
      <View style={styles.toggle}>
        {(
          [
            { id: "email", label: "E-mail" },
            { id: "phone", label: "Téléphone" },
          ] as { id: IdentifierKind; label: string }[]
        ).map((option) => (
          <Pressable
            key={option.id}
            onPress={() => {
              setKind(option.id);
              setIdentifier("");
              setError(null);
            }}
            style={[styles.toggleOption, kind === option.id && styles.toggleOptionActive]}
          >
            <Text style={[styles.toggleText, kind === option.id && styles.toggleTextActive]}>
              {option.label}
            </Text>
          </Pressable>
        ))}
      </View>
      <TextField
        label={kind === "email" ? "Adresse e-mail" : "Numéro de téléphone"}
        value={identifier}
        onChangeText={setIdentifier}
        placeholder={kind === "email" ? "awa@example.com" : "+226 70 00 00 00"}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType={kind === "email" ? "email-address" : "phone-pad"}
      />
      <TextField label="Nom" value={name} onChangeText={setName} placeholder="Awa Sawadogo" optional />
      <PasswordField label="Mot de passe (8 caractères minimum)" value={password} onChangeText={setPassword} />
      <PasswordField label="Confirmer le mot de passe" value={confirmPassword} onChangeText={setConfirmPassword} />
      <ErrorText message={error} />
      <PrimaryButton title="Créer mon compte" onPress={() => void handleRegister()} busy={busy} />
      <Text style={styles.footerText}>
        Déjà un compte ?{" "}
        <Link href="/login" style={styles.link}>
          Se connecter
        </Link>
      </Text>
    </AuthShell>
  );
}

const styles = StyleSheet.create({
  toggle: {
    flexDirection: "row",
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    backgroundColor: colors.surface,
    padding: 4,
    gap: 4,
  },
  toggleOption: {
    flex: 1,
    borderRadius: 8,
    paddingVertical: 8,
    alignItems: "center",
  },
  toggleOptionActive: { backgroundColor: colors.surfaceElevated, borderWidth: 1, borderColor: colors.border },
  toggleText: { fontSize: 13, color: colors.muted, fontWeight: "500" },
  toggleTextActive: { color: colors.ink },
  footerText: { color: colors.muted, fontSize: 13, textAlign: "center", marginTop: 4 },
  link: { color: colors.accent, fontWeight: "500" },
});
