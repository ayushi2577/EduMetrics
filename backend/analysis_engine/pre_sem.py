# ============================================================
#  analysis_engine/pre_sem/pre_sem.py  (Django ORM version)
#
#  Runs at week 1 of the EVEN semester.
#  Uses Random Forest (JSON) to predict at-risk students.
#  Writes results to pre_sem_watchlist.
#
#  All mysql.connector calls replaced with Django ORM.
#  Client DB  → ClientXxx models (routed to 'client_db')
#  Analysis DB → WeeklyMetrics, InterventionLog, SubjectDifficulty,
#                PreSemWatchlist models (routed to 'default')
# ============================================================

import os
import json
import warnings
import traceback

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ── Client DB models ──────────────────────────────────────────
from analysis_engine.client_models import (
    ClientStudent,
    ClientClass,
    ClientClassSubject,
    ClientSubject,
    ClientExamResult,
    ClientExamSchedule,
)

# ── Analysis DB models ────────────────────────────────────────
# These must exist in analysis_engine/models.py
from analysis_engine.models import (
    WeeklyMetrics,
    SubjectDifficulty,
    PreSemWatchlist,
)


# ══════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════

_HERE       = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH  = os.getenv(
    'PRE_SEM_MODEL_PATH',
    os.path.join(_HERE, 'models', 'student_risk_model.json'),
)
FEATURE_COLS              = ['att_rate', 'assn_rate', 'max_plagiarism', 'exam_avg', 'escalation_level']
HARD_PASS_RATE_THRESHOLD  = 0.60

# Sentinel used by sklearn Decision Trees for leaf nodes
_LEAF_FLAG = -1


# ══════════════════════════════════════════════════════════════
# 1. CURRENT SEMESTER
# ══════════════════════════════════════════════════════════════

def _get_current_semester():
    """
    Read the MAX semester from weekly_metrics (analysis DB).
    That is the semester we just finished.
    """
    from django.db.models import Max
    result = WeeklyMetrics.objects.aggregate(sem=Max('semester'))
    return result['sem']   # None if no data yet


# ══════════════════════════════════════════════════════════════
# 2. FEATURE EXTRACTION
# ══════════════════════════════════════════════════════════════

def _pull_student_features(completed_semester):
    """
    Build the 5-feature DataFrame for every student who was active in
    completed_semester.

    att_rate, assn_rate, max_plagiarism → from WeeklyMetrics (analysis DB)
    exam_avg                            → avg(midterm, endterm) from client DB
    escalation_level                    → latest row in InterventionLog
    """

    # ── A. Attendance rate, assignment rate, plagiarism (analysis DB) ─────────
    from django.db.models import Avg, Max as DMax

    metrics_qs = (
        WeeklyMetrics.objects
        .filter(semester=completed_semester)
        .values('student_id', 'class_id')
        .annotate(
            att_rate       = Avg('overall_att_pct'),
            assn_rate      = Avg('assn_submit_rate'),
            max_plagiarism = DMax('assn_plagiarism_pct'),
        )
    )

    if not metrics_qs.exists():
        return pd.DataFrame()

    df = pd.DataFrame(list(metrics_qs))
    for col in ['att_rate', 'assn_rate', 'max_plagiarism']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # ── B. exam_avg = avg(midterm, endterm) from client DB ────────────────────
    #
    #  Pull all exam_results for the completed semester, compute per-student
    #  per-exam-type average, then average the two types equally.
    #
    sched_qs = ClientExamSchedule.objects.using('client_db').filter(
        exam_type__in=['midterm', 'endterm'],
        # semester=completed_semester,  # uncomment if your schema has this column
    ).values('schedule_id', 'exam_type')
    sched_map = {s['schedule_id']: s['exam_type'] for s in sched_qs}

    if sched_map:
        result_qs = ClientExamResult.objects.using('client_db').filter(
            schedule_id__in=list(sched_map.keys()),
            score_pct__isnull=False,
        ).values('student_id', 'schedule_id', 'score_pct')

        exam_rows = [
            {
                'student_id': r['student_id'],
                'exam_type':  sched_map[r['schedule_id']],
                'score_pct':  float(r['score_pct']),
            }
            for r in result_qs
        ]

        if exam_rows:
            exam_df = pd.DataFrame(exam_rows)
            exam_df['score_pct'] = pd.to_numeric(exam_df['score_pct'], errors='coerce')

            # Average per student per exam type, then average the two types
            exam_pivot = (
                exam_df
                .groupby(['student_id', 'exam_type'])['score_pct']
                .mean()
                .unstack('exam_type')
                .reset_index()
            )
            exam_pivot.columns.name = None
            for col in ['midterm', 'endterm']:
                if col not in exam_pivot.columns:
                    exam_pivot[col] = np.nan
            exam_pivot['exam_avg'] = exam_pivot[['midterm', 'endterm']].mean(axis=1, skipna=True)
            exam_pivot = exam_pivot[['student_id', 'exam_avg']]
            df = df.merge(exam_pivot, on='student_id', how='left')
        else:
            print(f"  [pre_sem] WARNING — no exam results found for semester {completed_semester}.")
            df['exam_avg'] = np.nan
    else:
        df['exam_avg'] = np.nan

    df['exam_avg'] = pd.to_numeric(df['exam_avg'], errors='coerce')

    # ── C. Escalation level (analysis DB — latest row per student) ────────────
    esc_qs = (
        WeeklyMetrics.objects
        .filter(semester=completed_semester)
        .order_by('student_id', '-sem_week')
        .values('student_id', 'escalation_level')
    )
    esc_map = {}
    for row in esc_qs:
        if row['student_id'] not in esc_map:   # first row = latest week
            esc_map[row['student_id']] = row['escalation_level'] or 0

    df['escalation_level'] = df['student_id'].map(esc_map).fillna(0).astype(int)

    # ── D. Fill missing values ────────────────────────────────────────────────
    df.fillna({
        'att_rate':       1.0,
        'assn_rate':      1.0,
        'max_plagiarism': 0.0,
        'exam_avg':       75.0,
    }, inplace=True)

    return df


# ══════════════════════════════════════════════════════════════
# 3. SUBJECT DIFFICULTY
# ══════════════════════════════════════════════════════════════

def _compute_and_cache_subject_difficulty(completed_semester):
    """
    Reads difficulty directly from ClientSubject.difficulty for next semester's subjects.
    Writes results to SubjectDifficulty (analysis DB).
    Returns: { subject_id → difficulty_label }
    """
    subj_qs = ClientSubject.objects.using('client_db').filter(
        semester=completed_semester + 1
    ).values('subject_id', 'difficulty')

    if not subj_qs.exists():
        print("  [pre_sem] No subjects found for next semester — skipping subject difficulty.")
        return {}

    difficulty_map = {}
    for s in subj_qs:
        raw = (s['difficulty'] or 'medium').lower()
        if   'hard' in raw: label = 'hard'
        elif 'easy' in raw: label = 'easy'
        else:               label = 'medium'

        difficulty_map[s['subject_id']] = label

        SubjectDifficulty.objects.update_or_create(
            subject_id=s['subject_id'],
            semester=completed_semester,
            defaults={
                'total_students':   0,
                'students_passed':  0,
                'pass_rate':        1.0 if label == 'easy' else (0.7 if label == 'medium' else 0.5),
                'difficulty_label': label,
            }
        )

    hard_count = sum(1 for v in difficulty_map.values() if v == 'hard')
    print(f"  [pre_sem] Subject difficulty cached — {len(difficulty_map)} subjects, {hard_count} hard.")
    return difficulty_map

def _count_hard_subjects_per_student(next_semester, difficulty_map):
    """
    Count how many hard subjects each student is enrolled in for next_semester.
    Returns: { student_id → hard_subject_count }
    """
    if not difficulty_map:
        return {}

    # Get subjects for next_semester
    subj_qs = ClientSubject.objects.using('client_db').filter(
        semester=next_semester
    ).values('subject_id')
    next_sem_subjects = {s['subject_id'] for s in subj_qs}

    # Get class_subjects for those subjects
    cs_qs = ClientClassSubject.objects.using('client_db').filter(
        subject_id__in=next_sem_subjects
    ).values('class_id', 'subject_id')
    class_subjects = list(cs_qs)

    # Get students and their class
    student_qs = ClientStudent.objects.using('client_db').filter(
        class_id__in=[cs['class_id'] for cs in class_subjects]
    ).values('student_id', 'class_id')

    # Build lookup: class_id → list of subject_ids
    class_to_subjects = {}
    for cs in class_subjects:
        class_to_subjects.setdefault(cs['class_id'], []).append(cs['subject_id'])

    hard_counts = {}
    for stu in student_qs:
        sid      = stu['student_id']
        cid      = stu['class_id']
        subjects = class_to_subjects.get(cid, [])
        hard_counts[sid] = sum(
            1 for s in subjects if difficulty_map.get(s) == 'hard'
        )

    return hard_counts


# ══════════════════════════════════════════════════════════════
# 4. MODEL LOADING & PREDICTION
# ══════════════════════════════════════════════════════════════

# Module-level cache so the JSON is only parsed once per process
_model_cache = None


def _load_model():
    global _model_cache
    if _model_cache is not None:
        return _model_cache

    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"  [pre_sem] Model file not found at '{MODEL_PATH}'.\n"
            "  Set the PRE_SEM_MODEL_PATH env var or place student_risk_model.json "
            "in the same directory as this script."
        )

    with open(MODEL_PATH, 'r') as f:
        model = json.load(f)

    print(f"  [pre_sem] Model loaded from {MODEL_PATH}")

    # Feature-name guard (mirrors the original hasattr(model, 'feature_names_in_') check)
    trained_on = model['meta']['feature_names_in_']
    if trained_on != FEATURE_COLS:
        raise ValueError(
            f"  [pre_sem] Feature mismatch!\n"
            f"  Model expects : {trained_on}\n"
            f"  Script sends  : {FEATURE_COLS}\n"
            "  Retrain the model or update FEATURE_COLS to match."
        )

    _model_cache = model
    return model


def _predict_proba_json(model, X_arr):
    """
    Pure-numpy Random Forest inference replicating sklearn's predict_proba.

    Each tree casts a probability vote by normalising its leaf's class counts.
    Final probability = average vote across all trees.

    Parameters
    ----------
    model : dict   Loaded from student_risk_model.json
    X_arr : np.ndarray, shape (n_samples, n_features), dtype float64

    Returns
    -------
    np.ndarray, shape (n_samples, n_classes)
        Columns correspond to model['meta']['classes_'].
    """
    trees     = model['estimators']
    n_classes = model['meta']['n_classes_']
    n_samples = X_arr.shape[0]

    all_proba = np.zeros((n_samples, n_classes), dtype=np.float64)

    for tree in trees:
        feature        = tree['feature']
        threshold      = tree['threshold']
        children_left  = tree['children_left']
        children_right = tree['children_right']
        value          = tree['value']   # list of [count_class0, count_class1, ...] per node

        for i in range(n_samples):
            node = 0
            while children_left[node] != _LEAF_FLAG:
                if X_arr[i, feature[node]] <= threshold[node]:
                    node = children_left[node]
                else:
                    node = children_right[node]

            leaf_counts = np.array(value[node], dtype=np.float64)
            total = leaf_counts.sum()
            all_proba[i] += leaf_counts / total if total > 0 else leaf_counts

    all_proba /= len(trees)
    return all_proba


def _run_predictions(model, df):
    X = df[FEATURE_COLS].copy()
    for col in FEATURE_COLS:
        X[col] = pd.to_numeric(X[col], errors='coerce').fillna(0)

    X_arr = X.values.astype(np.float64)
    probs = _predict_proba_json(model, X_arr)[:, 1]   # class-1 = at-risk

    df = df.copy()
    df['risk_probability_pct'] = (probs * 100).round(2)
    return df


# ══════════════════════════════════════════════════════════════
# 5. WRITE WATCHLIST — replaces _aexecute_many
# ══════════════════════════════════════════════════════════════

def _write_watchlist(df, next_semester):
    """Upsert into PreSemWatchlist. UNIQUE KEY (student_id, target_semester)."""
    for _, r in df.iterrows():
        PreSemWatchlist.objects.update_or_create(
            student_id      = str(r['student_id']),
            target_semester = int(next_semester),
            defaults={
                'class_id':             str(r['class_id']),
                'risk_probability_pct': float(r['risk_probability_pct']),
                'escalation_level':     int(r['escalation_level']),
                'max_plagiarism':       float(r['max_plagiarism']),
                'att_rate_hist':        float(r['att_rate']),
                'assn_rate_hist':       float(r['assn_rate']),
                'exam_avg_hist':        float(r['exam_avg']),
                'hard_subject_count':   int(r.get('hard_subject_count', 0)),
            }
        )


# ══════════════════════════════════════════════════════════════
# 6. PUBLIC ENTRY POINT
# ══════════════════════════════════════════════════════════════

def run():
    print("  [pre_sem] Starting pre-semester ML watchlist generation...")

    completed_semester = _get_current_semester()
    if completed_semester is None:
        print("  [pre_sem] SKIP — no weekly_metrics data found.")
        return

    next_semester = completed_semester + 1
    print(f"  [pre_sem] Completed semester: {completed_semester}  →  Predicting for: {next_semester}")

    # Guard: need at least one semester of data
    if not WeeklyMetrics.objects.filter(semester=completed_semester).exists():
        print(
            f"  [pre_sem] SKIP — no weekly_metrics rows for semester {completed_semester}. "
            "Expected on first-ever deployment."
        )
        return

    # ── Pull features ──────────────────────────────────────────
    df = _pull_student_features(completed_semester)
    if df.empty:
        print("  [pre_sem] SKIP — feature extraction returned no rows.")
        return
    print(f"  [pre_sem] Features pulled for {len(df)} student-class records.")

    # ── Subject difficulty ─────────────────────────────────────
    difficulty_map           = _compute_and_cache_subject_difficulty(completed_semester)
    hard_counts              = _count_hard_subjects_per_student(next_semester, difficulty_map)
    df['hard_subject_count'] = df['student_id'].map(hard_counts).fillna(0).astype(int)

    # ── Load model and predict ─────────────────────────────────
    model = _load_model()
    df    = _run_predictions(model, df)

    # ── Write to DB ────────────────────────────────────────────
    _write_watchlist(df, next_semester)

    # ── Summary ────────────────────────────────────────────────
    flagged = (df['risk_probability_pct'] >= 50).sum()
    print(
        f"  [pre_sem] Done — {len(df)} students scored, "
        f"{flagged} at ≥ 50% risk for semester {next_semester}. "
        f"Results written to pre_sem_watchlist."
    )


if __name__ == '__main__':
    import django, os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project.settings')
    django.setup()
    try:
        run()
    except Exception:
        traceback.print_exc()