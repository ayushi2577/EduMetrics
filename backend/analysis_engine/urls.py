"""
EduMetrics — analysis_engine/urls.py  (v3 — newViews contract)

All routes under /api/analysis/  (configured in config/urls.py).

NEW ENDPOINTS (match newViews.py frontend contract)
────────────────────────────────────────────────────
GET  dashboard/summary/                  →  dashboard_summary
GET  dashboard/class_summary/            →  class_summary_view       (AI narrative)

GET  interventions/                      →  interventions_list
POST interventions/log/                  →  log_intervention

GET  flags/weekly/                       →  weekly_flags_view
GET  flags/<flag_id>/expand/             →  expand_flag
GET  flags/last_week/                    →  last_weeks_flags

GET  students/all/                       →  all_students
GET  students/detainment_risk/           →  detainment_risk

GET  reports/pre_midterm/                →  pre_midterm_report
GET  reports/post_midterm/               →  post_midterm_report
GET  reports/pre_endterm/                →  pre_endterm_report
GET  reports/post_endterm/               →  post_endterm_report

INTERNAL
────────
POST trigger_calibrate/                 →  trigger_calibrate
"""

from django.urls import path
from .views import (
    # ── NEW: Dashboard ──────────────────────────────────────────────────────
    dashboard_summary,
    class_summary_view,

    # ── NEW: Interventions ──────────────────────────────────────────────────
    interventions_list,
    log_intervention,

    # ── NEW: Flags ──────────────────────────────────────────────────────────
    weekly_flags_view,
    expand_flag,
    last_weeks_flags,

    # ── NEW: Students ───────────────────────────────────────────────────────
    all_students,
    detainment_risk,
    student_detail,

    # ── NEW: Reports ────────────────────────────────────────────────────────
    pre_midterm_report,
    post_midterm_report,
    pre_endterm_report,
    post_endterm_report,


    # ── Internal ────────────────────────────────────────────────────────────
    trigger_calibrate,
)

urlpatterns = [

    # ══════════════════════════════════════════════════════════════════════════
    #  NEW ENDPOINTS  (newViews.py contract)
    # ══════════════════════════════════════════════════════════════════════════

    # ── Dashboard ─────────────────────────────────────────────────────────────
    # GET /api/analysis/dashboard/summary/?class_id=X&semester=Y&sem_week=Z
    path('dashboard/summary/', dashboard_summary, name='dashboard_summary'),

    # GET /api/analysis/dashboard/class_summary/?class_id=X&semester=Y&sem_week=Z
    path('dashboard/class_summary/', class_summary_view, name='class_summary'),

    # ── Interventions ─────────────────────────────────────────────────────────
    # GET  /api/analysis/interventions/?class_id=X&semester=Y&sem_week=Z
    path('interventions/', interventions_list, name='interventions_list'),

    # POST /api/analysis/interventions/log/
    # Body: { flag_id, intervention, timestamp }
    path('interventions/log/', log_intervention, name='log_intervention'),

    # ── Flags ─────────────────────────────────────────────────────────────────
    # GET /api/analysis/flags/weekly/?class_id=X&semester=Y&sem_week=Z
    path('flags/weekly/', weekly_flags_view, name='weekly_flags'),

    # GET /api/analysis/flags/<flag_id>/expand/?semester=Y&sem_week=Z
    path('flags/<int:flag_id>/expand/', expand_flag, name='expand_flag'),

    # GET /api/analysis/flags/last_week/?class_id=X&semester=Y&sem_week=Z
    path('flags/last_week/', last_weeks_flags, name='last_weeks_flags'),

    # ── Students ──────────────────────────────────────────────────────────────
    # GET /api/analysis/students/all/?class_id=X&semester=Y&sem_week=Z
    path('students/all/', all_students, name='all_students'),

    # GET /api/analysis/students/detainment_risk/?class_id=X&semester=Y
    path('students/detainment_risk/', detainment_risk, name='detainment_risk'),

    # GET /api/analysis/students/<str:student_id>/?class_id=X&semester=Y
    path('students/<str:student_id>/', student_detail, name='student_detail'),

    # ── Reports ───────────────────────────────────────────────────────────────
    # GET /api/analysis/reports/pre_midterm/?class_id=X&semester=Y
    path('reports/pre_midterm/', pre_midterm_report, name='pre_midterm_report'),

    # GET /api/analysis/reports/post_midterm/?class_id=X&semester=Y
    path('reports/post_midterm/', post_midterm_report, name='post_midterm_report'),

    # GET /api/analysis/reports/pre_endterm/?class_id=X&semester=Y
    path('reports/pre_endterm/', pre_endterm_report, name='pre_endterm_report'),

    # GET /api/analysis/reports/post_endterm/?class_id=X&semester=Y
    path('reports/post_endterm/', post_endterm_report, name='post_endterm_report'),


    # ── Internal ──────────────────────────────────────────────────────────────
    # POST /api/analysis/trigger_calibrate/
    path('trigger_calibrate/', trigger_calibrate, name='trigger_calibrate'),
]
