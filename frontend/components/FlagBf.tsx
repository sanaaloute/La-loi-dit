/** Flag of Burkina Faso (red over green, gold star) as a small inline SVG. */
export default function FlagBf({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 18" className={className} aria-hidden="true">
      <rect width="24" height="9" fill="#EF2B2D" />
      <rect y="9" width="24" height="9" fill="#009E49" />
      <path
        d="M0-3.4 1-1.38 3.24-1.05 1.62 0.53 2 2.75 0 1.71 -2 2.75 -1.62 0.53 -3.24-1.05 -1-1.38Z"
        transform="translate(12 9)"
        fill="#FCD116"
      />
    </svg>
  );
}
