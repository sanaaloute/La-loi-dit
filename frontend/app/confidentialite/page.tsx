import type { Metadata } from "next";
import Link from "next/link";

// Page publique requise par Google Play (l'application collecte des données
// de compte et utilise le micro). Pas de robots noindex : les réviseurs Play
// doivent pouvoir y accéder.
export const metadata: Metadata = {
  title: "Politique de confidentialité — Yawoto",
  description:
    "Politique de confidentialité de l'application Yawoto : données collectées, finalités, conservation, droits des utilisateurs.",
};

const SECTIONS: { title: string; body: string[] }[] = [
  {
    title: "1. Responsable du traitement",
    body: [
      "L'application Yawoto (mobile et web) est éditée par Neobytech. Pour toute question relative à la protection de vos données, contactez : elsanal1995@gmail.com.",
    ],
  },
  {
    title: "2. Données collectées",
    body: [
      "Données de compte : adresse e-mail ou numéro de téléphone, nom affiché, mot de passe (stocké uniquement sous forme hachée).",
      "Identifiant d'appareil : un identifiant technique (X-Device-Id) généré à la première utilisation, utilisé pour sécuriser les sessions mobiles.",
      "Contenus saisis : questions juridiques posées, conversations, documents générés, marque-pages et préférences (profil utilisateur, thème, modèle choisi).",
      "Enregistrements vocaux : lorsque vous utilisez la dictée vocale, l'extrait audio est transmis au serveur uniquement pour être transcrit en texte ; l'audio n'est ni conservé ni réutilisé après transcription.",
      "Données d'usage : volumes de requêtes et de tokens consommés (gestion des quotas journaliers), journaux techniques.",
    ],
  },
  {
    title: "3. Finalités",
    body: [
      "Fournir le service d'assistance juridique (réponses citées à partir de textes officiels du Burkina Faso et de l'OHADA), la transcription vocale, la rédaction de documents, l'historique et la synchronisation entre appareils.",
      "Assurer la sécurité des comptes et appliquer les limites d'utilisation liées à votre offre (gratuit, pro, cabinet).",
      "Aucune donnée personnelle n'est vendue ni partagée à des fins publicitaires. Aucune publicité n'est affichée dans l'application.",
    ],
  },
  {
    title: "4. Hébergement et sous-traitants",
    body: [
      "Les traitements sont réalisés sur les serveurs exploités par Neobytech. Lorsque des prestataires techniques sont sollicités (par exemple un fournisseur de modèle de langage ou de transcription), seules les données strictement nécessaires à la requête en cours leur sont transmises.",
      "Les notifications push sont acheminées par le service Expo Push (Expo / Expo.dev) via un jeton d'appareil, uniquement si vous l'avez autorisé.",
    ],
  },
  {
    title: "5. Conservation",
    body: [
      "Les données de compte et l'historique sont conservés tant que le compte est actif. Les extraits audio ne sont pas conservés au-delà de la transcription. Les journaux techniques sont conservés pour une durée limitée à des fins de diagnostic et de sécurité.",
    ],
  },
  {
    title: "6. Vos droits",
    body: [
      "Vous pouvez accéder à vos données, les rectifier et supprimer votre compte à tout moment depuis l'application (onglet Compte → Supprimer le compte). La suppression du compte entraîne celle de l'historique, des marque-pages et des préférences associés.",
      "Pour toute autre demande (export, rectification, réclamation), écrivez à elsanal1995@gmail.com.",
    ],
  },
  {
    title: "7. Sécurité",
    body: [
      "Les échanges entre l'application et les serveurs sont chiffrés (HTTPS/TLS). Les mots de passe sont hachés et les jetons de session sont stockés dans le stockage sécurisé de l'appareil.",
    ],
  },
  {
    title: "8. Mineurs",
    body: [
      "Le service n'est pas destiné aux enfants de moins de 13 ans et ne collecte pas sciemment leurs données.",
    ],
  },
  {
    title: "9. Modifications",
    body: [
      "La présente politique peut être mise à jour ; la version en vigueur est celle publiée sur cette page, avec sa date de révision.",
    ],
  },
];

export default function ConfidentialitePage() {
  return (
    <main className="min-h-screen bg-white text-gray-800">
      <div className="mx-auto max-w-3xl px-4 py-12">
        <Link href="/" className="text-sm font-medium text-accent hover:text-accent-hover">
          ← Retour à Yawoto
        </Link>
        <h1 className="mt-6 text-3xl font-bold text-gray-900">Politique de confidentialité</h1>
        <p className="mt-2 text-sm text-gray-500">Dernière révision : 3 septembre 2026</p>
        <p className="mt-6 leading-relaxed">
          Yawoto est un assistant juridique qui répond à vos questions à partir des textes officiels
          du Burkina Faso et de l&apos;OHADA. La présente politique décrit les données que nous
          collectons, pourquoi, et les droits dont vous disposez.
        </p>
        {SECTIONS.map((s) => (
          <section key={s.title} className="mt-10">
            <h2 className="text-lg font-semibold text-gray-900">{s.title}</h2>
            {s.body.map((p, i) => (
              <p key={i} className="mt-3 leading-relaxed text-gray-700">
                {p}
              </p>
            ))}
          </section>
        ))}
      </div>
    </main>
  );
}
