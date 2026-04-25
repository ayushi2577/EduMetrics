"""
analysis_engine/models.py
==========================
Django models for the EduMetrics ANALYSIS database (edumetrics_analysis).

All table names, column names, index names, and constraints match
analysis_db_schema.sql v2.1 exactly.  Script-level aliases (CamelCase)
are kept at the bottom so existing import statements need no changes.
"""

from django.db import models


# ============================================================
# 1. ANALYSIS STATE  (singleton)
# ============================================================
class analysis_state(models.Model):
    id                  = models.IntegerField(primary_key=True, default=1)
    current_sem_week    = models.IntegerField(default=0)
    current_global_week = models.IntegerField(default=0)
    current_semester    = models.IntegerField(default=1)
    last_updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'analysis_state'

    def save(self, *args, **kwargs):
        self.id = 1          # enforce singleton
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"Analysis at sem_week={self.current_sem_week}, "
            f"semester={self.current_semester}"
        )


# ============================================================
# 2. WEEKLY METRICS
#    One row per (student, semester, sem_week).
#    Column names match analysis_db_schema.sql exactly.
# ============================================================
class weekly_metrics(models.Model):
    id          = models.AutoField(primary_key=True)
    student_id  = models.CharField(max_length=10)
    class_id    = models.CharField(max_length=20)
    semester    = models.IntegerField() 
    sem_week    = models.IntegerField()          # 1-18, never 8 or 18
    computed_at = models.DateTimeField(auto_now_add=True)

    # Effort score (E_t, 0-100)
    effort_score        = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    library_visits = models.IntegerField(default=0,null=True, blank=True)
    book_borrows        = models.IntegerField(default=0)
    assn_quality_pct    = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    assn_plagiarism_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    weekly_att_pct      = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    quiz_attempt_rate   = models.DecimalField(max_digits=5, decimal_places=4, null=True)
    assn_submit_rate    = models.DecimalField(max_digits=5, decimal_places=4, null=True)

    # Academic performance (A_t, 0-100)
    academic_performance = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    quiz_avg_pct         = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    assn_avg_pct         = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    midterm_score_pct    = models.DecimalField(max_digits=5, decimal_places=2, null=True)

    # Risk of detention (0-100, every teaching week)
    risk_of_detention = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    overall_att_pct   = models.DecimalField(max_digits=5, decimal_places=2, null=True)

    # risk score 
    risk_score = models.IntegerField(null=True, blank=True, default=None)
    # escalation level is not written by any script
    escalation_level=models.IntegerField(default=0)
    class Meta:
        db_table = 'weekly_metrics'
        constraints = [
            models.UniqueConstraint(
                fields=['student_id', 'semester', 'sem_week'],
                name='uq_wm',
            )
        ]
        indexes = [
            models.Index(fields=['class_id', 'semester', 'sem_week']),
            models.Index(fields=['student_id', 'semester', 'sem_week']),
        ]

    def __str__(self):
        return (
            f"Metrics for {self.student_id} | class={self.class_id} "
            f"sem={self.semester} week={self.sem_week}"
        )

WeeklyMetrics = weekly_metrics
weekly_metrics=weekly_metrics


# ============================================================
# 3. PRE MID TERM
# ============================================================
class pre_mid_term(models.Model):
    id          = models.BigAutoField(primary_key=True)
    student_id  = models.CharField(max_length=10)
    class_id    = models.CharField(max_length=20)
    semester    = models.IntegerField()
    sem_week    = models.IntegerField()   # 6 or 7
    computed_at = models.DateTimeField(auto_now_add=True)

    predicted_midterm_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)

    class Meta:
        db_table = 'pre_mid_term'
        indexes = [
            models.Index(fields=['student_id'],                        name='idx_pmt_student'),
            models.Index(fields=['class_id', 'semester', 'sem_week'], name='idx_pmt_class_sem_week'),
        ]

    def __str__(self):
        return (
            f"PreMidTerm for {self.student_id} | sem={self.semester} "
            f"week={self.sem_week} | predicted={self.predicted_midterm_score}"
        )

PreMidTerm = pre_mid_term


# ============================================================
# 4. PRE END TERM
# ============================================================
class pre_end_term(models.Model):
    id          = models.BigAutoField(primary_key=True)
    student_id  = models.CharField(max_length=10)
    class_id    = models.CharField(max_length=20)
    semester    = models.IntegerField()
    sem_week    = models.IntegerField()   # always 17
    computed_at = models.DateTimeField(auto_now_add=True)

    predicted_endterm_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)

    class Meta:
        db_table = 'pre_end_term'
        indexes = [
            models.Index(fields=['student_id'],                        name='idx_pet_student'),
            models.Index(fields=['class_id', 'semester', 'sem_week'], name='idx_pet_class_sem_week'),
        ]

    def __str__(self):
        return (
            f"PreEndTerm for {self.student_id} | sem={self.semester} "
            f"week={self.sem_week} | predicted={self.predicted_endterm_score}"
        )

PreEndTerm = pre_end_term


# ============================================================
# 5. RISK OF FAILING
#    Table name matches SQL schema: risk_of_failing
# ============================================================
class risk_of_failing(models.Model):
    id          = models.BigAutoField(primary_key=True)
    student_id  = models.CharField(max_length=10)
    class_id    = models.CharField(max_length=20)
    semester    = models.IntegerField()
    sem_week    = models.IntegerField()   # 10-17
    computed_at = models.DateTimeField(auto_now_add=True)

    p_fail     = models.DecimalField(max_digits=5, decimal_places=4)
    risk_label = models.CharField(max_length=10)   # LOW | MEDIUM | HIGH

    class Meta:
        db_table = 'risk_of_failing'
        constraints = [
            models.UniqueConstraint(
                fields=['student_id', 'semester', 'sem_week'],
                name='uq_rof',
            )
        ]
        indexes = [
            models.Index(fields=['student_id'],                        name='idx_rof_student'),
            models.Index(fields=['class_id', 'semester', 'sem_week'], name='idx_rof_class_sem_week'),
        ]

    def __str__(self):
        return (
            f"RiskOfFailing for {self.student_id} | sem={self.semester} "
            f"week={self.sem_week} | {self.risk_label} ({self.p_fail})"
        )

RiskOfFailing           = risk_of_failing
RiskOfFailingPrediction = risk_of_failing   # legacy alias


# ============================================================
# 6. WEEKLY FLAGS
#    schema but written by flagging.py and read by the portal.
# ============================================================
class weekly_flags(models.Model):
    id               = models.AutoField(primary_key=True)
    student_id       = models.CharField(max_length=10)
    class_id         = models.CharField(max_length=20)
    semester         = models.IntegerField()
    sem_week         = models.IntegerField()
    computed_at      = models.DateTimeField(auto_now_add=True)

    risk_tier        = models.CharField(max_length=40)
    urgency_score    = models.IntegerField()
    # change from  v1
    # removing escalation level from weekly_flags
    # escalation_level = models.IntegerField(default=0)
    diagnosis        = models.TextField()

    helpful          = models.BooleanField(null=True, default=None)
    feedback_at      = models.DateTimeField(null=True, blank=True)

    
    class Meta:
        db_table = 'weekly_flags'
        constraints = [
        models.UniqueConstraint(
            fields=['student_id', 'semester', 'sem_week'],
            name='uq_wf_student_sem_week'
        )
    ]
        indexes = [
            models.Index(fields=['class_id', 'semester', 'sem_week']),
            models.Index(fields=['student_id', 'semester']),
        ]

    def __str__(self):
        return (
            f"Flag for {self.student_id} | class={self.class_id} "
            f"sem={self.semester} week={self.sem_week} | {self.risk_tier}"
        )

WeeklyFlag  = weekly_flags
WeeklyFlags = weekly_flags


# ============================================================
# 7. INTERVENTION LOG
#    flag_id FK to weekly_flags (nullable so engine writes
#    that happen before the flag PK is resolved still work).
#    'notes' is the canonical advisor column per the SQL schema.
#    'trigger_diagnosis' / 'advisor_notified' kept as legacy
#    nullable extras so existing bulk_create calls don't break.
# change from v1: now this table gets updated by frontend only
# ============================================================
class intervention_log(models.Model):
    id               = models.BigAutoField(primary_key=True)
    flag             = models.ForeignKey(
                           weekly_flags,
                           on_delete=models.PROTECT,
                           db_column='flag_id',
                           null=True,
                           blank=True,
                       )
    student_id       = models.CharField(max_length=10)
    semester         = models.IntegerField()
    sem_week         = models.IntegerField()
    logged_at        = models.DateTimeField(auto_now_add=True)
    notes             = models.TextField(blank=True, default='')

    # legacy fields kept nullable
    trigger_diagnosis = models.TextField(blank=True, default='')
    advisor_notified  = models.BooleanField(default=False)

    class Meta:
        db_table = 'intervention_log'
        indexes = [
            models.Index(fields=['student_id'],           name='idx_il_student'),
            models.Index(fields=['semester', 'sem_week'], name='idx_il_sem_week'),
        ]

    def __str__(self):
        return (
            f"InterventionLog for {self.student_id} | "
            f"sem={self.semester} week={self.sem_week} "
        )

InterventionLog = intervention_log


# ============================================================
# 8. PRE SEM WATCHLIST
# ============================================================
class pre_sem_watchlist(models.Model):
    id                   = models.BigAutoField(primary_key=True)
    student_id           = models.CharField(max_length=20)
    class_id             = models.CharField(max_length=20)
    target_semester      = models.IntegerField()
    computed_at          = models.DateTimeField(auto_now_add=True)

    risk_probability_pct = models.DecimalField(max_digits=5, decimal_places=2)
    escalation_level     = models.IntegerField(default=0)
    max_plagiarism       = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    att_rate_hist        = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    assn_rate_hist       = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    exam_avg_hist        = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    hard_subject_count   = models.IntegerField(default=0)

    class Meta:
        db_table = 'pre_sem_watchlist'
        constraints = [
            models.UniqueConstraint(
                fields=['student_id', 'target_semester'],
                name='uq_psw',
            )
        ]
        indexes = [
            models.Index(fields=['class_id', 'target_semester'], name='idx_psw_class'),
        ]

    def __str__(self):
        return (
            f"PreSemWatchlist for {self.student_id} | class={self.class_id} "
            f"target_sem={self.target_semester}"
        )

PreSemWatchlist = pre_sem_watchlist


# ============================================================
# 9. SUBJECT DIFFICULTY
# ============================================================
class subject_difficulty(models.Model):
    subject_id       = models.CharField(max_length=20)
    semester         = models.IntegerField()
    computed_at      = models.DateTimeField(auto_now=True)

    total_students   = models.IntegerField()
    students_passed  = models.IntegerField()
    pass_rate        = models.DecimalField(max_digits=5, decimal_places=4)
    difficulty_label = models.CharField(max_length=10)   # easy|medium|hard

    class Meta:
        db_table = 'subject_difficulty'
        constraints = [
            models.UniqueConstraint(
                fields=['subject_id', 'semester'],
                name='uq_sd',
            )
        ]

    def __str__(self):
        return (
            f"SubjectDifficulty {self.subject_id} sem={self.semester}: "
            f"{self.difficulty_label} (pass_rate={self.pass_rate})"
        )

SubjectDifficulty = subject_difficulty


# ============================================================
# 10. EVENT LOG
# ============================================================
class event_log(models.Model):
    id            = models.BigAutoField(primary_key=True)
    event_type    = models.CharField(max_length=50)
    triggered_at  = models.DateTimeField(auto_now_add=True)
    client_week   = models.IntegerField(null=True)
    analysis_week = models.IntegerField(null=True)
    semester      = models.IntegerField(null=True)
    status        = models.CharField(max_length=10, default='ok')   # ok|skip|error
    error_message = models.TextField(null=True)
    duration_ms   = models.IntegerField(null=True)

    class Meta:
        db_table = 'event_log'

    def __str__(self):
        return f"Event {self.event_type} at {self.triggered_at} [{self.status}]"

EventLog = event_log
