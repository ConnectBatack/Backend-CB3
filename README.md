# Batack — Backend API (FastAPI)

API REST complète de la plateforme de dons et de gestion pour le village de Batack (Cameroun — COVIDEBA, UMONS, ENSPY).

## Fonctionnalités principales

- **Authentification & Sécurité** :
  - Hachage des mots de passe avec **Argon2id** (paramètres OWASP `m=19 MiB, t=2, p=1`).
  - Sessions serveur stockées par empreinte SHA-256 (`user_sessions`) : les cookies `httpOnly` **et** les JWT sont immédiatement invalidés à la déconnexion ou au changement de mot de passe.
  - Changement de mot de passe obligatoire (`must_change_password`) appliqué sur toutes les routes tant qu'il n'est pas effectué.
  - Double authentification **TOTP (2FA)** protégée par mot de passe et code TOTP, protection anti brute-force par IP sans verrouillage de compte, limitation de requêtes (`rate_limit` avec `TRUST_PROXY_HEADERS=false` par défaut), et **journal d'audit** (`audit_logs`).
  - Documentation Swagger/ReDoc désactivée automatiquement lorsque `APP_ENV=production`.
- **Administration & Gestion des rôles (RBAC)** :
  - `admin`, `tresorier`, `membre`, `encadrant` (lecture seule, exports et vérification d'intégrité).
- **Gestion locale des médias (`storage/`) & CMS** :
  - Stockage local structuré (`storage/photos`, `storage/logos`, `storage/justificatifs`, `storage/documents`, `storage/autres`).
  - Justificatifs et documents **privés par défaut** (`is_public=false`).
  - Contenus CMS et projets validés en texte brut (sans balises HTML).
  - Montants collectés et nombre de contributeurs calculés dynamiquement à partir des seules écritures validées du registre.
- **Dons Stripe & Suivi des projets (RGPD)** :
  - Appels Stripe non-bloquants, pas de création prématurée de `Customer`, gestion des remboursements (`charge.refunded`), litiges (`charge.dispute.created`), renouvellements (`invoice.paid`) et frais Stripe inscrits au registre.
  - Dons manuels possibles sans adresse e-mail (téléphone facultatif), libellés publics anonymisés.
  - Suivi de projet par e-mail (SMTP) avec double opt-in et désinscription (aucun jeton exposé en HTTP).
- **Registre financier chaîné SHA-256 & Transferts** :
  - Append-only strict sous verrou transactionnel (`pg_advisory_xact_lock` + verrou asyncio) : toute validation ou rejet crée une nouvelle écriture chaînée, aucune ligne n'est modifiée.
  - Séparation des tâches stricte (y compris pour `admin`), transferts générant automatiquement une sortie + une entrée.
- **Aide à la décision (AHP & Solveur MILP v5_5)** :
  - Calcul AHP (Saaty) et solveur MILP **HiGHS** (`highspy`) avec revérification indépendante (`verifier_plan`) à l'enregistrement.

## Démarrage rapide

```bash
cd backend
cp .env.example .env
# Remplacer toutes les valeurs CHANGE_ME_* dans .env
docker compose up -d db redis --wait
source .venv/bin/activate
alembic upgrade head
uvicorn app.main:app --reload --port 8001
```

## Lancer les tests & le lint

```bash
make lint
make test
```
