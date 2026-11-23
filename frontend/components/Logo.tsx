/**
 * Yawoto brand mark — the speech bubble of the mobile app icon, redrawn as a
 * single-color SVG so it inherits `currentColor` (accent on light surfaces,
 * white on accent blocks, etc.). Use it wherever the product identity is
 * shown instead of a generic icon.
 */
export default function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 100 100" fill="currentColor" className={className} aria-hidden="true">
      <path d="M50 16C30.7 16 15 29.3 15 45.2c0 9.4 4.9 17.7 12.5 22.7-.6 3-1.9 6.8-3.8 9.9-.9 1.4.2 3.2 1.9 2.8 6-1.6 11.3-4 15-6.4 3 .5 6.1.8 9.4.8 19.3 0 35-13.3 35-29.2S69.3 16 50 16Z" />
    </svg>
  );
}
