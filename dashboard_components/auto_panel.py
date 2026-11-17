"""Aba principal: Autonomo 24/7 (controla o worker autotrader.py via state/autotrader.json)."""
import subprocess
import time

import pandas as pd
import streamlit as st

from dashboard_components import autostate as A
from dashboard_components import risk as R

ROOT = A.ROOT


def _start_worker():
    A.STOP_FLAG.unlink(missing_ok=True)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([str(ROOT / ".venv" / "Scripts" / "pythonw.exe"), "autotrader.py"], cwd=str(ROOT), creationflags=flags,
                     close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


def _stop_worker():
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([str(ROOT / ".venv" / "Scripts" / "python.exe"), "autotrader.py", "--stop"], cwd=str(ROOT), creationflags=flags)


def _cb_toggle(on: bool):
    A.update_state(enabled=on)


def _cb_arm():
    if (st.session_state.get("auto_confirm") or "").strip() == "CONFIRMAR":
        A.update_state(real_armed=True, armed_at=pd.Timestamp.now(tz="UTC").isoformat())
        st.session_state["auto_msg"] = ("success", "Conta REAL armada para o autônomo (fica salva, inclusive após reiniciar).")
    else:
        st.session_state["auto_msg"] = ("error", "Digite CONFIRMAR exatamente para armar a conta real.")
    st.session_state["auto_confirm"] = ""


def _cb_disarm():
    A.update_state(real_armed=False, armed_at=None)
    st.session_state["auto_msg"] = ("info", "Conta real desarmada.")


def status_block():
    stt, rt = A.load_state(), A.load_runtime()
    age = A.heartbeat_age()
    running = age < 60 and A.pid_alive(rt.get("pid"))
    trading = stt["enabled"] and not R.kill_active() and (stt["env"] == "testnet" or stt["real_armed"]) and not stt["dry_run"]
    c = st.columns(6)
    c[0].metric("Worker", "RODANDO" if running else ("PARADO" if A.STOP_FLAG.exists() else "SEM SINAL"),
                f"heartbeat {age:.0f}s" if age < 1e8 else "nunca rodou", delta_color="off")
    c[1].metric("Autônomo", "ON" if stt["enabled"] else "OFF", "operando" if (trading and running) else "não opera", delta_color="off")
    c[2].metric("Ambiente", stt["env"].upper(), "ARMADA ✅" if stt["real_armed"] else ("—" if stt["env"] == "testnet" else "não armada"),
                delta_color="off")
    w = rt.get("wallet") or {}
    c[3].metric("Saldo disponível", f"{w.get('available', 0):.2f} USDT" if w else "—", f"equity {w.get('equity', 0):.2f}" if w else "", delta_color="off")
    c[4].metric("Trades hoje", rt.get("trades_today", 0), f"abertos {len(rt.get('open', []))}", delta_color="off")
    pnl = rt.get("pnl_today")
    c[5].metric("PnL realizado hoje", f"{pnl:+.4f} USDT" if pnl is not None else "—", f"ledger do robô · {rt.get('closed_today', 0)} saídas", delta_color="off")
    if stt["env"] == "real" and stt["real_armed"] and stt["enabled"]:
        st.markdown('<div class="gt-real">⚠️ AUTÔNOMO OPERANDO NA CONTA REAL ⚠️</div>', unsafe_allow_html=True)
    if stt["dry_run"]:
        st.info("Modo simulação (dry-run) ligado: o worker NÃO envia ordens, só registra o que faria.")
    if rt.get("last_reason"):
        st.markdown(f"**Último motivo de não enviar:** {rt['last_reason']}")

    st.markdown("#### 🔎 Oportunidades (todas as moedas do universo)")
    opp = rt.get("opportunities", [])
    if opp:
        df = pd.DataFrame(opp)
        cols = [c_ for c_ in ["symbol", "decision", "score", "edge_bruto_pct", "taxa_rt_pct", "edge_liq_pct", "hits", "best", "champion",
                              "methods", "rsi", "ret15", "z_vwap",
                              "order_qty", "order_usdt", "eligible", "reason"] if c_ in df.columns]
        st.dataframe(df[cols].rename(columns={"symbol": "par", "decision": "decisão", "hits": "detectores", "best": "metodologia do sinal",
                                              "champion": "melhor compra", "methods": "valid. c/ sinal",
                                              "edge_bruto_pct": "edge bruto %", "taxa_rt_pct": "taxas ida+volta %", "edge_liq_pct": "edge líquido %", "order_qty": "qtd ordem",
                                              "order_usdt": "ordem USDT", "eligible": "cabe no saldo", "reason": "motivo"}),
                     width="stretch", hide_index=True)
    else:
        st.caption("Ainda sem leitura — o worker treina as moedas (alguns segundos cada) e avalia a cada candle novo.")

    st.markdown("#### 💰 Moedas elegíveis (mínimo da Bybit + 0,15 USDT)")
    syms = rt.get("symbols", {})
    if syms:
        rows = [{"par": s, "preço": v["price"], "qtd mín": v["min_qty"], "passo": v["qty_step"], "notional mín": v["min_notional"],
                 "mínimo Bybit USDT": v["min_req"], "ordem (qtd)": v["order_qty"], "ordem USDT": v["order_usdt"],
                 "custo c/ taxa": v["margin"], "elegível": "✅" if v["eligible"] else "❌", "obs": v["why"],
                 "melhor compra": (rt.get("champions", {}).get(s) or {}).get("strategy"),
                 "edge líq %": (rt.get("champions", {}).get(s) or {}).get("long_net_pct"),
                 "validadas": (rt.get("champions", {}).get(s) or {}).get("n_validated")} for s, v in syms.items()]
        st.dataframe(pd.DataFrame(rows).sort_values("ordem USDT"), width="stretch", hide_index=True)
    st.markdown("#### 📂 Posições do robô (só vende o que ele comprou)")
    st.dataframe(pd.DataFrame(rt.get("open", [])), width="stretch", hide_index=True)
    st.markdown("#### 📜 Eventos recentes do worker")
    st.dataframe(pd.DataFrame(list(reversed(rt.get("events", [])))[:60]), width="stretch", hide_index=True)
    with st.expander("Trades concluídos"):
        st.dataframe(pd.DataFrame(list(reversed(rt.get("done", [])))[:100]), width="stretch", hide_index=True)


def render():
    stt = A.load_state()
    ss = st.session_state
    if ss.get("auto_msg"):
        k, m = ss.pop("auto_msg")
        getattr(st, k)(m)
    st.markdown("### 🤖 Autônomo 24/7 · Bybit SPOT (só compra) — opera sozinho, mesmo com o navegador fechado")
    b1, b2, b3, b4 = st.columns(4)
    if stt["enabled"]:
        b1.button("⏹️ DESLIGAR AUTÔNOMO", type="primary", width="stretch", on_click=_cb_toggle, args=(False,))
    else:
        b1.button("▶️ LIGAR AUTÔNOMO", type="primary", width="stretch", on_click=_cb_toggle, args=(True,),
                  disabled=R.kill_active())
    env = b2.selectbox("Ambiente do worker", ["real", "testnet"], index=0 if stt["env"] == "real" else 1, key="auto_env")
    if env != stt["env"]:
        A.update_state(env=env, real_armed=False if env == "testnet" else stt["real_armed"])
        st.rerun()
    if b3.button("▶️ Iniciar worker", width="stretch"):
        _start_worker()
        st.toast("Worker iniciando…")
    if b4.button("⏸️ Parar worker", width="stretch"):
        _stop_worker()
        st.toast("Parando worker…")
    if stt["env"] == "real":
        if stt["real_armed"]:
            a1, a2 = st.columns([3, 1])
            a1.success(f"Conta REAL armada desde {str(stt.get('armed_at'))[:19]} UTC")
            a2.button("Desarmar conta real", on_click=_cb_disarm, width="stretch")
        else:
            a1, a2 = st.columns([3, 1])
            a1.text_input("Para operar na conta REAL, digite CONFIRMAR", key="auto_confirm")
            a2.button("Armar conta REAL", type="primary", on_click=_cb_arm, width="stretch")

    st.fragment(status_block, run_every="5s")()

    with st.expander("⚙️ Configurações do autônomo (0 = sem limite)", expanded=False):
        with st.form("auto_cfg"):
            c1, c2, c3, c4 = st.columns(4)
            new = dict(stt)
            new["universe"] = [s.strip().upper() for s in c1.text_area("Moedas (USDT perpétuo)", ", ".join(stt["universe"])).split(",") if s.strip()]
            new["interval"] = c2.selectbox("Timeframe (min)", ["1", "3", "5", "15", "30", "60"], index=["1", "3", "5", "15", "30", "60"].index(stt["interval"]) if stt["interval"] in ["1", "3", "5", "15", "30", "60"] else 0)
            new["expiry_candles"] = int(c3.number_input("Expiração (candles)", 1, 120, int(stt["expiry_candles"])))
            new["extra_usdt"] = c4.number_input("Acima do mínimo (USDT)", 0.0, 100.0, float(stt["extra_usdt"]), 0.01)
            d1, d2, d3, d4 = st.columns(4)
            new["fee_pct"] = d1.number_input("Taxa spot por lado %", 0.0, 1.0, float(stt.get("fee_pct", 0.1)), 0.01)
            new["tp_pct"] = d2.number_input("Take profit % (0 = sem)", 0.0, 50.0, float(stt["tp_pct"]), 0.05)
            new["sl_pct"] = d3.number_input("Stop loss % (0 = sem)", 0.0, 50.0, float(stt["sl_pct"]), 0.05)
            new["dry_run"] = d4.checkbox("Simulação (não envia ordens)", value=bool(stt["dry_run"]))
            st.markdown("**Limites opcionais — 0 = sem limite**")
            e1, e2, e3, e4, e5 = st.columns(5)
            new["max_open_positions"] = int(e1.number_input("Máx. posições", 0, 100, int(stt["max_open_positions"])))
            new["max_daily_loss_usdt"] = e2.number_input("Perda diária máx. USDT", 0.0, 1e7, float(stt["max_daily_loss_usdt"]), 1.0)
            new["max_trades_per_hour"] = int(e3.number_input("Máx. trades/hora", 0, 10000, int(stt["max_trades_per_hour"])))
            new["cooldown_min"] = e4.number_input("Cooldown por par (min)", 0.0, 1440.0, float(stt["cooldown_min"]), 0.5)
            new["max_stake_pct"] = e5.number_input("Margem máx. % saldo/trade", 0.0, 100.0, float(stt["max_stake_pct"]), 0.5)
            st.markdown("**Validação das entradas**")
            f1, f2, f3, f4, f5 = st.columns(5)
            new["min_net_edge_pct"] = f1.number_input("Edge líquido mín. % (após taxas)", -5.0, 10.0, float(stt.get("min_net_edge_pct", 0.0)), 0.01)
            new["min_long_trades"] = int(f2.number_input("Mín. compras OOS", 1, 1000, int(stt.get("min_long_trades", 5))))
            new["score_min"] = f3.number_input("Score mínimo", -10.0, 100.0, float(stt["score_min"]), 0.01)
            new["require_bonferroni"] = f4.checkbox("Só Bonferroni", value=bool(stt["require_bonferroni"]))
            f5.caption("Spot: só compra. Venda = saída da posição do robô.")
            g1, g2, g3, g4 = st.columns(4)
            new["retrain_every_min"] = int(g1.number_input("Re-treinar a cada (min)", 5, 1440, int(stt["retrain_every_min"])))
            new["n_trials"] = int(g2.number_input("Trials Optuna", 2, 60, int(stt["n_trials"])))
            new["use_orderbook"] = g3.checkbox("Usar livro de ofertas", value=bool(stt["use_orderbook"]))
            new["kill_close_all"] = g4.checkbox("Kill switch fecha posições", value=bool(stt["kill_close_all"]))
            if st.form_submit_button("💾 Salvar configurações", type="primary"):
                cur = A.load_state()
                for k_ in ("enabled", "real_armed", "armed_at", "env"):
                    new[k_] = cur[k_]
                A.save_state(new)
                st.success("Configurações salvas — o worker aplica no próximo ciclo.")
