"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { FilePenLine, Gauge, Menu, MessageSquare, Scale, Settings, Tag } from "lucide-react";
import SettingsPopover from "@/components/SettingsPopover";
import { getToken } from "@/lib/api";

interface AppHeaderProps {
  /** Controlled token state; when omitted the header manages it internally. */
  token?: string | null;
  onTokenChange?: (token: string | null) => void;
  /** Extra content at the far left (e.g. the chat history drawer button). */
  leftSlot?: React.ReactNode;
  /** Right-side slot, rendered before the settings button. */
  children?: React.ReactNode;
}

const NAV_LINKS = [
  { href: "/", label: "Assistant", icon: MessageSquare },
  { href: "/redaction", label: "Rédaction", icon: FilePenLine },
  { href: "/tarifs", label: "Tarifs", icon: Tag },
  { href: "/compte", label: "Compte", icon: Gauge },
];

export default function AppHeader({ token: tokenProp, onTokenChange, leftSlot, children }: AppHeaderProps) {
  const [internalToken, setInternalToken] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const pathname = usePathname();
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setInternalToken(getToken());
  }, []);

  // Close the mobile nav when the route changes.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const token = tokenProp !== undefined ? tokenProp : internalToken;
  const handleTokenChange = onTokenChange ?? setInternalToken;

  return (
    <header className="glass z-20 flex items-center justify-between gap-2 px-4 py-3 sm:px-6">
      <div className="flex min-w-0 items-center gap-3">
        {leftSlot}
        <Link href="/" className="flex shrink-0 items-center gap-3" title="Yawoto — accueil">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-law-cyan to-law-blue shadow-glow-sm">
            <Scale className="h-5 w-5 text-white" />
          </div>
          <div className="hidden sm:block">
            <span className="block text-base font-semibold text-white sm:text-lg">
              Yawoto<span className="gradient-text">.</span>
            </span>
            <span className="block text-xs text-slate-400">
              Assistant juridique — Afrique de l&apos;Ouest
            </span>
          </div>
        </Link>
        <nav className="ml-2 hidden items-center gap-1 md:flex">
          {NAV_LINKS.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
                  active
                    ? "border-law-cyan/40 bg-law-cyan/10 text-law-cyan"
                    : "border-transparent text-slate-300 hover:bg-white/5 hover:text-white"
                }`}
              >
                <link.icon className="h-4 w-4" />
                {link.label}
              </Link>
            );
          })}
        </nav>
      </div>
      <div className="flex items-center gap-2 sm:gap-3">
        {children}
        {/* Mobile nav */}
        <div ref={menuRef} className="relative md:hidden">
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-300 transition-colors hover:bg-white/5 hover:text-white"
            title="Menu"
            aria-label="Menu"
          >
            <Menu className="h-5 w-5" />
          </button>
          {menuOpen && (
            <div className="absolute right-0 top-full z-50 mt-2 w-48 rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-1.5 shadow-2xl backdrop-blur-xl">
              {NAV_LINKS.map((link) => {
                const active = pathname === link.href;
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    className={`flex items-center gap-2 rounded-lg px-3 py-2.5 text-sm transition-colors ${
                      active
                        ? "bg-law-cyan/10 text-law-cyan"
                        : "text-slate-200 hover:bg-white/5"
                    }`}
                  >
                    <link.icon className="h-4 w-4" />
                    {link.label}
                  </Link>
                );
              })}
            </div>
          )}
        </div>
        {/* Account */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setSettingsOpen((v) => !v)}
            className={`flex h-10 w-10 items-center justify-center rounded-lg border text-xs font-medium transition-colors ${
              token
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20"
                : "border-slate-600/60 bg-slate-800/60 text-slate-300 hover:bg-slate-700/60"
            }`}
            title="Compte et connexion"
            aria-label="Compte et connexion"
          >
            <Settings className="h-4 w-4" />
          </button>
          {settingsOpen && (
            <SettingsPopover
              token={token}
              onTokenChange={handleTokenChange}
              onClose={() => setSettingsOpen(false)}
            />
          )}
        </div>
      </div>
    </header>
  );
}
