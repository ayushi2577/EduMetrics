"""
=================================================================================
  EduMetrics — analysis_engine/views.py  (v3 — newViews contract)

  Every endpoint is shaped to match the data structures demanded by the
  frontend developer in newViews.py.  Existing helper functions (_f, _cap,
  _risk_level, _avatar, _build_factors, _name_map) are kept unchanged so
  nothing else breaks.

  BASE URL: /api/analysis/

  ENDPOINTS
  ─────────────────────────────────────────────────────────────────────────────
  DASHBOARD
    GET  dashboard/summary/?class_id=X&semester=Y&sem_week=Z
    GET  dashboard/class_summary/?class_id=X&semester=Y&sem_week=Z   (AI)

  INTERVENTIONS
    GET  interventions/?class_id=X&semester=Y&sem_week=Z
    POST interventions/log/                                           (body: flag_id, intervention)

  FLAGS
    GET  flags/weekly/?class_id=X&semester=Y&sem_week=Z
    GET  flags/<flag_id>/expand/?semester=Y&sem_week=Z
    GET  flags/last_week/?class_id=X&semester=Y&sem_week=Z

  STUDENTS
    GET  students/all/?class_id=X&semester=Y&sem_week=Z
    GET  students/detainment_risk/?class_id=X&semester=Y

  EVENTS / REPORTS
    GET  reports/pre_midterm/?class_id=X&semester=Y
    GET  reports/post_midterm/?class_id=X&semester=Y
    GET  reports/pre_endterm/?class_id=X&semester=Y
    GET  reports/post_endterm/?class_id=X&semester=Y

  INTERNAL
    POST trigger_calibrate/
=================================================================================
"""

import os
import traceback
from datetime import datetime

from django.db.models import Avg, Max, StdDev
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from .models import (
    weekly_flags,
    weekly_metrics,
    pre_mid_term,
    pre_end_term,
    risk_of_failing,
    pre_sem_watchlist,
    intervention_log,
)
from .serializer import (
    weekly_flagSerializer,
    performanceSerializer,
    PreMidTermSerializer,
    PreEndTermSerializer,
    RiskOfFailingSerializer,
    PreSemWatchlistSerializer,
)

try:
    from .client_models import ClientStudent, ClientExamResult, ClientExamSchedule
    HAS_CLIENT_DB = True
except Exception:
    HAS_CLIENT_DB = False

from .calibrate_analysis_db import calibrate

# ─────────────────────────────────────────────────────────────────────────────
#  SHARED HELPERS  (unchanged from v2)
# ─────────────────────────────────────────────────────────────────────────────

def _require(request, *params):
    missing = [p for p in params if not request.query_params.get(p)]
    if missing:
        return None, Response(
            {'error': f'Missing required query params: {", ".join(missing)}'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return {p: request.query_params[p] for p in params}, None


def _name_map(class_id):
    if not HAS_CLIENT_DB:
        return {}
    try:
        qs = ClientStudent.objects.using('client_db').filter(class_id=class_id)
        return {s.student_id: s.name for s in qs}
    except Exception:
        return {}


def _risk_level(risk_tier: str) -> str:
    rt = (risk_tier or '').lower()
    if 'tier 1' in rt or 'critical' in rt:
        return 'high'
    if 'tier 2' in rt or 'high' in rt:
        return 'high'
    if 'tier 3' in rt or 'warning' in rt:
        return 'med'
    return 'safe'


def _cap(urgency) -> int:
    return min(int(urgency or 0), 100)


def _f(val, default=0.0):
    if val is None:
        return default
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return default


def _avatar(name: str) -> str:
    parts = name.split()
    return ''.join(p[0].upper() for p in parts[:2]) if parts else '?'


def _build_factors(diagnosis: str, urgency_score: int):
    parts = [d.strip() for d in (diagnosis or '').split('|') if d.strip()]
    COLOR_MAP = {
        'severe absenteeism':  '#ef4444',
        'low attendance':      '#ef4444',
        'attendance fader':    '#f59e0b',
        'stopped submitting':  '#f59e0b',
        'integrity violation': '#7c3aed',
        'exam failure':        '#ef4444',
        'hard test drop':      '#f59e0b',
    }
    factors = []
    n = max(len(parts), 1)
    for part in parts[:3]:
        color = '#a78bfa'
        for key, col in COLOR_MAP.items():
            if key in part.lower():
                color = col
                break
        factors.append({
            'label': part,
            'pct': min(int(urgency_score * 0.8 / n), 100),
            'color': color,
        })
    return factors, parts[0] if parts else 'Unknown'


def _get_midterm_score(student_id, semester):
    if not HAS_CLIENT_DB:
        return False
    try:
        schedule = ClientExamSchedule.objects.using('client_db').filter(
            exam_type='MIDTERM'
        ).first()
        if not schedule:
            return False
        result = ClientExamResult.objects.using('client_db').filter(
            student_id=student_id,
            schedule_id=schedule.schedule_id,
        ).first()
        return _f(result.score_pct) if result else False
    except Exception:
        return False


def _get_endterm_score(student_id, semester):
    if not HAS_CLIENT_DB:
        return False
    try:
        schedule = ClientExamSchedule.objects.using('client_db').filter(
            exam_type='ENDTERM'
        ).first()
        if not schedule:
            return False
        result = ClientExamResult.objects.using('client_db').filter(
            student_id=student_id,
            schedule_id=schedule.schedule_id,
        ).first()
        return _f(result.score_pct) if result else False
    except Exception:
        return False

# ─────────────────────────────────────────────────────────────────────────────
#  1. DASHBOARD  — get_dashboard_stats  +  class_summary (AI)
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
def dashboard_summary(request):
    """
    GET /api/analysis/dashboard/summary/?class_id=X&semester=Y&sem_week=Z

    Returns:
    {
        total_students          : int,
        avg_risk_score          : float,   # avg urgency_score of flagged students this week
        flagged_this_week       : int,
        interventions_this_week : int,
        risk_breakdown          : { critical, watch, warning, safe }
    }
    """
    params, err = _require(request, 'class_id', 'semester', 'sem_week')
    if err:
        return err

    class_id = params['class_id']
    semester = int(params['semester'])
    sem_week = int(params['sem_week'])

    total_students = weekly_metrics.objects.filter(
        class_id=class_id, semester=semester, sem_week=1
    ).count()

    flags_qs = weekly_flags.objects.filter(
        class_id=class_id, semester=semester, sem_week=sem_week
    )
    flagged = flags_qs.count()

    
    # urg_qs=weekly_metrics.objects.filter(
            # class_id=class_id, semester=semester, sem_week=sem_week
        # )
    avg_urgency = _f(flags_qs.aggregate(a=Avg('urgency_score'))['a'])
    

    interventions = intervention_log.objects.filter(
        semester=semester, sem_week=sem_week,
        student_id__in=flags_qs.values_list('student_id', flat=True),
    ).count()

    return Response({
        'class_id':                class_id,
        'semester':                semester,
        'sem_week':                sem_week,
        'total_students':          total_students,
        'avg_risk_score':          round(avg_urgency, 1),
        'flagged_this_week':       flagged,
        'interventions_this_week': interventions,
        'risk_breakdown': {
            'critical': flags_qs.filter(risk_tier__icontains='Tier 1').count(),
            'watch':    flags_qs.filter(risk_tier__icontains='Tier 2').count(),
            'warning':  flags_qs.filter(risk_tier__icontains='Tier 3').count(),
            'safe':     max(total_students - flagged, 0),
        },
    })


@api_view(['GET'])
def class_summary_view(request):
    """
    GET /api/analysis/dashboard/class_summary/?class_id=X&semester=Y&sem_week=Z

    Prepares the input prompt and calls ai_helpers.class_summary() (Gemini).
    Returns the raw AI-generated class summary string.

    {
        class_id   : str,
        semester   : int,
        sem_week   : int,
        summary    : str   # AI-generated narrative
    }
    """
    params, err = _require(request, 'class_id', 'semester', 'sem_week')
    if err:
        return err

    class_id = params['class_id']
    semester = int(params['semester'])
    sem_week = int(params['sem_week'])

    flags_qs = weekly_flags.objects.filter(
        class_id=class_id, semester=semester, sem_week=sem_week
    )
    metrics_agg = weekly_metrics.objects.filter(
        class_id=class_id, semester=semester, sem_week=sem_week
    ).aggregate(
        avg_et=Avg('effort_score'),
        avg_perf=Avg('academic_performance'),
        avg_att=Avg('overall_att_pct'),
    )

    input_prompt = {
        'class_id':         class_id,
        'semester':         semester,
        'sem_week':         sem_week,
        'total_students':   weekly_metrics.objects.filter(
                                class_id=class_id, semester=semester, sem_week=sem_week
                            ).count(),
        'flagged_count':    flags_qs.count(),
        'tier1_count':      flags_qs.filter(risk_tier__icontains='Tier 1').count(),
        'tier2_count':      flags_qs.filter(risk_tier__icontains='Tier 2').count(),
        'tier3_count':      flags_qs.filter(risk_tier__icontains='Tier 3').count(),
        'avg_effort':       _f(metrics_agg['avg_et']),
        'avg_performance':  _f(metrics_agg['avg_perf']),
        'avg_attendance':   _f(metrics_agg['avg_att']),
        'top_diagnoses':    list(
                                flags_qs.values_list('diagnosis', flat=True)[:5]
                            ),
    }

    try:
        from .aiviews import class_summary as ai_class_summary
        summary_text = ai_class_summary(input_prompt)
    except Exception as exc:
        return Response({'error': f'AI summary failed: {exc}'}, status=500)

    return Response({
        'class_id':  class_id,
        'semester':  semester,
        'sem_week':  sem_week,
        'summary':   summary_text,
    })


# ─────────────────────────────────────────────────────────────────────────────
#  2. INTERVENTIONS
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
def interventions_list(request):
    """
    GET /api/analysis/interventions/?class_id=X&semester=Y&sem_week=Z

    Returns:
    {
        intervention_id: {
            student_id, name, type_of_intervention, date_of_logging
        }
    }
    """
    params, err = _require(request, 'class_id', 'semester', 'sem_week')
    if err:
        return err

    class_id = params['class_id']
    semester = int(params['semester'])
    sem_week = int(params['sem_week'])

    logs = intervention_log.objects.filter(
        student_id__in=weekly_metrics.objects.filter(
            class_id=class_id, semester=semester
        ).values_list('student_id', flat=True),
        semester=semester,
        sem_week__lte=sem_week,
    ).order_by('-sem_week', '-logged_at')

    names = _name_map(class_id)
    result = {}
    for log in logs:
        sid = log.student_id
        result[log.id] = {
            'student_id':          sid,
            'name':                names.get(sid, sid),
            'type_of_intervention': log.notes or log.trigger_diagnosis or f'Level {log.escalation_level} Escalation',
            'date_of_logging':     log.logged_at.strftime('%Y-%m-%d') if log.logged_at else f'Week {log.sem_week}',
        }

    return Response(result)


@api_view(['POST'])
def log_intervention(request):
    """
    POST /api/analysis/interventions/log/
    Body: { flag_id, intervention}

    Writes into intervention_log table and returns the saved record id.
    """
    flag_id      = request.data.get('flag_id')
    intervention = request.data.get('intervention', '')

    if not flag_id:
        return Response({'error': 'flag_id is required'}, status=400)

    try:
        flag_obj = weekly_flags.objects.get(id=flag_id)
    except weekly_flags.DoesNotExist:
        return Response({'error': f'flag_id {flag_id} not found'}, status=404)

    log_entry = intervention_log.objects.create(
        flag=flag_obj,
        student_id=flag_obj.student_id,
        semester=flag_obj.semester,
        sem_week=flag_obj.sem_week,
        notes=intervention,
        trigger_diagnosis=flag_obj.diagnosis,
        advisor_notified=True,
    )

    return Response({
        'id':         log_entry.id,
        'flag_id':    flag_id,
        'student_id': flag_obj.student_id,
        'logged_at':  log_entry.logged_at.strftime('%Y-%m-%d %H:%M:%S'),
    }, status=201)


# ─────────────────────────────────────────────────────────────────────────────
#  3. FLAGS — weekly_flags, expand_flag, last_weeks_flags
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
def weekly_flags_view(request):
    """
    GET /api/analysis/flags/weekly/?class_id=X&semester=Y&sem_week=Z

    Returns:
    {
        flag1: {
            student_id, student_name, risk_tier, diagnosis,
            attendance_pct, risk_score, escalation_level,
            top_signal: { key, value }   ← most-saturated signal for card face
        },
        ...
    }

    top_signal saturation = current_raw_value / max_possible_raw_value.
    The signal closest to its ceiling wins and is shown on the card face.
    Labels are rendered by RISK_CARD_LABEL in api.js (no extra round-trip).
    """
    params, err = _require(request, 'class_id', 'semester', 'sem_week')
    if err:
        return err

    class_id = params['class_id']
    semester = int(params['semester'])
    sem_week = int(params['sem_week'])

    flags = list(
        weekly_flags.objects.filter(
            class_id=class_id, semester=semester, sem_week=sem_week
        ).order_by('-urgency_score')
    )

    student_ids = [f.student_id for f in flags]

    # ── Current-week metrics (attendance, risk_score, escalation) ─────────────
    metrics_map = {
        m.student_id: m
        for m in weekly_metrics.objects.filter(
            student_id__in=student_ids, semester=semester, sem_week=sem_week
        )
    }

    # ── Prior-window metrics for streak / trend signals ───────────────────────
    # Up to 3 teaching weeks before sem_week, skipping exam weeks (8, 18).
    EXAM_WEEKS = {8, 18}
    prior_weeks = []
    w = sem_week - 1
    while w >= 1 and len(prior_weeks) < 3:
        if w not in EXAM_WEEKS:
            prior_weeks.append(w)
        w -= 1
    prior_weeks_desc = prior_weeks   # already descending (most-recent first)

    # Bulk fetch: one query for all students × all prior weeks
    prior_metrics_map = {}   # { student_id: { sem_week: row_dict } }
    for pm in weekly_metrics.objects.filter(
        student_id__in=student_ids,
        semester=semester,
        sem_week__in=prior_weeks,
    ).values(
        'student_id', 'sem_week',
        'assn_submit_rate', 'quiz_attempt_rate',
        'risk_score', 'effort_score', 'academic_performance',
        'risk_of_detention',
    ):
        prior_metrics_map \
            .setdefault(pm['student_id'], {}) \
            [pm['sem_week']] = pm

    # ── Top-signal helper ─────────────────────────────────────────────────────

    def _top_signal(sid, current_m):
        """
        For each signal compute saturation = current_raw / max_raw ∈ [0, 1].
        Return (key, display_value) for the signal with the highest saturation.

        Signals and their max raw values:
          risk_of_detention  → raw ∈ [0, 100],  max = 100
          assn_streak        → streak ∈ {0,1,2,3}, max = 3
          quiz_streak        → streak ∈ {0,1,2,3}, max = 3
          high_risk_streak   → count ∈ {0,1,2,3}, max = 3
          et_drop            → pp drop ∈ [0, 100], max = 100
          avg_at_3w          → inverted avg A_t; low perf = high signal
          avg_et_3w          → inverted avg E_t; low effort = high signal
        """
        pw = prior_metrics_map.get(sid, {})
        rows = [pw[wk] for wk in prior_weeks_desc if wk in pw]

        # a. risk_of_detention (current week)
        rod_raw = _f(current_m.risk_of_detention if current_m else None)

        # b. assignment streak (consecutive from most-recent)
        assn_streak = 0
        for row in rows:
            rate = row.get('assn_submit_rate')
            if rate is not None and _f(rate) < 1.0:
                assn_streak += 1
            else:
                break

        # c. quiz streak
        quiz_streak = 0
        for row in rows:
            rate = row.get('quiz_attempt_rate')
            if rate is not None and _f(rate) < 1.0:
                quiz_streak += 1
            else:
                break

        # d. high-risk streak (risk_score >= 50 anywhere in window)
        high_risk_count = sum(
            1 for row in rows
            if row.get('risk_score') is not None and row['risk_score'] >= 50
        )

        # e. E_t drop (oldest minus newest, clipped at 0)
        et_vals = [
            _f(row['effort_score'])
            for row in rows
            if row.get('effort_score') is not None
        ]
        et_drop = max(0.0, et_vals[-1] - et_vals[0]) if len(et_vals) >= 2 else 0.0

        # f. avg A_t inverted (low performance = high signal)
        at_vals = [
            _f(row['academic_performance'])
            for row in rows
            if row.get('academic_performance') is not None
        ]
        avg_at = round(sum(at_vals) / len(at_vals), 1) if at_vals else None
        avg_at_signal = max(0.0, 100.0 - avg_at) if avg_at is not None else 0.0

        # g. avg E_t inverted (low effort = high signal)
        et_eff_vals = [
            _f(row['effort_score'])
            for row in rows
            if row.get('effort_score') is not None
        ]
        avg_et = round(sum(et_eff_vals) / len(et_eff_vals), 1) if et_eff_vals else None
        avg_et_signal = max(0.0, 100.0 - avg_et) if avg_et is not None else 0.0

        # Saturation table: (saturation_ratio, display_value)
        candidates = {
            'risk_of_detention': (rod_raw / 100.0,        round(rod_raw, 1)),
            'assn_streak':       (assn_streak / 3.0,       assn_streak),
            'quiz_streak':       (quiz_streak / 3.0,       quiz_streak),
            'high_risk_streak':  (high_risk_count / 3.0,   high_risk_count),
            'et_drop':           (et_drop / 100.0,         round(et_drop, 1)),
            'avg_at_3w':         (avg_at_signal / 100.0,   avg_at if avg_at is not None else 0),
            'avg_et_3w':         (avg_et_signal / 100.0,   avg_et if avg_et is not None else 0),
        }

        best_key = max(candidates, key=lambda k: candidates[k][0])
        saturation, display_val = candidates[best_key]

        return {
            'key':        best_key,
            'value':      display_val,
            'saturation': round(saturation * 100, 1),  # % of max, useful for debugging
        }

    # ── Build response ────────────────────────────────────────────────────────
    names  = _name_map(class_id)
    result = {}

    for i, flag in enumerate(flags, start=1):
        sid    = flag.student_id
        m      = metrics_map.get(sid)

        result[f'flag{i}'] = {
            'id':              flag.id,
            'student_id':      sid,
            'student_name':    names.get(sid, sid),
            'risk_tier':       flag.risk_tier,
            'diagnosis':       flag.diagnosis,
            'attendance_pct':  round(_f(m.overall_att_pct if m else None), 1),
            'risk_score':      _cap(m.risk_score if m else None),
            'escalation_level': m.escalation_level if m else 0,
            'top_signal':      _top_signal(sid, m),
        }

    return Response(result)

@api_view(['GET'])
def expand_flag(request, flag_id):
    """
    GET /api/analysis/flags/<flag_id>/expand/?semester=Y&sem_week=Z

    Returns the full student deep-dive for a flagged student.
    Shape matches expand_flag() spec in newViews.py exactly.
    """
    semester_str = request.query_params.get('semester')
    sem_week_str = request.query_params.get('sem_week')

    if not semester_str:
        return Response({'error': 'semester is required'}, status=400)

    semester = int(semester_str)
    sem_week = int(sem_week_str) if sem_week_str else None

    try:
        flag = weekly_flags.objects.get(id=flag_id)
    except weekly_flags.DoesNotExist:
        return Response({'error': f'flag_id {flag_id} not found'}, status=404)

    sid = flag.student_id

    # ── Current-week metrics ──────────────────────────────────────────────────
    m_filter = dict(student_id=sid, semester=semester)
    if sem_week:
        m_filter['sem_week'] = sem_week
    m = weekly_metrics.objects.filter(**m_filter).order_by('-sem_week').first()

    # ── All historical metrics for this semester ──────────────────────────────
    traj_qs = weekly_metrics.objects.filter(
        student_id=sid, semester=semester
    )
    if sem_week:
        traj_qs = traj_qs.filter(sem_week__lte=sem_week)
    traj = list(traj_qs.order_by('sem_week').values(
        'sem_week', 'effort_score', 'academic_performance',
        'overall_att_pct', 'risk_of_detention',
    ))

    week_et   = [_f(r['effort_score']) for r in traj]
    week_at   = [_f(r['academic_performance']) for r in traj]

    avg_effort       = round(sum(week_et) / max(len(week_et), 1), 2)
    avg_performance  = round(sum(week_at) / max(len(week_at), 1), 2)
    overall_att      = round(_f(m.overall_att_pct if m else None), 1)
    risk_detention   = round(_f(m.risk_of_detention if m else None), 1)
    midterm_score    = _get_midterm_score(sid, semester)

    # ── student_summary (AI) ──────────────────────────────────────────────────
    # Build student_data dict for aiviews.student_summary()
    cls_agg = weekly_metrics.objects.filter(
        class_id=flag.class_id, semester=semester, sem_week=(sem_week or flag.sem_week)
    ).aggregate(avg_et=Avg('effort_score'), avg_perf=Avg('academic_performance'))
    class_avg_et   = _f(cls_agg['avg_et'],   65.0)
    class_avg_perf = _f(cls_agg['avg_perf'], 70.0)

    flag_count_qs = weekly_flags.objects.filter(
        student_id=sid, semester=semester
    ).order_by('sem_week')
    flagging_history_data = {
        'times_flagged':          flag_count_qs.count(),
        'weeks_since_each_flag':  [
            (sem_week or flag.sem_week) - f['sem_week']
            for f in flag_count_qs.values('sem_week')
        ],
    }

    student_data = {
        'E_t':                  _f(m.effort_score if m else None),
        'A_t':                  _f(m.academic_performance if m else None) or None,
        'reasons_for_flagging': flag.diagnosis,
        'urgency_score':        float(flag.urgency_score or 0),
        'risk_score':           float(flag.urgency_score or 0),
        'E_t_history':          week_et[:-1],
        'A_t_history':          [x for x in week_at if x > 0],
        'E':                    class_avg_et,
        'A':                    class_avg_perf,
        'e':                    avg_effort,
        'a':                    avg_performance,
        'del_E':                round(avg_effort - class_avg_et, 2),
        'del_A':                round(avg_performance - class_avg_perf, 2),
        'flagging_history':     flagging_history_data,
        'effort_contributors_student': {
            'avg_library_visits':         _f(m.library_visits if m else None),
            'avg_book_borrows':           _f(m.book_borrows if m else None),
            'avg_attendance_pct':         _f(m.overall_att_pct if m else None) / 100,
            'avg_assignment_submit_rate': _f(getattr(m, 'assn_submit_rate', None) if m else None),
            'avg_plagiarism_free_rate':   1 - _f(getattr(m, 'assn_plagiarism_pct', None) if m else None) / 100,
            'avg_quiz_attempt_rate':      _f(m.quiz_attempt_rate if m else None),
        },
        'effort_contributors_class': {
            'avg_library_visits':         1.8,
            'avg_book_borrows':           0.9,
            'avg_attendance_pct':         class_avg_et / 100,
            'avg_assignment_submit_rate': 0.87,
            'avg_plagiarism_free_rate':   0.91,
            'avg_quiz_attempt_rate':      0.78,
        },
    }

    ai_summary = None
    try:
        from .aiviews import student_summary as ai_student_summary
        ai_summary = ai_student_summary(student_data)
    except Exception:
        ai_summary = None

    # ── Flagging history ──────────────────────────────────────────────────────
    intervened_weeks = set(
        intervention_log.objects.filter(
            student_id=sid, semester=semester, advisor_notified=True
        ).values_list('sem_week', flat=True)
    )
    # NEW
    flagging_history = {
        f['sem_week']: {
            'diagnosis':       f['diagnosis'],
            'did_we_intervene': f['sem_week'] in intervened_weeks,
        }
        for f in weekly_flags.objects.filter(
            student_id=sid, semester=semester, sem_week__lte=sem_week
        ).order_by('sem_week').values('sem_week', 'diagnosis')
    }

    # ── Trends ────────────────────────────────────────────────────────────────
    trends = {
        'E_t': {r['sem_week']: _f(r['effort_score'])          for r in traj},
        'A_t': {r['sem_week']: _f(r['academic_performance']) or False for r in traj},
    }

    # ── Effort vs Performance ─────────────────────────────────────────────────
    all_student_avgs = weekly_metrics.objects.filter(
        class_id=flag.class_id, semester=semester, sem_week=(sem_week or flag.sem_week)
    ).values('student_id').annotate(
        avg_effort=Avg('effort_score'),
        avg_perf=Avg('academic_performance'),
    )
    class_effort_mean = _f(
        sum(_f(s['avg_effort']) for s in all_student_avgs) / max(len(list(all_student_avgs)), 1)
    ) if all_student_avgs else class_avg_et
    # Re-query because the queryset was consumed
    cls_perf_vals = list(
        weekly_metrics.objects.filter(
            class_id=flag.class_id, semester=semester,
            sem_week=(sem_week or flag.sem_week)
        ).values_list('academic_performance', flat=True)
    )
    class_perf_mean = round(sum(_f(v) for v in cls_perf_vals) / max(len(cls_perf_vals), 1), 2)

    effort_vs_performance = {
        'avg_effort_of_class':      round(class_avg_et, 2),
        'avg_performance_of_class': round(class_perf_mean, 2),
        'avg_performance_of_student': avg_performance,
        'avg_effort_of_student':      avg_effort,
        'E_t': _f(m.effort_score if m else None),
        'A_t': _f(m.academic_performance if m else None),
    }

    # ── Flagging contributors ─────────────────────────────────────────────────
    parts = [d.strip() for d in (flag.diagnosis or '').split('|') if d.strip()]
    n = max(len(parts), 1)
    total_score = _cap(flag.urgency_score) or 1
    flagging_contributors = {
        part: round(total_score / n, 1)
        for part in parts
    }

    # ── Risk score breakdown ──────────────────────────────────────────────────────
    # Weights are static (match flagging.py WEIGHTS exactly)
    RISK_WEIGHTS = {
        'risk_of_detention': 30,
        'assn_streak':       15,
        'quiz_streak':        8,
        'high_risk_streak':  12,
        'lag_score_penalty': 10,
        'avg_risk_score_3w':  7,
        'avg_at_3w':          5,
        'avg_et_3w':          5,
        'et_drop':            8,
    }

    # Pull current-week raw values for each signal
    # risk_of_detention current raw value
    rod_raw    = _f(m.risk_of_detention if m else None)
    rod_signal = (rod_raw / 100.0) ** 2 * 100.0          # convex

    # assn_streak: read from assn_submit_rate of last 3 weeks
    window_wks = sorted(
        [r['sem_week'] for r in traj if r['sem_week'] < (sem_week or flag.sem_week)],
        reverse=True
    )[:3]
    traj_map_local = {r['sem_week']: r for r in traj}

    assn_streak_count = 0
    for wk in window_wks:
        row = traj_map_local.get(wk, {})
        rate = _f(row.get('assn_submit_rate') if isinstance(row, dict) else None, default=None)
        if rate is not None and rate < 1.0:
            assn_streak_count += 1
        else:
            break
    assn_streak_signal = min(assn_streak_count, 3) / 3 * 100

    quiz_streak_count = 0
    for wk in window_wks:
        row = traj_map_local.get(wk, {})
        rate = _f(row.get('quiz_attempt_rate') if isinstance(row, dict) else None, default=None)
        if rate is not None and rate < 1.0:
            quiz_streak_count += 1
        else:
            break
    quiz_streak_signal = min(quiz_streak_count, 3) / 3 * 100

    # high_risk_streak: weeks in window with risk_score >= 50
    HIGH_RISK_THRESHOLD = 50
    high_risk_wm = weekly_metrics.objects.filter(
        student_id=sid, semester=semester, sem_week__in=window_wks
    ).values_list('risk_score', flat=True)
    high_risk_count = sum(1 for rs in high_risk_wm if rs is not None and rs >= HIGH_RISK_THRESHOLD)
    high_risk_ratio = min(high_risk_count, 3) / 3
    high_risk_signal = high_risk_ratio ** 2 * 100

    # lag_score_penalty (read from traj)
    traj_wm = weekly_metrics.objects.filter(
        student_id=sid, semester=semester, sem_week__in=window_wks
    ).values('effort_score', 'academic_performance')
    ats = [_f(r['academic_performance']) for r in traj_wm if r['academic_performance'] is not None and _f(r['effort_score']) > 0]
    ets = [_f(r['effort_score'])         for r in traj_wm if r['effort_score'] is not None and _f(r['effort_score']) > 0]
    if ats and ets and class_avg_et > 0:
        student_ratio = (sum(ats)/len(ats)) / (sum(ets)/len(ets))
        class_ratio   = class_avg_perf / class_avg_et
        lag_signal    = min(max(0.0, 1.0 - (student_ratio / class_ratio)) * 100, 100) if class_ratio > 0 else 0.0
    else:
        lag_signal = 0.0

    # avg_risk_score_3w: plain avg of prior risk_scores in window
    prior_rs = list(weekly_metrics.objects.filter(
        student_id=sid, semester=semester, sem_week__in=window_wks
    ).values_list('risk_score', flat=True))
    prior_rs_vals = [float(rs) for rs in prior_rs if rs is not None]
    avg_rs_signal = sum(prior_rs_vals) / len(prior_rs_vals) if prior_rs_vals else 0.0

    # avg_at_3w and avg_et_3w (inverted)
    at_vals = [_f(r['academic_performance']) for r in traj if r['sem_week'] in window_wks and r['academic_performance'] is not None]
    et_vals = [_f(r['effort_score'])         for r in traj if r['sem_week'] in window_wks and r['effort_score'] is not None]
    avg_at_signal = max(0.0, 100.0 - (sum(at_vals)/len(at_vals))) if at_vals else 0.0
    avg_et_signal = max(0.0, 100.0 - (sum(et_vals)/len(et_vals))) if et_vals else 0.0

    # et_drop: oldest - newest E_t
    et_ordered = [_f(r['effort_score']) for r in sorted(traj, key=lambda x: x['sem_week']) if r.get('effort_score') is not None]
    et_drop_signal = max(0.0, et_ordered[-1] - et_ordered[0]) if len(et_ordered) >= 2 else 0.0

    SIGNALS = {
        'risk_of_detention': rod_signal,
        'assn_streak':       assn_streak_signal,
        'quiz_streak':       quiz_streak_signal,
        'high_risk_streak':  high_risk_signal,
        'lag_score_penalty': lag_signal,
        'avg_risk_score_3w': avg_rs_signal,
        'avg_at_3w':         avg_at_signal,
        'avg_et_3w':         avg_et_signal,
        'et_drop':           et_drop_signal,
    }

    DISPLAY_LABELS = {
        'risk_of_detention': 'Risk of Detention',
        'assn_streak':       'Missed Assignment Streak',
        'quiz_streak':       'Missed Quiz Streak',
        'high_risk_streak':  'Weeks at High Risk (≥50)',
        'lag_score_penalty': 'Effort→Performance Gap',
        'avg_risk_score_3w': 'Avg Risk Score (3w)',
        'avg_at_3w':         'Avg Academic Performance (3w)',
        'avg_et_3w':         'Avg Effort Score (3w)',
        'et_drop':           'Effort Drop',
    }

    CURRENT_VALUES = {
        'risk_of_detention': round(rod_raw, 1),
        'assn_streak':       assn_streak_count,
        'quiz_streak':       quiz_streak_count,
        'high_risk_streak':  high_risk_count,
        'lag_score_penalty': round(lag_signal, 1),
        'avg_risk_score_3w': round(avg_rs_signal, 1),
        'avg_at_3w':         round(100 - avg_at_signal, 1),   # show actual avg, not inverted
        'avg_et_3w':         round(100 - avg_et_signal, 1),   # same
        'et_drop':           round(et_drop_signal, 1),
    }

    CURRENT_UNITS = {
        'risk_of_detention': '/100',
        'assn_streak':       ' week(s)',
        'quiz_streak':       ' week(s)',
        'high_risk_streak':  ' week(s)',
        'lag_score_penalty': '/100',
        'avg_risk_score_3w': '/100',
        'avg_at_3w':         '/100',
        'avg_et_3w':         '/100',
        'et_drop':           ' pp',
    }

    risk_score_breakdown = [
        {
            'key':                 key,
            'label':               DISPLAY_LABELS[key],
            'weight':              RISK_WEIGHTS[key],
            'signal':              round(SIGNALS[key], 1),
            'contribution':        round(RISK_WEIGHTS[key] * SIGNALS[key] / 100, 1),
            'max_contribution':    RISK_WEIGHTS[key],
            'current_value':       CURRENT_VALUES[key],
            'unit':                CURRENT_UNITS[key],
        }
        for key in RISK_WEIGHTS
    ]

    # ── Class-level percentile distribution for this week ────────────────────────
    all_risk_scores = list(
        weekly_metrics.objects.filter(
            class_id=flag.class_id, semester=semester,
            sem_week=(sem_week or flag.sem_week),
        ).exclude(risk_score__isnull=True)
        .values_list('risk_score', flat=True)
    )
    all_risk_scores_sorted = sorted(all_risk_scores)
    n_class = len(all_risk_scores_sorted)

    def _percentile_val(sorted_list, pct):
        if not sorted_list: return 0
        idx = int(len(sorted_list) * pct / 100)
        return float(sorted_list[min(idx, len(sorted_list)-1)])

    student_risk_score = m.risk_score if m and m.risk_score is not None else flag.urgency_score
    students_below = sum(1 for rs in all_risk_scores if rs < student_risk_score)
    student_percentile = round(students_below / max(n_class, 1) * 100, 1)

    risk_percentiles = {
        'p25':                round(_percentile_val(all_risk_scores_sorted, 25), 1),
        'p50':                round(_percentile_val(all_risk_scores_sorted, 50), 1),
        'p75':                round(_percentile_val(all_risk_scores_sorted, 75), 1),
        'p100':               float(all_risk_scores_sorted[-1]) if all_risk_scores_sorted else 0,
        'p0':                 float(all_risk_scores_sorted[0])  if all_risk_scores_sorted else 0,
        'student_score':      float(student_risk_score or 0),
        'student_percentile': student_percentile,
    }

    return Response({
        'student_overview': {
            'avg_risk_score':           _cap(flag.urgency_score),
            'avg_effort':               avg_effort,
            'avg_academic_performance': avg_performance,
            'overall_attendance':       overall_att,
            'risk_of_detention':        risk_detention,
            'mid_term_score':           midterm_score,
        },
        'student_summary':       ai_summary,
        'flagging_history':      flagging_history,
        'trends':                trends,
        'effort_vs_performance': effort_vs_performance,
        'flagging_contributors': flagging_contributors,
        'risk_score_breakdown':  risk_score_breakdown,
        'risk_percentiles':      risk_percentiles,
    })


# ─────────────────────────────────────────────────────────────────────────────
#  AI — student_summary_view
#  POST /api/analysis/ai/student_summary/
#  Body: { flag_id: int, semester: int, sem_week: int }
#
#  Builds student_info_json from DB then calls aiviews.student_summary_new().
#  Returns the structured AI recommendation (intervention, talking points, briefs).
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['POST'])
def student_summary_view(request):
    """
    POST /api/analysis/ai/student_summary/
    Body: { flag_id, semester, sem_week }

    Returns:
    {
        flag_id                  : int,
        student_id               : str,
        student_name             : str,
        recommended_intervention : str,   # monitor | email_student | one_to_one_check | email_parent | refer_to_counsellor
        secondary_intervention   : str | null,
        reasoning                : str,
        urgency                  : str,   # low | moderate | high | critical
        tone                     : str,   # supportive | urgent | neutral
        talking_points           : [str],
        email_student_brief      : str | null,
        email_parent_brief       : str | null,
        counsellor_brief         : str | null,
        signals_to_highlight     : [str],
    }
    """
    flag_id  = request.data.get('flag_id')
    semester_raw = request.data.get('semester')
    sem_week_raw = request.data.get('sem_week')

    if not flag_id:
        return Response({'error': 'flag_id is required'}, status=400)
    if not semester_raw:
        return Response({'error': 'semester is required'}, status=400)
    if not sem_week_raw:
        return Response({'error': 'sem_week is required'}, status=400)

    semester = int(semester_raw)
    sem_week = int(sem_week_raw)

    # ── Fetch flag ────────────────────────────────────────────────────────────
    try:
        flag = weekly_flags.objects.get(id=flag_id)
    except weekly_flags.DoesNotExist:
        return Response({'error': f'flag_id {flag_id} not found'}, status=404)

    sid = flag.student_id

    # ── Student name ──────────────────────────────────────────────────────────
    names = _name_map(flag.class_id)
    student_name = names.get(sid, sid)

    # ── Current-week metrics ──────────────────────────────────────────────────
    m = weekly_metrics.objects.filter(
        student_id=sid, semester=semester, sem_week=sem_week
    ).order_by('-sem_week').first()

    # ── Class averages ────────────────────────────────────────────────────────
    cls_agg = weekly_metrics.objects.filter(
        class_id=flag.class_id, semester=semester, sem_week=sem_week
    ).aggregate(
        avg_et=Avg('effort_score'),
        avg_perf=Avg('academic_performance'),
    )
    class_avg_effort      = _f(cls_agg['avg_et'],   65.0)
    class_avg_performance = _f(cls_agg['avg_perf'], 70.0)

    # ── Student's rolling averages ────────────────────────────────────────────
    # Up-to-8-week effort average
    effort_8w_qs = list(
        weekly_metrics.objects.filter(
            student_id=sid, semester=semester, sem_week__lte=sem_week
        ).order_by('-sem_week').values_list('effort_score', flat=True)[:8]
    )
    avg_effort_8w = round(
        sum(_f(v) for v in effort_8w_qs) / max(len(effort_8w_qs), 1), 2
    ) if effort_8w_qs else _f(m.effort_score if m else None)

    # Last-5-week performance average
    perf_5w_qs = list(
        weekly_metrics.objects.filter(
            student_id=sid, semester=semester, sem_week__lte=sem_week
        ).order_by('-sem_week').values_list('academic_performance', flat=True)[:5]
    )
    avg_performance_5w = round(
        sum(_f(v) for v in perf_5w_qs) / max(len(perf_5w_qs), 1), 2
    ) if perf_5w_qs else _f(m.academic_performance if m else None)

    # ── Exam scores ───────────────────────────────────────────────────────────
    # midterm_score: available if week 8 < sem_week < 18
    midterm_score = False
    if 8 < sem_week < 18:
        midterm_score = _get_midterm_score(sid, semester)

    # endterm_score: available in even semesters weeks 4–7
    endterm_score = False
    if semester % 2 == 0 and 4 <= sem_week <= 7:
        endterm_score = _get_endterm_score(sid, semester)


    RISK_SCORE_DEF = (
        "Composite risk score (0–100) computed as a weighted sum of: "
        "risk_of_detention (w=30), assignment_streak (w=15), quiz_streak (w=8), "
        "high_risk_streak (w=12), lag_score_penalty (w=10), avg_risk_3w (w=7), "
        "avg_academic_performance_3w (w=5), avg_effort_3w (w=5), effort_drop (w=8)."
    )
    EFFORT_DEF = (
        "Effort score (0–100) reflects deliberate academic behaviours weighted as: "
        "library_visits, book_borrows, plagiarism_free_submissions, quiz_attempts, "
        "assignment_submission_rate, and attendance. Higher = more effort."
    )
    ACADEMIC_PERF_DEF = (
        "Academic performance score (0–100) derived from quiz scores and assignment "
        "scores for the current week. Null/0 if no assessments occurred."
    )
    LAG_SCORE_DEF = (
        "Lag score (0–100) measures the gap between effort invested and academic "
        "output achieved, compared against the class average ratio. Higher = effort "
        "is not converting into performance (comprehension gap)."
    )
    RISK_DETENTION_DEF = (
        "Risk of detention (0–100) based on cumulative attendance percentage relative "
        "to the institution's minimum attendance threshold (typically 75%)."
    )

    # ── Assemble student_info_json ────────────────────────────────────────────
    student_info_json = {
        "student_name":                     student_name,
        "risk_score":                       _cap(m.risk_score if m else flag.urgency_score),
        "risk_score_definition":            RISK_SCORE_DEF,
        "effort":                           _f(m.effort_score if m else None),
        "effort_definition":                EFFORT_DEF,
        "academic_performance":             _f(m.academic_performance if m else None),
        "academic_performance_definition":  ACADEMIC_PERF_DEF,
        "lag_score":                        _f(getattr(m, 'lag_score', None) if m else None),
        "lag_score_definition":             LAG_SCORE_DEF,
        "risk_of_detention":                _f(m.risk_of_detention if m else None),
        "risk_of_detention_definition":     RISK_DETENTION_DEF,
        "sem_week":                         sem_week,
        "midterm_week":                     18,
        "endterm_week":                     19,
        "midterm_week":                     8,
        "endterm_week":                     18,
        "reason_of_flagging":               flag.diagnosis or '',
        "class_avg_effort":                 class_avg_effort,
        "class_avg_performance":            class_avg_performance,
        "avg_effort_8w":                    avg_effort_8w,
        "avg_performance_5w":               avg_performance_5w,
        "midterm_score":                    midterm_score,
        "endterm_score":                    endterm_score,
    }

    # ── Call AI ───────────────────────────────────────────────────────────────
    try:
        from .aiviews import student_summary_new
        ai_result = student_summary_new(student_info_json)
    except Exception as exc:
        return Response({'error': f'AI analysis failed: {exc}'}, status=500)

    return Response({
        'flag_id':                   flag_id,
        'student_id':                sid,
        'student_name':              student_name,
        **ai_result,  # spreads all AI output keys directly into response
    })


# ─────────────────────────────────────────────────────────────────────────────
#  AI — generate_content_view
#  POST /api/analysis/ai/generate_content/
#  Body: { flag_id, content_type, ai_analysis, semester, sem_week }
#
#  Takes the ai_analysis dict returned by student_summary_view and generates
#  the appropriate human-facing communication document.
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['POST'])
def generate_content_view(request):
    """
    POST /api/analysis/ai/generate_content/
    Body: {
        flag_id      : int,
        content_type : "email_to_student" | "email_to_parent" |
                       "one_to_one_conversation" | "counsellor_report",
        ai_analysis  : { ... }   ← full dict from student_summary_view response
        semester     : int,
        sem_week     : int,
    }

    Returns:
    {
        flag_id      : int,
        content_type : str,
        content      : str   ← the generated document text
    }
    """
    flag_id      = request.data.get('flag_id')
    content_type = request.data.get('content_type')
    ai_analysis  = request.data.get('ai_analysis')
    semester_raw = request.data.get('semester')
    sem_week_raw = request.data.get('sem_week')

    VALID = {'email_to_student', 'email_to_parent', 'one_to_one_conversation', 'counsellor_report'}

    if not flag_id:
        return Response({'error': 'flag_id is required'}, status=400)
    if not content_type or content_type not in VALID:
        return Response({
            'error': f'content_type must be one of: {", ".join(sorted(VALID))}'
        }, status=400)
    if not ai_analysis or not isinstance(ai_analysis, dict):
        return Response({'error': 'ai_analysis (dict) is required'}, status=400)

    # ── Fetch student name ────────────────────────────────────────────────────
    student_name = ai_analysis.get('student_name', '')
    if not student_name:
        try:
            flag = weekly_flags.objects.get(id=flag_id)
            names = _name_map(flag.class_id)
            student_name = names.get(flag.student_id, flag.student_id)
        except weekly_flags.DoesNotExist:
            student_name = 'Student'

    # ── Call AI content generator ─────────────────────────────────────────────
    try:
        from .aiviews import generate_content as ai_generate_content
        content = ai_generate_content(content_type, student_name, ai_analysis)
    except ValueError as exc:
        return Response({'error': str(exc)}, status=400)
    except Exception as exc:
        return Response({'error': f'Content generation failed: {exc}'}, status=500)

    return Response({
        'flag_id':      flag_id,
        'content_type': content_type,
        'content':      content,
    })




@api_view(['GET'])
def last_weeks_flags(request):
    """
    GET /api/analysis/flags/last_week/?class_id=X&semester=Y&sem_week=Z

    For each flag from prev_week, returns:
      basic_details, more, this_week_vs_last_week (with ALL metrics),
      top_signal (most-saturated signal with pct change prev→curr),
      risk_breakdown_prev, risk_breakdown_curr (for overlay comparison)
    """
    params, err = _require(request, 'class_id', 'semester', 'sem_week')
    if err:
        return err

    class_id  = params['class_id']
    semester  = int(params['semester'])
    sem_week  = int(params['sem_week'])
    prev_week = sem_week - 1

    if prev_week < 1:
        return Response({})

    EXAM_WEEKS = {8, 18}

    prev_flags   = list(weekly_flags.objects.filter(
        class_id=class_id, semester=semester, sem_week=prev_week
    ).order_by('-urgency_score'))
    student_ids  = [f.student_id for f in prev_flags]
    names        = _name_map(class_id)

    # ── Bulk fetch all metrics we need ────────────────────────────────────────
    # Weeks needed: prev_week, curr_week, and up to 3 prior to each
    prior_to_prev = [w for w in range(max(1, prev_week - 3), prev_week) if w not in EXAM_WEEKS]
    prior_to_curr = [w for w in range(max(1, sem_week  - 3), sem_week)  if w not in EXAM_WEEKS]
    all_weeks_needed = list(set([prev_week, sem_week] + prior_to_prev + prior_to_curr))

    all_metrics = {}   # { sid: { sem_week: row_dict } }
    for row in weekly_metrics.objects.filter(
        student_id__in=student_ids, semester=semester,
        sem_week__in=all_weeks_needed,
    ).values(
        'student_id', 'sem_week',
        'effort_score', 'academic_performance', 'overall_att_pct',
        'risk_of_detention', 'risk_score', 'escalation_level',
        'assn_submit_rate', 'quiz_attempt_rate', 'assn_plagiarism_pct',
        'quiz_avg_pct', 'assn_avg_pct', 'midterm_score_pct',
        'weekly_att_pct',
    ):
        all_metrics.setdefault(row['student_id'], {})[row['sem_week']] = row

    curr_flags_map = {
        f.student_id: f
        for f in weekly_flags.objects.filter(
            student_id__in=student_ids, semester=semester, sem_week=sem_week
        )
    }

    # ── Class avg for lag score denominator ───────────────────────────────────
    def _class_ratio(wk):
        cls = weekly_metrics.objects.filter(
            class_id=class_id, semester=semester, sem_week=wk
        ).aggregate(avg_et=Avg('effort_score'), avg_perf=Avg('academic_performance'))
        et   = _f(cls['avg_et'],   65.0)
        perf = _f(cls['avg_perf'], 70.0)
        return et, perf, (perf / et if et > 0 else 1.0)

    cls_et_prev, cls_perf_prev, cls_ratio_prev = _class_ratio(prev_week)
    cls_et_curr, cls_perf_curr, cls_ratio_curr = _class_ratio(sem_week)

    # ── Signal computation helper (reused for both weeks) ─────────────────────
    def _compute_signals(sid, target_week, prior_weeks_desc, cls_ratio):
        """
        Returns dict of signal_key → (raw_value, signal_0_100).
        raw_value is the human-readable value (e.g. streak count, actual score).
        """
        wm = all_metrics.get(sid, {})
        cur = wm.get(target_week, {})
        rows = [wm[w] for w in prior_weeks_desc if w in wm]

        rod_raw    = _f(cur.get('risk_of_detention'))
        rod_signal = (rod_raw / 100.0) ** 2 * 100.0

        assn_streak = 0
        for row in rows:
            rate = row.get('assn_submit_rate')
            if rate is not None and _f(rate) < 1.0: assn_streak += 1
            else: break

        quiz_streak = 0
        for row in rows:
            rate = row.get('quiz_attempt_rate')
            if rate is not None and _f(rate) < 1.0: quiz_streak += 1
            else: break

        high_risk_count = sum(
            1 for row in rows
            if row.get('risk_score') is not None and row['risk_score'] >= 50
        )
        high_risk_ratio  = min(high_risk_count, 3) / 3
        high_risk_signal = high_risk_ratio ** 2 * 100

        ats = [_f(r['academic_performance']) for r in rows if r.get('academic_performance') is not None and _f(r.get('effort_score', 0)) > 0]
        ets = [_f(r['effort_score'])         for r in rows if r.get('effort_score') is not None and _f(r.get('effort_score', 0)) > 0]
        if ats and ets and cls_ratio > 0:
            student_ratio = (sum(ats)/len(ats)) / (sum(ets)/len(ets))
            lag_signal = min(max(0.0, 1.0 - (student_ratio / cls_ratio)) * 100, 100)
        else:
            lag_signal = 0.0

        prior_rs_vals = [float(r['risk_score']) for r in rows if r.get('risk_score') is not None]
        avg_rs_signal = sum(prior_rs_vals) / len(prior_rs_vals) if prior_rs_vals else 0.0

        at_vals = [_f(r['academic_performance']) for r in rows if r.get('academic_performance') is not None]
        et_vals = [_f(r['effort_score'])         for r in rows if r.get('effort_score') is not None]
        avg_at_signal = max(0.0, 100.0 - (sum(at_vals)/len(at_vals))) if at_vals else 0.0
        avg_et_signal = max(0.0, 100.0 - (sum(et_vals)/len(et_vals))) if et_vals else 0.0

        et_ordered = sorted(
            [(r['sem_week'], _f(r['effort_score'])) for r in rows if r.get('effort_score') is not None],
            key=lambda x: x[0]
        )
        et_drop_signal = max(0.0, et_ordered[-1][1] - et_ordered[0][1]) if len(et_ordered) >= 2 else 0.0

        return {
            # key: (display_raw, signal_0_100)
            'risk_of_detention': (round(rod_raw, 1),           rod_signal),
            'assn_streak':       (assn_streak,                 min(assn_streak, 3) / 3 * 100),
            'quiz_streak':       (quiz_streak,                 min(quiz_streak, 3) / 3 * 100),
            'high_risk_streak':  (high_risk_count,             high_risk_signal),
            'lag_score_penalty': (round(lag_signal, 1),        lag_signal),
            'avg_risk_score_3w': (round(avg_rs_signal, 1),     avg_rs_signal),
            'avg_at_3w':         (round(100 - avg_at_signal, 1), avg_at_signal),
            'avg_et_3w':         (round(100 - avg_et_signal, 1), avg_et_signal),
            'et_drop':           (round(et_drop_signal, 1),    et_drop_signal),
        }

    MAX_RAW = {
        'risk_of_detention': 100,
        'assn_streak':         3,
        'quiz_streak':         3,
        'high_risk_streak':    3,
        'lag_score_penalty': 100,
        'avg_risk_score_3w': 100,
        'avg_at_3w':         100,
        'avg_et_3w':         100,
        'et_drop':           100,
    }
    WEIGHTS = {
        'risk_of_detention': 30,
        'assn_streak':       15,
        'quiz_streak':        8,
        'high_risk_streak':  12,
        'lag_score_penalty': 10,
        'avg_risk_score_3w':  7,
        'avg_at_3w':          5,
        'avg_et_3w':          5,
        'et_drop':            8,
    }
    DISPLAY_LABELS = {
        'risk_of_detention': 'Risk of Detention',
        'assn_streak':       'Missed Assignment Streak',
        'quiz_streak':       'Missed Quiz Streak',
        'high_risk_streak':  'Weeks at High Risk (≥50)',
        'lag_score_penalty': 'Effort→Performance Gap',
        'avg_risk_score_3w': 'Avg Risk Score (3w)',
        'avg_at_3w':         'Avg Academic Performance (3w)',
        'avg_et_3w':         'Avg Effort Score (3w)',
        'et_drop':           'Effort Drop',
    }
    UNITS = {
        'risk_of_detention': '/100',
        'assn_streak':       ' week(s)',
        'quiz_streak':       ' week(s)',
        'high_risk_streak':  ' week(s)',
        'lag_score_penalty': '/100',
        'avg_risk_score_3w': '/100',
        'avg_at_3w':         '/100',
        'avg_et_3w':         '/100',
        'et_drop':           ' pp',
    }

    def _to_breakdown(signals_dict):
        return [
            {
                'key':             key,
                'label':           DISPLAY_LABELS[key],
                'weight':          WEIGHTS[key],
                'current_value':   signals_dict[key][0],
                'unit':            UNITS[key],
                'signal':          round(signals_dict[key][1], 1),
                'contribution':    round(WEIGHTS[key] * signals_dict[key][1] / 100, 1),
                'max_contribution': WEIGHTS[key],
            }
            for key in WEIGHTS
        ]

    def _top_signal(signals_dict):
        best_key = max(
            signals_dict,
            key=lambda k: signals_dict[k][0] / MAX_RAW[k] if MAX_RAW[k] > 0 else 0
        )
        return best_key, signals_dict[best_key][0]

    # ── All metrics for a week as a flat comparable dict ──────────────────────
    def _week_metrics_snapshot(sid, wk):
        row = all_metrics.get(sid, {}).get(wk, {})
        return {
            'effort_score':          round(_f(row.get('effort_score')), 1),
            'academic_performance':  round(_f(row.get('academic_performance')), 1),
            'overall_att_pct':       round(_f(row.get('overall_att_pct')), 1),
            'risk_of_detention':     round(_f(row.get('risk_of_detention')), 1),
            'risk_score':            row.get('risk_score'),
            'weekly_att_pct':        round(_f(row.get('weekly_att_pct')), 1),
            'quiz_avg_pct':          round(_f(row.get('quiz_avg_pct')), 1),
            'assn_avg_pct':          round(_f(row.get('assn_avg_pct')), 1),
            'assn_submit_rate':      round(_f(row.get('assn_submit_rate')), 3),
            'quiz_attempt_rate':     round(_f(row.get('quiz_attempt_rate')), 3),
        }

    def _pct_change(prev_val, curr_val):
        """Signed pct change curr vs prev. None if prev is 0."""
        if prev_val == 0:
            return None
        return round((curr_val - prev_val) / abs(prev_val) * 100, 1)

    prior_to_prev_desc = sorted(prior_to_prev, reverse=True)
    prior_to_curr_desc = sorted(prior_to_curr, reverse=True)

    # ── Build result ──────────────────────────────────────────────────────────
    result = {}

    for flag in prev_flags:
        sid  = flag.student_id
        name = names.get(sid, sid)

        wm      = all_metrics.get(sid, {})
        prev_m  = wm.get(prev_week, {})
        curr_m  = wm.get(sem_week, {})

        # trajectory for avg effort/perf
        traj = [wm[w] for w in sorted(wm.keys()) if w <= prev_week]
        week_et   = [_f(r.get('effort_score'))          for r in traj]
        week_perf = [_f(r.get('academic_performance'))  for r in traj]
        avg_et    = round(sum(week_et)   / max(len(week_et),   1), 2)
        avg_perf  = round(sum(week_perf) / max(len(week_perf), 1), 2)
        avg_risk  = _cap(flag.urgency_score)

        # Signals for both weeks
        signals_prev = _compute_signals(sid, prev_week, prior_to_prev_desc, cls_ratio_prev)
        signals_curr = _compute_signals(sid, sem_week,  prior_to_curr_desc, cls_ratio_curr)

        # Top signal from prev week (face of the card)
        top_key, top_val_prev = _top_signal(signals_prev)
        top_val_curr = signals_curr[top_key][0]

        # Pct change of the top signal's RAW value from prev → curr
        top_pct_change = _pct_change(top_val_prev, top_val_curr)

        # Week snapshots for the overlay comparison table
        snap_prev = _week_metrics_snapshot(sid, prev_week)
        snap_curr = _week_metrics_snapshot(sid, sem_week)

        # Risk score deltas
        rs_prev = float(prev_m.get('risk_score') or avg_risk)
        rs_curr = float(curr_m.get('risk_score') or rs_prev)

        curr_flag = curr_flags_map.get(sid)

        result[flag.id] = {
            'basic_details': [sid, name, flag.diagnosis, flag.risk_tier],
            'more': {
                'avg_risk_score':           avg_risk,
                'avg_effort':               avg_et,
                'avg_academic_performance': avg_perf,
                'overall_attendance':       round(_f(prev_m.get('overall_att_pct')), 1),
                'risk_of_detention':        round(_f(prev_m.get('risk_of_detention')), 1),
                'mid_term_score':           _get_midterm_score(sid, semester),
                'flagged_again': sid in curr_flags_map,
            },

            # Card face
            'top_signal': {
                'key':        top_key,
                'value':      top_val_prev,        # value at flag week (prev)
                'value_curr': top_val_curr,        # value this week
                'pct_change': top_pct_change,      # signed %, None if div-by-zero
            },

            # Full metric rows for overlay (week N and week N+1)
            'week_n':    {'week': prev_week, **snap_prev},
            'week_n1':   {'week': sem_week,  **snap_curr},

            # Risk score breakdown for both weeks
            'risk_breakdown_prev': _to_breakdown(signals_prev),
            'risk_breakdown_curr': _to_breakdown(signals_curr),

            # Deltas (kept for backward compat)
            'this_week_vs_last_week': {
                'effort': {
                    'E_t_previous': _f(prev_m.get('effort_score')),
                    'delta_E_t':    round(_f(curr_m.get('effort_score')) - _f(prev_m.get('effort_score')), 2),
                },
                'performance': {
                    'A_t_previous': _f(prev_m.get('academic_performance')),
                    'delta_A_t':    round(_f(curr_m.get('academic_performance')) - _f(prev_m.get('academic_performance')), 2),
                },
                'risk_score': {
                    'risk_score_previous': rs_prev,
                    'delta_risk_score':    round(rs_curr - rs_prev, 1),
                },
            },
        }

    return Response(result)

      


# ─────────────────────────────────────────────────────────────────────────────
#  4. STUDENT ROSTER  — all_students  +  detainment_risk
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
def all_students(request):
    """
    GET /api/analysis/students/all/?class_id=X&semester=Y&sem_week=Z

    Returns whatever metrics are available on or before this week in the same
    semester. Previous-semester midterm scores are NOT included.

    {
        student_map          : { student_id: name },
        A_t                  : { student_id: value | False },
        E_t                  : { student_id: value | False },
        risk_score           : { student_id: value | False },
        predicted_midterm_score : { student_id: value | False },
        actual_midterm_score    : { student_id: value | False },
        predicted_endterm_score : { student_id: value | False },
        actual_endterm_score    : { student_id: value | False },
        attendance:           {student_id:value | False}
    }
    """
    params, err = _require(request, 'class_id', 'semester', 'sem_week')
    if err:
        return err

    class_id = params['class_id']
    semester = int(params['semester'])
    sem_week = int(params['sem_week'])
    metrics_qs = list(weekly_metrics.objects.filter(
        class_id=class_id, semester=semester, sem_week=sem_week
        ).values('student_id', 'effort_score', 'academic_performance', 'risk_score', 'overall_att_pct'))

    # Latest midterm / endterm predictions (only this semester)
    pmt_map = {}
    for p in pre_mid_term.objects.filter(
        class_id=class_id, semester=semester, sem_week__lte=sem_week
    ).order_by('-sem_week'):
        if p.student_id not in pmt_map:
            pmt_map[p.student_id] = _f(p.predicted_midterm_score)

    pet_map = {}
    for p in pre_end_term.objects.filter(
        class_id=class_id, semester=semester
    ).order_by('-sem_week'):
        if p.student_id not in pet_map:
            pet_map[p.student_id] = _f(p.predicted_endterm_score)

    names = _name_map(class_id)
    student_ids = [m['student_id'] for m in metrics_qs]

    student_map          = {}
    A_t_map              = {}
    E_t_map              = {}
    risk_score_map       = {}
    pred_midterm_map     = {}
    actual_midterm_map   = {}
    pred_endterm_map     = {}
    actual_endterm_map   = {}
    att_map = {}

    # ✅ FIX: Fetch all exam scores in 2 queries total
    from .client_models import ClientExamSchedule, ClientExamResult
    if HAS_CLIENT_DB:
        try:
            mid_schedule = ClientExamSchedule.objects.using('client_db').filter(
                exam_type='MIDTERM', semester=semester
            ).first()
            end_schedule = ClientExamSchedule.objects.using('client_db').filter(
                exam_type='ENDTERM', semester=semester
            ).first()
            
            bulk_mid = {}
            if mid_schedule:
                for r in ClientExamResult.objects.using('client_db').filter(
                    student_id__in=student_ids, exam_id=mid_schedule.exam_id
                ):
                    bulk_mid[r.student_id] = _f(r.score_pct)
            
            bulk_end = {}
            if end_schedule:
                for r in ClientExamResult.objects.using('client_db').filter(
                    student_id__in=student_ids, exam_id=end_schedule.exam_id
                ):
                    bulk_end[r.student_id] = _f(r.score_pct)
        except Exception:
            bulk_mid, bulk_end = {}, {}
    else:
        bulk_mid, bulk_end = {}, {}

    for m in metrics_qs:
        sid = m['student_id']
        student_map[sid]        = names.get(sid, sid)
        A_t_map[sid]            = _f(m['academic_performance']) or False
        E_t_map[sid]            = _f(m['effort_score']) or False
        risk_score_map[sid]     = m['risk_score'] if m['risk_score'] is not None else False
        pred_midterm_map[sid]   = pmt_map.get(sid, False)
        pred_endterm_map[sid]   = pet_map.get(sid, False)
        actual_midterm_map[sid] = _get_midterm_score(sid, semester)
        actual_endterm_map[sid] = _get_endterm_score(sid, semester)
        att_map[sid] = _f(m['overall_att_pct']) or False

    return Response({
        'student_map':              student_map,
        'A_t':                      A_t_map,
        'E_t':                      E_t_map,
        'risk_score':               risk_score_map,
        'predicted_midterm_score':  pred_midterm_map,
        'actual_midterm_score':     actual_midterm_map,
        'predicted_endterm_score':  pred_endterm_map,
        'actual_endterm_score':     actual_endterm_map,
        'overall_att_pct': att_map
    })


# ─────────────────────────────────────────────────────────────────────────────
#  5b. STUDENT DETAIL  — student_detail
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
def student_detail(request, student_id):
    """
    GET /api/analysis/students/<student_id>/?class_id=X&semester=Y&sem_week=Z

    Returns the same shape as expand_flag but keyed by student_id rather than
    flag_id, so it works for any student (flagged or not).

    {
        student_id        : str,
        name              : str,
        avatar            : str,
        risk_level        : str,          # 'high' | 'med' | 'safe'
        student_overview  : {
            avg_risk_score, avg_effort, avg_academic_performance,
            overall_attendance, risk_of_detention, risk_of_failing,
            mid_term_score
        },
        student_summary   : str | null,   # AI narrative
        flagging_history  : {
            total_flags     : int,
            interventions   : int,
            by_week         : { sem_week: { diagnosis, did_we_intervene } }
        },
        trends            : { E_t: { week: val }, A_t: { week: val | False } },
        effort_vs_performance : {
            avg_effort_of_class, avg_performance_of_class,
            avg_effort_of_student, avg_performance_of_student,
            E_t, A_t
        },
    }
    """
    params, err = _require(request, 'class_id', 'semester', 'sem_week')
    if err:
        return err

    class_id = params['class_id']
    semester = int(params['semester'])
    sem_week = int(params['sem_week'])

    # ── Latest metrics for this student ──────────────────────────────────────
    m = (
        weekly_metrics.objects
        .filter(student_id=student_id, semester=semester, sem_week__lte=sem_week)
        .order_by('-sem_week')
        .first()
    )
    if not m:
        return Response({'error': f'No metrics found for student {student_id}'}, status=404)

    # ── Historical trajectory ─────────────────────────────────────────────────
    traj = list(
        weekly_metrics.objects
        .filter(student_id=student_id, semester=semester, sem_week__lte=sem_week)
        .order_by('sem_week')
        .values('sem_week', 'effort_score', 'academic_performance',
                'overall_att_pct', 'risk_of_detention', 'risk_score')
    )

    week_et  = [_f(r['effort_score'])        for r in traj]
    week_at  = [_f(r['academic_performance']) for r in traj]

    avg_effort      = round(sum(week_et) / max(len(week_et), 1), 2)
    avg_performance = round(sum(week_at) / max(len(week_at), 1), 2)
    overall_att     = round(_f(m.overall_att_pct), 1)
    risk_detention  = round(_f(m.risk_of_detention), 1)
    risk_failing    = round(_f(m.risk_score), 1)
    midterm_score   = _get_midterm_score(student_id, semester)

    # ── Class averages ────────────────────────────────────────────────────────
    cls_agg = weekly_metrics.objects.filter(
        class_id=class_id, semester=semester, sem_week=sem_week
    ).aggregate(avg_et=Avg('effort_score'), avg_perf=Avg('academic_performance'))
    class_avg_et   = _f(cls_agg['avg_et'],   65.0)
    class_avg_perf = _f(cls_agg['avg_perf'], 70.0)

    cls_perf_vals = list(
        weekly_metrics.objects.filter(
            class_id=class_id, semester=semester, sem_week=sem_week
        ).values_list('academic_performance', flat=True)
    )
    class_perf_mean = round(
        sum(_f(v) for v in cls_perf_vals) / max(len(cls_perf_vals), 1), 2
    )

    # ── Most recent flag (if any) ─────────────────────────────────────────────
    latest_flag = (
        weekly_flags.objects
        .filter(student_id=student_id, semester=semester, sem_week__lte=sem_week)
        .order_by('-sem_week', '-urgency_score')
        .first()
    )

    # ── AI summary ────────────────────────────────────────────────────────────
    flag_count_qs = weekly_flags.objects.filter(
        student_id=student_id, semester=semester
    ).order_by('sem_week')

    student_data = {
        'E_t':                  _f(m.effort_score),
        'A_t':                  _f(m.academic_performance) or None,
        'reasons_for_flagging': latest_flag.diagnosis if latest_flag else 'No flags',
        'urgency_score':        float(latest_flag.urgency_score or 0) if latest_flag else 0.0,
        'risk_score':           float(latest_flag.urgency_score or 0) if latest_flag else 0.0,
        'E_t_history':          week_et[:-1],
        'A_t_history':          [x for x in week_at if x > 0],
        'E':                    class_avg_et,
        'A':                    class_avg_perf,
        'e':                    avg_effort,
        'a':                    avg_performance,
        'del_E':                round(avg_effort - class_avg_et, 2),
        'del_A':                round(avg_performance - class_avg_perf, 2),
        'flagging_history': {
            'times_flagged':         flag_count_qs.count(),
            'weeks_since_each_flag': [
                sem_week - f['sem_week']
                for f in flag_count_qs.values('sem_week')
            ],
        },
        'effort_contributors_student': {
            'avg_library_visits':         _f(m.library_visits),
            'avg_book_borrows':           _f(m.book_borrows),
            'avg_attendance_pct':         _f(m.overall_att_pct) / 100,
            'avg_assignment_submit_rate': _f(getattr(m, 'assn_submit_rate', None)),
            'avg_plagiarism_free_rate':   1 - _f(getattr(m, 'assn_plagiarism_pct', None)) / 100,
            'avg_quiz_attempt_rate':      _f(m.quiz_attempt_rate),
        },
        'effort_contributors_class': {
            'avg_library_visits':         1.8,
            'avg_book_borrows':           0.9,
            'avg_attendance_pct':         class_avg_et / 100,
            'avg_assignment_submit_rate': 0.87,
            'avg_plagiarism_free_rate':   0.91,
            'avg_quiz_attempt_rate':      0.78,
        },
    }

    ai_summary = None
    try:
        from .aiviews import student_summary as ai_student_summary
        ai_summary = ai_student_summary(student_data)
    except Exception:
        ai_summary = None

    # ── Flagging history ──────────────────────────────────────────────────────
    intervened_weeks = set(
        intervention_log.objects.filter(
            student_id=student_id, semester=semester, advisor_notified=True
        ).values_list('sem_week', flat=True)
    )
    all_flags_qs = (
        weekly_flags.objects
        .filter(student_id=student_id, semester=semester)
        .order_by('sem_week')
        .values('sem_week', 'diagnosis')
    )
    flagging_by_week = {
        f['sem_week']: {
            'diagnosis':        f['diagnosis'],
            'did_we_intervene': f['sem_week'] in intervened_weeks,
        }
        for f in all_flags_qs
    }
    total_interventions = intervention_log.objects.filter(
        student_id=student_id, semester=semester
    ).count()

    # ── Trends ────────────────────────────────────────────────────────────────
    trends = {
        'E_t': {r['sem_week']: _f(r['effort_score'])                      for r in traj},
        'A_t': {r['sem_week']: _f(r['academic_performance']) or False      for r in traj},
    }

    # ── Names / avatar ────────────────────────────────────────────────────────
    names = _name_map(class_id)
    name  = names.get(student_id, student_id)

    return Response({
        'student_id':    student_id,
        'name':          name,
        'avatar':        _avatar(name),
        'risk_level':    _risk_level(latest_flag.risk_tier if latest_flag else ''),
        'student_overview': {
            'avg_risk_score':           _cap(latest_flag.urgency_score) if latest_flag else 0,
            'avg_effort':               avg_effort,
            'avg_academic_performance': avg_performance,
            'overall_attendance':       overall_att,
            'risk_of_detention':        risk_detention,
            'risk_of_failing':          risk_failing,
            'mid_term_score':           midterm_score,
        },
        'student_summary':    ai_summary,
        'flagging_history': {
            'total_flags':    flag_count_qs.count(),
            'interventions':  total_interventions,
            'by_week':        flagging_by_week,
        },
        'trends':             trends,
        'effort_vs_performance': {
            'avg_effort_of_class':        round(class_avg_et, 2),
            'avg_performance_of_class':   round(class_perf_mean, 2),
            'avg_effort_of_student':      avg_effort,
            'avg_performance_of_student': avg_performance,
            'E_t': _f(m.effort_score),
            'A_t': _f(m.academic_performance),
        },
    })


@api_view(['GET'])
def detainment_risk(request):
    """
    GET /api/analysis/students/detainment_risk/?class_id=X&semester=Y&sem_week=Z

    Returns all students with risk_of_detention >= 50% for the given week.

    {
        student_id: {
            risk_score    : float,   # risk_of_detention value
            attendance_pct: float    # overall_att_pct (not weekly)
        }
    }
    """
    params, err = _require(request, 'class_id', 'semester', 'sem_week')
    if err:
        return err

    class_id    = params['class_id']
    semester    = int(params['semester'])
    latest_week = int(params['sem_week'])

    if not latest_week:
        return Response({})

    at_risk = weekly_metrics.objects.filter(
        class_id=class_id,
        semester=semester,
        sem_week=latest_week,
    ).values('student_id', 'risk_of_detention', 'overall_att_pct')

    result = {
        m['student_id']: {
            'risk_score':     round(_f(m['risk_of_detention']), 1),
            'attendance_pct': round(_f(m['overall_att_pct']), 1),
        }
        for m in at_risk
    }
    if result:
        return Response(result)
    

# ─────────────────────────────────────────────────────────────────────────────
#  5. EVENT REPORTS
# ─────────────────────────────────────────────────────────────────────────────

def _marks_distribution(scores: list) -> dict:
    """Build marks distribution dict from a list of float scores (0-100)."""
    buckets = {
        'lt_40':   0,
        '40to50':  0,
        '51to60':  0,
        '61to70':  0,
        '71to80':  0,
        '81to90':  0,
        '91to100': 0,
    }
    for s in scores:
        if s < 40:
            buckets['lt_40']   += 1
        elif s <= 50:
            buckets['40to50']  += 1
        elif s <= 60:
            buckets['51to60']  += 1
        elif s <= 70:
            buckets['61to70']  += 1
        elif s <= 80:
            buckets['71to80']  += 1
        elif s <= 90:
            buckets['81to90']  += 1
        else:
            buckets['91to100'] += 1
    return buckets


def _mode(scores: list) -> float:
    """Return the mode of a list of floats (rounded to nearest 5)."""
    if not scores:
        return 0.0
    rounded = [round(s / 5) * 5 for s in scores]
    return max(set(rounded), key=rounded.count)


@api_view(['GET'])
def pre_midterm_report(request):
    """
    GET /api/analysis/reports/pre_midterm/?class_id=X&semester=Y

    {
        marks_distribution : { lt_40, 40to50, … 91to100 },
        mean_predicted_score      : float,
        standard_deviation        : float,
        mode_marks                : float,
        top20_pct                 : float,
        bottom20_pct              : float,
        watchlist                 : { student_id: [name, reason, predicted_score, risk_level] }
    }
    """
    params, err = _require(request, 'class_id', 'semester')
    if err:
        return err

    class_id = params['class_id']
    semester = int(params['semester'])

    pmt_qs = list(
        pre_mid_term.objects.filter(class_id=class_id, semester=semester)
        .order_by('student_id', '-sem_week')
    )
    # One entry per student (latest prediction)
    seen = {}
    for p in pmt_qs:
        if p.student_id not in seen:
            seen[p.student_id] = _f(p.predicted_midterm_score)

    scores = list(seen.values())
    if not scores:
        return Response({'error': 'No pre-midterm predictions found'}, status=404)

    names   = _name_map(class_id)
    mean    = round(sum(scores) / len(scores), 2)
    n       = len(scores)
    std_dev = round((sum((s - mean) ** 2 for s in scores) / max(n, 1)) ** 0.5, 2)

    sorted_scores = sorted(scores)
    cutoff_20_pct = max(1, n // 5)
    top20_score   = round(sorted_scores[-cutoff_20_pct], 2) if sorted_scores else 0.0
    bot20_score   = round(sorted_scores[cutoff_20_pct - 1], 2) if sorted_scores else 0.0

    # Watchlist: students predicted below 50 or flagged this semester
    flagged_sids = set(
        weekly_flags.objects.filter(class_id=class_id, semester=semester)
        .values_list('student_id', flat=True)
    )
    watchlist = {}
    for sid, score in seen.items():
        flag = weekly_flags.objects.filter(
            student_id=sid, semester=semester
        ).order_by('-urgency_score').first()
        if score < 50 or sid in flagged_sids:
            watchlist[sid] = [
                names.get(sid, sid),
                flag.diagnosis if flag else 'Low predicted score',
                score,
                _risk_level(flag.risk_tier if flag else ''),
            ]

    return Response({
        'marks_distribution':      _marks_distribution(scores),
        'mean_predicted_score':    mean,
        'standard_deviation':      std_dev,
        'mode_marks':              _mode(scores),
        'top20_pct':               top20_score,
        'bottom20_pct':            bot20_score,
        'watchlist':               watchlist,
    })


@api_view(['GET'])
def post_midterm_report(request):
    """
    GET /api/analysis/reports/post_midterm/?class_id=X&semester=Y

    {
        marks_distribution : { lt_40: {predicted, actual}, … },
        avg_score          : float,
        standard_deviation : float,
        mode_score         : float,
        bottom20_pct       : float,
        top20_pct          : float,
        underperformers    : { student_id: [name, {predicted_score, actual_score}] },
        outperformers      : { student_id: [name, {predicted_score, actual_score}] }
    }
    """
    params, err = _require(request, 'class_id', 'semester')
    if err:
        return err

    class_id = params['class_id']
    semester = int(params['semester'])

    if not HAS_CLIENT_DB:
        return Response({'error': 'Client DB not available'}, status=503)

    # Collect predictions
    pmt_map = {}
    for p in pre_mid_term.objects.filter(class_id=class_id, semester=semester).order_by('-sem_week'):
        if p.student_id not in pmt_map:
            pmt_map[p.student_id] = _f(p.predicted_midterm_score)

    if not pmt_map:
        return Response({'error': 'No midterm predictions found'}, status=404)

    # Collect actual scores
    actual_map = {sid: _get_midterm_score(sid, semester) for sid in pmt_map}
    actual_scores = [v for v in actual_map.values() if v is not False]

    if not actual_scores:
        return Response({'error': 'No actual midterm scores found yet'}, status=404)

    names  = _name_map(class_id)
    mean   = round(sum(actual_scores) / len(actual_scores), 2)
    n      = len(actual_scores)
    std    = round((sum((s - mean) ** 2 for s in actual_scores) / max(n, 1)) ** 0.5, 2)

    sorted_a  = sorted(actual_scores)
    cut       = max(1, n // 5)
    top20     = round(sorted_a[-cut], 2)
    bot20     = round(sorted_a[cut - 1], 2)

    # Distribution with predicted vs actual per bucket
    pred_scores  = [pmt_map.get(sid, 0) for sid in pmt_map]
    dist_pred    = _marks_distribution(pred_scores)
    dist_actual  = _marks_distribution(actual_scores)
    distribution = {
        bucket: {
            'predicted_number_of_students': dist_pred[bucket],
            'actual_number_of_students':    dist_actual[bucket],
        }
        for bucket in dist_pred
    }

    underperformers = {}
    outperformers   = {}
    for sid, pred in pmt_map.items():
        actual = actual_map.get(sid)
        if actual is False:
            continue
        delta = actual - pred
        entry = [names.get(sid, sid), {'predicted_score': pred, 'actual_score': actual}]
        if delta <= -10:
            underperformers[sid] = entry
        elif delta >= 10:
            outperformers[sid] = entry

    return Response({
        'marks_distribution': distribution,
        'avg_score':          mean,
        'standard_deviation': std,
        'mode_score':         _mode(actual_scores),
        'bottom20_pct':       bot20,
        'top20_pct':          top20,
        'underperformers':    underperformers,
        'outperformers':      outperformers,
    })


@api_view(['GET'])
def pre_endterm_report(request):
    """
    GET /api/analysis/reports/pre_endterm/?class_id=X&semester=Y

    Same shape as pre_midterm_report but includes midterm_score in watchlist.
    """
    params, err = _require(request, 'class_id', 'semester')
    if err:
        return err

    class_id = params['class_id']
    semester = int(params['semester'])

    pet_qs = list(
        pre_end_term.objects.filter(class_id=class_id, semester=semester)
        .order_by('student_id', '-sem_week')
    )
    seen = {}
    for p in pet_qs:
        if p.student_id not in seen:
            seen[p.student_id] = _f(p.predicted_endterm_score)

    scores = list(seen.values())
    if not scores:
        return Response({'error': 'No pre-endterm predictions found'}, status=404)

    names   = _name_map(class_id)
    mean    = round(sum(scores) / len(scores), 2)
    n       = len(scores)
    std_dev = round((sum((s - mean) ** 2 for s in scores) / max(n, 1)) ** 0.5, 2)

    sorted_scores = sorted(scores)
    cut       = max(1, n // 5)
    top20     = round(sorted_scores[-cut], 2) if sorted_scores else 0.0
    bot20     = round(sorted_scores[cut - 1], 2) if sorted_scores else 0.0

    flagged_sids = set(
        weekly_flags.objects.filter(class_id=class_id, semester=semester)
        .values_list('student_id', flat=True)
    )
    watchlist = {}
    for sid, score in seen.items():
        flag = weekly_flags.objects.filter(
            student_id=sid, semester=semester
        ).order_by('-urgency_score').first()
        midterm = _get_midterm_score(sid, semester)
        if score < 50 or sid in flagged_sids:
            watchlist[sid] = [
                names.get(sid, sid),
                flag.diagnosis if flag else 'Low predicted score',
                score,
                _risk_level(flag.risk_tier if flag else ''),
                midterm,
            ]

    return Response({
        'marks_distribution':   _marks_distribution(scores),
        'mean_predicted_score': mean,
        'standard_deviation':   std_dev,
        'mode_marks':           _mode(scores),
        'top20_pct':            top20,
        'bottom20_pct':         bot20,
        'watchlist':            watchlist,
    })


@api_view(['GET'])
def post_endterm_report(request):
    """
    GET /api/analysis/reports/post_endterm/?class_id=X&semester=Y

    Same shape as post_midterm_report but for endterm scores.
    """
    params, err = _require(request, 'class_id', 'semester')
    if err:
        return err

    class_id = params['class_id']
    semester = int(params['semester'])

    if not HAS_CLIENT_DB:
        return Response({'error': 'Client DB not available'}, status=503)

    pet_map = {}
    for p in pre_end_term.objects.filter(class_id=class_id, semester=semester).order_by('-sem_week'):
        if p.student_id not in pet_map:
            pet_map[p.student_id] = _f(p.predicted_endterm_score)

    if not pet_map:
        return Response({'error': 'No endterm predictions found'}, status=404)

    actual_map    = {sid: _get_endterm_score(sid, semester) for sid in pet_map}
    actual_scores = [v for v in actual_map.values() if v is not False]

    if not actual_scores:
        return Response({'error': 'No actual endterm scores found yet'}, status=404)

    names = _name_map(class_id)
    mean  = round(sum(actual_scores) / len(actual_scores), 2)
    n     = len(actual_scores)
    std   = round((sum((s - mean) ** 2 for s in actual_scores) / max(n, 1)) ** 0.5, 2)

    sorted_a = sorted(actual_scores)
    cut      = max(1, n // 5)
    top20    = round(sorted_a[-cut], 2)
    bot20    = round(sorted_a[cut - 1], 2)

    pred_scores = [pet_map.get(sid, 0) for sid in pet_map]
    dist_pred   = _marks_distribution(pred_scores)
    dist_actual = _marks_distribution(actual_scores)
    distribution = {
        bucket: {
            'predicted_number_of_students': dist_pred[bucket],
            'actual_number_of_students':    dist_actual[bucket],
        }
        for bucket in dist_pred
    }

    underperformers = {}
    outperformers   = {}
    for sid, pred in pet_map.items():
        actual = actual_map.get(sid)
        if actual is False:
            continue
        delta = actual - pred
        entry = [names.get(sid, sid), {'predicted_score': pred, 'actual_score': actual}]
        if delta <= -10:
            underperformers[sid] = entry
        elif delta >= 10:
            outperformers[sid] = entry

    return Response({
        'marks_distribution': distribution,
        'avg_score':          mean,
        'standard_deviation': std,
        'mode_score':         _mode(actual_scores),
        'bottom20_pct':       bot20,
        'top20_pct':          top20,
        'underperformers':    underperformers,
        'outperformers':      outperformers,
    })





# ─────────────────────────────────────────────────────────────────────────────
#  INTERNAL
# ─────────────────────────────────────────────────────────────────────────────

import threading

_calibration_status = {"running": False, "result": None, "error": None}

@csrf_exempt
def trigger_calibrate(request):
    """POST /api/analysis/calibrate/"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    secret          = os.getenv('INTERNAL_SECRET')
    provided_secret = request.headers.get('X-Internal-Secret')
    if secret and provided_secret != secret:
        return JsonResponse({'error': 'forbidden'}, status=403)

    if _calibration_status["running"]:
        return JsonResponse({"status": "already_running"}, status=202)

    def run():
        _calibration_status["running"] = True
        _calibration_status["result"]  = None
        _calibration_status["error"]   = None
        try:
            result = calibrate()
            from accounts.addingdata import sync
            sync()
            _calibration_status["result"] = result
        except Exception as e:
            print(f'[FATAL] calibrate() raised:\n{traceback.format_exc()}')
            _calibration_status["error"] = str(e)
        finally:
            _calibration_status["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return JsonResponse({"status": "started"}, status=202)


@csrf_exempt
def calibrate_status(request):
    """GET /api/analysis/calibrate/status/"""
    return JsonResponse({
        "running": _calibration_status["running"],
        "result":  _calibration_status["result"],
        "error":   _calibration_status["error"],
    })
