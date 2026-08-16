interface PageShellProps {
  /** Usually <AppHeader />. */
  header?: React.ReactNode;
  /** Small centered footer disclaimer bar; omitted when empty. */
  disclaimer?: string;
  children: React.ReactNode;
}

/**
 * Common page scaffold: full-height column, scrollable content area and the
 * glass footer disclaimer bar. The off-white page background comes from
 * globals.css (body). The chat page keeps its own layout.
 */
export default function PageShell({ header, disclaimer, children }: PageShellProps) {
  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      {header}
      <main className="flex-1 overflow-y-auto px-4 py-6 sm:px-6">{children}</main>
      {disclaimer && (
        <footer className="glass z-10 px-4 py-2 sm:px-6">
          <p className="mx-auto max-w-3xl text-center text-[10px] text-gray-500">{disclaimer}</p>
        </footer>
      )}
    </div>
  );
}
