# Hybrid running training recommendation system (code)

Code that accompanies the paper *Evaluating Deterministic Constraints and LLM-Based Personalization in a Hybrid Running Training Recommendation System* (Bina Nusantara University).

A deterministic rule engine computes every number of a multi-week running plan (a VDOT score from a 5K time, training pace zones, weekly volume, long run, quality session, cutback weeks, taper). An LLM may only personalize the explanatory text, and a validation module in the backend rejects any output in which a number, a day, or a workout type has changed; a deterministic fallback then returns the template-based plan.

This is a research prototype. It is not a medical device, and nothing here shows that its plans are safe or effective for real runners.

## Contents

| Folder | What it contains |
|---|---|
| `backend/` | FastAPI application: rule engine (`app/rules/`), LLM personalization and validation (`app/ai/`), authentication, workouts, SQLite models, Alembic migrations, and the pytest suite (`tests/`). |
| `frontend/` | React (Vite) web interface. |
| `research_design/` | Scripts of the constraint-detection experiment: prompt-specification and build generation, gates, runner, scoring, analysis, resume and integrity checks (`step7` to `step10`), and two independent implementations of the scenario-construction gates (`implementation_a/` and `step9/impl_b.py`). |
| `paper/evaluation_layer_A/` | Scripts of the evaluation of the deterministic layer (layers A, B1, B2, C) and the 30 synthetic runner profiles (`profiles_30.json`). |
| `transfer_qwen/` | Launcher used to run the second model on another computer. |

## Backend

```
cd backend
pip install -r requirements.txt
cp .env.example .env        # optional: LLM provider settings
alembic upgrade head
pytest
python -m uvicorn app.main:app --reload --port 8000
```

The test suite runs against a temporary SQLite database and makes no live LLM calls.

## Frontend

```
cd frontend
npm install
npm run dev
```

## Evaluation scripts

The scripts in `paper/evaluation_layer_A/` import the rule engine from `backend/` and write their results to new CSV files next to them; they refuse to overwrite an existing file. For example, the audit of the engine's session allocation:

```
cd paper/evaluation_layer_A
python layerB_engine_audit_v2.py
```

## Not included

The experiment scripts in `research_design/` and `transfer_qwen/` need the frozen build outputs, parameter manifests, decision records, and model files of the original study. Those data (raw model outputs, run folders, decision records, drafts of the paper) are not part of this repository, so these scripts are provided for inspection and are not runnable as they are. The local models were run with Ollama (`llama3.2:3b` and `qwen2.5:3b`).

## License

No license has been chosen yet; all rights are reserved by the authors until one is added.
