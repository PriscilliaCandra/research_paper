"""Acceptance tests for the O4 read-only pilot usage reader. SYNTHETIC data only, in temporary folders: no real pilot, build, prompt or response is read; no network.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_o4_usage_reader.py"""
import builtins
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import o4_usage_reader as O4  # noqa: E402

SECRET_PROMPT = "SENTINEL_PROMPT_TEXT_9f3a"
SECRET_RESPONSE = "SENTINEL_RESPONSE_TEXT_77c1"
SECRET_KEY = "SENTINEL_API_KEY_a1b2"


def wj(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="o4_")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.build = os.path.join(self.d, "build")
        self.run = os.path.join(self.d, "run")
        os.makedirs(os.path.join(self.build, "pilot", "prompts"))
        os.makedirs(os.path.join(self.run, "raw"))
        with open(os.path.join(self.build, "manifest.json"), "wb") as f:
            f.write(b'{"synthetic": true}\n')
        self.mhash = hashlib.sha256(b'{"synthetic": true}\n').hexdigest()
        self.table, self.recs = [], []
        for i in range(1, 19):
            tid, cond = f"T-{(i + 1) // 2:03d}", "AB"[i % 2]
            chars = 1016 if i <= 6 else 500 + i
            sha = hashlib.sha256(f"p{i}".encode()).hexdigest()
            self.table.append({"template_id": tid, "condition": cond, "sha256": sha, "chars": chars})
            self.recs.append({"call_index": i, "call_key": f"{tid},{cond},1", "template_id": tid, "condition": cond, "prompt_sha256": sha,
                              "usage": {"promptTokenCount": int(chars * 0.9), "totalTokenCount": 2000}, "response_text": SECRET_RESPONSE})
        with open(os.path.join(self.build, "pilot", "prompts", "T-001-A.txt"), "w") as f:
            f.write(SECRET_PROMPT)
        with open(os.path.join(self.run, "raw", "00001.json"), "w") as f:
            f.write(SECRET_RESPONSE + SECRET_KEY)
        self.marker = "RUN_COMPLETE"
        self.summary = {"scope": "pilot_round_1", "status": "RUN_COMPLETE", "planned_calls": 18, "recorded_calls": 18, "build_manifest_sha256": self.mhash}

    def write(self):
        wj(os.path.join(self.build, "pilot", "prompt_table.json"), self.table)
        with open(os.path.join(self.run, "calls.jsonl"), "w", encoding="utf-8") as f:
            for r in self.recs:
                f.write(json.dumps(r) + "\n")
        for m in ("RUN_COMPLETE", "RUN_INCOMPLETE"):
            p = os.path.join(self.run, m)
            if os.path.exists(p):
                os.remove(p)
        wj(os.path.join(self.run, self.marker), self.summary)

    def go(self, assumption=1016):
        self.write()
        rows, problems, B = O4.analyse(self.run, self.build, assumption)
        return O4.status_of(problems, B, assumption), problems, B, rows

    def cli(self, *extra):
        self.write()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = O4.main(["--run", self.run, "--build", self.build, *extra])
        return code, out.getvalue() + err.getvalue()

    def set_ratio(self, tokens, chars):
        for r, t in zip(self.recs, self.table):
            t["chars"] = chars
            r["usage"]["promptTokenCount"] = tokens


class TestStatuses(Fixture):
    def test_supported_when_all_valid_and_B_not_above_the_assumption(self):
        st, problems, B, rows = self.go()
        self.assertEqual((st, problems), (O4.SUPPORTED, []))
        self.assertLessEqual(B, 1016)
        self.assertEqual(sum(r["ok"] for r in rows), 18)

    def test_boundary_B_equal_to_the_assumption_is_supported(self):
        self.set_ratio(1016, 1016)                                           # ratio exactly 1 -> B = 1016
        st, _, B, _ = self.go()
        self.assertEqual((st, B), (O4.SUPPORTED, 1016))

    def test_one_token_above_the_boundary_is_not_supported(self):
        self.set_ratio(1017, 1016)                                           # ratio x 1016 = 1017 exactly
        st, _, B, _ = self.go()
        self.assertEqual((st, B), (O4.NOT_SUPPORTED, 1017))

    def test_ceiling_rounding_of_B(self):
        self.set_ratio(7, 9)                                                 # 7/9 x 1016 = 790.2 -> 791
        self.assertEqual(self.go()[2], 791)
        self.set_ratio(1, 3)                                                 # 1016/3 = 338.67 -> 339
        self.assertEqual(self.go()[2], 339)
        self.set_ratio(508, 1016)                                            # exactly 508 -> 508 (no spurious +1)
        self.assertEqual(self.go()[2], 508)

    def test_B_uses_the_maximum_ratio(self):
        self.recs[3]["usage"]["promptTokenCount"] = 2 * self.table[3]["chars"]
        st, _, B, rows = self.go()
        self.assertEqual((st, B), (O4.NOT_SUPPORTED, 2032))
        self.assertEqual(max(r["ratio"] for r in rows), 2)

    def test_a_different_assumption_is_compared(self):
        self.assertEqual(self.go(assumption=100)[0], O4.NOT_SUPPORTED)


class TestInconclusive(Fixture):
    def inconclusive(self, needle):
        st, problems, B, _ = self.go()
        self.assertEqual((st, B), (O4.INCONCLUSIVE, None))
        self.assertTrue(any(needle in p for p in problems), problems)

    def test_missing_or_invalid_prompt_token_count(self):
        for name, usage in {"absent": None, "empty": {}, "zero": {"promptTokenCount": 0}, "negative": {"promptTokenCount": -3}, "string": {"promptTokenCount": "900"},
                            "bool": {"promptTokenCount": True}, "float": {"promptTokenCount": 9.5}, "not a dict": [900]}.items():
            with self.subTest(name):
                self.setUp()
                self.recs[2]["usage"] = usage
                self.inconclusive("promptTokenCount missing or invalid")

    def test_transport_failure_record_without_usage(self):
        self.recs[4] = dict(self.recs[4], usage=None, error="TimeoutError", http_status=None)
        self.inconclusive("promptTokenCount missing or invalid")

    def test_mapping_ambiguous_missing_or_hash_mismatch(self):
        self.table.append(dict(self.table[0]))                               # duplicate table entry
        self.inconclusive("mapping ambiguous or missing")
        self.setUp()
        del self.table[5]
        self.inconclusive("mapping ambiguous or missing")
        self.setUp()
        self.recs[1]["prompt_sha256"] = "0" * 64
        self.inconclusive("differs from the prompt table")

    def test_duplicate_or_missing_call_index_and_count_mismatch(self):
        self.recs[1]["call_index"] = self.recs[0]["call_index"]
        self.inconclusive("call_index missing or duplicate")
        self.setUp()
        del self.recs[17]
        self.inconclusive("number of records differs")

    def test_prompt_over_the_character_limit_is_never_a_success(self):
        self.table[7]["chars"] = 1017
        st, problems, B, _ = self.go()
        self.assertEqual((st, B), (O4.INCONCLUSIVE, None))
        self.assertTrue(any(p.startswith("LIMIT:") for p in problems))

    def test_invalid_characters_in_the_table(self):
        for bad in (0, -5, "1016", None, True):
            with self.subTest(bad=bad):
                self.setUp()
                self.table[2]["chars"] = bad
                self.inconclusive("characters missing or invalid")

    def test_run_prerequisites(self):
        self.marker, self.summary["status"] = "RUN_INCOMPLETE", "RUN_INCOMPLETE"
        self.inconclusive("RUN_INCOMPLETE")
        self.setUp()
        self.summary["scope"] = "main"
        self.inconclusive("not a pilot round")
        self.setUp()
        self.summary["build_manifest_sha256"] = "f" * 64
        self.inconclusive("another build")
        self.setUp()
        self.write()
        wj(os.path.join(self.run, "RUN_INCOMPLETE"), self.summary)           # two markers
        rows, problems, B = O4.analyse(self.run, self.build)
        self.assertEqual(B, None)
        self.assertTrue(any("single status marker" in p for p in problems))

    def test_unreadable_inputs_close_without_crashing(self):
        self.write()
        os.remove(os.path.join(self.build, "pilot", "prompt_table.json"))
        self.assertEqual(O4.status_of(*O4.analyse(self.run, self.build)[1:], 1016), O4.INCONCLUSIVE)
        self.write()
        with open(os.path.join(self.run, "calls.jsonl"), "ab") as f:
            f.write(b"not json at all\n")
        rows, problems, B = O4.analyse(self.run, self.build)
        self.assertEqual(B, None)
        self.assertTrue(any("not valid JSON" in p for p in problems))
        self.assertEqual(O4.analyse(os.path.join(self.d, "nope"), self.build)[1:], (["PREREQUISITE: the run folder has no single status marker"], None))


class TestOutputAndReadOnly(Fixture):
    def test_exit_codes_and_status_line(self):
        self.assertEqual(self.cli()[0], 0)
        self.set_ratio(2000, 1000)
        code, text = self.cli()
        self.assertEqual(code, 1)
        self.assertIn("STATUS: NOT_SUPPORTED_REQUIRES_OWNER_REVIEW", text)
        self.table[0]["chars"] = 5000
        code, text = self.cli()
        self.assertEqual(code, 2)
        self.assertIn("STATUS: INCONCLUSIVE", text)
        self.assertNotIn("SUPPORTED_FOR_PILOT_TEMPLATES", text)

    def test_output_never_contains_prompt_response_key_call_key_or_raw_json(self):
        self.recs[3]["usage"] = None                                         # an INCONCLUSIVE output also must not leak
        self.recs[5]["extra_secret"] = SECRET_KEY
        for _ in range(2):
            code, text = self.cli()
            for s in (SECRET_PROMPT, SECRET_RESPONSE, SECRET_KEY, "response_text", "call_key", "usageMetadata", "{", "}", "T-001,A", "AB"):
                self.assertNotIn(s, text, s)
            self.setUp()
            self.recs[1]["usage"] = {"promptTokenCount": 800}
        self.assertIn("call  prompt8", text)

    def test_only_the_four_allowed_files_are_opened_and_none_for_writing(self):
        opened = []
        real = builtins.open

        def spy(file, mode="r", *a, **k):
            opened.append((os.path.relpath(file, self.d) if isinstance(file, str) else str(file), mode))
            return real(file, mode, *a, **k)
        self.write()
        with mock.patch("builtins.open", spy), contextlib.redirect_stdout(io.StringIO()):
            O4.main(["--run", self.run, "--build", self.build])
        names = sorted(set(p.replace("\\", "/") for p, _ in opened))
        self.assertEqual(names, ["build/manifest.json", "build/pilot/prompt_table.json", "run/RUN_COMPLETE", "run/calls.jsonl"])
        self.assertTrue(all("w" not in m and "a" not in m and "x" not in m and "+" not in m for _, m in opened), opened)

    def test_nothing_is_created_or_changed(self):
        self.write()

        def snapshot():
            out = {}
            for dp, _, fs in os.walk(self.d):
                for f in fs:
                    p = os.path.join(dp, f)
                    with open(p, "rb") as h:
                        out[os.path.relpath(p, self.d)] = hashlib.sha256(h.read()).hexdigest()
            return out
        before = snapshot()
        with contextlib.redirect_stdout(io.StringIO()):
            O4.main(["--run", self.run, "--build", self.build])
        self.assertEqual(snapshot(), before)

    def test_no_network_and_no_environment_credentials_are_used(self):
        import socket
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network")), mock.patch.dict(os.environ, {"GEMINI_API_KEY": SECRET_KEY}):
            code, text = self.cli()
        self.assertEqual(code, 0)
        self.assertNotIn(SECRET_KEY, text)
        with open(os.path.join(HERE, "o4_usage_reader.py"), encoding="utf-8") as f:
            src = f.read()
        for word in ("urllib", "socket", "requests", "environ", "getenv"):
            self.assertNotIn(word, src, word)


if __name__ == "__main__":
    unittest.main()
