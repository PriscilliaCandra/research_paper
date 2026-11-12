"""Tests of the Ollama provider, the provider-dependent gate, the cost accounting and the named-FZ command line (decision records 12.63 and 12.64). FAKE server only:
urllib's urlopen is patched, so NO socket is opened and nothing is sent anywhere.
Run: python -B -I -W error::ResourceWarning -m unittest discover -s research_design/step10 -p test_ollama_provider.py"""
import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from fractions import Fraction as F
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import fz_index as FZI  # noqa: E402
import s10_integrity as IG  # noqa: E402
import s10_resume as RS  # noqa: E402
import s10_runner as RN  # noqa: E402
import s10_scoring as SC  # noqa: E402
import test_step10 as T10  # noqa: E402

RN.FZ_INDEX_ENFORCED = False  # enforcement is tested explicitly below
BUILD = T10.BUILD
FZ2 = os.path.join(RN.RD, "PARAMETER_FREEZE_MANIFEST_FZ_v2.json")
with open(FZ2, encoding="utf-8") as _f:
    FZ2_DOC = json.load(_f)
S = FZ2_DOC["register"]["row17_model_and_settings"]
SECRET = "SENTINEL_RESPONSE_TEXT_8c1e"


class Resp:
    def __init__(self, payload, status=200):
        self.status = status
        self._b = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def good_body(text=None):
    return {"model": "llama3.2:3b", "response": text if text is not None else f"Constraint: x\nVerdict: satisfied\nComments: {SECRET}", "done": True,
            "done_reason": "stop", "prompt_eval_count": 250, "eval_count": 60}


class Fake:
    """Scripted Ollama on the loopback interface."""

    def __init__(self, gen=None, version=S["ollama_version"], digest=S["model_digest"], template_sha=S["model_template_sha256"], models=("llama3.2:3b",), tpl=None):
        self.requests, self.gen, self.version, self.digest, self.models = [], gen or (lambda n, b: good_body()), version, digest, models
        self.template_sha, self.tpl, self.n = template_sha, tpl, 0

    def __call__(self, req, timeout=None):
        url, body = req.full_url, (json.loads(req.data.decode("utf-8")) if req.data else None)
        self.requests.append((req.get_method(), url, body, timeout))
        if url.endswith("/api/version"):
            return Resp({"version": self.version})
        if url.endswith("/api/tags"):
            return Resp({"models": [{"name": m, "model": m, "digest": self.digest} for m in self.models]})
        if url.endswith("/api/show"):
            return Resp({"template": self.tpl if self.tpl is not None else "TEMPLATE"})
        self.n += 1
        out = self.gen(self.n, body)
        if isinstance(out, tuple) and out[0] == "http":
            raise urllib.error.HTTPError(url, out[1], "err", None, io.BytesIO(b'{"error":"x"}'))
        if isinstance(out, tuple) and out[0] == "raise":
            raise out[1]
        return Resp(out)


def settings_with_template(tpl="TEMPLATE", **kw):
    s = dict(S)
    s["model_template_sha256"] = RN.sha(tpl.encode("utf-8"))
    s.update(kw)
    return s


class TestMapping(unittest.TestCase):
    def prov(self):
        return RN.OllamaProvider(S["model_id"], S["base_url"])

    def test_finish_reason_mapping_never_collides_with_block_names(self):
        f = RN.OllamaProvider.finish
        self.assertEqual((f("stop", True), f("length", True), f(None, True)), ("STOP", "MAX_TOKENS", None))
        self.assertEqual((f("other", True), f("load", True), f("a b!", True)), ("OLLAMA_OTHER", "OLLAMA_LOAD", "OLLAMA_AB"))
        self.assertEqual(f("stop", False), "STOP")                                 # a done=false answer still reports its reason; the harness counts the anomaly elsewhere
        self.assertEqual(f(None, False), "OLLAMA_NOT_DONE")
        self.assertNotIn(f("other", True), SC.BLOCK_REASONS)
        self.assertNotIn(f("other", True), SC.TOKEN_LIMIT_REASONS)

    def test_a_normal_answer_is_mapped_to_the_project_record_shape(self):
        fake = Fake()
        with mock.patch("urllib.request.urlopen", fake):
            r = self.prov().generate("PROMPT", S, "k,A,1")
        self.assertEqual((r["http_status"], r["error"], r["finish_reason"], r["model_returned"], r["block_reason"]), (200, None, "STOP", "llama3.2:3b", None))
        self.assertEqual(r["usage"], {"promptTokenCount": 250, "candidatesTokenCount": 60, "totalTokenCount": 310})
        self.assertIn(SECRET, r["text"])
        (m, url, body, timeout), = fake.requests
        self.assertEqual((m, url, timeout), ("POST", "http://127.0.0.1:11434/api/generate", 180))
        self.assertEqual(sorted(body), ["keep_alive", "model", "options", "prompt", "stream"])
        self.assertEqual((body["model"], body["stream"], body["keep_alive"], body["options"]), ("llama3.2:3b", False, "30m", {"num_ctx": 16384, "num_predict": 8192}))

    def test_failures_become_records_without_content(self):
        for name, out, status, err in (("http", ("http", 503), 503, "HTTP 503"), ("refused", ("raise", ConnectionRefusedError()), None, "ConnectionRefusedError"),
                                       ("timeout", ("raise", TimeoutError()), None, "TimeoutError")):
            with self.subTest(name), mock.patch("urllib.request.urlopen", Fake(lambda n, b, o=out: o)):
                r = self.prov().generate("PROMPT", S, "k")
            self.assertEqual((r["http_status"], r["error"], r["text"]), (status, err, ""))
        with mock.patch("urllib.request.urlopen", Fake(lambda n, b: b"not json")):
            r = self.prov().generate("PROMPT", S, "k")
        self.assertEqual((r["http_status"], r["error"]), (200, "InvalidJSON"))
        with mock.patch("urllib.request.urlopen", Fake(lambda n, b: b"[1, 2]")):
            self.assertEqual(self.prov().generate("PROMPT", S, "k")["error"], "InvalidJSON")

    def test_usage_is_never_invented(self):
        def go(**drop):
            body = good_body()
            for k in drop:
                body.pop(k)
            with mock.patch("urllib.request.urlopen", Fake(lambda n, b: body)):
                return self.prov().generate("P", S, "k")["usage"]
        self.assertEqual(go(prompt_eval_count=1), {"candidatesTokenCount": 60})
        self.assertEqual(go(eval_count=1), {"promptTokenCount": 250})
        body = dict(good_body(), prompt_eval_count=True, eval_count="9")
        with mock.patch("urllib.request.urlopen", Fake(lambda n, b: body)):
            self.assertIsNone(self.prov().generate("P", S, "k")["usage"])

    def test_non_loopback_urls_are_refused_before_any_request(self):
        for bad in ("http://example.com:11434", "https://127.0.0.1:11434", "http://127.0.0.1@evil.com", "http://192.168.1.2:11434", "http://127.0.0.1:11434/api", ""):
            with self.subTest(bad), mock.patch("urllib.request.urlopen", side_effect=AssertionError("request")):
                p = RN.OllamaProvider("llama3.2:3b", bad)
                self.assertTrue(p.preflight(S))
                self.assertEqual(p.generate("P", S, "k")["error"], "ValueError")

    def test_factory_and_prices(self):
        self.assertIsInstance(RN.make_provider(S), RN.OllamaProvider)
        self.assertIsInstance(RN.make_provider({"provider": "Gemini", "model_id": "gemini-3.5-flash-lite"}), RN.GeminiProvider)
        with self.assertRaises(ValueError):
            RN.make_provider({"provider": "Other"})
        self.assertEqual(RN.prices_for(S), (F(0), F(0)))
        self.assertEqual(RN.prices_for({"provider": "Gemini"}), (F(30, 100), F(250, 100)))
        self.assertEqual(RN.prices_for(None), (F(30, 100), F(250, 100)))              # legacy runs without a provider key keep the Gemini prices

    def test_the_source_of_the_provider_needs_no_credential(self):
        with open(os.path.join(HERE, "s10_runner.py"), encoding="utf-8") as f:
            src = f.read()
        cls = src.split("class OllamaProvider")[1].split("def make_provider")[0]
        for bad in ("os.environ", "getenv", "API_KEY", "x-goog", "import socket"):
            self.assertNotIn(bad, cls, bad)


class TestPreflight(unittest.TestCase):
    def prov(self, **kw):
        return RN.OllamaProvider(S["model_id"], S["base_url"])

    def check(self, fake, settings=None):
        with mock.patch("urllib.request.urlopen", fake):
            return self.prov().preflight(settings or settings_with_template())

    def test_matching_runtime_passes_and_starts_no_generation(self):
        fake = Fake()
        self.assertEqual(self.check(fake), [])
        self.assertTrue(all(not u.endswith("/api/generate") for _, u, _, _ in fake.requests))

    def test_each_mismatch_is_reported(self):
        self.assertTrue(any("version" in r for r in self.check(Fake(version="0.7.0"))))
        self.assertTrue(any("digest" in r for r in self.check(Fake(digest="0" * 64))))
        self.assertTrue(any("digest" in r for r in self.check(Fake(models=("other:1b",)))))
        self.assertTrue(any("template" in r for r in self.check(Fake(tpl="OTHER TEMPLATE"))))
        self.assertTrue(any("model ID" in r for r in self.check(Fake(), settings_with_template(model_id="llama3.2:1b"))))
        self.assertTrue(any("URL" in r for r in self.check(Fake(), settings_with_template(base_url="http://localhost:11434"))))
        self.assertTrue(any("not Ollama" in r for r in self.check(Fake(), settings_with_template(provider="Gemini"))))

    def test_server_not_running_is_a_reason_not_a_crash(self):
        def refuse(req, timeout=None):
            raise ConnectionRefusedError()
        why = self.check(refuse)
        self.assertTrue(any("could not be verified (ConnectionRefusedError)" in r for r in why), why)


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ol_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bh = RN.sha(RN.file_bytes(os.path.join(BUILD, "manifest.json")))
        for p in (mock.patch.object(RN, "FZ_PATH", FZ2),):
            p.start()
            self.addCleanup(p.stop)

    def fz_copy(self, mutate):
        d = copy.deepcopy(FZ2_DOC)
        mutate(d["register"]["row17_model_and_settings"])
        p = os.path.join(self.tmp, "fz_mut.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
        return p


class TestGate(Gate):
    n = 0

    def gate(self, **kw):
        TestGate.n += 1
        ev, au = T10.TestRunner.files(self, "pilot_round_1", days=8 + TestGate.n, calls=18)
        return RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=False, **kw)

    def test_the_gate_accepts_the_frozen_ollama_register(self):
        m = self.gate()
        self.assertEqual((m["provider"], m["model_id"], m["num_ctx"]), ("Ollama", "llama3.2:3b", 16384))

    def test_any_deviation_of_the_register_from_the_code_constants_closes_the_gate(self):
        for name, mut in {"digest": lambda r: r.update(model_digest="0" * 64), "version": lambda r: r.update(ollama_version="0.7.0"), "num_ctx": lambda r: r.update(num_ctx=8192),
                          "model": lambda r: r.update(model_id="llama3.2:1b"), "url": lambda r: r.update(base_url="http://localhost:11434"), "retries": lambda r: r.update(retries=1),
                          "template": lambda r: r.update(model_template_sha256="0" * 64), "keep_alive": lambda r: r.update(keep_alive="5m")}.items():
            with self.subTest(name), mock.patch.object(RN, "FZ_PATH", self.fz_copy(mut)):
                with self.assertRaises(RN.GateClosed) as cm:
                    self.gate()
                self.assertTrue(any("differ" in r for r in cm.exception.reasons), cm.exception.reasons)

    def test_an_unknown_provider_closes_the_gate(self):
        with mock.patch.object(RN, "FZ_PATH", self.fz_copy(lambda r: r.update(provider="Other"))):
            with self.assertRaises(RN.GateClosed) as cm:
                self.gate()
        self.assertTrue(any("not supported" in r for r in cm.exception.reasons))

    def test_cost_is_zero_for_the_local_provider_but_the_ceiling_still_has_to_be_stated(self):
        ev, au = T10.TestRunner.files(self, "pilot_round_1", days=9, calls=18)
        with open(au, encoding="utf-8") as f:
            a = json.load(f)
        a["cost_ceiling_usd"] = 50
        bad = os.path.join(self.tmp, "au_bad.json")
        with open(bad, "w") as f:
            json.dump(a, f)
        with self.assertRaises(RN.GateClosed) as cm:
            RN.check_gates("pilot_round_1", BUILD, ev, bad, verify_build=False)
        self.assertTrue(any("ceiling" in r for r in cm.exception.reasons))

    def test_gemini_register_still_works_with_its_own_constants(self):
        with mock.patch.object(RN, "FZ_PATH", os.path.join(RN.RD, "PARAMETER_FREEZE_MANIFEST_FZ_v1.json")):
            ev, au = T10.TestRunner.files(self, "pilot_round_1", days=9, calls=18)
            self.assertEqual(RN.check_gates("pilot_round_1", BUILD, ev, au, verify_build=False)["provider"], "Gemini")


class TestRunUnderFZv2(Gate):
    def test_a_mock_run_records_zero_api_cost_and_passes_the_integrity_check(self):
        ev, au = T10.TestRunner.files(self, "pilot_round_1", days=9, calls=18)

        def fn(key, prompt):
            r = T10.good(key, prompt)
            return dict(r, model_returned="llama3.2:3b", usage={"promptTokenCount": 250, "candidatesTokenCount": 60, "totalTokenCount": 310})
        out = os.path.join(self.tmp, "run")
        with contextlib.redirect_stdout(io.StringIO()):
            s = RN.run("pilot_round_1", BUILD, out, RN.MockProvider(fn), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        self.assertEqual((s["status"], s["recorded_calls"], s["settings"]["provider"]), ("RUN_COMPLETE", 18, "Ollama"))
        self.assertEqual((s["observed_cost_usd_lower_bound"], s["observed_cost_usd_conservative"]), (0.0, 0.0))
        P, st = IG.verify_run(out, BUILD)
        self.assertEqual(P, [])
        self.assertEqual(st["observed_cost_usd_at_recorded_prices"], 0.0)

    def test_a_gemini_model_id_returned_by_a_local_run_triggers_the_safe_stop(self):
        ev, au = T10.TestRunner.files(self, "pilot_round_1", days=9, calls=18)
        out = os.path.join(self.tmp, "run2")
        with contextlib.redirect_stdout(io.StringIO()):
            s = RN.run("pilot_round_1", BUILD, out, RN.MockProvider(T10.good), evidence_path=ev, auth_path=au, sleeper=lambda x: None, verify_build=False)
        self.assertEqual((s["status"], s["recorded_calls"]), ("RUN_INCOMPLETE", 1))
        self.assertIn("provider anomaly", s["halt"])

    def test_the_cost_state_uses_the_prices_of_the_provider(self):
        st = RN.RunState(F(0), (F(0), F(0)))
        st.tokens_in, st.tokens_out = 10 ** 6, 10 ** 6
        self.assertEqual(st.observed_cost_usd(), 0)
        st2 = RN.RunState()
        st2.tokens_in, st2.tokens_out = 10 ** 6, 10 ** 6
        self.assertEqual(st2.observed_cost_usd(), F(30, 100) + F(250, 100))


class TestNamedFZ(Gate):
    """--fz-manifest is required when enforcement is on, and the named manifest must be listed in the FZ index."""

    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "root")
        os.makedirs(os.path.join(self.root, "research_design"))
        self.fzrel = "research_design/PARAMETER_FREEZE_MANIFEST_FZ_v2.json"
        self.fzcopy = os.path.join(self.root, *self.fzrel.split("/"))
        shutil.copyfile(FZ2, self.fzcopy)
        self.ev, self.au = T10.TestRunner.files(self, "pilot_round_1", days=9, calls=18)
        self.verifiers = [mock.patch.object(RN.s8_verify, "verify", lambda b: []), mock.patch.object(RN.impl_b, "verify", lambda b: {"failures": []}),
                          mock.patch.object(RN, "REPO_ROOT", self.root), mock.patch.object(RN, "FZ_INDEX_ENFORCED", True)]
        for p in self.verifiers:
            p.start()
            self.addCleanup(p.stop)

    def index(self, rels):
        lst = os.path.join(self.tmp, "list.txt")
        with open(lst, "wb") as f:
            f.write(("\n".join(rels) + "\n").encode("utf-8"))
        idx = os.path.join(self.tmp, "idx.json")
        h = FZI.write_index(self.root, lst, idx)
        return idx, h

    def argv(self, idx, h, with_manifest=True):
        a = ["--scope", "pilot_round_1", "--build", BUILD, "--out", os.path.join(self.tmp, "o"), "--evidence", self.ev, "--authorization", self.au, "--fz-index", idx, "--fz-hash", h]
        return a + (["--fz-manifest", self.fzcopy] if with_manifest else [])

    def main(self, argv, fake=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch("urllib.request.urlopen", fake or Fake(tpl="TEMPLATE")), mock.patch.object(RN, "FZ_PATH", RN.FZ_PATH):
            code = RN.main(argv)
        return code, buf.getvalue()

    def test_the_manifest_must_be_named_when_enforcement_is_on(self):
        idx, h = self.index([self.fzrel])
        code, text = self.main(self.argv(idx, h, with_manifest=False))
        self.assertEqual(code, 2)
        self.assertIn("--fz-manifest is required", text)

    def test_the_manifest_must_be_listed_in_the_index(self):
        other = os.path.join(self.root, "research_design", "other.txt")
        with open(other, "w") as f:
            f.write("x")
        idx, h = self.index(["research_design/other.txt"])
        code, text = self.main(self.argv(idx, h))
        self.assertEqual(code, 2)
        self.assertIn("is not listed in the FZ index", text)

    def test_a_listed_manifest_opens_the_gate_and_the_loopback_preflight_runs_without_generation(self):
        fake = Fake(tpl="TEMPLATE")
        with mock.patch.object(RN, "OLLAMA_EXPECTED", dict(RN.OLLAMA_EXPECTED, model_template_sha256=RN.sha(b"TEMPLATE"))):
            with mock.patch.object(RN, "EXPECTED_BY_PROVIDER", {"Gemini": RN.EXPECTED_SETTINGS, "Ollama": dict(RN.OLLAMA_EXPECTED, model_template_sha256=RN.sha(b"TEMPLATE"))}):
                fz_doc = copy.deepcopy(FZ2_DOC)
                fz_doc["register"]["row17_model_and_settings"]["model_template_sha256"] = RN.sha(b"TEMPLATE")
                with open(self.fzcopy, "w", encoding="utf-8") as f:
                    json.dump(fz_doc, f)
                idx, h = self.index([self.fzrel])
                code, text = self.main(self.argv(idx, h), fake)
        self.assertEqual(code, 0, text)
        self.assertIn("gates open; --live not given: nothing was run", text)
        self.assertTrue(all(not u.endswith("/api/generate") for _, u, _, _ in fake.requests))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "o")))

    def test_resume_cli_has_the_same_requirement(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = RS.main(["--run", self.tmp, "--build", BUILD, "--evidence", self.ev, "--authorization", self.au, "--resume-authorization", self.au])
        self.assertEqual(code, 2)
        self.assertIn("--fz-manifest is required", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
