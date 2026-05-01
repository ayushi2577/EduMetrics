# ============================================================
#  analysis_engine/weekly/weekly_metrics_calculator.py  (Django ORM version)
#
#  Computes three metrics per student per (teaching) week and writes them
#  to the weekly_metrics table in the analysis DB.
#
#  All mysql.connector calls replaced with Django ORM.
#  Client DB  → ClientXxx models  (routed to 'client_db')
#  Analysis DB → WeeklyMetrics model (routed to 'default')
#
#  ── Metrics ──────────────────────────────────────────────────────────────────
#  E_t  (effort_score)         — dynamic_score() with W_E weights, this-week signals
#  A_t  (academic_performance) — dynamic_score() with W_A weights, cumulative signals
#  risk_of_detention           — pressure-based score aimed at endterm (week 18)
# ============================================================

import math
import warnings

warnings.filterwarnings('ignore')

# ── Client DB models ──────────────────────────────────────────
from analysis_engine.client_models import (
    ClientSimState,
    ClientClass,
    ClientStudent,
    ClientAttendance,
    ClientAssignmentDefinition,
    ClientAssignmentSubmission,
    ClientQuizDefinition,
    ClientQuizSubmission,
    ClientLibraryVisit,
    ClientBookBorrow,
    ClientExamResult,
    ClientExamSchedule,
)

# ── Analysis DB models ────────────────────────────────────────
from analysis_engine.models import WeeklyMetrics


# ══════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════

MIDTERM_WEEK = 8
ENDTERM_WEEK = 18
EXAM_WEEKS   = {MIDTERM_WEEK, ENDTERM_WEEK}

# E_t base weights  (re-normalised dynamically when signals are absent)
W_E = dict(
    attendance          = 0.30,
    assignment_submit   = 0.20,
    clean_work          = 0.15,   # 100 - plagiarism_pct
    quiz_attempt        = 0.15,
    library_visits      = 0.10,
    book_borrows        = 0.10,
)

# A_t base weights
W_A = dict(
    quiz_score       = 0.50,
    assignment_score = 0.50,
)

# Library / borrow caps  (individual visits/borrows that map to 100 %)
LIBRARY_CAP = 10   # visits per week
BORROW_CAP  =  5   # borrows per week

# Detention risk constants
ATTENDANCE_TARGET = 0.75    # 75% rule


# ══════════════════════════════════════════════════════════════
# 1. SCORING ENGINE
# ══════════════════════════════════════════════════════════════

def dynamic_score(values: dict, base_weights: dict):
    """
    Parameters
    ----------
    values       : {component: value_0_to_100 | None}
                   None means the signal was absent this week.
    base_weights : {component: weight}  -- same keys as values

    Returns
    -------
    score        : weighted score (0-100) using only present signals,
                   re-normalised so active weights sum to 1.
                   Returns math.nan when no signal is present at all.
    coverage     : sum(active base weights) / sum(all base weights) in [0, 1]
    """
    total_base = sum(base_weights.values())
    active = {k: v for k, v in values.items() if v is not None}

    if not active:
        return math.nan, 0.0

    active_weight_sum = sum(base_weights[k] for k in active)
    score    = sum(base_weights[k] / active_weight_sum * active[k] for k in active)
    coverage = active_weight_sum / total_base
    return round(score, 4), round(coverage, 4)


# ══════════════════════════════════════════════════════════════
# 2. CONTEXT
# ══════════════════════════════════════════════════════════════

def _get_sim_context():
    state = ClientSimState.objects.using('client_db').get(id=1)
    gw    = state.current_week

    if gw <= 18:
        sem_week, slot = gw, 'odd'
    else:
        sem_week, slot = gw - 18, 'even'

    classes = list(ClientClass.objects.using('client_db').all())
    sem_map = {
        cls.class_id: (cls.odd_sem if slot == 'odd' else cls.even_sem)
        for cls in classes
    }

    return {
        'global_week': gw,
        'sem_week':    sem_week,
        'slot':        slot,
        'sem_map':     sem_map,
        'classes':     classes,
    }


# ══════════════════════════════════════════════════════════════
# 3. CLIENT DB FETCHERS (ORM)
# ══════════════════════════════════════════════════════════════

def _fetch_students(sem_map):
    class_ids = list(sem_map.keys())
    return list(
        ClientStudent.objects.using('client_db')
        .filter(class_id__in=class_ids)
        .values('student_id', 'class_id')
    )


def _fetch_attendance_this_week(sem_map, sem_week):
    """Attendance for the CURRENT week only — used for weekly_att_pct and E_t."""
    semesters = list(set(sem_map.values()))
    qs = ClientAttendance.objects.using('client_db').filter(
        semester__in=semesters,
        week=sem_week,
    )
    return [
        {
            'student_id':    r['student_id'],
            'present':       r['present'],
            'lectures_held': r['lectures_held'],
        }
        for r in qs.values('student_id', 'present', 'lectures_held')
    ]


def _fetch_attendance_cumulative(sem_map, sem_week):
    """
    All attendance rows weeks 1..sem_week (inclusive) — used for
    overall_att_pct (detention risk). Excludes exam weeks.
    """
    semesters = list(set(sem_map.values()))
    weeks = [w for w in range(1, sem_week + 1) if w not in EXAM_WEEKS]
    if not weeks:
        return []
    qs = ClientAttendance.objects.using('client_db').filter(
        semester__in=semesters,
        week__in=weeks,
    )
    return [
        {
            'student_id':    r['student_id'],
            'present':       r['present'],
            'lectures_held': r['lectures_held'],
        }
        for r in qs.values('student_id', 'present', 'lectures_held')
    ]


def _fetch_quizzes_this_week(sem_map, sem_week):
    """
    Quiz submissions for quizzes scheduled in the CURRENT week.
    Used for quiz_attempt_rate (E_t).
    """
    semesters = list(set(sem_map.values()))
    defn_qs = ClientQuizDefinition.objects.using('client_db').filter(
        semester__in=semesters,
        scheduled_week=sem_week,
    ).values('quiz_id')
    quiz_ids = [d['quiz_id'] for d in defn_qs]
    if not quiz_ids:
        return []
    return [
        {
            'student_id': r['student_id'],
            'attempted':  r['attempted'],
            'score_pct':  r['score_pct'],
        }
        for r in ClientQuizSubmission.objects.using('client_db')
        .filter(quiz_id__in=quiz_ids)
        .values('student_id', 'attempted', 'score_pct')
    ]


def _fetch_quizzes_cumulative(sem_map, sem_week):
    """
    All attempted quiz submissions up to and including sem_week — for cumulative A_t.
    """
    semesters = list(set(sem_map.values()))
    weeks = [w for w in range(1, sem_week + 1) if w not in EXAM_WEEKS]
    if not weeks:
        return []
    defn_qs = ClientQuizDefinition.objects.using('client_db').filter(
        semester__in=semesters,
        scheduled_week__in=weeks,
    ).values('quiz_id')
    quiz_ids = [d['quiz_id'] for d in defn_qs]
    if not quiz_ids:
        return []
    return [
        {
            'student_id': r['student_id'],
            'score_pct':  r['score_pct'],
        }
        for r in ClientQuizSubmission.objects.using('client_db')
        .filter(quiz_id__in=quiz_ids, attempted=True)
        .values('student_id', 'score_pct')
    ]


def _fetch_assignments_this_week(sem_map, sem_week):
    """
    Assignment submissions due in the CURRENT week.
    Used for assn_submit_rate, assn_quality_pct, assn_plagiarism_pct (E_t).
    """
    semesters = list(set(sem_map.values()))
    defn_qs = ClientAssignmentDefinition.objects.using('client_db').filter(
        semester__in=semesters,
        due_week=sem_week,
    ).values('assignment_id', 'max_marks')
    defn_map = {d['assignment_id']: d['max_marks'] for d in defn_qs}
    if not defn_map:
        return []
    rows = []
    for r in (
        ClientAssignmentSubmission.objects.using('client_db')
        .filter(assignment_id__in=list(defn_map.keys()))
        .values('student_id', 'assignment_id', 'marks_obtained', 'quality_pct', 'plagiarism_pct')
    ):
        rows.append({
            'student_id':     r['student_id'],
            'marks_obtained': r['marks_obtained'],
            'quality_pct':    r['quality_pct'],
            'plagiarism_pct': r['plagiarism_pct'],
            'max_marks':      defn_map[r['assignment_id']],
            'submitted':      1 if (r['marks_obtained'] or 0) > 0 else 0,
        })
    return rows


def _fetch_assignments_cumulative(sem_map, sem_week):
    """
    All submitted assignments (marks_obtained > 0) up to sem_week — for cumulative A_t.
    """
    semesters = list(set(sem_map.values()))
    weeks = [w for w in range(1, sem_week + 1) if w not in EXAM_WEEKS]
    if not weeks:
        return []
    defn_qs = ClientAssignmentDefinition.objects.using('client_db').filter(
        semester__in=semesters,
        due_week__in=weeks,
    ).values('assignment_id', 'max_marks')
    defn_map = {d['assignment_id']: d['max_marks'] for d in defn_qs}
    if not defn_map:
        return []
    rows = []
    for r in (
        ClientAssignmentSubmission.objects.using('client_db')
        .filter(assignment_id__in=list(defn_map.keys()), marks_obtained__gt=0)
        .values('student_id', 'assignment_id', 'marks_obtained')
    ):
        rows.append({
            'student_id':     r['student_id'],
            'marks_obtained': r['marks_obtained'],
            'max_marks':      defn_map[r['assignment_id']],
        })
    return rows


def _fetch_library_this_week(sem_map, sem_week):
    semesters = list(set(sem_map.values()))
    return [
        {
            'student_id':      r['student_id'],
            'physical_visits': r['physical_visits'],
        }
        for r in ClientLibraryVisit.objects.using('client_db')
        .filter(semester__in=semesters, week=sem_week)
        .values('student_id', 'physical_visits')
    ]


def _fetch_borrows_this_week(sem_map, sem_week):
    """Count of book borrows initiated in the current week, grouped by student."""
    semesters = list(set(sem_map.values()))
    from django.db.models import Count
    qs = (
        ClientBookBorrow.objects.using('client_db')
        .filter(semester__in=semesters, borrow_week=sem_week)
        .values('student_id')
        .annotate(borrow_count=Count('borrow_id'))
    )
    return [{'student_id': r['student_id'], 'borrow_count': r['borrow_count']} for r in qs]


def _fetch_midterm_results(sem_map):
    semesters = list(set(sem_map.values()))
    sched_ids = list(
        ClientExamSchedule.objects.using('client_db')
        .filter(semester__in=semesters, exam_type='midterm')
        .values_list('schedule_id', flat=True)
    )
    if not sched_ids:
        return []
    from django.db.models import Avg
    return list(
        ClientExamResult.objects.using('client_db')
        .filter(schedule_id__in=sched_ids)
        .values('student_id')
        .annotate(score_pct=Avg('score_pct'))
    )


def _fetch_prior_endterm_avg(sem_map):
    """Average endterm score from the immediately preceding semester per student."""
    prior_sems = [s - 1 for s in set(sem_map.values()) if s > 1]
    if not prior_sems:
        return []
    sched_ids = list(
        ClientExamSchedule.objects.using('client_db')
        .filter(semester__in=prior_sems, exam_type='endterm')
        .values_list('schedule_id', flat=True)
    )
    if not sched_ids:
        return []
    from django.db.models import Avg
    return list(
        ClientExamResult.objects.using('client_db')
        .filter(schedule_id__in=sched_ids)
        .values('student_id')
        .annotate(score_pct=Avg('score_pct'))
    )


def _fetch_recent_ap(sem_map, sem_week):
    """
    Up to 2 most recent non-NULL academic_performance values from the
    analysis DB — used as the A_t fallback when no quiz/assignment data exists.
    Returns { student_id: [(sem_week, value), ...] } sorted desc.
    """
    semesters = list(set(sem_map.values()))
    lookback  = [w for w in range(sem_week - 1, 0, -1) if w not in EXAM_WEEKS][:2]
    if not lookback:
        return {}

    qs = WeeklyMetrics.objects.filter(
        semester__in=semesters,
        sem_week__in=lookback,
        academic_performance__isnull=False,
    ).values('student_id', 'sem_week', 'academic_performance')

    result = {}
    for r in qs:
        result.setdefault(r['student_id'], []).append(
            (r['sem_week'], float(r['academic_performance']))
        )
    for sid in result:
        result[sid].sort(key=lambda x: x[0], reverse=True)
    return result


# ══════════════════════════════════════════════════════════════
# 4. GROUPING HELPER
# ══════════════════════════════════════════════════════════════

def _by_student(rows):
    out = {}
    for r in rows:
        out.setdefault(r['student_id'], []).append(r)
    return out


# ══════════════════════════════════════════════════════════════
# 5. E_t  —  EFFORT SCORE
#    All six signals are this-week snapshots (not rolling windows).
# ══════════════════════════════════════════════════════════════

def _compute_Et(
    sid,
    att_this_week,
    quiz_this_week,
    assn_this_week,
    lib_this_week,
    borrows_this_week,
    has_prior_semester,
    ap,
):
    """
    Returns (effort_score, coverage,
             weekly_att_pct, quiz_attempt_rate, assn_submit_rate,
             assn_quality_pct, assn_plagiarism_pct,
             library_visits, book_borrows)

    Returns all-None tuple if effort is not yet calculable.
    """
    if not has_prior_semester and ap is None:
        return (None,) * 9

    # ── Attendance ───────────────────────────────────────────────────────────
    if att_this_week:
        total_present = sum(float(r.get('present') or 0) for r in att_this_week)
        total_held    = sum(float(r.get('lectures_held') or 0) for r in att_this_week)
        att_pct       = (total_present / total_held * 100) if total_held > 0 else None
        weekly_att_pct_store = round(att_pct, 2) if att_pct is not None else None
    else:
        att_pct              = None
        weekly_att_pct_store = None

    # ── Quiz attempt rate ─────────────────────────────────────────────────────
    if quiz_this_week:
        n_attempted       = sum(1 for r in quiz_this_week if r.get('attempted'))
        quiz_attempt_rate = n_attempted / len(quiz_this_week)
        quiz_att_pct      = quiz_attempt_rate * 100
    else:
        quiz_attempt_rate = None
        quiz_att_pct      = None

    # ── Assignment submission rate + quality + plagiarism ─────────────────────
    if assn_this_week:
        submitted     = [r for r in assn_this_week if r.get('submitted')]
        assn_sub_rate = len(submitted) / len(assn_this_week)
        assn_sub_pct  = assn_sub_rate * 100

        if submitted:
            qualities = [float(r.get('quality_pct') or 0) for r in submitted
                         if r.get('quality_pct') is not None]
            assn_quality_pct = sum(qualities) / len(qualities) if qualities else None

            plags = [float(r.get('plagiarism_pct') or 0) for r in assn_this_week]
            assn_plag_pct  = max(plags) if plags else 0.0
            clean_work_pct = max(0.0, 100.0 - assn_plag_pct) if assn_plag_pct is not None else None
        else:
            assn_quality_pct = None
            assn_plag_pct    = None
            clean_work_pct   = None
    else:
        assn_sub_rate    = None
        assn_sub_pct     = None
        assn_quality_pct = None
        assn_plag_pct    = None
        clean_work_pct   = None

    # ── Library visits ────────────────────────────────────────────────────────
    lib_count = sum(int(r.get('physical_visits') or 0) for r in lib_this_week)
    lib_pct   = min(lib_count, LIBRARY_CAP) / LIBRARY_CAP * 100

    # ── Book borrows ──────────────────────────────────────────────────────────
    borrow_count = sum(int(r.get('borrow_count') or 0) for r in borrows_this_week)
    borrow_pct   = min(borrow_count, BORROW_CAP) / BORROW_CAP * 100

    # ── dynamic_score() ───────────────────────────────────────────────────────
    e_values = {
        'attendance':        att_pct,
        'assignment_submit': assn_sub_pct,
        'clean_work':        clean_work_pct,
        'quiz_attempt':      quiz_att_pct,
        'library_visits':    lib_pct,
        'book_borrows':      borrow_pct,
    }
    effort_score, coverage = dynamic_score(e_values, W_E)

    return (
        effort_score,
        coverage,
        weekly_att_pct_store,
        round(quiz_attempt_rate, 4) if quiz_attempt_rate is not None else None,
        round(assn_sub_rate,     4) if assn_sub_rate     is not None else None,
        round(assn_quality_pct,  2) if assn_quality_pct  is not None else None,
        round(assn_plag_pct,     2) if assn_plag_pct     is not None else None,
        lib_count,
        borrow_count,
    )


# ══════════════════════════════════════════════════════════════
# 6. A_t  —  ACADEMIC PERFORMANCE
# ══════════════════════════════════════════════════════════════

def _compute_At(
    sid,
    sem_week,
    quiz_cumulative,
    assn_cumulative,
    midterm_score,
    prior_endterm,
    recent_ap,
):
    """
    Returns (academic_performance, quiz_avg_pct, assn_avg_pct, midterm_score_pct)
    Any value may be None.
    """
    if sem_week == MIDTERM_WEEK:
        return None, None, None, None

    # ── Cumulative quiz average ───────────────────────────────────────────────
    quiz_avg = (
        sum(float(r['score_pct'] or 0) for r in quiz_cumulative) / len(quiz_cumulative)
        if quiz_cumulative else None
    )

    # ── Cumulative assignment average ─────────────────────────────────────────
    assn_avg = None
    if assn_cumulative:
        pcts = []
        for r in assn_cumulative:
            mm = float(r.get('max_marks') or 0)
            mo = float(r.get('marks_obtained') or 0)
            if mm > 0:
                pcts.append(mo / mm * 100)
        assn_avg = sum(pcts) / len(pcts) if pcts else None

    has_quiz = quiz_avg is not None
    has_assn = assn_avg is not None

    # ── Active weeks: at least one signal present → dynamic_score ────────────
    if has_quiz or has_assn:
        a_values = {
            'quiz_score':       quiz_avg,
            'assignment_score': assn_avg,
        }
        ap_base, _ = dynamic_score(a_values, W_A)

        # Blend in midterm once it lands: 70% quiz/assn score + 30% midterm
        if midterm_score is not None and ap_base is not None:
            ap = round(0.70 * ap_base + 0.30 * midterm_score, 2)
        else:
            ap = ap_base

        return (
            ap,
            round(quiz_avg, 2) if has_quiz else None,
            round(assn_avg, 2) if has_assn else None,
            midterm_score,
        )

    # ── No quiz or assignment data yet ────────────────────────────────────────

    # Fallback 1: prior semester endterm
    if prior_endterm is not None:
        return round(prior_endterm, 2), None, None, midterm_score

    # Fallback 2: weighted avg of last 2 weeks from analysis DB
    if recent_ap:
        ap = (
            round((2 * recent_ap[0][1] + 1 * recent_ap[1][1]) / 3, 2)
            if len(recent_ap) >= 2
            else round(recent_ap[0][1], 2)
        )
        return ap, None, None, midterm_score

    # Fallback 3: truly initial, nothing available
    return None, None, None, None


# ══════════════════════════════════════════════════════════════
# 7. RISK OF DETENTION
# ══════════════════════════════════════════════════════════════

def _pressure(attended: int, held: int, remaining: int):
    """
    Fraction of remaining lectures the student MUST attend to reach 75% overall.

    Returns:
        0.0        — already safe (no pressure)
        0.0 – 1.0  — partial pressure
        > 1.0      — mathematically impossible (certain detention)
        None       — no remaining lectures (exam week or beyond)
    """
    if remaining <= 0:
        return None

    needed = ATTENDANCE_TARGET * (held + remaining) - attended

    if needed <= 0:
        return 0.0

    return needed / remaining


def _pressure_to_risk(p) -> float:
    """
    Map a pressure ratio p → risk_of_detention score in [0, 100].

    Piecewise linear scale:
        p <= 0          →   0
        0  < p <= 0.50  →   0 – 40   (low-moderate)
        0.50 < p <= 0.75→  40 – 60   (moderate)
        0.75 < p <= 1.0 →  60 – 90   (high risk)
        p  > 1.0        → 100        (certain detention)
    """
    if p is None:
        return None

    if p <= 0:
        return 0.0

    if p > 1.0:
        return 100.0

    if p <= 0.50:
        return round(p / 0.50 * 40.0, 2)

    if p <= 0.75:
        return round(40.0 + (p - 0.50) / 0.25 * 20.0, 2)

    # p in (0.75, 1.0]
    return round(60.0 + (p - 0.75) / 0.25 * 30.0, 2)


def _compute_detention_risk(total_present, total_held, sem_week):
    """
    Compute overall_att_pct and risk_of_detention for a single student.

    Remaining lectures to the endterm are estimated from the average lectures
    held per teaching week so far — this naturally accounts for the actual
    number of subjects without needing a separate subject-count query.

    Returns (overall_att_pct, risk_of_detention) — both None if no data.
    """
    if total_held <= 0:
        return None, None

    overall_att_pct = round(total_present / total_held * 100.0, 2)

    if sem_week >= 17:
        risk_end = 100.0 if overall_att_pct < 75.0 else 0.0
        return overall_att_pct, risk_end

    # Teaching weeks elapsed so far (excluding exam weeks)
    weeks_so_far = len([w for w in range(1, sem_week + 1) if w not in EXAM_WEEKS])

    # Average lectures held per teaching week (reflects real subject count + cancellations)
    avg_held_per_week = total_held / weeks_so_far if weeks_so_far > 0 else 0

    # Remaining teaching weeks to endterm
    weeks_left_to_end = [
        w for w in range(sem_week + 1, ENDTERM_WEEK+1)
        if w not in EXAM_WEEKS
    ]
    remaining_end = round(avg_held_per_week * len(weeks_left_to_end))

    p_end    = _pressure(int(total_present), int(total_held), remaining_end)
    risk_end = _pressure_to_risk(p_end)

    return overall_att_pct, risk_end


# ══════════════════════════════════════════════════════════════
# 8. MAIN FUNCTION
# ══════════════════════════════════════════════════════════════

def run(sem_week=None, semester=None):
    print('  [weekly_metrics] Starting...')

    # when we eventually replace calibrate_analysis_db.py
    if not(sem_week or semester):
        ctx          = _get_sim_context()
        sem_week = ctx['sem_week']
        sem_map      = ctx['sem_map']
        rep_semester = next(iter(sem_map.values()))
    else:
        rep_semester=semester
        classes = list(ClientClass.objects.using('client_db').all())
        # this part could be wrong
        sem_map = {
        cls.class_id: (cls.odd_sem if semester == 1 else cls.even_sem)
        for cls in classes
        }
        
    print(f'  sem_week={sem_week}  semester={rep_semester}')

    # ── Exam week: write NULL rows to preserve the timeline ───────────────────
    if sem_week in EXAM_WEEKS:
        print(f'  [weekly_metrics] Exam week {sem_week} — writing NULL rows.')
        students  = _fetch_students(sem_map)
        to_create = []
        to_update = []
        existing  = {
            wm.student_id: wm
            for wm in WeeklyMetrics.objects.filter(
                semester=rep_semester,
                sem_week=sem_week,
                student_id__in=[s['student_id'] for s in students],
            )
        }
        null_fields = dict(
            effort_score=None, weekly_att_pct=None, quiz_attempt_rate=None,
            assn_submit_rate=None, assn_quality_pct=None, assn_plagiarism_pct=None,
            library_visits=None, book_borrows=None,
            academic_performance=None, quiz_avg_pct=None, assn_avg_pct=None,
            midterm_score_pct=None, risk_of_detention=None, overall_att_pct=None,
        )
        for stu in students:
            sid = stu['student_id']
            if sid in existing:
                obj = existing[sid]
                for k, v in null_fields.items():
                    setattr(obj, k, v)
                to_update.append(obj)
            else:
                to_create.append(WeeklyMetrics(
                    student_id=sid, class_id=stu['class_id'],
                    semester=rep_semester, sem_week=sem_week,
                    **null_fields,
                ))
        if to_create:
            WeeklyMetrics.objects.bulk_create(to_create)
        if to_update:
            WeeklyMetrics.objects.bulk_update(to_update, list(null_fields.keys()))
        print(f'  [weekly_metrics] Done — {len(students)} NULL rows written.')
        return

    # ── Bulk fetch ────────────────────────────────────────────────────────────
    students       = _fetch_students(sem_map)
    att_week       = _fetch_attendance_this_week(sem_map, sem_week)
    att_cumul      = _fetch_attendance_cumulative(sem_map, sem_week)
    quiz_week      = _fetch_quizzes_this_week(sem_map, sem_week)
    quiz_cumul     = _fetch_quizzes_cumulative(sem_map, sem_week)
    assn_week      = _fetch_assignments_this_week(sem_map, sem_week)
    assn_cumul     = _fetch_assignments_cumulative(sem_map, sem_week)
    lib_week       = _fetch_library_this_week(sem_map, sem_week)
    borrows_week   = _fetch_borrows_this_week(sem_map, sem_week)
    midterm_rows   = _fetch_midterm_results(sem_map)
    prior_end_rows = _fetch_prior_endterm_avg(sem_map)
    recent_ap_map  = _fetch_recent_ap(sem_map, sem_week)

    # ── Index by student ──────────────────────────────────────────────────────
    att_wk_by_stu    = _by_student(att_week)
    att_cu_by_stu    = _by_student(att_cumul)
    quiz_wk_by_stu   = _by_student(quiz_week)
    quiz_cu_by_stu   = _by_student(quiz_cumul)
    assn_wk_by_stu   = _by_student(assn_week)
    assn_cu_by_stu   = _by_student(assn_cumul)
    lib_wk_by_stu    = _by_student(lib_week)
    borrows_by_stu   = _by_student(borrows_week)
    midterm_by_stu   = {r['student_id']: float(r['score_pct'] or 0) for r in midterm_rows}
    prior_end_by_stu = {r['student_id']: float(r['score_pct'] or 0) for r in prior_end_rows}
    students_with_prior = set(prior_end_by_stu.keys())

    # ── Per-student computation ───────────────────────────────────────────────
    to_create = []
    to_update = []
    existing  = {
        wm.student_id: wm
        for wm in WeeklyMetrics.objects.filter(
            semester=rep_semester,
            sem_week=sem_week,
            student_id__in=[s['student_id'] for s in students],
        )
    }

    for stu in students:
        sid = stu['student_id']
        cid = stu['class_id']

        # ── A_t ──────────────────────────────────────────────────────────────
        ap, quiz_avg, assn_avg, midterm_pct = _compute_At(
            sid, sem_week,
            quiz_cu_by_stu.get(sid, []),
            assn_cu_by_stu.get(sid, []),
            midterm_by_stu.get(sid),
            prior_end_by_stu.get(sid),
            recent_ap_map.get(sid, []),
        )

        # ── E_t ──────────────────────────────────────────────────────────────
        (effort_score, _coverage,
         weekly_att_pct, quiz_attempt_rate, assn_submit_rate,
         assn_quality_pct, assn_plagiarism_pct,
         lib_visits, book_borrows) = _compute_Et(
            sid,
            att_wk_by_stu.get(sid, []),
            quiz_wk_by_stu.get(sid, []),
            assn_wk_by_stu.get(sid, []),
            lib_wk_by_stu.get(sid, []),
            borrows_by_stu.get(sid, []),
            sid in students_with_prior,
            ap,
        )

        # ── Overall attendance (cumulative) for detention risk ────────────────
        cum_att       = att_cu_by_stu.get(sid, [])
        total_present = sum(float(r.get('present')      or 0) for r in cum_att)
        total_held    = sum(float(r.get('lectures_held') or 0) for r in cum_att)

        overall_att_pct, risk_detention = _compute_detention_risk(
            total_present, total_held, sem_week,
        )

        fields = dict(
            class_id             = cid,
            effort_score         = effort_score,
            weekly_att_pct       = weekly_att_pct,
            quiz_attempt_rate    = quiz_attempt_rate,
            assn_submit_rate     = assn_submit_rate,
            assn_quality_pct     = assn_quality_pct,
            assn_plagiarism_pct  = assn_plagiarism_pct,
            library_visits       = lib_visits   if lib_visits   is not None else 0,
            book_borrows         = book_borrows if book_borrows is not None else 0,
            academic_performance = ap,
            quiz_avg_pct         = quiz_avg,
            assn_avg_pct         = assn_avg,
            midterm_score_pct    = midterm_pct,
            risk_of_detention    = risk_detention,
            overall_att_pct      = overall_att_pct,
        )

        if sid in existing:
            obj = existing[sid]
            for k, v in fields.items():
                setattr(obj, k, v)
            to_update.append(obj)
        else:
            to_create.append(WeeklyMetrics(
                student_id=sid, semester=rep_semester, sem_week=sem_week,
                **fields,
            ))

    if to_create:
        WeeklyMetrics.objects.bulk_create(to_create)
    if to_update:
        WeeklyMetrics.objects.bulk_update(to_update, list(fields.keys()))

    computed = sum(
        1 for wm in (to_create + to_update)
        if wm.effort_score is not None or wm.academic_performance is not None
    )
    print(
        f'  [weekly_metrics] Done — {len(students)} students written, '
        f'{computed} with at least one metric computed '
        f'(sem {rep_semester}, week {sem_week})'
    )


if __name__ == '__main__':
    import django
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project.settings')
    django.setup()
    run()