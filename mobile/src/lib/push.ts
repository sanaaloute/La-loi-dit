// Push notifications for legal-news alerts ("Nouveautés juridiques").
// The Expo push token is registered with the backend (POST /push/token) once
// signed in and unregistered (DELETE /push/token) on logout. Everything is
// best-effort: missing permissions, a simulator without push support, or an
// unreachable backend must never crash or block the app.
import Constants from "expo-constants";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";
import { apiFetch } from "./api";
import { getDeviceId } from "./device";

// Foreground behavior: show the alert (banner + notification list) and play
// the sound instead of swallowing the notification.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

// Android channel carrying the legal-news alerts.
const ANDROID_CHANNEL_ID = "nouveautes";

// Last token sent to the backend, kept so logout can delete it without
// re-fetching (permissions may have been revoked in the meantime).
let currentToken: string | null = null;

/**
 * Ask for notification permissions and fetch the Expo push token.
 * Returns null when the user refuses or the device can't receive pushes —
 * callers treat null as "push unavailable".
 */
export async function registerForPushNotifications(): Promise<string | null> {
  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync(ANDROID_CHANNEL_ID, {
      name: "Nouveautés juridiques",
      importance: Notifications.AndroidImportance.HIGH,
    });
  }

  const { status: existingStatus } = await Notifications.getPermissionsAsync();
  let finalStatus = existingStatus;
  if (existingStatus !== "granted") {
    const { status } = await Notifications.requestPermissionsAsync();
    finalStatus = status;
  }
  if (finalStatus !== "granted") {
    return null;
  }

  try {
    const projectId =
      Constants?.expoConfig?.extra?.eas?.projectId ?? Constants?.easConfig?.projectId;
    if (!projectId) throw new Error("Project ID not found");
    return (await Notifications.getExpoPushTokenAsync({ projectId })).data;
  } catch {
    // Push unavailable here (simulator, missing FCM credentials…).
    return null;
  }
}

/**
 * Register the current push token with the backend (idempotent upsert).
 * Silent no-op when push is unavailable, offline, or the request fails —
 * notifications must never break the sign-in flow.
 */
export async function syncPushToken(): Promise<void> {
  const token = await registerForPushNotifications();
  if (!token) return;
  try {
    const res = await apiFetch("/push/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, device_id: await getDeviceId() }),
    });
    if (res.ok) currentToken = token;
  } catch {
    // Offline or backend down: retried on the next app start / sign-in.
  }
}

/**
 * Unregister the push token from the backend. Called on logout, before the
 * local session is wiped (the DELETE needs the Bearer token). Best effort: a
 * failure only means the backend keeps a stale token until the next sync.
 */
export async function unregisterPushToken(): Promise<void> {
  const token = currentToken;
  currentToken = null;
  if (!token) return;
  try {
    await apiFetch("/push/token", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
  } catch {
    // Best effort.
  }
}
