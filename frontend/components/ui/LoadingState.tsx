import { Loader2 } from "lucide-react";

/** Centered spinner with an optional label. */
export default function LoadingState({ label }: { label?: string }) {
  return (
    <div className="flex h-full items-center justify-center gap-2 text-sm text-gray-500">
      <Loader2 className="h-4 w-4 animate-spin text-accent" />
      {label}
    </div>
  );
}
