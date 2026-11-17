import React from 'react';
import type { RiskTelemetry, TrustEntry } from '../types';

interface Props {
  risk: RiskTelemetry | undefined;
  ledger: TrustEntry[] | undefined;
  /** chama POST /api/v1/risk/reset_breaker com a confirmação digitada */
  onResetBreaker: (confirm: string) => Promise<void>;
}

const fmt = (n: number | null | undefined, d = 2) => (n === null || n === undefined ? '—' : n.toLocaleString('pt-BR', { minimumFractionDigits: d, maximumFractionDigits: d }));

export const RiskControlPanel: React.FC<Props> = ({ risk, ledger, onResetBreaker }) => {
  if (!risk) return <div className="bg-gt-panel border border-gt-surface rounded-xl p-4 text-xs text-gt-muted">Aguardando telemetria de risco…</div>;

  const ddPct = Math.min(100, (risk.drawdown_pct / Math.max(risk.max_daily_drawdown_pct, 0.01)) * 100);
  const tripped = risk.breaker.tripped;
  const paperEquity = risk.equity_source === 'PAPER';

  const reset = async () => {
    const typed = window.prompt('Reset MANUAL do circuit breaker.\nAs posições já foram fechadas; o funil NÃO é religado.\nDigite CONFIRMAR para continuar:');
    if (typed === null) return;
    await onResetBreaker(typed.trim());
  };

  return (
    <div className="bg-gt-panel border border-gt-surface rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-bold uppercase tracking-wider text-slate-200">Risco Institucional</span>
        <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${paperEquity ? 'bg-amber-500/20 text-amber-300 border-amber-500/40' : 'bg-gt-accent/15 text-gt-accent border-gt-accent/40'}`} title={risk.equity_label}>
          {paperEquity ? 'EQUITY PAPER' : risk.equity_source.replace('_', ' ')}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 text-[11px] font-mono mb-3">
        <div><div className="text-gt-muted text-[10px] uppercase">Equity</div><div className="text-slate-100 text-sm">{risk.equity === null ? 'indefinido' : `$${fmt(risk.equity)}`}</div></div>
        <div><div className="text-gt-muted text-[10px] uppercase">PnL aberto / hoje</div>
          <div className={risk.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}>{fmt(risk.unrealized_pnl)} / {fmt(risk.realized_today)}</div></div>
      </div>
      {risk.equity === null && <p className="text-[11px] text-amber-300 mb-2">{risk.equity_label}</p>}

      <div className="mb-3">
        <div className="flex justify-between text-[10px] font-mono text-gt-muted mb-1">
          <span>Drawdown do dia ({risk.day})</span><span>{fmt(risk.drawdown_pct)}% / {fmt(risk.max_daily_drawdown_pct, 1)}%</span>
        </div>
        <div className="h-2 bg-gt-input rounded-full overflow-hidden">
          <div style={{ width: `${ddPct}%` }} className={`h-full transition-all ${ddPct >= 80 ? 'bg-rose-500' : ddPct >= 50 ? 'bg-amber-400' : 'bg-gt-accent'}`} />
        </div>
      </div>

      <div className={`rounded-lg border px-3 py-2 mb-3 text-xs ${tripped ? 'bg-rose-500/15 border-rose-500/60' : 'bg-gt-input border-gt-surface'}`}>
        <div className="flex items-center justify-between">
          <span className={`font-mono font-bold ${tripped ? 'text-rose-300' : 'text-emerald-400'}`}>{tripped ? 'CIRCUIT BREAKER ATIVO' : 'Breaker armado'}</span>
          {tripped && <button onClick={reset} className="px-2 py-1 rounded bg-rose-600 hover:bg-rose-500 text-white text-[11px] font-mono">Resetar…</button>}
        </div>
        {tripped && <div className="text-[11px] text-rose-200 mt-1">{risk.breaker.reason} • reset automático 00:00 BRT</div>}
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] font-mono mb-3">
        <span className="text-gt-muted">Win rate (rolling)</span>
        <span>{fmt(risk.win_rate_rolling * 100, 1)}% <span className="text-gt-muted">({risk.stats_mode === 'NEUTRO' ? `neutro, ${risk.trades_in_window}/${risk.min_trades_for_kelly} trades` : `${risk.trades_in_window} trades`})</span></span>
        <span className="text-gt-muted">Payoff médio</span><span>{fmt(risk.payoff_ratio)}</span>
        <span className="text-gt-muted">Kelly pleno f*</span><span>{fmt(risk.kelly_full * 100)}%</span>
        <span className="text-gt-muted">Kelly aplicado ({fmt(risk.kelly_fraction * 100, 0)}% de f*, teto)</span><span>{fmt(risk.kelly_applied * 100, 3)}%</span>
      </div>
      {risk.stats_mode === 'NEUTRO' && <p className="text-[10px] text-gt-muted mb-3">Sem histórico real suficiente: entradas autônomas usam risco mínimo (BOOTSTRAP). Dados simulados não entram na estatística.</p>}

      <div className="text-[10px] uppercase text-gt-muted mb-1">Posições sob gestão ({risk.positions.length})</div>
      <div className="space-y-1 max-h-40 overflow-y-auto font-mono text-[11px] mb-3">
        {risk.positions.length === 0 && <div className="text-gt-muted">nenhuma</div>}
        {risk.positions.map((p) => (
          <div key={p.id} className="bg-gt-bg border border-gt-surface rounded px-2 py-1" title={`entrada ${p.entry} • SL inicial ${p.initial_sl}`}>
            <div className="flex justify-between">
              <span><span className={p.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}>{p.side}</span> {p.symbol} {p.qty} <span className="text-gt-muted">{p.leverage}x{p.paper ? ' PAPER' : ''}</span></span>
              <span className={p.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}>{fmt(p.unrealized_pnl)}</span>
            </div>
            <div className="text-[10px] text-gt-muted">SL {fmt(p.stop_loss)} ({p.stop_kind}{p.breakeven_done ? ' • breakeven ok' : ''}) • {fmt(p.move_pct, 2)}%</div>
          </div>
        ))}
      </div>

      <div className="text-[10px] uppercase text-gt-muted mb-1">Portão de confiança (últimas decisões)</div>
      <div className="space-y-1 max-h-36 overflow-y-auto font-mono text-[10px]">
        {(ledger || []).length === 0 && <div className="text-gt-muted">sem decisões ainda</div>}
        {(ledger || []).map((e, i) => (
          <div key={`${e.ts}-${i}`} className="flex justify-between gap-2" title={e.reasons.join('; ')}>
            <span className={e.allowed ? 'text-emerald-400' : 'text-rose-400'}>{e.allowed ? 'OK ' : e.code}</span>
            <span className="flex-1 truncate text-slate-300">{e.origin} {e.side} {e.symbol} [{e.venue}]</span>
            <span className="text-gt-muted">{e.paper ? 'paper' : 'live'}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

export default RiskControlPanel;
