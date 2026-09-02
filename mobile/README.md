# Yawoto — application mobile

Application mobile (Expo / React Native, TypeScript) de l'assistant juridique
Yawoto pour le Burkina Faso. Elle parle au backend FastAPI existant — le même
que l'application web — sous `{apiUrl}/api/v1`.

## Lancer en développement

```bash
cd mobile
npx expo start
```

puis scanner le QR code avec Expo Go, ou lancer sur un simulateur (`i` / `a`).

### URL de l'API

- Par défaut : `extra.apiUrl` dans `app.json`
  (`https://api-yawoto.neobytech.net`).
- Surcharge de dev (backend local) :

  ```bash
  EXPO_PUBLIC_API_URL=http://localhost:8000 npx expo start
  ```

  Sur un appareil physique, utilisez l'IP locale de la machine
  (`http://192.168.x.x:8000`) — `localhost` désigne le téléphone lui-même.

## Fonctionnement

### Authentification et liaison d'appareil

- JWT en `Authorization: Bearer <token>`, stocké dans `expo-secure-store`
  (`src/lib/storage.ts`). Renouvellement glissant via `POST /auth/refresh`
  quand il reste moins de 5 min sur le `exp` du jeton (une seule requête de
  rafraîchissement en vol, comme sur le web).
- **Chaque requête** (login, register, refresh compris) porte l'en-tête
  `X-Device-Id: <uuid>` (`src/lib/device.ts`) : l'UUID est généré une fois au
  premier appel et persisté dans le secure store. Le backend lie les sessions
  mobiles à cet identifiant plutôt qu'à l'adresse IP (les téléphones changent
  de réseau en permanence).
- Un 401 sur n'importe quel appel efface le jeton et renvoie à l'écran de
  connexion (`app/_layout.tsx` joue le rôle de portail d'authentification).

### Chat (onglet principal)

Portage fidèle de la machine à états de résilience du web
(`frontend/components/ChatWindow.tsx`), dans `src/lib/chat.ts` :

- `session_id` généré côté client (UUID) **avant** l'envoi — permet
  l'annulation serveur et la récupération.
- Flux SSE `GET /chat/stream` via `expo/fetch` (ReadableStream incrémentale) ;
  watchdog de silence de 15 s (heartbeats `: hb` ignorés, frames
  `data: {json}` parsées).
- Événements : `node_start` / `update` animent la frise des 18 agents
  (libellés français de `PIPELINE_NODES`) ; `delta` remplit une bulle
  provisoire façon machine à écrire ; `final` la remplace par la réponse
  structurée ; `cancelled` / `error` gérés.
- Si le flux échoue : consultation de `GET /chat/sessions/{id}/run` ; si le
  run tourne encore, sondage de `GET /chat/sessions/{id}` toutes les 5 s
  (~10 min max), en vérifiant que la réponse correspond exactement à la
  question envoyée ; 3 relevés consécutifs « not running » → échec franc ;
  sinon repli sur `POST /chat` non-streamé ; 409 → « interrompu »,
  429 → bulle de quota.
- Retour au premier plan (`AppState`) pendant un run : abandon immédiat de la
  socket tuée par l'OS et récupération silencieuse depuis l'historique.
- Entrée vocale : micro `expo-audio` (30 s max) → `POST /chat/transcribe` →
  texte inséré dans le champ de saisie pour relecture — jamais envoyé
  automatiquement.
- Compteur de 200 mots, bouton stop (AbortController + `POST /chat/cancel`),
  feedback 👍/👎 (`POST /chat/feedback`), export PDF/Word/CSV/Markdown vers la
  feuille de partage (`expo-file-system` + `expo-sharing`).

### Autres onglets

- **Historique** : conversations, dates relatives en français, suppression
  avec confirmation, ouverture dans le chat.
- **Rédaction** : modèles → formulaire dynamique → document Markdown +
  citations ; 403 → panneau « offre supérieure » (pas d'achat intégré) ;
  export PDF/Word/Markdown.
- **Compte** : profil et espace de travail (`/auth/me`), jauge d'usage
  quotidien + mini-graphique 30 jours (`/usage/me`), abonnement en lecture
  seule (`/billing/subscription`), choix du modèle (`/models`, badges
  Pro/Cabinet, choix persisté dans le secure store), déconnexion
  (`POST /auth/logout`), suppression du compte avec double confirmation
  (`DELETE /auth/me`).

## Structure

```
app/            routes expo-router (_layout, (auth)/*, (tabs)/*)
src/lib/        client API, SSE, moteur de chat, auth, stockage, exports
src/components/ Markdown, AnswerView, timeline, citations, preuves, exports
```

Toutes les chaînes d'interface sont en français, comme l'application web.

## Builds store (EAS)

Nécessite les comptes Expo / Apple Developer / Google Play de l'utilisateur :

```bash
npm install -g eas-cli   # si besoin
eas login
cd mobile
eas build:configure      # génère eas.json (une fois)
npx eas build --platform all
```

Les identifiants (`net.neobytech.yawoto`) sont déjà définis dans `app.json`.
La description micro iOS (`NSMicrophoneUsageDescription`) est configurée pour
la fonction dictée vocale.
