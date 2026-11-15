import math
import time
from typing import Any

# Limites alignées avec frontend/src/lib/solveur/modele.js (v5_5)
LIMITES = {
    "taches": 200,
    "horizon": 36,
    "precedences": 1000,
    "montant": 1e9,
    "poids": 1e6,
    "delta": 1.0,
    "messages": 20,
}


def _a_un_cycle(ids: list[int], prec: list[list[int]]) -> bool:
    suivants: dict[int, list[int]] = {tid: [] for tid in ids}
    for i, j in prec:
        suivants[i].append(j)
    etat: dict[int, int] = {}  # 1 = en cours, 2 = terminé

    def visiter(node: int) -> bool:
        st = etat.get(node)
        if st == 1:
            return True
        if st == 2:
            return False
        etat[node] = 1
        for nxt in suivants[node]:
            if visiter(nxt):
                return True
        etat[node] = 2
        return False

    return any(visiter(tid) for tid in ids)


def valider_instance(inst: dict[str, Any]) -> list[dict[str, Any]]:
    """Valide une instance du modèle d'optimisation v5_5."""
    erreurs: list[dict[str, Any]] = []

    def ajouter(code: str, **params: Any) -> None:
        erreurs.append({"code": code, **params})

    if not isinstance(inst, dict):
        return [{"code": "instance"}]

    h = inst.get("h")
    if not isinstance(h, int) or isinstance(h, bool) or h < 1 or h > LIMITES["horizon"]:
        ajouter("h", max=LIMITES["horizon"])

    for champ in ("S0", "Smin", "delta"):
        v = inst.get(champ)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0:
            ajouter("nombre", champ=champ)

    delta = inst.get("delta")
    if isinstance(delta, (int, float)) and math.isfinite(delta) and delta > LIMITES["delta"]:
        ajouter("delta_max", max=LIMITES["delta"])

    for champ in ("S0", "Smin"):
        v = inst.get(champ)
        if isinstance(v, (int, float)) and math.isfinite(v) and v > LIMITES["montant"]:
            ajouter("montant_max", champ=champ, max=LIMITES["montant"])

    D = inst.get("D")
    if (
        not isinstance(D, list)
        or not isinstance(h, int)
        or len(D) != h
        or not all(
            isinstance(d, (int, float))
            and not isinstance(d, bool)
            and math.isfinite(d)
            and 0 <= d <= LIMITES["montant"]
            for d in D
        )
    ):
        ajouter("D", max=LIMITES["montant"])

    taches = inst.get("taches")
    if not isinstance(taches, list) or len(taches) == 0 or len(taches) > LIMITES["taches"]:
        ajouter("nb_taches", max=LIMITES["taches"])
        return erreurs

    ids: set[int] = set()
    for k, t in enumerate(taches):
        if len(erreurs) >= LIMITES["messages"]:
            break
        if not isinstance(t, dict):
            ajouter("tache_objet", index=k + 1)
            continue
        tid = t.get("id")
        if not isinstance(tid, int) or isinstance(tid, bool):
            ajouter("id", index=k + 1)
        elif tid in ids:
            ajouter("id_double", id=tid)
        if isinstance(tid, int) and not isinstance(tid, bool):
            ids.add(tid)

        cout = t.get("cout")
        if not isinstance(cout, (int, float)) or not math.isfinite(cout) or cout < 0 or cout > LIMITES["montant"]:
            ajouter("cout", id=tid, max=LIMITES["montant"])
        duree = t.get("duree")
        if not isinstance(duree, int) or isinstance(duree, bool) or duree < 1:
            ajouter("duree", id=tid)
        poids = t.get("poids")
        if not isinstance(poids, (int, float)) or not math.isfinite(poids) or poids < 0 or poids > LIMITES["poids"]:
            ajouter("poids", id=tid, max=LIMITES["poids"])

    prec = inst.get("precedences") or []
    if not isinstance(prec, list):
        ajouter("precedences_liste")
    elif len(prec) > LIMITES["precedences"]:
        ajouter("nb_precedences", max=LIMITES["precedences"])
    else:
        vues: set[tuple[int, int]] = set()
        for p in prec:
            if len(erreurs) >= LIMITES["messages"]:
                break
            if not isinstance(p, list) or len(p) != 2 or p[0] not in ids or p[1] not in ids:
                ajouter("precedence", valeur=str(p)[:40])
            elif p[0] == p[1]:
                ajouter("boucle", id=p[0])
            elif (p[0], p[1]) in vues:
                ajouter("precedence_double", valeur=f"{p[0]} -> {p[1]}")
            else:
                vues.add((p[0], p[1]))
        if not erreurs and _a_un_cycle(list(ids), prec):
            ajouter("cycle")

    return erreurs


def verifier_plan(
    inst: dict[str, Any], lancements: list[dict[str, int]], tolerance: float = 1e-6
) -> dict[str, Any]:
    """Vérifie indépendamment un plan (contraintes 2, 3, 4, 5 et objectif 1)."""
    mois_par_tache: dict[int, int] = {}
    for item in lancements:
        tid = item["id"]
        if tid in mois_par_tache:
            return {"ok": False, "raison": "lancement_double"}
        mois_par_tache[tid] = item["mois"]

    taches_par_id = {t["id"]: t for t in inst["taches"]}
    soldes: list[float] = []
    S = float(inst["S0"])
    h = int(inst["h"])
    for t in range(1, h + 1):
        depenses = sum(
            float(tache["cout"])
            for tache in inst["taches"]
            if mois_par_tache.get(tache["id"]) == t
        )
        S += float(inst["D"][t - 1]) - depenses
        if S < float(inst["Smin"]) - tolerance:
            return {"ok": False, "raison": "reserve", "mois": t}
        soldes.append(round(S, 2))

    for i, j in inst.get("precedences") or []:
        if j not in mois_par_tache:
            continue
        di = int(taches_par_id[i]["duree"])
        if i not in mois_par_tache or mois_par_tache[i] > mois_par_tache[j] - di:
            return {"ok": False, "raison": "precedence", "taches": [i, j]}

    delta = float(inst["delta"])
    valeur = sum(
        float(tache["poids"]) / ((1.0 + delta) ** (mois_par_tache[tache["id"]] - 1))
        for tache in inst["taches"]
        if tache["id"] in mois_par_tache
    )
    return {"ok": True, "soldes": soldes, "valeur": valeur}


def _solve_with_highspy(inst: dict[str, Any], limite_temps_s: float) -> dict[str, Any]:
    import highspy

    debut = time.perf_counter()
    h = int(inst["h"])
    S0 = float(inst["S0"])
    Smin = float(inst["Smin"])
    delta = float(inst["delta"])
    D = [float(x) for x in inst["D"]]
    taches = inst["taches"]
    n_t = len(taches)
    index = {t["id"]: k for k, t in enumerate(taches)}

    model = highspy.Highs()
    model.setOptionValue("output_flag", False)
    model.setOptionValue("time_limit", float(limite_temps_s))
    model.setOptionValue("mip_rel_gap", 0.0)
    model.setOptionValue("mip_abs_gap", 0.0)

    # Variables x[k, t] (indices 0 .. n_t*h - 1), puis S[t] (indices n_t*h .. n_t*h + h - 1)
    x_var = {}
    for k, tache in enumerate(taches):
        for t in range(1, h + 1):
            coef = float(tache["poids"]) / ((1.0 + delta) ** (t - 1))
            idx = model.getNumCol()
            model.addVar(0.0, 1.0)
            model.changeColCost(idx, coef)
            model.changeColIntegrality(idx, highspy.HighsVarType.kInteger)
            x_var[(k, t)] = idx

    s_var = {}
    for t in range(1, h + 1):
        idx = model.getNumCol()
        model.addVar(Smin, highspy.kHighsInf)
        model.changeColCost(idx, 0.0)
        s_var[t] = idx

    model.changeObjectiveSense(highspy.ObjSense.kMaximize)

    # (2) Au plus un lancement par tâche
    for k in range(n_t):
        cols = [x_var[(k, t)] for t in range(1, h + 1)]
        vals = [1.0] * h
        model.addRow(-highspy.kHighsInf, 1.0, len(cols), cols, vals)

    # (3) Évolution du solde : S_t - S_{t-1} + sum_k c_k x_{k,t} = D_t (ou S0 + D_1 pour t=1)
    for t in range(1, h + 1):
        cols = [s_var[t]]
        vals = [1.0]
        if t > 1:
            cols.append(s_var[t - 1])
            vals.append(-1.0)
        for k, tache in enumerate(taches):
            cout = float(tache["cout"])
            if cout != 0.0:
                cols.append(x_var[(k, t)])
                vals.append(cout)
        rhs = (S0 + D[0]) if t == 1 else D[t - 1]
        model.addRow(rhs, rhs, len(cols), cols, vals)

    # (5) Précédences : x[j, t] - sum_{tau=1}^{t - d_i} x[i, tau] <= 0
    for a, b in inst.get("precedences") or []:
        i = index[a]
        j = index[b]
        di = int(taches[i]["duree"])
        for t in range(1, h + 1):
            cols = [x_var[(j, t)]]
            vals = [1.0]
            for tau in range(1, t - di + 1):
                cols.append(x_var[(i, tau)])
                vals.append(-1.0)
            model.addRow(-highspy.kHighsInf, 0.0, len(cols), cols, vals)

    model.run()
    elapsed = round(time.perf_counter() - debut, 4)
    status = model.getModelStatus()

    if status == highspy.HighsModelStatus.kInfeasible:
        return {"statut": "infaisable", "temps_calcul_s": elapsed}

    sol = model.getSolution()
    has_primal = sol.value_valid and len(sol.col_value) >= n_t * h
    if status == highspy.HighsModelStatus.kTimeLimit and not has_primal:
        return {"statut": "limite_temps_sans_plan", "temps_calcul_s": elapsed}

    if status not in (highspy.HighsModelStatus.kOptimal, highspy.HighsModelStatus.kTimeLimit) or not has_primal:
        return {
            "statut": "erreur",
            "erreurs": [{"code": "solveur", "detail": str(status)}],
            "temps_calcul_s": elapsed,
        }

    lancements: list[dict[str, int]] = []
    for k, tache in enumerate(taches):
        for t in range(1, h + 1):
            if sol.col_value[x_var[(k, t)]] > 0.5:
                lancements.append({"id": tache["id"], "mois": t})

    verifie = verifier_plan(inst, lancements)
    if not verifie["ok"]:
        return {
            "statut": "erreur",
            "erreurs": [{"code": "plan_incoherent", "detail": verifie["raison"]}],
            "temps_calcul_s": elapsed,
        }

    return {
        "statut": "limite_temps" if status == highspy.HighsModelStatus.kTimeLimit else "optimal",
        "valeur": round(verifie["valeur"], 6),
        "lancements": lancements,
        "soldes": verifie["soldes"],
        "temps_calcul_s": elapsed,
    }


def _solve_fallback_exact(inst: dict[str, Any], limite_temps_s: float) -> dict[str, Any]:
    """Solveur de secours (branch-and-bound pur Python) si highspy n'est pas installé."""
    debut = time.perf_counter()
    h = int(inst["h"])
    S0 = float(inst["S0"])
    Smin = float(inst["Smin"])
    delta = float(inst["delta"])
    D = [float(x) for x in inst["D"]]
    taches = sorted(inst["taches"], key=lambda x: float(x["poids"]), reverse=True)
    n_t = len(taches)
    preds: dict[int, list[int]] = {t["id"]: [] for t in taches}
    succs: dict[int, list[int]] = {t["id"]: [] for t in taches}
    dur_map = {t["id"]: int(t["duree"]) for t in taches}
    for a, b in inst.get("precedences") or []:
        preds[b].append(a)
        succs[a].append(b)

    # Vérifier faisabilité sans aucune dépense
    s = S0
    for t in range(1, h + 1):
        s += D[t - 1]
        if s < Smin - 1e-6:
            return {"statut": "infaisable", "temps_calcul_s": round(time.perf_counter() - debut, 4)}

    best_val = -1.0
    best_assign: dict[int, int] = {}
    timed_out = False

    # Bornes supérieures optimistes pour l'élagage
    suffix_max = [0.0] * (n_t + 1)
    for idx in range(n_t - 1, -1, -1):
        suffix_max[idx] = suffix_max[idx + 1] + float(taches[idx]["poids"])

    spent = [0.0] * h
    assign: dict[int, int] = {}

    def dfs(idx: int, current_val: float) -> None:
        nonlocal best_val, best_assign, timed_out
        if time.perf_counter() - debut > limite_temps_s:
            timed_out = True
            return
        if current_val + suffix_max[idx] <= best_val + 1e-9:
            return
        if idx == n_t:
            # S'assurer qu'aucune tâche lancée ne manque un prédécesseur non lancé
            for tid_s, m_s in assign.items():
                for p_id in preds[tid_s]:
                    if p_id not in assign or assign[p_id] > m_s - dur_map[p_id]:
                        return
            best_val = current_val
            best_assign = dict(assign)
            return

        tache = taches[idx]
        tid = tache["id"]
        cout = float(tache["cout"])
        poids = float(tache["poids"])

        # Essayer chaque mois t=1..h puis non-lancement
        for t in range(1, h + 1):
            # Vérifier précédences (prédécesseurs et successeurs déjà affectés)
            ok_prec = True
            for p_id in preds[tid]:
                if p_id in assign and assign[p_id] > t - dur_map[p_id]:
                    ok_prec = False
                    break
            if ok_prec:
                for s_id in succs[tid]:
                    if s_id in assign and t > assign[s_id] - dur_map[tid]:
                        ok_prec = False
                        break
            if not ok_prec:
                continue

            # Vérifier réserve S[tau] >= Smin pour tau >= t
            spent[t - 1] += cout
            s_bal = S0
            feasible = True
            for tau in range(h):
                s_bal += D[tau] - spent[tau]
                if s_bal < Smin - 1e-6:
                    feasible = False
                    break
            if feasible:
                assign[tid] = t
                gain = poids / ((1.0 + delta) ** (t - 1))
                dfs(idx + 1, current_val + gain)
                del assign[tid]
            spent[t - 1] -= cout
            if timed_out:
                return

        if not any(s_id in assign for s_id in succs[tid]):
            dfs(idx + 1, current_val)

    dfs(0, 0.0)
    elapsed = round(time.perf_counter() - debut, 4)
    lancements = [{"id": tid, "mois": m} for tid, m in sorted(best_assign.items(), key=lambda x: (x[1], x[0]))]
    verifie = verifier_plan(inst, lancements)
    if not verifie["ok"]:
        return {"statut": "infaisable", "temps_calcul_s": elapsed}
    return {
        "statut": "limite_temps" if timed_out else "optimal",
        "valeur": round(verifie["valeur"], 6),
        "lancements": lancements,
        "soldes": verifie["soldes"],
        "temps_calcul_s": elapsed,
    }


def resoudre_instance(inst: dict[str, Any], limite_temps_s: float = 30.0) -> dict[str, Any]:
    """Valide et résout une instance MILP v5_5 (via HiGHS ou secours exact)."""
    erreurs = valider_instance(inst)
    if erreurs:
        return {"statut": "erreur", "erreurs": erreurs}
    try:
        import highspy  # noqa: F401

        return _solve_with_highspy(inst, limite_temps_s)
    except ImportError:
        return _solve_fallback_exact(inst, limite_temps_s)
