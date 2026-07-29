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
 */
window.equityChartConfig = function (curve) {
  var byTrades = curve.some(function (p) { return p.kind === 'trade'; });
  var money = function (v) {
    return '$' + Number(v).toLocaleString('en-US',
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  var dot = function (p) {
    return p.kind === 'payout' ? 4 : p.kind === 'open' ? 4 : 0;
  };
  var dotColor = function (p) {
    return p.kind === 'payout' ? '#f59e0b' : p.kind === 'open' ? '#10b981' : '#6366f1';
  };

  return {
    type: 'line',
    data: {
      labels: curve.map(function (p) { return p.i; }),
      datasets: [{
        label: 'Equity',
        data: curve.map(function (p) { return p.equity; }),
        borderColor: '#6366f1',
        backgroundColor: 'rgba(99,102,241,.07)',
        fill: true,
        tension: .15,
        borderWidth: 2,
        pointRadius: curve.map(dot),
        pointHoverRadius: 5,
        pointBackgroundColor: curve.map(dotColor),
        pointBorderColor: '#fff',
        pointBorderWidth: 1.5,
      }],
    },
    options: {
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
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
            color: '#94a3b8',
            font: { size: 11 },
          },
          ticks: { color: '#94a3b8', font: { size: 10 }, autoSkip: true, maxTicksLimit: 10 },
          grid: { display: false },
        },
        y: {
          ticks: {
            color: '#94a3b8',
            font: { size: 10 },
            callback: function (v) { return '$' + (v / 1000).toFixed(1) + 'k'; },
          },
          grid: { color: '#eef0f6' },
        },
      },
    },
  };
};
