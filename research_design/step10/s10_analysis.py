"""Main-run analysis (specification 12, 12.3, 18.1, 18.2; decision record 12.30). Input: per-call rows from s10_scoring.call_table,
the registry templates, R, the bootstrap seed (from the decision record), n_boot, level, L_small. Output: a canonical dict.

Rules applied exactly as frozen: 12 primary cells; no pooled headline; unclear and unparseable stay in the denominator; SF calls stay
in n_calls (interim reporting, v2.7 18.2 as written, owner decision 2026-10-13): the four outcome proportions are over n_calls, the
structural-failure count and rate are reported beside them, nothing is rescaled; cluster-level mean = mean over the L clusters of
h_b = mean over the cluster's templates of p_t(c) = matches / R over ALL R calls (12). A cell with fewer than R recorded calls for
some template is INCOMPLETE: counts only, no estimate, no interval (13.8; no estimator is invented). Bootstrap (DP8, DP9): clusters
resampled with replacement, random.Random(seed).choices(k=L), a fresh generator per family, replicates sequential, percentile
interval. Per-family stream and percentile index convention are implementation choices (the text does not fix them).
No significance claim is computed.
"""
import random
from fractions import Fraction as F

OUTCOMES = ("satisfied", "violated", "unclear", "UNP")
EXPECT = {"A": "satisfied", "B": "violated", "C": "satisfied", "D": "violated"}


def cells_for(family):
    return [("A", None), ("C", None), ("B", "MILD"), ("B", "CLEAR"), ("D", "MILD"), ("D", "CLEAR")]


def cell_name(fam, cond, cls):
    return f"{fam}:{cond}" + (f":{cls}" if cls else "")


def _cat(row):
    return "SF" if row["outcome"] == "SF" else row["outcome"]


def analyze(rows, templates, R, seed, n_boot, level, L_small):
    tmeta = {t["id"]: t for t in templates}
    by_tc = {}
    for r in rows:
        by_tc.setdefault((r["template_id"], r["condition"]), []).append(r)
    out = {"R": R, "seed": seed, "n_boot": n_boot, "level": str(level), "cells": {}, "contrasts": {}, "status": "COMPLETE"}
    bootstrap_inputs = {}
    for fam in ("MIL", "PAC"):
        fam_t = [t for t in templates if t["family"] == fam]
        bases = sorted({t["base_index"] for t in fam_t})
        L = len(bases)
        hb = {}                                                     # (cell, category) -> {base: Fraction} or None if incomplete
        for cond, cls in cells_for(fam):
            name = cell_name(fam, cond, cls)
            ts = [t for t in fam_t if cls is None or t["class"] == cls]
            calls = [r for t in ts for r in by_tc.get((t["id"], cond), [])]
            complete = all(len(by_tc.get((t["id"], cond), [])) == R for t in ts)
            cnt = {k: 0 for k in OUTCOMES + ("SF",)}
            sf_by = {}
            for r in calls:
                cnt[_cat(r)] += 1
                if r["outcome"] == "SF":
                    sf_by[r["flags"][0]] = sf_by.get(r["flags"][0], 0) + 1
            n = len(calls)
            cell = {"template_conditions": len(ts), "planned_calls": len(ts) * R, "recorded_calls": n, "complete": complete,
                    "counts": cnt, "sf_primary_by_type_provisional": sf_by, "matches": sum(r["match"] for r in calls)}
            if n:
                cell["proportions_over_recorded_calls"] = {k: str(F(v, n)) for k, v in cnt.items()}
                cell["parseable_only_sensitivity"] = ({k: str(F(cnt[k], n - cnt["UNP"])) for k in ("satisfied", "violated", "unclear")}
                                                      if n - cnt["UNP"] > 0 else None)
                cell["proportions_sum_note"] = "four outcome proportions + SF rate = 1; the four alone sum to 1 only if SF = 0"
            if not complete:
                cell["estimate"] = None
                cell["note"] = "INCOMPLETE: counts only, no cluster estimate or interval (13.8)"
                out["status"] = "INCOMPLETE"
            else:
                h = {}
                for cat in OUTCOMES + ("SF",):
                    per_base = {}
                    for b in bases:
                        tb = [t for t in ts if t["base_index"] == b]
                        vals = []
                        for t in tb:
                            rs = by_tc[(t["id"], cond)]
                            vals.append(F(sum(1 for r in rs if _cat(r) == cat), R))
                        per_base[b] = sum(vals) / len(vals)
                    h[cat] = per_base
                mt = {}
                for b in bases:
                    tb = [t for t in ts if t["base_index"] == b]
                    mt[b] = sum(F(sum(r["match"] for r in by_tc[(t["id"], cond)]), R) for t in tb) / len(tb)
                h["match"] = mt
                hb[name] = h
                cell["estimate_cluster_mean"] = {cat: str(sum(h[cat].values()) / L) for cat in h}
            out["cells"][name] = cell
        out.setdefault("clusters", {})[fam] = L
        bootstrap_inputs[fam] = (bases, hb)
    # contrasts and bootstrap, only for complete families
    for fam, (bases, hb) in bootstrap_inputs.items():
        L = len(bases)
        names = {k: cell_name(fam, *k) for k in cells_for(fam)}
        if not all(names[k] in hb for k in names):
            out["contrasts"][fam] = {"status": "INCOMPLETE: no contrast, no interval"}
            continue

        def cond_mean(cond, b):                                     # h_b over all templates of the base in the condition (both classes)
            return (hb[names[(cond, "MILD")]]["match"][b] + hb[names[(cond, "CLEAR")]]["match"][b]) / 2

        def stat_fns():
            f = {}
            for k, nm in names.items():
                f[nm] = (lambda bs, nm=nm: sum(hb[nm]["match"][b] for b in bs) / len(bs))
            f[f"{fam}:contrast C-A"] = lambda bs: sum(hb[names[("C", None)]]["match"][b] - hb[names[("A", None)]]["match"][b] for b in bs) / len(bs)
            f[f"{fam}:contrast D-B"] = lambda bs: sum(cond_mean("D", b) - cond_mean("B", b) for b in bs) / len(bs)
            for cond in ("B", "D"):
                f[f"{fam}:class contrast MILD-CLEAR in {cond}"] = (lambda bs, cond=cond: sum(
                    hb[names[(cond, "MILD")]]["match"][b] - hb[names[(cond, "CLEAR")]]["match"][b] for b in bs) / len(bs))
            return f
        fns = stat_fns()
        point = {k: v(bases) for k, v in fns.items()}
        res = {k: {"point": str(point[k]), "point_float": float(point[k])} for k in fns}
        if L < L_small:
            for k in res:
                res[k]["interval"] = None
                res[k]["note"] = f"L={L} < L_small={L_small}: counts and per-cluster proportions only"
        else:
            rng = random.Random(seed)
            reps = {k: [] for k in fns}
            for _ in range(n_boot):
                bs = [bases[i] for i in rng.choices(range(L), k=L)]
                for k, fn in fns.items():
                    reps[k].append(float(fn(bs)))
            a = (1 - float(level)) / 2
            lo_i, hi_i = int(a * n_boot), int((1 - a) * n_boot) - 1
            for k in res:
                s = sorted(reps[k])
                res[k]["interval"] = [s[lo_i], s[hi_i]]
                if s[0] == s[-1]:
                    res[k]["note"] = "interval not informative (all resamples equal); planning scale z/(2*sqrt(L)) applies"
        out["contrasts"][fam] = res
    return out
