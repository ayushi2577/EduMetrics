# ============================================================
#  analysis_engine/weekly/flagging.py  (v3 — composite risk score)
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
#  2. Compute eight sub-signals, each normalised to [0, 100].
#  3. Combine via weighted sum → risk_score in [0, 100].
#  4. Rank students within each class → flag top 20%.
#  5. Override: plagiarism or severe absenteeism always flag
#     regardless of rank (policy violations, not just risk signals).
#  6. Assign risk tier from (percentile rank, risk_score) pair.
#  7. Write WeeklyFlag rows; write risk_score + escalation_level
#     back into weekly_metrics.
#     escalation_level = streak of consecutive flagged weeks,
#     read from weekly_flags (NOT derived from risk_score magnitude).
#
#  Sub-signals (see WEIGHTS dict for tunable coefficients)
#  -------------------------------------------------------
#  a. risk_of_detention   — convex transform of detention risk     [0-100]
#                           signal = (raw/100)² × 100
#  b. assn_streak         — consecutive weeks of missed assignments [0-100]
#                           capped at 3, divided by 3, scaled to 100
#  c. quiz_streak         — consecutive weeks of missed quizzes     [0-100]
#                           capped at 3, divided by 3, scaled to 100
#  d. high_risk_streak    — weeks in window where risk_score ≥ 50  [0-100]
#                           capped at 3, divided by 3, convex transform
#  e. lag_score_penalty   — how much worse student converts effort
#                           vs class average                        [0-100]
#  f. avg_risk_score_3w   — plain average of prior risk_scores      [0-100]
#  g. avg_at_3w           — average A_t over qualifying window      [0-100]
#                           inverted: high performance → low signal
#  h. avg_et_3w           — average E_t over qualifying window      [0-100]
#                           inverted: high effort → low signal
#  i. et_drop             — pp decline in E_t oldest → newest       [0-100]
#                           positive drop (effort fell) → higher signal
#
#  Final score
#  -----------
#  risk_score = clamp(Σ weight_i * signal_i / 100, 0, 100)
#  Weights sum to 100, each signal is [0-100] → result is [0-100].
#
#  Risk tier assignment
#  --------------------
#  Tier 1 (Critical)  : percentile ≥ 90 AND risk_score > TIER1_ABS
#  Tier 2 (High Risk) : percentile ≥ 80 OR  risk_score > TIER2_ABS
#  Tier 3 (Watch)     : everyone else in the flagged set
#  Override flags bypass tier logic entirely → always Tier 1.
#
#  escalation_level (written to weekly_metrics)
#  --------------------------------------------
#  Consecutive weeks this student has appeared in weekly_flags,
#  counting the current week. Streak resets to 0 if not flagged.
#  Exam weeks are neutral — they neither extend nor break a streak.
#
#  Client DB   → ClientXxx models  (routed to 'client_db')
#  Analysis DB → WeeklyFlag, weekly_metrics (routed to 'default')
# ============================================================

import math
import warnings

warnings.filterwarnings('ignore')

from django.db.models import Max

from analysis_engine.client_models import (
    ClientSimState,
    ClientClass,
    ClientStudent,
)
from analysis_engine.models import WeeklyFlag, weekly_metrics


# ══════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════

MIDTERM_WEEK = 8
ENDTERM_WEEK = 18
EXAM_WEEKS   = {MIDTERM_WEEK, ENDTERM_WEEK}

# Window of prior teaching weeks fed into the scoring model
WINDOW_SIZE = 3   # look back up to this many qualifying weeks

# Consecutive-miss streak cap (applies to assn and quiz streaks)
STREAK_CAP = 3

# Threshold for high-risk-streak signal
HIGH_RISK_THRESHOLD = 40

# Weights — MUST sum to 100 so the weighted sum lands in [0, 100]
WEIGHTS = dict(
    risk_of_detention  = 30,   # a — convex transform
    assn_streak        = 15,   # b — capped-streak of missed assignments
    quiz_streak        =  8,   # c — capped-streak of missed quizzes
    high_risk_streak   = 12,   # d — weeks with risk_score ≥ 50, capped 3, convex
    lag_score_penalty  = 10,   # e — effort-to-performance conversion gap
    avg_risk_score_3w  =  7,   # f — avg risk_score over qualifying window
    avg_at_3w          =  5,   # g — avg A_t over window (inverted)
    avg_et_3w          =  5,   # h — avg E_t over window (inverted)
    et_drop            =  8,   # i — pp drop in E_t oldest → newest
)
assert sum(WEIGHTS.values()) == 100, "WEIGHTS must sum to 100"

# Tier thresholds
TIER1_PERCENTILE = 90
TIER2_PERCENTILE = 80
TIER1_ABS        = 50   # risk_score must also exceed this for Tier 1
TIER2_ABS        = 30   # risk_score alone above this → Tier 2

# Hard-rule override thresholds
OVERRIDE_PLAG_PCT = 50   # max plag % in window > this  → always Tier 1
OVERRIDE_ATT_PCT  = 30   # current week_att_pct ≤ this  → always Tier 1


# ══════════════════════════════════════════════════════════════
# 1. CONTEXT
# ══════════════════════════════════════════════════════════════

def _get_sim_context():
    """Read live week/semester from client DB."""
    state       = ClientSimState.objects.using('client_db').get(id=1)
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
    Up to `size` teaching weeks immediately before sem_week,
    descending order, skipping exam weeks.
    Coverage filter is applied per-student in the main loop.
    """
    weeks = []
    w = sem_week - 1
    while w >= 1 and len(weeks) < size:
        if w not in EXAM_WEEKS:
            weeks.append(w)
        w -= 1
    return weeks   # e.g. [7, 6, 5]


def _safe_float(val, default=None):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _week_is_covered(row):
    """
    Row qualifies for the scoring window when effort_score is non-null
    AND at least one E_t sub-component is also non-null.
    (Proxy for ≥50% E_t signal coverage without a dedicated column.)
    """
    if row.get('effort_score') is None:
        return False
    return any(
        row.get(c) is not None
        for c in ('weekly_att_pct', 'quiz_attempt_rate',
                  'assn_submit_rate', 'assn_quality_pct')
    )



# ══════════════════════════════════════════════════════════════
# 3. DATA FETCH
# ══════════════════════════════════════════════════════════════

def _fetch_students(sem_map):
    return list(
        ClientStudent.objects.using('client_db')
        .filter(class_id__in=list(sem_map.keys()))
        .values('student_id', 'name', 'class_id')
    )


def _fetch_metric_window(semester, sem_week, candidate_weeks, student_ids):
    """
    Bulk-fetch weekly_metrics rows for
    (semester, candidate_weeks ∪ {sem_week}, student_ids).
    The current week's row is included so we can read
    risk_of_detention and weekly_att_pct (written before this script runs).

    Returns { student_id: { sem_week: row_dict } }.
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
        'assn_submit_rate', 'assn_quality_pct',
        'assn_plagiarism_pct',
        'risk_of_detention',
        'risk_score',
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
            'assn_quality_pct':    _safe_float(row['assn_quality_pct']),
            'assn_plagiarism_pct': _safe_float(row['assn_plagiarism_pct']),
            'risk_of_detention':   _safe_float(row['risk_of_detention']),
            'risk_score':          _safe_float(row['risk_score']),
        }
    return result


def _fetch_flag_streaks(semester, sem_week, student_ids):
    """
    For each student, count consecutive teaching weeks immediately
    before sem_week where they appear in weekly_flags.

    Exam weeks are neutral: they don't break or extend the streak —
    we skip over them when walking back.

    Returns { student_id: prior_consecutive_flagged_weeks }
    """
    prior_flagged = set(
        WeeklyFlag.objects
        .filter(
            semester=semester,
            sem_week__lt=sem_week,
            student_id__in=student_ids,
        )
        .values_list('student_id', 'sem_week')
    )

    streaks = {}
    for sid in student_ids:
        streak = 0
        w = sem_week - 1
        while w >= 1:
            if w in EXAM_WEEKS:
                w -= 1
                continue            # neutral — skip without breaking streak
            if (sid, w) in prior_flagged:
                streak += 1
            else:
                break               # streak broken
            w -= 1
        streaks[sid] = streak
    return streaks


# ══════════════════════════════════════════════════════════════
# 4. CLASS-LEVEL At/Et RATIO  (lag_penalty denominator)
# ══════════════════════════════════════════════════════════════

def _class_at_et_ratios(all_metric_windows, student_class_map, candidate_weeks):
    """
    For each class, compute mean(At/Et) across all students and all
    candidate weeks where both signals are non-null and Et > 0.
    Returns { class_id: ratio | None }.
    """
    class_ratios = {}
    for sid, week_rows in all_metric_windows.items():
        cid = student_class_map.get(sid)
        if cid is None:
            continue
        for w in candidate_weeks:
            row = week_rows.get(w)
            if row is None:
                continue
            at = row.get('academic_performance')
            et = row.get('effort_score')
            if at is not None and et is not None and et > 0:
                class_ratios.setdefault(cid, []).append(at / et)

    return {
        cid: (sum(vals) / len(vals) if vals else None)
        for cid, vals in class_ratios.items()
    }


# ══════════════════════════════════════════════════════════════
# 5. SUB-SIGNAL COMPUTERS  (each returns float in [0, 100])
# ══════════════════════════════════════════════════════════════

def _signal_detention_risk(current_week_row):
    """
    Convex transform: signal = (raw/100)² × 100.
    Small detention risk stays small; high detention risk hits hard.
    risk_of_detention is already in [0, 100].
    """
    if current_week_row is None:
        return 0.0
    raw = float(current_week_row.get('risk_of_detention') or 0.0)
    return (raw / 100.0) ** 2 * 100.0


def _signal_assn_streak(week_rows, qualifying_weeks):
    """
    Count consecutive weeks (from most recent backward) in the qualifying
    window where the student missed at least one assignment
    (assn_submit_rate < 1.0).

    Capped at STREAK_CAP, divided by STREAK_CAP → [0, 1], scaled to [0, 100].

    Example: 3 consecutive missed weeks → min(3,3)/3 × 100 = 100.
             1 missed week then submitted → streak = 1 → 33.3.
    """
    streak = 0
    for w in qualifying_weeks:          # descending: most recent first
        rate = (week_rows.get(w) or {}).get('assn_submit_rate')
        if rate is not None and float(rate) < 1.0:
            streak += 1
        else:
            break                       # streak broken at first non-miss
    return float(min(streak, STREAK_CAP) / STREAK_CAP * 100.0)


def _signal_quiz_streak(week_rows, qualifying_weeks):
    """
    Count consecutive weeks (most recent first) where the student missed
    at least one quiz (quiz_attempt_rate < 1.0).

    Capped at STREAK_CAP, divided by STREAK_CAP → [0, 100].
    """
    streak = 0
    for w in qualifying_weeks:          # descending: most recent first
        rate = (week_rows.get(w) or {}).get('quiz_attempt_rate')
        if rate is not None and float(rate) < 1.0:
            streak += 1
        else:
            break
    return float(min(streak, STREAK_CAP) / STREAK_CAP * 100.0)


def _signal_high_risk_streak(week_rows, qualifying_weeks):
    """
    Count qualifying weeks (anywhere in the window, not necessarily
    consecutive) where the prior risk_score was ≥ HIGH_RISK_THRESHOLD.

    Capped at STREAK_CAP, divided by STREAK_CAP, then convex-transformed:
        raw_ratio = count / STREAK_CAP          ∈ [0, 1]
        signal    = raw_ratio² × 100            ∈ [0, 100]

    Convex: one high-risk week barely registers; three consecutive
    high-risk weeks hits hard (100).
    """
    count = sum(
        1 for w in qualifying_weeks
        if w in week_rows
        and week_rows[w].get('risk_score') is not None
        and float(week_rows[w]['risk_score']) >= HIGH_RISK_THRESHOLD
    )
    raw_ratio = min(count, STREAK_CAP) / STREAK_CAP   # [0, 1]
    return raw_ratio ** 2 * 100.0


def _signal_plag(week_rows, qualifying_weeks):
    """Max plagiarism % across the qualifying window. Already in [0, 100]."""
    plags = [
        week_rows[w]['assn_plagiarism_pct']
        for w in qualifying_weeks
        if w in week_rows and week_rows[w]['assn_plagiarism_pct'] is not None
    ]
    return float(max(plags)) if plags else 0.0


def _signal_lag_penalty(week_rows, qualifying_weeks, class_at_et_ratio):
    """
    Measures how much worse the student converts effort into performance
    compared with the class average.

        student_ratio = mean(At) / mean(Et)    over qualifying weeks
        lag_score     = student_ratio / class_at_et_ratio
        penalty       = max(0, 1 − lag_score) × 100

    lag_score < 1 → student converts worse than class → positive penalty.
    lag_score ≥ 1 → student converts at least as well → 0 (clipped).
    """
    ats, ets = [], []
    for w in qualifying_weeks:
        row = week_rows.get(w)
        if row is None:
            continue
        at = row.get('academic_performance')
        et = row.get('effort_score')
        if at is not None and et is not None and et > 0:
            ats.append(at)
            ets.append(et)

    if not ats or class_at_et_ratio is None or class_at_et_ratio <= 0:
        return 0.0

    student_ratio = (sum(ats) / len(ats)) / (sum(ets) / len(ets))
    penalty       = max(0.0, 1.0 - (student_ratio / class_at_et_ratio)) * 100.0
    return float(min(penalty, 100.0))


def _signal_avg_risk_score(week_rows, qualifying_weeks):
    """
    Plain average of prior risk_score values across the qualifying window.
    risk_score is already in [0, 100] (written by the previous run of this
    script), so the average is also in [0, 100] with no further transform.
    Returns 0 if no prior scores are available.
    """
    scores = [
        week_rows[w]['risk_score']
        for w in qualifying_weeks
        if w in week_rows and week_rows[w].get('risk_score') is not None
    ]
    if not scores:
        return 0.0
    return float(sum(scores) / len(scores))


def _signal_avg_at(week_rows, qualifying_weeks):
    """
    Average A_t (academic_performance) over the qualifying window,
    inverted: high performance → low risk signal.

    signal = 100 − avg_at   (A_t guaranteed [0, 100])
    Returns 0 if no data (missing data is not evidence of good performance
    but we don't want to penalise data gaps here — lag_penalty handles that).
    """
    ats = [
        week_rows[w]['academic_performance']
        for w in qualifying_weeks
        if w in week_rows and week_rows[w].get('academic_performance') is not None
    ]
    if not ats:
        return 0.0
    return float(max(0.0, 100.0 - (sum(ats) / len(ats))))


def _signal_avg_et(week_rows, qualifying_weeks):
    """
    Average E_t (effort_score) over the qualifying window, inverted:
    high effort → low risk signal.

    signal = 100 − avg_et   (E_t guaranteed [0, 100])
    Returns 0 if no data.
    """
    ets = [
        week_rows[w]['effort_score']
        for w in qualifying_weeks
        if w in week_rows and week_rows[w].get('effort_score') is not None
    ]
    if not ets:
        return 0.0
    return float(max(0.0, 100.0 - (sum(ets) / len(ets))))


def _signal_et_drop(week_rows, qualifying_weeks):
    """
    Percentage-point drop in E_t from oldest → newest qualifying week.
    Positive → effort fell (bad). Clipped at 0 so rising effort is neutral,
    not rewarding. E_t is guaranteed [0, 100] by weekly_metrics_calculator
    so no upper cap is needed.

    qualifying_weeks is descending, so index 0 is newest, -1 is oldest.
    """
    ets = [
        week_rows[w]['effort_score']
        for w in qualifying_weeks
        if w in week_rows and week_rows[w]['effort_score'] is not None
    ]
    if len(ets) < 2:
        return 0.0
    drop = ets[-1] - ets[0]   # oldest minus newest; positive when effort fell
    return float(max(0.0, drop))


# ══════════════════════════════════════════════════════════════
# 6. COMPOSITE RISK SCORE  →  integer [0, 100]
# ══════════════════════════════════════════════════════════════

def _compute_risk_score(week_rows, qualifying_weeks, current_week_row, class_at_et_ratio):
    """
    Weighted sum of nine sub-signals, each in [0, 100].
    Weights sum to 100 → result is in [0, 100].

    Returns (risk_score: int, sub_signals: dict).
    """
    W = WEIGHTS

    a = _signal_detention_risk(current_week_row)                      # convex
    b = _signal_assn_streak(week_rows, qualifying_weeks)
    c = _signal_quiz_streak(week_rows, qualifying_weeks)
    d = _signal_high_risk_streak(week_rows, qualifying_weeks)         # convex
    e = _signal_lag_penalty(week_rows, qualifying_weeks, class_at_et_ratio)
    f = _signal_avg_risk_score(week_rows, qualifying_weeks)
    g = _signal_avg_at(week_rows, qualifying_weeks)                   # inverted
    h = _signal_avg_et(week_rows, qualifying_weeks)                   # inverted
    i = _signal_et_drop(week_rows, qualifying_weeks)

    raw = (
        W['risk_of_detention'] * a +
        W['assn_streak']       * b +
        W['quiz_streak']       * c +
        W['high_risk_streak']  * d +
        W['lag_score_penalty'] * e +
        W['avg_risk_score_3w'] * f +
        W['avg_at_3w']         * g +
        W['avg_et_3w']         * h +
        W['et_drop']           * i
    ) / 100.0

    risk_score = int(round(max(0.0, min(raw, 100.0))))

    # Store raw detention value for diagnosis display (not the transformed one)
    a_raw = float(current_week_row.get('risk_of_detention') or 0.0) if current_week_row else 0.0
    sub_signals = dict(a=a_raw, b=b, c=c, d=d, e=e, f=f, g=g, h=h, i=i)
    return risk_score, sub_signals


# ══════════════════════════════════════════════════════════════
# 7. OVERRIDE CHECK
# ══════════════════════════════════════════════════════════════

def _check_override(current_week_row, week_rows, qualifying_weeks):
    """
    Hard-rule violations that force a Tier 1 flag regardless of percentile rank.
    Returns (is_override: bool, reasons: list[str]).
    """
    reasons = []

    if current_week_row:
        att = current_week_row.get('weekly_att_pct')
        if att is not None and float(att) <= OVERRIDE_ATT_PCT:
            reasons.append(f'Severe Absenteeism (att={att:.1f}%)')

    max_plag = _signal_plag(week_rows, qualifying_weeks)
    if max_plag > OVERRIDE_PLAG_PCT:
        reasons.append(f'Integrity Violation (plag={max_plag:.1f}%)')

    return bool(reasons), reasons


# ══════════════════════════════════════════════════════════════
# 8. TIER ASSIGNMENT
# ══════════════════════════════════════════════════════════════

def _assign_tier(percentile, risk_score, is_override):
    if is_override:
        return 'Tier 1 (Critical)'
    if percentile >= TIER1_PERCENTILE and risk_score > TIER1_ABS:
        return 'Tier 1 (Critical)'
    if percentile >= TIER2_PERCENTILE or risk_score > TIER2_ABS:
        return 'Tier 2 (High Risk)'
    return 'Tier 3 (Watch)'


# ══════════════════════════════════════════════════════════════
# 9. PERCENTILE HELPER
# ══════════════════════════════════════════════════════════════

def _percentile_rank(score, all_scores):
    """Fraction of class scoring strictly below this student, as [0, 100]."""
    if not all_scores:
        return 0.0
    return (sum(1 for s in all_scores if s < score) / len(all_scores)) * 100.0


# ══════════════════════════════════════════════════════════════
# 10. DIAGNOSIS STRING
# ══════════════════════════════════════════════════════════════

def _build_diagnosis(sub_signals, override_reasons, qualifying_weeks):
    """
    Human-readable explanation of what drove this flag.
    Override reasons always appear first. Soft signals only surface
    when they crossed a meaningful threshold.

    sub_signals keys:
        a = detention risk raw [0-100]           (displayed pre-transform)
        b = assignment streak signal [0-100]
        c = quiz streak signal [0-100]
        d = high-risk streak signal [0-100]      (convex; risk_score ≥ 50)
        e = lag penalty [0-100]
        f = avg risk_score over window [0-100]
        g = avg A_t signal [0-100]  (inverted; high g = low A_t)
        h = avg E_t signal [0-100]  (inverted; high h = low E_t)
        i = E_t drop [0-100]        (pp drop oldest → newest; clipped at 0)
    """
    parts = list(override_reasons)

    a = sub_signals['a']   # raw detention risk for readability
    b = sub_signals['b']
    c = sub_signals['c']
    d = sub_signals['d']
    e = sub_signals['e']
    f = sub_signals['f']
    g = sub_signals['g']
    h = sub_signals['h']
    i = sub_signals['i']

    n_weeks = len(qualifying_weeks)

    # Don't re-report what's already captured as an override reason
    if not override_reasons:
        if a >= 60:
            parts.append(f'Detention Risk ({a:.0f}/100)')

    if b >= 33:   # ≥1 consecutive missed-assignment week
        streak_count = round(b / 100 * STREAK_CAP)
        parts.append(f'Assignment Streak ({streak_count}w consecutive missed)')
    if c >= 33:   # ≥1 consecutive missed-quiz week
        streak_count = round(c / 100 * STREAK_CAP)
        parts.append(f'Quiz Streak ({streak_count}w consecutive missed)')
    if d >= 11:   # at least 1 week with risk_score ≥ 50  → (1/3)² × 100 ≈ 11
        high_weeks = round(d / 100 * STREAK_CAP)
        parts.append(f'Persistent High Risk ({high_weeks}w risk_score≥{HIGH_RISK_THRESHOLD})')
    if e >= 25:
        parts.append(f'Poor Effort–Performance Conversion ({e:.0f}/100)')
    if f >= 50:
        parts.append(f'Sustained Risk History (avg score={f:.0f} over {n_weeks}w)')
    if g >= 40:   # avg A_t ≤ 60
        avg_at = 100.0 - g
        parts.append(f'Low Academic Performance (avg={avg_at:.0f}/100 over {n_weeks}w)')
    if h >= 40:   # avg E_t ≤ 60
        avg_et = 100.0 - h
        parts.append(f'Low Effort (avg={avg_et:.0f}/100 over {n_weeks}w)')
    if i >= 15:
        parts.append(f'Effort Declining (−{i:.0f}pp over {n_weeks}w)')

    return ' | '.join(parts) if parts else 'Composite Risk'


# ══════════════════════════════════════════════════════════════
# 11. ESCALATION  —  streak of consecutive flagged weeks
# ══════════════════════════════════════════════════════════════

def _compute_escalation(flag_streaks, student_ids, flagged_this_week):
    """
    escalation_level = consecutive weeks the student has been flagged,
    including the current week.

    flag_streaks[sid] = count of consecutive prior flagged weeks
                        (from weekly_flags, computed before this run).

    Flagged this week  → escalation = prior_streak + 1
    Not flagged        → escalation = 0  (streak resets)

    Returns { student_id: escalation_level }.
    """
    return {
        sid: (flag_streaks.get(sid, 0) + 1) if sid in flagged_this_week else 0
        for sid in student_ids
    }


# ══════════════════════════════════════════════════════════════
# 12. MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════

def generate_weekly_triage(sem_week=None, semester=None):
    """
    Compute composite risk scores for all students and write:
      • weekly_flags   — top-20% per class + hard-rule override students
      • weekly_metrics — risk_score [0-100] + escalation_level

    Parameters
    ----------
    sem_week : int | None — override live sim week (used by calibrate scripts)
    semester : int | None — override live semester  (used by calibrate scripts)
    When both are None, reads live state from ClientSimState.
    """
    print("=== Composite Risk Scoring Engine (v3) ===")

    # ── Resolve week / semester ───────────────────────────────────────────────
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

    # ── Guard: grace period and exam weeks produce no flags ───────────────────
    if sem_week <= 3:
        print("  Grace period (week ≤ 3) — no flags generated.")
        return
    if sem_week in EXAM_WEEKS:
        print(f"  Exam week {sem_week} — no flags generated.")
        return

    # ── Students ──────────────────────────────────────────────────────────────
    students          = _fetch_students(sem_map)
    student_ids       = [s['student_id'] for s in students]
    student_class_map = {s['student_id']: s['class_id'] for s in students}
    student_name_map  = {s['student_id']: s['name']     for s in students}

    if not student_ids:
        print("  No students found.")
        return

    # ── Candidate window ──────────────────────────────────────────────────────
    candidate_weeks = _qualifying_window(sem_week)
    print(f"  candidate_window={candidate_weeks}")

    # ── Bulk data fetches (one query each) ────────────────────────────────────
    all_metric_windows = _fetch_metric_window(
        rep_semester, sem_week, candidate_weeks, student_ids
    )
    flag_streaks = _fetch_flag_streaks(rep_semester, sem_week, student_ids)

    # ── Class-level At/Et ratio for lag_penalty ───────────────────────────────
    class_ratios = _class_at_et_ratios(
        all_metric_windows, student_class_map, candidate_weeks
    )

    # ── Per-student scoring ───────────────────────────────────────────────────
    scored_students   = []
    override_students = []

    for stu in students:
        sid = stu['student_id']
        cid = stu['class_id']

        week_rows        = all_metric_windows.get(sid, {})
        current_week_row = week_rows.get(sem_week)

        # Per-student qualifying weeks (coverage check is per-student because
        # a student absent from the data in a given week differs from the class)
        qualifying_weeks = [
            w for w in candidate_weeks
            if w in week_rows and _week_is_covered(week_rows[w])
        ]

        is_override, override_reasons = _check_override(
            current_week_row, week_rows, qualifying_weeks
        )

        risk_score, sub_signals = _compute_risk_score(
            week_rows,
            qualifying_weeks,
            current_week_row,
            class_ratios.get(cid),
        )

        record = {
            'student_id':       sid,
            'class_id':         cid,
            'risk_score':       risk_score,
            'sub_signals':      sub_signals,
            'qualifying_weeks': qualifying_weeks,
            'override_reasons': override_reasons,
            'is_override':      is_override,
        }
        scored_students.append(record)
        if is_override:
            override_students.append(record)

    # ── Percentile ranking within each class ──────────────────────────────────
    class_scores = {}
    for rec in scored_students:
        class_scores.setdefault(rec['class_id'], []).append(rec['risk_score'])

    # ── Build flag list: top 20% per class ∪ override students ───────────────
    flag_student_ids = set()
    flags_to_write   = []

    for cid, scores_in_class in class_scores.items():
        class_students = [r for r in scored_students if r['class_id'] == cid]
        class_students.sort(key=lambda r: r['risk_score'], reverse=True)

        n_to_flag    = max(1, math.ceil(len(class_students) * 0.20))
        top_students = class_students[:n_to_flag]

        for rec in top_students:
            sid        = rec['student_id']
            percentile = _percentile_rank(rec['risk_score'], scores_in_class)
            tier       = _assign_tier(percentile, rec['risk_score'], rec['is_override'])
            diagnosis  = _build_diagnosis(
                rec['sub_signals'], rec['override_reasons'], rec['qualifying_weeks']
            )
            if sid not in flag_student_ids:
                flag_student_ids.add(sid)
                flags_to_write.append({
                    'student_id':    sid,
                    'class_id':      cid,
                    'risk_tier':     tier,
                    'urgency_score': rec['risk_score'],   # already [0, 100]
                    'diagnosis':     diagnosis,
                })

    # Add override students who fell outside the top-20% cut
    for rec in override_students:
        sid = rec['student_id']
        if sid not in flag_student_ids:
            cid       = rec['class_id']
            diagnosis = _build_diagnosis(
                rec['sub_signals'], rec['override_reasons'], rec['qualifying_weeks']
            )
            flag_student_ids.add(sid)
            flags_to_write.append({
                'student_id':    sid,
                'class_id':      cid,
                'risk_tier':     'Tier 1 (Critical)',
                'urgency_score': rec['risk_score'],
                'diagnosis':     diagnosis,
            })

    # ── Escalation: computed AFTER flag_student_ids is finalised ─────────────
    # escalation_level = consecutive weeks flagged including the current week.
    escalation_map = _compute_escalation(flag_streaks, student_ids, flag_student_ids)

    # ── Write weekly_flags ────────────────────────────────────────────────────
    if flags_to_write:
        WeeklyFlag.objects.bulk_create(
            [
                WeeklyFlag(
                    student_id    = f['student_id'],
                    class_id      = f['class_id'],
                    semester      = rep_semester,
                    sem_week      = sem_week,
                    risk_tier     = f['risk_tier'],
                    urgency_score = f['urgency_score'],
                    diagnosis     = f['diagnosis'],
                )
                for f in flags_to_write
            ],
            ignore_conflicts=True,
        )
        by_class = {}
        for f in flags_to_write:
            by_class[f['class_id']] = by_class.get(f['class_id'], 0) + 1
        print(
            f"  Flags written → {len(flags_to_write)} student(s)  "
            + ", ".join(f"{c}: {n}" for c, n in sorted(by_class.items()))
        )
    else:
        print("  No flags generated this week.")

    # ── Write risk_score + escalation_level → weekly_metrics ─────────────────
    risk_score_map = {r['student_id']: r['risk_score'] for r in scored_students}

    rows_to_update = []
    for wm in weekly_metrics.objects.filter(
        semester=rep_semester,
        sem_week=sem_week,
        student_id__in=student_ids,
    ):
        sid = wm.student_id
        if sid in risk_score_map:
            wm.risk_score       = risk_score_map[sid]           # int [0, 100]
            wm.escalation_level = escalation_map.get(sid, 0)   # int ≥ 0
            rows_to_update.append(wm)

    if rows_to_update:
        weekly_metrics.objects.bulk_update(
            rows_to_update, ['risk_score', 'escalation_level']
        )
        print(f"  weekly_metrics updated ({len(rows_to_update)} rows)")
    else:
        print("  No weekly_metrics rows found for this week.")

    print("=== Done ===")


# ── Standalone entry point ────────────────────────────────────────────────────
if __name__ == '__main__':
    import os
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project.settings')
    django.setup()
    generate_weekly_triage()
