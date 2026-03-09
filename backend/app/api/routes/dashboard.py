from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["dashboard"])


@router.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> str:
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Nof1 Trading Analytics</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg: #0b1020;
      --card: #131a2e;
      --card2: #10162a;
      --text: #e5ecff;
      --muted: #9eb0d0;
      --accent: #6ea8fe;
      --green: #3ddc97;
      --red: #ff6b6b;
    }
    * { box-sizing: border-box; font-family: Inter, Arial, sans-serif; }
    body { margin: 0; background: linear-gradient(160deg, #0b1020, #080d1a); color: var(--text); }
    .wrap { max-width: 1200px; margin: 0 auto; padding: 20px; }
    h1 { margin: 0 0 8px 0; font-size: 28px; }
    p.sub { margin: 0 0 18px 0; color: var(--muted); }
    .kpi-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }
    .card { background: rgba(19, 26, 46, 0.9); border: 1px solid #273250; border-radius: 14px; padding: 14px; }
    .kpi-title { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
    .kpi-value { margin-top: 8px; font-size: 24px; font-weight: 700; }
    .panel-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; margin-bottom: 12px; }
    .panel-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .table { width: 100%; border-collapse: collapse; font-size: 14px; }
    .table th, .table td { padding: 10px; border-bottom: 1px solid #273250; text-align: left; }
    .table th { color: var(--muted); font-weight: 600; }
    .pill { display: inline-block; padding: 4px 10px; border-radius: 999px; background: #1e2740; color: #b8ccff; font-size: 12px; }
    .green { color: var(--green); }
    .red { color: var(--red); }
    .badge { display: inline-block; padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: 600; }
    .badge-gold { background: #2a2310; color: #ffd700; }
    .badge-silver { background: #1e2030; color: #c0c0c0; }
    .badge-bronze { background: #2a1a10; color: #cd7f32; }
    .progress-bar { background: #1e2740; border-radius: 8px; height: 8px; overflow: hidden; margin-top: 6px; }
    .progress-fill { height: 100%; background: var(--accent); border-radius: 8px; transition: width .3s; }
    .section-title { color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 10px; }
    @media (max-width: 1000px) {
      .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .panel-grid, .panel-grid-2 { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Nof1 Trading Analytics</h1>
    <p class="sub">Live dashboard powered by <code>/admin/analytics</code></p>

    <div class="kpi-grid">
      <div class="card"><div class="kpi-title">Trades</div><div id="kpi-trades" class="kpi-value">-</div></div>
      <div class="card"><div class="kpi-title">Forecasts</div><div id="kpi-forecasts" class="kpi-value">-</div></div>
      <div class="card"><div class="kpi-title">Markets</div><div id="kpi-markets" class="kpi-value">-</div></div>
      <div class="card"><div class="kpi-title">Volume (USD)</div><div id="kpi-volume" class="kpi-value">-</div></div>
      <div class="card"><div class="kpi-title">MTM PnL (USD)</div><div id="kpi-pnl" class="kpi-value">-</div></div>
    </div>

    <div class="panel-grid">
      <div class="card">
        <div class="kpi-title">Daily Volume</div>
        <canvas id="dailyVolumeChart" height="120"></canvas>
      </div>
      <div class="card">
        <div class="kpi-title">Trade Side Mix</div>
        <canvas id="sideMixChart" height="120"></canvas>
      </div>
    </div>

    <div class="card" style="margin-bottom: 12px;">
      <div class="section-title">Tournament Leaderboard</div>
      <div id="tournament-info" style="margin-bottom: 8px; font-size: 13px; color: var(--muted);"></div>
      <div class="progress-bar" style="margin-bottom: 12px;"><div id="tournament-progress" class="progress-fill" style="width:0%"></div></div>
      <table class="table" id="leaderboard-table">
        <thead><tr><th>#</th><th>Model</th><th>Balance</th><th>Return %</th><th>Score</th><th>Volume</th><th>Trades</th><th>Status</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>

    <div class="panel-grid-2">
      <div class="card">
        <div class="kpi-title">Top Markets By Volume</div>
        <table class="table" id="markets-table">
          <thead><tr><th>Market</th><th>Volume USD</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
      <div class="card">
        <div class="kpi-title">Model Analytics</div>
        <table class="table" id="models-table">
          <thead><tr><th>Model</th><th>Trades</th><th>Volume USD</th><th>MTM PnL</th><th>Forecasts</th><th>Avg Conf</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </div>

  <script>
    let dailyChart;
    let sideChart;

    function formatUsd(n) {
      return "$" + Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
    }

    async function loadAnalytics() {
      const res = await fetch("/admin/analytics");
      const data = await res.json();
      const ov = data.overview || {};

      document.getElementById("kpi-trades").textContent = ov.total_trades ?? 0;
      document.getElementById("kpi-forecasts").textContent = ov.total_forecasts ?? 0;
      document.getElementById("kpi-markets").textContent = ov.total_markets ?? 0;
      document.getElementById("kpi-volume").textContent = formatUsd(ov.total_volume_usd);
      const pnlEl = document.getElementById("kpi-pnl");
      pnlEl.textContent = formatUsd(ov.mark_to_market_pnl_usd);
      pnlEl.className = "kpi-value " + ((ov.mark_to_market_pnl_usd || 0) >= 0 ? "green" : "red");

      const daily = data.daily_volume || [];
      const dLabels = daily.map(x => x.date);
      const dValues = daily.map(x => x.volume_usd);

      if (dailyChart) dailyChart.destroy();
      dailyChart = new Chart(document.getElementById("dailyVolumeChart"), {
        type: "line",
        data: {
          labels: dLabels,
          datasets: [{ label: "Daily Volume", data: dValues, borderColor: "#6ea8fe", backgroundColor: "rgba(110,168,254,.25)", fill: true, tension: .3 }]
        },
        options: { plugins: { legend: { labels: { color: "#e5ecff" } } }, scales: { x: { ticks: { color: "#9eb0d0" } }, y: { ticks: { color: "#9eb0d0" } } } }
      });

      if (sideChart) sideChart.destroy();
      sideChart = new Chart(document.getElementById("sideMixChart"), {
        type: "doughnut",
        data: {
          labels: ["YES", "NO"],
          datasets: [{ data: [ov.yes_trades || 0, ov.no_trades || 0], backgroundColor: ["#3ddc97", "#ff6b6b"] }]
        },
        options: { plugins: { legend: { labels: { color: "#e5ecff" } } } }
      });

      const marketsBody = document.querySelector("#markets-table tbody");
      marketsBody.innerHTML = "";
      (data.top_markets_by_volume || []).forEach(row => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${row.market}</td><td>${formatUsd(row.volume_usd)}</td>`;
        marketsBody.appendChild(tr);
      });

      const modelsBody = document.querySelector("#models-table tbody");
      modelsBody.innerHTML = "";
      (data.models || []).forEach(row => {
        const tr = document.createElement("tr");
        const pnlClass = (row.mark_to_market_pnl_usd || 0) >= 0 ? "green" : "red";
        tr.innerHTML = `
          <td><span class="pill">${row.model_name}</span></td>
          <td>${row.trade_count || 0}</td>
          <td>${formatUsd(row.volume_usd)}</td>
          <td class="${pnlClass}">${formatUsd(row.mark_to_market_pnl_usd)}</td>
          <td>${row.forecast_count || 0}</td>
          <td>${((row.avg_confidence || 0) * 100).toFixed(1)}%</td>
        `;
        modelsBody.appendChild(tr);
      });
    }

    async function loadLeaderboard() {
      try {
        const res = await fetch("/admin/leaderboard");
        const data = await res.json();
        const t = data.tournament;
        const infoEl = document.getElementById("tournament-info");
        const progEl = document.getElementById("tournament-progress");

        if (!t) {
          infoEl.textContent = "No active tournament. Enable GAME_LOOP_ENABLED=true to start.";
          return;
        }
        const start = new Date(t.started_at).toLocaleDateString();
        const end = new Date(t.ends_at).toLocaleDateString();
        infoEl.innerHTML = `<strong>${t.name}</strong> &middot; ${start} &rarr; ${end} &middot; ${t.status.toUpperCase()} &middot; ${t.progress_pct}% complete &middot; $${t.start_budget_usd}/model`;
        progEl.style.width = t.progress_pct + "%";

        const tbody = document.querySelector("#leaderboard-table tbody");
        tbody.innerHTML = "";
        (data.entries || []).forEach((row, idx) => {
          const tr = document.createElement("tr");
          let badge = "";
          if (idx === 0) badge = '<span class="badge badge-gold">1st</span>';
          else if (idx === 1) badge = '<span class="badge badge-silver">2nd</span>';
          else if (idx === 2) badge = '<span class="badge badge-bronze">3rd</span>';
          else badge = `${idx + 1}`;
          const retClass = row.total_return_pct >= 0 ? "green" : "red";
          const scoreClass = (row.composite_score || 0) >= 0 ? "green" : "red";
          const statusLabel = row.eliminated ? '<span class="badge" style="background:#2a1a10;color:#ff6b6b">ELIMINATED</span>' : '<span class="badge" style="background:#1a2a10;color:#3ddc97">ACTIVE</span>';
          if (row.eliminated) badge = '<span style="color:#ff6b6b">EL</span>';
          tr.innerHTML = `
            <td>${badge}</td>
            <td><span class="pill">${row.model_name}</span></td>
            <td>${formatUsd(row.current_balance_usd)}</td>
            <td class="${retClass}">${row.total_return_pct >= 0 ? "+" : ""}${row.total_return_pct}%</td>
            <td class="${scoreClass}">${(row.composite_score || 0).toFixed(3)}</td>
            <td>${formatUsd(row.total_volume_usd || 0)}</td>
            <td>${row.total_trades}</td>
            <td>${statusLabel}</td>
          `;
          tbody.appendChild(tr);
        });
      } catch(e) { /* leaderboard endpoint may not be ready yet */ }
    }

    loadAnalytics();
    loadLeaderboard();
    setInterval(loadAnalytics, 15000);
    setInterval(loadLeaderboard, 15000);
  </script>
</body>
</html>
"""
