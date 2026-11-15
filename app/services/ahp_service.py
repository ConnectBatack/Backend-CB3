import math

# Indice aléatoire de Saaty (n = 1 à 10 critères) — aligné avec frontend/src/lib/ahp.js
RI = [0.0, 0.0, 0.58, 0.90, 1.12, 1.24, 1.32, 1.41, 1.45, 1.49]
SEUIL_COHERENCE = 0.1


def valeur_depuis_curseur(c: int) -> float:
    """Convertit un curseur de -8 à 8 vers l'échelle de Saaty (1/9 à 9)."""
    if c < -8 or c > 8:
        raise ValueError("Le curseur doit être compris entre -8 et 8.")
    return float(c + 1) if c >= 0 else 1.0 / float(1 - c)


def matrice_depuis_comparaisons(
    criteres: list[dict], curseurs: dict[str, int] | None
) -> list[list[float]]:
    """Construit la matrice réciproque n x n à partir des curseurs deux à deux."""
    n = len(criteres)
    A = [[1.0 for _ in range(n)] for _ in range(n)]
    curseurs = curseurs or {}
    for i in range(n):
        for j in range(i + 1, n):
            key_ab = f"{criteres[i]['id']}-{criteres[j]['id']}"
            key_ba = f"{criteres[j]['id']}-{criteres[i]['id']}"
            if key_ab in curseurs:
                v = valeur_depuis_curseur(curseurs[key_ab])
            elif key_ba in curseurs:
                v = 1.0 / valeur_depuis_curseur(curseurs[key_ba])
            else:
                v = 1.0
            A[i][j] = v
            A[j][i] = 1.0 / v
    return A


def verifier_matrice(A: list[list[float]], tolerance: float = 1e-6) -> list[str]:
    """Vérifie qu'une matrice est carrée, positive, de diagonale 1 et réciproque."""
    n = len(A)
    erreurs: list[str] = []
    if n == 0 or any(len(ligne) != n for ligne in A):
        return ["La matrice n'est pas carrée."]
    if n > len(RI):
        return [f"Au plus {len(RI)} critères sont supportés."]
    for i in range(n):
        for j in range(n):
            v = A[i][j]
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
                erreurs.append(f"Valeur invalide en ({i + 1}, {j + 1}).")
            elif i == j and abs(v - 1.0) > tolerance:
                erreurs.append(f"La diagonale doit valoir 1 en ({i + 1}, {i + 1}).")
            elif abs(v * A[j][i] - 1.0) > tolerance:
                erreurs.append(f"La matrice n'est pas réciproque en ({i + 1}, {j + 1}).")
    return erreurs


def _normaliser(v: list[float]) -> list[float]:
    s = sum(v)
    return [x / s for x in v]


def poids_vecteur_propre(
    A: list[list[float]], iterations: int = 1000, tolerance: float = 1e-12
) -> tuple[list[float], float]:
    """Vecteur propre principal (méthode de la puissance) et lambda_max."""
    n = len(A)
    w = [1.0 / n for _ in range(n)]
    for _ in range(iterations):
        aw = [sum(A[i][j] * w[j] for j in range(n)) for i in range(n)]
        suivant = _normaliser(aw)
        ecart = max(abs(suivant[i] - w[i]) for i in range(n))
        w = suivant
        if ecart < tolerance:
            break
    aw = [sum(A[i][j] * w[j] for j in range(n)) for i in range(n)]
    lambda_max = sum(aw[i] / w[i] for i in range(n)) / n
    return w, lambda_max


def ratio_coherence(A: list[list[float]], lambda_max: float) -> float:
    """Ratio de cohérence CR = CI / RI (0 si n <= 2)."""
    n = len(A)
    if n <= 2:
        return 0.0
    ci = (lambda_max - n) / (n - 1)
    return max(0.0, ci / RI[n - 1])


def calculer_ahp(
    criteres: list[dict],
    curseurs: dict[str, int] | None = None,
    matrice: list[list[float]] | None = None,
    notes: dict[str, float] | None = None,
    tache_ids: list[int] | None = None,
) -> dict:
    """Exécute le calcul AHP complet pour des critères et des tâches candidates."""
    if matrice is not None:
        if len(matrice) != len(criteres):
            raise ValueError("La taille de la matrice doit correspondre au nombre de critères.")
        A = [[float(x) for x in row] for row in matrice]
    else:
        A = matrice_depuis_comparaisons(criteres, curseurs)

    erreurs = verifier_matrice(A)
    if erreurs:
        raise ValueError(" ".join(erreurs))

    poids, lambda_max = poids_vecteur_propre(A)
    cr = ratio_coherence(A, lambda_max)
    est_coherent = cr <= SEUIL_COHERENCE

    notes = notes or {}
    tache_ids = tache_ids or []
    poids_taches: dict[int, float] = {}
    for tid in tache_ids:
        score = 0.0
        for idx, crit in enumerate(criteres):
            note = float(notes.get(f"{tid}-{crit['id']}", 5.0))
            note = max(0.0, min(10.0, note))
            score += poids[idx] * note
        poids_taches[tid] = round(score, 4)

    return {
        "matrice": [[round(x, 6) for x in row] for row in A],
        "poids_criteres": [round(x, 6) for x in poids],
        "lambda_max": round(lambda_max, 6),
        "ratio_coherence": round(cr, 5),
        "est_coherent": est_coherent,
        "seuil_coherence": SEUIL_COHERENCE,
        "poids_taches": poids_taches,
    }

