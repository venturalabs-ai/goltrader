"""Goltrader — Painel completo: Torneio das 10 Metodologias, Monte Carlo e execucao Bybit (Testnet/Real).

Seguranca: Testnet e o padrao. Conta REAL so opera com toggle + 'CONFIRMAR' a cada sessao.
Toda ordem passa por dashboard_components.risk.guarded_order (limites + kill switch + log sem chaves).
"""
import json
import os
import sqlite3
import subprocess
import time
from datetime import timedelta

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")  # .env explicito do goltrader

import pandas as pd
import requests
import streamlit as st

from core.config import FREE_OPENCODE_MODELS, settings
from core.hybrid_memory import HybridMemoryStore
from dashboard_components import charts as C
from dashboard_components import autostate as A
from dashboard_components import auto_panel as AP
from dashboard_components import engine as E
from dashboard_components import env_store as ES
from dashboard_components import keys
from dashboard_components import risk as R
from dashboard_components.bybit_client import BybitClient, BybitError, fetch_candles, qty_from_stake, round_step
from dashboard_components.styles import CSS

st.set_page_config(page_title="Goltrader — Painel", layout="wide", page_icon="🏆", initial_sidebar_state="expanded")
st.markdown(CSS, unsafe_allow_html=True)
ss = st.session_state
ss.setdefault("real_armed", False)
ss.setdefault("auto_on", False)
ss.setdefault("flash", [])
if "tournament" not in ss:
    ss.tournament = R.load_json(R.TOURNAMENT_FILE, None)

INTERVAL_MIN = {"1": 1, "3": 3, "5": 5, "15": 15, "30": 30, "60": 60, "120": 120, "240": 240, "D": 1440}


# ------------------------------------------------------------------ cache helpers
@st.cache_resource
def get_client(mode: str) -> BybitClient:
    return BybitClient(mode)


@st.cache_data(ttl=8, show_spinner=False)
def get_candles(symbol, interval, category, source_mode, limit=1000):
    return fetch_candles(symbol, interval, category, source_mode, limit)


@st.cache_data(ttl=20, show_spinner=False)
def conn_status(mode: str):
    c = get_client(mode)
    out = {"public": False, "auth": False, "msg": ""}
    try:
        c.server_time()
        out["public"] = True
    except Exception as e:
        out["msg"] = keys.scrub(e)
        return out
    if not c.authenticated:
        out["msg"] = "chaves da conta real não configuradas" if mode == keys.REAL else "chaves da testnet não configuradas"
        return out
    try:
        c.wallet()
        out["auth"] = True
    except Exception as e:
        out["msg"] = keys.scrub(e)
    return out


@st.cache_data(ttl=10, show_spinner=False)
def get_wallet(mode: str):
    return get_client(mode).wallet()


@st.cache_data(show_spinner=False)
def fan_cached(outcomes: tuple, payout: float, mode: str):
    return E.mc_fan(list(outcomes), payout, mode)


def flash(kind, msg):
    ss.flash.append((kind, msg))


def money(x):
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ------------------------------------------------------------------ callbacks (podem alterar widgets)
def cb_kill():
    R.set_kill(True, "botao kill switch")
    ss.auto_on = False
    A.update_state(enabled=False)
    mode = ss.get("_mode", keys.TESTNET)
    c = get_client(mode)
    if c.authenticated:
        for cat in ("linear", "spot"):
            try:
                c.cancel_all(cat)
                R.log_cancel(mode, "kill_switch", f"cancel_all {cat}", True, "")
            except Exception as e:
                R.log_cancel(mode, "kill_switch", f"cancel_all {cat}", False, str(e))
        if ss.get("kill_close_pos"):
            try:
                for p in c.positions("linear"):
                    R.guarded_order(c, mode=mode, real_armed=True, risk=R.load_risk(), source="kill_switch",
                                    category="linear", symbol=p["symbol"], side=("Sell" if p["side"] == "Buy" else "Buy"),
                                    qty=p["size"], reduce_only=True)
            except Exception as e:
                R.log_cancel(mode, "kill_switch", "close_positions", False, str(e))
    flash("error", "KILL SWITCH acionado: modo autônomo desligado e ordens abertas canceladas.")


def cb_rearm():
    R.set_kill(False)
    flash("success", "Kill switch desligado. Modo autônomo continua DESLIGADO até você ligar.")


def cb_mode_change():
    ss.real_armed = False
    ss.auto_on = False
    ss.real_toggle = False
    ss.real_confirm = ""


# ------------------------------------------------------------------ CHAVES BYBIT
KEY_SLOTS = [("real_key", "API key — conta REAL"), ("real_secret", "API secret — conta REAL"),
             ("test_key", "API key — TESTNET"), ("test_secret", "API secret — TESTNET")]


def reload_clients():
    get_client.clear()
    conn_status.clear()
    get_wallet.clear()


def cb_save_keys(prefix: str):
    vals = {k: (ss.get(f"{prefix}_{k}") or "").strip().replace(" ", "") for k, _ in KEY_SLOTS}
    for k, _ in KEY_SLOTS:  # limpa os campos imediatamente (segredo nao fica na sessao)
        ss[f"{prefix}_{k}"] = ""
    results, errors = [], []
    pairs = [(keys.REAL, vals["real_key"], vals["real_secret"]), (keys.TESTNET, vals["test_key"], vals["test_secret"])]
    to_save = {}
    for mode_, k, s_ in pairs:
        if not k and not s_:
            continue
        if not (k and s_):
            errors.append(f"{'Conta REAL' if mode_ == keys.REAL else 'Testnet'}: preencha a key E o secret.")
            continue
        if mode_ == keys.REAL:
            to_save.update({"BYBIT_API_KEY": k, "BYBIT_API_SECRET": s_, "BYBIT_REAL_API_KEY": k, "BYBIT_REAL_API_SECRET": s_})
        else:
            to_save.update({"BYBIT_TESTNET_API_KEY": k, "BYBIT_TESTNET_API_SECRET": s_})
    if not to_save and not errors:
        errors.append("Nenhuma chave preenchida.")
    saved = None
    if to_save:
        try:
            saved = ES.set_vars(to_save)
            reload_clients()
        except Exception as e:
            errors.append(f"Falha ao gravar .env: {keys.scrub(e)}")
    if saved:
        for mode_, k, s_ in pairs:
            if k and s_:
                results.append(ES.test_keys(mode_, k, s_))
        R.log_order({"action": "chaves_salvas", "names": saved["names"], "backup": saved["backup"],
                     "testes": [{"mode": r["mode"], "ok": r["ok"], "key_end": r["key_end"]} for r in results]})
    del vals, pairs, to_save
    ss["keys_result"] = {"saved": saved, "errors": errors, "results": results}


def cb_save_payout(prefix: str):
    v = float(ss.get(f"{prefix}_payout", 0.85))
    try:
        ES.set_vars({"DEFAULT_PAYOUT": f"{v:g}"})
        settings.default_payout = v
        ss["payout_result"] = ("success", f"DEFAULT_PAYOUT salvo: {v:g}")
    except Exception as e:
        ss["payout_result"] = ("error", f"Falha ao salvar payout: {keys.scrub(e)}")


def restart_panel():
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd", "/c", "start", "", "/min", str(keys.ROOT / "start_dashboard.bat")], cwd=str(keys.ROOT),
                     creationflags=flags, close_fds=True)


def keys_form(prefix: str):
    st.markdown("#### 🔑 Chaves Bybit")
    st.caption("Cole as chaves e clique em Salvar e testar. Os valores nunca são exibidos; os campos são limpos logo após salvar.")
    c_r, c_t = st.columns(2)
    c_r.markdown(f"**Conta REAL:** {ES.key_status(keys.REAL)}")
    c_t.markdown(f"**Testnet:** {ES.key_status(keys.TESTNET)}")
    a, b = st.columns(2)
    for i, (k, label) in enumerate(KEY_SLOTS):
        (a if i % 2 == 0 else b).text_input(label, type="password", key=f"{prefix}_{k}", autocomplete="off")
    st.button("💾 Salvar e testar", type="primary", key=f"{prefix}_save", on_click=cb_save_keys, args=(prefix,))
    res = ss.get("keys_result")
    if res:
        for e in res["errors"]:
            st.error(e)
        if res.get("saved"):
            st.success(f"Gravado no .env: {', '.join(res['saved']['names'])} · backup: {res['saved']['backup'] or '—'}")
        for r in res["results"]:
            amb = "REAL" if r["mode"] == keys.REAL else "TESTNET"
            if not r["ok"]:
                st.error(f"❌ {amb} ({r['key_end']}): {r['error']}")
                if r.get("hint"):
                    st.info("💡 " + r["hint"])
                continue
            inf = r["info"]
            st.success(f"✅ {amb} ({r['key_end']}): chave válida")
            st.write(f"Permissões: {inf['permissoes'] or '—'} · Somente leitura: {'SIM' if inf['somente_leitura'] else 'não'} · "
                     f"IPs liberados: {', '.join(inf['ips']) if inf['ips'] else 'qualquer IP'} · Expira: {inf['expira_em']}"
                     f"{' (' + str(inf['dias_restantes']) + ' dias)' if inf.get('dias_restantes') not in (None, '') else ''}")
            if r.get("warn"):
                st.warning(r["warn"])
            if r.get("wallet"):
                st.write(f"Saldo UNIFIED: equity {r['wallet']['equity_total']} · disponível {r['wallet']['disponivel']}")
            elif r.get("wallet_error"):
                st.warning(f"Saldo UNIFIED não lido: {r['wallet_error']}" + (f" — {r['wallet_hint']}" if r.get("wallet_hint") else ""))
        if res["results"]:
            st.caption("Chaves recarregadas no painel — não precisa reiniciar.")
    st.markdown("**Payout padrão (DEFAULT_PAYOUT)**")
    p1, p2 = st.columns([2, 1])
    p1.number_input("Payout (sugestão 0,85)", 0.05, 10.0, 0.85, 0.01, key=f"{prefix}_payout")
    p2.button("Salvar payout", key=f"{prefix}_payout_btn", on_click=cb_save_payout, args=(prefix,))
    if ss.get("payout_result"):
        getattr(st, ss["payout_result"][0])(ss["payout_result"][1])
    with st.expander("Reiniciar o painel (só se algo não atualizar)"):
        if st.button("🔄 Reiniciar painel agora", key=f"{prefix}_restart"):
            restart_panel()
            st.info("Reiniciando… recarregue a página em ~15 segundos.")


@st.dialog("Chaves Bybit", width="large")
def keys_dialog():
    keys_form("dlg")


# ------------------------------------------------------------------ SIDEBAR
with st.sidebar:
    if st.button("🔑 Chaves Bybit (colar e testar)", width="stretch"):
        keys_dialog()
    st.markdown("### 🔐 Conta Bybit")
    mode_label = st.radio("Ambiente", ["Testnet", "Real"], index=0, horizontal=True, key="mode_label", on_change=cb_mode_change)
    mode = keys.TESTNET if mode_label == "Testnet" else keys.REAL
    ss._mode = mode
    if mode == keys.REAL:
        st.toggle("Ativar ordens na conta REAL", key="real_toggle")
        st.text_input("Digite CONFIRMAR para liberar nesta sessão", key="real_confirm", type="default")
        ss.real_armed = bool(ss.get("real_toggle")) and ss.get("real_confirm", "").strip() == "CONFIRMAR"
        if not ss.real_armed:
            st.warning("Conta REAL selecionada, mas ordens BLOQUEADAS até ativar e digitar CONFIRMAR.")
    else:
        ss.real_armed = False

    st.markdown("### 📈 Mercado")
    category = st.selectbox("Produto", ["spot", "linear"], format_func=lambda x: "USDT Perpétuo (linear)" if x == "linear" else "Spot")
    symbol = st.text_input("Par Bybit", settings.default_symbol).strip().upper()
    ivals = list(INTERVAL_MIN)
    interval = st.selectbox("Timeframe (min)", ivals, index=ivals.index(settings.default_interval) if settings.default_interval in ivals else 0)
    src_label = st.radio("Fonte dos candles", ["Mainnet (dados reais)", "Mesmo ambiente da conta"], index=0)
    candle_src = keys.REAL if src_label.startswith("Mainnet") else mode
    payout = st.number_input("Payout", 0.05, 10.0, float(settings.default_payout), 0.01)
    if payout > 1.0:
        st.warning(f"Payout {payout} parece alto (opções binárias costumam ser 0,80–0,95). DEFAULT_PAYOUT no .env.")
    expiry = int(st.number_input("Expiração (candles)", 1, 60, int(settings.expiry_candles)))
    n_trials = int(st.slider("Trials Optuna por metodologia", 5, 60, 12))
    models = list(dict.fromkeys([settings.llm_model, *FREE_OPENCODE_MODELS]))
    model = st.selectbox("Modelo IA", models, index=0)

    st.markdown("### 🛡️ Limites de risco")
    rk = R.load_risk()
    rk["max_stake_pct"] = st.number_input("Stake máx. por trade (% do saldo, 0 = sem limite)", 0.0, 100.0, float(rk["max_stake_pct"]), 0.1)
    rk["max_daily_loss_usdt"] = st.number_input("Perda diária máx. (USDT, 0 = sem limite)", 0.0, 1e7, float(rk["max_daily_loss_usdt"]), 1.0)
    rk["max_open_positions"] = int(st.number_input("Máx. posições abertas (0 = sem limite)", 0, 100, int(rk["max_open_positions"])))
    if rk != R.load_risk():
        R.save_risk(rk)
    base_pct = st.number_input("Stake base (% do saldo)", 0.1, 100.0, 2.0, 0.1)
    leverage = int(st.number_input("Alavancagem (linear)", 1, 50, 1)) if category == "linear" else 1

    st.markdown("### 🤖 Autônomo 24/7")
    _ast = A.load_state()
    st.caption(f"Worker: {'ON' if _ast['enabled'] else 'OFF'} · {_ast['env'].upper()} · {'armada' if _ast['real_armed'] else 'não armada'} — controle na aba 🤖 Autônomo 24/7")
    st.markdown("### 🔄 Atualização")
    tick = st.selectbox("Atualizar gráfico a cada (s)", [10, 15, 30, 60], index=1)
    live = st.checkbox("Painel ao vivo (atualiza sozinho)", value=True)

    st.markdown("### 🛑 Emergência")
    st.checkbox("Kill switch também fecha posições", key="kill_close_pos")
    st.button("🛑 KILL SWITCH", type="primary", width="stretch", on_click=cb_kill)
    if R.kill_active():
        st.button("Rearmar (desligar kill switch)", width="stretch", on_click=cb_rearm)

client = get_client(mode)
mins = INTERVAL_MIN[interval]

# ------------------------------------------------------------------ BANNERS
for kind, msg in ss.flash:
    getattr(st, kind)(msg)
ss.flash = []
if mode == keys.REAL and ss.real_armed:
    st.markdown('<div class="gt-real">⚠️ CONTA REAL ATIVA — ORDENS COM DINHEIRO DE VERDADE ⚠️</div>', unsafe_allow_html=True)
elif mode == keys.TESTNET:
    st.markdown('<div class="gt-test">TESTNET — dinheiro de teste</div>', unsafe_allow_html=True)
if R.kill_active():
    st.markdown('<div class="gt-kill">🛑 KILL SWITCH ATIVO — nenhuma ordem nova será enviada</div>', unsafe_allow_html=True)
if not client.authenticated:
    if mode == keys.REAL:
        st.error("chaves da conta real não configuradas — preencha BYBIT_REAL_API_KEY e BYBIT_REAL_API_SECRET no .env (modelo em .env.example).")
    else:
        st.error("chaves da testnet não configuradas — preencha BYBIT_TESTNET_API_KEY e BYBIT_TESTNET_API_SECRET no .env (modelo em .env.example). "
                 "Crie as chaves em testnet.bybit.com → API.")

# ------------------------------------------------------------------ HEADER
stt = conn_status(mode)
env_name = "TESTNET" if mode == keys.TESTNET else "REAL"
if stt["auth"]:
    b_conn = f'<span class="gt-badge gt-ok">● Bybit {env_name} conectada</span>'
elif stt["public"]:
    b_conn = f'<span class="gt-badge gt-warn">● Bybit {env_name}: só dados públicos</span>'
else:
    b_conn = f'<span class="gt-badge gt-bad">● Bybit {env_name} offline</span>'
st.markdown(
    f'<div class="gt-header"><span class="gt-title">Goltrader — Torneio das 10 Metodologias &amp; Motor Bybit {"Testnet" if mode == keys.TESTNET else "REAL"}</span>'
    f'{b_conn}<span class="gt-badge">{symbol} · {interval}m · {category}</span>'
    f'<span class="gt-badge">Payout {payout:.2f}</span><span class="gt-badge">🧠 {model}</span>'
    f'<span class="gt-badge {"gt-ok" if A.load_state()["enabled"] and not R.kill_active() else ""}">Autônomo 24/7 {"ON" if A.load_state()["enabled"] else "OFF"}</span></div>',
    unsafe_allow_html=True)
if stt["msg"] and not stt["auth"]:
    _h = ES.hint_for(stt["msg"])
    st.warning(f"Conexão Bybit {env_name}: {stt['msg']}" + (f" — 💡 {_h}" if _h else "") + " · Use 🔑 Chaves Bybit para corrigir.")

t = ss.tournament
t_match = bool(t and t.get("champion") and t.get("symbol") == symbol and t.get("interval") == interval)
champ = t["champion"] if t_match else None


def wallet_safe():
    if not client.authenticated:
        return None
    try:
        return get_wallet(mode)
    except Exception as e:
        return {"error": keys.scrub(e)}


def auto_outcomes():
    st_ = R.load_json(R.AUTO_FILE, {})
    return [d.get("outcome") for d in st_.get("done", []) if d.get("mode") == mode and d.get("symbol") == symbol][-20:]


tab_auto, tab_mkt, tab_op, tab_pos, tab_hist, tab_keys, tab_sys = st.tabs(
    ["🤖 Autônomo 24/7", "📊 Mercado & Torneio", "⚡ Operar", "📂 Posições & Ordens", "📜 Histórico", "🔑 Chaves Bybit", "🧠 IA & Sistema"])

with tab_auto:
    AP.render()

with tab_mkt:
    # ------------------------------------------------------------------ LIVE SECTION (cards + chart)
    def live_section():
        try:
            df = get_candles(symbol, interval, category, candle_src)
        except Exception as e:
            st.error(f"Não consegui ler candles {symbol} ({category}) da Bybit: {keys.scrub(e)}")
            return
        sigs = E.champion_signals(df, champ) if champ else None
        sig = int(sigs.iloc[-1]) if sigs is not None else 0
        dec = E.decide(sig, champ)
        w = wallet_safe()
        avail = w.get("available") if w and "available" in w else None
        last = float(df["close"].iloc[-1])
        prev = float(df["close"].iloc[-2])

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            act = dec["action"]
            cls = "gt-call" if "CALL" in act else ("gt-put" if "PUT" in act else "gt-hold")
            st.markdown(f'<div class="gt-card"><h4>Ativo / Sinal</h4><div class="gt-sub">{symbol} · {money(last)} '
                        f'({(last / prev - 1) * 100:+.3f}%)</div><div class="gt-big {cls}">{act}</div>'
                        f'<div class="gt-sub">{"Campeã: " + champ["strategy"] if champ else "Rode o torneio para ter uma campeã"}</div></div>',
                        unsafe_allow_html=True)
            st.plotly_chart(C.sparkline(df["close"].tail(60).tolist(), C.GREEN if last >= prev else C.RED), width="stretch",
                            config={"displayModeBar": False}, key=f"spark")
        with c2:
            mc = (t or {}).get("monte_carlo", {}).get(dec["bankroll_mode"], {}) if t_match else {}
            lvl = E.bankroll_stake(dec["bankroll_mode"], 1.0, payout, auto_outcomes())["nivel"]
            st.markdown(f'<div class="gt-card"><h4>Gestão</h4><div class="gt-big">{dec["bankroll_mode"].upper()}</div>'
                        f'<div class="gt-sub">Nível soros atual: {lvl} · Ruína MC: {mc.get("ruin_prob", "—")}'
                        f'<br>Mediana final MC: {mc.get("median_final", "—")} · DD p95: {mc.get("p95_max_dd", "—")}</div></div>',
                        unsafe_allow_html=True)
        with c3:
            if avail is not None:
                base_stake = avail * base_pct / 100
                nxt = E.bankroll_stake(dec["bankroll_mode"], base_stake, payout, auto_outcomes())["stake"]
                pct = 100 * nxt / avail if avail else 0
                warn = rk["max_stake_pct"] > 0 and pct > rk["max_stake_pct"]
                st.markdown(f'<div class="gt-card"><h4>Valor</h4><div class="gt-big">{pct:.2f}% · $ {money(nxt)}</div>'
                            f'<div class="gt-sub">Disponível: $ {money(avail)} · Equity: $ {money(w.get("total_equity", 0))}</div>'
                            f'<div class="gt-sub" style="color:{"#ff4d5e" if warn else "#8b95a7"}">'
                            f'{"⚠️ acima do limite de " + str(rk["max_stake_pct"]) + "% — ordem será bloqueada" if warn else ("Limite: " + str(rk["max_stake_pct"]) + "% por trade" if rk["max_stake_pct"] > 0 else "Sem limite por trade")}'
                            f'</div></div>', unsafe_allow_html=True)
            else:
                err = (w or {}).get("error") or ("chaves da conta real não configuradas" if mode == keys.REAL else "chaves da testnet não configuradas")
                st.markdown(f'<div class="gt-card"><h4>Valor</h4><div class="gt-big">{base_pct:.2f}% · $ —</div>'
                            f'<div class="gt-sub">Saldo indisponível: {err}</div></div>', unsafe_allow_html=True)
        with c4:
            if champ:
                be = E.breakeven(payout)
                st.markdown(f'<div class="gt-card"><h4>Expectativa (EV)</h4><div class="gt-big {"gt-call" if champ["EV_por_trade"] > 0 else "gt-put"}">'
                            f'{champ["EV_por_trade"]:+.4f}/trade</div><div class="gt-sub">Win rate {champ["win_rate"]:.1%} vs breakeven {be:.1%}'
                            f'<br>p-value {champ["p_value"]:.4g} · Bonferroni {"✅" if champ["aprovado_bonferroni"] else "❌"} · {champ["trades"]} trades OOS</div></div>',
                            unsafe_allow_html=True)
            else:
                st.markdown('<div class="gt-card"><h4>Expectativa (EV)</h4><div class="gt-big">—</div>'
                            '<div class="gt-sub">Sem torneio para este par/timeframe</div></div>', unsafe_allow_html=True)

        bb_len = int((champ or {}).get("best_params", {}).get("bb_len", 20))
        bb_std = float((champ or {}).get("best_params", {}).get("bb_std", 2.0))
        st.plotly_chart(C.candles(df, sigs, bb_len, bb_std, title=f"{symbol} {interval}m — candles Bybit {'mainnet' if candle_src == keys.REAL else 'testnet'}"
                                                                f" · sinais {'da campeã ' + champ['strategy'] if champ else '(rode o torneio)'}"),
                        width="stretch", key="candles")
        st.caption(f"Último candle: {df.index[-1].tz_convert('America/Sao_Paulo'):%d/%m %H:%M} (horário de Brasília) · {len(df)} candles")


    st.fragment(live_section, run_every=(f"{tick}s" if live else None))()

    # ------------------------------------------------------------------ TORNEIO
    st.markdown("---")
    cb1, cb2 = st.columns([1, 2])
    run_btn = cb1.button("🏆 Executar Torneio Walk-Forward & Monte Carlo", type="primary", width="stretch")
    prog = cb2.empty()
    if run_btn:
        bar = prog.progress(0.0, text="Baixando candles…")
        try:
            df_t = fetch_candles(symbol, interval, category, candle_src, 1000)

            def _p(i, n, name):
                bar.progress(min(i / n, 1.0), text=f"Otimizando {name} ({i}/{n})…" if i < n else "Monte Carlo…")

            res = E.run_tournament(df_t, symbol, interval, payout, expiry, n_trials, _p)
            res["category"], res["candle_source"] = category, candle_src
            R.save_json(R.TOURNAMENT_FILE, res)
            ss.tournament = res
            bar.progress(1.0, text="Torneio concluído ✅")
            st.rerun()
        except Exception as e:
            bar.empty()
            st.error(f"Falha no torneio: {keys.scrub(e)}")

    if t and not t_match:
        st.info(f"Último torneio foi em {t.get('symbol')} {t.get('interval')}m ({t.get('ts', '')[:16]} UTC). Rode de novo para {symbol} {interval}m.")
    if t:
        champ_name = (t.get("champion") or {}).get("strategy")
        rows = []
        for r in sorted(t["leaderboard"], key=lambda x: x["EV_por_trade"], reverse=True):
            rows.append({"": "👑" if r["strategy"] == champ_name else "", "Metodologia": r["strategy"], "Trades": r["trades"],
                         "Wins": r["wins"], "Win rate": r["win_rate"], "Breakeven": r["breakeven_wr"], "EV/trade": r["EV_por_trade"],
                         "p-value": r["p_value"], "Bonferroni": "✅" if r["aprovado_bonferroni"] else "❌",
                         "Melhores parâmetros": json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.get("best_params", {}).items()})})
        st.markdown(f"#### Torneio das 10 Metodologias — {t['symbol']} {t['interval']}m · {t['candles']} candles · "
                    f"OOS 30% · {t['n_trials']} trials · {t.get('ts', '')[:16].replace('T', ' ')} UTC")
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                     column_config={"Win rate": st.column_config.NumberColumn(format="percent"),
                                    "Breakeven": st.column_config.NumberColumn(format="percent"),
                                    "EV/trade": st.column_config.NumberColumn(format="%.4f"),
                                    "p-value": st.column_config.NumberColumn(format="%.5f")})

        if t.get("champion"):
            df_now = None
            try:
                df_now = get_candles(t["symbol"], t["interval"], t.get("category", "spot"), t.get("candle_source", keys.REAL))
                sig_now = int(E.champion_signals(df_now, t["champion"]).iloc[-1])
            except Exception:
                sig_now = 0
            dec_now = E.decide(sig_now, t["champion"])
            st.markdown("#### Monte Carlo da campeã (3.000 simulações por gestão, 200 trades)")
            mcols = st.columns(3)
            for col, m in zip(mcols, ["fixed", "soros", "martingale"]):
                s = t["monte_carlo"][m]
                star = " ★" if m == dec_now["bankroll_mode"] else ""
                col.metric(f"{m.upper()}{star} — mediana final", s["median_final"],
                           help="Banca inicial 1000, stake base 2%")
                col.caption(f"Ruína: {s['ruin_prob']:.2%} · Drawdown p95: {s['p95_max_dd']:.2%}")
            fan_mode = st.radio("Leque de equity", ["fixed", "soros", "martingale"], horizontal=True,
                                index=["fixed", "soros", "martingale"].index(dec_now["bankroll_mode"]))
            fan = fan_cached(tuple(t["outcomes"][t["champion"]["strategy"]]), float(t["payout"]), fan_mode)
            if fan:
                st.plotly_chart(C.mc_fan(fan, fan_mode, fan_mode == dec_now["bankroll_mode"]), width="stretch", key="fan")
            dcol1, dcol2 = st.columns(2)
            with dcol1:
                st.markdown("**Decisão autônoma (regra do motor)**")
                st.json(dec_now)
            with dcol2:
                st.markdown("**Filtro de segurança do main.py**")
                st.write("✅ Aprovado" if t.get("main_safety_ok") else "❌ Reprovado — main.py manteria HOLD")
                st.caption(json.dumps(E.MAIN_SAFETY))
        else:
            st.warning("Nenhuma metodologia teve trades na janela OOS.")

# ------------------------------------------------------------------ ORDERS UI

orders_enabled = client.authenticated and not R.kill_active() and (mode == keys.TESTNET or ss.real_armed)


def build_and_send(side, order_type, size_mode, size_val, limit_price, tp_pct, sl_pct, reduce_only, source):
    inst = client.instrument(category, symbol)
    ref = float(limit_price) if order_type == "Limit" else client.ticker_price(category, symbol)
    lev = leverage if category == "linear" else 1
    if size_mode == "pct":
        w = get_wallet(mode)
        stake = w["available"] * size_val / 100
        qty = qty_from_stake(stake, ref, lev, inst, category)
    else:
        qty = round_step(size_val, inst["qty_step"])
        stake = float(qty) * ref / lev
    notional = float(qty) * ref
    if float(qty) <= 0 or float(qty) < inst["min_qty"]:
        R.log_order({"mode": mode, "source": source, "symbol": symbol, "side": side, "qty": qty, "status": "BLOQUEADA",
                     "reason": f"qty abaixo do mínimo {inst['min_qty']}"})
        return False, f"Quantidade {qty} abaixo do mínimo da Bybit ({inst['min_qty']}) — aumente o stake."
    if inst["min_notional"] and notional < inst["min_notional"] and not reduce_only:
        R.log_order({"mode": mode, "source": source, "symbol": symbol, "side": side, "qty": qty, "status": "BLOQUEADA",
                     "reason": f"notional {notional:.2f} < mínimo {inst['min_notional']}"})
        return False, f"Valor da ordem {notional:.2f} USDT abaixo do mínimo ({inst['min_notional']})."
    tick_sz = inst["tick"]
    tp = sl = None
    if tp_pct > 0:
        tp = round_step(ref * (1 + tp_pct / 100) if side == "Buy" else ref * (1 - tp_pct / 100), tick_sz)
    if sl_pct > 0:
        sl = round_step(ref * (1 - sl_pct / 100) if side == "Buy" else ref * (1 + sl_pct / 100), tick_sz)
    if category == "linear" and not reduce_only:
        try:
            client.set_leverage(symbol, lev)
        except Exception as e:
            flash("warning", f"Não ajustei a alavancagem: {keys.scrub(e)}")
    ok, msg, _ = R.guarded_order(client, mode=mode, real_armed=ss.real_armed, risk=rk, source=source, category=category,
                                 symbol=symbol, side=side, qty=qty, order_type=order_type,
                                 price=round_step(ref, tick_sz) if order_type == "Limit" else None,
                                 take_profit=tp, stop_loss=sl, reduce_only=reduce_only, stake_usdt=0.0 if reduce_only else stake)
    get_wallet.clear()
    return ok, msg


def show_order_result(ok, msg):
    if ok:
        st.success(msg)
    else:
        h = ES.hint_for(msg)
        st.error(f"Ordem NÃO enviada — motivo: **{ES.explain(msg)}**. {msg}" + (f" 💡 {h}" if h else ""))


with tab_op:
    if not orders_enabled:
        _why = ("kill switch" if R.kill_active() else ("chave inválida" if not client.authenticated else "conta real não confirmada"))
        st.warning(f"Envio de ordens desabilitado — motivo: **{_why}**. " + (
            "Rearme o kill switch na barra lateral." if R.kill_active() else
            ("Configure as chaves deste ambiente na aba 🔑 Chaves Bybit." if not client.authenticated else "Ative o toggle da conta REAL e digite CONFIRMAR.")))
    elif not stt["auth"]:
        st.warning(f"Chave configurada mas a Bybit recusou — motivo: **chave inválida**. {stt['msg']} 💡 {ES.hint_for(stt['msg'])}")
    _last = R.read_orders_log(1)
    if _last and _last[0].get("status") in ("BLOQUEADA", "ERRO"):
        st.caption(f"Última tentativa ({_last[0].get('source')}, {_last[0].get('ts_utc', '')[:19]} UTC): não enviada — "
                   f"{ES.explain(_last[0].get('reason', ''))}: {_last[0].get('reason', '')}")
    q1, q2 = st.columns(2)
    if q1.button(f"🟢 CALL a mercado ({base_pct:.1f}% do saldo)", width="stretch", disabled=not orders_enabled):
        try:
            ok, msg = build_and_send("Buy", "Market", "pct", base_pct, None, 0.0, 0.0, False, "manual_quick")
            show_order_result(ok, msg)
        except Exception as e:
            st.error(keys.scrub(e))
    if q2.button(f"🔴 PUT a mercado ({base_pct:.1f}% do saldo)", width="stretch", disabled=not orders_enabled):
        try:
            ok, msg = build_and_send("Sell", "Market", "pct", base_pct, None, 0.0, 0.0, False, "manual_quick")
            show_order_result(ok, msg)
        except Exception as e:
            st.error(keys.scrub(e))
    with st.form("ordem_manual"):
        st.markdown("**Ordem manual completa**")
        f1, f2, f3 = st.columns(3)
        side = f1.radio("Direção", ["Buy", "Sell"], format_func=lambda x: "CALL / Buy (compra)" if x == "Buy" else "PUT / Sell (venda)")
        otype = f2.radio("Tipo", ["Market", "Limit"], format_func=lambda x: "A mercado" if x == "Market" else "Limitada")
        size_mode = f3.radio("Tamanho por", ["pct", "qty"], format_func=lambda x: "% do saldo (stake)" if x == "pct" else "Quantidade (moeda base)")
        g1, g2, g3, g4 = st.columns(4)
        size_val = g1.number_input("Valor (% ou qtd)", 0.0, 1e9, float(base_pct), format="%.6f")
        lim_price = g2.number_input("Preço limite", 0.0, 1e9, 0.0, format="%.4f", help="Só para ordem limitada")
        tp_pct = g3.number_input("TP %", 0.0, 100.0, 0.0, 0.05)
        sl_pct = g4.number_input("SL %", 0.0, 100.0, 0.0, 0.05)
        reduce_only = st.checkbox("Somente reduzir (reduce-only)", value=False, disabled=category != "linear")
        sent = st.form_submit_button("Enviar ordem", type="primary", disabled=not orders_enabled)
    if sent:
        if otype == "Limit" and lim_price <= 0:
            st.error("Informe o preço limite.")
        else:
            try:
                ok, msg = build_and_send(side, otype, size_mode, size_val, lim_price, tp_pct, sl_pct, reduce_only, "manual")
                show_order_result(ok, msg)
            except Exception as e:
                st.error(keys.scrub(e))
    st.caption("Toda ordem passa por: kill switch → conta real armada → stake máx. % → perda diária → máx. posições. Log em logs/orders.log (sem chaves).")

with tab_pos:
    if not client.authenticated:
        st.info("Configure as chaves deste ambiente para ver saldo, posições e ordens.")
    else:
        try:
            w = get_wallet(mode)
            st.markdown(f"**Carteira {w['account_type']}** · Equity $ {money(w['total_equity'])} · Disponível $ {money(w['available'])}")
            st.dataframe(pd.DataFrame(w["coins"]), width="stretch", hide_index=True)
        except Exception as e:
            st.error(f"Saldo: {keys.scrub(e)}")
        try:
            pos = client.positions("linear")
            st.markdown(f"**Posições abertas (linear): {len(pos)}**")
            for p in pos:
                pc = st.columns([3, 1])
                pc[0].write(f"{p['symbol']} · {p['side']} · tamanho {p['size']} · entrada {p.get('avgPrice')} · "
                            f"PnL {p.get('unrealisedPnl')} · TP {p.get('takeProfit') or '—'} · SL {p.get('stopLoss') or '—'}")
                if pc[1].button("Fechar posição", key=f"close_{p['symbol']}_{p['side']}"):
                    ok, msg, _ = R.guarded_order(client, mode=mode, real_armed=ss.real_armed, risk=rk, source="manual_close",
                                                 category="linear", symbol=p["symbol"], side=("Sell" if p["side"] == "Buy" else "Buy"),
                                                 qty=p["size"], reduce_only=True)
                    show_order_result(ok, msg)
        except Exception as e:
            st.error(f"Posições: {keys.scrub(e)}")
        try:
            oo = client.open_orders(category)
            oc = st.columns([3, 1])
            oc[0].markdown(f"**Ordens abertas ({category}): {len(oo)}**")
            if oo and oc[1].button("Cancelar todas", key="cancel_all"):
                try:
                    client.cancel_all(category)
                    R.log_cancel(mode, "manual", f"cancel_all {category}", True, "")
                    st.success("Ordens canceladas.")
                except Exception as e:
                    R.log_cancel(mode, "manual", f"cancel_all {category}", False, str(e))
                    st.error(keys.scrub(e))
            for o in oo:
                c_ = st.columns([3, 1])
                c_[0].write(f"{o['symbol']} · {o['side']} {o['orderType']} · qtd {o['qty']} @ {o.get('price')} · {o['orderStatus']} · id {o['orderId'][:10]}…")
                if c_[1].button("Cancelar", key=f"cancel_{o['orderId']}"):
                    try:
                        client.cancel_order(category, o["symbol"], o["orderId"])
                        R.log_cancel(mode, "manual", f"cancel {o['orderId']}", True, "")
                        st.success("Cancelada.")
                    except Exception as e:
                        R.log_cancel(mode, "manual", f"cancel {o['orderId']}", False, str(e))
                        st.error(keys.scrub(e))
        except Exception as e:
            st.error(f"Ordens abertas: {keys.scrub(e)}")
        st.button("🔄 Atualizar", key="refresh_pos")

with tab_hist:
    if client.authenticated:
        try:
            hist = client.order_history(category, 50)
            cols = ["createdTime", "symbol", "side", "orderType", "qty", "price", "avgPrice", "orderStatus", "cumExecQty", "orderId"]
            dfh = pd.DataFrame(hist)
            if not dfh.empty:
                dfh = dfh[[c for c in cols if c in dfh.columns]]
                dfh["createdTime"] = pd.to_datetime(dfh["createdTime"].astype("int64"), unit="ms", utc=True).dt.tz_convert("America/Sao_Paulo")
            st.markdown(f"**Histórico de ordens Bybit ({env_name}, {category})**")
            st.dataframe(dfh, width="stretch", hide_index=True)
        except Exception as e:
            st.error(f"Histórico Bybit: {keys.scrub(e)}")
    st.markdown("**Log local de tentativas (logs/orders.log)**")
    st.dataframe(pd.DataFrame(R.read_orders_log(200)), width="stretch", hide_index=True)
    try:
        mem = HybridMemoryStore()
        st.markdown("**Ledger de trades (memory/goltrader_memory.db)**")
        st.dataframe(pd.DataFrame(mem.get_recent_trades(50)), width="stretch", hide_index=True)
        with sqlite3.connect(mem.db_path) as conn:
            cyc = pd.read_sql_query("SELECT timestamp_utc, symbol, champion_strategy, oos_win_rate, oos_ev, p_value FROM improvement_cycles ORDER BY id DESC LIMIT 30", conn)
        st.markdown("**Ciclos de melhoria (campeãs anteriores)**")
        st.dataframe(cyc, width="stretch", hide_index=True)
    except Exception as e:
        st.error(f"Memória: {keys.scrub(e)}")


with tab_sys:
    st.markdown(f"**Parecer da IA** (modelo `{model}`)")
    if st.button("Pedir análise da campeã à IA", disabled=not (t and t.get("champion"))):
        try:
            from openai import OpenAI
            llm = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key, timeout=60)
            summary = {"symbol": t["symbol"], "interval": t["interval"], "payout": t["payout"],
                       "leaderboard": [{k: r[k] for k in ("strategy", "trades", "win_rate", "EV_por_trade", "p_value", "aprovado_bonferroni")} for r in t["leaderboard"]],
                       "monte_carlo": t.get("monte_carlo")}
            r = llm.chat.completions.create(model=model, messages=[
                {"role": "system", "content": "Você é analista quantitativo. Responda em português, curto e objetivo, sobre risco e robustez."},
                {"role": "user", "content": "Analise este torneio e diga se a campeã é confiável para operar:\n" + json.dumps(summary)}])
            st.write(r.choices[0].message.content)
        except Exception as e:
            st.error(f"IA indisponível ({settings.llm_base_url.split('//')[-1].split('/')[0]}): {keys.scrub(e)}")
    st.markdown("**API Goltrader (porta 8080)**")
    try:
        h = requests.get("http://127.0.0.1:8080/health", timeout=2).json()
        st.success(f"API online · Bybit {h.get('bybit_mode')}")
    except Exception:
        st.info("API (uvicorn api.server:app) não está rodando — opcional.")
    st.markdown("**REA DNA (repositórios upstream indexados)**")
    summ = R.load_json(R.ROOT / "memory" / "rea_dna_summary.json", None)
    if summ:
        st.write(f"Capacidades indexadas: {summ.get('indexed_capabilities')}")
        st.json(summ.get("repositories", {}), expanded=False)
    with st.expander("Atualizar DNA REA (git pull de ~1 GB de repositórios, demorado)"):
        if st.checkbox("Confirmo que quero atualizar agora") and st.button("Rodar harvest"):
            with st.spinner("Clonando/atualizando e indexando…"):
                from rea_bridge.harvester import REAHybridHarvester
                st.json(REAHybridHarvester().run_full_harvest())

with tab_keys:
    keys_form("tab")
    st.markdown("---")
    st.markdown("**Variáveis de ambiente (só nomes — valores nunca são exibidos)**")
    st.dataframe(pd.DataFrame([{"variável": k, "status": v} for k, v in keys.env_names_status().items()]), width="stretch", hide_index=True)
    for m_, lbl in ((keys.TESTNET, "Testnet"), (keys.REAL, "Real")):
        _, _, srcn = keys.resolve(m_)
        st.write(f"{lbl}: {'usando ' + srcn if srcn else ('chaves da conta real não configuradas' if m_ == keys.REAL else 'chaves da testnet não configuradas')}")
    st.caption("Modelo de preenchimento em .env.example. Edite só o arquivo .env no seu computador e reinicie o painel.")
