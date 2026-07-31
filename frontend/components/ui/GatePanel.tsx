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
    <div className="mx-auto mt-16 max-w-md rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-6 text-center shadow-2xl backdrop-blur-xl">
      <Lock className="mx-auto mb-3 h-8 w-8 text-law-cyan" />
      <h2 className="mb-2 text-lg font-semibold text-white">{title}</h2>
      <p className={`text-sm text-slate-400 ${children ? "mb-5" : ""}`}>{body}</p>
      {children}
    </div>
  );
}
