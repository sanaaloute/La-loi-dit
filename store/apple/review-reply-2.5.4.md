# Yawoto 1.0.3 — Reply to App Review (Guidelines 2.5.4 + 2.1, round 2)

SUBMITTED Nov 3, 2026 ~9:55 PM with build 1.0.3 (10); status: Waiting for Review.
The text below is what was posted in the App Review thread and pasted into
the version's Notes field (Notes uses an extended version adding Purpose,
Setup, External Services, Regional Differences, Protected Material sections).

---

Hello App Review team,

Thank you for the follow-up. We have addressed both issues.

## Guideline 2.5.4 — UIBackgroundModes audio

We removed the voice-dictation feature entirely: the microphone input, audio
recording, and speech transcription are gone from the app (build 1.0.3 (10)),
and the `audio` entry was removed from UIBackgroundModes (the regenerated
Info.plist contains no UIBackgroundModes key). The app has no feature
requiring persistent audio.

## Guideline 2.1 — Quota information

The app is completely free. Answers to your questions:

- What happens once the quota is reached? The daily quota applies only to the
  chat assistant. When it is reached, the chat answers no new questions until
  the next day, when a fresh quota is allocated. All other features (browsing
  the official legal corpus in Explorer, drafting documents in Rédaction,
  history, bookmarks, sharing, account settings) keep working without limits.
- Can users purchase more requests? No. There is no purchase anywhere in the
  app, and no paid tier exists.
- Where can users purchase a paid tier? Nowhere — no paid tier is offered.
- Where do they pay and what is the payment method? Not applicable — the app
  has no payments of any kind.
- An account that has reached the daily quota:
  - User name (email): quota-demo@neobytech.net
  - Password: QuotaDemo2026!
  Signing in with this account and asking any question in the Chat tab shows
  the quota-reached state ("Quota journalier de requêtes atteint. Réessayez
  demain.").

The previously provided demo account (phone +22670000000, password Test1234)
remains available for the full working experience.

The physical-device screen recording sent with our previous message
demonstrates the app running on an iPhone with the latest iOS.

Thank you for your review.
