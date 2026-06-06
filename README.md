<div align="center">

```
███████╗ ██████╗  ██╗   ██╗ ███╗   ███╗ ███████╗████████╗ ██████╗  ██╗  ██████╗ ███████╗
██╔════╝ ██╔══██╗ ██║   ██║ ████╗ ████║ ██╔════╝╚══██╔══╝ ██╔══██╗ ██║ ██╔════╝ ██╔════╝
█████╗   ██║  ██║ ██║   ██║ ██╔████╔██║ █████╗     ██║    ██████╔╝ ██║ ██║      ███████╗
██╔══╝   ██║  ██║ ██║   ██║ ██║╚██╔╝██ ║██╔══╝     ██║    ██╔══██╗ ██║ ██║      ╚════██║
███████╗ ██████╔╝ ╚██████╔╝ ██║ ╚═╝ ██ ║███████╗   ██║    ██║  ██║ ██║ ╚██████ ╗███████║
╚══════╝ ╚═════╝   ╚═════╝ ╚═╝     ╚═╝ ╚══════╝    ╚═╝    ╚═╝  ╚═╝╚═╝  ╚═════╝ ╚══════╝
```

**AI-powered academic analytics platform for college advisors.**  
Know who needs attention. Act early. Spread help smartly.

<br/>

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-4.x-092E20?style=flat-square&logo=django&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![scikit-learn](https://img.shields.io/badge/scikit--learn-ML-F7931E?style=flat-square&logo=scikit-learn&logoColor=white)
![Status](https://img.shields.io/badge/Status-In_Development-yellow?style=flat-square)

</div>

---

## What Is EduMetrics?

EduMetrics is an academic advisory intelligence platform built for college advisors. It connects to a college's existing database — attendance, assignments, quizzes, library usage — and surfaces **who needs attention this week**, complete with plain-English explanations of why.

The platform is built around a core belief: **advisors have limited time and many students.** EduMetrics makes sure that time is spent on the students who need it most, with the right context to act effectively.

```
College Database (read-only)          EduMetrics Analytics Engine
┌─────────────────────────┐           ┌────────────────────────────────┐
│  Attendance records     │  ──────▶  │  Weekly metrics computation    │
│  Assignment submissions │           │  Effort + academic scoring     │
│  Quiz attempts          │           │  Risk flagging + triage        │
│  Library visit logs     │           │  Pre/post exam analysis        │
└─────────────────────────┘           └──────────────┬─────────────────┘
                                                      │
                                                      ▼
                                       REST API (Django REST Framework)
                                       ┌──────────────────────────────┐
                                       │  Weekly flagged student list  │
                                       │  Student performance metrics  │
                                       │  Pre/post midterm analysis    │
                                       │  End-of-semester predictions  │
                                       └──────────────────────────────┘
```

---

## The Problem

Most colleges track student data. Very few use it proactively. An advisor managing 60+ students has no practical way to know that **a student's attendance dropped 18% over the last 3 weeks**, or that **someone hasn't submitted an assignment in 12 days** — until it shows up as a fail on a result sheet. By that point, intervention is too late.

EduMetrics closes that gap. Every week, the system recalculates metrics for every student and surfaces a ranked triage list: exactly who to reach out to, why, and with what context.

---

## Architecture

EduMetrics uses a **dual-database design**: it reads from the college's existing database (read-only) and writes its own analytics results to a separate analysis database. The college's data is never modified.

```
┌──────────────────┐     Django ORM      ┌────────────────────────┐
│   Client DB      │  ◀── (read-only) ── │                        │
│  (college data)  │                     │   Analytics Engine     │
│                  │                     │   (Python / Django)    │
│  Students        │                     │                        │
│  Attendance      │                     │  weekly_metrics_       │
│  Assignments     │                     │    calculator.py       │
│  Quizzes         │                     │  flagging.py           │
│  Library visits  │                     │  pre_mid_term.py       │
│  Exam results    │                     │  pre_end_term.py       │
└──────────────────┘                     │  pre_sem.py            │
                                         └────────────┬───────────┘
┌──────────────────┐                                  │ writes
│   Analysis DB    │  ◀───────────────────────────────┘
│  (EduMetrics)    │
│                  │
│  WeeklyMetrics   │
│  WeeklyFlag      │
│  PreMidTerm      │
│  PreEndTerm      │
│  Predictions     │
│  Watchlists      │
└──────────────────┘
```

Database routing is handled automatically — all `ClientXxx` models route to `client_db`, all analysis models route to `default`.

---

## Analytics Engine

### Weekly Metrics (`weekly_metrics_calculator.py`)

Every week, the engine computes two scores per student:

**Effort Score** — measures behavioural engagement across 6 signals:

| Signal | Weight |
|--------|--------|
| Library visits | 25% |
| Book borrows | 20% |
| Assignment quality | 20% |
| Plagiarism penalty | 15% |
| Attendance rate | 10% |
| Quiz + assignment submission rate | 10% |

**Academic Performance Score** — a weighted composite of the student's last 3 weeks, with more recent weeks weighted higher (40 / 30 / 30).

Both scores are written to the `WeeklyMetrics` table in the analysis DB. Exam weeks (Week 8 midterm, Week 18 end-term) are excluded from trend calculations to avoid distorting the baseline.

### Weekly Flagging (`flagging.py`)

After metrics are computed, the flagging engine identifies students who need advisor attention. It:

- Reads the current simulator week and derives semester context (odd/even slot, current semester per class)
- Computes a 4-week baseline window (excluding exam weeks) to detect meaningful deviations
- Applies a **grace period** for the first 3 weeks of semester (flags are softer while patterns establish)
- Tracks **escalation memory** — students who were flagged last week are escalated, not re-flagged from scratch
- Writes triage results to `WeeklyFlag` with plain-English reasons

### Semester Analysis Windows

Analysis runs at 5 points in the semester, triggered by the scheduler or manually:

| Module | When | What It Does |
|--------|------|--------------|
| `pre_sem.py` | Start of semester | Generates a watchlist based on prior semester performance |
| `weekly_metrics_calculator.py` + `flagging.py` | Every week | Computes scores, flags at-risk students |
| `pre_mid_term.py` | Before Week 8 | Predicts midterm exam scores, flags students at risk of failing |
| `pre_end_term.py` | Before Week 18 | Predicts end-term outcomes, generates pass/fail risk per student |
| Post-analysis | After results | Compares actuals to predictions, identifies underperformers |

---

## REST API

Built with Django REST Framework. Key endpoints (all in `analysis_engine/`):

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/flagged/` | GET | Weekly triage list filtered by semester, week, and class |
| `/class-performance/` | GET | Aggregated performance for a class |
| `/student-performance/` | GET | Individual student metrics for a given week |
| `/pre-midterm/` | GET | Pre-midterm predictions per student |
| `/pre-endterm/` | GET | End-term risk predictions |
| `/risk-of-failing/` | GET | Per-subject fail risk predictions |
| `/watchlist/` | GET | Pre-semester watchlist |
| `/calibrate/` | POST | Triggers a full recalibration of the analysis DB |

A custom database router (`routers.py`) handles directing reads/writes to the correct database automatically.

---

## Project Structure

```
EduMetrics/
│
└── backend/
    ├── manage.py
    ├── requirements.txt
    │
    ├── config/
    │   ├── settings.py         # Dual-DB config, JWT auth, DRF settings
    │   ├── urls.py             # Root URL routing
    │   ├── wsgi.py
    │   └── asgi.py
    │
    ├── accounts/               # Custom user model + auth
    │   ├── models.py           # Users model (AUTH_USER_MODEL)
    │   ├── views.py            # Login / registration endpoints
    │   └── addingdata.py       # Seed utilities
    │
    └── analysis_engine/        # Core analytics
        ├── client_models.py    # Read-only models for college DB (managed=False)
        ├── models.py           # Analysis DB models (WeeklyMetrics, WeeklyFlag, etc.)
        ├── routers.py          # DB router: client_db vs default
        ├── serializer.py       # DRF serializers for all analysis models
        ├── views.py            # API view functions
        ├── urls.py             # Analysis engine URL patterns
        │
        ├── weekly_metrics_calculator.py   # Effort + academic score computation
        ├── flagging.py                    # Weekly triage engine
        ├── pre_mid_term.py                # Pre-midterm analysis
        ├── pre_end_term.py                # Pre-end-term analysis
        ├── pre_sem.py                     # Start-of-semester watchlist
        ├── calibrate_analysis_db.py       # Full DB recalibration
        ├── scheduler.py                   # Maps weeks to analysis jobs
        │
        └── management/commands/
            └── run_weekly.py              # Django management command for weekly run
```

---

## Getting Started

### Prerequisites

```
Python 3.11+
PostgreSQL (or Supabase)
Git
```

### 1. Clone and set up the environment

```bash
git clone https://github.com/your-username/EduMetrics.git
cd EduMetrics/backend

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Configure databases

EduMetrics requires two PostgreSQL databases: one for the college's existing data (or a replica/seed), and one for the analysis results.

Create a `.env` file in `backend/`:

```env
# Analysis DB (EduMetrics writes here)
DEFAULT_DB_URL=postgresql://user:password@host:5432/edumetrics_analysis

# Client DB (college data — read-only)
CLIENT_DB_URL=postgresql://user:password@host:5432/college_data

SECRET_KEY=your-long-random-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
```

Generate a secret key:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Run migrations

```bash
# Migrate the analysis DB
python manage.py migrate

# The client DB is read-only (managed=False models) — no migrations needed for it
```

### 4. Start the server

```bash
python manage.py runserver
# API available at http://localhost:8000
```

### 5. Run the weekly analysis

```bash
# Via management command
python manage.py run_weekly

# Or trigger via API (POST to /api/calibrate/)
```

---

## Authentication

EduMetrics uses JWT authentication via `djangorestframework-simplejwt`. A custom `Users` model is set as `AUTH_USER_MODEL`.

Obtain a token:
```
POST /api/token/
{ "username": "...", "password": "..." }
```

Include in subsequent requests:
```
Authorization: Bearer <access_token>
```
### Test Accounts

The following seeded accounts are available for local development and testing:

| Advisor ID | Name | Role | Username | Password |
|------------|------|------|----------|----------|
| ADV001 | Dr. Priya Mehta | Advisor | `ADV001` | `Dr. Priya Mehta001` |


---

## Database Schema (Analysis DB)

The tables EduMetrics writes to:

| Table | Contents |
|-------|----------|
| `weekly_metrics` | Per-student effort score + academic performance, per week |
| `weekly_flags` | Triage output — flagged students with risk level and reasons |
| `pre_mid_term` | Predicted midterm scores and at-risk flags |
| `pre_end_term` | End-term pass/fail risk per student |
| `risk_of_failing_prediction` | Per-subject failure probability |
| `pre_sem_watchlist` | Start-of-semester carry-over watchlist |
| `intervention_log` | Advisor intervention records |

The client DB schema (college data) is accessed through `ClientXxx` unmanaged models and is never modified by EduMetrics.

---

## Roadmap

- [x] Dual-database architecture with custom ORM router
- [x] Custom JWT auth with role-based user model
- [x] Weekly metrics calculator (effort + academic performance scores)
- [x] Weekly flagging engine with escalation memory and grace period logic
- [x] Pre-midterm analysis pipeline
- [x] Pre-end-term analysis pipeline
- [x] Pre-semester watchlist generation
- [x] REST API with DRF serializers
- [x] Management command for weekly runs
- [ ] Frontend — React + Tailwind advisor portal
- [ ] Student Galaxy scatter view
- [ ] Intervention logger
- [ ] ML risk classifier (Stage 2)
- [ ] Claude API integration for AI-generated briefings and reports
- [ ] Gmail integration for advisor-to-student/parent emails

---

## Contributing

1. Branch from `main` → `feature/your-feature-name`
2. Keep commits small and descriptive
3. Before opening a PR, verify the weekly pipeline runs cleanly: `python manage.py run_weekly`
4. Document any new environment variables in `.env.example`

---

## License

MIT — use freely, attribution appreciated.

---

<div align="center">

**EduMetrics** — built because students deserve advisors who know their story before it's too late.

</div>
