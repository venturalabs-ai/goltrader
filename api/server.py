from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from core.config import settings, FREE_OPENCODE_MODELS
from core.hybrid_memory import HybridMemoryStore
from rea_bridge.harvester import REAHybridHarvester
from agent.continuous_loop import ContinuousSelfImprovingAgent
from execution.bybit_hybrid_connector import BybitHybridConnector

app = FastAPI(title="Goltrader REA Hybrid Engine API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class EvolutionRequest(BaseModel):
    symbol: str = "BTCUSDT"
    interval: str = "1"
    payout: float = 0.85
    n_trials_per_strategy: int = 12
    llm_model: Optional[str] = "DeepSeek V4.1 Flash Free"
    auto_execute_dry_run: bool = True

@app.get("/health")
def health():
    return {"status": "operational", "bybit_mode": "Testnet" if settings.bybit_testnet else "Mainnet", "free_opencode_models": FREE_OPENCODE_MODELS}

@app.post("/api/v1/rea/harvest")
def trigger_rea_harvest():
    return REAHybridHarvester().run_full_harvest()

@app.post("/api/v1/evolution/run")
def run_evolution(req: EvolutionRequest):
    return ContinuousSelfImprovingAgent(model_name=req.llm_model).run_evolution_cycle(req.symbol, req.interval, req.payout, req.n_trials_per_strategy, req.auto_execute_dry_run)

@app.get("/api/v1/memory/trades")
def get_trades(limit: int = 50):
    return HybridMemoryStore().get_recent_trades(limit=limit)
