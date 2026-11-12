"""Command-line wrapper for s12_coding (post-run coding preparation and tabulation). Adds no rule: it calls prepare_coding / read_sheet / tabulate /
recode_agreement unchanged and writes new files only (refuses to overwrite). Decision record 12.35. NOT RUN on real data.
  prepare : python -B -I s12_cli.py prepare --run <main run> --build <build> --analysis <folder with analysis.json> --out <new folder>
  tabulate: python -B -I s12_cli.py tabulate --prepared <coding folder> --run <main run> --sheet <filled first-pass csv> --out <new json>
  recode  : python -B -I s12_cli.py recode --prepared <coding folder> --first <first-pass csv> --second <re-code csv> --out <new json>
Exit codes: 0 done, 2 coding blocked (PG11b application failed) or invalid input, 3 incomplete coding. Prints no response text."""
import argparse
import csv
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import s12_coding as CD  # noqa: E402


def _write(path, obj):
    with open(path, "xb") as f:
        f.write(CD.cj(obj))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="s12_cli.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    for k in ("run", "build", "analysis", "out"):
        p.add_argument(f"--{k}", required=True)
    t = sub.add_parser("tabulate")
    for k in ("prepared", "run", "sheet", "out"):
        t.add_argument(f"--{k}", required=True)
    r = sub.add_parser("recode")
    for k in ("prepared", "first", "second", "out"):
        r.add_argument(f"--{k}", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "prepare":
            res = CD.prepare_coding(a.run, a.build, a.analysis, a.out)
            print(res["status"], res.get("reason", ""))
            return 0 if res["status"] == "READY" else 2
        if a.cmd == "tabulate":
            codes = CD.read_sheet(a.sheet, "coder1")
            _write(a.out, CD.tabulate(a.prepared, a.run, codes))
            print("written", a.out)
            return 0
        first = CD.read_sheet(a.first, "coder1")
        second = CD.read_sheet(a.second, "recode1")
        with open(os.path.join(a.prepared, "recode_selection_and_order.csv"), "rb") as f:
            ids = [row["opaque_id"] for row in csv.DictReader(io.StringIO(f.read().decode("utf-8")))]
        _write(a.out, CD.recode_agreement(first, second, ids))
        print("written", a.out)
        return 0
    except ValueError as e:
        print("refused:", e)
        return 3 if "no code" in str(e) else 2


if __name__ == "__main__":
    sys.exit(main())
