import React from "react";
import { Linking, StyleSheet } from "react-native";
import MarkdownDisplay from "react-native-markdown-display";
import { colors } from "../theme";

interface MarkdownProps {
  children: string;
}

const styles = StyleSheet.create({
  body: {
    color: colors.ink,
    fontSize: 14,
    lineHeight: 21,
  },
  heading1: {
    fontSize: 20,
    fontWeight: "700",
    color: colors.ink,
    marginTop: 12,
    marginBottom: 6,
  },
  heading2: {
    fontSize: 17,
    fontWeight: "700",
    color: colors.ink,
    marginTop: 10,
    marginBottom: 5,
  },
  heading3: {
    fontSize: 15,
    fontWeight: "600",
    color: colors.ink,
    marginTop: 8,
    marginBottom: 4,
  },
  heading4: {
    fontSize: 14,
    fontWeight: "600",
    color: colors.ink,
    marginTop: 8,
    marginBottom: 4,
  },
  paragraph: {
    marginTop: 0,
    marginBottom: 8,
  },
  strong: {
    fontWeight: "700",
  },
  em: {
    fontStyle: "italic",
  },
  link: {
    color: colors.accent,
    textDecorationLine: "underline",
  },
  blockquote: {
    backgroundColor: colors.surface,
    borderLeftWidth: 4,
    borderLeftColor: colors.accent,
    paddingHorizontal: 12,
    paddingVertical: 6,
    marginBottom: 8,
  },
  code_inline: {
    backgroundColor: colors.surface,
    color: colors.accent,
    fontFamily: "Menlo",
    fontSize: 12.5,
    borderRadius: 4,
    paddingHorizontal: 4,
    paddingVertical: 1,
  },
  code_block: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    padding: 10,
    fontFamily: "Menlo",
    fontSize: 12.5,
    marginBottom: 8,
  },
  fence: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    padding: 10,
    fontFamily: "Menlo",
    fontSize: 12.5,
    marginBottom: 8,
  },
  bullet_list: {
    marginBottom: 8,
  },
  ordered_list: {
    marginBottom: 8,
  },
  list_item: {
    marginBottom: 2,
  },
  hr: {
    backgroundColor: colors.border,
    height: 1,
    marginVertical: 10,
  },
  table: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 6,
    marginBottom: 8,
  },
  thead: {
    backgroundColor: colors.surface,
  },
  th: {
    padding: 6,
    fontWeight: "600",
  },
  td: {
    padding: 6,
  },
});

/** Markdown renderer with the app's sober styling; links open externally. */
export default function Markdown({ children }: MarkdownProps) {
  return (
    <MarkdownDisplay
      style={styles}
      onLinkPress={(url) => {
        void Linking.openURL(url).catch(() => {});
        return false;
      }}
    >
      {children}
    </MarkdownDisplay>
  );
}
