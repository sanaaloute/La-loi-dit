interface ErrorCardProps {
  message: string;
  /** Optional actions rendered below the message (e.g. a retry button). */
  children?: React.ReactNode;
}

/** Rose error card for unrecoverable load failures. */
export default function ErrorCard({ message, children }: ErrorCardProps) {
  return (
    <div className="mx-auto mt-16 max-w-md rounded-xl border border-rose-500/30 bg-rose-500/10 p-6 text-center">
      <p className={`text-sm text-rose-300 ${children ? "mb-4" : ""}`}>{message}</p>
      {children}
    </div>
  );
}
