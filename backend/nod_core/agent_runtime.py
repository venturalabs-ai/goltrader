"""Núcleo de confiança do agente (cofre de segredos + portão de confiança + ledger).

Inspirado no conceito de agente que "pede antes de agir" do projeto n8n-io/nod (Apache-2.0). NENHUM código do nod
foi copiado: é uma reimplementação independente (ver NOTICE.md).

- NodSecretVault: lê segredos só de variáveis de ambiente; nunca serializa/exibe valores (só booleanos).
- NodTrustGate: PONTO ÚNICO de aprovação de qualquer ordem (cesta manual, estrutura de opções, funil autônomo).
- TrustDecision: resultado auditável (sem segredos) registrado no ledger.
- RedactingFilter: remove valores de segredos de qualquer log.
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Deque, Dict, List, Optional

from config import env_bool, env_float, env_int, env_str

SECRET_NAMES = (
    "GOLTRADER_API_TOKEN", "BYBIT_API_KEY", "BYBIT_API_SECRET", "IQ_EMAIL", "IQ_PASSWORD",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
)
MIN_SECRET_LEN = 6  # valores curtos demais gerariam falsos positivos na redação


class NodSecretVault:
    """Cofre somente-leitura baseado em ambiente. Nunca serializa segredos."""

    def __init__(self, names=SECRET_NAMES):
        self._names = tuple(names)

    # --- uso interno (código que precisa do valor)
    def get(self, name: str) -> str:
        if name not in self._names:
            raise KeyError(f"segredo não registrado: {name}")
        return os.getenv(name, "")

    def has(self, name: str) -> bool:
        return bool(self.get(name))

    # --- seguro para exibir
    def masked_status(self) -> Dict[str, bool]:
        return {n: bool(os.getenv(n, "")) for n in self._names}

    def redact(self, text: str) -> str:
        out = str(text)
        for n in self._names:
            v = os.getenv(n, "")
            if len(v) >= MIN_SECRET_LEN:
                out = out.replace(v, "***")
        return out

    def __repr__(self) -> str:  # nunca vaza
        return f"NodSecretVault(secrets={sum(self.masked_status().values())}/{len(self._names)} definidos)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("NodSecretVault não pode ser serializado")

    def __getstate__(self):
        raise TypeError("NodSecretVault não pode ser serializado")


class RedactingFilter(logging.Filter):
    """Filtro de logging que remove valores de segredos de mensagens e argumentos."""

    def __init__(self, vault: NodSecretVault):
        super().__init__()
        self.vault = vault

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = self.vault.redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def install_log_redaction(vault: NodSecretVault) -> None:
    """Redação global: todo LogRecord criado a partir daqui tem valores de segredos trocados por ***."""
    prev = logging.getLogRecordFactory()
    if getattr(prev, "_gt_redacting", False):
        return

    def factory(*args, **kwargs):
        rec = prev(*args, **kwargs)
        try:
            rec.msg = vault.redact(rec.getMessage())
            rec.args = ()
        except Exception:
            pass
        return rec

    factory._gt_redacting = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


@dataclass
class TrustDecision:
    allowed: bool
    code: str = "OK"
    reasons: List[str] = field(default_factory=list)
    paper: bool = True  # modo de execução decidido pelo portão
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return asdict(self)

    @property
    def message(self) -> str:
        return "; ".join(self.reasons)


def allowed_venues() -> set:
    raw = env_str("GOLTRADER_ALLOWED_VENUES", "BYBIT_PERP,BYBIT_SPOT,BYBIT_OPT,IQ_BINARY,IQ_DIGITAL")
    venues = {v.strip().upper() for v in raw.split(",") if v.strip()}
    if not env_bool("GOLTRADER_ENABLE_IQ", False):  # IQ Option: API não oficial -> desligada por padrão
        venues = {v for v in venues if not v.startswith("IQ_")}
    return venues


class NodTrustGate:
    """Único ponto de decisão antes de qualquer ordem sair do sistema."""

    def __init__(self, state, ledger_size: int = 200):
        self.state = state
        self.ledger: Deque[Dict] = deque(maxlen=ledger_size)

    def verify_order(self, order, origin: str = "MANUAL") -> TrustDecision:
        st = self.state
        st.roll_day()
        reasons: List[str] = []
        code = "OK"

        def deny(c: str, msg: str):
            nonlocal code
            if code == "OK":
                code = c
            reasons.append(msg)

        paper = st.analyzer_mode or (origin == "FUNIL" and st.data_source != "BYBIT")

        if order.venue not in allowed_venues():
            if order.venue.startswith("IQ_"):
                deny("VENUE", f"venue {order.venue} desabilitada (IQ Option exige GOLTRADER_ENABLE_IQ=true; API não oficial)")
            else:
                deny("VENUE", f"venue {order.venue} fora de GOLTRADER_ALLOWED_VENUES")
        if st.kill_switch:
            deny("KILL_SWITCH", "KILL_SWITCH ativo: novas ordens bloqueadas")
        risk = getattr(st, "risk", None)
        if risk is not None and risk.breaker_tripped:
            deny("BREAKER", f"circuit breaker de drawdown diário ativo ({risk.breaker_reason})")
        limit = env_float("GOLTRADER_MAX_DAILY_LOSS_USD", 200)
        if limit > 0 and st.daily_pnl_usd <= -limit:
            deny("DAILY_LOSS", f"perda diária máxima atingida (US$ {limit:.2f})")

        if order.venue == "BYBIT_PERP":
            max_lev = env_int("GOLTRADER_MAX_LEVERAGE", 10)
            if (order.leverage or 1) > max_lev:
                deny("LEVERAGE", f"alavancagem {order.leverage}x acima do máximo {max_lev}x")
            if st.prices.get(order.symbol, 0) <= 0:
                deny("NO_PRICE", f"sem preço de referência para {order.symbol} (motor sem ticks)")
        if order.venue == "BYBIT_OPT" and not order.price:
            deny("OPT_PRICE", "ordem de opção exige preço limite")

        if not paper:
            if not env_bool("GOLTRADER_ALLOW_LIVE", False):
                deny("ALLOW_LIVE", "GOLTRADER_ALLOW_LIVE não habilitado")
            if order.venue == "BYBIT_PERP" and risk is not None:
                eq = risk.wallet_equity  # LIVE exige o saldo REAL lido da corretora (nunca o paper equity)
                if eq is None:
                    deny("EQUITY", "saldo da corretora ainda não lido (equity real desconhecido)")
                else:
                    margin = order.amount * st.prices.get(order.symbol, 0) / max(order.leverage or 1, 1)
                    cap = eq * risk.cfg.max_margin_frac
                    if margin > cap:
                        deny("MARGIN", f"margem US$ {margin:.2f} acima do teto {risk.cfg.max_margin_frac:.0%} do equity (US$ {cap:.2f})")

        if code == "OK":  # reserva do rate limit só se todo o resto passou (atômico: sem await)
            now = time.time()
            while st.order_times and now - st.order_times[0] > 60:
                st.order_times.popleft()
            per_min = env_int("GOLTRADER_MAX_ORDERS_PER_MIN", 30)
            if len(st.order_times) >= per_min:
                deny("RATE_LIMIT", f"limite de {per_min} ordens/min atingido")
            else:
                st.order_times.append(now)

        decision = TrustDecision(allowed=code == "OK", code=code, reasons=reasons or ["aprovada"], paper=paper)
        self.ledger.appendleft({
            "ts": int(decision.ts * 1000), "origin": origin, "symbol": order.symbol, "venue": order.venue,
            "side": order.side, "amount": order.amount, "allowed": decision.allowed, "code": code,
            "reasons": decision.reasons, "paper": paper,
        })
        return decision

    def recent(self, n: int = 20) -> List[Dict]:
        return list(self.ledger)[:n]


class TelegramNotifier:
    """Notificações opcionais (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID). Sem as variáveis, não faz nada."""

    def __init__(self, vault: NodSecretVault, sender=None):
        self.vault = vault
        self._sender = sender  # injetável nos testes (sem rede)

    @property
    def enabled(self) -> bool:
        return self.vault.has("TELEGRAM_BOT_TOKEN") and self.vault.has("TELEGRAM_CHAT_ID")

    async def notify(self, text: str) -> bool:
        if not self.enabled:
            return False
        safe = self.vault.redact(text)[:3500]
        try:
            if self._sender is not None:
                await self._sender(self.vault.get("TELEGRAM_CHAT_ID"), safe)
                return True
            import aiohttp
            url = f"https://api.telegram.org/bot{self.vault.get('TELEGRAM_BOT_TOKEN')}/sendMessage"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
                async with s.post(url, json={"chat_id": self.vault.get("TELEGRAM_CHAT_ID"), "text": f"[Goltrader] {safe}"}) as r:
                    return r.status == 200
        except Exception as e:  # nunca incluir URL (contém o token) no log
            logging.getLogger("goltrader.notify").warning("falha Telegram: %s", type(e).__name__)
            return False
