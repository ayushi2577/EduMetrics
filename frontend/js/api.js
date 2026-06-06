// EduMetrics — js/api.js
// JWT-authenticated API client  (performance-optimised)

const API_BASE = 'https://edumetrics-wswe.onrender.com';

// ── RISK SCORE WEIGHTS (mirrors flagging.py WEIGHTS exactly) ─────────────────
// Stored here once so any page can reference it without an API call.
const RISK_WEIGHTS = Object.freeze({
  risk_of_detention: 30,
  assn_streak:       15,
  quiz_streak:        8,
  high_risk_streak:  12,
  lag_score_penalty: 10,
  avg_risk_score_3w:  7,
  avg_at_3w:          5,
  avg_et_3w:          5,
  et_drop:            8,
});

// Max possible raw value for each signal (used to compute saturation on card face)
const RISK_MAX_RAW = Object.freeze({
  risk_of_detention: 100,   // raw detention score out of 100
  assn_streak:         3,   // streak capped at 3 weeks
  quiz_streak:         3,
  high_risk_streak:    3,
  lag_score_penalty: 100,   // signal already 0-100
  avg_risk_score_3w: 100,
  avg_at_3w:         100,   // inverted; 0 performance = 100 signal
  avg_et_3w:         100,
  et_drop:           100,   // pp drop max 100
});

// Human-readable card label for each signal key
const RISK_CARD_LABEL = Object.freeze({
  risk_of_detention: v => `Detention risk: ${v}/100`,
  assn_streak:       v => `${v} assignment${v>1?'s':''} missed in a row`,
  quiz_streak:       v => `${v} quiz${v>1?'zes':''} missed in a row`,
  high_risk_streak:  v => `${v} week${v>1?'s':''} at high risk`,
  lag_score_penalty: v => `Effort not converting (gap: ${v})`,
  avg_risk_score_3w: v => `Avg risk score over past 3 weeks: ${v}/100`,
  avg_at_3w:         v => `Low avg performance: ${v}/100`,
  avg_et_3w:         v => `Low avg effort: ${v}/100`,
  et_drop:           v => `Effort dropped ${v}pp`,
});

// ── TOKEN MANAGEMENT ──────────────────────────────────────────────────────────

function getAccessToken()  { return localStorage.getItem('em_access'); }
function getRefreshToken() { return localStorage.getItem('em_refresh'); }
function getAdvisorInfo()  {
  return {
    class_id:     localStorage.getItem('em_class_id')   || 'CSE_Y1_A',
    advisor_id:   localStorage.getItem('em_advisor_id') || '',
    advisor_name: localStorage.getItem('em_advisor_name') || 'Advisor',
    semester:     parseInt(localStorage.getItem('em_semester'))  || 1,
    sem_week:     parseInt(localStorage.getItem('em_sem_week'))  || 1,
    actual_semester: parseInt(localStorage.getItem('em_actual_semester')) || 1,
  };
}

function saveTokens({ access, refresh, class_id, advisor_id, advisor_name, semester, sem_week ,actual_semester}) {
  localStorage.setItem('em_access',       access);
  localStorage.setItem('em_refresh',      refresh);
  localStorage.setItem('em_class_id',     class_id     || '');
  localStorage.setItem('em_advisor_id',   advisor_id   || '');
  localStorage.setItem('em_advisor_name', advisor_name || '');
  localStorage.setItem('em_semester',     semester     || 1);
  localStorage.setItem('em_sem_week',     sem_week     || 1);
  localStorage.setItem('em_actual_semester',actual_semester||1);
}

function clearTokens() {
  ['em_access','em_refresh','em_class_id','em_advisor_id','em_advisor_name','em_semester','em_sem_week','em_actual_semester']
    .forEach(k => localStorage.removeItem(k));
}

function isLoggedIn() { return !!getAccessToken(); }

function requireAuth() {
  if (!isLoggedIn()) { window.location.href = 'index.html'; }
}

// ── TOKEN REFRESH ─────────────────────────────────────────────────────────────

async function refreshAccessToken() {
  const refresh = getRefreshToken();
  if (!refresh) throw new Error('No refresh token');
  const res = await fetch(`${API_BASE}/api/token/refresh/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh }),
  });
  if (!res.ok) { clearTokens(); window.location.href = 'index.html'; throw new Error('Session expired'); }
  const data = await res.json();
  localStorage.setItem('em_access', data.access);
  return data.access;
}

// ── AUTHENTICATED FETCH ───────────────────────────────────────────────────────

async function apiFetch(path, options = {}) {
  let token = getAccessToken();
  const makeReq = (tok) => fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${tok}`,
      ...(options.headers || {}),
    },
  });

  let res = await makeReq(token);

  if (res.status === 401) {
    try {
      token = await refreshAccessToken();
      res = await makeReq(token);
    } catch {
      window.location.href = 'index.html';
      throw new Error('Authentication failed');
    }
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `API error ${res.status}`);
  }

  return res.json();
}

// ── AUTH ──────────────────────────────────────────────────────────────────────

async function login(advisor_id, password) {
  const res = await fetch(`${API_BASE}/api/login/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ advisor_id, password }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Login failed');
  saveTokens(data);
  return data;
}

async function logout() {
  const refresh = getRefreshToken();
  try {
    await apiFetch('/api/logout/', { method: 'POST', body: JSON.stringify({ refresh }) });
  } catch {}
  clearTokens();
  window.location.href = 'index.html';
}

// ── QUERY STRING HELPER ───────────────────────────────────────────────────────

function qs(params) {
  return '?' + new URLSearchParams(params).toString();
}

// ── RESPONSE CACHE ────────────────────────────────────────────────────────────
// Stores completed responses so repeated calls (tab switches, week changes)
// return immediately without hitting the network again.
//
// TTL = 5 minutes. Keyed by full URL path+params.
// In-flight deduplication: if the same URL is already fetching, return the
// same promise instead of firing a second request.

const _cache    = new Map();   // key → { data, ts }
const _inflight = new Map();   // key → Promise
const CACHE_TTL = 5 * 60 * 1000;  // 5 minutes

function _cachedApiFetch(path) {
  const now = Date.now();

  // Return from cache if fresh
  if (_cache.has(path)) {
    const { data, ts } = _cache.get(path);
    if (now - ts < CACHE_TTL) return Promise.resolve(data);
    _cache.delete(path);
  }

  // Return in-flight promise if already requesting
  if (_inflight.has(path)) return _inflight.get(path);

  // Fire the request and store the promise
  const promise = apiFetch(path)
    .then(data => {
      _cache.set(path, { data, ts: Date.now() });
      _inflight.delete(path);
      return data;
    })
    .catch(err => {
      _inflight.delete(path);
      throw err;
    });

  _inflight.set(path, promise);
  return promise;
}

// Call this when the week changes so dashboard data is refreshed.
function clearDashboardCache(class_id, semester) {
  for (const key of _cache.keys()) {
    // Clear anything that isn't a report (reports are semester-level, not week-level)
    if (!key.includes('/reports/')) {
      _cache.delete(key);
    }
  }
}

// Call this to fully wipe the cache (e.g. on logout / advisor switch).
function clearAllCache() {
  _cache.clear();
  _inflight.clear();
}

// ── DASHBOARD ─────────────────────────────────────────────────────────────────

function fetchDashboardSummary(class_id, semester, sem_week) {
  return _cachedApiFetch(`/api/analysis/dashboard/summary/${qs({ class_id, semester, sem_week })}`);
}

function fetchClassSummary(class_id, semester, sem_week) {
  return _cachedApiFetch(`/api/analysis/dashboard/class_summary/${qs({ class_id, semester, sem_week })}`);
}

// ── FLAGS ─────────────────────────────────────────────────────────────────────

function fetchWeeklyFlags(class_id, semester, sem_week) {
  return _cachedApiFetch(`/api/analysis/flags/weekly/${qs({ class_id, semester, sem_week })}`);
}

// expand_flag is NOT cached here — it includes live AI output and is called
// per student on demand. Caching is handled in script.js per flag_id.
function fetchExpandFlag(flag_id, semester, sem_week) {
  return apiFetch(`/api/analysis/flags/${flag_id}/expand/${qs({ semester, sem_week })}`);
}

function fetchLastWeekFlags(class_id, semester, sem_week) {
  return _cachedApiFetch(`/api/analysis/flags/last_week/${qs({ class_id, semester, sem_week })}`);
}

// ── INTERVENTIONS ─────────────────────────────────────────────────────────────

function fetchInterventions(class_id, semester, sem_week) {
  return _cachedApiFetch(`/api/analysis/interventions/${qs({ class_id, semester, sem_week })}`);
}

// POST — never cached
async function logInterventionAPI(flag_id, intervention) {
  return apiFetch('/api/analysis/interventions/log/', {
    method: 'POST',
    body: JSON.stringify({ flag_id, intervention, timestamp: new Date().toISOString() }),
  });
}



// ── AI — student_summary (on-demand, NOT cached — always fresh AI call) ───────
//
// Returns the AI recommendation for a flagged student:
// { recommended_intervention, secondary_intervention, reasoning, urgency,
//   tone, talking_points, email_student_brief, email_parent_brief,
//   counsellor_brief, signals_to_highlight, student_name, student_id, flag_id }
//
// The result should be stored in script.js (_aiAnalysisCache) keyed by flag_id
// so repeat opens of the same flag card don't re-fire the Gemini call.

function fetchStudentSummaryAI(flag_id, semester, sem_week) {
  return apiFetch('/api/analysis/ai/student_summary/', {
    method: 'POST',
    body: JSON.stringify({ flag_id, semester, sem_week }),
  });
}

// ── AI — generate_content (on-demand, NOT cached — content may be regenerated) ─
//
// content_type: "email_to_student" | "email_to_parent" |
//               "one_to_one_conversation" | "counsellor_report"
// ai_analysis : the full object returned by fetchStudentSummaryAI()
//
// Returns: { flag_id, content_type, content }

function fetchGenerateContent(flag_id, content_type, ai_analysis, semester, sem_week) {
  return apiFetch('/api/analysis/ai/generate_content/', {
    method: 'POST',
    body: JSON.stringify({ flag_id, content_type, ai_analysis, semester, sem_week }),
  });
}
// ── STUDENTS ──────────────────────────────────────────────────────────────────

function fetchAllStudents(class_id, semester, sem_week) {
  return _cachedApiFetch(`/api/analysis/students/all/${qs({ class_id, semester, sem_week })}`);
}

function fetchDetainmentRisk(class_id, semester,sem_week) {
  return _cachedApiFetch(`/api/analysis/students/detainment_risk/${qs({ class_id, semester,sem_week })}`);
}

function fetchStudentDetail(student_id, class_id, semester, sem_week) {
  return _cachedApiFetch(`/api/analysis/students/${student_id}/${qs({ class_id, semester, sem_week })}`);
}

// ── REPORTS ───────────────────────────────────────────────────────────────────
// Reports are semester-level (don't change with week) — longer TTL not needed
// because CACHE_TTL covers a session comfortably.

function fetchPreMidtermReport(class_id, semester) {
  return _cachedApiFetch(`/api/analysis/reports/pre_midterm/${qs({ class_id, semester })}`);
}

function fetchPostMidtermReport(class_id, semester) {
  return _cachedApiFetch(`/api/analysis/reports/post_midterm/${qs({ class_id, semester })}`);
}

function fetchPreEndtermReport(class_id, semester) {
  return _cachedApiFetch(`/api/analysis/reports/pre_endterm/${qs({ class_id, semester })}`);
}

function fetchPostEndtermReport(class_id, semester) {
  return _cachedApiFetch(`/api/analysis/reports/post_endterm/${qs({ class_id, semester })}`);
}



