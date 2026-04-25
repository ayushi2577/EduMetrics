// EduMetrics — js/charts.js
// All Chart.js chart builders.
// Analytics charts read live data injected by script.js via window._xxx variables.
// If no live data is present yet, sensible fallback arrays are used.

function isDark() { return document.documentElement.getAttribute('data-theme') !== 'light' }

function chartDefaults() {
  const d = isDark();
  return {
    tc: d ? '#7aaac8' : '#64748b',
    gc: d ? 'rgba(100,160,255,0.07)' : 'rgba(0,0,0,0.05)',
    tip: {
      backgroundColor: d ? '#0e1e35' : '#fff', borderColor: d ? 'rgba(100,160,255,0.22)' : 'rgba(0,0,0,.08)', borderWidth: 1,
      titleColor: d ? '#ddeeff' : '#0f172a', bodyColor: d ? '#7aaac8' : '#475569', padding: 12, cornerRadius: 10
    }
  };
}

// ── NULL FILL HELPER ──────────────────────────────────────────────────────────
// Interpolates across null gaps; carries forward/backward at edges.
// Keeps the analysis engine clean — nulls are only filled at display time.
function fillNulls(arr) {
  if (!arr || !arr.length) return arr;
  const out = [...arr];
  for (let i = 0; i < out.length; i++) {
    if (out[i] == null) {
      const prev = out.slice(0, i).reverse().find(v => v != null) ?? null;
      const next = out.slice(i + 1).find(v => v != null) ?? null;
      if (prev != null && next != null) out[i] = Math.round(((prev + next) / 2) * 100) / 100;
      else if (prev != null) out[i] = prev;
      else if (next != null) out[i] = next;
      // else leave null — truly no data at all (chart will just skip point)
    }
  }
  return out;
}

// ── RISK SCATTER CHART (Dashboard) ──
let riskChartInst = null;
function buildRiskChart() {
  if (riskChartInst) { riskChartInst.destroy(); riskChartInst = null }
  const d = isDark();
  const { tc, gc, tip } = chartDefaults();
  const colorMap = { high: 'rgba(255,80,80,0.88)', med: 'rgba(245,166,35,0.88)', safe: 'rgba(0,214,143,0.88)' };
  // ALL_STUDENTS is the normalised array from script.js
  // Fields: attendance (may be 0), riskScore, riskLevel
  const riskData = (typeof ALL_STUDENTS !== 'undefined' ? ALL_STUDENTS : []).map(s => ({
    name: s.name, attend: s.attendance || 0, riskScore: s.riskScore || 0, r: s.riskLevel || 'safe'
  }));
  riskChartInst = new Chart(document.getElementById('riskChart'), {
    type: 'scatter',
    data: {
      datasets: [{
        label: 'Students', data: riskData.map(s => ({ x: s.attend, y: s.riskScore, name: s.name, r: s.r })),
        backgroundColor: riskData.map(s => colorMap[s.r] || colorMap.safe),
        borderColor: riskData.map(s => (colorMap[s.r] || colorMap.safe).replace('0.85', '1')),
        borderWidth: 1.5, pointRadius: 9, pointHoverRadius: 13,
      }, {
        label: '75% Threshold', data: [{ x: 75, y: 0 }, { x: 75, y: 100 }],
        type: 'line', borderColor: 'rgba(210,153,34,0.7)', borderWidth: 2, borderDash: [6, 4], pointRadius: 0, fill: false,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false }, tooltip: {
          ...tip, filter: item => item.datasetIndex === 0,
          callbacks: { title: ctx => `${ctx[0].raw.name}`, label: ctx => `Attendance: ${ctx.raw.x}% · Risk: ${ctx.raw.y}%` }
        }
      },
      scales: {
        x: { grid: { color: gc }, ticks: { color: tc, font: { size: 11, family: 'DM Sans' } }, min: 0, max: 105, title: { display: true, text: 'Attendance (%)', color: tc, font: { size: 11 } } },
        y: { grid: { color: gc }, ticks: { color: tc, font: { size: 11 } }, min: 0, max: 100, title: { display: true, text: 'Risk Score (%)', color: tc, font: { size: 11 } } }
      }
    }
  });
}

// ── STUDENT LINE CHART (Flagged Detail Modal) ──
let dmLineChartInst = null;
function buildDmLineChart(s) {
  if (dmLineChartInst) { dmLineChartInst.destroy(); dmLineChartInst = null }
  const { tc, gc, tip } = chartDefaults();
  dmLineChartInst = new Chart(document.getElementById('dmLineChart'), {
    type: 'line',
    data: {
      labels: WEEKS, datasets: [
        { label: 'Effort', data: fillNulls(s.weekEt), spanGaps: true, borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.08)', borderWidth: 2.5, tension: 0.38, fill: true, pointBackgroundColor: '#58a6ff', pointRadius: 4, pointHoverRadius: 7 },
        { label: 'Academic Performance', data: fillNulls(s.weekAt), spanGaps: true, borderColor: '#d29922', backgroundColor: 'rgba(210,153,34,0.06)', borderWidth: 2.5, tension: 0.38, fill: true, pointBackgroundColor: '#d29922', pointRadius: 4, pointHoverRadius: 7 }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: tip },
      scales: { x: { grid: { color: gc }, ticks: { color: tc, font: { size: 10, family: 'DM Sans' } } }, y: { grid: { color: gc }, ticks: { color: tc, font: { size: 10 } }, min: 0, max: 100 } }
    }
  });
}

// ── STUDENT QUAD CHART (Flagged Detail Modal) ──
let dmQuadChartInst = null;
function buildDmQuadChart(s) {
  if (dmQuadChartInst) { dmQuadChartInst.destroy(); dmQuadChartInst = null }
  const { tc, gc, tip } = chartDefaults();
  const classEt = s.classAvgEt || 65;
  const classPerf = s.classAvgPerf || 70;
  dmQuadChartInst = new Chart(document.getElementById('dmQuadChart'), {
    type: 'scatter',
    data: {
      datasets: [
        { data: [{ x: classEt, y: 0 }, { x: classEt, y: 100 }], type: 'line', borderColor: 'rgba(100,116,139,0.5)', borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0, fill: false },
        { data: [{ x: 0, y: classPerf }, { x: 100, y: classPerf }], type: 'line', borderColor: 'rgba(100,116,139,0.5)', borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0, fill: false },
        { label: 'This Week', data: [{ x: s.etThisWeek || 0, y: s.perfThisWeek || 0, label: 'This Week' }], backgroundColor: 'rgba(210,153,34,0.9)', borderColor: '#d29922', borderWidth: 2, pointRadius: 11, pointHoverRadius: 15 },
        { label: 'Avg', data: [{ x: s.studentAvgEt || 0, y: s.studentAvgPerf || 0, label: 'Avg' }], backgroundColor: 'rgba(88,166,255,0.9)', borderColor: '#58a6ff', borderWidth: 2, pointRadius: 11, pointHoverRadius: 15 }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { ...tip, filter: item => item.datasetIndex >= 2, callbacks: { title: ctx => `${ctx[0].raw.label || ''}`, label: ctx => `Effort: ${ctx.raw.x}% · Acad: ${ctx.raw.y}%` } } },
      scales: { x: { grid: { color: gc }, ticks: { color: tc, font: { size: 10 } }, min: 0, max: 100, title: { display: true, text: 'Effort (%)', color: tc, font: { size: 10 } } }, y: { grid: { color: gc }, ticks: { color: tc, font: { size: 10 } }, min: 0, max: 100, title: { display: true, text: 'Academic Performance (%)', color: tc, font: { size: 10 } } } }
    }
  });
}

// ── STUDENT DETAIL LINE & QUAD (Students Page Modal) ──
let stuDetLineInst = null, stuDetQuadInst = null;
function buildStuDetLineChart(s) {
  if (stuDetLineInst) { stuDetLineInst.destroy(); stuDetLineInst = null }
  const { tc, gc, tip } = chartDefaults();
  stuDetLineInst = new Chart(document.getElementById('stuDetLineChart'), {
    type: 'line',
    data: {
      labels: WEEKS, datasets: [
        { label: 'Effort', data: fillNulls(s.weekEt), spanGaps: true, borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.08)', borderWidth: 2.5, tension: 0.38, fill: true, pointBackgroundColor: '#58a6ff', pointRadius: 4, pointHoverRadius: 7 },
        { label: 'Academic Performance', data: fillNulls(s.weekAt), spanGaps: true, borderColor: '#d29922', backgroundColor: 'rgba(210,153,34,0.06)', borderWidth: 2.5, tension: 0.38, fill: true, pointBackgroundColor: '#d29922', pointRadius: 4, pointHoverRadius: 7 }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: tip },
      scales: { x: { grid: { color: gc }, ticks: { color: tc, font: { size: 10, family: 'DM Sans' } } }, y: { grid: { color: gc }, ticks: { color: tc, font: { size: 10 } }, min: 0, max: 100 } }
    }
  });
}
function buildStuDetQuadChart(s) {
  if (stuDetQuadInst) { stuDetQuadInst.destroy(); stuDetQuadInst = null }
  const { tc, gc, tip } = chartDefaults();
  const classEt = s.classAvgEt || 65;
  const classPerf = s.classAvgPerf || 70;
  stuDetQuadInst = new Chart(document.getElementById('stuDetQuadChart'), {
    type: 'scatter',
    data: {
      datasets: [
        { data: [{ x: classEt, y: 0 }, { x: classEt, y: 100 }], type: 'line', borderColor: 'rgba(100,116,139,0.5)', borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0, fill: false },
        { data: [{ x: 0, y: classPerf }, { x: 100, y: classPerf }], type: 'line', borderColor: 'rgba(100,116,139,0.5)', borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0, fill: false },
        { label: 'This Week', data: [{ x: s.etThisWeek || 0, y: s.perfThisWeek || 0, label: 'This Week' }], backgroundColor: 'rgba(210,153,34,0.9)', borderColor: '#d29922', borderWidth: 2, pointRadius: 11, pointHoverRadius: 15 },
        { label: 'Avg', data: [{ x: s.studentAvgEt || 0, y: s.studentAvgPerf || 0, label: 'Avg' }], backgroundColor: 'rgba(88,166,255,0.9)', borderColor: '#58a6ff', borderWidth: 2, pointRadius: 11, pointHoverRadius: 15 }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { ...tip, filter: item => item.datasetIndex >= 2, callbacks: { title: ctx => `${ctx[0].raw.label || ''}`, label: ctx => `Effort: ${ctx.raw.x}% · Acad: ${ctx.raw.y}%` } } },
      scales: { x: { grid: { color: gc }, ticks: { color: tc, font: { size: 10 } }, min: 0, max: 100, title: { display: true, text: 'Effort (%)', color: tc, font: { size: 10 } } }, y: { grid: { color: gc }, ticks: { color: tc, font: { size: 10 } }, min: 0, max: 100, title: { display: true, text: 'Academic Performance (%)', color: tc, font: { size: 10 } } } }
    }
  });
}

// ── ANALYTICS CHARTS (live data injected by script.js) ─────────────────────────
// Fallback data used when backend response is unavailable
const _FB_DIST_PRED = [2, 5, 6, 8, 12, 4, 2];
const _FB_DIST_ACTUAL = [3, 4, 6, 10, 10, 5, 2];
const DIST_LABELS_CHART = ['<40%', '41–50%', '51–60%', '61–70%', '71–80%', '81–90%', '91–100%'];

// Pre-Midterm
let preDistChartInst = null, preAttChartInst = null;
function _buildPreMidtermCharts() {
  const { tc, gc, tip } = chartDefaults();
  const distData = window._preMidtermDistData || _FB_DIST_PRED;

  if (preDistChartInst) { preDistChartInst.destroy(); preDistChartInst = null }
  const c1 = document.getElementById('preDistChart'); if (c1) {
    preDistChartInst = new Chart(c1, {
      type: 'bar', data: {
        labels: DIST_LABELS_CHART,
        datasets: [{
          label: 'No. of Students', data: distData,
          backgroundColor: ['rgba(248,100,100,0.82)', 'rgba(248,100,100,0.65)', 'rgba(210,175,34,0.70)', 'rgba(210,175,34,0.55)', 'rgba(52,199,143,0.78)', 'rgba(52,199,143,0.90)', 'rgba(88,166,255,0.85)'],
          borderRadius: 7, borderSkipped: false, barThickness: 'flex'
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { ...tip, callbacks: { label: ctx => `${ctx.raw} students` } } },
        scales: {
          x: { grid: { color: gc }, ticks: { color: tc, font: { size: 11, family: 'DM Sans' } }, title: { display: true, text: 'Predicted Score Range', color: tc, font: { size: 11 } } },
          y: { grid: { color: gc }, ticks: { color: tc, font: { size: 11 } }, min: 0, title: { display: true, text: 'No. of Students', color: tc, font: { size: 11 } } }
        }
      }
    });
  }
}

// Post-Midterm
let postDistChartInst = null, postRiskChartInst = null;
function _buildPostMidtermCharts() {
  const { tc, gc, tip } = chartDefaults();
  const predData = window._postMidtermPredData || _FB_DIST_PRED;
  const actualData = window._postMidtermActualData || _FB_DIST_ACTUAL;

  if (postDistChartInst) { postDistChartInst.destroy(); postDistChartInst = null }
  const c1 = document.getElementById('postDistChart'); if (c1) {
    postDistChartInst = new Chart(c1, {
      type: 'bar', data: {
        labels: DIST_LABELS_CHART,
        datasets: [
          { label: 'Actual', data: actualData, backgroundColor: 'rgba(74,144,226,0.85)', borderRadius: 5, borderSkipped: false },
          { label: 'Predicted', data: predData, backgroundColor: 'rgba(183,139,250,0.38)', borderRadius: 5, borderSkipped: false }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: true, labels: { color: tc, font: { size: 11, family: 'DM Sans' }, boxWidth: 12, usePointStyle: true } }, tooltip: tip },
        scales: {
          x: { grid: { color: gc }, ticks: { color: tc, font: { size: 11, family: 'DM Sans' } }, title: { display: true, text: 'Score Range', color: tc, font: { size: 11 } } },
          y: { grid: { color: gc }, ticks: { color: tc, font: { size: 11 } }, min: 0, title: { display: true, text: 'No. of Students', color: tc, font: { size: 11 } } }
        }
      }
    });
  }

  // Risk delta chart — built from outperformers/underperformers if available
  if (postRiskChartInst) { postRiskChartInst.destroy(); postRiskChartInst = null }
  const c2 = document.getElementById('postRiskDeltaChart'); if (c2) {
    const cache = window._analyticsCache && window._analyticsCache.postMidterm;
    let names = [], deltas = [];
    if (cache) {
      const all = { ...(cache.outperformers || {}), ...(cache.underperformers || {}) };
      const entries = Object.entries(all).slice(0, 8);
      names = entries.map(([, v]) => v[0].split(' ')[0]);
      deltas = entries.map(([, v]) => v[1].actual_score - v[1].predicted_score);
    } else {
      names = ['Stu 1', 'Stu 2', 'Stu 3', 'Stu 4', 'Stu 5', 'Stu 6', 'Stu 7'];
      deltas = [8, 12, -5, 4, 15, -8, 2];
    }
    postRiskChartInst = new Chart(c2, {
      type: 'bar', data: {
        labels: names,
        datasets: [{
          label: 'Score Δ vs Predicted',
          data: deltas,
          backgroundColor: deltas.map(v => v > 0 ? 'rgba(63,185,80,0.70)' : 'rgba(248,81,73,0.70)'),
          borderRadius: 4, borderSkipped: false
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { ...tip, callbacks: { label: ctx => `Δ: ${ctx.raw > 0 ? '+' : ''}${ctx.raw}%` } } },
        scales: {
          x: { grid: { color: gc }, ticks: { color: tc, font: { size: 10, family: 'DM Sans' } } },
          y: { grid: { color: gc }, ticks: { color: tc, font: { size: 10 } }, title: { display: true, text: 'Score Change (%)', color: tc, font: { size: 10 } } }
        }
      }
    });
  }
}

// Pre-End Term
let preEndDistChartInst = null, preEndTrendChartInst = null;
function _buildPreEndtermCharts() {
  const testCanvas = document.getElementById('preEndChart');
  if (testCanvas && testCanvas.offsetWidth === 0) { setTimeout(_buildPreEndtermCharts, 30); return; }
  const { tc, gc, tip } = chartDefaults();
  const distData = window._preEndtermDistData || [5, 6, 8, 9, 8, 3, 1];

  if (preEndDistChartInst) { preEndDistChartInst.destroy(); preEndDistChartInst = null }
  const c1 = document.getElementById('preEndChart'); if (c1) {
    preEndDistChartInst = new Chart(c1, {
      type: 'bar', data: {
        labels: DIST_LABELS_CHART,
        datasets: [{
          label: 'No. of Students', data: distData,
          backgroundColor: ['rgba(248,100,100,0.82)', 'rgba(248,100,100,0.65)', 'rgba(210,175,34,0.70)', 'rgba(210,175,34,0.55)', 'rgba(52,199,143,0.78)', 'rgba(52,199,143,0.90)', 'rgba(88,166,255,0.85)'],
          borderRadius: 7, borderSkipped: false, barThickness: 'flex'
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { ...tip, callbacks: { label: ctx => `${ctx.raw} students` } } },
        scales: {
          x: { grid: { color: gc }, ticks: { color: tc, font: { size: 11, family: 'DM Sans' } }, title: { display: true, text: 'Predicted Score Range', color: tc, font: { size: 11 } } },
          y: { grid: { color: gc }, ticks: { color: tc, font: { size: 11 } }, min: 0, title: { display: true, text: 'No. of Students', color: tc, font: { size: 11 } } }
        }
      }
    });
  }

  if (preEndTrendChartInst) { preEndTrendChartInst.destroy(); preEndTrendChartInst = null }
  const c2 = document.getElementById('preEndTrendChart'); if (c2) {
    preEndTrendChartInst = new Chart(c2, {
      type: 'line', data: {
        labels: ['W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W7', 'W8', 'W9', 'W10', 'W11', 'W12'],
        datasets: [
          { label: 'Avg Academic Performance', data: [72, 70, 68, 65, 62, 60, 58, 57, 56, 54, 53, 52], borderColor: '#d29922', backgroundColor: 'rgba(210,153,34,0.08)', borderWidth: 2.5, tension: 0.35, fill: true, pointRadius: 3, pointHoverRadius: 6 },
          { label: 'Class Target', data: [65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65], borderColor: 'rgba(63,185,80,0.6)', borderWidth: 1.5, borderDash: [5, 4], pointRadius: 0, fill: false }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: true, labels: { color: tc, font: { size: 10, family: 'DM Sans' }, boxWidth: 10, usePointStyle: true } }, tooltip: tip },
        scales: { x: { grid: { color: gc }, ticks: { color: tc, font: { size: 10 } } }, y: { grid: { color: gc }, ticks: { color: tc, font: { size: 10 } }, min: 30, max: 100, title: { display: true, text: 'Performance (%)', color: tc, font: { size: 10 } } } }
      }
    });
  }
}

// Post-End Term
let postEndDistChartInst = null, postEndSubjectChartInst = null;
function _buildPostEndtermCharts() {
  const testCanvas = document.getElementById('postEndChart');
  if (testCanvas && testCanvas.offsetWidth === 0) { setTimeout(_buildPostEndtermCharts, 30); return; }
  const { tc, gc, tip } = chartDefaults();
  const predData = window._postEndtermPredData || [5, 6, 8, 9, 8, 3, 1];
  const actualData = window._postEndtermActualData || [4, 5, 9, 9, 7, 4, 2];

  if (postEndDistChartInst) { postEndDistChartInst.destroy(); postEndDistChartInst = null }
  const c1 = document.getElementById('postEndChart'); if (c1) {
    postEndDistChartInst = new Chart(c1, {
      type: 'bar', data: {
        labels: DIST_LABELS_CHART,
        datasets: [
          { label: 'Actual', data: actualData, backgroundColor: 'rgba(74,144,226,0.85)', borderRadius: 5, borderSkipped: false },
          { label: 'Predicted', data: predData, backgroundColor: 'rgba(183,139,250,0.38)', borderRadius: 5, borderSkipped: false }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: true, labels: { color: tc, font: { size: 11, family: 'DM Sans' }, boxWidth: 12, usePointStyle: true } }, tooltip: tip },
        scales: {
          x: { grid: { color: gc }, ticks: { color: tc, font: { size: 11, family: 'DM Sans' } }, title: { display: true, text: 'Score Range', color: tc, font: { size: 11 } } },
          y: { grid: { color: gc }, ticks: { color: tc, font: { size: 11 } }, min: 0, title: { display: true, text: 'No. of Students', color: tc, font: { size: 11 } } }
        }
      }
    });
  }

  if (postEndSubjectChartInst) { postEndSubjectChartInst.destroy(); postEndSubjectChartInst = null }
  const c2 = document.getElementById('postEndSubjectChart'); if (c2) {
    postEndSubjectChartInst = new Chart(c2, {
      type: 'radar', data: {
        labels: ['Data Structures', 'Algorithms', 'OS', 'DBMS', 'Computer Networks'],
        datasets: [
          { label: 'Class Average', data: [68, 62, 55, 70, 58], borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.12)', borderWidth: 2, pointBackgroundColor: '#58a6ff', pointRadius: 4 },
          { label: 'Top Performers', data: [88, 85, 80, 90, 82], borderColor: '#3fb950', backgroundColor: 'rgba(63,185,80,0.08)', borderWidth: 2, pointBackgroundColor: '#3fb950', pointRadius: 4 }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: true, labels: { color: tc, font: { size: 10, family: 'DM Sans' }, boxWidth: 10, usePointStyle: true } }, tooltip: tip },
        scales: { r: { grid: { color: gc }, ticks: { color: tc, font: { size: 9 }, backdropColor: 'transparent' }, pointLabels: { color: tc, font: { size: 10 } }, min: 0, max: 100 } }
      }
    });
  }
}

// Master orchestrators (called from script.js)
function buildMidtermCharts() {
  const preSec = document.getElementById('anl-pre');
  if (preSec && preSec.classList.contains('active')) requestAnimationFrame(() => requestAnimationFrame(_buildPreMidtermCharts));
  else _buildPostMidtermCharts();
}
function buildEndtermCharts() {
  const preSec = document.getElementById('anl-pre-end');
  if (preSec && preSec.classList.contains('active')) requestAnimationFrame(() => requestAnimationFrame(_buildPreEndtermCharts));
  else setTimeout(_buildPostEndtermCharts, 30);
}
