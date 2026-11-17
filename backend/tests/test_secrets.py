import json
import logging
import pickle

import pytest
from fastapi.testclient import TestClient

import main
from execution import state
from nod_core.agent_runtime import NodSecretVault, TelegramNotifier, install_log_redaction

SECRETS = {
    "GOLTRADER_API_TOKEN": "tok-ABCDEF123456",
    "BYBIT_API_KEY": "KEYVALUE-9988776655",
    "BYBIT_API_SECRET": "SECRETVALUE-1122334455",
    "IQ_EMAIL": "alguem@exemplo.invalid",
    "IQ_PASSWORD": "PASSWORDVALUE-5544",
    "TELEGRAM_BOT_TOKEN": "123456:TELEGRAMTOKENVALUE",
    "TELEGRAM_CHAT_ID": "-100987654321",
}


@pytest.fixture
def secrets_env(monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    return SECRETS


def test_vault_masked_status_only_booleans(secrets_env):
    v = NodSecretVault()
    st = v.masked_status()
    assert set(st.values()) == {True} and set(st) == set(SECRETS)
    for text in (repr(v), str(v), json.dumps(st)):
        for val in SECRETS.values():
            assert val not in text


def test_vault_cannot_be_serialized(secrets_env):
    v = NodSecretVault()
    with pytest.raises(TypeError):
        pickle.dumps(v)
    with pytest.raises(KeyError):
        v.get("PATH")


def test_vault_redact(secrets_env):
    v = NodSecretVault()
    out = v.redact("erro com BYBIT_API_SECRET=SECRETVALUE-1122334455 e token tok-ABCDEF123456")
    assert "SECRETVALUE" not in out and "tok-ABCDEF" not in out and "***" in out


def test_api_responses_never_contain_secrets(secrets_env):
    c = TestClient(main.app)
    h = {"Authorization": f"Bearer {SECRETS['GOLTRADER_API_TOKEN']}"}
    responses = [
        c.get("/api/saude"),
        c.post("/api/v1/toggle_mode", headers=h, json={"confirm": "CONFIRMAR"}),
        c.post("/api/v1/place_concurrent_basket", headers=h, json=[{"symbol": "BTCUSDT", "venue": "BYBIT_PERP", "side": "BUY", "amount": 0.01}]),
        c.post("/api/v1/kill_switch", headers=h),
        c.post("/api/v1/risk/reset_breaker", headers=h, json={"confirm": "CONFIRMAR"}),
        c.post("/api/v1/place_concurrent_basket", headers={"Authorization": "Bearer errado"}, json=[]),
    ]
    with c.websocket_connect(f"/ws/pulse?token={SECRETS['GOLTRADER_API_TOKEN']}") as ws:
        responses_ws = ws.receive_text()
    blob = "\n".join(r.text for r in responses) + responses_ws
    for name, val in SECRETS.items():
        assert val not in blob, f"{name} vazou em uma resposta"
    saude = responses[0].json()
    assert saude["secrets_configured"]["BYBIT_API_SECRET"] is True


def test_logs_never_contain_secrets(secrets_env, caplog):
    install_log_redaction(state.vault)
    logger = logging.getLogger("goltrader.test")
    with caplog.at_level(logging.INFO):
        logger.info("chave %s e %s", SECRETS["BYBIT_API_KEY"], SECRETS["BYBIT_API_SECRET"])
        logging.getLogger("outro").error("falha " + SECRETS["TELEGRAM_BOT_TOKEN"])
    text = caplog.text
    for val in SECRETS.values():
        assert val not in text


async def test_execution_error_message_redacted(secrets_env, monkeypatch):
    import execution
    from models import SmartOrderRequest
    monkeypatch.setenv("GOLTRADER_ALLOW_LIVE", "true")
    state.analyzer_mode = False
    state.risk.set_wallet_equity(10000.0, "BYBIT_TESTNET")

    async def boom(order):
        raise RuntimeError("falhou com " + SECRETS["BYBIT_API_SECRET"])

    monkeypatch.setattr(execution, "_live_bybit", boom)
    r = await execution.execute_single_order(SmartOrderRequest(symbol="BTCUSDT", venue="BYBIT_PERP", side="BUY", amount=0.01))
    assert r["status"] == "ERROR" and SECRETS["BYBIT_API_SECRET"] not in json.dumps(r)


async def test_telegram_optional_and_redacted(secrets_env, monkeypatch):
    sent = []

    async def sender(chat, text):
        sent.append((chat, text))

    n = TelegramNotifier(NodSecretVault(), sender=sender)
    assert n.enabled
    await n.notify("breaker " + SECRETS["BYBIT_API_SECRET"])
    assert sent and SECRETS["BYBIT_API_SECRET"] not in sent[0][1]
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    n2 = TelegramNotifier(NodSecretVault(), sender=sender)
    assert n2.enabled is False and await n2.notify("x") is False and len(sent) == 1
