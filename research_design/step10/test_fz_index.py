"""INACTIVE DRAFT acceptance tests for P5 (fz_index.py). Synthetic fixtures in a temporary folder; no repository file is read. To activate together with the tool.
Run (after activation): python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_fz_index.py"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import fz_index as FZ  # noqa: E402


def sha(b):
    return hashlib.sha256(b).hexdigest()


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="fz_")
        self.addCleanup(shutil.rmtree, self.d, True)
        os.makedirs(os.path.join(self.d, "a"))
        self.files = {"a/x.txt": b"alpha\n", "b.json": b"{}\n", "a/y.bin": bytes(range(8))}
        for p, b in self.files.items():
            with open(os.path.join(self.d, *p.split("/")), "wb") as f:
                f.write(b)
        self.lst = os.path.join(self.d, "list.txt")
        self.write_list(["b.json", "a/x.txt", "a/y.bin"])

    def write_list(self, paths):
        with open(self.lst, "wb") as f:
            f.write(("\n".join(paths) + "\n").encode())

    def test_hash_equals_hand_computation(self):
        out = os.path.join(self.d, "out", "idx.json")
        os.makedirs(os.path.dirname(out))
        h = FZ.write_index(self.d, self.lst, out)
        hand = '{"a/x.txt":"%s","a/y.bin":"%s","b.json":"%s"}\n' % (sha(self.files["a/x.txt"]), sha(self.files["a/y.bin"]), sha(self.files["b.json"]))
        with open(out, "rb") as f:
            self.assertEqual(f.read(), hand.encode())
        self.assertEqual(h, sha(hand.encode()))

    def test_order_of_the_list_does_not_matter(self):
        o1, o2 = os.path.join(self.d, "i1.json"), os.path.join(self.d, "i2.json")
        h1 = FZ.write_index(self.d, self.lst, o1)
        self.write_list(["a/y.bin", "b.json", "a/x.txt"])
        self.assertEqual(h1, FZ.write_index(self.d, self.lst, o2))

    def test_one_byte_change_changes_the_hash_and_verify_fails(self):
        o = os.path.join(self.d, "i.json")
        h = FZ.write_index(self.d, self.lst, o)
        self.assertTrue(FZ.verify_index(self.d, self.lst, o, h)[0])
        with open(os.path.join(self.d, "a", "x.txt"), "ab") as f:
            f.write(b"!")
        ok, stored, now = FZ.verify_index(self.d, self.lst, o, h)
        self.assertFalse(ok)
        self.assertNotEqual(stored, now)

    def test_never_overwrites(self):
        o = os.path.join(self.d, "i.json")
        FZ.write_index(self.d, self.lst, o)
        with self.assertRaises(FileExistsError):
            FZ.write_index(self.d, self.lst, o)

    def test_refuses_missing_duplicate_unsafe_and_self_listed_paths(self):
        for bad in (["nope.txt"], ["b.json", "b.json"], ["../x"], ["/abs"], ["C:/x"], ["a//x.txt"], ["./b.json"]):
            self.write_list(bad)
            with self.assertRaises(FZ.IndexError_, msg=str(bad)):
                FZ.write_index(self.d, self.lst, os.path.join(self.d, "z.json"))
        self.write_list(["b.json", "self_index.json"])
        open(os.path.join(self.d, "self_index.json"), "wb").close()
        with self.assertRaises(FZ.IndexError_):
            FZ.write_index(self.d, self.lst, os.path.join(self.d, "self_index.json"))

    def test_verify_with_wrong_expected_hash_fails(self):
        o = os.path.join(self.d, "i.json")
        FZ.write_index(self.d, self.lst, o)
        self.assertFalse(FZ.verify_index(self.d, self.lst, o, "0" * 64)[0])

    def test_cli_exit_codes(self):
        o = os.path.join(self.d, "i.json")
        self.assertEqual(FZ.main(["write", "--root", self.d, "--list", self.lst, "--out", o]), 0)
        self.assertEqual(FZ.main(["verify", "--root", self.d, "--list", self.lst, "--index", o]), 0)
        self.assertEqual(FZ.main(["write", "--root", self.d, "--list", self.lst, "--out", o]), 2)      # exists: refused, not overwritten


if __name__ == "__main__":
    unittest.main()
