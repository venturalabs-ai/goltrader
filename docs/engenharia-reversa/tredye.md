# Engenharia reversa — Tredye Market Pulse

Data da análise: 17/11/2026 (BRT). Ferramenta: **REA 6.9.0** (`capture-browser-scenario`, `list-browser-targets`,
`analyze-web-bundle`, `inspect-analysis-view`). Escopo: **somente páginas públicas, sem login**, sem contornar
bloqueios. O `robots.txt` do site proíbe `/api/`, `/admin/` e `/broker/callback/`: **nada disso foi acessado
diretamente** (o site público não chamou `/api/*` no modo de exemplo). Objetivo: entender e **reimplementar com
código próprio** — nenhum código do Tredye foi copiado para este repositório.

Páginas capturadas (PNG em `capturas/`): `/pulse/?view=flow`, `/markets/`, `/options/`, `/liquidity/`,
`/strength/`, `/radar/`. Cenários REA reprodutíveis em `rea-evidencias/cenario-*.json`; lista das requisições de
rede em `rea-evidencias/rede-paginas-publicas.txt`; formato (só nomes de campos) dos JSON em
`rea-evidencias/estrutura-json-amostras.json`.

## 1. Stack observada
- **Next.js (App Router, Turbopack, RSC)** + **React**; CSS único ~200 KB; fontes self-hosted; tema claro.
- Gráficos: o **Sankey de fluxo é desenhado em `<canvas>`** (`role="img"` + `aria-label` com a manchete; nenhuma lib
  d3-sankey/echarts/recharts nos bundles — render próprio com curvas de largura proporcional ao fluxo, partículas
  animadas via `requestAnimationFrame`, brilho em "surto"). Há também canvases para **treemap setorial** (tamanho =
  dinheiro negociado, cor = % var.) e **"money rotation"**. Candles/perfil de volume (Liquidity, Radar) usam
  **lightweight-charts (TradingView)**. KPIs, medidores (PCR), radar de 8 eixos, barras e bolhas dos "fastest
  movers" são SVG/DOM (≈45–77 `<svg>` por página). Acessibilidade: cada gráfico traz texto alternativo.
- Analytics: GA4, AdSense, Cloudflare (CDN). Login de corretora indiano (Kite/Zerodha, Fyers, Upstox) por OAuth
  no servidor; sem login o site roda em **modo "sample/demo"** com dados de exemplo (rótulo "Example").
- Endpoints estáticos do modo exemplo: `/sample/config.json`, `/sample/data/{pulse,flow,strength,symbols}.json`,
  `/sample/data/options@symbol-<SYM>.json`, `/sample/data/liq@symbol-<SYM>__tf-<tf>.json`,
  `/sample/data/radar@symbol-<SYM>.json`. Config remota `data.tredye.com/data/config.json` define telas
  (`pulse, opt, radar, calm, crypto, liq, oiflow, screen, strength, journal, academy, coffee`), listas de
  símbolos e **intervalos de refresh** (pulse 17 s ao vivo / 10 min fechado; flow 27 s; opções 62 s; radar 122 s;
  liquidez 92 s; strength 62 s). Endpoints internos citados no bundle (não acessados): `/api/track`,
  `/api/auth/me`, `/api/broker/kite/{check,session}`.

## 2. Layout e componentes (Pulse, `?view=flow`)
1. **Cabeçalho**: navegação (Markets, Options, Stocks, Crypto & Gold, Learn, Play), busca global (cmd-K).
2. **Hero "Market Pulse"**: manchete em linguagem natural ("Dinheiro saiu de X e foi para Y. Fluxo líquido +₹N"),
   cartões Mood (0–100 + rótulo), NIFTY, BANKNIFTY, India VIX com regime.
3. **Abas**: Overview · Money flow · Order flow; selo "mercado fechado – última sessão".
4. **KPIs**: Inflow, Outflow, Net flow (selo "Net buying"), Breadth (231:267, barra adv/dec).
5. **Sankey de fluxo por setor** (coluna esquerda = setores com mini-barras entrada/saída; coluna central = barra
   verde (INFLOW) e vermelha (OUTFLOW); direita = top 8–10 ações de entrada/saída com ₹ cr). Espessura = ₹ fluxo,
   velocidade das partículas = intensidade do volume, brilho = surto.
6. **Fastest movers**: bolhas de entrada/saída mais rápidas (tamanho = fluxo, rótulo = % variação).
7. Outras telas: **Options** (manchete de faixa esperada, range bar, tug-of-war, cadeia de OI com muros put/call e
   max pain, medidor PCR, smile de IV, straddle), **Liquidity** (candles + OI walls + perfil de volume POC/HVN/LVN +
   VWAP ancorada + build-up de futuros; cartões Suporte/Resistência/Build-up com score 0–100), **Strength**
   (dispersão de todas as ações ordenadas por força relativa vs NIFTY, listas "mais fortes/fracas", setores),
   **Radar** (8 sinais → score −10..+10, faixa esperada por ATR, níveis S/R/POC/VWAP).

## 3. Modelo de dados (amostra pública, dados ilustrativos)
- `pulse.json`: `mood{score,label,parts{breadth,flow,nifty,vix}}`, `headline`, `indices[]`
  (spot, chg, bias, norm, expiry, range, iv1, walls, conviction, spark, vix{last,prev,yhi,ylo,pctile,regime},
  atr{points,pct,median60,ratio,state}), `vix`, `sectors[]`, `adv/dec`, `inflow/outflow`, `ts/open`.
- `flow.json`: `inflow/outflow`, `adv/dec/unch`, `sectors[]` (g, f=fluxo líquido, fin, fout, tv=turnover, n, adv,
  dec, p=% var, spd, dens, surge), `stocks[]` (s, g, p, lp, tv, **f**, **w**, **clv**, **rv**, fo, doi, bu=buildup
  "Long buildup/…", spd, dens, surge), `top_in/top_out`, `fast_in/fast_out`, `shockers`, `surges`, `oi_unusual`,
  `session_frac`, `summary`.
- `options@`, `liq@`, `radar@`, `strength.json`: cadeia por strike (coi/poi, cchg/pchg, civ/piv), muros, max pain,
  PCR, IV ATM, straddle, `chips`(suporte/resistência/bias), `axes[]` do radar, `comps[]` (sinais com score e razão),
  `series` 15m/1h/1d (close+VWAP), `bull/bear` com `setup{support,resistance,liquidity,invalid,rr}`.

## 4. Fórmulas — o que dá para afirmar
Os cálculos reais rodam no servidor do Tredye (não visíveis). Pelo que os dados de exemplo mostram:
- **Fluxo setorial** `f_setor = Σ f_ação` (conferido: soma dos stocks = f do setor ±0,1). `fin` = soma dos
  positivos, `fout` = soma dos negativos; `inflow/outflow` globais = soma de todas as ações (Σ+ / Σ−).
- **Fluxo por ação** `f ≈ turnover × c`, com c fortemente correlacionado (r≈0,95) a `w` (peso/pressão compradora
  0..1) e menos a `clv` (close location value, 0..1) e `rv` (volume relativo). É uma **estimativa de pressão
  compradora/vendedora ponderada pelo turnover** ("price × volume estimate", como diz o próprio site).
- **Humor (mood 0–100)**: combinação de 4 componentes normalizados (breadth, flow, nifty, vix) em [−1,1];
  rótulos tipo "Constructive". **Pesos não são deriváveis** de uma única amostra → definiremos os nossos (§ plano).
- **VIX regime/percentil**, **ATR ratio (ATR / mediana 60)** → "expanding/contracting", **PCR**, **max pain**,
  **faixa esperada = ± straddle/IV 1σ até o vencimento**, **conviction** 0–100 (concordância entre sinais).
- Strength: força = movimento do ativo vs NIFTY normalizado por ADR (ADR 50 dias; impulso ≥ 0,35 ADR).

## 5. Reimplementação própria para cripto (Bybit) — especificação
Ver `docs/PLANO-HIBRIDO.md` §4. Mapeamento: setor → **categoria cripto** (L1, L2, DeFi, IA, Meme, Exchange, Gaming,
RWA, Stable…), ação → par USDT (spot/linear perp), turnover → `turnover` 24h/candle, ₹ cr → USDT, breadth →
adv/dec do universo, VIX → **índice de volatilidade** (DVOL/IV de opções BTC ou ATR% próprio), NIFTY → BTC/ETH.

## 6. O que o REA capturou e o que não
- ✅ Capturou: 6 páginas (screenshot, DOM, árvore de acessibilidade, ~40–85 requisições cada com corpo),
  bundle estático Next.js (40 scripts, 5 endpoints, fingerprints React/Next.js), JSON de amostras.
- ❌ Não capturou/limitações: lógica do servidor (fórmulas exatas, pesos do mood); dados reais/ao vivo (exigem
  login em corretora — não feito); 1 script excedeu o deadline de análise estática por script (30 s) → parcial;
  screenshot é só da viewport (1440×2000), não de página inteira; eventos "completos" ficaram `incomplete`
  por limite de memória de eventos (1 evento descartado por página) sem impacto nas conclusões.
