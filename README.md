# ConnectBatack 3 — Backend API (FastAPI)

API REST complète pour la plateforme **ConnectBatack 3** (village de Batack, Cameroun — COVIDEBA, UMONS, ENSPY), alignée sur les besoins du frontend Next.js (`../frontend`).

## Fonctionnalités principales

- **Authentification & Sécurité** :
  - Hachage des mots de passe avec **Argon2id** (paramètres OWASP `m=19 MiB, t=2, p=1`).
  - Double support **JWT (Access + Refresh rotation)** et **cookie de session `httpOnly` / `SameSite=Lax`** dont seule l'empreinte SHA-256 est stockée en base (`user_sessions`).
  - Double authentification **TOTP (2FA)**, verrouillage anti brute-force, limitation de requêtes (`rate_limit`), et **journal d'audit** complet (`audit_logs`).
- **Administration & Gestion des rôles (RBAC)** :
  - `admin` (ENSPY / Administrateur technique) : création des comptes, attribution des rôles, réinitialisation des mots de passe, journal d'audit, accès complet.
  - `tresorier` : enregistrement des dons manuels et dépenses, contresignature, transferts entre rubriques, exports, planification.
  - `membre` (COVIDEBA) : gestion des projets, contenus, photos, tâches, analyse AHP, planification, soumission et contresignature Mobile Money.
  - `encadrant` : consultation en lecture seule, vérification d'intégrité cryptographique du registre, exports CSV/JSON et journal d'audit.
- **Gestion locale des médias & contenus (`storage/`)** :
  - Stockage local structuré (`storage/photos`, `storage/logos`, `storage/justificatifs`, `storage/documents`, `storage/autres`) persisté par volume Docker.
  - Validation MIME et octets magiques (Pillow pour les images, en-tête `%PDF-` pour les PDF), empreinte SHA-256, métadonnées et textes alternatifs multilingues (`fr`, `en`, `es`, `nl`).
  - CMS multilingue pour les projets vitrine (`projects`) et blocs éditoriaux (`site_contents`).
- **Dons Stripe & Suivi des projets** :
  - Création de sessions Stripe Checkout (`/api/v1/donations/checkout`) avec garde de sécurité refusant toute clé de production sans `PAIEMENT_REEL_AUTORISE=oui`.
  - Webhook Stripe signé (`/api/v1/webhooks/stripe`) avec idempotence (`stripe_events`) et écriture automatique dans le registre financier.
  - Abonnements de suivi de projet avec consentement RGPD, double opt-in et lien de désinscription (`/api/v1/subscriptions/*`).
- **Registre financier chaîné & Transferts** :
  - Montants exacts (`NUMERIC(15,2)` EUR et entier FCFA à parité fixe `1 € = 655,957 FCFA`).
  - Écritures immuables chaînées par empreinte **SHA-256** et workflow de contresignature (*à contresigner* -> *validée* / *rejetée*).
  - Vérification d'intégrité de la chaîne (`/api/v1/registry/verify-integrity`) et export CSV/JSON.
- **Aide à la décision (AHP & Solveur MILP v5_5)** :
  - Calcul AHP (vecteur propre principal de Perron-Frobenius et ratio de cohérence de Saaty `CR <= 0.1`).
  - Résolution du modèle d'investissement MILP v5_5 via **HiGHS** (`highspy`).

## Démarrage rapide avec Docker Compose

```bash
cd backend
cp .env.example .env
# Adapter SECRET_KEY et les mots de passe dans .env avant mise en production
docker compose up --build -d
```

L'API sera accessible sur :
- API : `http://localhost:8000`
- Documentation interactive (Swagger) : `http://localhost:8000/docs`
- Documentation ReDoc : `http://localhost:8000/redoc`
- Healthcheck : `http://localhost:8000/api/v1/health`

Au premier démarrage avec `APP_ENV=development` et `SEED_ON_STARTUP=true`, les migrations Alembic s'appliquent automatiquement et `scripts/seed_db.py` initialise :
- les comptes de démonstration (`admin@batack.org`, `tresorier@batack.org`, `membre@batack.org`, `encadrant@batack.org`),
- les photos du dossier `frontend/public/images` dans `backend/storage/photos/`,
- les 6 projets vitrine et des exemples de tâches / écritures.

## Démarrage local (sans Docker)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Copier .env.example vers .env et ajuster DATABASE_URL (ex: sqlite+aiosqlite:///./batack_dev.db)
alembic upgrade head
python -m scripts.seed_db
uvicorn app.main:app --reload --port 8000
```

## Lancer les tests

```bash
pytest -v
```

