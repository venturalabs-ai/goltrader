import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FlowChart } from './components/FlowChart';
import { CommandBar, Command, ExchangeClock, ModeSwitch } from './components/CommandBar';
import { OptionsSuite, ExecLeg } from './components/OptionsSuite';
import { apiPost, getToken, setToken, wsUrl } from './config';
import type { FlowBubble, Payload } from './types';

type Conn = 'conectando' | 'online' | 'offline';

export default function App() {
  const [data, setData] = useState<Payload | null>(null);
  const [bubbles, setBubbles] = useState<FlowBubble[]>([]);
  const [whales, setWhales] = useState<FlowBubble[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [conn, setConn] = useState<Conn>('conectando');
  const [token, setTokenState] = useState(getToken());
  const [notice, setNotice] = useState('');
  const wsRef = useRef<WebSocket | null>(null);

  // WebSocket com reconexão; URL derivada do host da página / VITE_API_URL; token via query
  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const connect = () => {
      setConn('conectando');
      const ws = new WebSocket(wsUrl('/ws/pulse', token));
      wsRef.current = ws;
      ws.onopen = () => setConn('online');
      ws.onmessage = (event) => {
        const payload: Payload = JSON.parse(event.data);
        setData(payload);
        const fresh = payload.flow_bubbles || [];
        if (fresh.length) {
          setBubbles((prev) => [...fresh, ...prev].slice(0, 26));
          const w = fresh.filter((b) => b.is_whale);
          if (w.length) setWhales((prev) => [...w, ...prev].slice(0, 13));
        }
      };
      ws.onclose = () => {
        setConn('offline');
        if (!stop) timer = window.setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => { stop = true; window.clearTimeout(timer); wsRef.current?.close(); };
  }, [token]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setSearchOpen((p) => !p); }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const call = useCallback(async (path: string, body?: unknown) => {
    const r = await apiPost(path, body);
    if (!r.ok) setNotice(`Falha (${r.status}): ${typeof r.data?.detail === 'string' ? r.data.detail : JSON.stringify(r.data?.detail ?? r.data)}`);
    else setNotice('');
    return r;
  }, []);

  const toggleMode = async () => {
    if (data?.analyzer_mode === false) { await call('/api/v1/toggle_mode'); return; }
    const typed = window.prompt('ATENÇÃO: ir para LIVE envia ordens às corretoras.\nDigite CONFIRMAR para continuar:');
    if (typed === null) return;
    await call('/api/v1/toggle_mode', { confirm: typed.trim() });
  };
  const toggleAutonomous = () => call('/api/v1/toggle_autonomous');
  const killSwitch = async () => {
    if (data?.kill_switch) await call('/api/v1/kill_switch', { active: false });
    else if (window.confirm('Ativar KILL SWITCH? Bloqueia novas ordens, pausa o funil e volta para SANDBOX.')) await call('/api/v1/kill_switch', { active: true });
  };

  const triggerManualBasket = async (side: 'BUY' | 'SELL') => {
    const r = await call('/api/v1/place_concurrent_basket', [
      { symbol: 'BTCUSDT', venue: 'BYBIT_PERP', side, amount: 0.001, leverage: 5 },
    ]);
    if (r.ok) setNotice(`Cesta enviada: ${r.data.executions.map((e: any) => `${e.venue} ${e.status}`).join(' | ')}`);
  };

  const executeStructure = async (legs: ExecLeg[]) => {
    const r = await call('/api/v1/place_concurrent_basket', legs);
    if (!r.ok) throw new Error(typeof r.data?.detail === 'string' ? r.data.detail : `HTTP ${r.status}`);
    return r.data.executions.map((e: any) => `${e.side} ${e.symbol}: ${e.status}${e.error ? ` (${e.error})` : ''} [${e.mode}]`).join('\n');
  };

  const commands: Command[] = useMemo(() => [
    { id: 'flow', label: 'Ir para Fluxo (gráfico)', run: () => window.scrollTo({ top: 0, behavior: 'smooth' }) },
    { id: 'opts', label: 'Ir para Opções (GEX, IV, estruturas)', run: () => document.getElementById('opcoes')?.scrollIntoView({ behavior: 'smooth' }) },
    { id: 'auto', label: 'Alternar funil autônomo', hint: 'exige token', run: () => { void toggleAutonomous(); } },
    { id: 'kill', label: 'Kill switch (ativar/reativar)', hint: 'exige token', run: () => { void killSwitch(); } },
    { id: 'mode', label: 'Alternar SANDBOX / LIVE', hint: 'LIVE exige CONFIRMAR', run: () => { void toggleMode(); } },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [data?.analyzer_mode, data?.kill_switch]);

  const wall = data?.book_wall ?? null;
  const bidPct = wall ? Math.round((wall.bid_vol_usd_m / (wall.bid_vol_usd_m + wall.ask_vol_usd_m)) * 100) : 50;
  const simulated = data?.simulated ?? true;

  return (
    <div className="min-h-screen bg-gt-bg text-slate-100 font-sans antialiased selection:bg-gt-accent/30">
      <header className="sticky top-0 z-30 bg-gt-bg/90 backdrop-blur-md border-b border-gt-surface px-4 py-2.5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-full bg-gt-accent shadow-[0_0_12px_#40D7DD]" />
            <span className="font-bold tracking-tight text-base">GOLTRADER</span>
            <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-gt-accent/10 text-gt-accent border border-gt-accent/30">Pulse • Flow • Opções</span>
            <span
              className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded border ${simulated ? 'bg-amber-500/20 text-amber-300 border-amber-500/50' : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40'}`}
              title={simulated ? 'Preços, book e fluxo são SINTÉTICOS. Ordens do funil são sempre paper.' : 'Dados públicos reais da Bybit'}
            >
              {simulated ? 'DADOS SIMULADOS' : 'DADOS BYBIT (público)'}
            </span>
            <span className={`text-[10px] font-mono ${conn === 'online' ? 'text-emerald-400' : conn === 'offline' ? 'text-rose-400' : 'text-amber-300'}`}>● {conn}</span>
          </div>
          <ExchangeClock />
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <input
            type="password"
            placeholder="token da API"
            value={token}
            onChange={(e) => { setTokenState(e.target.value); setToken(e.target.value); }}
            className="w-32 bg-gt-input border border-gt-surface rounded-lg px-2 py-1.5 text-xs font-mono text-slate-200 placeholder:text-gt-muted"
            title="Fica só no sessionStorage deste navegador (não vai para o bundle)."
          />
          <button onClick={() => setSearchOpen(true)} className="bg-gt-input hover:bg-gt-surface border border-gt-surface text-gt-muted text-xs px-3 py-1.5 rounded-lg flex items-center gap-3">
            <span>Buscar tela ou ação...</span>
            <kbd className="bg-gt-bg px-1.5 py-0.5 rounded text-[10px] font-mono text-slate-300 border border-gt-surface">Ctrl K</kbd>
          </button>
          <button
            onClick={toggleAutonomous}
            className={`px-3 py-1.5 rounded-lg text-xs font-mono font-semibold border transition ${data?.autonomous_funnel ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/50' : 'bg-gt-surface/40 text-gt-muted border-gt-surface'}`}
          >
            FUNIL: {data?.autonomous_funnel ? (simulated || data?.analyzer_mode ? 'ATIVO (PAPER)' : 'ATIVO') : 'PAUSADO'}
          </button>
          <ModeSwitch analyzer={data?.analyzer_mode} onToggle={toggleMode} />
          <button
            onClick={killSwitch}
            className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold border ${data?.kill_switch ? 'bg-rose-600 text-white border-rose-400' : 'bg-rose-500/10 text-rose-300 border-rose-500/40 hover:bg-rose-500/20'}`}
          >
            {data?.kill_switch ? 'KILL ATIVO — reativar' : 'KILL SWITCH'}
          </button>
        </div>
      </header>

      {notice && <div className="mx-4 mt-3 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/40 text-xs text-amber-200 whitespace-pre-wrap">{notice}</div>}

      <main className="p-4 grid grid-cols-12 gap-4 max-w-[1920px] mx-auto">
        <section className="col-span-12 lg:col-span-8 flex flex-col gap-4">
          <FlowChart candles={data?.history || []} orderBlocks={data?.order_blocks || []} bookWall={wall} recentBubbles={bubbles} simulated={simulated} />

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {(data?.order_blocks || []).slice(0, 3).map((ob) => (
              <div key={ob.id} className="bg-gt-panel border border-gt-surface rounded-xl p-3.5">
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="font-mono text-gt-muted">{ob.symbol} • OB 1m</span>
                  <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold ${ob.side === 'BUY' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-rose-500/15 text-rose-400'}`}>
                    {ob.side}-SIDE • {ob.status}
                  </span>
                </div>
                <div className="text-base font-mono font-bold tabular-nums">${ob.bottom.toLocaleString()} – ${ob.top.toLocaleString()}</div>
                <div className="text-[11px] text-gt-muted mt-1 flex justify-between">
                  <span>{ob.retested ? 'Retestado' : 'Aguardando 1º reteste'}</span>
                  <span className="font-mono text-slate-300">{ob.distance_pct}% dist.</span>
                </div>
              </div>
            ))}
          </div>

          <div className="bg-gt-panel border border-gt-surface rounded-xl p-4">
            <div className="mb-3">
              <h2 className="text-sm font-bold uppercase tracking-wider text-slate-200 flex items-center gap-2">
                Derivatives Pulse & Buildup
                {simulated && <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 text-[10px] font-mono">SIMULADO</span>}
              </h2>
              <p className="text-xs text-gt-muted">Preço × Open Interest (janela indicada), funding, base mark/index, PCR e Max Pain (opções BTC)</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs font-mono tabular-nums">
                <thead><tr className="border-b border-gt-surface text-gt-muted">
                  <th className="py-2">Ativo</th><th>Último</th><th>24h %</th><th>Open Interest</th><th>Funding</th><th>Base</th><th>PCR / Max Pain</th><th>IV ATM</th><th>Buildup</th>
                </tr></thead>
                <tbody className="divide-y divide-gt-surface">
                  {(data?.derivatives_pulse || []).map((row) => (
                    <tr key={row.symbol} className="hover:bg-gt-surface/30">
                      <td className="py-2.5 font-bold">{row.symbol}</td>
                      <td>${row.last_price.toLocaleString()}</td>
                      <td className={(row.change_24h ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}>{row.change_24h === null ? '—' : `${row.change_24h >= 0 ? '+' : ''}${row.change_24h}%`}</td>
                      <td>{row.open_interest_usd_m === null ? '—' : `$${row.open_interest_usd_m}M`}{row.oi_change_pct !== null && ` (${row.oi_change_pct >= 0 ? '+' : ''}${row.oi_change_pct}%/${row.oi_window_s}s)`}</td>
                      <td className="text-amber-300">{row.funding_rate_pct === null ? '—' : `${row.funding_rate_pct}%`}</td>
                      <td>{row.basis_pct === null ? '—' : `${row.basis_pct}%`}</td>
                      <td>{row.pcr_oi ?? '—'} / {row.max_pain ? `$${row.max_pain.toLocaleString()}` : '—'}</td>
                      <td>{row.atm_iv === null ? '—' : `${row.atm_iv}%`}</td>
                      <td>
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${row.buildup_status.includes('LONG BUILDUP') ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40' : row.buildup_status.includes('SHORT BUILDUP') ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40' : 'bg-amber-500/20 text-amber-300 border border-amber-500/40'}`}>
                          {row.buildup_status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <aside className="col-span-12 lg:col-span-4 flex flex-col gap-4">
          <div className="bg-gt-panel border border-gt-surface rounded-xl p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-200">Disparo Rápido (Bybit Perp)</span>
              <span className="text-[11px] font-mono text-gt-accent">{data?.analyzer_mode ? 'PAPER' : data?.kill_switch ? 'BLOQUEADO' : 'LIVE'}</span>
            </div>
            <p className="text-xs text-gt-muted mb-3">0,001 BTC a mercado, 5x. Passa pelo endpoint autenticado com guardas de risco; no SANDBOX nenhuma ordem sai.</p>
            <div className="grid grid-cols-2 gap-2.5">
              <button onClick={() => triggerManualBasket('BUY')} className="bg-emerald-600 hover:bg-emerald-500 text-slate-950 font-bold py-2.5 rounded-lg text-xs font-mono uppercase">▲ LONG</button>
              <button onClick={() => triggerManualBasket('SELL')} className="bg-rose-600 hover:bg-rose-500 text-white font-bold py-2.5 rounded-lg text-xs font-mono uppercase">▼ SHORT</button>
            </div>
          </div>

          {wall && (
            <div className="bg-gt-panel border border-gt-surface rounded-xl p-4">
              <div className="flex justify-between items-center text-xs mb-1.5">
                <span className="font-bold uppercase">Order Book Wall (±{wall.range_pct}%)</span>
                <span className="font-mono text-gt-muted">Imbalance: {wall.imbalance_ratio}x</span>
              </div>
              <p className="text-xs text-slate-300 mb-2.5">
                <span className="text-emerald-400 font-mono font-bold">${wall.bid_vol_usd_m}M</span> comprando vs{' '}
                <span className="text-rose-400 font-mono font-bold">${wall.ask_vol_usd_m}M</span> vendendo
                {wall.range_pct < 0.5 && <span className="text-gt-muted"> (profundidade recebida cobre só ±{wall.range_pct}%)</span>}.
              </p>
              <div className="w-full h-3 bg-rose-500/30 rounded-full overflow-hidden flex">
                <div style={{ width: `${bidPct}%` }} className="bg-emerald-500 h-full transition-all duration-300" />
              </div>
              <div className="flex justify-between text-[11px] font-mono text-gt-muted mt-2">
                <span>Buy: ${wall.biggest_bid_size_usd_m}M @ {wall.biggest_bid_price}</span>
                <span>Sell: ${wall.biggest_ask_size_usd_m}M @ {wall.biggest_ask_price}</span>
              </div>
            </div>
          )}

          <div className="bg-gt-panel border border-gt-surface rounded-xl p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold uppercase text-amber-400">Funil de Ineficiências & Flash Dips</span>
              <span className="text-[11px] font-mono text-gt-muted">Z {data?.z_score ?? '—'} • news {data?.news_sentiment_btc ?? 0}</span>
            </div>
            {data?.funnel_signal ? (
              <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-xs">
                <div className="flex justify-between font-mono font-bold text-emerald-300">
                  <span>GATILHO: {data.funnel_signal.direction}</span><span>CONFIANÇA: {data.funnel_signal.confidence}%</span>
                </div>
                <ul className="mt-1.5 space-y-1 text-[11px] text-slate-300 list-disc list-inside">
                  {data.funnel_signal.reasons.map((r, i) => <li key={i}>{r}</li>)}
                </ul>
              </div>
            ) : (
              <div className="text-xs text-gt-muted font-mono py-2">
                {data && !data.feed_ready ? 'Aguardando dados do feed…' : 'Monitorando exaustão, paredes de liquidez e OBs…'}
              </div>
            )}
          </div>

          <div className="bg-gt-panel border border-gt-surface rounded-xl p-4">
            <h3 className="text-xs font-bold uppercase mb-2">Baleias recentes</h3>
            <div className="space-y-1 max-h-28 overflow-y-auto font-mono text-[11px]">
              {whales.length === 0 && <div className="text-gt-muted">Nenhuma baleia (≥ limite) ainda.</div>}
              {whales.map((w) => (
                <div key={w.id} className="flex justify-between">
                  <span className={w.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}>{w.side} ${Math.round(w.size_usd).toLocaleString()}</span>
                  <span className="text-gt-muted">{w.venue} • {w.time}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-gt-panel border border-gt-surface rounded-xl p-4 flex-1">
            <h3 className="text-xs font-bold uppercase mb-2">Execuções recentes</h3>
            <div className="space-y-1.5 max-h-56 overflow-y-auto font-mono text-[11px]">
              {(data?.active_positions || []).map((pos) => (
                <div key={pos.order_id} className="flex items-center justify-between px-2.5 py-1.5 rounded bg-gt-bg border border-gt-surface" title={pos.error || pos.mode}>
                  <div>
                    <span className={pos.side === 'BUY' ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}>{pos.side}</span>{' '}
                    <span>{pos.symbol}</span> <span className="text-gt-muted">[{pos.venue}]</span>
                  </div>
                  <div className="text-right">
                    <span className={pos.status.startsWith('REJECTED') ? 'text-rose-300' : pos.paper ? 'text-amber-300' : 'text-gt-accent'}>{pos.paper ? 'PAPER' : pos.status}</span>{' '}
                    <span className="text-gt-muted">{pos.time}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </aside>
      </main>

      {/* Suíte de Opções na mesma tela, abaixo do grid principal */}
      <div id="opcoes">
        <OptionsSuite
          analytics={data?.options_analytics ?? null}
          news={data?.market_news ?? null}
          calendar={data?.economic_calendar ?? null}
          analyzerMode={data?.analyzer_mode}
          onExecute={executeStructure}
        />
      </div>

      <CommandBar open={searchOpen} onClose={() => setSearchOpen(false)} commands={commands} />
    </div>
  );
}
