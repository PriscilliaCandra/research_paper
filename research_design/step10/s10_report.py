"""Main-run report: calls.jsonl of a COMPLETE or INCOMPLETE run -> call table, analysis (s10_analysis), markdown summary.
Refuses to overwrite. Usage: python -B -I s10_report.py --run <run dir> --build <build> --decision-record <json> --out <new dir>
Reads response text only through s7_parser (by code); prints no response text. Pilot runs are NOT analysed here (13.7: counts only)."""
import argparse, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.dont_write_bytecode = True
import s10_analysis as AN, s10_runner as RN, s10_scoring as SC

def main(argv=None):
    a = argparse.ArgumentParser(); [a.add_argument(f"--{k}", required=True) for k in ("run", "build", "decision-record", "out")]
    a = a.parse_args(argv)
    summ = [f for f in ("RUN_COMPLETE", "RUN_INCOMPLETE") if os.path.exists(os.path.join(a.run, f))]
    if len(summ) != 1 or RN.jl(os.path.join(a.run, summ[0]))["scope"] != "main":
        print("not a main-run folder"); return 2
    fz = RN.jl(RN.FZ_PATH)["register"]
    reg = RN.jl(os.path.join(a.build, "main", "registry.json"))["templates"]
    rec = RN.jl(a.decision_record)
    with open(os.path.join(a.run, "calls.jsonl"), "rb") as f:
        recs = [json.loads(l) for l in f.read().decode("utf-8").split("\n") if l]
    rows = SC.call_table(recs, {t["id"]: t for t in reg})
    res = AN.analyze(rows, reg, fz["row12_R"], rec["seed"], fz["row15_interval"]["n_boot"], fz["row14_precision"]["level"], fz["row15_interval"]["L_small"])
    res["run_status"] = summ[0]
    os.makedirs(a.out)
    with open(os.path.join(a.out, "analysis.json"), "xb") as f: f.write(RN.cj(res))
    with open(os.path.join(a.out, "call_table.json"), "xb") as f: f.write(RN.cj(rows))
    lines = [f"# Main-run analysis ({summ[0]}, status {res['status']})", "", "Descriptive; no significance claim. SF calls stay in n_calls and are reported beside the four proportions.", "",
             "| cell | recorded/planned | satisfied | violated | unclear | unparseable | SF | cluster-mean match |", "|---|---|---|---|---|---|---|---|"]
    for n, c in res["cells"].items():
        k = c["counts"]; est = (c.get("estimate_cluster_mean") or {}).get("match", "n/a")
        lines.append(f"| {n} | {c['recorded_calls']}/{c['planned_calls']} | {k['satisfied']} | {k['violated']} | {k['unclear']} | {k['UNP']} | {k['SF']} | {est} |")
    with open(os.path.join(a.out, "RESULTS.md"), "xb") as f: f.write(("\n".join(lines) + "\n").encode("utf-8"))
    print("report written", a.out, res["status"]); return 0

if __name__ == "__main__":
    sys.exit(main())
