import React from "react";
import { Tabs } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useTheme } from "../../src/theme-context";

// Tab bar mirrors the web sidebar: Accueil, Veille, Sources, Dossiers, Compte.
// History lives in the chat header (drawer pattern, like the web) and the
// drafting tool is reached from the chat empty state.
export default function TabsLayout() {
  const { colors } = useTheme();
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        // Keyboard open => hide the tab bar (keeps the chat composer and form
        // inputs fully visible; pairs with android softwareKeyboardLayoutMode "pan").
        tabBarHideOnKeyboard: true,
        // Mirrors the web sidebar: fixed deep navy in both themes, gold active.
        tabBarActiveTintColor: colors.gold,
        tabBarInactiveTintColor: colors.sidebarText,
        tabBarStyle: {
          backgroundColor: colors.sidebarBg,
          borderTopColor: "rgba(255, 255, 255, 0.08)",
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Accueil",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="home-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="veille"
        options={{
          title: "Veille",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="notifications-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="explorer"
        options={{
          title: "Sources",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="library-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="dossiers"
        options={{
          title: "Dossiers",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="folder-open-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="account"
        options={{
          title: "Compte",
          tabBarIcon: ({ color, size }) => <Ionicons name="person-circle-outline" size={size} color={color} />,
        }}
      />
    </Tabs>
  );
}
