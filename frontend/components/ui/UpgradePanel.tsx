import { Sparkles } from "lucide-react";

interface UpgradePanelProps {
  title?: string;
  body: string;
  /** Optional actions rendered below the body (e.g. a back link). */
  children?: React.ReactNode;
}

/** "Disponible dès l'offre Pro" panel shown when the tier lacks a feature. */
export default function UpgradePanel({
  title = "Disponible dès l'offre Pro",
  body,
  children,
}: UpgradePanelProps) {
  return (
    <div className="mx-auto mt-16 max-w-md rounded-xl border border-accent/20 bg-white p-6 text-center shadow-2xl backdrop-blur-xl">
      <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent">
        <Sparkles className="h-7 w-7 text-white" />
      </div>
      <h2 className="mb-2 text-lg font-semibold text-gray-900">{title}</h2>
      <p className={`text-sm text-gray-500 ${children ? "mb-5" : ""}`}>{body}</p>
      {children}
    </div>
  );
}
