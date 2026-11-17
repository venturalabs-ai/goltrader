import React, { useMemo, useState } from 'react';
import type { EconomicCalendar, GexRow, MarketNews, OptionContract, OptionsAnalytics } from '../types';

export interface ExecLeg { symbol: string; venue: 'BYBIT_OPT'; side: 'BUY' | 'SELL'; amount: number; price: number }
interface Leg { key: string; c: OptionContract; side: 'BUY' | 'SELL'; qty: number; price: number }

interface Props {
  analytics: OptionsAnalytics | null;
  news: MarketNews | null;
  calendar: EconomicCalendar | null;
  analyzerMode: boolean | undefined;
  /** envia a cesta pelo endpoint autenticado/guardado; devolve mensagem de resultado */
  onExecute: (legs: ExecLeg[]) => Promise<string>;
}

const fmt = (n: number | null | undefined, d = 2) => (n === null || n === undefined || Number.isNaN(n) ? '—' : n.toLocaleString('pt-BR', { maximumFractionDigits: d, minimumFractionDigits: d }));
const legPrice = (c: OptionContract, side: 'BUY' | 'SELL') => (side === 'BUY' ? c.ask : c.bid) || c.mark;
const panel = 'bg-gt-panel border border-gt-surface rounded-xl p-4';

export const SourceBadge: React.FC<{ source?: string; example?: boolean }> = ({ source, example }) => {
  if (example) return <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 text-[10px] font-mono">dados de exemplo</span>;
  if (source === 'SIMULADO') return <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 text-[10px] font-mono">SIMULADO</span>;
  return <span className="px-1.5 py-0.5 rounded bg-gt-accent/15 text-gt-accent border border-gt-accent/40 text-[10px] font-mono">{source || '—'}</span>;
};

// ---------------------------------------------------------------- GEX por strike
const GexBars: React.FC<{ rows: GexRow[]; spot: number; flip: number | null; maxPain: number | null }> = ({ rows, spot, flip, maxPain }) => {
  const view = useMemo(() => {
    const sorted = [...rows].sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot)).slice(0, 24);
    return sorted.sort((a, b) => b.strike - a.strike);
  }, [rows, spot]);
  const max = Math.max(1, ...view.map((r) => Math.abs(r.net_gex)));
  return (
    <div className="space-y-0.5 max-h-80 overflow-y-auto pr-1">
      {view.map((r) => {
        const w = (Math.abs(r.net_gex) / max) * 50;
        const pos = r.net_gex >= 0;
        const near = Math.abs(r.strike - spot) === Math.min(...view.map((v) => Math.abs(v.strike - spot)));
        return (
          <div key={r.strike} className="flex items-center gap-2 text-[11px] font-mono" title={`call ${fmt(r.call_gex / 1e6)}M • put ${fmt(r.put_gex / 1e6)}M`}>
            <span className={`w-16 text-right ${near ? 'text-gt-accent font-bold' : 'text-slate-400'}`}>{r.strike.toLocaleString('pt-BR')}</span>
            <div className="relative flex-1 h-3.5 bg-gt-input rounded">
              <div className="absolute left-1/2 top-0 bottom-0 w-px bg-gt-muted/60" />
              <div className={`absolute top-0 bottom-0 ${pos ? 'bg-emerald-500/70' : 'bg-rose-500/70'}`} style={pos ? { left: '50%', width: `${w}%` } : { right: '50%', width: `${w}%` }} />
            </div>
            <span className={`w-16 ${pos ? 'text-emerald-400' : 'text-rose-400'}`}>{fmt(r.net_gex / 1e6, 2)}M</span>
            <span className="w-14 text-[10px] text-gt-muted">
              {maxPain === r.strike ? 'MaxPain' : ''}
              {flip !== null && Math.abs(r.strike - flip) < (view.length > 1 ? Math.abs(view[0].strike - view[1].strike) / 2 : 1) ? ' Flip' : ''}
            </span>
          </div>
        );
      })}
    </div>
  );
};

// ---------------------------------------------------------------- IV smile
const IvSmile: React.FC<{ pts: { strike: number; iv_pct: number }[]; spot: number; rv: number | null }> = ({ pts, spot, rv }) => {
  if (pts.length < 2) return <div className="text-xs text-gt-muted">Sem dados suficientes para o smile.</div>;
  const W = 420, H = 180, P = 28;
  const xs = pts.map((p) => p.strike), ys = pts.map((p) => p.iv_pct).concat(rv !== null ? [rv] : []);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys) * 0.95, y1 = Math.max(...ys) * 1.05;
  const X = (v: number) => P + ((v - x0) / (x1 - x0 || 1)) * (W - 2 * P);
  const Y = (v: number) => H - P - ((v - y0) / (y1 - y0 || 1)) * (H - 2 * P);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      <polyline fill="none" stroke="#40D7DD" strokeWidth="2" points={pts.map((p) => `${X(p.strike)},${Y(p.iv_pct)}`).join(' ')} />
      {pts.map((p) => <circle key={p.strike} cx={X(p.strike)} cy={Y(p.iv_pct)} r="2.5" fill="#40D7DD" />)}
      {spot >= x0 && spot <= x1 && <line x1={X(spot)} x2={X(spot)} y1={P / 2} y2={H - P} stroke="#646C78" strokeDasharray="3 3" />}
      {rv !== null && <g><line x1={P} x2={W - P} y1={Y(rv)} y2={Y(rv)} stroke="#F59E0B" strokeDasharray="5 4" /><text x={W - P} y={Y(rv) - 4} fill="#F59E0B" fontSize="10" textAnchor="end">RV {fmt(rv, 1)}%</text></g>}
      <text x={P} y={H - 8} fill="#646C78" fontSize="10">{x0.toLocaleString('pt-BR')}</text>
      <text x={W - P} y={H - 8} fill="#646C78" fontSize="10" textAnchor="end">{x1.toLocaleString('pt-BR')}</text>
      <text x={4} y={Y(y1) + 8} fill="#646C78" fontSize="10">{fmt(y1, 0)}%</text>
      <text x={4} y={Y(y0)} fill="#646C78" fontSize="10">{fmt(y0, 0)}%</text>
    </svg>
  );
};

// ---------------------------------------------------------------- payoff
function payoffAt(legs: Leg[], S: number, size: number): number {
  return legs.reduce((acc, l) => {
    const intr = l.c.cp === 'C' ? Math.max(S - l.c.strike, 0) : Math.max(l.c.strike - S, 0);
    const sgn = l.side === 'BUY' ? 1 : -1;
    return acc + sgn * l.qty * (intr - l.price) * size;
  }, 0);
}

const PayoffChart: React.FC<{ legs: Leg[]; spot: number; size: number }> = ({ legs, spot, size }) => {
  const W = 420, H = 190, P = 30;
  const grid = useMemo(() => Array.from({ length: 121 }, (_, i) => spot * (0.7 + (0.6 * i) / 120)), [spot]);
  const vals = useMemo(() => grid.map((s) => payoffAt(legs, s, size)), [grid, legs, size]);
  const mn = Math.min(...vals, 0), mx = Math.max(...vals, 0);
  const X = (v: number) => P + ((v - grid[0]) / (grid[grid.length - 1] - grid[0])) * (W - 2 * P);
  const Y = (v: number) => H - P - ((v - mn) / (mx - mn || 1)) * (H - 2 * P);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      <line x1={P} x2={W - P} y1={Y(0)} y2={Y(0)} stroke="#646C78" />
      <line x1={X(spot)} x2={X(spot)} y1={P / 2} y2={H - P} stroke="#646C78" strokeDasharray="3 3" />
      <polyline fill="none" stroke="#40D7DD" strokeWidth="2" points={grid.map((s, i) => `${X(s)},${Y(vals[i])}`).join(' ')} />
      <text x={P} y={H - 8} fill="#646C78" fontSize="10">{fmt(grid[0], 0)}</text>
      <text x={W - P} y={H - 8} fill="#646C78" fontSize="10" textAnchor="end">{fmt(grid[grid.length - 1], 0)}</text>
      <text x={4} y={Y(mx) + 8} fill="#10B981" fontSize="10">{fmt(mx, 0)}</text>
      <text x={4} y={Y(mn)} fill="#EF4444" fontSize="10">{fmt(mn, 0)}</text>
    </svg>
  );
};

function summarize(legs: Leg[], spot: number, size: number) {
  const grid = Array.from({ length: 1201 }, (_, i) => spot * (0.5 + (1.0 * i) / 1200));
  const v = grid.map((s) => payoffAt(legs, s, size));
  const be: number[] = [];
  for (let i = 1; i < grid.length; i++) if (v[i - 1] * v[i] < 0) be.push(grid[i - 1] + ((grid[i] - grid[i - 1]) * -v[i - 1]) / (v[i] - v[i - 1]));
  const slopeHi = v[v.length - 1] - v[v.length - 2], slopeLo = v[1] - v[0];
  return {
    max: Math.max(...v), min: Math.min(...v), be,
    upUnbounded: slopeHi > 1e-9, downUnbounded: slopeLo < -1e-9,
    cost: legs.reduce((a, l) => a + (l.side === 'BUY' ? 1 : -1) * l.qty * l.price * size, 0),
    greeks: ['delta', 'gamma', 'vega', 'theta'].reduce((o, g) => ({
      ...o, [g]: legs.reduce((a, l) => a + (l.side === 'BUY' ? 1 : -1) * l.qty * (l.c[g as 'delta'] || 0) * size, 0),
    }), {} as Record<string, number>),
  };
}

// ---------------------------------------------------------------- componente principal
export const OptionsSuite: React.FC<Props> = ({ analytics, news, calendar, analyzerMode, onExecute }) => {
  const [legs, setLegs] = useState<Leg[]>([]);
  const [msg, setMsg] = useState<string>('');
  const [busy, setBusy] = useState(false);

  const a = analytics && !analytics.error ? analytics : null;
  const size = a?.contract_size ?? 1;
  const canExecute = !!a && a.source === 'BYBIT' && legs.length > 0 && legs.length <= 20 && !busy;

  const strikes = useMemo(() => (a ? [...new Set(a.chain.map((c) => c.strike))].sort((x, y) => x - y) : []), [a]);
  const find = (strike: number, cp: 'C' | 'P') => a?.chain.find((c) => c.strike === strike && c.cp === cp);
  const atmIdx = a ? strikes.reduce((best, s, i) => (Math.abs(s - a.spot) < Math.abs(strikes[best] - a.spot) ? i : best), 0) : 0;

  const addLeg = (c: OptionContract, side: 'BUY' | 'SELL') =>
    setLegs((p) => [...p, { key: `${c.symbol}-${side}-${Date.now()}-${p.length}`, c, side, qty: 1, price: legPrice(c, side) }]);

  const preset = (name: string) => {
    if (!a || strikes.length < 9) return;
    const at = (o: number, cp: 'C' | 'P', side: 'BUY' | 'SELL'): Leg | null => {
      const c = find(strikes[Math.min(Math.max(atmIdx + o, 0), strikes.length - 1)], cp);
      return c ? { key: `${c.symbol}-${side}-${Math.random()}`, c, side, qty: 1, price: legPrice(c, side) } : null;
    };
    const defs: Record<string, [number, 'C' | 'P', 'BUY' | 'SELL'][]> = {
      'Long Straddle': [[0, 'C', 'BUY'], [0, 'P', 'BUY']],
      'Long Strangle': [[2, 'C', 'BUY'], [-2, 'P', 'BUY']],
      'Bull Call Spread': [[0, 'C', 'BUY'], [2, 'C', 'SELL']],
      'Iron Condor': [[-4, 'P', 'BUY'], [-2, 'P', 'SELL'], [2, 'C', 'SELL'], [4, 'C', 'BUY']],
    };
    setLegs((defs[name] || []).map(([o, cp, s]) => at(o, cp, s)).filter(Boolean) as Leg[]);
  };

  const sum = a && legs.length ? summarize(legs, a.spot, size) : null;

  const execute = async () => {
    const lines = legs.map((l) => `${l.side} ${l.qty}× ${l.c.symbol} @ ${l.price}`).join('\n');
    const modo = analyzerMode ? 'PAPER (nenhuma ordem real)' : 'ORDENS REAIS na Bybit (testnet, salvo BYBIT_TESTNET=false)';
    if (!window.confirm(`Executar estrutura (${legs.length} pernas) em ${modo}?\n\n${lines}`)) return;
    setBusy(true);
    try {
      setMsg(await onExecute(legs.map((l) => ({ symbol: l.c.symbol, venue: 'BYBIT_OPT' as const, side: l.side, amount: l.qty, price: l.price }))));
    } catch (e) { setMsg(`Erro: ${(e as Error).message}`); } finally { setBusy(false); }
  };

  return (
    <section className="p-4 max-w-[1920px] mx-auto">
      <div className="flex items-center gap-3 mb-3">
        <h2 className="text-sm font-bold uppercase tracking-wider text-slate-200">Suíte de Opções • GEX • IV Smile • Estruturas</h2>
        <SourceBadge source={analytics?.source} />
        {a && <span className="text-[11px] font-mono text-gt-muted">vencimento em {fmt(a.expiry_days, 1)}d • spot {fmt(a.spot)}</span>}
      </div>

      {!a && <div className={`${panel} text-xs text-gt-muted`}>{analytics?.error ? `Cadeia de opções indisponível: ${analytics.error}` : 'Aguardando cadeia de opções…'}</div>}

      {a && (
        <div className="grid grid-cols-12 gap-4">
          {/* métricas */}
          <div className={`${panel} col-span-12 grid grid-cols-2 md:grid-cols-6 gap-3 text-xs font-mono`}>
            {[
              ['IV ATM', a.atm_iv_pct !== null ? `${fmt(a.atm_iv_pct)}%` : '—'],
              ['Vol. realizada', a.rv_pct !== null ? `${fmt(a.rv_pct)}%` : 'indisp.'],
              ['Prêmio IV−RV', a.iv_rv_premium_pts !== null ? `${a.iv_rv_premium_pts >= 0 ? '+' : ''}${fmt(a.iv_rv_premium_pts, 1)} pts` : 'indisp.'],
              ['PCR (OI)', fmt(a.pcr_oi)],
              ['Max Pain', a.max_pain !== null ? a.max_pain.toLocaleString('pt-BR') : '—'],
              ['Gamma Flip', a.gamma_flip !== null ? a.gamma_flip.toLocaleString('pt-BR') : 'sem cruzamento'],
            ].map(([k, v]) => (
              <div key={k}><div className="text-gt-muted text-[10px] uppercase">{k}</div><div className="text-slate-100 text-sm">{v}</div></div>
            ))}
            <div className="col-span-2 md:col-span-6 text-[11px] text-gt-muted font-sans">
              {a.rv_pct !== null && a.atm_iv_pct !== null
                ? `IV ATM ${fmt(a.atm_iv_pct, 1)}% vs RV ${fmt(a.rv_pct, 1)}% (retornos de 1m, n=${a.rv_window_returns}) → prêmio ${fmt(a.iv_rv_premium_pts, 1)} pts. `
                : 'Vol. realizada indisponível (poucas velas). '}
              GEX: {a.gex_assumption}. GEX total líquido: {fmt(a.net_gex_total / 1e6)}M US$/1%.
            </div>
          </div>

          <div className={`${panel} col-span-12 lg:col-span-6`}>
            <div className="text-xs font-bold uppercase text-slate-200 mb-2">GEX por strike (US$ M por 1%)</div>
            <GexBars rows={a.gex_by_strike} spot={a.spot} flip={a.gamma_flip} maxPain={a.max_pain} />
          </div>
          <div className={`${panel} col-span-12 lg:col-span-6`}>
            <div className="text-xs font-bold uppercase text-slate-200 mb-2">IV Smile (OTM) vs Vol. Realizada</div>
            <IvSmile pts={a.iv_smile} spot={a.spot} rv={a.rv_pct} />
          </div>

          {/* strategy builder */}
          <div className={`${panel} col-span-12 lg:col-span-7`}>
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <span className="text-xs font-bold uppercase text-slate-200 mr-2">Construtor de Estruturas</span>
              {['Long Straddle', 'Long Strangle', 'Bull Call Spread', 'Iron Condor'].map((n) => (
                <button key={n} onClick={() => preset(n)} className="px-2 py-1 rounded bg-gt-surface hover:bg-gt-surface/70 text-[11px] text-slate-200">{n}</button>
              ))}
              <button onClick={() => setLegs([])} className="px-2 py-1 rounded border border-gt-surface text-[11px] text-gt-muted">Limpar</button>
            </div>
            <div className="overflow-auto max-h-64 mb-3">
              <table className="w-full text-[11px] font-mono">
                <thead className="text-gt-muted sticky top-0 bg-gt-panel"><tr>
                  <th className="text-right">Call bid/ask</th><th></th><th className="text-center">Strike</th><th></th><th className="text-left">Put bid/ask</th>
                </tr></thead>
                <tbody>
                  {strikes.map((s) => {
                    const c = find(s, 'C'), p = find(s, 'P');
                    return (
                      <tr key={s} className={`border-t border-gt-surface/60 ${s === strikes[atmIdx] ? 'bg-gt-surface/40' : ''}`}>
                        <td className="text-right py-0.5">{c ? `${fmt(c.bid, 1)} / ${fmt(c.ask, 1)}` : '—'}</td>
                        <td className="px-1 whitespace-nowrap">{c && <><button className="text-emerald-400" onClick={() => addLeg(c, 'BUY')}>B</button> <button className="text-rose-400" onClick={() => addLeg(c, 'SELL')}>S</button></>}</td>
                        <td className="text-center text-slate-100">{s.toLocaleString('pt-BR')}</td>
                        <td className="px-1 whitespace-nowrap">{p && <><button className="text-emerald-400" onClick={() => addLeg(p, 'BUY')}>B</button> <button className="text-rose-400" onClick={() => addLeg(p, 'SELL')}>S</button></>}</td>
                        <td className="py-0.5">{p ? `${fmt(p.bid, 1)} / ${fmt(p.ask, 1)}` : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {legs.length > 0 && (
              <div className="space-y-1 mb-2 text-[11px] font-mono">
                {legs.map((l) => (
                  <div key={l.key} className="flex items-center gap-2 bg-gt-input rounded px-2 py-1">
                    <span className={l.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}>{l.side}</span>
                    <span className="flex-1 truncate">{l.c.symbol}</span>
                    <label className="text-gt-muted">qtd
                      <input type="number" min={0.01} step={0.01} value={l.qty} onChange={(e) => setLegs((p) => p.map((x) => x.key === l.key ? { ...x, qty: Math.max(0.01, Number(e.target.value) || 0.01) } : x))} className="ml-1 w-16 bg-gt-bg rounded px-1 text-slate-100" />
                    </label>
                    <label className="text-gt-muted">prêmio
                      <input type="number" min={0} step={0.1} value={l.price} onChange={(e) => setLegs((p) => p.map((x) => x.key === l.key ? { ...x, price: Math.max(0, Number(e.target.value) || 0) } : x))} className="ml-1 w-20 bg-gt-bg rounded px-1 text-slate-100" />
                    </label>
                    <button onClick={() => setLegs((p) => p.filter((x) => x.key !== l.key))} className="text-gt-muted hover:text-rose-400">✕</button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className={`${panel} col-span-12 lg:col-span-5`}>
            <div className="text-xs font-bold uppercase text-slate-200 mb-2">Payoff no vencimento</div>
            {sum ? (
              <>
                <PayoffChart legs={legs} spot={a.spot} size={size} />
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] font-mono mt-2">
                  <span className="text-gt-muted">Custo líquido (débito+)</span><span>{fmt(sum.cost)}</span>
                  <span className="text-gt-muted">Lucro máx. (±50% do spot)</span><span className="text-emerald-400">{sum.upUnbounded ? 'ilimitado ↑' : fmt(sum.max)}</span>
                  <span className="text-gt-muted">Perda máx. (±50% do spot)</span><span className="text-rose-400">{sum.downUnbounded ? 'ilimitada ↓' : fmt(sum.min)}</span>
                  <span className="text-gt-muted">Breakevens</span><span>{sum.be.length ? sum.be.map((b) => fmt(b, 0)).join(' / ') : '—'}</span>
                  <span className="text-gt-muted">Δ líquido</span><span>{fmt(sum.greeks.delta, 3)}</span>
                  <span className="text-gt-muted">Γ líquido</span><span>{sum.greeks.gamma.toExponential(2)}</span>
                  <span className="text-gt-muted">Vega líquido</span><span>{fmt(sum.greeks.vega, 2)}</span>
                  <span className="text-gt-muted">Theta líquido/dia</span><span>{fmt(sum.greeks.theta, 2)}</span>
                </div>
                <button
                  disabled={!canExecute}
                  onClick={execute}
                  title={a.source !== 'BYBIT' ? 'Desabilitado: a cadeia é SIMULADA — símbolos não existem na Bybit' : undefined}
                  className="mt-3 w-full py-2 rounded-lg text-xs font-bold font-mono uppercase tracking-wider bg-gt-accent text-gt-bg disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  Executar Estrutura ({analyzerMode ? 'PAPER' : 'LIVE'})
                </button>
                {a.source !== 'BYBIT' && <p className="text-[11px] text-amber-300 mt-1">Cadeia SIMULADA: use GOLTRADER_DATA_SOURCE=bybit para executar.</p>}
                {msg && <p className="text-[11px] text-slate-300 mt-1 whitespace-pre-wrap">{msg}</p>}
              </>
            ) : <div className="text-xs text-gt-muted">Adicione pernas (B = comprar, S = vender) ou escolha um modelo.</div>}
          </div>
        </div>
      )}

      {/* notícias + calendário */}
      <div className="grid grid-cols-12 gap-4 mt-4">
        <div className={`${panel} col-span-12 lg:col-span-7`}>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs font-bold uppercase text-slate-200">Notícias globais</span>
            <SourceBadge source="RSS" />
            <span className="text-[10px] text-gt-muted">sentimento por palavras-chave (heurística)</span>
          </div>
          <ul className="space-y-1.5 max-h-72 overflow-y-auto">
            {(news?.items || []).length === 0 && <li className="text-xs text-gt-muted">Sem notícias ainda (aguardando RSS).</li>}
            {(news?.items || []).map((n) => (
              <li key={n.id} className="text-xs flex items-start gap-2">
                <span className={`mt-0.5 px-1.5 rounded font-mono text-[10px] ${n.sentiment > 0.2 ? 'bg-emerald-500/20 text-emerald-300' : n.sentiment < -0.2 ? 'bg-rose-500/20 text-rose-300' : 'bg-gt-surface text-gt-muted'}`}>{n.sentiment > 0 ? '+' : ''}{n.sentiment.toFixed(2)}</span>
                <div className="flex-1 min-w-0">
                  {n.link ? <a href={n.link} target="_blank" rel="noopener noreferrer" className="text-slate-200 hover:text-gt-accent">{n.title}</a> : <span className="text-slate-200">{n.title}</span>}
                  <div className="text-[10px] text-gt-muted">{n.source} • {new Date(n.published * 1000).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}{n.assets.length ? ` • ${n.assets.join(', ')}` : ''}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <div className={`${panel} col-span-12 lg:col-span-5`}>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs font-bold uppercase text-slate-200">Calendário econômico</span>
            <SourceBadge example={calendar?.example} source={calendar?.source} />
          </div>
          {calendar?.example && <p className="text-[11px] text-amber-300 mb-2">{calendar.note}</p>}
          <table className="w-full text-[11px]"><tbody>
            {(calendar?.events || []).map((e, i) => (
              <tr key={i} className="border-t border-gt-surface/60">
                <td className="py-1 text-gt-muted">{e.country}</td><td className="text-slate-200">{e.event}</td>
                <td className="text-gt-muted">{e.impact}</td>
                <td className="font-mono text-right">{e.forecast ?? '—'}</td>
              </tr>
            ))}
          </tbody></table>
        </div>
      </div>
    </section>
  );
};

export default OptionsSuite;
