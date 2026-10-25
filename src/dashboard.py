"""Flask web dashboard for migration progress tracking."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template_string

from .db import MigrationDB

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dropbox → Google Drive Migration</title>
<style>
:root {
  --bg: #0f172a; --card: #1e293b; --border: #334155;
  --text: #e2e8f0; --muted: #94a3b8; --accent: #3b82f6;
  --green: #22c55e; --red: #ef4444; --yellow: #eab308; --purple: #a855f7;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
.container { max-width: 1200px; margin: 0 auto; padding: 24px 16px; }
h1 { font-size: 1.5rem; margin-bottom: 8px; }
.subtitle { color: var(--muted); margin-bottom: 24px; font-size: 0.875rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
.card-label { font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
.card-value { font-size: 1.75rem; font-weight: 700; }
.card-sub { font-size: 0.75rem; color: var(--muted); margin-top: 4px; }
.green { color: var(--green); } .red { color: var(--red); } .yellow { color: var(--yellow); } .purple { color: var(--purple); } .accent { color: var(--accent); }

.donut-wrap { display: flex; justify-content: center; gap: 32px; margin-bottom: 24px; flex-wrap: wrap; }
.donut-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 24px; text-align: center; min-width: 220px; }
.donut-card h3 { font-size: 0.875rem; color: var(--muted); margin-bottom: 12px; }
.donut-svg { width: 160px; height: 160px; }
.donut-center { font-size: 1.25rem; font-weight: 700; }

.section { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 16px; }
.section h2 { font-size: 1rem; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; }
th { text-align: left; color: var(--muted); font-weight: 500; padding: 8px; border-bottom: 1px solid var(--border); }
td { padding: 8px; border-bottom: 1px solid var(--border); }
.badge { display: inline-block; padding: 2px 8px; border-radius: 9999px; font-size: 0.6875rem; font-weight: 600; }
.badge-transferred { background: #22c55e22; color: var(--green); }
.badge-pending { background: #3b82f622; color: var(--accent); }
.badge-failed { background: #ef444422; color: var(--red); }
.badge-skipped { background: #eab30822; color: var(--yellow); }
.badge-running { background: #a855f722; color: var(--purple); }

.progress-bar { height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; margin-top: 8px; }
.progress-fill { height: 100%; background: var(--green); border-radius: 4px; transition: width 0.5s; }

.log-entry { font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 0.75rem; padding: 4px 0; border-bottom: 1px solid var(--border); }
.log-time { color: var(--muted); margin-right: 8px; }
.log-error { color: var(--red); }
.log-info { color: var(--green); }

.refresh-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.btn { background: var(--accent); color: white; border: none; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 0.8125rem; }
.btn:hover { opacity: 0.9; }
.auto-tag { font-size: 0.75rem; color: var(--muted); }
</style>
</head>
<body>
<div class="container">
  <div class="refresh-bar">
    <div>
      <h1>Dropbox → Google Drive Migration</h1>
      <p class="subtitle">Real-time migration dashboard</p>
    </div>
    <div>
      <span class="auto-tag" id="autoRefresh">Auto-refresh: 5s</span>
      <button class="btn" onclick="refresh()">Refresh Now</button>
    </div>
  </div>

  <div class="grid" id="statsGrid"></div>
  <div class="donut-wrap" id="donutWrap"></div>

  <div class="section">
    <h2>Overall Progress</h2>
    <div id="progressInfo"></div>
    <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
  </div>

  <div class="section">
    <h2>Recent Runs</h2>
    <table><thead><tr><th>Run</th><th>Started</th><th>Status</th><th>Transferred</th><th>Skipped</th><th>Failed</th><th>Data</th></tr></thead>
    <tbody id="runsBody"></tbody></table>
  </div>

  <div class="section">
    <h2>Failed Files</h2>
    <table><thead><tr><th>Path</th><th>Size</th><th>Error</th></tr></thead>
    <tbody id="failedBody"></tbody></table>
  </div>

  <div class="section">
    <h2>Recent Logs</h2>
    <div id="logsWrap" style="max-height:300px;overflow-y:auto"></div>
  </div>
</div>

<script>
function fmt(b) {
  if (!b) return '0 B';
  const u = ['B','KB','MB','GB','TB'];
  const i = Math.floor(Math.log(b)/Math.log(1024));
  return (b/Math.pow(1024,i)).toFixed(i?1:0)+' '+u[i];
}
function fmtTime(s) {
  if (!s) return '-';
  return new Date(s).toLocaleString();
}
function badge(status) {
  return `<span class="badge badge-${status}">${status}</span>`;
}

function makeDonut(id, pct, label, color) {
  const r=54, c=2*Math.PI*r, off=c-(pct/100)*c;
  return `<div class="donut-card"><h3>${label}</h3>
    <svg class="donut-svg" viewBox="0 0 120 120">
      <circle cx="60" cy="60" r="${r}" fill="none" stroke="var(--border)" stroke-width="10"/>
      <circle cx="60" cy="60" r="${r}" fill="none" stroke="${color}" stroke-width="10"
        stroke-dasharray="${c}" stroke-dashoffset="${off}" stroke-linecap="round"
        transform="rotate(-90 60 60)" style="transition:stroke-dashoffset 0.5s"/>
      <text x="60" y="60" text-anchor="middle" dominant-baseline="central"
        fill="var(--text)" class="donut-center">${pct.toFixed(1)}%</text>
    </svg></div>`;
}

async function refresh() {
  try {
    const r = await fetch('/api/stats');
    const d = await r.json();
    const s = d.stats;
    const total = s.total || 0;
    const transferred = s.transferred || 0;
    const pending = s.pending || 0;
    const failed = s.failed || 0;
    const skipped = s.skipped || 0;

    document.getElementById('statsGrid').innerHTML = `
      <div class="card"><div class="card-label">Total Files</div><div class="card-value accent">${total.toLocaleString()}</div><div class="card-sub">${fmt(s.total_bytes)}</div></div>
      <div class="card"><div class="card-label">Transferred</div><div class="card-value green">${transferred.toLocaleString()}</div><div class="card-sub">${fmt(s.transferred_bytes)}</div></div>
      <div class="card"><div class="card-label">Pending</div><div class="card-value yellow">${pending.toLocaleString()}</div><div class="card-sub">remaining</div></div>
      <div class="card"><div class="card-label">Failed</div><div class="card-value red">${failed.toLocaleString()}</div></div>
      <div class="card"><div class="card-label">Skipped</div><div class="card-value purple">${skipped.toLocaleString()}</div></div>
    `;

    const pctDone = total ? (transferred/total)*100 : 0;
    const pctFail = total ? (failed/total)*100 : 0;
    document.getElementById('donutWrap').innerHTML =
      makeDonut('d1', pctDone, 'Migration Progress', 'var(--green)') +
      makeDonut('d2', total ? ((transferred+skipped)/total)*100 : 0, 'Processed', 'var(--accent)') +
      makeDonut('d3', pctFail, 'Error Rate', 'var(--red)');

    document.getElementById('progressFill').style.width = pctDone+'%';
    document.getElementById('progressInfo').innerHTML = `<span>${transferred.toLocaleString()} / ${total.toLocaleString()} files (${pctDone.toFixed(1)}%) — ${fmt(s.transferred_bytes)} transferred</span>`;

    // Runs
    const runs = d.runs || [];
    document.getElementById('runsBody').innerHTML = runs.map(r => `<tr>
      <td>#${r.id}</td><td>${fmtTime(r.started_at)}</td><td>${badge(r.status)}</td>
      <td class="green">${r.transferred||0}</td><td class="yellow">${r.skipped||0}</td><td class="red">${r.failed||0}</td>
      <td>${fmt(r.bytes_transferred)}</td></tr>`).join('');

    // Failed
    const fails = d.failed || [];
    document.getElementById('failedBody').innerHTML = fails.length ? fails.map(f => `<tr>
      <td style="word-break:break-all">${f.dropbox_path}</td><td>${fmt(f.size_bytes)}</td>
      <td class="red">${f.error_message||''}</td></tr>`).join('') : '<tr><td colspan="3" style="color:var(--muted)">No failed files</td></tr>';

    // Logs
    const logs = d.logs || [];
    document.getElementById('logsWrap').innerHTML = logs.map(l => `<div class="log-entry">
      <span class="log-time">${fmtTime(l.timestamp)}</span>
      <span class="log-${l.level.toLowerCase()}">[${l.level}]</span> ${l.message}
    </div>`).join('');
  } catch(e) { console.error('Refresh failed', e); }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def create_app(db: MigrationDB) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/api/stats")
    def api_stats():
        stats = db.get_stats()
        runs = db.get_runs(limit=10)
        failed = db.get_failed_files()
        logs = db.get_recent_logs(limit=50)
        return jsonify({"stats": stats, "runs": runs, "failed": failed, "logs": logs})

    return app


def run_dashboard(db: MigrationDB, port: int = 5050) -> threading.Thread:
    app = create_app(db)
    t = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False), daemon=True)
    t.start()
    return t
