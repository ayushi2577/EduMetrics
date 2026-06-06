"""
Initial migration — creates all EduMetrics analysis DB tables.
Column names match models.py (analysis_db_schema.sql v2.1) exactly.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True
    dependencies = []

    operations = [

        # ── analysis_state (singleton) ──────────────────────────────────────
        migrations.CreateModel(
            name='analysis_state',
            fields=[
                ('id',                  models.IntegerField(default=1, primary_key=True, serialize=False)),
                ('current_sem_week',    models.IntegerField(default=0)),
                ('current_global_week', models.IntegerField(default=0)),
                ('current_semester',    models.IntegerField(default=1)),
                ('last_updated_at',     models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'analysis_state'},
        ),

        # ── weekly_metrics ──────────────────────────────────────────────────
        migrations.CreateModel(
            name='weekly_metrics',
            fields=[
                ('id',          models.AutoField(primary_key=True, serialize=False)),
                ('student_id',  models.CharField(max_length=10)),
                ('class_id',    models.CharField(max_length=20)),
                ('semester',    models.IntegerField()),
                ('sem_week',    models.IntegerField()),
                ('computed_at', models.DateTimeField(auto_now_add=True)),
                # Effort score (E_t)
                ('effort_score',        models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                ('library_visits',      models.IntegerField(default=0, null=True, blank=True)),
                ('book_borrows',        models.IntegerField(default=0)),
                ('assn_quality_pct',    models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                ('assn_plagiarism_pct', models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                ('weekly_att_pct',      models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                ('quiz_attempt_rate',   models.DecimalField(decimal_places=4, max_digits=5, null=True)),
                ('assn_submit_rate',    models.DecimalField(decimal_places=4, max_digits=5, null=True)),
                # Academic performance (A_t)
                ('academic_performance', models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                ('quiz_avg_pct',         models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                ('assn_avg_pct',         models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                ('midterm_score_pct',    models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                # Risk of detention
                ('risk_of_detention', models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                ('overall_att_pct',   models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                # Added in v2
                ('risk_score',       models.IntegerField(null=True, blank=True, default=None)),
                ('escalation_level', models.IntegerField(default=0)),
            ],
            options={'db_table': 'weekly_metrics'},
        ),
        migrations.AddConstraint(
            model_name='weekly_metrics',
            constraint=models.UniqueConstraint(
                fields=['student_id', 'semester', 'sem_week'], name='uq_wm'),
        ),
        migrations.AddIndex(
            model_name='weekly_metrics',
            index=models.Index(fields=['class_id', 'semester', 'sem_week'], name='idx_wm_class_sem_week'),
        ),
        migrations.AddIndex(
            model_name='weekly_metrics',
            index=models.Index(fields=['student_id', 'semester', 'sem_week'], name='idx_wm_student'),
        ),

        # ── pre_mid_term ────────────────────────────────────────────────────
        migrations.CreateModel(
            name='pre_mid_term',
            fields=[
                ('id',          models.BigAutoField(primary_key=True, serialize=False)),
                ('student_id',  models.CharField(max_length=10)),
                ('class_id',    models.CharField(max_length=20)),
                ('semester',    models.IntegerField()),
                ('sem_week',    models.IntegerField()),
                ('computed_at', models.DateTimeField(auto_now_add=True)),
                ('predicted_midterm_score', models.DecimalField(decimal_places=2, max_digits=5, null=True)),
            ],
            options={'db_table': 'pre_mid_term'},
        ),
        migrations.AddIndex(
            model_name='pre_mid_term',
            index=models.Index(fields=['student_id'], name='idx_pmt_student'),
        ),
        migrations.AddIndex(
            model_name='pre_mid_term',
            index=models.Index(fields=['class_id', 'semester', 'sem_week'], name='idx_pmt_class_sem_week'),
        ),

        # ── pre_end_term ────────────────────────────────────────────────────
        migrations.CreateModel(
            name='pre_end_term',
            fields=[
                ('id',          models.BigAutoField(primary_key=True, serialize=False)),
                ('student_id',  models.CharField(max_length=10)),
                ('class_id',    models.CharField(max_length=20)),
                ('semester',    models.IntegerField()),
                ('sem_week',    models.IntegerField()),
                ('computed_at', models.DateTimeField(auto_now_add=True)),
                ('predicted_endterm_score', models.DecimalField(decimal_places=2, max_digits=5, null=True)),
            ],
            options={'db_table': 'pre_end_term'},
        ),
        migrations.AddIndex(
            model_name='pre_end_term',
            index=models.Index(fields=['student_id'], name='idx_pet_student'),
        ),
        migrations.AddIndex(
            model_name='pre_end_term',
            index=models.Index(fields=['class_id', 'semester', 'sem_week'], name='idx_pet_class_sem_week'),
        ),

        # ── risk_of_failing ─────────────────────────────────────────────────
        migrations.CreateModel(
            name='risk_of_failing',
            fields=[
                ('id',          models.BigAutoField(primary_key=True, serialize=False)),
                ('student_id',  models.CharField(max_length=10)),
                ('class_id',    models.CharField(max_length=20)),
                ('semester',    models.IntegerField()),
                ('sem_week',    models.IntegerField()),
                ('computed_at', models.DateTimeField(auto_now_add=True)),
                ('p_fail',      models.DecimalField(decimal_places=4, max_digits=5)),
                ('risk_label',  models.CharField(max_length=10)),
            ],
            options={'db_table': 'risk_of_failing'},
        ),
        migrations.AddConstraint(
            model_name='risk_of_failing',
            constraint=models.UniqueConstraint(
                fields=['student_id', 'semester', 'sem_week'], name='uq_rof'),
        ),
        migrations.AddIndex(
            model_name='risk_of_failing',
            index=models.Index(fields=['student_id'], name='idx_rof_student'),
        ),
        migrations.AddIndex(
            model_name='risk_of_failing',
            index=models.Index(fields=['class_id', 'semester', 'sem_week'], name='idx_rof_class_sem_week'),
        ),

        # ── weekly_flags ─────────────────────────────────────────────────────
        # NOTE: escalation_level and archetype removed vs original migration.
        migrations.CreateModel(
            name='weekly_flags',
            fields=[
                ('id',            models.AutoField(primary_key=True, serialize=False)),
                ('student_id',    models.CharField(max_length=10)),
                ('class_id',      models.CharField(max_length=20)),
                ('semester',      models.IntegerField()),
                ('sem_week',      models.IntegerField()),
                ('computed_at',   models.DateTimeField(auto_now_add=True)),
                ('risk_tier',     models.CharField(max_length=40)),
                ('urgency_score', models.IntegerField()),
                ('diagnosis',     models.TextField()),
                ('helpful',       models.BooleanField(default=None, null=True)),
                ('feedback_at',   models.DateTimeField(blank=True, null=True)),
            ],
            options={'db_table': 'weekly_flags'},
        ),
        migrations.AddConstraint(
            model_name='weekly_flags',
            constraint=models.UniqueConstraint(
                fields=['student_id', 'semester', 'sem_week'], name='uq_wf_student_sem_week'),
        ),
        migrations.AddIndex(
            model_name='weekly_flags',
            index=models.Index(fields=['class_id', 'semester', 'sem_week'], name='idx_wf_class_sem_week'),
        ),
        migrations.AddIndex(
            model_name='weekly_flags',
            index=models.Index(fields=['student_id', 'semester'], name='idx_wf_student'),
        ),

        # ── intervention_log ─────────────────────────────────────────────────
        # NOTE: escalation_level removed vs original migration.
        migrations.CreateModel(
            name='intervention_log',
            fields=[
                ('id',               models.BigAutoField(primary_key=True, serialize=False)),
                ('flag',             models.ForeignKey(
                                         blank=True,
                                         db_column='flag_id',
                                         null=True,
                                         on_delete=django.db.models.deletion.PROTECT,
                                         to='analysis_engine.weekly_flags',
                                     )),
                ('student_id',       models.CharField(max_length=10)),
                ('semester',         models.IntegerField()),
                ('sem_week',         models.IntegerField()),
                ('logged_at',        models.DateTimeField(auto_now_add=True)),
                ('notes',            models.TextField(blank=True, default='')),
                # legacy columns (kept nullable for backwards compat)
                ('trigger_diagnosis', models.TextField(blank=True, default='')),
                ('advisor_notified',  models.BooleanField(default=False)),
            ],
            options={'db_table': 'intervention_log'},
        ),
        migrations.AddIndex(
            model_name='intervention_log',
            index=models.Index(fields=['student_id'], name='idx_il_student'),
        ),
        migrations.AddIndex(
            model_name='intervention_log',
            index=models.Index(fields=['semester', 'sem_week'], name='idx_il_sem_week'),
        ),

        # ── pre_sem_watchlist ────────────────────────────────────────────────
        # NOTE: att_rate_hist, assn_rate_hist, exam_avg_hist are max_digits=6, decimal_places=2.
        migrations.CreateModel(
            name='pre_sem_watchlist',
            fields=[
                ('id',                   models.BigAutoField(primary_key=True, serialize=False)),
                ('student_id',           models.CharField(max_length=20)),
                ('class_id',             models.CharField(max_length=20)),
                ('target_semester',      models.IntegerField()),
                ('computed_at',          models.DateTimeField(auto_now_add=True)),
                ('risk_probability_pct', models.DecimalField(decimal_places=2, max_digits=5)),
                ('escalation_level',     models.IntegerField(default=0)),
                ('max_plagiarism',       models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ('att_rate_hist',        models.DecimalField(decimal_places=2, max_digits=6, null=True)),
                ('assn_rate_hist',       models.DecimalField(decimal_places=2, max_digits=6, null=True)),
                ('exam_avg_hist',        models.DecimalField(decimal_places=2, max_digits=6, null=True)),
                ('hard_subject_count',   models.IntegerField(default=0)),
            ],
            options={'db_table': 'pre_sem_watchlist'},
        ),
        migrations.AddConstraint(
            model_name='pre_sem_watchlist',
            constraint=models.UniqueConstraint(
                fields=['student_id', 'target_semester'], name='uq_psw'),
        ),
        migrations.AddIndex(
            model_name='pre_sem_watchlist',
            index=models.Index(fields=['class_id', 'target_semester'], name='idx_psw_class'),
        ),

        # ── subject_difficulty ───────────────────────────────────────────────
        # NOTE: added AutoField primary key (missing from original migration).
        migrations.CreateModel(
            name='subject_difficulty',
            fields=[
                ('id',               models.AutoField(primary_key=True, serialize=False)),
                ('subject_id',       models.CharField(max_length=20)),
                ('semester',         models.IntegerField()),
                ('computed_at',      models.DateTimeField(auto_now=True)),
                ('total_students',   models.IntegerField()),
                ('students_passed',  models.IntegerField()),
                ('pass_rate',        models.DecimalField(decimal_places=4, max_digits=5)),
                ('difficulty_label', models.CharField(max_length=10)),
            ],
            options={'db_table': 'subject_difficulty'},
        ),
        migrations.AddConstraint(
            model_name='subject_difficulty',
            constraint=models.UniqueConstraint(
                fields=['subject_id', 'semester'], name='uq_sd'),
        ),

        # ── event_log ────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='event_log',
            fields=[
                ('id',            models.BigAutoField(primary_key=True, serialize=False)),
                ('event_type',    models.CharField(max_length=50)),
                ('triggered_at',  models.DateTimeField(auto_now_add=True)),
                ('client_week',   models.IntegerField(null=True)),
                ('analysis_week', models.IntegerField(null=True)),
                ('semester',      models.IntegerField(null=True)),
                ('status',        models.CharField(default='ok', max_length=10)),
                ('error_message', models.TextField(null=True)),
                ('duration_ms',   models.IntegerField(null=True)),
            ],
            options={'db_table': 'event_log'},
        ),
    ]
