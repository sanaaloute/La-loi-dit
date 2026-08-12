"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { FilePenLine, Gauge, Menu, MessageSquare, Scale, Settings, ShieldCheck, Tag } from "lucide-react";
import SettingsPopover from "@/components/SettingsPopover";
import ThemeToggle from "@/components/ThemeToggle";
import { useAuthToken } from "@/lib/useAuth";
import { me } from "@/lib/api";

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

const ADMIN_LINK = { href: "/admin", label: "Admin", icon: ShieldCheck };

export default function AppHeader({ token: tokenProp, onTokenChange, leftSlot, children }: AppHeaderProps) {
  const [internalToken, setInternalToken] = useAuthToken();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const pathname = usePathname();
  const menuRef = useRef<HTMLDivElement>(null);

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

  // The "Admin" entry is only shown to logged-in administrators.
  useEffect(() => {
    if (!token) {
      setIsAdmin(false);
      return;
    }
    let cancelled = false;
    me(token)
      .then((p) => {
        if (!cancelled) setIsAdmin(p.role === "admin");
      })
      .catch(() => {
        if (!cancelled) setIsAdmin(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const navLinks = isAdmin ? [...NAV_LINKS, ADMIN_LINK] : NAV_LINKS;

  return (
    <header className="glass z-40 flex items-center justify-between gap-2 px-4 py-3 sm:px-6">
      <div className="flex min-w-0 items-center gap-3">
        {leftSlot}
        <Link href="/" className="flex shrink-0 items-center gap-3" title="Yawoto — accueil">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent">
            <Scale className="h-5 w-5 text-white" />
          </div>
          <div className="hidden sm:block">
            <span className="block text-base font-semibold text-gray-900 sm:text-lg">
              Yawoto
            </span>
            <span className="block text-xs text-gray-500">
              Assistant juridique
            </span>
          </div>
        </Link>
        <nav className="ml-2 hidden items-center gap-1 md:flex">
          {navLinks.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
                  active
                    ? "border-accent/40 bg-accent/10 text-accent"
                    : "border-transparent text-gray-600 hover:bg-gray-100 hover:text-gray-900"
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
            className="flex h-10 w-10 items-center justify-center rounded-lg text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-900"
            title="Menu"
            aria-label="Menu"
          >
            <Menu className="h-5 w-5" />
          </button>
          {menuOpen && (
            <div className="absolute right-0 top-full z-50 mt-2 w-48 rounded-xl border border-gray-200 bg-white p-1.5 shadow-2xl backdrop-blur-xl">
              {navLinks.map((link) => {
                const active = pathname === link.href;
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    className={`flex items-center gap-2 rounded-lg px-3 py-2.5 text-sm transition-colors ${
                      active
                        ? "bg-accent/10 text-accent"
                        : "text-gray-700 hover:bg-gray-100"
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
        {/* Theme */}
        <ThemeToggle />
        {/* Account */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setSettingsOpen((v) => !v)}
            className={`flex h-10 w-10 items-center justify-center rounded-lg border text-xs font-medium transition-colors ${
              token
                ? "border-accent/40 bg-accent/10 text-accent hover:bg-accent/20"
                : "border-gray-300 bg-gray-50 text-gray-600 hover:bg-gray-100"
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
