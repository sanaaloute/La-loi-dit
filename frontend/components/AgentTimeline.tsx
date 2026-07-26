"use client";

import { Check, Loader2, Circle } from "lucide-react";
import { PIPELINE_NODES } from "@/lib/api";

export type NodeStatus = "pending" | "running" | "done";

interface AgentTimelineProps {
  statuses: Record<string, NodeStatus>;
  active: boolean;
}

function StatusIcon({ status }: { status: NodeStatus }) {
  if (status === "done") {
    return (
      <span className="flex h-6 w-6 items-center justify-center rounded-full border border-emerald-400/30 bg-emerald-400/10 text-emerald-300">
        <Check className="h-3.5 w-3.5" />
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className="flex h-6 w-6 items-center justify-center rounded-full border border-law-cyan/30 bg-law-cyan/10">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-law-cyan" />
      </span>
    );
  }
  return (
    <span className="flex h-6 w-6 items-center justify-center rounded-full border border-slate-600/40 bg-slate-800/50">
      <Circle className="h-2 w-2 text-slate-500" />
    </span>
  );
}

export default function AgentTimeline({ statuses, active }: AgentTimelineProps) {
  const anyActivity = active || PIPELINE_NODES.some((n) => statuses[n.id] !== "pending");

  return (
    <div className="p-4">
      <h3 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
        <span className="h-1.5 w-1.5 rounded-full bg-law-emerald" />
        Exécution des agents
      </h3>
      {!anyActivity ? (
        <div className="rounded-xl border border-slate-700/40 bg-slate-800/30 p-4 text-center">
          <p className="text-xs text-slate-400">
            La chaîne d&apos;agents s&apos;affichera ici pendant le traitement d&apos;une question.
          </p>
        </div>
      ) : (
        <ol className="space-y-2">
          {PIPELINE_NODES.map((node) => {
            const status = statuses[node.id] ?? "pending";
            return (
              <li
                key={node.id}
                className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm transition-all ${
                  status === "running"
                    ? "border-law-cyan/20 bg-law-cyan/5 font-medium text-law-cyan shadow-glow-sm"
                    : status === "done"
                      ? "border-emerald-500/10 bg-emerald-500/5 text-slate-200"
                      : "border-slate-700/30 bg-slate-800/20 text-slate-500"
                }`}
              >
                <StatusIcon status={status} />
                <span>{node.label}</span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
