import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Star, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react';
import Button from '../components/ui/Button';
import { fetchBlindPlans, submitBlindEvaluation } from '../api';
import { LIKERT_CRITERIA } from '../constants/likertCriteria';

// Blinded rating page for the RQ3 instrument. It shows two plans only as "Plan A" and "Plan B"; the server
// decides which condition each label stands for and never sends that mapping to this page. This page makes no
// statement about how the plans were produced, and it does not use the shared disclaimer banner because that
// banner's text names the generation method.
//
// Known limits (not solved here): the two plans differ in content, so a participant may still guess the source;
// who receives the link, how many plans each participant rates and in what order are study-procedure decisions
// that are not made by this page.

const STORAGE_KEY = 'rq3_participant_code';
const EMPTY_SCORES = {
  personalization_score: null,
  usefulness_score: null,
  clarity_score: null,
  confidence_score: null,
};

function newParticipantCode() {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  return 'P-' + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('').toUpperCase();
}

// Anonymous code: random, no free text, kept only for this browser session so one participant's ratings can be paired.
function loadParticipantCode() {
  try {
    const saved = window.sessionStorage.getItem(STORAGE_KEY);
    if (saved && /^P-[0-9A-F]{8}$/.test(saved)) return saved;
  } catch {
    // sessionStorage unavailable: fall through to a fresh in-memory code.
  }
  const code = newParticipantCode();
  try {
    window.sessionStorage.setItem(STORAGE_KEY, code);
  } catch {
    // Not persisted; the code then only lives for this page view.
  }
  return code;
}

function PlanView({ plan, weekIndex, onWeekChange }) {
  const week = plan.weeks[weekIndex] || plan.weeks[0];
  return (
    <div className="space-y-4">
      {plan.overview && (
        <div className="p-3 rounded-lg bg-slate-50 border border-slate-200 text-xs text-slate-700 leading-relaxed">{plan.overview}</div>
      )}
      <div className="flex items-center gap-2 text-xs">
        <label htmlFor="week-select" className="font-semibold text-slate-700">Week</label>
        <select
          id="week-select"
          value={weekIndex}
          onChange={(e) => onWeekChange(Number(e.target.value))}
          className="border border-slate-300 rounded-md px-2 py-1 bg-white"
        >
          {plan.weeks.map((w, i) => (
            <option key={w.week_number} value={i}>{w.week_number} ({w.phase})</option>
          ))}
        </select>
        <span className="text-slate-500">Planned distance: {week.weekly_mileage_km} km</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs text-left border-collapse">
          <thead>
            <tr className="text-slate-500 border-b border-slate-200">
              <th className="py-1.5 pr-3">Day</th>
              <th className="py-1.5 pr-3">Workout</th>
              <th className="py-1.5 pr-3">Distance (km)</th>
              <th className="py-1.5 pr-3">Pace</th>
              <th className="py-1.5">Details</th>
            </tr>
          </thead>
          <tbody>
            {week.workouts.map((w) => (
              <tr key={w.day} className="border-b border-slate-100 align-top">
                <td className="py-1.5 pr-3 font-semibold text-slate-800">{w.day}</td>
                <td className="py-1.5 pr-3">{w.workout_type}</td>
                <td className="py-1.5 pr-3">{w.distance_km}</td>
                <td className="py-1.5 pr-3">{w.pace_target}</td>
                <td className="py-1.5 text-slate-600">
                  {[w.intensity_zone, w.warmup_cooldown, w.workout_execution, w.recovery_instruction, w.purpose].filter(Boolean).join(' · ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {plan.insights && (
        <div className="text-xs text-slate-700 space-y-1">
          {Object.values(plan.insights).map((text) => (
            <p key={text}>{text}</p>
          ))}
        </div>
      )}
      {plan.notes && (
        <ul className="text-xs text-slate-700 list-disc pl-5 space-y-0.5">
          {plan.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function BlindEvaluationPage() {
  const { planId } = useParams();
  const [participantCode] = useState(loadParticipantCode);
  const [plans, setPlans] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [activeLabel, setActiveLabel] = useState('A');
  const [weekIndex, setWeekIndex] = useState(0);
  const [scores, setScores] = useState({ A: EMPTY_SCORES, B: EMPTY_SCORES });
  const [comments, setComments] = useState({ A: '', B: '' });
  const [done, setDone] = useState({ A: false, B: false });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  // The shared index.html title names the generation method, so the tab title is neutral on this page.
  useEffect(() => {
    const previous = document.title;
    document.title = 'Plan rating';
    return () => {
      document.title = previous;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchBlindPlans(planId)
      .then((data) => {
        if (!cancelled) setPlans(data.plans);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err.message || 'The plans could not be loaded.');
      });
    return () => {
      cancelled = true;
    };
  }, [planId]);

  const current = plans ? plans.find((p) => p.label === activeLabel) : null;
  const labelScores = scores[activeLabel];
  const complete = LIKERT_CRITERIA.every((c) => labelScores[c.key] !== null);

  const setScore = (key, value) => {
    setScores({ ...scores, [activeLabel]: { ...labelScores, [key]: value } });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!complete) {
      setSubmitError('Please answer all four questions. No answer is pre-selected.');
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      await submitBlindEvaluation({
        training_plan_id: planId,
        plan_label: activeLabel,
        participant_code: participantCode,
        ...labelScores,
        comments: comments[activeLabel].trim() ? comments[activeLabel].trim() : null,
      });
      setDone({ ...done, [activeLabel]: true });
      const other = activeLabel === 'A' ? 'B' : 'A';
      if (!done[other]) {
        setActiveLabel(other);
        setWeekIndex(0);
      }
    } catch (err) {
      setSubmitError(err.message || 'The rating could not be saved.');
    } finally {
      setSubmitting(false);
    }
  };

  if (loadError) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-8">
        <div className="p-3.5 bg-rose-50 border-l-4 border-rose-500 text-rose-800 rounded-r-lg text-xs flex items-center space-x-2">
          <AlertCircle className="w-4 h-4 text-rose-600 shrink-0" />
          <span>{loadError}</span>
        </div>
      </div>
    );
  }

  if (!plans) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-12 flex items-center justify-center text-xs text-slate-500 gap-2">
        <Loader2 className="w-4 h-4 animate-spin" />
        <span>Loading plans...</span>
      </div>
    );
  }

  if (done.A && done.B) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-12 text-center space-y-3">
        <CheckCircle2 className="w-10 h-10 text-emerald-600 mx-auto" />
        <h2 className="text-xl font-bold text-slate-900">Both ratings were saved</h2>
        <p className="text-xs text-slate-600">Thank you. You can close this page.</p>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <div className="mb-6 text-xs text-amber-900 bg-amber-50 border-l-4 border-amber-500 px-4 py-3 rounded-r-lg">
        These are research plans for an academic study, not medical advice. Stop exercising and consult a doctor if you
        feel unwell. Please do not type your name or contact details anywhere on this page.
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 shadow-xs p-6 sm:p-8 space-y-6">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Rate the plans</h2>
          <p className="text-xs text-slate-500 mt-1">
            Your anonymous code: <span className="font-mono text-slate-700">{participantCode}</span>
          </p>
          <div className="mt-4 flex gap-2" role="tablist" aria-label="Plans">
            {['A', 'B'].map((label) => (
              <button
                key={label}
                type="button"
                role="tab"
                aria-selected={activeLabel === label}
                onClick={() => {
                  setActiveLabel(label);
                  setWeekIndex(0);
                  setSubmitError(null);
                }}
                className={`min-h-[40px] px-4 py-2 rounded-lg text-xs font-bold cursor-pointer ${
                  activeLabel === label ? 'bg-indigo-600 text-white' : 'bg-white text-slate-700 border border-slate-200 hover:bg-slate-100'
                }`}
              >
                Plan {label}{done[label] ? ' ✓' : ''}
              </button>
            ))}
          </div>
        </div>

        {current && <PlanView plan={current} weekIndex={weekIndex} onWeekChange={setWeekIndex} />}

        {done[activeLabel] ? (
          <p className="text-xs text-emerald-700 font-semibold">Plan {activeLabel} has been rated.</p>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-6 border-t border-slate-100 pt-6">
            {submitError && (
              <div className="p-3.5 bg-rose-50 border-l-4 border-rose-500 text-rose-800 rounded-r-lg text-xs flex items-center space-x-2">
                <AlertCircle className="w-4 h-4 text-rose-600 shrink-0" />
                <span>{submitError}</span>
              </div>
            )}

            {LIKERT_CRITERIA.map((criterion, idx) => {
              const value = labelScores[criterion.key];
              return (
                <div key={criterion.key} className="p-4 rounded-xl bg-slate-50 border border-slate-200/70">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 mb-1">
                    <h3 className="text-sm font-bold text-slate-900">
                      <span className="text-slate-400 font-semibold mr-1">{idx + 1}/{LIKERT_CRITERIA.length}</span>
                      {criterion.title}
                    </h3>
                    <span className="text-xs font-semibold text-indigo-700">
                      {value === null ? 'Not rated yet' : `Score: ${value} / 5 (${criterion.labels[value - 1]})`}
                    </span>
                  </div>
                  <p className="text-xs text-slate-600 mb-3">{criterion.description}</p>
                  <div className="grid grid-cols-5 gap-1.5 sm:gap-2">
                    {[1, 2, 3, 4, 5].map((val) => (
                      <button
                        key={val}
                        type="button"
                        onClick={() => setScore(criterion.key, val)}
                        aria-label={`${criterion.title}: ${criterion.labels[val - 1]}`}
                        aria-pressed={value === val}
                        className={`min-h-[56px] py-2 text-xs font-bold rounded-lg border cursor-pointer flex flex-col items-center justify-center gap-0.5 ${
                          value === val
                            ? 'bg-indigo-600 text-white border-indigo-600'
                            : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-100'
                        }`}
                      >
                        <Star className={`w-3.5 h-3.5 ${value !== null && value >= val ? 'fill-current' : 'text-slate-300'}`} />
                        <span>{val}</span>
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}

            <div className="space-y-1.5">
              <label htmlFor="blind-comments" className="block text-xs font-semibold text-slate-700">
                Comments on Plan {activeLabel} <span className="text-slate-400 font-normal">(optional; no names or contact details)</span>
              </label>
              <textarea
                id="blind-comments"
                rows={3}
                maxLength={2000}
                value={comments[activeLabel]}
                onChange={(e) => setComments({ ...comments, [activeLabel]: e.target.value })}
                className="w-full px-3 py-2.5 border border-slate-300 rounded-lg text-xs bg-white"
              />
            </div>

            <div className="pt-4 border-t border-slate-100 flex justify-end">
              <Button type="submit" disabled={submitting || !complete} variant="success" className="w-full sm:w-auto">
                {submitting ? 'Saving...' : `Save rating for Plan ${activeLabel}`}
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
