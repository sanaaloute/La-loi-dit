"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Bell,
  Briefcase,
  Check,
  ChevronDown,
  FolderOpen,
  Home,
  Landmark,
  Menu,
  PanelLeft,
  PanelLeftClose,
  Search,
  Settings,
  ShieldCheck,
  X,
} from "lucide-react";
import FlagBf from "@/components/FlagBf";
import Logo from "@/components/Logo";
import ThemeToggle from "@/components/ThemeToggle";
import { apiBase, listFreshnessEvents, me, type UserProfile } from "@/lib/api";
import { useAuthToken } from "@/lib/useAuth";
import { isLegalTextEvent } from "@/lib/veille";

const APP_VERSION = "1.2.0";

const NAV_ITEMS = [
  { href: "/", label: "Accueil", icon: Home },
  { href: "/recherche", label: "Recherche juridique", icon: Search },
  { href: "/dossiers", label: "Mes dossiers", icon: FolderOpen },
  { href: "/veille", label: "Veille juridique", icon: Bell },
  { href: "/explorer", label: "Sources officielles", icon: Landmark },
  { href: "/compte", label: "Paramètres", icon: Settings },
];

const ADMIN_ITEM = { href: "/admin", label: "Admin", icon: ShieldCheck };

/** Jurisdictions shown as upcoming in the top-bar selector. */
const UPCOMING_COUNTRIES = ["Côte d'Ivoire", "Mali", "Niger", "Bénin", "Togo", "Sénégal"];

const SIDEBAR_COLLAPSED_KEY = "yawoto.sidebar.collapsed";

function initialsOf(profile: UserProfile): string {
  const base = (profile.name ?? profile.email).trim();
  const parts = base.includes("@") ? base.split("@")[0].split(/[._-]/) : base.split(/\s+/);
  const letters = parts.filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase() ?? "");
  return letters.join("") || "Y";
}

interface AppShellProps {
  /** "fill": the content fills the remaining height without its own scroll
   * or padding (chat-style internal panes). Default: scrollable padded page. */
  fill?: boolean;
  /** Small centered footer disclaimer bar; omitted when empty. */
  disclaimer?: string;
  children: React.ReactNode;
}

/** App frame: deep-navy sidebar (logo, nav, workspace) and top bar
 * (jurisdiction, sources status, theme, account). */
export default function AppShell({ children, fill = false, disclaimer }: AppShellProps) {
  const [token] = useAuthToken();
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [wsOpen, setWsOpen] = useState(false);
  const [countryOpen, setCountryOpen] = useState(false);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [veilleCount, setVeilleCount] = useState(0);
  const [sourcesOk, setSourcesOk] = useState<boolean | null>(null);

  // Close the mobile nav on every route change.
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  // Restore the desktop sidebar collapse preference after mount (SSR renders
  // the expanded default; the stored value wins on first client effect).
  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1");
    } catch {}
  }, []);

  function toggleCollapsed() {
    setCollapsed((v) => {
      try {
        window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, v ? "0" : "1");
      } catch {}
      return !v;
    });
  }

  // Escape closes the jurisdiction dropdown.
  useEffect(() => {
    if (!countryOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setCountryOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [countryOpen]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    me(token)
      .then((p) => {
        if (!cancelled) setProfile(p);
      })
      .catch(() => {});
    listFreshnessEvents(50, token)
      .then((events) => {
        if (cancelled) return;
        const weekAgo = Date.now() - 7 * 24 * 3600 * 1000;
        setVeilleCount(
          events.filter((e) => isLegalTextEvent(e) && Date.parse(e.detected_at) >= weekAgo).length,
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Backend reachability drives the "Sources connectées" indicator.
  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase()}/ready`)
      .then((res) => {
        if (!cancelled) setSourcesOk(res.ok);
      })
      .catch(() => {
        if (!cancelled) setSourcesOk(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const navItems = profile?.role === "admin" ? [...NAV_ITEMS, ADMIN_ITEM] : NAV_ITEMS;

  const sidebarContent = (isCollapsed: boolean) => (
    <>
      {/* Logo */}
      <Link
        href="/"
        className={`flex items-center gap-3 pb-5 pt-5 ${isCollapsed ? "justify-center px-2" : "px-5"}`}
        title="Yawoto — accueil"
      >
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gold/15">
          <Logo className="h-6 w-6 text-gold" />
        </span>
        {!isCollapsed && (
          <span className="min-w-0">
            <span className="block font-serif text-xl font-bold leading-tight text-paper">
              Yawoto
            </span>
            <span className="block text-[11px] leading-tight text-sidebar-text">
              Votre assistant juridique au Burkina Faso
            </span>
          </span>
        )}
      </Link>

      {/* Main nav */}
      <nav className={`flex-1 space-y-1 overflow-y-auto ${isCollapsed ? "px-2" : "px-3"}`}>
        {navItems.map((item) => {
          const itemPath = item.href.split("?")[0];
          const active =
            pathname === item.href || (item.href === itemPath && pathname === itemPath);
          return (
            <Link
              key={item.label}
              href={item.href}
              title={item.label}
              className={`relative flex items-center gap-3 rounded-xl py-2.5 text-sm font-medium transition-colors ${
                isCollapsed ? "justify-center px-2" : "px-3.5"
              } ${
                active ? "bg-paper/10 text-paper" : "text-sidebar-text hover:bg-paper/5 hover:text-paper"
              }`}
            >
              <item.icon className="h-[18px] w-[18px] shrink-0" />
              {!isCollapsed && <span className="min-w-0 flex-1 truncate">{item.label}</span>}
              {item.href === "/veille" && veilleCount > 0 && (
                isCollapsed ? (
                  <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-red-600" />
                ) : (
                  <span className="rounded-full bg-red-600 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-paper">
                    {veilleCount}
                  </span>
                )
              )}
            </Link>
          );
        })}
      </nav>

      {/* Workspace (expanded only) */}
      {!isCollapsed && (
        <div className="px-3 pb-3">
          <p className="px-2 pb-1.5 text-[11px] font-medium text-sidebar-text">Espace de travail</p>
          <button
            type="button"
            onClick={() => setWsOpen((v) => !v)}
            aria-expanded={wsOpen}
            className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-paper transition-colors hover:bg-paper/5"
          >
            <Briefcase className="h-[18px] w-[18px] shrink-0 text-sidebar-text" />
            <span className="min-w-0 flex-1 truncate text-left">
              {profile?.workspace_name || "Espace personnel"}
            </span>
            <ChevronDown
              className={`h-4 w-4 shrink-0 text-sidebar-text transition-transform ${wsOpen ? "rotate-180" : ""}`}
            />
          </button>
          {wsOpen && (
            <div className="mt-1.5 rounded-xl border border-paper/10 bg-paper/5 p-3">
              <p className="truncate text-xs font-semibold text-paper">
                {profile?.name ?? profile?.email ?? "Compte"}
              </p>
              {profile?.name && (
                <p className="mt-0.5 truncate text-[11px] text-sidebar-text">{profile.email}</p>
              )}
              {profile?.role && (
                <span className="mt-1.5 inline-block rounded-full bg-gold/15 px-2 py-0.5 text-[10px] font-medium text-gold">
                  {profile.role}
                </span>
              )}
              <div className="mt-2 space-y-0.5 border-t border-paper/10 pt-2">
                <Link
                  href="/compte"
                  className="block rounded-lg px-2 py-1.5 text-xs text-sidebar-text transition-colors hover:bg-paper/5 hover:text-paper"
                >
                  Paramètres
                </Link>
                <Link
                  href="/dossiers"
                  className="block rounded-lg px-2 py-1.5 text-xs text-sidebar-text transition-colors hover:bg-paper/5 hover:text-paper"
                >
                  Mes dossiers
                </Link>
                <Link
                  href="/dossiers?tab=marques"
                  className="block rounded-lg px-2 py-1.5 text-xs text-sidebar-text transition-colors hover:bg-paper/5 hover:text-paper"
                >
                  Marque-pages
                </Link>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Collapse toggle (desktop) */}
      <div className={`hidden pb-2 lg:block ${isCollapsed ? "px-2" : "px-3"}`}>
        <button
          type="button"
          onClick={toggleCollapsed}
          title={isCollapsed ? "Agrandir le menu" : "Réduire le menu"}
          aria-label={isCollapsed ? "Agrandir le menu" : "Réduire le menu"}
          className={`flex w-full items-center gap-3 rounded-xl py-2 text-sidebar-text transition-colors hover:bg-paper/5 hover:text-paper ${
            isCollapsed ? "justify-center px-2" : "px-3.5"
          }`}
        >
          {isCollapsed ? (
            <PanelLeft className="h-[18px] w-[18px] shrink-0" />
          ) : (
            <>
              <PanelLeftClose className="h-[18px] w-[18px] shrink-0" />
              <span className="text-xs">Réduire</span>
            </>
          )}
        </button>
      </div>

      {!isCollapsed && (
        <p className="px-5 pb-4 text-[10px] text-sidebar-text">Yawoto v{APP_VERSION}</p>
      )}
    </>
  );

  return (
    <div className="flex h-dvh overflow-hidden">
      {/* Desktop sidebar */}
      <aside
        className={`hidden shrink-0 flex-col bg-sidebar transition-[width] duration-300 lg:flex ${
          collapsed ? "w-20" : "w-72"
        }`}
      >
        {sidebarContent(collapsed)}
      </aside>

      {/* Mobile sidebar drawer */}
      {navOpen && (
        <button
          type="button"
          onClick={() => setNavOpen(false)}
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
          aria-label="Fermer le menu"
        />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-[min(84vw,18rem)] flex-col bg-sidebar transition-transform duration-300 lg:hidden ${
          navOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <button
          type="button"
          onClick={() => setNavOpen(false)}
          className="absolute right-3 top-4 flex h-9 w-9 items-center justify-center rounded-lg text-sidebar-text hover:bg-paper/5 hover:text-paper"
          title="Fermer le menu"
          aria-label="Fermer le menu"
        >
          <X className="h-5 w-5" />
        </button>
        {sidebarContent(false)}
      </aside>

      {/* Right column: top bar + content */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="glass z-30 flex h-14 shrink-0 items-center gap-3 px-4 sm:px-6">
          <button
            type="button"
            onClick={() => setNavOpen(true)}
            className="flex h-10 w-10 items-center justify-center rounded-lg text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-900 lg:hidden"
            title="Menu"
            aria-label="Menu"
          >
            <Menu className="h-5 w-5" />
          </button>
          <div className="relative">
            <button
              type="button"
              onClick={() => setCountryOpen((v) => !v)}
              aria-expanded={countryOpen}
              aria-haspopup="listbox"
              className="flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-gray-100"
            >
              <FlagBf className="h-4 w-6 rounded-[2px] shadow-sm" />
              <span className="text-sm font-medium text-gray-900">Burkina Faso</span>
              <ChevronDown
                className={`h-3.5 w-3.5 text-gray-400 transition-transform ${countryOpen ? "rotate-180" : ""}`}
              />
            </button>
            {countryOpen && (
              <>
                <button
                  type="button"
                  onClick={() => setCountryOpen(false)}
                  className="fixed inset-0 z-40 cursor-default"
                  aria-label="Fermer le sélecteur de pays"
                />
                <div
                  role="listbox"
                  aria-label="Juridiction"
                  className="absolute left-0 top-full z-50 mt-1 w-60 rounded-xl border border-gray-200 bg-surface-elevated p-1.5 shadow-panel"
                >
                  <div className="flex items-center gap-2.5 rounded-lg bg-gray-100 px-2.5 py-2">
                    <FlagBf className="h-3.5 w-5 rounded-[2px] shadow-sm" />
                    <span className="min-w-0 flex-1 text-sm font-medium text-gray-900">
                      Burkina Faso
                    </span>
                    <Check className="h-4 w-4 shrink-0 text-gold" />
                  </div>
                  <div className="mx-2 my-1.5 border-t border-gray-200" />
                  {UPCOMING_COUNTRIES.map((country) => (
                    <div
                      key={country}
                      role="option"
                      aria-disabled="true"
                      aria-selected={false}
                      className="flex cursor-not-allowed items-center gap-2.5 rounded-lg px-2.5 py-2 opacity-60"
                    >
                      <span className="h-3.5 w-5 shrink-0 rounded-[2px] bg-gray-200" />
                      <span className="min-w-0 flex-1 text-sm text-gray-500">{country}</span>
                      <span className="shrink-0 rounded-full bg-gold-light px-1.5 py-0.5 text-[10px] font-semibold text-gold">
                        Bientôt
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
          <span className="ml-1 hidden items-center gap-1.5 text-xs text-gray-500 sm:flex">
            <span
              className={`h-2 w-2 rounded-full ${
                sourcesOk === false ? "bg-red-600" : "bg-emerald-500"
              }`}
            />
            {sourcesOk === false ? "Sources hors ligne" : "Sources connectées"}
          </span>
          <span className="flex-1" />
          <ThemeToggle />
          <Link
            href="/compte"
            className="flex items-center gap-2 rounded-lg px-1.5 py-1 transition-colors hover:bg-gray-100"
            title="Mon compte"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-xs font-semibold text-white">
              {profile ? initialsOf(profile) : "…"}
            </span>
            <span className="hidden max-w-32 truncate text-sm font-medium text-gray-900 sm:block">
              {profile?.name ?? profile?.email ?? "Compte"}
            </span>
            <ChevronDown className="hidden h-3.5 w-3.5 text-gray-400 sm:block" />
          </Link>
        </header>

        <main
          className={
            fill ? "flex min-h-0 flex-1 flex-col" : "min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6"
          }
        >
          {children}
        </main>

        {disclaimer && (
          <footer className="glass shrink-0 px-4 py-2 sm:px-6">
            <p className="mx-auto max-w-3xl text-center text-[10px] text-gray-500">{disclaimer}</p>
          </footer>
        )}
      </div>
    </div>
  );
}
