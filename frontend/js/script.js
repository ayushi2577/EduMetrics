// EduMetrics — js/script.js
// Performance-optimised: progressive rendering, expand_flag cache, no redundant fetches.

// ── APP STATE ─────────────────────────────────────────────────────────────────
const WEEKS = ['W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W7', 'W8', 'W9', 'W10', 'W11', 'W12', 'W13', 'W14'];
let currentWeek = 1;
let SEMESTER = 1;

let CLASS_ID = '';
let ADVISOR_NAME = 'Advisor';

// Normalised data stores
let ALL_STUDENTS = [];
let FLAGGED = [];
let LAST_WEEK = [];
let INTERVENTIONS = [];

// Map from student_id → flag_id
let STUDENT_TO_FLAG_ID = {};

// Per-session expand_flag cache: flag_id → expanded data
// Avoids re-calling the slow AI endpoint every time a card is opened.
const _expandCache = new Map();

// ── INIT ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  requireAuth();
  const info = getAdvisorInfo();
  CLASS_ID = info.class_id || 'Harcoded class';
  ADVISOR_NAME = info.advisor_name || 'Harcoded advisor';
  SEMESTER = info.semester || 1;
  currentWeek = info.sem_week || 1;
  
  // ── Build week dropdown dynamically up to currentWeek ──
  const weekSelect = document.getElementById('weekSelect');
  if (weekSelect) {
    weekSelect.innerHTML = '';
    for (let w = 1; w <= currentWeek; w++) {
      const opt = document.createElement('option');
      opt.value = w;
      opt.textContent = 'Week ' + w;
      if (w === currentWeek) opt.selected = true;
      weekSelect.appendChild(opt);
    }
  }

  const weekBadge = document.getElementById('weekBadge');
  if (weekBadge) weekBadge.textContent = 'Week ' + currentWeek;
  const flaggedWeekSub = document.getElementById('flaggedWeekSub');
  if (flaggedWeekSub) flaggedWeekSub.textContent = 'Week ' + currentWeek + ' · Semester ' + SEMESTER;
  const semBadge = document.getElementById('semBadge');
  if (semBadge) semBadge.textContent='Semester'+SEMESTER;

  document.querySelectorAll('.advisor-name').forEach(el => el.textContent = ADVISOR_NAME);
  document.querySelectorAll('.advisor-avatar').forEach(el => {
    const parts = ADVISOR_NAME.split(' ');
    el.textContent = parts.map(p => p[0]).join('').toUpperCase().slice(0, 2);
  });

  document.querySelectorAll('.logout-btn').forEach(el => el.onclick = logout);

  await loadDashboardData();
});

// ── LOADING HELPERS ───────────────────────────────────────────────────────────
function showLoading(id) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = '<div class="api-loading"><div class="api-spinner"></div><span>Loading…</span></div>';
}

function showError(id, msg = 'Failed to load data') {
  const el = document.getElementById(id);
  if (el) el.innerHTML = `<div class="api-error">⚠ ${msg}</div>`;
}

// ── MAIN DATA LOADER ──────────────────────────────────────────────────────────
// Progressive strategy:
//   1. Fire all 5 requests in parallel (Promise.allSettled — never blocks on one failure).
//   2. Render each section the moment its data arrives instead of waiting for all 5.
//
// On repeat visits (tab switch / week change) api.js returns cached data
// instantly so this function completes in milliseconds.

async function loadDashboardData() {
  showLoadingState();

  // Fire all requests simultaneously — do NOT await one before starting the next.
  const summaryP = fetchDashboardSummary(CLASS_ID, SEMESTER, currentWeek);
  const flagsP = fetchWeeklyFlags(CLASS_ID, SEMESTER, currentWeek);
  const studentsP = fetchAllStudents(CLASS_ID, SEMESTER, currentWeek);
  const lastWeekP = fetchLastWeekFlags(CLASS_ID, SEMESTER, currentWeek);
  const interventionsP = fetchInterventions(CLASS_ID, SEMESTER, currentWeek);
  const detainmentP = fetchDetainmentRisk(CLASS_ID, SEMESTER,currentWeek);

  // Render stat cards as soon as summary arrives (fastest endpoint)
  summaryP
    .then(updateStatCards)
    .catch(() => { });

  // Render flagged cards as soon as flags arrive
  flagsP
    .then(raw => {
      FLAGGED = normaliseFlaggedStudents(raw);
      buildFlaggedCards();
    })
    .catch(() => showError('flaggedGrid', 'Could not load flagged students'));

  // Add this new handler:
  Promise.all([studentsP, detainmentP])
    .then(([studentsRaw, detainmentRaw]) => {
      ALL_STUDENTS = ALL_STUDENTS.length ? ALL_STUDENTS : normaliseAllStudents(studentsRaw);
      buildRiskChart(detainmentRaw);
    })
  .catch(() => { });
  
  // Render last-week cards as soon as they arrive
  lastWeekP
    .then(raw => {
      LAST_WEEK = normaliseLastWeekFlags(raw);
      buildLastWeekCards();
    })
    .catch(() => showError('lastWeekGrid', 'Could not load last week data'));

  // Store interventions quietly
  interventionsP
    .then(raw => { INTERVENTIONS = normaliseInterventions(raw); })
    .catch(() => { });

  // Wait for all to settle so the caller knows when everything is done.
  await Promise.allSettled([summaryP, flagsP, studentsP, lastWeekP, interventionsP]);
}

function showLoadingState() {
  showLoading('flaggedGrid');
  showLoading('lastWeekGrid');
}

// ── DATA NORMALISERS ──────────────────────────────────────────────────────────

function normaliseFlaggedStudents(raw) {
  STUDENT_TO_FLAG_ID = {};
  return Object.entries(raw).map(([key, f]) => {
    const flagId = f.id || parseInt(key.replace(/\D/g, ''), 10) || key;
    STUDENT_TO_FLAG_ID[f.student_id] = flagId;
    const riskLevel = riskTierToLevel(f.risk_tier);
    return {
      flagId,
      id: f.student_id,
      name: f.student_name,
      avatar: initials(f.student_name),
      riskLevel,
      risk: riskLevel,
      riskScore: Math.round(f.risk_score || 0),
      attendance: Math.round(f.attendance_pct || 0),
      reason: f.diagnosis || 'Flagged for review',
      escalation_level: f.escalation_level,
      flagHistory: [],
    };
  });
}

function normaliseAllStudents(raw) {
  const sm = raw.student_map || {};
  const at = raw.A_t || {};
  const et = raw.E_t || {};
  const rs = raw.risk_score || {};
  const pmt = raw.predicted_midterm_score || {};
  const pet = raw.predicted_endterm_score || {};
  const amt = raw.actual_midterm_score || {};
  const aet = raw.actual_endterm_score || {};

  return Object.keys(sm).map(sid => {
    const riskScore = Math.round(rs[sid] || 0);
    const riskLevel = riskScore >= 70 ? 'high' : riskScore >= 45 ? 'med' : 'safe';
    const acad = parseFloat(at[sid]) || 0;
    const eff = parseFloat(et[sid]) || 0;
    return {
      id: sid,
      name: sm[sid],
      avatar: initials(sm[sid]),
      riskLevel,
      risk: riskLevel,
      riskScore,
      academicPerf: Math.round(acad),
      effort: Math.round(eff),
      attendance: Math.round(parseFloat(raw.overall_att_pct?.[sid]) || 0),
      predMidterm: parseFloat(pmt[sid]) || 0,
      predEndterm: parseFloat(pet[sid]) || 0,
      midterm: amt[sid] !== false ? amt[sid] : null,
      endterm: aet[sid] !== false ? aet[sid] : null,
      weekEt: [], weekAt: [],
      avgRisk: riskScore, avgEt: Math.round(eff), avgAt: Math.round(acad),
      overallAttend: Math.round(parseFloat(raw.overall_att_pct?.[sid]) || 0),
      riskDetention: 0, riskFail: riskScore,
      flagHistory: [],
      factors: [],
    };
  });
}

function normaliseLastWeekFlags(raw) {
  return Object.entries(raw).map(([flagId, f]) => {
    const [sid, name, diagnosis, tier] = f.basic_details || [];
    const more = f.more || {};
    const tvl = f.this_week_vs_last_week || {};
    const riskLevel = riskTierToLevel(tier);

    const etPrev = tvl.effort?.E_t_previous || 0;
    const etDelta = tvl.effort?.delta_E_t || 0;
    const atPrev = tvl.performance?.A_t_previous || 0;
    const atDelta = tvl.performance?.delta_A_t || 0;
    const rkPrev = tvl.risk_score?.risk_score_previous || 0;
    const rkDelta = tvl.risk_score?.delta_risk_score || 0;

    const etCurr = Math.round(etPrev + etDelta);
    const atCurr = Math.round(atPrev + atDelta);
    const riskCurr = Math.round(rkPrev + rkDelta);

    return {
      flagId,
      id: sid,
      name: name || sid,
      avatar: initials(name || sid),
      risk: riskLevel,
      riskLevel,
      status: rkDelta > 5 ? 'intervene' : rkDelta < -5 ? 'resolved' : 'monitor',
      reason: diagnosis || 'Flagged last week',
      avgRisk: Math.round(more.avg_risk_score || 0),
      avgEt: Math.round(more.avg_effort || 0),
      avgAt: Math.round(more.avg_academic_performance || 0),
      overallAttend: Math.round(more.overall_attendance || 0),
      riskDetention: Math.round(more.risk_of_detention || 0),
      riskFailing: Math.round(rkPrev),
      midterm: more.mid_term_score !== null && more.mid_term_score !== false ? more.mid_term_score : 'N/A',
      etPrev: Math.round(etPrev), etCurr,
      atPrev: Math.round(atPrev), atCurr,
      riskPrev: Math.round(rkPrev), riskCurr,
      recovery: Math.max(5, 100 - riskCurr),
      intervention: null,
      factors: buildFactorsFromDiagnosis(diagnosis, Math.round(more.avg_risk_score || 0)),
    };
  });
}

function normaliseInterventions(raw) {
  return Object.entries(raw).map(([id, iv]) => ({
    id,
    student_id: iv.student_id,
    student: iv.name,
    name: iv.name,
    type: iv.type_of_intervention,
    date: iv.date_of_logging,
    change: '—',
  }));
}

// ── EXPAND FLAG NORMALISER ────────────────────────────────────────────────────

function mergeExpandFlagIntoStudent(base, expanded) {
  const ov = expanded.student_overview || {};
  const evp = expanded.effort_vs_performance || {};
  const trends = expanded.trends || {};
  const fh = expanded.flagging_history || {};
  const fc = expanded.flagging_contributors || {};

  const weekKeys = Array.from({ length: 14 }, (_, i) => i + 1);
  const weekEt = weekKeys.map(w => trends.E_t?.[w] || null);
  const weekAt = weekKeys.map(w => trends.A_t?.[w] || null);

  const fcEntries = Object.entries(fc);
  const totalFcScore = fcEntries.reduce((s, [, v]) => s + v, 0) || 1;
  const colors = ['var(--red)', 'var(--amber)', '#58a6ff', 'var(--purple)', 'var(--green)'];
  const factors = fcEntries.map(([label, val], i) => ({
    label,
    pct: Math.round((val / totalFcScore) * 100),
    color: colors[i % colors.length],
  }));

  const flagHistory = Object.entries(fh).map(([week, fhe]) => ({
    week: parseInt(week),
    diagnosis: fhe.diagnosis,
    intervened: fhe.did_we_intervene,
  }));

  const riskScore = Math.round(ov.avg_risk_score || base.riskScore || 0);

  return {
    ...base,
    avgRisk: riskScore,
    avgEt: Math.round(ov.avg_effort || 0),
    avgAt: Math.round(ov.avg_academic_performance || 0),
    overallAttend: Math.round(ov.overall_attendance || 0),
    riskDetention: Math.round(ov.risk_of_detention || 0),
    riskFail: riskScore,
    riskScore,
    midterm: ov.mid_term_score !== null && ov.mid_term_score !== false ? ov.mid_term_score : 'N/A',
    weekEt, weekAt,
    factors, flagHistory,
    majorFactor: factors.length ? factors[0].label : '',
    aiSummary: expanded.student_summary || null,
    etThisWeek: Math.round(evp.E_t || 0),
    perfThisWeek: Math.round(evp.A_t || 0),
    studentAvgEt: Math.round(evp.avg_effort_of_student || 0),
    studentAvgPerf: Math.round(evp.avg_performance_of_student || 0),
    classAvgEt: Math.round(evp.avg_effort_of_class || 0),
    classAvgPerf: Math.round(evp.avg_performance_of_class || 0),
    recovery: Math.max(5, 100 - riskScore),
  };
}

// ── HELPERS ───────────────────────────────────────────────────────────────────

function initials(name) {
  if (!name) return '??';
  return name.split(' ').map(p => p[0]).join('').toUpperCase().slice(0, 2);
}

function riskTierToLevel(tier) {
  if (!tier) return 'safe';
  const t = tier.toLowerCase();
  if (t.includes('tier 1') || t.includes('critical')) return 'high';
  if (t.includes('tier 2') || t.includes('watch')) return 'med';
  if (t.includes('tier 3') || t.includes('warning')) return 'low';
  return 'safe';
}

function buildFactorsFromDiagnosis(diagnosis, totalScore) {
  if (!diagnosis) return [];
  const parts = diagnosis.split('|').map(p => p.trim()).filter(Boolean);
  if (!parts.length) return [];
  const colors = ['var(--red)', 'var(--amber)', '#58a6ff', 'var(--purple)', 'var(--green)'];
  return parts.map((label, i) => ({
    label,
    pct: Math.round(totalScore / parts.length),
    color: colors[i % colors.length],
  }));
}

function rc(risk) {
  const m = {
    high: { cls: 'risk-high', bg: 'rgba(248,81,73,0.12)', txt: '#f85149', border: 'rgba(248,81,73,0.30)', label: 'High Risk' },
    med: { cls: 'risk-med', bg: 'rgba(210,153,34,0.12)', txt: '#d29922', border: 'rgba(210,153,34,0.30)', label: 'Medium Risk' },
    low: { cls: 'risk-low', bg: 'rgba(63,185,80,0.12)', txt: '#3fb950', border: 'rgba(63,185,80,0.30)', label: 'Low Risk' },
    safe: { cls: 'risk-safe', bg: 'rgba(63,185,80,0.12)', txt: '#3fb950', border: 'rgba(63,185,80,0.30)', label: 'Safe' },
  };
  return m[risk] || m.safe;
}

function statusCfg(s) {
  const m = {
    intervene: { bg: 'rgba(248,81,73,0.12)', txt: '#f85149', border: 'rgba(248,81,73,0.30)', label: 'Needs Intervention' },
    monitor: { bg: 'rgba(210,153,34,0.12)', txt: '#d29922', border: 'rgba(210,153,34,0.30)', label: 'Monitoring' },
    resolved: { bg: 'rgba(63,185,80,0.12)', txt: '#3fb950', border: 'rgba(63,185,80,0.30)', label: 'Resolved' },
  };
  return m[s] || m.monitor;
}

// ── THEME (locked to light — toggle removed from UI) ──────────────────────────
function setTheme() { /* light mode is permanent; no-op */ }

// ── SIDEBAR ───────────────────────────────────────────────────────────────────
function toggleSidebar() { document.getElementById('sidebar').classList.toggle('collapsed'); }

// ── PAGE NAV ──────────────────────────────────────────────────────────────────
function showPage(p) {
  document.querySelectorAll('.page').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  document.getElementById('page-' + p).classList.add('active');
  const nav = document.getElementById('nav-' + p);
  if (nav) nav.classList.add('active');
  if (p === 'students') initStudentsPage();
  if (p === 'calendar') initCalendar();
  if (p === 'analytics') initAnalyticsPage();
}

// ── WEEK SELECTOR ─────────────────────────────────────────────────────────────
function changeWeek(w) {
  currentWeek = parseInt(w);
  document.getElementById('weekBadge').textContent = 'Week ' + currentWeek;
  const sub = document.getElementById('flaggedWeekSub');
  if (sub) sub.textContent = 'Week ' + currentWeek + ' · Semester ' + SEMESTER;

  // Clear week-level data and expand cache (week changed so old expansions are stale)
  ALL_STUDENTS = [];
  _expandCache.clear();
  _stuDetailCache.clear(); 
  // Clear week-level entries from the api.js response cache
  clearDashboardCache(CLASS_ID, SEMESTER);

  loadDashboardData();
}

// ── STAT CARDS ────────────────────────────────────────────────────────────────
function updateStatCards(s) {
  const vs = document.querySelector('.stat-card-blue .stat-value');
  if (vs) animateStatVal(vs, s.total_students || 0);

  const vr = document.getElementById('riskValue');
  if (vr) { animateStatValPct(vr, Math.round(s.avg_risk_score || 0)); updateWaterFill(Math.round(s.avg_risk_score || 0)); }

  const vf = document.querySelector('.stat-card-red .stat-value');
  if (vf) animateStatVal(vf, s.flagged_this_week || 0);

  const vi = document.querySelector('.stat-card-green .stat-value');
  if (vi) animateStatVal(vi, s.interventions_this_week || 0);
}

function animateStatVal(el, target) {
  let t0 = null;
  function step(ts) {
    if (!t0) t0 = ts;
    const p = Math.min((ts - t0) / 900, 1);
    el.textContent = Math.floor((1 - Math.pow(1 - p, 3)) * target);
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function animateStatValPct(el, target) {
  let t0 = null;
  function step(ts) {
    if (!t0) t0 = ts;
    const p = Math.min((ts - t0) / 1100, 1);
    el.textContent = Math.floor((1 - Math.pow(1 - p, 3)) * target) + '%';
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function updateWaterFill(riskScore) {
  const fill = document.getElementById('waterFill');
  if (!fill) return;
  let color;
  if (riskScore <= 30) color = 'rgba(63,185,80,0.4)';
  else if (riskScore <= 50) color = 'rgba(210,153,34,0.4)';
  else if (riskScore <= 70) color = 'rgba(210,153,34,0.6)';
  else color = 'rgba(248,81,73,0.5)';
  fill.style.height = riskScore + '%';
  fill.style.background = color;
}

// ── FLAGGED CARDS ─────────────────────────────────────────────────────────────
function buildFlaggedCards() {
  const grid = document.getElementById('flaggedGrid');
  if (!grid) return;
  grid.innerHTML = '';
  if (!FLAGGED.length) { grid.innerHTML = '<div class="api-empty">✓ No flagged students this week</div>'; return; }
  FLAGGED.forEach((s, i) => {
    const r = rc(s.riskLevel);
    const card = document.createElement('div');
    card.className = `flag-card ${r.cls}`;
    card.style.animationDelay = `${.05 + i * .06}s`;
    const attended = s.attendance || 0;
    const atColor = attended < 65 ? 'var(--red)' : attended < 75 ? 'var(--amber)' : 'var(--green)';
    card.innerHTML = `
      <div class="flag-top-row">
        <div class="flag-identity">
          <div class="flag-av" style="background:${r.bg};color:${r.txt};// NEW
          width:28px;height:28px;...font-size:10px;flex-shrink:0">${s.avatar}</div>
          <div>
            <div class="flag-name">${s.name}</div>
            <div class="flag-id">${s.id}</div>
          </div>
        </div>
        <span class="risk-pill" style="background:${r.bg};color:${r.txt};border:1px solid ${r.border}">
          <span class="rpd" style="background:${r.txt}"></span>${r.label}
        </span>
      </div>
      <div class="flag-reason">${s.reason}</div>
      </div>
      </div>
      <div class="flag-btn-row">
        <button class="view-btn" style="background:${r.bg};color:${r.txt};border:1px solid ${r.border}" onclick="openFlaggedDetail('${s.id}')">View Details →</button>
      </div>`;
    grid.appendChild(card);
  });
}

// ── LAST WEEK CARDS ───────────────────────────────────────────────────────────
function buildLastWeekCards() {
  const lwg = document.getElementById('lastWeekGrid');
  if (!lwg) return;
  lwg.innerHTML = '';
  if (!LAST_WEEK.length) { lwg.innerHTML = '<div class="api-empty">No prior-week flags found</div>'; return; }
  LAST_WEEK.forEach((s, i) => {
    const r = rc(s.risk);
    const card = document.createElement('div');
    card.className = `lw-flag-card ${r.cls}`;
    card.style.animationDelay = `${.08 + i * .07}s`;
    card.innerHTML = `
      <div class="flag-top-row">
        <div class="flag-identity">
            <div class="flag-av" style="background:${r.bg};color:${r.txt};width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:10px;flex-shrink:0">${s.avatar}</div>
            <div class="flag-name">${s.name}</div>
            <div class="flag-id">${s.id}</div>
          </div>
        </div>
        <span class="risk-pill" style="background:${r.bg};color:${r.txt};border:1px solid ${r.border}">
          <span class="rpd" style="background:${r.txt}"></span>${r.label}
        </span>
      </div>
      <div class="flag-reason">${s.reason}</div>
      <div class="flag-btn-row">
        <button class="view-btn" style="background:${r.bg};color:${r.txt};border:1px solid ${r.border}" onclick="openLwDetailById('${s.id}')">View Details →</button>
      </div>`;
    lwg.appendChild(card);
  });
}

// ── LAST WEEK DETAIL ──────────────────────────────────────────────────────────
function openLwDetailById(id) {
  const idx = LAST_WEEK.findIndex(s => s.id === id);
  if (idx === -1) return;
  openLwDetail(idx);
}

function openLwDetail(idx) {
  const s = LAST_WEEK[idx]; const r = rc(s.risk); const sc = statusCfg(s.status);
  document.getElementById('lwDmAv').textContent = s.avatar;
  document.getElementById('lwDmAv').style.cssText = `background:${r.bg};color:${r.txt};width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:15px;flex-shrink:0`;
  document.getElementById('lwDmTitle').textContent = s.name;
  document.getElementById('lwDmSub').textContent = s.id;
  document.getElementById('lwDmRisk').innerHTML = `<span class="rpd" style="background:${r.txt}"></span>${r.label}`;
  document.getElementById('lwDmRisk').style.cssText = `background:${r.bg};color:${r.txt};border:1px solid ${r.border};display:inline-flex;align-items:center;gap:5px;font-size:10.5px;font-weight:700;padding:4px 10px;border-radius:20px`;
  const etDir = s.etCurr >= s.etPrev, atDir = s.atCurr >= s.atPrev, rkDir = s.riskCurr >= s.riskPrev;
  document.getElementById('lwDmBody').innerHTML = `
    <div class="lw-two-col">
      <div class="dm-panel">
        <div class="lw-section-label">About the Student</div>
        <div class="lw-stat-row"><span class="lw-stat-label">Avg Risk Score</span><span class="lw-stat-val">${s.avgRisk}%</span></div>
        <div class="lw-stat-row"><span class="lw-stat-label">Avg Effort</span><span class="lw-stat-val">${s.avgEt}%</span></div>
        <div class="lw-stat-row"><span class="lw-stat-label">Avg Academic Perf</span><span class="lw-stat-val">${s.avgAt}%</span></div>
        <div class="lw-stat-row"><span class="lw-stat-label">Overall Attendance</span><span class="lw-stat-val">${s.overallAttend}%</span></div>
        <div class="lw-stat-row"><span class="lw-stat-label">Risk of Detention</span><span class="lw-stat-val" style="color:${s.riskDetention > 60 ? 'var(--red)' : s.riskDetention > 40 ? 'var(--amber)' : 'var(--green)'}">${s.riskDetention}%</span></div>
        <div class="lw-stat-row"><span class="lw-stat-label">Risk of Failing</span><span class="lw-stat-val" style="color:${s.riskFailing > 60 ? 'var(--red)' : s.riskFailing > 40 ? 'var(--amber)' : 'var(--green)'}">${s.riskFailing}%</span></div>
        <div class="lw-stat-row"><span class="lw-stat-label">Midterm Score</span><span class="lw-stat-val">${s.midterm}</span></div>
      </div>
      <div class="dm-panel">
        <div class="lw-section-label">Reason for Flagging</div>
        ${(s.factors || []).map(f => `<div class="factor-bar-row"><div class="factor-bar-top"><span class="factor-bar-label">${f.label}</span><span class="factor-bar-pct" style="color:${f.color}">${f.pct}%</span></div><div class="factor-bar-track"><div class="factor-bar-fill" style="width:${f.pct}%;background:${f.color}"></div></div></div>`).join('')}
      </div>
    </div>
    <div class="dm-panel">
      <div class="lw-section-label">This Week vs Last Week</div>
      <div class="situation-grid">
        <div class="sit-item"><div class="sit-label">Effort</div><div class="sit-vals"><span class="sit-val">${s.etCurr}%</span><span class="sit-arrow" style="color:${etDir ? 'var(--green)' : 'var(--red)'}">${etDir ? '▲' : '▼'}</span><span class="sit-change" style="color:${etDir ? 'var(--green)' : 'var(--red)'}">${Math.abs(s.etCurr - s.etPrev)}</span></div><div style="font-size:10px;color:var(--txt3);margin-top:3px">prev: ${s.etPrev}%</div></div>
        <div class="sit-item"><div class="sit-label">Acad. Perf</div><div class="sit-vals"><span class="sit-val">${s.atCurr}%</span><span class="sit-arrow" style="color:${atDir ? 'var(--green)' : 'var(--red)'}">${atDir ? '▲' : '▼'}</span><span class="sit-change" style="color:${atDir ? 'var(--green)' : 'var(--red)'}">${Math.abs(s.atCurr - s.atPrev)}</span></div><div style="font-size:10px;color:var(--txt3);margin-top:3px">prev: ${s.atPrev}%</div></div>
        <div class="sit-item"><div class="sit-label">Risk Score</div><div class="sit-vals"><span class="sit-val" style="color:${rkDir ? 'var(--red)' : 'var(--green)'}">${s.riskCurr}%</span><span class="sit-arrow" style="color:${rkDir ? 'var(--red)' : 'var(--green)'}">${rkDir ? '▲' : '▼'}</span><span class="sit-change" style="color:${rkDir ? 'var(--red)' : 'var(--green)'}">${Math.abs(s.riskCurr - s.riskPrev)}</span></div><div style="font-size:10px;color:var(--txt3);margin-top:3px">prev: ${s.riskPrev}%</div></div>
      </div>
    </div>
    <div class="lw-bottom">
      <div class="lw-bottom-item"><div class="lw-bottom-label">Recovery %</div><div class="lw-bottom-val" style="color:${s.recovery < 30 ? 'var(--red)' : s.recovery < 55 ? 'var(--amber)' : 'var(--green)'}">${s.recovery}%</div><div style="height:4px;background:var(--bg3);border-radius:10px;margin-top:8px;overflow:hidden"><div style="height:100%;width:${s.recovery}%;background:${s.recovery < 30 ? 'var(--red)' : s.recovery < 55 ? 'var(--amber)' : 'var(--green)'};border-radius:10px"></div></div></div>
      <div class="lw-bottom-item"><div class="lw-bottom-label">Status</div><span class="status-pill" style="background:${sc.bg};color:${sc.txt};border:1px solid ${sc.border}">${sc.label}</span></div>
      <div class="lw-bottom-item"><div class="lw-bottom-label">Intervention</div><div style="font-size:11.5px;color:var(--txt2);line-height:1.55;margin-top:4px">${s.intervention || 'None recorded'}</div></div>
    </div>`;
  document.getElementById('lwOverlay').classList.add('open'); document.body.style.overflow = 'hidden';
}

function closeLwOverlay() { document.getElementById('lwOverlay').classList.remove('open'); document.body.style.overflow = ''; }
function handleLwOvClick(e) { if (e.target === document.getElementById('lwOverlay')) closeLwOverlay(); }

// ── INTERVENTIONS POPUP ───────────────────────────────────────────────────────
function openInterventionsPopup() {
  let html = `<table class="int-table"><thead><tr><th>Student</th><th>ID</th><th>Type</th><th>Date</th><th>Change</th></tr></thead><tbody>`;
  if (!INTERVENTIONS.length) {
    html += `<tr><td colspan="5" style="text-align:center;color:var(--txt3)">No interventions recorded</td></tr>`;
  } else {
    INTERVENTIONS.forEach(iv => {
      html += `<tr>
        <td style="font-weight:700;color:var(--txt)">${iv.student || iv.name}</td>
        <td>${iv.student_id}</td>
        <td>${iv.type}</td>
        <td>${iv.date}</td>
        <td class="perf-same">—</td>
      </tr>`;
    });
  }
  html += `</tbody></table>`;
  document.getElementById('interventionsBody').innerHTML = html;
  document.getElementById('interventionsOverlay').classList.add('open'); document.body.style.overflow = 'hidden';
}
function closeInterventionsPopup() { document.getElementById('interventionsOverlay').classList.remove('open'); document.body.style.overflow = ''; }
function handleIntOvClick(e) { if (e.target === document.getElementById('interventionsOverlay')) closeInterventionsPopup(); }

// ── STUDENTS PAGE ─────────────────────────────────────────────────────────────
let currentStuView = 'academicPerf';
const STU_TOGGLE_KEYS = ['academicPerf', 'riskScore', 'effort', 'predMidterm', 'predEndterm', 'attendance'];
const STU_META = {
  academicPerf: { label: 'Academic Performance', colHeader: 'Acad. Perf', barColor: 'var(--accent2)' },
  riskScore: { label: 'Risk Score', colHeader: 'Risk Score', barColor: 'var(--red)' },
  effort: { label: 'Effort', colHeader: 'Effort', barColor: 'var(--purple)' },
  predMidterm: { label: 'Pred. Mid Term', colHeader: 'Mid Term', barColor: 'var(--purple)' },
  predEndterm: { label: 'Pred. End Term', colHeader: 'End Term', barColor: 'var(--amber)' },
  attendance: { label: 'Attendance', colHeader: 'Attendance', barColor: 'var(--green)' },
};

async function initStudentsPage() {
  // ALL_STUDENTS is populated by loadDashboardData — if it's ready just render.
  // If the user navigates here before dashboard data loads, fetch it.
  if (!ALL_STUDENTS.length) {
    showLoading('stuViewContainer');
    try {
      const raw = await fetchAllStudents(CLASS_ID, SEMESTER, currentWeek);
      ALL_STUDENTS = normaliseAllStudents(raw);
    } catch {
      showError('stuViewContainer', 'Could not load students');
      return;
    }
  }
  renderStudentsView();
}

function setStuView(view) {
  currentStuView = view;
  document.querySelectorAll('#stuToggleGroup .tgl-btn').forEach((b, i) => { b.classList.toggle('active', STU_TOGGLE_KEYS[i] === view); });
  renderStudentsView();
}

function renderStudentsView() {
  const view = currentStuView; const meta = STU_META[view];
  let sorted = [...ALL_STUDENTS].sort((a, b) => (b[view] || 0) - (a[view] || 0));
  const container = document.getElementById('stuViewContainer');
  if (!container) return;
  let html = `<div class="stu-list-wrap">
    <div class="stu-list-header" style="grid-template-columns:40px 1fr 140px 80px 100px">
      <span>#</span><span>Student</span><span>${meta.colHeader}</span><span>Risk</span><span></span>
    </div>`;
  sorted.forEach((s, i) => {
    const val = s[view] || 0; const r = rc(s.riskLevel);
    html += `<div class="stu-list-row" style="grid-template-columns:40px 1fr 140px 80px 100px;animation-delay:${i * .03}s">
      <span class="stu-rank">${i + 1}</span>
      <div class="stu-name-cell">
        <div class="stu-av" style="background:${r.bg};color:${r.txt}">${s.avatar}</div>
        <div><div class="stu-name-txt">${s.name}</div><div class="stu-roll-txt">${s.id}</div></div>
      </div>
      <div class="stu-val-cell">
        <div class="stu-bar-wrap"><div class="stu-bar-fill" style="width:${val}%;background:${meta.barColor}"></div></div>
        <span class="stu-val-num" style="color:${val < 40 ? 'var(--red)' : val < 60 ? 'var(--amber)' : 'var(--txt)'}">${val}%</span>
      </div>
      <button class="stu-view-btn" onclick="openStuDetail('${s.id}')">View Details</button>
    </div>`;
  });
  html += `</div>`;
  container.innerHTML = html;
}

// ── STUDENT DETAIL ────────────────────────────────────────────────────────────
let currentStudent = null;

function generateStudentSummary(s) {
  if (s.aiSummary) return s.aiSummary;
  const name = s.name.split(' ')[0];
  const acad = s.avgAt || s.academicPerf || 0;
  const attend = s.overallAttend || s.attendance || 0;
  const effort = s.avgEt || s.effort || 0;
  const risk = s.riskFail || s.riskScore || 0;
  const flags = (s.flagHistory || []).length;
  let sentences = [];
  if (acad >= 80) sentences.push(`${name} is performing <strong>excellently academically</strong> with an average score of ${typeof acad === 'number' ? acad.toFixed(1) : acad}%, consistently above class benchmarks.`);
  else if (acad >= 65) sentences.push(`${name}'s academic performance is <strong>satisfactory</strong> at ${typeof acad === 'number' ? acad.toFixed(1) : acad}%, tracking close to the class average.`);
  else if (acad >= 50) sentences.push(`${name}'s academic performance is <strong>below expectations</strong> at ${typeof acad === 'number' ? acad.toFixed(1) : acad}%, showing a need for focused academic support.`);
  else sentences.push(`${name} is <strong>critically underperforming academically</strong> with an average of only ${typeof acad === 'number' ? acad.toFixed(1) : acad}%, placing them at high risk of failing.`);
  if (attend >= 85) sentences.push(`Attendance is <strong>strong at ${attend}%</strong>, reflecting consistent commitment and class presence.`);
  else if (attend >= 75) sentences.push(`Attendance stands at <strong>${attend}%</strong>, which is acceptable but could be improved to reduce risk.`);
  else if (attend >= 60) sentences.push(`Attendance is <strong>low at ${attend}%</strong> — below the 75% threshold — and is a growing concern that may affect exam eligibility.`);
  else if (attend > 0) sentences.push(`Attendance is <strong>critically low at ${attend}%</strong>, placing ${name} at serious risk of <strong>detention</strong>.`);
  if (effort >= 80) sentences.push(`Effort levels are <strong>high at ${typeof effort === 'number' ? effort.toFixed(1) : effort}%</strong>, indicating strong personal initiative and engagement.`);
  else if (effort >= 60) sentences.push(`Effort is <strong>moderate at ${typeof effort === 'number' ? effort.toFixed(1) : effort}%</strong>; with a bit more consistency, outcomes could improve significantly.`);
  else if (effort > 0) sentences.push(`Effort is <strong>poor at ${typeof effort === 'number' ? effort.toFixed(1) : effort}%</strong>, suggesting disengagement — early intervention is recommended.`);
  if (risk <= 20 && flags === 0) sentences.push(`Overall, ${name} is in a <strong>safe position</strong> with no flags this semester and minimal risk of failure.`);
  else if (flags > 0) sentences.push(`${name} has been flagged <strong>${flags} time${flags > 1 ? 's' : ''}</strong> this semester — continued monitoring and targeted intervention are advised.`);
  return sentences.join(' ');
}

// ── CACHED EXPAND FLAG FETCH ──────────────────────────────────────────────────
// First click on a student card: calls backend (takes a few seconds for AI).
// Every subsequent click on the SAME student: returns instantly from cache.

async function getExpandedFlag(flagId) {
  const key = `${flagId}_${currentWeek}_${SEMESTER}`;
  if (_expandCache.has(key)) return _expandCache.get(key);

  const expanded = await fetchExpandFlag(flagId, SEMESTER, currentWeek);
  _expandCache.set(key, expanded);
  return expanded;
}

// Open from flagged cards
async function openFlaggedDetail(studentId) {
  const base = FLAGGED.find(x => x.id === studentId);
  const flagId = STUDENT_TO_FLAG_ID[studentId] || (base && base.flagId);

  if (!flagId) {
    if (base) { openDetailFromStudent(base); return; }
    alert('Could not find flag ID for this student'); return;
  }

  // Show the overlay immediately with whatever we already have (no wait)
  // so the user sees something right away.
  const baseOrSkeleton = base || { id: studentId, name: studentId, avatar: initials(studentId), riskLevel: 'med' };

  try {
    const expanded = await getExpandedFlag(flagId);
    const full = mergeExpandFlagIntoStudent(baseOrSkeleton, expanded);
    openDetailFromStudent(full);
  } catch {
    // Fallback: open with base data (no AI summary / trends)
    if (base) openDetailFromStudent(base);
    else alert('Could not load student details.');
  }
}

// Open from students page
// ── STUDENT DETAIL — via new student_detail endpoint ─────────────────────────
// Per-session cache: student_id+week → merged student object.
// Same pattern as _expandCache so repeat opens are instant.
const _stuDetailCache = new Map();

async function getStudentDetail(studentId) {
  const key = `${studentId}_${currentWeek}_${SEMESTER}`;
  if (_stuDetailCache.has(key)) return _stuDetailCache.get(key);

  const data = await fetchStudentDetail(studentId, CLASS_ID, SEMESTER, currentWeek);
  _stuDetailCache.set(key, data);
  return data;
}

function mergeStudentDetailIntoStudent(base, detail) {
  const ov  = detail.student_overview    || {};
  const evp = detail.effort_vs_performance || {};
  const trends = detail.trends           || {};
  const fh  = detail.flagging_history    || {};

  const weekKeys = Array.from({ length: 14 }, (_, i) => i + 1);
  const weekEt   = weekKeys.map(w => trends.E_t?.[w] ?? null);
  const weekAt   = weekKeys.map(w => (trends.A_t?.[w] !== false ? trends.A_t?.[w] : null) ?? null);

  // Build factors from flagging_contributors if present, else from last flag diagnosis
  const fc = detail.flagging_contributors || {};
  const fcEntries = Object.entries(fc);
  const colors = ['var(--red)', 'var(--amber)', '#58a6ff', 'var(--purple)', 'var(--green)'];
  const factors = fcEntries.length
    ? (() => {
        const total = fcEntries.reduce((s, [, v]) => s + v, 0) || 1;
        return fcEntries.map(([label, val], i) => ({
          label,
          pct: Math.round((val / total) * 100),
          color: colors[i % colors.length],
        }));
      })()
    : buildFactorsFromDiagnosis(base.reason || '', Math.round(ov.avg_risk_score || 0));

  // flagging_history is now { total_flags, interventions, by_week }
  const byWeek = fh.by_week || {};
  const flagHistory = Object.entries(byWeek).map(([week, entry]) => ({
    week:      parseInt(week),
    diagnosis: entry.diagnosis,
    intervened: entry.did_we_intervene,
  }));

  const riskScore = Math.round(ov.avg_risk_score || base.riskScore || 0);

  return {
    ...base,
    // identity (prefer detail values which come from ClientStudent)
    name:         detail.name   || base.name,
    avatar:       detail.avatar || base.avatar || initials(detail.name || base.name),
    riskLevel:    detail.risk_level || base.riskLevel || 'safe',
    risk:         detail.risk_level || base.risk      || 'safe',
    // overview
    avgRisk:      riskScore,
    avgEt:        Math.round(ov.avg_effort               || 0),
    avgAt:        Math.round(ov.avg_academic_performance || 0),
    overallAttend: Math.round(ov.overall_attendance      || 0),
    riskDetention: Math.round(ov.risk_of_detention       || 0),
    riskFail:     Math.round(ov.risk_of_failing          || ov.avg_risk_score || 0),
    riskScore,
    midterm: ov.mid_term_score !== null && ov.mid_term_score !== false
      ? ov.mid_term_score : 'N/A',
    // trends
    weekEt, weekAt,
    // factors
    factors,
    majorFactor: factors.length ? factors[0].label : '',
    // flag history (counts come from the new shape; rows from by_week)
    flagHistory,
    totalFlags:    fh.total_flags   ?? flagHistory.length,
    totalInterventions: fh.interventions ?? flagHistory.filter(f => f.intervened).length,
    // AI summary
    aiSummary: detail.student_summary || null,
    // effort vs performance
    etThisWeek:       Math.round(evp.E_t || 0),
    perfThisWeek:     Math.round(evp.A_t || 0),
    studentAvgEt:     Math.round(evp.avg_effort_of_student      || 0),
    studentAvgPerf:   Math.round(evp.avg_performance_of_student || 0),
    classAvgEt:       Math.round(evp.avg_effort_of_class        || 0),
    classAvgPerf:     Math.round(evp.avg_performance_of_class   || 0),
    recovery: Math.max(5, 100 - riskScore),
  };
}

// Replace the old openStuDetail ──────────────────────────────────────────────
async function openStuDetail(studentId) {
  const base = ALL_STUDENTS.find(x => x.id === studentId)
    || { id: studentId, name: studentId, avatar: initials(studentId), riskLevel: 'safe' };

  try {
    const detail = await getStudentDetail(studentId);
    const full   = mergeStudentDetailIntoStudent(base, detail);
    // Keep STUDENT_TO_FLAG_ID in sync so logIntervention works if a flag exists
    if (full.flagId) STUDENT_TO_FLAG_ID[studentId] = full.flagId;
    openStuDetailFromStudent(full);
  } catch {
    // Fallback: open with whatever all_students gave us (no trends / AI)
    openStuDetailFromStudent(base);
  }
}


function openStuDetailFromStudent(s) {
  currentStudent = s;
  const r = rc(s.riskLevel || s.risk || 'safe');

  // Ensure weekEt/weekAt are arrays (may be empty for non-flagged students)
  if (!s.weekEt || !s.weekEt.length) s.weekEt = Array(14).fill(null);
  if (!s.weekAt || !s.weekAt.length) s.weekAt = Array(14).fill(null);

  _fillDetailOverlay(s, r, 'stuDet', 'stuDetLineChart', 'stuDetQuadChart', 'stuDetailOverlay');
  document.getElementById('stuDetailOverlay').classList.add('open');
  document.body.style.overflow = 'hidden';
  setTimeout(() => {
    buildStuDetLineChart(s);
    buildStuDetQuadChart(s);
  }, 80);
}

function _fillDetailOverlay(s, r, prefix, lineChartId, quadChartId, overlayId) {
  const av = document.getElementById(prefix + 'Av');
  if (av) { av.textContent = s.avatar || initials(s.name); av.style.cssText = `background:${r.bg};color:${r.txt};width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:15px;flex-shrink:0`; }
  const title = document.getElementById(prefix + 'Title') || document.getElementById(prefix + 'DetTitle');
  if (title) title.textContent = s.name;
  const sub = document.getElementById(prefix + 'Sub') || document.getElementById(prefix + 'DetSub');
  if (sub) sub.textContent = s.id;
  const pill = document.getElementById(prefix + 'RiskPill') || document.getElementById(prefix + 'DetRiskPill');
  if (pill) { pill.innerHTML = `<span class="rpd" style="background:${r.txt}"></span>${r.label}`; pill.style.cssText = `background:${r.bg};color:${r.txt};border:1px solid ${r.border};display:inline-flex;align-items:center;gap:5px;font-size:10.5px;font-weight:700;padding:4px 10px;border-radius:20px`; }
  const summaryText = document.getElementById(prefix + 'SummaryText') || document.getElementById(prefix + 'DetSummaryText');
  if (summaryText) summaryText.innerHTML = generateStudentSummary(s);
  const summaryPanel = document.getElementById(prefix + 'SummaryPanel') || document.getElementById(prefix + 'DetSummaryPanel');
  if (summaryPanel) summaryPanel.style.borderLeftColor = r.txt;
  const stats = [
    ['Avg Risk Score', `${(s.avgRisk || 0).toFixed ? (s.avgRisk || 0).toFixed(1) : s.avgRisk || 0}%`, (s.avgRisk || 0) > 60 ? 'var(--red)' : (s.avgRisk || 0) > 40 ? 'var(--amber)' : 'var(--green)'],
    ['Avg Effort', `${(s.avgEt || 0).toFixed ? (s.avgEt || 0).toFixed(1) : s.avgEt || 0}%`, null],
    ['Avg Academic Performance', `${(s.avgAt || s.academicPerf || 0).toFixed ? (s.avgAt || s.academicPerf || 0).toFixed(1) : s.avgAt || s.academicPerf || 0}%`, (s.avgAt || s.academicPerf || 0) < 50 ? 'var(--red)' : null],
    ['Overall Attendance', `${s.overallAttend || s.attendance || 0}%`, (s.overallAttend || s.attendance || 0) < 75 && (s.overallAttend || s.attendance || 0) > 0 ? 'var(--red)' : null],
    ['Risk of Detention', `${s.riskDetention || 0}%`, (s.riskDetention || 0) > 60 ? 'var(--red)' : (s.riskDetention || 0) > 40 ? 'var(--amber)' : 'var(--green)'],
    ['Risk of Failing', `${(s.riskFail || s.riskScore || 0).toFixed ? (s.riskFail || s.riskScore || 0).toFixed(1) : s.riskFail || s.riskScore || 0}%`, (s.riskFail || 0) > 60 ? 'var(--red)' : (s.riskFail || 0) > 40 ? 'var(--amber)' : 'var(--green)'],
    ['Midterm Score', s.midterm !== null && s.midterm !== undefined ? s.midterm : 'N/A', null],
  ];
  const statsEl = document.getElementById(prefix + 'Stats') || document.getElementById(prefix + 'DetStats');
  if (statsEl) statsEl.innerHTML = stats.map(([l, v, c]) => `<div class="dm-stat-row"><span class="dm-stat-label">${l}</span><span class="dm-stat-val" style="${c ? `color:${c}` : ''}"> ${v}</span></div>`).join('');
  const fh = s.flagHistory || [];
  const tf = s.totalFlags         ?? fh.length;
  const ti = s.totalInterventions ?? fh.filter(f => f.intervened).length
  const fhSum = document.getElementById(prefix + 'FhSummary') || document.getElementById(prefix + 'DetFhSummary');
  if (fhSum) fhSum.innerHTML = `<div class="fh-sum-box"><div class="fh-sum-val" style="color:var(--red)">${tf}</div><div class="fh-sum-label">Total Flags</div></div><div class="fh-sum-box"><div class="fh-sum-val" style="color:var(--green)">${ti}</div><div class="fh-sum-label">Interventions</div></div>`;
  const fhList = document.getElementById(prefix + 'FhList') || document.getElementById(prefix + 'DetFhList');
  if (fhList) {
    fhList.innerHTML = fh.length
      ? fh.map(f => {
        const wk = f.week || f.sem_week || '?';
        const diag = f.diagnosis || 'Flagged';
        const intBadge = f.intervened ? `<span style="background:rgba(63,185,80,0.12);color:#3fb950;border:1px solid rgba(63,185,80,0.3);font-size:9.5px;padding:2px 7px;border-radius:10px;font-weight:700">Intervened</span>` : '';
        return `<div class="fh-row"><span class="fh-week">W${wk}</span><span class="fh-diag">${diag}</span>${intBadge}</div>`;
      }).join('')
      : '<div style="color:var(--txt3);font-size:12px">No flag history available</div>';
  }
}

function closeOverlay() { document.getElementById('overlay').classList.remove('open'); document.body.style.overflow = ''; }
function handleOvClick(e) { if (e.target === document.getElementById('overlay')) closeOverlay(); }
function closeStuOverlay() { document.getElementById('stuDetailOverlay').classList.remove('open'); document.body.style.overflow = ''; }
function handleStuOvClick(e) { if (e.target === document.getElementById('stuDetailOverlay')) closeStuOverlay(); }
// Aliases matching the names used in dashboard.html onclick attributes
function closeStuDetailOverlay() { closeStuOverlay(); }
function handleStuDetailOvClick(e) { handleStuOvClick(e); }


// ── SUGGESTED INTERVENTIONS ───────────────────────────────────────────────────
const SUGGESTED_INTERVENTIONS = {
  high: [
    { icon: '🚨', label: 'Urgent Counseling Session', desc: "Schedule an immediate one-on-one counseling session to address the student's academic and personal challenges." },
    { icon: '📞', label: 'Parent Notification Call', desc: "Contact parents or guardians immediately to discuss the student's risk status and develop a joint action plan." },
    { icon: '📋', label: 'Academic Recovery Plan', desc: "Create a structured, time-bound recovery plan with specific milestones for academic improvement." },
    { icon: '👩‍🏫', label: 'Peer Tutoring Assignment', desc: "Pair the student with a high-performing peer tutor for targeted subject support." },
  ],
  med: [
    { icon: '💬', label: 'One-on-One Check-in Meeting', desc: "Schedule a focused meeting to understand challenges and set concrete improvement goals." },
    { icon: '📊', label: 'Weekly Progress Check-in', desc: "Schedule a brief weekly touchpoint to monitor improvement and flag concerns early." },
    { icon: '👥', label: 'Study Group Assignment', desc: "Assign the student to a peer study group with stronger performers." },
    { icon: '⚠️', label: 'Attendance Warning Letter', desc: "Issue an official attendance warning letter to student and parents." },
  ],
  low: [
    { icon: '📊', label: 'Weekly Progress Check-in', desc: "Schedule a brief weekly touchpoint to monitor improvement and flag concerns early." },
    { icon: '📝', label: 'Assignment Recovery Plan', desc: "Create a structured plan to help the student recover missed assignments." },
  ],
  safe: [
    { icon: '🌟', label: 'Recognition & Encouragement', desc: "Acknowledge good performance to maintain motivation and engagement." },
    { icon: '📈', label: 'Set Stretch Goals', desc: "Work with the student to set higher academic targets for continued growth." },
  ],
};

function logIntervention() {
  if (!currentStudent) return;
  const level = currentStudent.riskLevel || currentStudent.risk || 'med';
  const suggestions = SUGGESTED_INTERVENTIONS[level] || SUGGESTED_INTERVENTIONS.med;
  const r = rc(level);
  const suggestionsHTML = suggestions.map((s, i) => `
    <label class="int-suggestion-item" for="intSug_${i}">
      <input type="checkbox" id="intSug_${i}" class="int-sug-checkbox" value="${s.label}"/>
      <div class="int-sug-icon">${s.icon}</div>
      <div class="int-sug-content">
        <div class="int-sug-label">${s.label}</div>
        <div class="int-sug-desc">${s.desc}</div>
      </div>
    </label>`).join('');
  document.getElementById('intPopupStudentName').textContent = currentStudent.name;
  document.getElementById('intPopupRiskPill').innerHTML = `<span class="rpd" style="background:${r.txt}"></span>${r.label}`;
  document.getElementById('intPopupRiskPill').style.cssText = `background:${r.bg};color:${r.txt};border:1px solid ${r.border};display:inline-flex;align-items:center;gap:5px;font-size:10.5px;font-weight:700;padding:4px 10px;border-radius:20px`;
  document.getElementById('intSuggestionsList').innerHTML = suggestionsHTML;
  document.getElementById('intWriteBox').value = '';
  document.getElementById('intWriteBox').style.borderColor = '';
  document.getElementById('interventionPopup').classList.add('open');
}

function closeInterventionPopup() { document.getElementById('interventionPopup').classList.remove('open'); }

async function lockIntervention() {
  const checked = [...document.querySelectorAll('.int-sug-checkbox:checked')].map(c => c.value);
  const note = document.getElementById('intWriteBox').value.trim();
  if (!checked.length && !note) {
    document.getElementById('intWriteBox').style.borderColor = 'var(--red)';
    document.getElementById('intWriteBox').placeholder = 'Please select a suggestion or write a note…';
    return;
  }
  closeInterventionPopup();

  const flagId = currentStudent && (STUDENT_TO_FLAG_ID[currentStudent.id] || currentStudent.flagId);
  if (flagId) {
    const interventionText = [...checked, note].filter(Boolean).join('; ');
    try { await logInterventionAPI(flagId, interventionText); } catch { }
  }

  const toast = document.createElement('div');
  toast.className = 'int-toast';
  toast.innerHTML = `✓ Intervention logged for <strong>${currentStudent.name}</strong>`;
  document.body.appendChild(toast);
  setTimeout(() => toast.classList.add('show'), 10);
  setTimeout(() => { toast.classList.remove('show'); setTimeout(() => toast.remove(), 400); }, 3000);
}

function mailStudent() { if (!currentStudent) return; alert(`📧 Email sent to ${currentStudent.name}`); }
function mailParents() { if (!currentStudent) return; alert(`📧 Email sent to parents of ${currentStudent.name}`); }

// ── ANALYTICS PAGE ────────────────────────────────────────────────────────────

// Analytics data is cached in api.js (session-level).
// No separate _analyticsCache needed here — just call the fetch functions directly.

async function initAnalyticsPage() {
  const w = currentWeek;
  const noDataEl = document.getElementById('anlNoData');
  const midtermSection = document.getElementById('anl-main-midterm');
  const endtermSection = document.getElementById('anl-main-endterm');
  const midtermTopBtn  = document.getElementById('atop-midterm');
  const endtermTopBtn  = document.getElementById('atop-endterm');
  const postMidTab     = document.getElementById('atgl-post');
  const postMidSection = document.getElementById('anl-post');

  if (noDataEl)       noDataEl.style.display       = 'none';
  if (midtermSection) { midtermSection.style.display = ''; midtermSection.classList.remove('active'); }
  if (endtermSection) { endtermSection.style.display = ''; endtermSection.classList.remove('active'); }
  if (midtermTopBtn)  { midtermTopBtn.style.display  = ''; midtermTopBtn.classList.remove('active'); }
  if (endtermTopBtn)  { endtermTopBtn.style.display  = ''; endtermTopBtn.classList.remove('active'); }
  if (postMidTab)     postMidTab.style.display      = '';
  if (postMidSection) postMidSection.style.display  = '';
  const postEndTab     = document.getElementById('atgl-post-end');
  const postEndSection = document.getElementById('anl-post-end');
  if (w < 17) {
    if (postEndTab)     postEndTab.style.display     = 'none';
    if (postEndSection) postEndSection.style.display = 'none';
  } else {
    if (postEndTab)     postEndTab.style.display     = '';
    if (postEndSection) postEndSection.style.display = '';
  }

  if (w < 6) {
    if (midtermSection) midtermSection.style.display = 'none';
    if (endtermSection) endtermSection.style.display = 'none';
    if (noDataEl) {
      noDataEl.style.display = 'flex';
      noDataEl.querySelector('span').textContent = `Analytics reports unlock from Week 6. You are on Week ${w}.`;
    }
    return;
  }

  if (w >= 16) {
    // Hide midterm section completely
    if (midtermSection) { midtermSection.style.display = 'none'; midtermSection.classList.remove('active'); }
    if (endtermSection) { endtermSection.style.display = ''; endtermSection.classList.add('active'); }
    document.querySelectorAll('.atop-btn').forEach(b => b.classList.remove('active'));
    if (endtermTopBtn) endtermTopBtn.classList.add('active');

    if (w >= 17) {
      setEndtermView('post');
    } else {
      setEndtermView('pre');
    }
    return;
  }

  // Weeks 6–15: midterm phase
  if (endtermSection) endtermSection.style.display = 'none';
  if (endtermTopBtn)  endtermTopBtn.style.display  = 'none';
  document.querySelectorAll('.atop-btn').forEach(b => b.classList.remove('active'));
  if (midtermSection) midtermSection.classList.add('active');
  if (midtermTopBtn)  midtermTopBtn.classList.add('active');

  // Post-midterm tab only from week 10
  if (w < 10) {
    if (postMidTab)     postMidTab.style.display     = 'none';
    if (postMidSection) postMidSection.style.display = 'none';
  }

  // "Published N weeks ago" badge for weeks > 7
  const badge = document.getElementById('anlPreMidPublishedBadge');
  if (badge) {
    if (w > 7) {
      const weeksAgo = w - 7;
      badge.textContent = `Published ${weeksAgo} week${weeksAgo > 1 ? 's' : ''} ago`;
      badge.style.display = 'inline-flex';
    } else {
      badge.style.display = 'none';
    }
  }

  if (w >= 10) {
    setMidtermView('post');
  } else {
    setMidtermView('pre');
  }
}

function setAnalyticsMain(section) {
  if (section === 'endterm' && currentWeek < 16) return;
  document.querySelectorAll('.analytics-main-section').forEach(s => {
    s.classList.remove('active');
    s.style.display = 'none';
  });
  const target = document.getElementById('anl-main-' + section);
  if (target) { target.style.display = ''; target.classList.add('active'); }
  document.querySelectorAll('.atop-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('atop-' + section);
  if (btn) btn.classList.add('active');
  if (section === 'midterm') requestAnimationFrame(() => requestAnimationFrame(buildMidtermCharts));
  if (section === 'endterm') requestAnimationFrame(() => requestAnimationFrame(buildEndtermCharts));
}
function setMidtermView(v) {
  if (v === 'post' && currentWeek < 10) return;
  ['pre', 'post'].forEach(x => { document.getElementById('anl-' + x).classList.toggle('active', x === v); document.getElementById('atgl-' + x).classList.toggle('active', x === v); });
  if (v === 'pre') requestAnimationFrame(() => requestAnimationFrame(buildPreMidtermCharts));
  if (v === 'post') requestAnimationFrame(() => requestAnimationFrame(buildPostMidtermCharts));
}
function setEndtermView(v) {
  if (v === 'post' && currentWeek < 17) return;
  ['pre', 'post'].forEach(x => { document.getElementById('anl-' + x + '-end').classList.toggle('active', x === v); document.getElementById('atgl-' + x + '-end').classList.toggle('active', x === v); });
  if (v === 'pre') requestAnimationFrame(() => requestAnimationFrame(buildPreEndtermCharts));
  if (v === 'post') setTimeout(buildPostEndtermCharts, 30);
}

// ── ANALYTICS DATA HELPERS ────────────────────────────────────────────────────

const DIST_KEYS = ['lt_40', '40to50', '51to60', '61to70', '71to80', '81to90', '91to100'];
const DIST_LABELS = ['<40%', '41–50%', '51–60%', '61–70%', '71–80%', '81–90%', '91–100%'];

function distToArray(dist) {
  return DIST_KEYS.map(k => dist[k] || 0);
}

// These all return from api.js cache instantly on repeat calls.
async function getPreMidtermData() {
  if (currentWeek < 6)  return null;
  try { return await fetchPreMidtermReport(CLASS_ID, SEMESTER); } catch { return null; }
}
async function getPostMidtermData() {
  if (currentWeek < 10) return null;
  try { return await fetchPostMidtermReport(CLASS_ID, SEMESTER); } catch { return null; }
}
async function getPreEndtermData() {
  if (currentWeek < 16) return null;
  try { return await fetchPreEndtermReport(CLASS_ID, SEMESTER); } catch { return null; }
}
async function getPostEndtermData() {
  if (currentWeek < 17) return null;
  try { return await fetchPostEndtermReport(CLASS_ID, SEMESTER); } catch { return null; }
}

async function buildPreMidtermCharts() {
  const data = await getPreMidtermData();
  if (data && data.marks_distribution) {
    window._preMidtermDistData = distToArray(data.marks_distribution);
    window._preMidtermStats = {
      mean: data.mean_predicted_score,
      std: data.standard_deviation,
      top20: data.top20_pct,
      bot20: data.bottom20_pct,
    };
  }
  _buildPreMidtermCharts();
  _renderPreMidtermStats(data);
}

async function buildPostMidtermCharts() {
  const data = await getPostMidtermData();
  if (data && data.marks_distribution) {
    const dist = data.marks_distribution;
    window._postMidtermPredData = DIST_KEYS.map(k => dist[k]?.predicted_number_of_students || 0);
    window._postMidtermActualData = DIST_KEYS.map(k => dist[k]?.actual_number_of_students || 0);
  }
  _buildPostMidtermCharts();
  _renderPostMidtermStats(data);
  _renderPerformerList(data.underperformers, 'postMidtermUnder', 'underperformers');
_renderPerformerList(data.outperformers,   'postMidtermOver',  'outperformers');
}

async function buildPreEndtermCharts() {
  const data = await getPreEndtermData();
  if (data && data.marks_distribution) {
    window._preEndtermDistData = distToArray(data.marks_distribution);
  }
  _buildPreEndtermCharts();
  _renderPreEndtermStats(data);
}

async function buildPostEndtermCharts() {
  const data = await getPostEndtermData();
  if (data && data.marks_distribution) {
    const dist = data.marks_distribution;
    window._postEndtermPredData = DIST_KEYS.map(k => dist[k]?.predicted_number_of_students || 0);
    window._postEndtermActualData = DIST_KEYS.map(k => dist[k]?.actual_number_of_students || 0);
  }
  _buildPostEndtermCharts();
  _renderPostEndtermStats(data);
  _renderPerformerList(data.underperformers, 'postEndtermUnder', 'underperformers');
  _renderPerformerList(data.outperformers,   'postEndtermOver',  'outperformers');
}

// ── ANALYTICS STAT RENDERERS ──────────────────────────────────────────────────

function _renderPreMidtermStats(data) {
  if (!data) return;
  const set = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
  const el = document.getElementById('preMidtermStatsSummary');
  if (el) el.innerHTML = `
    <div class="anl-big-stat-card"><div class="anl-big-stat-label">MEAN PREDICTED SCORE</div><div class="anl-big-stat-val" style="color:var(--accent2)">${data.mean_predicted_score}%</div></div>
    <div class="anl-big-stat-card"><div class="anl-big-stat-label">STD DEVIATION</div><div class="anl-big-stat-val" style="color:var(--purple)">±${data.standard_deviation}</div></div>
    <div class="anl-big-stat-card"><div class="anl-big-stat-label">MODE (MOST LIKELY SCORE)</div><div class="anl-big-stat-val" style="color:var(--green)">${data.mode_marks ?? '—'}</div></div>`;
  set('preMidBot', data.bottom20_pct != null ? data.bottom20_pct + '%' : '—');
  set('preMidTop', data.top20_pct    != null ? data.top20_pct    + '%' : '—');
  _renderWatchlist(data.watchlist, 'preMidtermWatchlist');
}

function _renderPostMidtermStats(data) {
  if (!data) return;
  const set = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
  const el = document.getElementById('postMidtermStatsSummary');
  if (el) el.innerHTML = `
    <div class="anl-big-stat-card"><div class="anl-big-stat-label">MEAN OF MIDTERM SCORE</div><div class="anl-big-stat-val" style="color:var(--accent2)">${data.avg_score}%</div></div>
    <div class="anl-big-stat-card"><div class="anl-big-stat-label">STD DEVIATION</div><div class="anl-big-stat-val" style="color:var(--purple)">±${data.standard_deviation}</div></div>
    <div class="anl-big-stat-card"><div class="anl-big-stat-label">MODE (MOST SCORED)</div><div class="anl-big-stat-val" style="color:var(--green)">${data.mode_score ?? '—'}</div></div>`;
  set('postMidBot', data.bottom20_pct != null ? data.bottom20_pct + '%' : '—');
  set('postMidTop', data.top20_pct    != null ? data.top20_pct    + '%' : '—');
  _renderPerformerList(data.underperformers, 'postMidtermUnder', 'underperformers');
  _renderPerformerList(data.outperformers,   'postMidtermOver',  'outperformers');
}

function _renderPreEndtermStats(data) {
  if (!data) return;
  const set = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
  const el = document.getElementById('preEndtermStatsSummary');
  if (el) el.innerHTML = `
    <div class="anl-big-stat-card"><div class="anl-big-stat-label">MEAN PREDICTED SCORE</div><div class="anl-big-stat-val" style="color:var(--accent2)">${data.mean_predicted_score}%</div></div>
    <div class="anl-big-stat-card"><div class="anl-big-stat-label">STD DEVIATION</div><div class="anl-big-stat-val" style="color:var(--purple)">±${data.standard_deviation}</div></div>
    <div class="anl-big-stat-card"><div class="anl-big-stat-label">MODE</div><div class="anl-big-stat-val" style="color:var(--green)">${data.mode_marks ?? '—'}</div></div>`;
  set('preEndBot', data.bottom20_pct != null ? data.bottom20_pct + '%' : '—');
  set('preEndTop', data.top20_pct    != null ? data.top20_pct    + '%' : '—');
  _renderWatchlist(data.watchlist, 'preEndtermWatchlist');
}

function _renderPostEndtermStats(data) {
  if (!data) return;
  const set = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
  const el = document.getElementById('postEndtermStatsSummary');
  if (el) el.innerHTML = `
    <div class="anl-big-stat-card"><div class="anl-big-stat-label">MEAN ACTUAL SCORE</div><div class="anl-big-stat-val" style="color:var(--accent2)">${data.avg_score}%</div></div>
    <div class="anl-big-stat-card"><div class="anl-big-stat-label">STD DEVIATION</div><div class="anl-big-stat-val" style="color:var(--purple)">±${data.standard_deviation}</div></div>
    <div class="anl-big-stat-card"><div class="anl-big-stat-label">MODE (MOST SCORED)</div><div class="anl-big-stat-val" style="color:var(--green)">${data.mode_score ?? '—'}</div></div>`;
  set('postEndBot', data.bottom20_pct != null ? data.bottom20_pct + '%' : '—');
  set('postEndTop', data.top20_pct    != null ? data.top20_pct    + '%' : '—');
  _renderPerformerList(data.underperformers, 'postEndtermUnder', 'underperformers');
  _renderPerformerList(data.outperformers,   'postEndtermOver',  'outperformers');
}

function _renderWatchlist(watchlist, elId) {
  const el = document.getElementById(elId);
  if (!el || !watchlist) return;
  const entries = Object.entries(watchlist);
  if (!entries.length) {
    el.innerHTML = '<div class="anl-student-cards-empty">No students on watchlist</div>';
    return;
  }
  el.innerHTML = `<div class="anl-student-card-grid">${entries.map(([sid, [name, reason, score, riskLvl]]) => {
    const scoreColor = score < 50 ? 'var(--red)' : score < 65 ? 'var(--amber)' : 'var(--green)';
    const r = rc(riskLvl || 'med');
    const av = initials(name);
    return `<div class="anl-stu-card anl-stu-card--watch">
      <div class="anl-stu-card-top">
        <div class="anl-stu-av" style="background:${r.bg};color:${r.txt}">${av}</div>
        <div class="anl-stu-info">
          <div class="anl-stu-name">${name}</div>
          <div class="anl-stu-id">${sid}</div>
        </div>
        <span class="anl-risk-pill" style="background:${r.bg};color:${r.txt};border:1px solid ${r.border}">${r.label}</span>
      </div>
      <div class="anl-stu-card-metrics">
        <div class="anl-stu-metric">
          <div class="anl-stu-metric-label">Predicted Score</div>
          <div class="anl-stu-metric-val" style="color:${scoreColor}">${score}%</div>
        </div>
      </div>
    </div>`;
  }).join('')}</div>`;
}

function _renderPerformerList(list, elId, label) {
  const el = document.getElementById(elId);
  if (!el || !list) return;
  const entries = Object.entries(list);
  if (!entries.length) {
    el.innerHTML = `<div class="anl-student-cards-empty">No ${label.toLowerCase()}</div>`;
    return;
  }
  const isOver = label === 'outperformers';
  el.innerHTML = `<div class="anl-student-card-grid">${entries.map(([sid, [name, scores]]) => {
    const delta = scores.actual_score - scores.predicted_score;
    const deltaStr = (delta > 0 ? '+' : '') + (delta.toFixed ? delta.toFixed(1) : delta) + '%';
    const deltaColor = delta > 0 ? 'var(--green)' : 'var(--red)';
    const av = initials(name);
    const avatarBg = isOver ? 'rgba(63,185,80,0.12)' : 'rgba(248,81,73,0.12)';
    const avatarTxt = isOver ? 'var(--green)' : 'var(--red)';
    return `<div class="anl-stu-card ${isOver ? 'anl-stu-card--over' : 'anl-stu-card--under'}">
      <div class="anl-stu-card-top">
        <div class="anl-stu-av" style="background:${avatarBg};color:${avatarTxt}">${av}</div>
        <div class="anl-stu-info">
          <div class="anl-stu-name">${name}</div>
          <div class="anl-stu-id">${sid}</div>
        </div>
        <span class="anl-stu-delta" style="color:${deltaColor}">${deltaStr}</span>
      </div>
      <div class="anl-stu-card-metrics">
        <div class="anl-stu-metric">
          <div class="anl-stu-metric-label">Predicted</div>
          <div class="anl-stu-metric-val">${scores.predicted_score}%</div>
        </div>
        <div class="anl-stu-metric">
          <div class="anl-stu-metric-label">Actual</div>
          <div class="anl-stu-metric-val" style="color:${deltaColor}">${scores.actual_score}%</div>
        </div>
      </div>
    </div>`;
  }).join('')}</div>`;
}

// ── SCHEDULE / CALENDAR ───────────────────────────────────────────────────────
let scheduleTasks = [
  { id: 1, name: "Prepare midterm review material", day: 2, time: 10, duration: 1, category: "Academic" },
  { id: 2, name: "Meet with HOD about at-risk student", day: 2, time: 14, duration: 1, category: "Meeting" },
  { id: 3, name: "Review flagged students progress", day: 1, time: 11, duration: 1, category: "Urgent" },
  { id: 4, name: "Send parent emails for flagged students", day: 3, time: 9, duration: 1, category: "Email" },
];
let nextSchedId = 5;
const SCHED_COLORS = {
  Academic: { bg: 'rgba(59,130,246,0.12)', border: 'rgba(59,130,246,0.3)', text: '#3b82f6', tagBg: 'rgba(59,130,246,0.12)', tagTxt: '#3b82f6' },
  Meeting: { bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.3)', text: '#f59e0b', tagBg: 'rgba(245,158,11,0.12)', tagTxt: '#d97706' },
  Urgent: { bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.3)', text: '#ef4444', tagBg: 'rgba(239,68,68,0.12)', tagTxt: '#ef4444' },
  Email: { bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.3)', text: '#10b981', tagBg: 'rgba(16,185,129,0.12)', tagTxt: '#059669' },
};
const SCHED_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const SCHED_DATES = [7, 8, 9, 10, 11, 12];

function addScheduleTask() {
  const name = document.getElementById('schedTaskName').value.trim();
  if (!name) return;
  const day = parseInt(document.getElementById('schedDay').value);
  const time = parseInt(document.getElementById('schedTime').value);
  const duration = parseInt(document.getElementById('schedDuration').value);
  const category = document.getElementById('schedCategory').value;
  scheduleTasks.push({ id: nextSchedId++, name, day, time, duration, category });
  document.getElementById('schedTaskName').value = '';
  renderSchedule();
}
function removeScheduleTask(id) { scheduleTasks = scheduleTasks.filter(t => t.id !== id); renderSchedule(); }
function renderScheduleTaskList() {
  const container = document.getElementById('schedTaskItems');
  if (!container) return;
  if (!scheduleTasks.length) { container.innerHTML = '<div style="font-size:12px;color:var(--txt3);text-align:center;padding:16px 0">No tasks yet — add one above!</div>'; return; }
  container.innerHTML = scheduleTasks.map(t => {
    const c = SCHED_COLORS[t.category] || SCHED_COLORS.Academic;
    const dayLabel = SCHED_DAYS[t.day] || 'Mon';
    const truncName = t.name.length > 26 ? t.name.substring(0, 26) + '...' : t.name;
    return `<div class="sched-task-item" style="border-left:3px solid ${c.text}">
      <div class="sched-task-info"><div class="sched-task-name">${truncName}</div><div class="sched-task-time">${dayLabel} · ${t.time}:00</div></div>
      <span class="todo-tag" style="background:${c.tagBg};color:${c.tagTxt};font-size:10px;font-weight:700;padding:2px 8px;border-radius:6px;white-space:nowrap">${t.category}</span>
      <button class="sched-del-btn" onclick="removeScheduleTask(${t.id})">×</button>
    </div>`;
  }).join('');
}
function renderScheduleGrid() {
  const grid = document.getElementById('calGrid');
  if (!grid) return;
  const hours = [];
  for (let h = 8; h <= 17; h++) hours.push(h);
  let html = '<div class="cal-day-head" style="background:var(--bg3);border-bottom:1px solid var(--border)"></div>';
  for (let d = 0; d < 6; d++) {
    const isToday = d === 1;
    html += `<div class="cal-day-head"><div class="cal-day-name">${SCHED_DAYS[d]}</div><div class="cal-day-num${isToday ? ' today' : ''}"> ${SCHED_DATES[d]}</div></div>`;
  }
  for (let h of hours) {
    html += `<div class="cal-time-cell">${h}:00</div>`;
    for (let d = 0; d < 6; d++) {
      const task = scheduleTasks.find(t => t.day === d && t.time === h);
      if (task) {
        const c = SCHED_COLORS[task.category] || SCHED_COLORS.Academic;
        const heightPx = task.duration * 50;
        const truncName = task.name.length > 20 ? task.name.substring(0, 20) + '...' : task.name;
        const endTime = task.time + task.duration;
        html += `<div class="cal-cell" style="position:relative"><div class="sched-block" style="background:${c.bg};border-left:3px solid ${c.text};height:${heightPx}px;position:absolute;top:2px;left:2px;right:2px;padding:6px 8px;border-radius:6px;font-size:11px;font-weight:600;color:${c.text};overflow:hidden;z-index:2;cursor:pointer">${truncName}<div style="font-size:10px;font-weight:400;margin-top:2px;opacity:0.75">${task.time}:00–${endTime}:00</div></div></div>`;
      } else {
        html += `<div class="cal-cell"></div>`;
      }
    }
  }
  grid.innerHTML = html;
}
function renderSchedule() { renderScheduleTaskList(); renderScheduleGrid(); }
function initCalendar() { renderSchedule(); }

// ── TOAST ─────────────────────────────────────────────────────────────────────
function showToast(msg) {
  const toast = document.createElement('div');
  toast.className = 'int-toast';
  toast.innerHTML = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.classList.add('show'), 10);
  setTimeout(() => { toast.classList.remove('show'); setTimeout(() => toast.remove(), 400); }, 3000);
}
