import React, { useEffect, useState } from "react";
import {
  APP_VERSION,
  fetchLatestRelease,
  isUpdateAvailable,
  openStoreListing,
  type LatestRelease,
} from "../lib/release";
import { UpdateDialog } from "./UpdateDialog";

/**
 * Launch-time update check. Mounted once at the root: while the installed
 * build is behind the store release, the dialog shows on every cold start —
 * "Plus tard" only dismisses it until the next launch.
 */
export function UpdateChecker() {
  const [release, setRelease] = useState<LatestRelease | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetchLatestRelease().then((latest) => {
      if (!cancelled && latest && isUpdateAvailable(APP_VERSION, latest.latest_version)) {
        setRelease(latest);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!release || dismissed) return null;
  return (
    <UpdateDialog
      visible
      currentVersion={APP_VERSION}
      release={release}
      onLater={() => setDismissed(true)}
      onUpdate={() => openStoreListing(release)}
    />
  );
}
