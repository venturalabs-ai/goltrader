import React, { useEffect, useMemo, useRef, useState } from 'react';

// ------------------------------------------------------------ Relógio mundial de bolsas (horários regulares, sem feriados)
interface Exchange { name: string; tz: string; open: [number, number]; close: [number, number] | null }
const EXCHANGES: Exchange[] = [
  { name: 'B3 São Paulo', tz: 'America/Sao_Paulo', open: [10, 0], close: [17, 55] },
  { name: 'NYSE', tz: 'America/New_York', open: [9, 30], close: [16, 0] },
  { name: 'LSE Londres', tz: 'Europe/London', open: [8, 0], close: [16, 30] },
  { name: 'Xetra Frankfurt', tz: 'Europe/Berlin', open: [9, 0], close: [17, 30] },
  { name: 'TSE Tóquio', tz: 'Asia/Tokyo', open: [9, 0], close: [15, 30] },
  { name: 'Cripto 24/7', tz: 'UTC', open: [0, 0], close: null },
];

function localParts(tz: string, d: Date) {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false, weekday: 'short' }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value || '';
  return { hour: parseInt(get('hour'), 10) % 24, minute: parseInt(get('minute'), 10), weekday: get('weekday') };
}

export function isOpen(ex: Exchange, d: Date): boolean {
  if (!ex.close) return true;
  const { hour, minute, weekday } = localParts(ex.tz, d);
  if (weekday === 'Sat' || weekday === 'Sun') return false;
  const m = hour * 60 + minute;
  return m >= ex.open[0] * 60 + ex.open[1] && m < ex.close[0] * 60 + ex.close[1];
}

export const ExchangeClock: React.FC = () => {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 15000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="hidden xl:flex items-center gap-3 text-[11px] text-gt-muted border-l border-gt-surface pl-4" title="Horários regulares; feriados não considerados">
      {EXCHANGES.map((ex) => {
        const open = isOpen(ex, now);
        const hhmm = new Intl.DateTimeFormat('pt-BR', { timeZone: ex.tz, hour: '2-digit', minute: '2-digit' }).format(now);
        return (
          <span key={ex.name} className={`font-mono ${open ? 'text-emerald-400' : 'text-gt-muted'}`}>
            {open ? '●' : '○'} {ex.name} <span className="text-slate-400">{hhmm}</span>
          </span>
        );
      })}
    </div>
  );
};

// ------------------------------------------------------------ Chave SANDBOX / LIVE
interface ModeProps { analyzer: boolean | undefined; onToggle: () => void }
export const ModeSwitch: React.FC<ModeProps> = ({ analyzer, onToggle }) => (
  <button
    onClick={onToggle}
    title={analyzer ? 'Clique para pedir LIVE (exige confirmação "CONFIRMAR" e GOLTRADER_ALLOW_LIVE no servidor)' : 'Clique para voltar ao SANDBOX'}
    className={`px-3 py-1.5 rounded-lg text-xs font-mono font-semibold border transition ${
      analyzer ? 'bg-amber-500/15 text-amber-300 border-amber-500/50' : 'bg-rose-500/20 text-rose-300 border-rose-500/60'
    }`}
  >
    MODO: {analyzer === undefined ? '…' : analyzer ? 'SANDBOX (PAPER)' : 'LIVE'}
  </button>
);

// ------------------------------------------------------------ Paleta Ctrl+K
export interface Command { id: string; label: string; hint?: string; run: () => void }
interface Props { open: boolean; onClose: () => void; commands: Command[] }

export const CommandBar: React.FC<Props> = ({ open, onClose, commands }) => {
  const [q, setQ] = useState('');
  const [idx, setIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) { setQ(''); setIdx(0); setTimeout(() => inputRef.current?.focus(), 0); }
  }, [open]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? commands.filter((c) => c.label.toLowerCase().includes(s) || (c.hint || '').toLowerCase().includes(s)) : commands;
  }, [q, commands]);

  if (!open) return null;

  const runAt = (i: number) => { const c = filtered[i]; if (c) { onClose(); c.run(); } };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-24 bg-black/60" onClick={onClose}>
      <div className="w-full max-w-lg bg-gt-panel border border-gt-surface rounded-xl shadow-2xl overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => { setQ(e.target.value); setIdx(0); }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose();
            else if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(i + 1, filtered.length - 1)); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); }
            else if (e.key === 'Enter') runAt(idx);
          }}
          placeholder="Buscar tela ou ação…"
          className="w-full bg-gt-input px-4 py-3 text-sm outline-none border-b border-gt-surface text-slate-100 placeholder:text-gt-muted"
        />
        <ul className="max-h-72 overflow-y-auto">
          {filtered.length === 0 && <li className="px-4 py-3 text-xs text-gt-muted">Nada encontrado</li>}
          {filtered.map((c, i) => (
            <li
              key={c.id}
              onMouseEnter={() => setIdx(i)}
              onClick={() => runAt(i)}
              className={`px-4 py-2.5 text-sm cursor-pointer flex justify-between ${i === idx ? 'bg-gt-surface text-gt-accent' : 'text-slate-300'}`}
            >
              <span>{c.label}</span>
              {c.hint && <span className="text-[11px] text-gt-muted">{c.hint}</span>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
};

export default CommandBar;
