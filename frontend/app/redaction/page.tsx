"use client";

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Copy,
  Download,
  FileCode,
  FilePenLine,
  FileText,
  Loader2,
  Search,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import AppHeader from "@/components/AppHeader";
import CitationPanel from "@/components/CitationPanel";
import ErrorCard from "@/components/ui/ErrorCard";
import GatePanel from "@/components/ui/GatePanel";
import LoadingState from "@/components/ui/LoadingState";
import PageShell from "@/components/ui/PageShell";
import UpgradePanel from "@/components/ui/UpgradePanel";
import {
  ApiError,
  createDraft,
  downloadBlob,
  exportDraft,
  getModel,
  getToken,
  listDraftTemplates,
  me,
  type DraftField,
  type DraftResponse,
  type DraftTemplate,
  type ExportFormat,
  type UserProfile,
} from "@/lib/api";

type Step = "templates" | "form" | "result";

const CATEGORY_LABELS: Record<DraftTemplate["category"], string> = {
  contract: "Contrats",
  case: "Procédures",
};

const CATEGORY_ORDER: DraftTemplate["category"][] = ["contract", "case"];

const DRAFT_EXPORTS: { id: ExportFormat | "md"; label: string; icon: React.ElementType; ext: string }[] = [
  { id: "pdf", label: "PDF", icon: FileText, ext: "pdf" },
  { id: "word", label: "Word", icon: FileText, ext: "docx" },
  { id: "md", label: "Markdown", icon: FileCode, ext: "md" },
];

function isForbidden(err: unknown): boolean {
  if (err instanceof ApiError) return err.status === 403;
  return err instanceof Error && err.message.includes("403");
}

const INPUT_CLASS =
  "w-full rounded-lg border border-slate-600/60 bg-slate-900/60 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-law-cyan/60 focus:outline-none";

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: DraftField;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = `draft-field-${field.name}`;
  if (field.type === "textarea") {
    return (
      <textarea
        id={id}
        rows={4}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.placeholder}
        className={INPUT_CLASS}
        required={field.required}
      />
    );
  }
  if (field.type === "select") {
    return (
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={INPUT_CLASS} required={field.required}>
        <option value="">Sélectionner…</option>
        {field.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      id={id}
      type={field.type === "date" ? "date" : field.type === "number" ? "number" : "text"}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={field.placeholder}
      className={INPUT_CLASS}
      required={field.required}
    />
  );
}

export default function RedactionPage() {
  const [token, setToken] = useState<string | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [templates, setTemplates] = useState<DraftTemplate[] | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [step, setStep] = useState<Step>("templates");
  const [search, setSearch] = useState("");
  const [template, setTemplate] = useState<DraftTemplate | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [instructions, setInstructions] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [quotaError, setQuotaError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [exporting, setExporting] = useState<ExportFormat | "md" | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setToken(getToken());
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    listDraftTemplates(token)
      .then((res) => {
        if (!cancelled) setTemplates(res.templates);
      })
      .catch((err) => {
        if (cancelled) return;
        if (isForbidden(err)) setForbidden(true);
        else setLoadError(err instanceof Error ? err.message : "Une erreur est survenue.");
      });
    me(token)
      .then((p) => {
        if (!cancelled) setProfile(p);
      })
      .catch(() => {
        // Profil indisponible : les boutons d'export ne sont pas filtrés.
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const grouped = useMemo(() => {
    const query = search.trim().toLowerCase();
    const filtered = (templates ?? []).filter(
      (t) =>
        query.length === 0 ||
        t.label.toLowerCase().includes(query) ||
        t.description.toLowerCase().includes(query),
    );
    return CATEGORY_ORDER.map((category) => ({
      category,
      label: CATEGORY_LABELS[category],
      templates: filtered.filter((t) => t.category === category),
    })).filter((group) => group.templates.length > 0);
  }, [templates, search]);

  function pickTemplate(t: DraftTemplate) {
    setTemplate(t);
    setValues({});
    setInstructions("");
    setFormError(null);
    setQuotaError(null);
    setStep("form");
  }

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!template || busy) return;
    const missing = template.fields.filter((f) => f.required && !(values[f.name] ?? "").trim());
    if (missing.length > 0) {
      setFormError(`Champs obligatoires manquants : ${missing.map((f) => f.label).join(", ")}`);
      return;
    }
    setBusy(true);
    setFormError(null);
    setQuotaError(null);
    try {
      const result = await createDraft(
        {
          template_id: template.id,
          fields: values,
          instructions: instructions.trim() || undefined,
          model: getModel() ?? undefined,
        },
        token,
      );
      setDraft(result);
      setStep("result");
    } catch (err) {
      if (isForbidden(err)) setForbidden(true);
      else if (err instanceof ApiError && err.status === 429) setQuotaError(err.message);
      else setFormError(err instanceof Error ? err.message : "Échec de la génération du document.");
    } finally {
      setBusy(false);
    }
  }

  async function handleExport(format: ExportFormat | "md") {
    if (!draft || exporting) return;
    setExporting(format);
    try {
      const blob = await exportDraft(draft, format, token);
      const prefix = template?.category === "case" ? "procedure" : "contrat";
      const ext = DRAFT_EXPORTS.find((f) => f.id === format)?.ext ?? format;
      downloadBlob(blob, `${prefix}-${draft.template_id}.${ext}`);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Échec de l'export");
    } finally {
      setExporting(null);
    }
  }

  async function handleCopy() {
    if (!draft) return;
    try {
      await navigator.clipboard.writeText(draft.draft_markdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      alert("Échec de la copie dans le presse-papiers");
    }
  }

  const allowedExports = profile?.features?.export;

  return (
    <PageShell
      header={<AppHeader token={token} onTokenChange={setToken} />}
      disclaimer="Avertissement : les documents générés sont des aides à la rédaction. Ils ne constituent pas un conseil juridique."
    >
      {!token ? (
        <GatePanel body="La rédaction de documents nécessite un compte. Connectez-vous depuis l'icône de compte en haut à droite." />
      ) : forbidden ? (
        <UpgradePanel body="La génération de documents juridiques (contrats, actes de procédure) est réservée aux offres Pro et Cabinet. Contactez votre administrateur pour mettre à niveau votre compte." />
      ) : loadError ? (
        <ErrorCard message={loadError}>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-600/60 bg-slate-800/60 px-4 py-2 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-700/60"
          >
            Réessayer
          </button>
        </ErrorCard>
      ) : templates === null ? (
        <LoadingState label="Chargement des modèles…" />
      ) : step === "templates" ? (
          <div className="mx-auto max-w-4xl">
            <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-xl font-semibold text-white">Choisissez un modèle</h2>
                <p className="mt-1 text-sm text-slate-400">
                  Le document est généré à partir de vos réponses et de sources vérifiées.
                </p>
              </div>
              <div className="relative sm:w-64">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Rechercher un modèle…"
                  className={`${INPUT_CLASS} pl-9`}
                />
              </div>
            </div>
            {grouped.length === 0 ? (
              <p className="py-10 text-center text-sm text-slate-500">
                Aucun modèle ne correspond à votre recherche.
              </p>
            ) : (
              grouped.map((group) => (
                <section key={group.category} className="mb-8">
                  <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                    <span className="h-1.5 w-1.5 rounded-full bg-law-cyan" />
                    {group.label}
                  </h3>
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {group.templates.map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        onClick={() => pickTemplate(t)}
                        className="rounded-xl border border-slate-700/60 bg-slate-800/40 p-4 text-left backdrop-blur-sm transition-all hover:border-law-cyan/50 hover:bg-slate-700/50"
                      >
                        <span className="mb-2 flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-law-cyan/20 to-law-blue/20 text-law-cyan">
                          <FilePenLine className="h-4 w-4" />
                        </span>
                        <span className="block text-sm font-medium text-white">{t.label}</span>
                        <span className="mt-1 block text-xs leading-relaxed text-slate-400">
                          {t.description}
                        </span>
                      </button>
                    ))}
                  </div>
                </section>
              ))
            )}
          </div>
        ) : step === "form" && template ? (
          <div className="mx-auto max-w-2xl">
            <button
              type="button"
              onClick={() => setStep("templates")}
              className="mb-4 flex items-center gap-1.5 text-xs text-slate-400 transition-colors hover:text-white"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Tous les modèles
            </button>
            {quotaError && (
              <div className="mb-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3">
                <p className="mb-1 flex items-center gap-2 text-sm font-semibold text-amber-300">
                  <AlertTriangle className="h-4 w-4" />
                  Quota journalier atteint
                </p>
                <p className="text-xs text-amber-100">{quotaError}</p>
                <p className="mt-1 text-xs text-amber-300/80">
                  Passez à l&apos;offre supérieure pour continuer.
                </p>
              </div>
            )}
            <div className="rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-5 shadow-2xl backdrop-blur-xl sm:p-6">
              <h2 className="text-lg font-semibold text-white">{template.label}</h2>
              <p className="mb-5 mt-1 text-sm text-slate-400">{template.description}</p>
              <form onSubmit={handleGenerate} className="space-y-4">
                {template.fields.map((field) => (
                  <div key={field.name}>
                    <label
                      htmlFor={`draft-field-${field.name}`}
                      className="mb-1 block text-xs font-medium text-slate-300"
                    >
                      {field.label}
                      {field.required && <span className="text-rose-400"> *</span>}
                    </label>
                    <FieldInput
                      field={field}
                      value={values[field.name] ?? ""}
                      onChange={(v) => setValues((prev) => ({ ...prev, [field.name]: v }))}
                    />
                  </div>
                ))}
                <div>
                  <label htmlFor="draft-instructions" className="mb-1 block text-xs font-medium text-slate-300">
                    Instructions complémentaires <span className="text-slate-500">(facultatif)</span>
                  </label>
                  <textarea
                    id="draft-instructions"
                    rows={3}
                    value={instructions}
                    onChange={(e) => setInstructions(e.target.value)}
                    placeholder="Précisions, clauses particulières, contexte…"
                    className={INPUT_CLASS}
                  />
                </div>
                {formError && <p className="text-xs text-rose-300">{formError}</p>}
                <button
                  type="submit"
                  disabled={busy}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-law-cyan to-law-blue px-3 py-2.5 text-sm font-medium text-white shadow-glow-sm transition-all hover:shadow-glow disabled:opacity-50"
                >
                  {busy ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Génération en cours…
                    </>
                  ) : (
                    <>
                      <Sparkles className="h-4 w-4" />
                      Générer le document
                    </>
                  )}
                </button>
              </form>
            </div>
          </div>
        ) : draft ? (
          <div className="mx-auto max-w-3xl space-y-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <button
                type="button"
                onClick={() => setStep("form")}
                className="flex items-center gap-1.5 text-xs text-slate-400 transition-colors hover:text-white"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                Modifier les informations
              </button>
              <button
                type="button"
                onClick={() => {
                  setDraft(null);
                  setTemplate(null);
                  setStep("templates");
                }}
                className="flex items-center gap-1.5 rounded-lg border border-slate-600/60 bg-slate-800/60 px-3 py-2 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-700/60"
              >
                <FilePenLine className="h-4 w-4" />
                Nouveau document
              </button>
            </div>

            <div className="rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-5 shadow-2xl backdrop-blur-xl sm:p-6">
              <div className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-slate-700/40 pb-4">
                <div>
                  <h2 className="text-lg font-semibold text-white">{draft.title}</h2>
                  <p className="mt-1 text-[11px] text-slate-500">
                    Généré en {draft.latency_ms.toFixed(0)} ms
                  </p>
                </div>
                {draft.requires_human_review && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-rose-400/30 bg-rose-400/10 px-2.5 py-0.5 text-xs font-semibold text-rose-300">
                    <ShieldAlert className="h-3 w-3" />
                    Révision humaine recommandée
                  </span>
                )}
              </div>

              {draft.requires_human_review && (
                <div className="mb-4 rounded-xl border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
                  Ce document doit être relu et validé par un juriste avant toute utilisation.
                </div>
              )}

              {draft.warnings.length > 0 && (
                <div className="mb-4 rounded-xl border border-amber-500/20 bg-amber-500/10 p-3">
                  <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-300">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    Avertissements
                  </p>
                  <ul className="list-disc space-y-1 pl-4 text-xs text-amber-100">
                    {draft.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="markdown-body">
                <ReactMarkdown>{draft.draft_markdown}</ReactMarkdown>
              </div>

              <div className="mt-6 flex flex-wrap items-center gap-2 border-t border-slate-700/40 pt-4">
                {DRAFT_EXPORTS.filter((f) => !allowedExports || allowedExports.includes(f.id)).map(
                  (f) => (
                    <button
                      key={f.id}
                      type="button"
                      onClick={() => void handleExport(f.id)}
                      disabled={exporting !== null}
                      className="flex items-center gap-2 rounded-lg border border-slate-600/40 bg-slate-800/50 px-3 py-2 text-xs font-medium text-slate-200 transition-colors hover:border-law-cyan/40 hover:bg-slate-700/50 disabled:opacity-50"
                    >
                      {exporting === f.id ? (
                        <Loader2 className="h-4 w-4 animate-spin text-law-cyan" />
                      ) : (
                        <f.icon className="h-4 w-4 text-law-cyan" />
                      )}
                      {f.label}
                    </button>
                  ),
                )}
                <button
                  type="button"
                  onClick={() => void handleCopy()}
                  className="flex items-center gap-2 rounded-lg border border-slate-600/40 bg-slate-800/50 px-3 py-2 text-xs font-medium text-slate-200 transition-colors hover:border-law-cyan/40 hover:bg-slate-700/50"
                >
                  {copied ? (
                    <Check className="h-4 w-4 text-emerald-400" />
                  ) : (
                    <Copy className="h-4 w-4 text-law-cyan" />
                  )}
                  {copied ? "Copié !" : "Copier"}
                </button>
                <span className="ml-auto hidden items-center gap-1 text-[11px] text-slate-500 sm:flex">
                  <Download className="h-3 w-3" />
                  Export au format PDF, Word ou Markdown
                </span>
              </div>
            </div>

            {draft.citations.length > 0 && (
              <div className="rounded-xl border border-slate-600/40 bg-[#0f172a]/95 shadow-2xl backdrop-blur-xl">
                <CitationPanel citations={draft.citations} />
              </div>
            )}
          </div>
        ) : null}
    </PageShell>
  );
}
