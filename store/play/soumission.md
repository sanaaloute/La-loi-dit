# Soumission Play Store — guide pas à pas

Tout est prêt côté code et assets. Il reste des étapes que **vous seul pouvez faire** dans
Google Play Console (compte personnel) : créez-les, puis dites-moi — je lance la soumission.

## État actuel

- Build production (AAB) : ✅ généré par EAS Build
- Icône de marque, bannière 1024×500, icône 512×512, 4 captures d'écran : ✅ dans `store/play/`
- Politique de confidentialité : ✅ en ligne sur https://yawoto.neobytech.net/confidentialite
- Textes de la fiche : ✅ `store/play/listing.md`

## ℹ️ Compte professionnel (changement en cours)

Vous avez demandé le passage en compte **professionnel/organisation**. Une fois validé
par Google (documents de l'entreprise parfois demandés : enregistrement, site web),
l'obligation de test fermé (12 testeurs / 14 jours) **disparaît** — la release pourra
aller directement en production après l'examen Google. En attendant la validation,
vous pouvez déjà faire les étapes 1, 2 et 4 (fiche store). Note : les comptes
professionnels affichent publiquement l'adresse de l'entreprise sur la fiche Play.

## Étape 1 — Créer l'app dans Play Console (5 min)

1. https://play.google.com/console → **Créer une application**
2. Nom : `Yawoto — Assistant juridique`, langue par défaut : français, type : Application, gratuit
3. Acceptez les conditions ; l'app est créée en mode brouillon.

## Étape 2 — Créer la clé de compte de service (pour EAS Submit)

1. Play Console → **Configuration → Accès à l'API** → liez (ou créez) un projet Google Cloud.
2. Dans Google Cloud Console → **IAM → Comptes de service** → créez `eas-submit`,
   puis **Clés → Ajouter une clé → JSON** → téléchargez le fichier.
3. De retour dans Play Console → **Accès à l'API** → le compte de service apparaît :
   **Accorder l'accès** → permissions « Release » (Gérer les versions / releases) sur l'app Yawoto.
4. Uploadez la clé JSON sur EAS : https://expo.dev/accounts/yawotos-team/projects/yawoto
   → **Credentials → Android → net.neobytech.yawoto → Service Credentials → Add a Google Service Account Key**.

## Étape 3 — Je soumets (dites-moi quand l'étape 2 est faite)

Je lance :

```bash
cd mobile && npx eas-cli submit --platform android --profile production
```

EAS uploade l'AAB sur le **track de test interne** (création de la première release).
L'app reste en brouillon tant que la fiche store n'est pas complète.

## Étape 4 — Compléter la fiche (30 min, avec `store/play/listing.md`)

Dans Play Console :
- **Fiche du store** : copiez titre/descriptions de `listing.md`, uploadez
  `play-icon-512.png`, `feature-graphic.png` et les 4 captures de `screenshots/`
- **Classification du contenu**, **Sécurité des données**, **Déclarations** : suivez `listing.md`
- **Release** : avec le compte professionnel validé, créez directement une release
  **production** (ou test interne d'abord si vous préférez une validation douce)

## Étape 5 — Publication

L'examen Google de la première release prend généralement quelques jours.
Une fois approuvée, l'app est en ligne sur le Play Store.

## Références

- EAS Submit : https://docs.expo.dev/submit/android/
- Builds : https://expo.dev/accounts/yawotos-team/projects/yawoto/builds
