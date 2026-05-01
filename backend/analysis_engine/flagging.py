# ============================================================
#  analysis_engine/weekly/flagging.py  (v2 — composite risk score)
#
#  Philosophy
#  ----------
#  We don't care HOW a student studies — only that their methods
#  are working now AND that they're not drifting away from them.
#  Therefore:
#    • A_t  is judged in absolute terms  (we want you to perform)
#    • E_t  is judged by pct-change      (we don't care if you
#           never go to the library as long as you never did —
#           we care if you suddenly stop)
#
#  Scoring pipeline
#  ----------------
#  1. Pull the last 3 qualifying (non-exam, coverage-OK) teaching
#     weeks from weekly_metrics for every student.
#  2. Compute seven sub-signals and combine into a raw_risk_score.
#  3. Rank students within each class → flag top 20%.
#  4. Override: plagiarism or severe absenteeism always flag,
#     regardless of rank (policy violations, not just risk signals).
#  5. Assign risk tier from (percentile rank, raw score) pair.
#  6. Write WeeklyFlag rows + write risk_score / escalation_level
#     back into weekly_metrics.
#
#  Sub-signals (see WEIGHTS dict for tunable coefficients)
#  -------------------------------------------------------
#  a. risk_of_detention   — policy pressure from cumulative attendance
#  b. et_drop             — % decline in E_t over qualifying window
#                           (positive = E_t fell; negative = E_t rose)
#  c. assn_streak         — consecutive weeks of zero assignment submission
#  d. plag_pct            — max plagiarism seen in qualifying window
#  e. lag_score_penalty   — 1 − (student At/Et efficiency ÷ class At/Et
#                           efficiency); positive = student converts effort
#                           worse than class average
#  f. momentum            — average raw_risk_score of prior 2 qualifying weeks
#  g. quiz_streak         — consecutive weeks of zero quiz attempt
#
#  Final score
#  -----------
#  raw_risk_score = weighted_sum(a..g)  ÷  min(weeks_until_next_exam, 6)
#
#  Risk tier assignment
#  --------------------
#  Tier 1 (Critical)  : percentile ≥ 90 AND raw_risk_score > TIER1_ABS
#  Tier 2 (High Risk) : percentile ≥ 80 OR  raw_risk_score > TIER2_ABS
#  Tier 3 (Watch)     : everyone else in the top-20% flag set
#
#  Override flags bypass tier-scoring entirely and are always Tier 1.
#
#  Client DB  → ClientXxx models  (routed to 'client_db')
#  Analysis DB → WeeklyFlag, weekly_metrics (routed to 'default')
# ============================================================

import math
import warnings

warnings.filterwarnings('ignore')

from analysis_engine.client_models import (
    ClientSimState,
    ClientClass,
    ClientStudent,
)
from analysis_engine.models import WeeklyFlag, weekly_metrics

from django.db.models import Max


# ══════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════

MIDTERM_WEEK = 8
ENDTERM_WEEK = 18
EXAM_WEEKS   = {MIDTERM_WEEK, ENDTERM_WEEK}

# Minimum coverage (fraction of E_t base weights that were non-null)
# for a week to qualify for the 3-week scoring window.
# weekly_metrics stores effort_score=NULL when the week has no data,
# so we proxy coverage by checking whether effort_score is non-null
# AND at least one of the component sub-scores is non-null.
# If you later add an et_coverage column, replace the proxy below.
COVERAGE_THRESHOLD = 0.50   # i.e. ≥50% of E_t base weights active

# Qualifying-window size (teaching weeks, coverage-filtered)
WINDOW_SIZE = 3

# Cap on weeks-until-next-exam denominator
# (prevents early-semester scores collapsing to near-zero)
WEEKS_DENOM_CAP = 6

# Sub-signal weights  (must sum to 100 for interpretability, but any scale works)
WEIGHTS = dict(
    risk_of_detention = 25,   # a — policy-level pressure
    et_drop           = 20,   # b — effort deviation (our core philosophy)
    assn_streak       = 15,   # c — assignment dropout behaviour
    plag_pct          = 10,   # d — integrity (hard rule backstop covers extreme cases)
    lag_score_penalty = 15,   # e — effort-to-performance conversion vs class
    momentum          = 10,   # f — persistence of risk across prior weeks
    quiz_streak       =  5,   # g — quiz dropout (softer signal)
)

# Percentile + absolute thresholds for tier assignment
FLAG_PERCENTILE  = 80   # bottom edge of "flagged" band (top 20%)
TIER1_PERCENTILE = 90
TIER2_PERCENTILE = 80
TIER1_ABS        = 60   # raw_risk_score must also exceed this for Tier 1
TIER2_ABS        = 35   # raw_risk_score alone above this → Tier 2 even if < 90th pctile

# Override thresholds — these bypass percentile logic entirely
OVERRIDE_PLAG_PCT = 50   # max plagiarism > this → always Tier 1
OVERRIDE_ATT_PCT  = 30   # weekly_att_pct ≤ this  → always Tier 1

# Escalation: only Tier 1 / Tier 2 flags extend the streak
ESCALATION_TIER_THRESHOLD_SCORE = TIER2_ABS


# ══════════════════════════════════════════════════════════════
# 1. CONTEXT
# ══════════════════════════════════════════════════════════════

def _get_sim_context():
    """Read live week/semester from client DB."""
    state      = ClientSimState.objects.using('client_db').get(id=1)
    global_week = state.current_week

    if global_week <= 18:
        sem_week, slot = global_week, 'odd'
    else:
        sem_week, slot = global_week - 18, 'even'

    classes = list(ClientClass.objects.using('client_db').all())
    sem_map = {
        cls.class_id: (cls.odd_sem if slot == 'odd' else cls.even_sem)
        for cls in classes
    }

    return {
        'global_week': global_week,
        'sem_week':    sem_week,
        'slot':        slot,
        'sem_map':     sem_map,
        'classes':     classes,
    }


# ══════════════════════════════════════════════════════════════
# 2. HELPERS
# ══════════════════════════════════════════════════════════════

def _qualifying_window(sem_week, size=WINDOW_SIZE):
    """
    Return up to `size` teaching weeks immediately before sem_week,
    in descending order, skipping exam weeks.
    These are candidate weeks; coverage filter is applied later per student.
    """
    weeks = []
    w = sem_week - 1
    while w >= 1 and len(weeks) < size:
        if w not in EXAM_WEEKS:
            weeks.append(w)
        w -= 1
    return weeks  # e.g. [7, 6, 5]  or  [7, 6] near the start


def _weeks_until_next_exam(sem_week):
    """
    Teaching weeks (non-exam) between current week (exclusive)
    and the next exam week (exclusive).
    Capped at WEEKS_DENOM_CAP so early-semester scores aren't suppressed.
    """
    if sem_week < MIDTERM_WEEK:
        boundary = MIDTERM_WEEK
    elif sem_week < ENDTERM_WEEK:
        boundary = ENDTERM_WEEK
    else:
        boundary = ENDTERM_WEEK + 1   # past endterm — treat as 1 week away

    count = sum(
        1 for w in range(sem_week + 1, boundary)
        if w not in EXAM_WEEKS
    )
    return max(1, min(count, WEEKS_DENOM_CAP))   # clamp to [1, cap]


def _safe_float(val, default=None):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _week_is_covered(wm_row):
    """
    A weekly_metrics row 'counts' for the scoring window if effort_score
    is non-null AND at least one component sub-field is also non-null.
    Replace with an explicit et_coverage column check if you add that field.
    """
    if wm_row['effort_score'] is None:
        return False
    components = [
        'weekly_att_pct', 'quiz_attempt_rate',
        'assn_submit_rate', 'assn_quality_pct',
    ]
    return any(wm_row[c] is not None for c in components)


# ══════════════════════════════════════════════════════════════
# 3. DATA FETCH  —  everything from analysis DB (weekly_metrics)
#    One bulk query per call; no per-student round-trips.
# ══════════════════════════════════════════════════════════════

def _fetch_metric_window(semester, sem_week, candidate_weeks, student_ids):
    """
    Fetch weekly_metrics rows for (semester, candidate_weeks ∪ {sem_week})
    for all students in student_ids.

    Returns a dict:
        { student_id: { sem_week: <row_dict>, ... } }

    The current week's row is included so we can read risk_of_detention
    and weekly_att_pct (already written by weekly_metrics_calculator
    before flagging.py runs).
    """
    all_weeks = list(set(candidate_weeks) | {sem_week})

    qs = weekly_metrics.objects.filter(
        semester=semester,
        sem_week__in=all_weeks,
        student_id__in=student_ids,
    ).values(
        'student_id', 'sem_week',
        'effort_score', 'academic_performance',
        'weekly_att_pct', 'quiz_attempt_rate',
        'assn_submit_rate', 'assn_plagiarism_pct',
        'risk_of_detention', 'risk_score',
        # sub-fields used for coverage proxy:
        'assn_quality_pct',
    )

    result = {}
    for row in qs:
        sid = row['student_id']
        wk  = row['sem_week']
        result.setdefault(sid, {})[wk] = {
            'effort_score':        _safe_float(row['effort_score']),
            'academic_performance':_safe_float(row['academic_performance']),
            'weekly_att_pct':      _safe_float(row['weekly_att_pct']),
            'quiz_attempt_rate':   _safe_float(row['quiz_attempt_rate']),
            'assn_submit_rate':    _safe_float(row['assn_submit_rate']),
            'assn_plagiarism_pct': _safe_float(row['assn_plagiarism_pct']),
            'assn_quality_pct':    _safe_float(row['assn_quality_pct']),
            'risk_of_detention':   _safe_float(row['risk_of_detention']),
            'risk_score':          _safe_float(row['risk_score']),
        }
    return result


def _fetch_students(sem_map):
    """All active students across all classes in sem_map."""
    class_ids = list(sem_map.keys())
    return list(
        ClientStudent.objects.using('client_db')
        .filter(class_id__in=class_ids)
        .values('student_id', 'name', 'class_id')
    )


def _fetch_escalation_memory(semester):
    """
    Latest escalation_level per student from weekly_metrics.
    Returns { student_id: {'escalation_level': int, 'last_flagged_week': int|None} }
    """
    latest_weeks = (
        weekly_metrics.objects
        .filter(semester=semester)
        .values('student_id')
        .annotate(latest_week=Max('sem_week'))
    )
    memory = {}
    for row in latest_weeks:
        wm = (
            weekly_metrics.objects
            .filter(
                student_id=row['student_id'],
                semester=semester,
                sem_week=row['latest_week'],
            )
            .only('student_id', 'escalation_level', 'sem_week')
            .first()
        )
        if wm:
            memory[wm.student_id] = {
                'escalation_level': wm.escalation_level or 0,
                'last_flagged_week': wm.sem_week,
            }
    return memory


# ══════════════════════════════════════════════════════════════
# 4. SUB-SIGNAL COMPUTERS
#    Each takes the student's window dict { sem_week: row_dict }
#    and returns a float in a natural scale (not yet weighted).
# ══════════════════════════════════════════════════════════════

def _signal_et_drop(window_rows, qualifying_weeks):
    """
    Percentage-point drop in E_t between the oldest and newest qualifying week.
    Positive  → E_t fell  (bad — effort declining)
    Negative  → E_t rose  (good — clipped to 0 so it never reduces total score)
    Range returned: [0, 100]

    If fewer than 2 qualifying weeks exist, returns 0 (can't measure a trend).
    """
    ets = [
        window_rows[w]['effort_score']
        for w in qualifying_weeks
        if w in window_rows and window_rows[w]['effort_score'] is not None
    ]
    if len(ets) < 2:
        return 0.0
    # qualifying_weeks is descending (newest first), so:
    newest, oldest = ets[0], ets[-1]
    drop = oldest - newest   # positive when newest < oldest
    return max(0.0, drop)    # clip: rising effort doesn't reward, only punishes declining


def _signal_assn_streak(window_rows, qualifying_weeks):
    """
    Count of consecutive qualifying weeks (from most recent going back)
    where assn_submit_rate == 0.
    Returns a count in [0, WINDOW_SIZE].
    Scaled to [0, 100] by multiplying by (100 / WINDOW_SIZE).
    """
    streak = 0
    for w in qualifying_weeks:   # newest → oldest
        row = window_rows.get(w)
        if row is None:
            break
        rate = row.get('assn_submit_rate')
        if rate is not None and rate == 0.0:
            streak += 1
        else:
            break   # streak broken
    return streak * (100.0 / WINDOW_SIZE)


def _signal_quiz_streak(window_rows, qualifying_weeks):
    """
    Same logic as assn_streak but for quiz_attempt_rate.
    """
    streak = 0
    for w in qualifying_weeks:
        row = window_rows.get(w)
        if row is None:
            break
        rate = row.get('quiz_attempt_rate')
        if rate is not None and rate == 0.0:
            streak += 1
        else:
            break
    return streak * (100.0 / WINDOW_SIZE)


def _signal_plag(window_rows, qualifying_weeks):
    """
    Max plagiarism percentage seen across the qualifying window.
    Returns [0, 100]. Already on the right scale.
    """
    plags = [
        window_rows[w]['assn_plagiarism_pct']
        for w in qualifying_weeks
        if w in window_rows and window_rows[w]['assn_plagiarism_pct'] is not None
    ]
    return max(plags) if plags else 0.0


def _signal_lag_penalty(window_rows, qualifying_weeks, class_at_et_ratio):
    """
    Measures whether the student's effort converts to performance as
    well as the class average.

    lag_score = (student_avg_At / student_avg_Et)
              / (class_avg_At   / class_avg_Et)

    lag_penalty = max(0, 1 − lag_score) × 100
        lag_score < 1 → student converts worse than class → positive penalty
        lag_score > 1 → student converts better           → 0 penalty

    class_at_et_ratio is pre-computed outside the per-student loop
    so it's the same denominator for all students in the same class.

    Returns [0, 100].
    """
    ats, ets = [], []
    for w in qualifying_weeks:
        row = window_rows.get(w)
        if row is None:
            continue
        at = row.get('academic_performance')
        et = row.get('effort_score')
        if at is not None and et is not None and et > 0:
            ats.append(at)
            ets.append(et)

    if not ats or not ets:
        return 0.0   # not enough data → neutral

    student_ratio = (sum(ats) / len(ats)) / (sum(ets) / len(ets))

    if class_at_et_ratio is None or class_at_et_ratio <= 0:
        return 0.0   # class ratio unavailable → neutral

    lag_score   = student_ratio / class_at_et_ratio
    lag_penalty = max(0.0, 1.0 - lag_score) * 100.0
    return round(min(lag_penalty, 100.0), 4)


def _signal_momentum(window_rows, qualifying_weeks):
    """
    Average raw_risk_score from the prior 2 qualifying weeks
    (already written back into weekly_metrics by the previous run).
    Returns [0, ∞) — same scale as raw_risk_score itself.
    Caller weights this by WEIGHTS['momentum'].
    """
    prior_scores = [
        window_rows[w]['risk_score']
        for w in qualifying_weeks
        if w in window_rows and window_rows[w]['risk_score'] is not None
    ][:2]   # at most 2 prior weeks
    if not prior_scores:
        return 0.0
    return sum(prior_scores) / len(prior_scores)


def _signal_detention_risk(current_week_row):
    """
    risk_of_detention is already in [0, 100], computed by
    weekly_metrics_calculator.py before this script runs.
    Returns [0, 100] or 0 if not available.
    """
    if current_week_row is None:
        return 0.0
    val = current_week_row.get('risk_of_detention')
    return val if val is not None else 0.0


# ══════════════════════════════════════════════════════════════
# 5. CLASS-LEVEL RATIO
#    Pre-computed once per class per call so lag_penalty uses
#    the same denominator for every student in that class.
# ══════════════════════════════════════════════════════════════

def _class_at_et_ratios(all_metric_windows, student_class_map, qualifying_weeks):
    """
    For each class, compute the average (At / Et) ratio across all students
    and all qualifying weeks where both signals are non-null and Et > 0.

    Returns { class_id: ratio | None }
    """
    class_pairs = {}   # { class_id: [(at, et), ...] }

    for sid, week_rows in all_metric_windows.items():
        cid = student_class_map.get(sid)
        if cid is None:
            continue
        for w in qualifying_weeks:
            row = week_rows.get(w)
            if row is None:
                continue
            at = row.get('academic_performance')
            et = row.get('effort_score')
            if at is not None and et is not None and et > 0:
                class_pairs.setdefault(cid, []).append((at, et))

    result = {}
    for cid, pairs in class_pairs.items():
        ratios = [at / et for at, et in pairs]
        result[cid] = sum(ratios) / len(ratios) if ratios else None

    return result


# ══════════════════════════════════════════════════════════════
# 6. COMPOSITE RISK SCORE
# ══════════════════════════════════════════════════════════════

def _compute_raw_risk_score(
    sid,
    week_rows,
    qualifying_weeks,
    current_week_row,
    class_at_et_ratio
):
    """
    Assemble all sub-signals and return raw_risk_score (float ≥ 0).

    Parameters
    ----------
    week_rows        : { sem_week: row_dict }  for this student
    qualifying_weeks : descending list of teaching weeks that meet
                       coverage threshold, before current week
    current_week_row : row_dict for the current sem_week (may be None)
    class_at_et_ratio: pre-computed class-level At/Et ratio
    weeks_denom      : min(weeks_until_next_exam, WEEKS_DENOM_CAP)

    Returns
    -------
    (raw_risk_score, sub_signals_dict)
    """
    W = WEIGHTS

    a = _signal_detention_risk(current_week_row)
    b = _signal_et_drop(week_rows, qualifying_weeks)
    c = _signal_assn_streak(week_rows, qualifying_weeks)
    d = _signal_plag(week_rows, qualifying_weeks)
    e = _signal_lag_penalty(week_rows, qualifying_weeks, class_at_et_ratio)
    f = _signal_momentum(week_rows, qualifying_weeks)
    g = _signal_quiz_streak(week_rows, qualifying_weeks)

    # Momentum (f) is on the same scale as raw_risk_score (not 0-100),
    # so we normalise it: cap at 100 before weighting.
    f_norm = min(f, 100.0)

    weighted_sum = (
        W['risk_of_detention'] * a / 100.0 +
        W['et_drop']           * b / 100.0 +
        W['assn_streak']       * c / 100.0 +
        W['plag_pct']          * d / 100.0 +
        W['lag_score_penalty'] * e / 100.0 +
        W['momentum']          * f_norm / 100.0 +
        W['quiz_streak']       * g / 100.0
    )
    # Weighted sum is now on [0, sum(WEIGHTS)] = [0, 100] scale.
    raw_risk_score = round(weighted_sum , 4)

    sub_signals = dict(a=a, b=b, c=c, d=d, e=e, f=f, g=g)
    return raw_risk_score, sub_signals


# ══════════════════════════════════════════════════════════════
# 7. OVERRIDE CHECK
#    Returns (True, reason_string) if hard-rule conditions met.
# ══════════════════════════════════════════════════════════════

def _check_override(current_week_row, week_rows, qualifying_weeks):
    """
    Hard-rule overrides — always flag as Tier 1 regardless of percentile.
    Returns (is_override: bool, reasons: list[str])
    """
    reasons = []

    if current_week_row:
        att = current_week_row.get('weekly_att_pct')
        if att is not None and att <= OVERRIDE_ATT_PCT:
            reasons.append(f'Severe Absenteeism (att={att:.1f}%)')

    max_plag = _signal_plag(week_rows, qualifying_weeks)
    if max_plag > OVERRIDE_PLAG_PCT:
        reasons.append(f'Integrity Violation (plag={max_plag:.1f}%)')

    return bool(reasons), reasons


# ══════════════════════════════════════════════════════════════
# 8. TIER ASSIGNMENT
# ══════════════════════════════════════════════════════════════

def _assign_tier(percentile, raw_risk_score, is_override,weeks_denom):
    """
    Determine risk tier string.

    Override → always Tier 1.
    Otherwise use (percentile, absolute score) pair.
    """
    severity_score=round(raw_risk_score/weeks_denom, 4)
    if is_override:
        return 'Tier 1 (Critical)'

    if percentile >= TIER1_PERCENTILE and severity_score > TIER1_ABS:
        return 'Tier 1 (Critical)'
    if percentile >= TIER2_PERCENTILE or severity_score > TIER2_ABS:
        return 'Tier 2 (High Risk)'
    return 'Tier 3 (Watch)'


# ══════════════════════════════════════════════════════════════
# 9. PERCENTILE HELPER
# ══════════════════════════════════════════════════════════════

def _percentile_rank(score, all_scores):
    """
    Percentile rank of `score` within `all_scores` list.
    Returns 0–100 (float). Higher = more at-risk.
    """
    if not all_scores:
        return 0.0
    n = len(all_scores)
    count_below = sum(1 for s in all_scores if s < score)
    return (count_below / n) * 100.0


# ══════════════════════════════════════════════════════════════
# 10. DIAGNOSIS STRING
# ══════════════════════════════════════════════════════════════

def _build_diagnosis(sub_signals, override_reasons, qualifying_weeks):
    """
    Human-readable diagnosis from sub-signals and any override triggers.
    Only surfaces signals that crossed meaningful thresholds.
    """
    parts = list(override_reasons)   # start with hard-rule reasons

    a, b, c, d, e, f, g = (
        sub_signals['a'], sub_signals['b'], sub_signals['c'],
        sub_signals['d'], sub_signals['e'], sub_signals['f'],
        sub_signals['g'],
    )

    if not override_reasons:   # don't double-report plag/att for overrides
        if a >= 60:
            parts.append(f'Detention Risk (score={a:.0f})')
        if d > 30 and d <= OVERRIDE_PLAG_PCT:
            parts.append(f'Elevated Plagiarism ({d:.0f}%)')

    if b >= 15:
        parts.append(f'Effort Declining (−{b:.0f}pp over {len(qualifying_weeks)}w)')
    if c >= 100.0 / WINDOW_SIZE:   # at least 1 consecutive missed week
        streak_count = int(round(c / (100.0 / WINDOW_SIZE)))
        parts.append(f'Assignment Dropout ({streak_count}w streak)')
    if g >= 100.0 / WINDOW_SIZE:
        streak_count = int(round(g / (100.0 / WINDOW_SIZE)))
        parts.append(f'Quiz Dropout ({streak_count}w streak)')
    if e >= 25:
        parts.append(f'Low Effort–Performance Conversion (lag penalty={e:.0f})')
    if f >= 20:
        parts.append(f'Persistent Risk (momentum={f:.0f})')

    return ' | '.join(parts) if parts else 'Composite Risk'


# ══════════════════════════════════════════════════════════════
# 11. ESCALATION UPDATE
# ══════════════════════════════════════════════════════════════

def _new_escalation_level(raw_risk_score, old_level, already_this_week):
    """
    Escalation tracks how many consecutive 'serious' weeks (Tier 1/2 equivalent)
    a student has had, boosting urgency for persistent cases.

    Tier 1 / Tier 2 equivalent (score > ESCALATION_TIER_THRESHOLD_SCORE)
        → extend streak (unless already flagged this week)
    Tier 3 / not flagged
        → reset streak to 0
    """
    if raw_risk_score > ESCALATION_TIER_THRESHOLD_SCORE:
        if already_this_week:
            return old_level         # idempotent within the same week
        return old_level + 1
    return 0


# ══════════════════════════════════════════════════════════════
# 12. MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════

def generate_weekly_triage(sem_week=None, semester=None):
    """
    Compute composite risk scores for all students and write:
      • weekly_flags       — top-20% per class + override students
      • weekly_metrics     — risk_score + escalation_level columns

    Parameters
    ----------
    sem_week  : int | None  — override the live sim week (used by calibrate)
    semester  : int | None  — override the live semester  (used by calibrate)

    When both are None the function reads the live week from ClientSimState.
    """
    print("=== Composite Risk Scoring Engine (v2) ===")

    # ── Resolve week / semester ───────────────────────────────
    if sem_week is None or semester is None:
        ctx          = _get_sim_context()
        sem_week     = ctx['sem_week']
        sem_map      = ctx['sem_map']
        rep_semester = next(iter(sem_map.values()))
    else:
        classes  = list(ClientClass.objects.using('client_db').all())
        sem_map  = {
            cls.class_id: (cls.odd_sem if semester % 2 == 1 else cls.even_sem)
            for cls in classes
        }
        rep_semester = semester

    print(f"  sem_week={sem_week}  semester={rep_semester}")

    # ── Grace period: weeks 1-3 have no scoring history ──────
    if sem_week <= 3:
        print("  Grace period (week ≤ 3) — no flags generated.")
        return

    # ── Exam week: nothing to flag ────────────────────────────
    if sem_week in EXAM_WEEKS:
        print(f"  Exam week {sem_week} — no flags generated.")
        return

    # ── Students ──────────────────────────────────────────────
    students         = _fetch_students(sem_map)
    student_ids      = [s['student_id'] for s in students]
    student_class_map = {s['student_id']: s['class_id'] for s in students}
    student_name_map  = {s['student_id']: s['name']     for s in students}

    if not student_ids:
        print("  No students found.")
        return

    # ── Candidate window weeks (same for everyone) ────────────
    candidate_weeks = _qualifying_window(sem_week)   # descending list
    weeks_denom     = _weeks_until_next_exam(sem_week)
    print(f"  candidate_window={candidate_weeks}  weeks_denom={weeks_denom}")

    # ── Bulk-fetch metric rows ─────────────────────────────────
    all_metric_windows = _fetch_metric_window(
        rep_semester, sem_week, candidate_weeks, student_ids
    )

    # ── Escalation memory ─────────────────────────────────────
    memory = _fetch_escalation_memory(rep_semester)

    # ── Class-level At/Et ratio (for lag_penalty denominator) ─
    class_ratios = _class_at_et_ratios(
        all_metric_windows, student_class_map, candidate_weeks
    )

    # ── Per-student scoring ───────────────────────────────────
    scored_students   = []   # list of dicts — all students
    override_students = []   # students who triggered hard rules

    for stu in students:
        sid = stu['student_id']
        cid = stu['class_id']

        week_rows        = all_metric_windows.get(sid, {})
        current_week_row = week_rows.get(sem_week)

        # Filter candidate weeks to those with sufficient coverage
        # for THIS student (another student in the same week might
        # lack data if they're new, suspended, etc.)
        qualifying_weeks = [
            w for w in candidate_weeks
            if w in week_rows and _week_is_covered(week_rows[w])
        ]

        # Override check (hard rules — always flag)
        is_override, override_reasons = _check_override(
            current_week_row, week_rows, qualifying_weeks
        )

        raw_risk_score, sub_signals = _compute_raw_risk_score(
            sid,
            week_rows,
            qualifying_weeks,
            current_week_row,
            class_ratios.get(cid),
        )

        hist             = memory.get(sid, {'escalation_level': 0, 'last_flagged_week': None})
        already_this_week = (hist['last_flagged_week'] == sem_week)
        new_escalation    = _new_escalation_level(
            raw_risk_score, hist['escalation_level'], already_this_week
        )

        record = {
            'student_id':     sid,
            'name':           student_name_map[sid],
            'class_id':       cid,
            'raw_risk_score': raw_risk_score,
            'sub_signals':    sub_signals,
            'qualifying_weeks': qualifying_weeks,
            'override_reasons': override_reasons,
            'is_override':    is_override,
            'new_escalation': new_escalation,
        }
        scored_students.append(record)

        if is_override:
            override_students.append(record)

    # ── Percentile ranking within each class ──────────────────
    class_scores = {}   # { class_id: [raw_risk_score, ...] }
    for rec in scored_students:
        class_scores.setdefault(rec['class_id'], []).append(rec['raw_risk_score'])

    # ── Build flag list ───────────────────────────────────────
    # Top 20% per class + all overrides (union, deduped by student_id)
    flag_student_ids = set()
    flags_to_write   = []

    for cid, scores_in_class in class_scores.items():
        class_students = [r for r in scored_students if r['class_id'] == cid]
        class_students.sort(key=lambda r: r['raw_risk_score'], reverse=True)

        n_to_flag = max(1, math.ceil(len(class_students) * 0.20))
        top_students = class_students[:n_to_flag]

        for rec in top_students:
            sid        = rec['student_id']
            percentile = _percentile_rank(rec['raw_risk_score'], scores_in_class)
            tier       = _assign_tier(percentile, rec['raw_risk_score'], rec['is_override'],weeks_denom)
            diagnosis  = _build_diagnosis(
                rec['sub_signals'], rec['override_reasons'], rec['qualifying_weeks']
            )
            urgency    = int(round(rec['raw_risk_score'] ))   # scale for display

            if sid not in flag_student_ids:
                flag_student_ids.add(sid)
                flags_to_write.append({
                    'student_id':   sid,
                    'class_id':     cid,
                    'risk_tier':    tier,
                    'urgency_score': urgency,
                    'diagnosis':    diagnosis,
                })

    # Add override students who weren't in the top-20% cut
    for rec in override_students:
        sid = rec['student_id']
        if sid not in flag_student_ids:
            cid        = rec['class_id']
            scores_in_class = class_scores.get(cid, [])
            percentile = _percentile_rank(rec['raw_risk_score'], scores_in_class)
            diagnosis  = _build_diagnosis(
                rec['sub_signals'], rec['override_reasons'], rec['qualifying_weeks']
            )
            urgency    = int(round(rec['raw_risk_score'] ))

            flag_student_ids.add(sid)
            flags_to_write.append({
                'student_id':   sid,
                'class_id':     cid,
                'risk_tier':    'Tier 1 (Critical)',
                'urgency_score': urgency,
                'diagnosis':    diagnosis,
            })

    # ── Write weekly_flags ────────────────────────────────────
    if flags_to_write:
        flag_objs = [
            WeeklyFlag(
                student_id   = f['student_id'],
                class_id     = f['class_id'],
                semester     = rep_semester,
                sem_week     = sem_week,
                risk_tier    = f['risk_tier'],
                urgency_score= f['urgency_score'],
                diagnosis    = f['diagnosis'],
            )
            for f in flags_to_write
        ]
        WeeklyFlag.objects.bulk_create(flag_objs, ignore_conflicts=True)

        by_class_count = {}
        for f in flags_to_write:
            by_class_count[f['class_id']] = by_class_count.get(f['class_id'], 0) + 1
        breakdown = ', '.join(f"{c}: {n}" for c, n in sorted(by_class_count.items()))
        print(f"  Flags written → {len(flags_to_write)} student(s)  [{breakdown}]")
    else:
        print("  No flags generated this week.")

    # ── Write risk_score + escalation_level → weekly_metrics ─
    risk_score_map    = {r['student_id']: r['raw_risk_score']  for r in scored_students}
    escalation_map    = {r['student_id']: r['new_escalation']  for r in scored_students}

    wm_qs = weekly_metrics.objects.filter(
        semester=rep_semester,
        sem_week=sem_week,
        student_id__in=student_ids,
    )

    rows_to_update = []
    for wm in wm_qs:
        sid = wm.student_id
        wm.risk_score       = int(round(risk_score_map[sid] * 100)) if sid in risk_score_map else wm.risk_score
        wm.escalation_level = escalation_map.get(sid, wm.escalation_level)
        rows_to_update.append(wm)

    if rows_to_update:
        weekly_metrics.objects.bulk_update(rows_to_update, ['risk_score', 'escalation_level'])
        print(f"  risk_score + escalation_level → weekly_metrics ({len(rows_to_update)} rows)")
    else:
        print("  No weekly_metrics rows found for this week — risk_score not written.")

    print("=== Done ===")


# ── Standalone entry point ────────────────────────────────────
if __name__ == '__main__':
    import django
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project.settings')
    django.setup()
    generate_weekly_triage()