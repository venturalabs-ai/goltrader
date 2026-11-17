import React, { useEffect, useRef } from 'react';
import { createChart, IChartApi, ISeriesApi, IPriceLine, LineStyle, UTCTimestamp, CandlestickData } from 'lightweight-charts';
import type { BookWall, Candle, FlowBubble, OrderBlock } from '../types';

interface Props {
  candles: Candle[];
  orderBlocks: OrderBlock[];
  bookWall: BookWall | null;
  recentBubbles: FlowBubble[];
  simulated: boolean;
}

export const FlowChart: React.FC<Props> = ({ candles, orderBlocks, bookWall, recentBubbles, simulated }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: { background: { color: '#121318' }, textColor: '#646C78', fontFamily: 'Inter, sans-serif' },
      grid: { vertLines: { color: '#191C23' }, horzLines: { color: '#191C23' } },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: '#212A34' },
      timeScale: { borderColor: '#212A34', timeVisible: true, secondsVisible: false },
      width: containerRef.current.clientWidth,
      height: 410,
    });
    const series = chart.addCandlestickSeries({
      upColor: '#10B981', downColor: '#EF4444', borderUpColor: '#10B981', borderDownColor: '#EF4444',
      wickUpColor: '#10B981', wickDownColor: '#EF4444',
    });
    chartRef.current = chart;
    seriesRef.current = series;
    const onResize = () => containerRef.current && chart.applyOptions({ width: containerRef.current.clientWidth });
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || candles.length === 0) return;
    series.setData(candles.map((c) => ({ ...c, time: c.time as UTCTimestamp })) as CandlestickData[]);

    linesRef.current.forEach((l) => series.removePriceLine(l));
    linesRef.current = [];

    // Order Blocks: sólido = confirmado | tracejado = formando
    orderBlocks.forEach((ob) => {
      const isBuy = ob.side === 'BUY';
      linesRef.current.push(series.createPriceLine({
        price: isBuy ? ob.top : ob.bottom,
        color: isBuy ? '#10B981' : '#EF4444',
        lineWidth: 2,
        lineStyle: ob.status === 'CONFIRMED' ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: true,
        title: `${isBuy ? 'BUY OB' : 'SELL OB'} (${ob.status === 'CONFIRMED' ? 'Conf' : 'Form'})${ob.retested ? ' • Retest' : ''}`,
      }));
    });

    // Maiores paredes do book (US$ milhões)
    if (bookWall) {
      linesRef.current.push(series.createPriceLine({
        price: bookWall.biggest_bid_price, color: '#059669', lineWidth: 1, lineStyle: LineStyle.Dotted,
        axisLabelVisible: true, title: `BUY WALL $${bookWall.biggest_bid_size_usd_m}M`,
      }));
      linesRef.current.push(series.createPriceLine({
        price: bookWall.biggest_ask_price, color: '#DC2626', lineWidth: 1, lineStyle: LineStyle.Dotted,
        axisLabelVisible: true, title: `SELL WALL $${bookWall.biggest_ask_size_usd_m}M`,
      }));
    }
  }, [candles, orderBlocks, bookWall]);

  return (
    <div className="flex flex-col gap-3">
      <div className="bg-gt-panel border border-gt-surface rounded-xl p-3 flex items-center justify-between overflow-hidden">
        <div className="flex items-center gap-3">
          <span className="h-2.5 w-2.5 rounded-full bg-gt-accent animate-ping" />
          <div>
            <div className="text-xs font-semibold text-slate-200 uppercase tracking-wider flex items-center gap-2">
              Live Money Flow • Bolha = tamanho do trade em US$
              {simulated && <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 text-[10px]">SIMULADO</span>}
            </div>
            <div className="text-[11px] text-gt-muted">Verde = agressão compradora • Vermelho = vendedora • Anel âmbar = baleia</div>
          </div>
        </div>
        <div className="flex items-center gap-2 overflow-x-auto py-1 max-w-[60%]">
          {recentBubbles.slice(0, 14).map((b) => {
            const diameter = Math.min(Math.max(Math.sqrt(b.size_usd / 1200) * 6, 18), 48);
            const isBuy = b.side === 'BUY';
            return (
              <div
                key={b.id}
                title={`${b.venue} | ${b.side} $${b.size_usd.toLocaleString()} @ ${b.price}`}
                style={{ width: diameter, height: diameter }}
                className={`rounded-full flex items-center justify-center text-[9px] font-mono font-bold shrink-0 transition-all duration-300 ${
                  isBuy ? 'bg-emerald-500/25 text-emerald-300 border border-emerald-500' : 'bg-rose-500/25 text-rose-300 border border-rose-500'
                } ${b.is_whale ? 'ring-2 ring-amber-400' : ''}`}
              >
                {b.size_usd >= 1000 ? `${Math.round(b.size_usd / 1000)}K` : ''}
              </div>
            );
          })}
        </div>
      </div>
      <div className="bg-gt-panel border border-gt-surface rounded-xl p-2">
        <div ref={containerRef} className="w-full" />
      </div>
    </div>
  );
};
