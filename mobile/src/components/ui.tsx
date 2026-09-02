// Small shared form controls (auth + drafting screens).
import React, { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  type KeyboardTypeOptions,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors } from "../theme";

interface TextFieldProps {
  label: string;
  value: string;
  onChangeText: (text: string) => void;
  placeholder?: string;
  keyboardType?: KeyboardTypeOptions;
  autoCapitalize?: "none" | "sentences" | "words" | "characters";
  autoCorrect?: boolean;
  multiline?: boolean;
  optional?: boolean;
}

export function TextField({
  label,
  value,
  onChangeText,
  placeholder,
  keyboardType,
  autoCapitalize = "sentences",
  autoCorrect = true,
  multiline = false,
  optional = false,
}: TextFieldProps) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>
        {label}
        {optional && <Text style={styles.optional}> (facultatif)</Text>}
      </Text>
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={colors.faint}
        keyboardType={keyboardType}
        autoCapitalize={autoCapitalize}
        autoCorrect={autoCorrect}
        multiline={multiline}
        style={[styles.input, multiline && styles.inputMultiline]}
      />
    </View>
  );
}

interface PasswordFieldProps {
  label: string;
  value: string;
  onChangeText: (text: string) => void;
}

export function PasswordField({ label, value, onChangeText }: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <View style={styles.passwordWrap}>
        <TextInput
          value={value}
          onChangeText={onChangeText}
          placeholder="••••••••"
          placeholderTextColor={colors.faint}
          secureTextEntry={!visible}
          autoCapitalize="none"
          autoCorrect={false}
          style={[styles.input, styles.passwordInput]}
        />
        <Pressable
          onPress={() => setVisible((v) => !v)}
          style={styles.eyeButton}
          accessibilityLabel={visible ? "Masquer le mot de passe" : "Afficher le mot de passe"}
        >
          <Ionicons name={visible ? "eye-off-outline" : "eye-outline"} size={18} color={colors.faint} />
        </Pressable>
      </View>
    </View>
  );
}

interface PrimaryButtonProps {
  title: string;
  onPress: () => void;
  busy?: boolean;
  disabled?: boolean;
}

export function PrimaryButton({ title, onPress, busy = false, disabled = false }: PrimaryButtonProps) {
  return (
    <Pressable
      onPress={onPress}
      disabled={busy || disabled}
      style={({ pressed }) => [
        styles.primaryButton,
        (busy || disabled) && styles.primaryButtonDisabled,
        pressed && !busy && !disabled && styles.primaryButtonPressed,
      ]}
    >
      {busy ? (
        <ActivityIndicator size="small" color="#fff" />
      ) : (
        <Text style={styles.primaryButtonText}>{title}</Text>
      )}
    </Pressable>
  );
}

export function ErrorText({ message }: { message: string | null }) {
  if (!message) return null;
  return <Text style={styles.error}>{message}</Text>;
}

const styles = StyleSheet.create({
  field: { gap: 4 },
  label: { fontSize: 12, fontWeight: "500", color: colors.inkSoft },
  optional: { color: colors.faint, fontWeight: "400" },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    backgroundColor: colors.surfaceElevated,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    color: colors.ink,
  },
  inputMultiline: { minHeight: 90, textAlignVertical: "top" },
  passwordWrap: { position: "relative", justifyContent: "center" },
  passwordInput: { paddingRight: 44 },
  eyeButton: { position: "absolute", right: 0, paddingHorizontal: 12, paddingVertical: 10 },
  primaryButton: {
    backgroundColor: colors.accent,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  primaryButtonPressed: { backgroundColor: colors.accentHover },
  primaryButtonDisabled: { opacity: 0.5 },
  primaryButtonText: { color: "#fff", fontSize: 14, fontWeight: "600" },
  error: { fontSize: 12, color: colors.danger },
});
