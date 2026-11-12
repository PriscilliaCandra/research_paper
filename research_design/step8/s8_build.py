"""Step 8 registry generation: single all-or-nothing build (specification v2.7 section 21.1 row 8) -- decision record 12.28.

Local only: no model call, no network, no token counting. Stages: S1 frozen-input validation; S2 independent construction
(s8_construct); S3 numeric gates M*, Q*, A* by Implementation A applied to the GENERATOR's templates; S4 equivalence with
Implementation A's builders (E-ID, E-PAR, E-GATE, E-PROMPT); S5 rendering of all prompts and rendering-stage gates (s8_gates);
S6 serialization and hashes; S7 repeat-build comparison; S8 atomic publication (one rename). Any failure rejects the whole build:
nothing is published, a failure log is written to a separate folder, nothing is repaired or retried.

Usage: python -B -I research_design/step8/s8_build.py --out research_design/step8_output
Exit codes: 0 published, 2 build rejected, 3 target exists / invalid input.
"""
import argparse
import datetime
import hashlib
import json
import os
import platform
import shutil
import sys
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.normpath(os.path.join(HERE, ".."))
for d in (HERE, os.path.join(RD, "step7"), os.path.join(RD, "implementation_a")):
    sys.path.insert(0, d)
sys.dont_write_bytecode = True
import impl_a as A  # noqa: E402  (Implementation A: the frozen dry-run code; its gates run at step 8, its builders are the comparison)
import s7_decision as D  # noqa: E402
import s8_construct as K  # noqa: E402
import s8_gates as GT  # noqa: E402

SPEC_SHA = "d5fcffadd1045fc0c86a7c84eb8d51592d807576b6a51b712cef025164082ca8"
FZ_REG_SHA = "c7d50e115b08b6d70b9c35f1d09183118c3d2aaffa89b1c838987f7ab73987a2"
IMPL_A_SHA = "daa2cd49b86ed1a546e95478bc3f40c1fc131ea52d4622b981e4e3b117f018ee"
FREEZE_MANIFEST = "STEP7_PROMPT_SPECIFICATION_FREEZE_MANIFEST_v1.json"
# The FZ version in use (decision record 12.64, D2): FZ-v1 (Gemini, default) or FZ-v2 (Ollama; only register item 17 differs). The Step 7 freeze manifest of FZ-v2 cites the FZ-v2 register.
FZ_VERSIONS = {
    "v1": {"label": "FZ-v1", "file": "PARAMETER_FREEZE_MANIFEST_FZ_v1.json", "register_sha": FZ_REG_SHA, "freeze_manifest": FREEZE_MANIFEST, "freeze_key": "fz_v1_register",
           "settings": ("gemini-3.5-flash-lite", "minimal", 8192, 0, 180, 1, 1)},
    "v2": {"label": "FZ-v2", "file": "PARAMETER_FREEZE_MANIFEST_FZ_v2.json", "register_sha": "72972e446c23f63c8e4dd3b20e5b7c12ef25c44d88c33a114b5c3f92d1eeaf30",
           "freeze_manifest": "STEP7_PROMPT_SPECIFICATION_FREEZE_MANIFEST_v2.json", "freeze_key": "fz_register",
           "settings": ("llama3.2:3b", "not applicable", 8192, 0, 180, 1, 1)},
}
ACTIVE = FZ_VERSIONS["v1"]
SCHEMA = "s8-registry-v1"


class Rejected(Exception):
    def __init__(self, gate, problems):
        super().__init__(gate)
        self.gate, self.problems = gate, problems


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with open(path, "rb") as fh:
        return sha(fh.read())


def canonical_bytes(obj):
    """Canonical JSON (decision 12.28): sorted keys, compact separators, ensure_ascii=False, UTF-8, one final LF."""
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


# ----------------------------------------------------------------------------- S1
def load_inputs():
    probs = []
    spec = os.path.join(RD, "SCENARIO_CONSTRUCTION_SPECIFICATION_v2_7_DRAFT.md")
    if file_sha(spec) != SPEC_SHA:
        probs.append("specification hash differs")
    fzp = os.path.join(RD, ACTIVE["file"])
    with open(fzp, "rb") as fh:
        fz = json.loads(fh.read().decode("utf-8"))
    if D.register_hash(fz) != ACTIVE["register_sha"] or fz["register_canonical_sha256"] != ACTIVE["register_sha"]:
        probs.append(f"{ACTIVE['label']} register hash differs")
    if file_sha(os.path.join(RD, "implementation_a", "impl_a.py")) != IMPL_A_SHA:
        probs.append("impl_a.py hash differs")
    fmp = os.path.join(RD, "step7", ACTIVE["freeze_manifest"])
    if not os.path.exists(fmp):
        probs.append("Step 7 freeze manifest missing")
        fm = {"freeze_set_files": {}}
    else:
        with open(fmp, "rb") as fh:
            fm = json.loads(fh.read().decode("utf-8"))
        for name, h in fm["freeze_set_files"].items():
            if file_sha(os.path.join(RD, "step7", name)) != h:
                probs.append(f"Step 7 freeze-set file changed: {name}")
        if fm[ACTIVE["freeze_key"]]["register_canonical_sha256"] != ACTIVE["register_sha"]:
            probs.append("freeze manifest cites another register hash")
    m = fz["register"]["row17_model_and_settings"]
    if (m["model_id"], m["thinking_level"], m["max_output_tokens"], m["retries"], m["timeout_s"], m["delay_between_calls_s"],
            m["concurrency"]) != ACTIVE["settings"]:
        probs.append("model settings of the register differ from the expected frozen values")
    if probs:
        raise Rejected("S1 frozen inputs", probs)
    return fz, fm, file_sha(fmp)


def generator_code_hashes(fm):
    h = {f"step8/{n}": file_sha(os.path.join(HERE, n)) for n in ("s8_construct.py", "s8_gates.py", "s8_build.py")}
    h.update({f"step7/{n}": v for n, v in sorted(fm["freeze_set_files"].items())})
    h["implementation_a/impl_a.py"] = IMPL_A_SHA
    return h


# ----------------------------------------------------------------------------- A's gates on the generator's templates
def a_register(fz, S, G):
    r = fz["register"]
    w = r["row4_windows_shared_OD5"]
    ws = A.WindowSet(w["W_V"][0], w["W_V"][1], w["W_MILD"][0], w["W_MILD"][1], w["W_CLEAR"][0], w["W_CLEAR"][1])
    pz, iv, mx = r["row14_precision"], r["row15_interval"], r["row10_plausibility"]
    sz = A.Sizing(z=pz["z"], hw_star=pz["hw_star"], C_max=r["row13_C_max_planned_main_call_slots"], R_min=r["row13_R_min"],
                  R_cap=r["row13_R_cap"], n_boot=iv["n_boot"], L_small=iv["L_small"], level=pz["level"])
    return A.Register(P=r["row1_P_percent"], N=r["row2_N"], tau_F=r["row6_tau_F_s"], rho_NT=r["row3_rho_NT"], windows=ws,
                      D_dist=r["row5_D_dist_km"], S_list=S, G_list=G, X_min=r["row9_X"]["X_min"], X_max=r["row9_X"]["X_max"],
                      p_min=mx["p_min"], p_max=mx["p_max"], G_min=mx["G_min"], G_max=mx["G_max"], Tm_min=mx["Tm_min"],
                      Tm_max=mx["Tm_max"], sizing=sz)


def to_a_templates(templates):
    mil, pac = {}, {}
    for t in templates:
        if t["family"] == "MIL":
            mil[(t["base_index"], t["position_index"])] = A.MilTemplate(t["base_index"], t["base_value"], t["position_index"], t["class"],
                                                                         tuple(t["valid"]["weekly_tenths_km"]), tuple(t["violating"]["weekly_tenths_km"]))
        else:
            def mem(m):
                return A.PacMember(F(m["D_dist"]["num"], m["D_dist"]["den"]), m["T_time_s"], m["tau_F_s"], tuple(m["tokens_s_per_km"]))
            pac[(t["base_index"], t["position_index"], t["direction"])] = A.PacTemplate(t["base_index"], t["base_value"], t["position_index"], t["direction"],
                                                                                     t["class"], mem(t["valid"]), mem(t["violating"]))
    return mil, pac


def gates_on_generator(reg, templates):
    """Implementation A's run_numeric_gates, with its two builders replaced IN MEMORY by lookups of the generator's templates,
    so that the 30 numeric-stage gates are evaluated on the generator's data. The module on disk is not changed."""
    mil, pac = to_a_templates(templates)
    orig_m, orig_p = A.build_mil_template, A.build_pac_template

    def shim_m(r, b, i, cls):
        t = mil[(b, i)]
        if t.cls != cls:
            raise A.SpecError("class mismatch between generator and class rule")
        return t

    def shim_p(r, b, i, s, cls):
        t = pac[(b, i, s)]
        if t.cls != cls:
            raise A.SpecError("class mismatch between generator and class rule")
        return t
    A.build_mil_template, A.build_pac_template = shim_m, shim_p
    try:
        return A.run_numeric_gates(reg)
    finally:
        A.build_mil_template, A.build_pac_template = orig_m, orig_p


def verdicts(rep):
    return {g: (o.evaluated and o.passed) for g, o in rep.outcomes.items()}


def equivalence(reg, templates, prompts, p):
    """E-ID, E-PAR, E-GATE, E-PROMPT against Implementation A's own builders (decision record 12.28)."""
    out = {"E-ID": [], "E-PAR": [], "E-GATE": [], "E-PROMPT": []}
    amil, apac = A.build_registry(reg)
    am = {(x.b, x.i): x for x in amil}
    ap = {(x.b, x.i, x.s): x for x in apac}
    n_mil = sum(1 for t in templates if t["family"] == "MIL")
    if (len(am), len(ap)) != (n_mil, len(templates) - n_mil):
        out["E-ID"].append(f"template counts differ: A {len(am)}+{len(ap)}, generator {n_mil}+{len(templates) - n_mil}")
    ids = [t["id"] for t in templates]
    fams = [t["family"] for t in templates]
    if len(set(ids)) != len(ids):
        out["E-ID"].append("duplicate IDs")
    for fam in ("MIL", "PAC"):                              # IDs must be MIL-001.. then PAC-001.. in canonical order, counters 1-based
        sub = [t["id"] for t in templates if t["family"] == fam]
        w = max(3, len(str(len(sub))))
        if sub != [f"{fam}-{n:0{w}d}" for n in range(1, len(sub) + 1)]:
            out["E-ID"].append(f"{fam} IDs are not the canonical sequence")
    if fams != sorted(fams, key=lambda f: 0 if f == "MIL" else 1):
        out["E-ID"].append("MIL does not precede PAC")
    keys_seen = []
    for t in templates:
        if t["family"] == "MIL":
            a = am.get((t["base_index"], t["position_index"]))
            ok = a is not None and list(a.valid) == t["valid"]["weekly_tenths_km"] and list(a.viol) == t["violating"]["weekly_tenths_km"] and a.cls == t["class"] and a.S_b == t["base_value"]
        else:
            a = ap.get((t["base_index"], t["position_index"], t["direction"]))
            ok = (a is not None and list(a.valid.tokens) == t["valid"]["tokens_s_per_km"] and list(a.viol.tokens) == t["violating"]["tokens_s_per_km"]
                  and a.valid.T_time == t["valid"]["T_time_s"] == a.viol.T_time and a.valid.tau_F == t["valid"]["tau_F_s"] and a.valid.D_dist == F(t["valid"]["D_dist"]["num"], t["valid"]["D_dist"]["den"])
                  and a.cls == t["class"] and a.G_b == t["base_value"])
        if not ok:
            out["E-PAR"].append(f"{t['id']}: parameters differ from Implementation A's builder")
            continue
        # E-PROMPT: prompts rendered from A's values equal the generator's prompts
        kw = GT.render_kwargs(t, p)
        for c, (which, claim) in GT.CONDS.items():
            av = (a.valid if which == "valid" else a.viol)
            vals = list(av) if t["family"] == "MIL" else list(av.tokens)
            if GT.C.build_prompt(t["family"], vals, claim=claim, **kw) != prompts[t["id"]][c]:
                out["E-PROMPT"].append(f"{t['id']} {c}: prompt differs")
    ra = A.run_numeric_gates(reg)
    rg = gates_on_generator(reg, templates)
    if verdicts(ra) != verdicts(rg):
        out["E-GATE"].append("gate verdicts differ between A's own build and the generator's templates")
    return out, ra, rg


def pilot_selection(templates):
    """Section 13.3: for each required cell the first template in canonical order with the required class and direction."""
    sel = []
    def first(fam, direction=None, cls=None):
        for t in templates:
            if t["family"] == fam and (direction is None or t["direction"] == direction) and (cls is None or t["class"] == cls):
                return t["id"]
        raise Rejected("pilot selection", [f"no template for {fam} {direction} {cls}"])
    for c in ("A", "C"):
        sel.append((first("MIL"), c, "MIL", None, None))
        for s in (0, 1):
            sel.append((first("PAC", s), c, "PAC", s, None))
    for c in ("B", "D"):
        for cls in ("MILD", "CLEAR"):
            sel.append((first("MIL", None, cls), c, "MIL", None, cls))
            for s in (0, 1):
                sel.append((first("PAC", s, cls), c, "PAC", s, cls))
    order = {t["id"]: n for n, t in enumerate(templates)}
    sel.sort(key=lambda x: (order[x[0]], x[1]))
    return sel


# ----------------------------------------------------------------------------- one registry
def build_one(kind, fz, fm, S, G, prov_extra):
    p = K.params_from_fz(fz["register"])
    templates = K.construct(p, S, G)
    reg = a_register(fz, S, G)
    prompts = GT.render_all(templates, p)
    eq, ra, rg = equivalence(reg, templates, prompts, p)
    probs = {g: v for g, v in eq.items() if v}
    fails = [g for g, ok in verdicts(rg).items() if not ok]
    if fails:
        probs["numeric gates on generator"] = [f"{g} {rg.outcomes[g].failures[:3]}" for g in fails]
    n_exp = {"main": (104, 208), "pilot": (8, 16)}[kind]
    n_mil = sum(1 for t in templates if t["family"] == "MIL")
    if (n_mil, len(templates) - n_mil) != n_exp:
        probs["counts"] = [f"{n_mil}+{len(templates) - n_mil} templates, expected {n_exp}"]
    registry = {
        "schema_version": SCHEMA, "registry_kind": kind,
        "provenance": dict(prov_extra, generator_code_sha256=generator_code_hashes(fm)),
        "register_values": {"P": p["P"], "N": p["N"], "K": p["N"] - 1, "rho_NT": str(p["rho_NT"]), "D_dist": str(p["D"]), "tau_F_s": p["tau_F"],
                            "windows": {k: [str(v[0]), str(v[1])] for k, v in p["windows"].items()}, "S_list": list(S), "G_list": list(G)},
        "counts": {"templates_MIL": n_mil, "templates_PAC": len(templates) - n_mil, "templates": len(templates), "prompts": 4 * len(templates)},
        "templates": templates,
    }
    rbytes = canonical_bytes(registry)
    rhash = sha(rbytes)
    G_, bundle, mapping = GT.run_gates(templates, prompts, p, rhash, p["windows"])
    for g, v in G_.items():
        if v:
            probs[g] = v[:20]
    if probs:
        raise Rejected(f"{kind} registry", probs)
    files = {f"{kind}/registry.json": rbytes}
    table = []
    for t in templates:
        for c in "ABCD":
            txt = prompts[t["id"]][c]
            files[f"{kind}/prompts/{t['id']}-{c}.txt"] = txt.encode("ascii")
            table.append({"template_id": t["id"], "condition": c, "sha256": GT.sha256_text(txt), "chars": len(txt)})
    files[f"{kind}/prompt_table.json"] = canonical_bytes(table)
    for oid, body in bundle.items():
        files[f"{kind}/checker_bundle/{oid}"] = body.encode("ascii")
    files[f"{kind}/checker_mapping_HELD_SEPARATELY.json"] = canonical_bytes(mapping)
    # I-6 informational diagnostic (not a gate; decision 12.28)
    inv = []
    amil, apac = to_a_templates(templates)
    for t in amil.values():
        inv += A.reading_invariance_mil(reg, t)
    for t in apac.values():
        inv += A.reading_invariance_pac(t)
    report = {
        "registry_kind": kind, "registry_sha256": rhash, "templates": len(templates), "prompts": 4 * len(templates),
        "numeric_gates_on_generator": {g: ("PASS" if ok else "FAIL") for g, ok in verdicts(rg).items()},
        "numeric_gates_on_implementation_a_builders": {g: ("PASS" if ok else "FAIL") for g, ok in verdicts(ra).items()},
        "equivalence": {k: ("PASS" if not v else "FAIL") for k, v in eq.items()},
        "rendering_gates": {g: ("PASS" if not v else "FAIL") for g, v in G_.items()},
        "PG3_strict_nonconsecutive_pairs_informational": len(GT.STRICT_NONCONSECUTIVE),
        "checker_bundle_files": len(bundle), "I6_reading_invariance_informational_failures": len(inv),
    }
    files[f"{kind}/gate_report.json"] = canonical_bytes(report)
    return files, templates, rhash, report


def build_all(fz, fm, fm_hash):
    prov = {"specification_sha256": SPEC_SHA, "fz_v1_register_sha256": ACTIVE["register_sha"], "step7_freeze_manifest_sha256": fm_hash}
    if ACTIVE["label"] != "FZ-v1":
        prov["fz_register_label"] = ACTIVE["label"]                  # the key above holds the register hash of the FZ version in use (name kept for compatibility)
    files, summary = {}, {}
    r = fz["register"]
    p = K.params_from_fz(r)
    for kind, S, G in (("main", p["S_main"], p["G_main"]), ("pilot", p["S_pilot"], p["G_pilot"])):
        f, templates, rhash, rep = build_one(kind, fz, fm, S, G, prov)
        files.update(f)
        summary[kind] = {"registry_sha256": rhash, "templates": len(templates), "prompts": 4 * len(templates),
                         "prompt_table_sha256": sha(f[f"{kind}/prompt_table.json"])}
        if kind == "main":
            ids = [t["id"] for t in templates]
            if ids != D.expected_template_ids(r):
                raise Rejected("call-list cross-check", ["generated IDs differ from expected_template_ids(register)"])
            text = D.call_list(ids, r["row12_R"])
            if 4 * r["row12_R"] * len(ids) != r["row13_C_max_planned_main_call_slots"]:
                raise Rejected("call-list cross-check", ["slot count differs from C_max"])
            summary["main"]["call_list_sha256"] = sha(text.encode("ascii"))
            summary["main"]["call_list_lines"] = text.count("\n")
        else:
            sel = pilot_selection(templates)
            lines = "".join(f"{tid},{c},1\n" for tid, c, *_ in sel)
            if len(sel) != 18:
                raise Rejected("pilot selection", [f"{len(sel)} calls selected, expected 18"])
            files["pilot/pilot_calls.txt"] = lines.encode("ascii")
            files["pilot/pilot_selection.json"] = canonical_bytes([{"template_id": a, "condition": b, "family": c, "direction": d, "class_required": e} for a, b, c, d, e in sel])
            summary["pilot"]["pilot_calls_sha256"] = sha(lines.encode("ascii"))
    inventory = [{"path": k, "sha256": sha(v), "bytes": len(v)} for k, v in sorted(files.items())]
    manifest = {
        "manifest_kind": "Step 8 registry generation manifest", "schema_version": SCHEMA, "carries_no_self_hash": True,
        "specification_sha256": SPEC_SHA, "fz_v1_register_sha256": ACTIVE["register_sha"], "step7_freeze_manifest_sha256": fm_hash,
        "generator_code_sha256": generator_code_hashes(fm),
        "summary": summary, "inventory": inventory,
        "determinism_note": "all files above are byte-identical across repeat builds; build_log.json (interpreter, UTC time) is outside this inventory",
        "not_done": ["Step 9 (Implementation B, human blind check)", "pilot", "API or model call", "token counting"],
    }
    files["manifest.json"] = canonical_bytes(manifest)
    return files, summary


def write_tree(files, root):
    for rel, data in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "xb") as fh:
            fh.write(data)


def publish(out_parent, argv_info=None):
    """Whole build; returns (target dir, summary). Raises Rejected. Writes nothing unless every gate and the repeat build pass."""
    fz, fm, fm_hash = load_inputs()
    files, summary = build_all(fz, fm, fm_hash)
    files2, _ = build_all(fz, fm, fm_hash)
    if files != files2:
        raise Rejected("repeat build", ["two builds in one process differ"])
    target = os.path.join(out_parent, "step8_build_" + summary["main"]["registry_sha256"][:12])
    if os.path.exists(target):
        raise FileExistsError(target)
    tmp = os.path.join(out_parent, f".step8_tmp_{os.getpid()}")
    os.makedirs(out_parent, exist_ok=True)
    try:
        write_tree(files, tmp)
        log = {"interpreter": platform.python_version(), "implementation": platform.python_implementation(), "platform": platform.platform(),
               "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "manifest_sha256": sha(files["manifest.json"]),
               "model_or_network_calls": 0}
        with open(os.path.join(tmp, "build_log.json"), "xb") as fh:
            fh.write(canonical_bytes(log))
        os.rename(tmp, target)                                  # the only publication step
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return target, summary


def main(argv=None):
    ap = argparse.ArgumentParser(prog="s8_build.py")
    ap.add_argument("--out", required=True, help="parent folder of the published build (created if missing)")
    ap.add_argument("--fz-version", choices=sorted(FZ_VERSIONS), default="v1", help="FZ version in use (default v1; v2 = Ollama, decision record 12.64)")
    args = ap.parse_args(argv)
    global ACTIVE
    ACTIVE = FZ_VERSIONS[args.fz_version]
    try:
        target, summary = publish(os.path.abspath(args.out))
    except Rejected as e:
        fdir = os.path.join(os.path.abspath(args.out), "step8_failures")
        os.makedirs(fdir, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with open(os.path.join(fdir, f"failure_{stamp}.json"), "xb") as fh:
            fh.write(canonical_bytes({"stage": e.gate, "problems": e.problems, "published": False}))
        print("BUILD REJECTED:", e.gate, "- nothing published")
        return 2
    except FileExistsError as e:
        print("refused: target exists:", e)
        return 3
    print("published", target)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
