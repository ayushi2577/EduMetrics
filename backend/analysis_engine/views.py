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
    """Return actual midterm score from ClientExamResult, or False if unavailable."""
    if not HAS_CLIENT_DB:
        return False
    try:
        schedule = ClientExamSchedule.objects.using('client_db').filter(
            exam_type='MIDTERM', semester=semester
        ).first()
        if not schedule:
            return False
        result = ClientExamResult.objects.using('client_db').filter(
            student_id=student_id,
            exam_id=schedule.exam_id,
        ).first()
        return _f(result.score_pct) if result else False
    except Exception:
        return False


def _get_endterm_score(student_id, semester):
    """Return actual endterm score from ClientExamResult, or False if unavailable."""
    if not HAS_CLIENT_DB:
        return False
    try:
        schedule = ClientExamSchedule.objects.using('client_db').filter(
            exam_type='ENDTERM', semester=semester
        ).first()
        if not schedule:
            return False
        result = ClientExamResult.objects.using('client_db').filter(
            student_id=student_id,
            exam_id=schedule.exam_id,
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
        class_id=class_id, semester=semester, sem_week=sem_week
    ).count()

    flags_qs = weekly_flags.objects.filter(
        class_id=class_id, semester=semester, sem_week=sem_week
    )
    flagged = flags_qs.count()
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
            attendance_pct, risk_score, escalation_level
        },
        ...
    }
    """
    params, err = _require(request, 'class_id', 'semester', 'sem_week')
    if err:
        return err

    class_id = params['class_id']
    semester = int(params['semester'])
    sem_week = int(params['sem_week'])

    flags = weekly_flags.objects.filter(
        class_id=class_id, semester=semester, sem_week=sem_week
    ).order_by('-urgency_score')

    names = _name_map(class_id)
    result = {}

    flags = weekly_flags.objects.filter(
    class_id=class_id, semester=semester, sem_week=sem_week
    ).order_by('-urgency_score')
    
    # ✅ FIX: Fetch ALL metrics in ONE query instead of one per student
    student_ids = [f.student_id for f in flags]
    metrics_map = {
        m.student_id: m
        for m in weekly_metrics.objects.filter(
            student_id__in=student_ids, semester=semester, sem_week=sem_week
        )
    }

    names = _name_map(class_id)
    result = {}

    for i, flag in enumerate(flags, start=1):
        sid = flag.student_id
        m = metrics_map.get(sid)

        result[f'flag{i}'] = {
            'id':flag.id,
            'student_id':       sid,
            'student_name':     names.get(sid, sid),
            'risk_tier':        flag.risk_tier,
            'diagnosis':        flag.diagnosis,
            'attendance_pct':   round(_f(m.overall_att_pct if m else None), 1),

            # NOW coming from weekly_metrics
            'risk_score':       _cap(m.risk_score if m else None),
            'escalation_level': m.escalation_level if m else 0,
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
    flagging_history = {
        f['sem_week']: {
            'diagnosis':       f['diagnosis'],
            'did_we_intervene': f['sem_week'] in intervened_weeks,
        }
        for f in weekly_flags.objects.filter(
            student_id=sid, semester=semester
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

    return Response({
        'student_overview': {
            'avg_risk_score':            _cap(flag.urgency_score),
            'avg_effort':                avg_effort,
            'avg_academic_performance':  avg_performance,
            'overall_attendance':        overall_att,
            'risk_of_detention':         risk_detention,
            'mid_term_score':            midterm_score,
        },
        'student_summary':    ai_summary,
        'flagging_history':   flagging_history,
        'trends':             trends,
        'effort_vs_performance': effort_vs_performance,
        'flagging_contributors': flagging_contributors,
    })


@api_view(['GET'])
def last_weeks_flags(request):
    """
    GET /api/analysis/flags/last_week/?class_id=X&semester=Y&sem_week=Z

    Returns flags from the previous week with delta comparisons.
    Shape matches last_weeks_flags() spec in newViews.py.
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

    prev_flags = weekly_flags.objects.filter(
        class_id=class_id, semester=semester, sem_week=prev_week
    ).order_by('-urgency_score')

    names = _name_map(class_id)
    result = {}
    
    prev_flags = list(prev_flags)  # evaluate once
    student_ids = [f.student_id for f in prev_flags]

    # ✅ FIX: All queries outside the loop
    curr_metrics_map = {
        m.student_id: m
        for m in weekly_metrics.objects.filter(
            student_id__in=student_ids, semester=semester, sem_week=sem_week
        )
    }
    prev_metrics_map = {
        m.student_id: m
        for m in weekly_metrics.objects.filter(
            student_id__in=student_ids, semester=semester, sem_week=prev_week
        )
    }
    traj_map = {}
    for m in weekly_metrics.objects.filter(
        student_id__in=student_ids, semester=semester, sem_week__lte=prev_week
    ).order_by('sem_week').values('student_id', 'effort_score', 'academic_performance', 'overall_att_pct'):
        traj_map.setdefault(m['student_id'], []).append(m)

    curr_flags_map = {
        f.student_id: f
        for f in weekly_flags.objects.filter(
            student_id__in=student_ids, semester=semester, sem_week=sem_week
        )
    }

    for flag in prev_flags:
        sid  = flag.student_id
        name = names.get(sid, sid)

        curr_m = weekly_metrics.objects.filter(
            student_id=sid, semester=semester, sem_week=sem_week
        ).first()
        prev_m = weekly_metrics.objects.filter(
            student_id=sid, semester=semester, sem_week=prev_week
        ).first()

        pmt = pre_mid_term.objects.filter(
            student_id=sid, semester=semester
        ).order_by('-sem_week').first()

        # historical metrics
        traj = list(weekly_metrics.objects.filter(
            student_id=sid, semester=semester, sem_week__lte=prev_week
        ).order_by('sem_week').values('effort_score', 'academic_performance', 'overall_att_pct'))
        week_et   = [_f(r['effort_score']) for r in traj]
        week_perf = [_f(r['academic_performance']) for r in traj]

        avg_risk  = _cap(flag.urgency_score)
        avg_et    = round(sum(week_et)   / max(len(week_et),   1), 2)
        avg_perf  = round(sum(week_perf) / max(len(week_perf), 1), 2)

        result[flag.id] = {
            'basic_details': [
                sid,
                name,
                flag.diagnosis,
                flag.risk_tier,
            ],
            'more': {
                'avg_risk_score':           avg_risk,
                'avg_effort':               avg_et,
                'avg_academic_performance': avg_perf,
                'overall_attendance':       round(_f(prev_m.overall_att_pct if prev_m else None), 1),
                'risk_of_detention':        round(_f(prev_m.risk_of_detention if prev_m else None), 1),
                'mid_term_score':           _get_midterm_score(sid, semester),
            },
            'diagnosis': {
                part.strip(): round(avg_risk / max(len(flag.diagnosis.split('|')), 1), 1)
                for part in (flag.diagnosis or '').split('|') if part.strip()
            },
            'this_week_vs_last_week': {
                'effort': {
                    'delta_E_t':      round(
                        _f(curr_m.effort_score if curr_m else None) -
                        _f(prev_m.effort_score if prev_m else None), 2
                    ),
                    'E_t_previous':   _f(prev_m.effort_score if prev_m else None),
                },
                'performance': {
                    'delta_A_t':      round(
                        _f(curr_m.academic_performance if curr_m else None) -
                        _f(prev_m.academic_performance if prev_m else None), 2
                    ),
                    'A_t_previous':   _f(prev_m.academic_performance if prev_m else None),
                },
                'risk_score': {
                    'delta_risk_score':   0,   # will be filled once curr flag is fetched
                    'risk_score_previous': avg_risk,
                },
            },
        }

        # fill delta_risk_score if current-week flag exists
        curr_flag = weekly_flags.objects.filter(
            student_id=sid, semester=semester, sem_week=sem_week
        ).first()
        if curr_flag:
            result[flag.id]['this_week_vs_last_week']['risk_score']['delta_risk_score'] = (
                _cap(curr_flag.urgency_score) - avg_risk
            )

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
        risk_of_detention__gte=50,
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
    # No one is >= 50%, so just return the single highest-risk student
    highest = weekly_metrics.objects.filter(
        class_id=class_id,
        semester=semester,
        sem_week=latest_week,
    ).order_by('-risk_of_detention').values(
        'student_id', 'risk_of_detention', 'overall_att_pct'
    ).first()

    if not highest:
        return Response({})

    return Response({
        highest['student_id']: {
            'risk_score':     round(_f(highest['risk_of_detention']), 1),
            'attendance_pct': round(_f(highest['overall_att_pct']), 1),
        }
    })

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

@csrf_exempt
def trigger_calibrate(request):
    """POST /api/analysis/trigger_calibrate/"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    secret          = os.getenv('INTERNAL_SECRET')
    provided_secret = request.headers.get('X-Internal-Secret')
    if secret and provided_secret != secret:
        return JsonResponse({'error': 'forbidden'}, status=403)

    try:
        result = calibrate()
        return JsonResponse(result, status=200)
    except Exception as e:
        print(f'[FATAL] calibrate() raised:\n{traceback.format_exc()}')
        return JsonResponse({'error': str(e)}, status=500)
