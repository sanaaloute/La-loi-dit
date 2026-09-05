// Non-government disclaimer and official sources of the legal information
// shown in the app. Required by the Google Play Misleading Claims policy for
// apps providing government information; keep in sync with store/play/listing.md.
export const GOVERNMENT_DISCLAIMER =
  "Yawoto est une application privée et indépendante. Elle ne représente aucune entité " +
  "gouvernementale et n'est ni affiliée ni approuvée par le Gouvernement du Burkina Faso, " +
  "l'OHADA ou toute autre institution publique.";

export interface OfficialSource {
  label: string;
  url: string;
}

export const OFFICIAL_SOURCES: OfficialSource[] = [
  {
    label: "OHADA — Droit des affaires",
    url: "https://www.ohada.org",
  },
  {
    label: "Assemblée nationale du Burkina Faso",
    url: "https://www.assembleenationale.bf",
  },
  {
    label: "Ministère de la Justice du Burkina Faso",
    url: "https://www.justice.gov.bf",
  },
  {
    label: "Ministère de la Sécurité — Textes officiels",
    url: "https://www.securite.gov.bf/ressources/textes-officiels",
  },
];
