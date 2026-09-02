import React, { useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { Link, useRouter } from "expo-router";
import { confirmPasswordReset, requestPasswordReset } from "../../src/lib/api";
import AuthShell from "../../src/components/AuthShell";
import { ErrorText, PasswordField, PrimaryButton, TextField } from "../../src/components/ui";
import { colors } from "../../src/theme";

type Step = "request" | "confirm";

export default function ForgotPasswordScreen() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("request");
  const [identifier, setIdentifier] = useState("");
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRequest() {
    if (busy) return;
    const id = identifier.trim();
    if (!id) {
      setError("Saisissez votre e-mail ou votre numéro de téléphone.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // Always 202: the backend never reveals whether the identifier exists.
      await requestPasswordReset(id);
      setStep("confirm");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec de la demande");
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm() {
    if (busy) return;
    setError(null);
    if (!token.trim()) {
      setError("Saisissez le code reçu.");
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
      await confirmPasswordReset(token.trim(), password);
      router.replace("/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Lien de réinitialisation invalide ou expiré.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Mot de passe oublié">
      {step === "request" ? (
        <>
          <Text style={styles.help}>
            Saisissez l'identifiant de votre compte. Si ce compte existe, un code de
            réinitialisation vous sera envoyé.
          </Text>
          <TextField
            label="E-mail ou numéro de téléphone"
            value={identifier}
            onChangeText={setIdentifier}
            placeholder="awa@example.com ou +226 70 00 00 00"
            autoCapitalize="none"
            autoCorrect={false}
          />
          <ErrorText message={error} />
          <PrimaryButton title="Envoyer le code" onPress={() => void handleRequest()} busy={busy} />
        </>
      ) : (
        <>
          <Text style={styles.help}>
            Si le compte « {identifier.trim()} » existe, un code a été envoyé. Saisissez-le avec
            votre nouveau mot de passe.
          </Text>
          <TextField
            label="Code de réinitialisation"
            value={token}
            onChangeText={setToken}
            autoCapitalize="none"
            autoCorrect={false}
          />
          <PasswordField label="Nouveau mot de passe (8 caractères minimum)" value={password} onChangeText={setPassword} />
          <PasswordField label="Confirmer le mot de passe" value={confirmPassword} onChangeText={setConfirmPassword} />
          <ErrorText message={error} />
          <PrimaryButton title="Réinitialiser le mot de passe" onPress={() => void handleConfirm()} busy={busy} />
          <PressableLink label="Renvoyer un code" onPress={() => setStep("request")} />
        </>
      )}
      <View style={styles.footer}>
        <Link href="/login" style={styles.link}>
          Retour à la connexion
        </Link>
      </View>
    </AuthShell>
  );
}

function PressableLink({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Text style={styles.linkCenter} onPress={onPress}>
      {label}
    </Text>
  );
}

const styles = StyleSheet.create({
  help: { fontSize: 13, lineHeight: 19, color: colors.muted },
  footer: { alignItems: "center", marginTop: 4 },
  link: { color: colors.accent, fontSize: 13, fontWeight: "500" },
  linkCenter: { color: colors.accent, fontSize: 13, fontWeight: "500", textAlign: "center" },
});
