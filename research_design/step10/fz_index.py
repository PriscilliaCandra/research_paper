"""INACTIVE DRAFT (patch P5, MD-04): NOT part of the harness, not imported, not run by any script. To activate: copy to research_design/step10/fz_index.py ONLY after
MD-04 is adopted with the file list; the list is an input of the tool, never stored in it.

Writes the FZ index: canonical JSON {relative path -> SHA-256 of the file bytes} (sorted keys, compact separators, ASCII, one final LF). The FZ hash is the SHA-256 of
that file. The tool creates a NEW file only (never overwrites), refuses unsafe or duplicate paths, refuses a list that contains the output file (no circularity),
and can verify a stored index against the files on disk. It reads no network, writes nothing else, and prints only paths and hashes.
Usage: python -B -I fz_index.py write  --root <dir> --list <text file, one relative path per line> --out <new index file>
       python -B -I fz_index.py verify --root <dir> --list <text file> --index <stored index file> [--expect <64 hex>]"""
import argparse
import hashlib
import json
import os
import sys

sys.dont_write_bytecode = True


class IndexError_(Exception):
    pass


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def read_list(path):
    with open(path, "rb") as f:
        lines = f.read().decode("utf-8").split("\n")
    out = [l.strip("\r") for l in lines if l.strip("\r").strip()]
    return out


def check_paths(paths, out_rel=None):
    seen = set()
    for p in paths:
        if os.path.isabs(p) or p.startswith(("/", "\\")) or ":" in p:
            raise IndexError_(f"path is not relative: {p}")
        parts = p.replace("\\", "/").split("/")
        if ".." in parts or "." in parts or "" in parts:
            raise IndexError_(f"unsafe path: {p}")
        key = "/".join(parts)
        if key in seen:
            raise IndexError_(f"duplicate path: {p}")
        seen.add(key)
        if out_rel is not None and key == out_rel:
            raise IndexError_("the output file is listed in its own index")
    return sorted(seen)


def canonical_index(root, paths):
    entries = {}
    for p in check_paths(paths):
        full = os.path.join(root, *p.split("/"))
        if not os.path.isfile(full):
            raise IndexError_(f"listed file does not exist: {p}")
        with open(full, "rb") as f:
            entries[p] = sha256_bytes(f.read())
    return (json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def write_index(root, list_path, out_path):
    root_abs = os.path.abspath(root)
    out_abs = os.path.abspath(out_path)
    out_rel = os.path.relpath(out_abs, root_abs).replace("\\", "/") if out_abs.startswith(root_abs + os.sep) else None
    paths = read_list(list_path)
    check_paths(paths, out_rel)
    data = canonical_index(root, paths)
    with open(out_path, "xb") as f:                     # FileExistsError if it exists: never overwrites
        f.write(data)
    return sha256_bytes(data)


def verify_index(root, list_path, index_path, expect=None):
    with open(index_path, "rb") as f:
        stored = f.read()
    now = canonical_index(root, read_list(list_path))
    ok = now == stored and (expect is None or sha256_bytes(stored) == expect)
    return ok, sha256_bytes(stored), sha256_bytes(now)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("--root", required=True)
    w.add_argument("--list", required=True)
    w.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--root", required=True)
    v.add_argument("--list", required=True)
    v.add_argument("--index", required=True)
    v.add_argument("--expect")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "write":
            print("FZ hash", write_index(a.root, a.list, a.out))
            return 0
        ok, stored, now = verify_index(a.root, a.list, a.index, a.expect)
        print("stored", stored, "recomputed", now)
        print("FZ INDEX MATCHES" if ok else "FZ INDEX MISMATCH")
        return 0 if ok else 1
    except (IndexError_, OSError) as e:
        print("refused:", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
