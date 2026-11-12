"""Step 8 build for FZ-v3 (second small-LLM arm, qwen2.5:3b) -- owner decisions D13-D18 of 2026-10-26.

A thin wrapper: it registers the FZ-v3 entry in s8_build.FZ_VERSIONS and runs s8_build.main() unchanged, so that s8_build.py itself (a member of the
FZ-v2 file list) stays byte-identical. Only register item 17 differs from FZ-v2 (model ID, digest, template hash, Ollama version, timeout_s 600).

Usage: python -B -I research_design/step8/s8_build_fz_v3.py --out research_design/step8_output_fz_v3 --fz-version v3
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import s8_build as B  # noqa: E402

B.FZ_VERSIONS["v3"] = {
    "label": "FZ-v3", "file": "PARAMETER_FREEZE_MANIFEST_FZ_v3.json",
    "register_sha": "4dedc5125ef8a03b9596121019f21c36c32cf10b52162bd1d2572700de9e1efe",
    "freeze_manifest": "STEP7_PROMPT_SPECIFICATION_FREEZE_MANIFEST_v3.json", "freeze_key": "fz_register",
    "settings": ("qwen2.5:3b", "not applicable", 8192, 0, 600, 1, 1),
}

if __name__ == "__main__":
    sys.exit(B.main())
