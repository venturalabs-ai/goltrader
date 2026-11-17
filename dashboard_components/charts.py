"""Graficos plotly (tema escuro)."""
import numpy as np
import pandas as pd
import plotly.graph_objects as go

BG, GRID, GREEN, RED, GOLD, BLUE = "#0e1117", "#1f2633", "#26d07c", "#ff4d5e", "#f5c542", "#4da3ff"


def _layout(fig, h=420, title=None):
    fig.update_layout(template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG, height=h,
                      margin=dict(l=10, r=10, t=40 if title else 10, b=10), title=title,
                      legend=dict(orientation="h", y=1.02, x=0, bgcolor="rgba(0,0,0,0)"),
                      xaxis=dict(gridcolor=GRID), yaxis=dict(gridcolor=GRID))
    return fig


def candles(df: pd.DataFrame, signals: pd.Series = None, bb_len: int = 20, bb_std: float = 2.0, show: int = 150, title=None):
    d = df.tail(show + bb_len).copy()
    sma = d["close"].rolling(bb_len).mean()
    std = d["close"].rolling(bb_len).std()
    d, sma, std = d.tail(show), sma.tail(show), std.tail(show)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=d.index, open=d["open"], high=d["high"], low=d["low"], close=d["close"], name="Candles",
                                 increasing_line_color=GREEN, decreasing_line_color=RED))
    fig.add_trace(go.Scatter(x=d.index, y=sma + bb_std * std, line=dict(color="rgba(77,163,255,.5)", width=1), name="BB sup"))
    fig.add_trace(go.Scatter(x=d.index, y=sma - bb_std * std, line=dict(color="rgba(77,163,255,.5)", width=1), name="BB inf",
                             fill="tonexty", fillcolor="rgba(77,163,255,.06)"))
    fig.add_trace(go.Scatter(x=d.index, y=sma, line=dict(color=GOLD, width=1.5), name=f"MM{bb_len}"))
    if signals is not None:
        s = signals.reindex(d.index).fillna(0)
        call, put = d[s == 1], d[s == -1]
        fig.add_trace(go.Scatter(x=call.index, y=call["low"] * 0.9995, mode="markers", name="CALL",
                                 marker=dict(symbol="triangle-up", size=11, color=GREEN)))
        fig.add_trace(go.Scatter(x=put.index, y=put["high"] * 1.0005, mode="markers", name="PUT",
                                 marker=dict(symbol="triangle-down", size=11, color=RED)))
    fig.update_layout(xaxis_rangeslider_visible=False)
    return _layout(fig, 460, title)


def sparkline(values, color=GREEN):
    fig = go.Figure(go.Scatter(y=list(values), mode="lines", line=dict(color=color, width=2), fill="tozeroy",
                               fillcolor="rgba(38,208,124,.08)" if color == GREEN else "rgba(255,77,94,.08)"))
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", height=60,
                      margin=dict(l=0, r=0, t=0, b=0), xaxis=dict(visible=False), showlegend=False,
                      yaxis=dict(visible=False, range=[min(values) * 0.9995, max(values) * 1.0005]))
    return fig


def mc_fan(fan: dict, mode: str, highlight: bool, initial: float = 1000.0):
    fig = go.Figure()
    x, p = fan["x"], fan["pct"]
    for path in fan["sample"]:
        fig.add_trace(go.Scatter(x=x, y=path, line=dict(color="rgba(150,150,170,.12)", width=1), showlegend=False, hoverinfo="skip"))
    col = GOLD if highlight else BLUE
    fig.add_trace(go.Scatter(x=x, y=p[95], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=p[5], fill="tonexty", fillcolor="rgba(245,197,66,.10)" if highlight else "rgba(77,163,255,.10)",
                             line=dict(width=0), name="P5–P95"))
    fig.add_trace(go.Scatter(x=x, y=p[75], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=p[25], fill="tonexty", fillcolor="rgba(245,197,66,.22)" if highlight else "rgba(77,163,255,.22)",
                             line=dict(width=0), name="P25–P75"))
    fig.add_trace(go.Scatter(x=x, y=p[50], line=dict(color=col, width=3), name="Mediana"))
    fig.add_hline(y=initial, line=dict(color="#888", dash="dot"))
    title = f"{mode.upper()}{'  ★ decisão autônoma' if highlight else ''}"
    fig.update_xaxes(title="trades")
    fig.update_yaxes(title="banca (base 1000)")
    return _layout(fig, 320, title)
