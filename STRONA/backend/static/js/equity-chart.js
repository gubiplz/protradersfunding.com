/* Account equity chart — shared by the trader portal and the admin panel.
 *
 * The backend (`_equity_curve` in main.py) returns points
 * {i, ts, equity, balance, kind, symbol?, side?, lots?, pnl?, payout?}, where
 * `kind` is start | trade | payout | open | tick and `i` is the TRADE NUMBER —
 * and that is the horizontal axis. A payout is not a trade, so it keeps the
 * same `i` as the previous point and draws as a vertical step down.
 *
 * An account with no trades at all gets time-based samples from the backend
 * (kind='tick') — then the axis is labelled with risk-engine reads, not trades.
 *
 * Colors are read from the CSS tokens at call time, so the chart follows the
 * light/dark theme; callers re-render on a theme switch instead of restyling.
 *
 * equityChartConfig(curve, opts):
 *   opts.lines — [{y, label, color}] horizontal dashed objective lines with a
 *                label badge at the left edge (target / floors / account size).
 */
window.chartTheme = function () {
  var s = getComputedStyle(document.documentElement);
  var t = function (name, fallback) { return (s.getPropertyValue(name) || fallback).trim(); };
  return {
    acc: t('--acc', '#6366f1'),
    txt: t('--txt', '#0f172a'),
    muted: t('--muted', '#64748b'),
    dim: t('--dim', '#94a3b8'),
    line: t('--line', '#e7eaf3'),
    panel: t('--panel', '#ffffff'),
    green: t('--green2', '#10b981'),
    red: t('--red', '#dc2626'),
    gold: t('--gold', '#d97706'),
    dark: document.documentElement.dataset.theme === 'dark',
  };
};

/* Dashed horizontal objective lines + left-edge label badges (FTMO-style). */
var goalLinesPlugin = {
  id: 'goalLines',
  afterDatasetsDraw: function (chart) {
    var lines = (chart.options.plugins.goalLines || {}).lines || [];
    if (!lines.length) return;
    var ctx = chart.ctx, area = chart.chartArea, scale = chart.scales.y;
    var th = window.chartTheme();
    ctx.save();
    lines.forEach(function (ln) {
      var y = scale.getPixelForValue(ln.y);
      if (y < area.top - 1 || y > area.bottom + 1) return;
      ctx.strokeStyle = ln.color;
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(area.left, y);
      ctx.lineTo(area.right, y);
      ctx.stroke();
      ctx.setLineDash([]);
      var label = ln.label + ': $' + Number(ln.y).toLocaleString('en-US', { maximumFractionDigits: 0 });
      ctx.font = '600 10px "JetBrains Mono", monospace';
      var w = ctx.measureText(label).width + 12, h = 16;
      var by = Math.min(Math.max(y - h / 2, area.top), area.bottom - h);
      ctx.fillStyle = ln.color;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(area.left + 6, by, w, h, 4);
      else ctx.rect(area.left + 6, by, w, h);
      ctx.fill();
      ctx.fillStyle = th.dark ? '#0b0d12' : '#ffffff';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, area.left + 12, by + h / 2 + .5);
    });
    ctx.restore();
  },
};
if (window.Chart) Chart.register(goalLinesPlugin);

window.equityChartConfig = function (curve, opts) {
  opts = opts || {};
  var th = window.chartTheme();
  var byTrades = curve.some(function (p) { return p.kind === 'trade'; });
  var money = function (v) {
    return '$' + Number(v).toLocaleString('en-US',
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  var dot = function (p) {
    return p.kind === 'payout' ? 4 : p.kind === 'open' ? 4 : 0;
  };
  var dotColor = function (p) {
    return p.kind === 'payout' ? th.gold : p.kind === 'open' ? th.green : th.acc;
  };
  var accFill = function (context) {
    var area = context.chart.chartArea;
    if (!area) return 'rgba(99,102,241,.08)';
    var g = context.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
    // Hex accent -> translucent gradient fill under the equity line.
    var hex = th.acc.replace('#', '');
    var r = parseInt(hex.slice(0, 2), 16), gc = parseInt(hex.slice(2, 4), 16), b = parseInt(hex.slice(4, 6), 16);
    g.addColorStop(0, 'rgba(' + r + ',' + gc + ',' + b + ',' + (th.dark ? '.22' : '.12') + ')');
    g.addColorStop(1, 'rgba(' + r + ',' + gc + ',' + b + ',0)');
    return g;
  };

  var datasets = [{
    label: 'Equity',
    data: curve.map(function (p) { return p.equity; }),
    borderColor: th.acc,
    backgroundColor: accFill,
    fill: true,
    tension: .15,
    borderWidth: 2,
    pointRadius: curve.map(dot),
    pointHoverRadius: 5,
    pointBackgroundColor: curve.map(dotColor),
    pointBorderColor: th.panel,
    pointBorderWidth: 1.5,
    order: 1,
  }];
  // A separate Balance series only earns its ink when it diverges from equity
  // (snapshot mode / floating P&L) — on closed-trade curves they are identical.
  if (curve.some(function (p) { return p.balance != null && p.balance !== p.equity; })) {
    datasets.push({
      label: 'Balance',
      data: curve.map(function (p) { return p.balance; }),
      borderColor: th.dim,
      borderWidth: 1.2,
      pointRadius: 0,
      fill: false,
      tension: .15,
      order: 2,
    });
  }

  // Objective lines must be inside the scale range even when price never
  // approached them — pad the axis to the outermost line.
  var lineVals = (opts.lines || []).map(function (l) { return l.y; });
  var yExtra = {};
  if (lineVals.length) {
    yExtra.suggestedMin = Math.min.apply(null, lineVals) * .999;
    yExtra.suggestedMax = Math.max.apply(null, lineVals) * 1.001;
  }

  return {
    type: 'line',
    data: {
      labels: curve.map(function (p) { return p.i; }),
      datasets: datasets,
    },
    options: {
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        goalLines: { lines: opts.lines || [] },
        tooltip: {
          backgroundColor: th.dark ? 'rgba(23,27,36,.96)' : 'rgba(15,23,42,.92)',
          titleColor: '#e8ecf5',
          bodyColor: '#c6cddc',
          borderColor: th.line,
          borderWidth: th.dark ? 1 : 0,
          callbacks: {
            title: function (items) {
              var p = curve[items[0].dataIndex];
              if (p.kind === 'start') return 'Starting balance';
              if (p.kind === 'payout') return 'Payout';
              if (p.kind === 'open') return 'Open position';
              if (p.kind === 'tick') return new Date(p.ts).toLocaleString();
              return 'Trade #' + p.i;
            },
            label: function (item) {
              if (item.datasetIndex !== 0) return null;
              var p = curve[item.dataIndex];
              var out = [];
              if (p.symbol) {
                out.push(p.symbol + ' ' + String(p.side || '').toUpperCase()
                  + (p.lots ? ' · ' + p.lots + ' lots' : ''));
              }
              if (p.kind === 'payout') out.push('Withdrawn: -' + money(p.payout));
              else if (p.pnl != null) out.push('P&L: ' + (p.pnl >= 0 ? '+' : '-') + money(Math.abs(p.pnl)));
              out.push((p.kind === 'open' ? 'Equity: ' : 'Balance: ') + money(p.equity));
              return out;
            },
          },
        },
      },
      scales: {
        x: {
          display: true,
          title: {
            display: true,
            text: byTrades ? 'Trades' : 'Risk-engine readings',
            color: th.dim,
            font: { size: 11 },
          },
          ticks: { color: th.dim, font: { size: 10 }, autoSkip: true, maxTicksLimit: 10 },
          grid: { display: false },
        },
        y: Object.assign({
          ticks: {
            color: th.dim,
            font: { size: 10 },
            callback: function (v) { return '$' + (v / 1000).toFixed(1) + 'k'; },
          },
          grid: { color: th.line },
        }, yExtra),
      },
    },
  };
};
