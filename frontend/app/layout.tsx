import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Yawoto — Assistant Juridique, Afrique de l'Ouest",
  description:
    "Assistant juridique agentique pour l'Afrique de l'Ouest : recherche fondée sur des sources vérifiées (OHADA et droits nationaux), rédaction de documents et suivi d'usage.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0f172a",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body className="antialiased">{children}</body>
    </html>
  );
}
