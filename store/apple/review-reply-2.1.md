# Yawoto 1.0.3 — Reply to App Review (Guideline 2.1, Information Needed)

Use this text for the App Review reply AND paste it into the Notes field
(App Review Information) of version 1.0.3, as Apple requests.

---

Hello App Review team,

Thank you for reviewing Yawoto. Below is all the requested information.

## 1. Screen recording

A screen recording captured on a physical iPhone running the latest iOS is
attached to this message. It shows: app launch, account registration, login,
the main Q&A flow with source citations, voice dictation, the Explorer
(official texts), guided document drafting, history, and the account screen
including the account deletion flow (Compte → Supprimer le compte).

## 2. Purpose and target audience

Yawoto is a French-language legal research assistant covering the law of
Burkina Faso and OHADA (the harmonized business-law framework shared by 17
African countries). Target audience: citizens, employees, HR staff,
entrepreneurs, and legal professionals in francophone West Africa.

Problem solved: official legal texts are scattered across government
publications and hard to search. Yawoto lets users ask questions in plain
French — typed or dictated by voice — and receive answers that cite the exact
official articles, with excerpts, a confidence level, and contradiction
warnings. It also provides guided drafting of legal documents (employment
contracts, commercial leases, NDAs, service agreements) with PDF/Word export.

Value provided: traceable, source-linked legal information instead of
unsourced chatbot answers. The app is an information and research aid; it is
a private, non-governmental application and does not replace professional
legal counsel (stated prominently in the app and in the store description).

## 3. Setup and access instructions

Install the app and sign in on the "Se connecter" screen with the demo
account (also provided in the Sign-In Information section):
- User name (phone): +22670000000
- Password: Test1234

Main features after login:
- Chat tab: ask a legal question, e.g. "Quels sont les droits du salarié en
  cas de licenciement ?" — answers stream with cited sources.
- Explorer tab: browse and search the official legal corpus.
- Rédaction tab: pick a template and answer the guided questions to generate
  a legal document.
- Historique tab: past conversations and the official-news feed.
- Compte tab: profile, daily quota, theme, and account deletion.

Voice dictation (microphone button) is optional and requires microphone
permission. Account deletion is self-service at Compte → Supprimer le compte
and requires no support contact. The demo account is on the free tier with a
daily quota; if the quota is exhausted during review, registering a new
account (email or phone + password, no verification step) also works.

## 4. External services

- Yawoto backend API (api-yawoto.neobytech.net), operated by NEOBYTE
  TECHNOLOGIE: authentication, legal-corpus search, answer generation,
  quotas, history.
- Afrikia LLM gateway (afrikia.neobytech.net), operated by NEOBYTE
  TECHNOLOGIE: open-weight Qwen-family language models for answer generation
  and text embeddings.
- Speech-to-text: faster-whisper, self-hosted on NEOBYTE TECHNOLOGIE
  infrastructure, used only to transcribe dictated questions; recordings are
  not retained.
- Push notifications: Apple Push Notification service (delivered via the
  Expo push service).
- Legal content sources (official public documents): OHADA (ohada.org),
  Assemblée nationale du Burkina Faso (assembleenationale.bf), Ministère de
  la Justice du Burkina Faso (justice.gov.bf), Ministère de la Sécurité du
  Burkina Faso (securite.gov.bf).

The app contains no advertising, no third-party analytics, no third-party
login, and no in-app payment processing.

## 5. Regional differences

None. The app functions identically in all regions; its corpus is the law of
Burkina Faso and OHADA regardless of where the app is used.

## 6. Regulated industry / protected material

Yawoto reproduces official legal texts (laws, decrees, uniform acts)
published by public institutions. These are official public documents, not
subject to third-party copyright restrictions; no protected third-party
material is used. The app clearly states it is private and independent, not
affiliated with or endorsed by any government, and that it provides legal
information rather than legal advice.
