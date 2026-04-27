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
// ── RISK SCATTER CHART (Dashboard) ──
let riskChartInst = null;
 
// Distinct palette — visually separable, works in both light & dark themes
const STUDENT_PALETTE = [
  '#4e79a7', '#f28e2b', '#e15759', '#76b7b2',
  '#59a14f', '#edc948', '#b07aa1', '#ff9da7',
  '#9c755f', '#bab0ac', '#17becf', '#bcbd22',
];
 
 
function buildRiskChart(detainmentRaw = {}) {
  if (riskChartInst) { riskChartInst.destroy(); riskChartInst = null; }
  const { tc, gc, tip } = chartDefaults();
 
  const entries = Object.entries(detainmentRaw);
 
  // One dataset per student — scatter only (showLine: false prevents the smear)
  const studentDatasets = entries.map(([student_id, d], i) => {
    const match = (typeof ALL_STUDENTS !== 'undefined' ? ALL_STUDENTS : [])
                    .find(s => s.id === student_id);
    const name  = match?.name ?? student_id;
    const color = STUDENT_PALETTE[i % STUDENT_PALETTE.length];
    const tier        = d.risk_tier ?? '';
    const borderColor = tier === 'HIGH'   ? 'rgba(255,80,80,1)'
                      : tier === 'MEDIUM' ? 'rgba(210,153,34,1)'
                      : color;
    const borderWidth = tier === 'HIGH' ? 2.5 : tier === 'MEDIUM' ? 2 : 1;
 
    return {
      label: name,
      data: [{ x: d.risk_score, y: d.attendance_pct, name, tier }],
      backgroundColor: color + 'cc',
      borderColor,
      borderWidth,
      showLine: false,          // critical: prevents Chart.js connecting points
      pointRadius: 10,
      pointHoverRadius: 14,
    };
  });
 
  // Threshold — pure line dataset, no points, not connected to scatter data
  const thresholdDataset = {
    label: '75% Detention Threshold',
    data: [{ x: 75, y: 0 }, { x: 75, y: 100 }],
    type: 'line',
    showLine: true,
    borderColor: 'rgba(210,153,34,0.85)',
    borderWidth: 2,
    borderDash: [6, 4],
    pointRadius: 0,
    pointHoverRadius: 0,
    fill: false,
    order: -1,                  // draw behind scatter points
  };
 
  riskChartInst = new Chart(document.getElementById('riskChart'), {
    type: 'scatter',
    data: { datasets: [...studentDatasets, thresholdDataset] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: {
          display: false,
          labels: {
            color: tc,
            font: { family: 'DM Sans', size: 11 },
            usePointStyle: true,
            pointStyleWidth: 10,
            // Filter out threshold line from legend if you prefer cleaner look:
            // filter: item => item.datasetIndex < studentDatasets.length,
          }
        },
        tooltip: {
          ...tip,
          // Only show tooltip for student point datasets, not the threshold line
          filter: item => item.datasetIndex < studentDatasets.length,
          callbacks: {
            title: ctx => ctx[0].raw.name,
            label: ctx => {
              const tier = ctx.raw.tier ? `  [${ctx.raw.tier}]` : '';
              return `Risk: ${ctx.raw.x}%  ·  Attendance: ${ctx.raw.y}%${tier}`;
            },
          }
        }
      },
      scales: {
        x: {
          grid: { color: gc },
          ticks: { color: tc, font: { size: 11, family: 'DM Sans' } },
          // suggestedMin/Max instead of hard min/max — points never get clipped
          suggestedMin: 0,
          suggestedMax: 100,
          title: { display: true, text: 'Risk of Detention (%)', color: tc, font: { size: 11 } }
        },
        y: {
          grid: { color: gc },
          ticks: { color: tc, font: { size: 11 } },
          suggestedMin: 0,
          suggestedMax: 100,
          title: { display: true, text: 'Overall Attendance (%)', color: tc, font: { size: 11 } }
        }
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
