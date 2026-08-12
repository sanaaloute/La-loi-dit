import { Lock } from "lucide-react";

interface GatePanelProps {
  title?: string;
  body: string;
  /** Optional actions rendered below the body (e.g. a back link). */
  children?: React.ReactNode;
}

/** "Connexion requise" panel shown to anonymous visitors. */
export default function GatePanel({ title = "Connexion requise", body, children }: GatePanelProps) {
  return (
    <div className="mx-auto mt-16 max-w-md rounded-xl border border-gray-200 bg-white p-6 text-center shadow-2xl backdrop-blur-xl">
      <Lock className="mx-auto mb-3 h-8 w-8 text-accent" />
      <h2 className="mb-2 text-lg font-semibold text-gray-900">{title}</h2>
      <p className={`text-sm text-gray-500 ${children ? "mb-5" : ""}`}>{body}</p>
      {children}
    </div>
  );
}
