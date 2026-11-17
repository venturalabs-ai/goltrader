export interface Candle { time: number; open: number; high: number; low: number; close: number; volume?: number }
export interface OrderBlock { id: string; symbol: string; side: 'BUY' | 'SELL'; top: number; bottom: number; status: 'FORMING' | 'CONFIRMED'; retested: boolean; distance_pct: number }
export interface BookWall {
  bid_vol_usd_m: number; ask_vol_usd_m: number; biggest_bid_price: number; biggest_bid_size_usd_m: number;
  biggest_ask_price: number; biggest_ask_size_usd_m: number; imbalance_ratio: number; range_pct: number;
}
export interface FlowBubble { id: string; symbol: string; side: 'BUY' | 'SELL'; size_usd: number; price: number; is_whale: boolean; venue: string; time: string }
export interface PulseRow {
  symbol: string; last_price: number; change_24h: number | null; open_interest_usd_m: number | null; oi_change_pct: number | null;
  oi_window_s: number | null; funding_rate_pct: number | null; basis_pct: number | null; pcr_oi: number | null;
  max_pain: number | null; atm_iv: number | null; buildup_status: string;
}
export interface OptionContract {
  symbol: string; base: string; expiry_ts: number; strike: number; cp: 'C' | 'P'; iv: number; oi: number;
  bid: number; ask: number; mark: number; delta: number; gamma: number; vega: number; theta: number;
}
export interface GexRow { strike: number; call_gex: number; put_gex: number; net_gex: number }
export interface OptionsAnalytics {
  source: string; simulated?: boolean; error?: string; spot: number; expiry_ts: number; expiry_days: number;
  atm_iv_pct: number | null; pcr_oi: number | null; max_pain: number | null; gamma_flip: number | null; net_gex_total: number;
  gex_assumption: string; gex_by_strike: GexRow[]; iv_smile: { strike: number; iv_pct: number }[]; chain: OptionContract[];
  rv_pct: number | null; rv_window_returns: number; iv_rv_premium_pts: number | null; contract_size: number;
}
export interface NewsItem { id: string; title: string; source: string; link: string; published: number; sentiment: number; assets: string[] }
export interface MarketNews { items: NewsItem[]; feeds: Record<string, { ok: boolean | null; error: string | null }>; count: number }
export interface CalendarEvent { time: number | null; country: string; event: string; impact: string; previous: number | null; forecast: number | null; actual: number | null }
export interface EconomicCalendar { source: string; example: boolean; note?: string; events: CalendarEvent[] }
export interface Execution { order_id: string; symbol: string; venue: string; side: string; status: string; mode: string; paper: boolean; latency_ms: number; time: string; error?: string }
export interface Payload {
  data_source: 'SIMULADO' | 'BYBIT'; simulated: boolean; feed_ready: boolean; analyzer_mode: boolean; autonomous_funnel: boolean;
  kill_switch: boolean; live_allowed: boolean; history: Candle[]; flow_bubbles: FlowBubble[]; book_wall: BookWall | null;
  order_blocks: OrderBlock[]; derivatives_pulse: PulseRow[]; funnel_signal: null | { direction: string; confidence: number; reasons: string[] };
  z_score: number | null; news_sentiment_btc: number; active_positions: Execution[]; options_analytics: OptionsAnalytics | null;
  market_news: MarketNews; economic_calendar: EconomicCalendar;
  risk_telemetry: RiskTelemetry; trust_ledger: TrustEntry[];
}

export interface RiskPosition {
  id: string; symbol: string; side: 'BUY' | 'SELL'; entry: number; qty: number; leverage: number; size_usd: number;
  stop_loss: number; initial_sl: number; stop_kind: string; paper: boolean; unrealized_pnl: number; move_pct: number; breakeven_done: boolean;
}
export interface RiskTelemetry {
  equity: number | null; equity_source: string; equity_label: string; day: string; day_start_equity: number | null;
  drawdown_pct: number; max_daily_drawdown_pct: number; breaker: { tripped: boolean; reason: string; ts: number | null };
  win_rate_rolling: number; payoff_ratio: number; stats_mode: 'NEUTRO' | 'KELLY'; trades_in_window: number; min_trades_for_kelly: number;
  kelly_full: number; kelly_fraction: number; kelly_applied: number; unrealized_pnl: number; realized_today: number;
  positions: RiskPosition[]; events: { ts: number; kind: string; msg: string }[];
}
export interface TrustEntry { ts: number; origin: string; symbol: string; venue: string; side: string; amount: number; allowed: boolean; code: string; reasons: string[]; paper: boolean }
