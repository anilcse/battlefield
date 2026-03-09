import json
from functools import lru_cache
from typing import Dict, List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "nof1-polymarket-clone"
    app_env: str = "dev"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/nof1"

    polymarket_base_url: str = "https://clob.polymarket.com"
    polymarket_api_key: str = ""
    polymarket_secret: str = ""
    polymarket_passphrase: str = ""
    polymarket_chain_id: int = 137
    enable_live_trading: bool = False
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    openrouter_app_name: str = "nof1-polymarket-clone"
    openrouter_site_url: str = "http://localhost:8000"
    polygon_rpc_url: str = ""
    private_key: str = ""
    wallet_address: str = ""
    auto_claim_enabled: bool = False
    auto_claim_interval_seconds: int = 600
    model_account_configs: Dict[str, Dict[str, str]] = {}
    game_loop_enabled: bool = False
    game_loop_interval_seconds: int = 3600
    tournament_duration_days: int = 30
    tournament_start_budget_usd: float = 100.0
    game_trade_size_usd: float = 5.0
    game_edge_threshold: float = 0.10

    default_model_monthly_budget_usd: float = 100.0
    model_names: List[str] = [
        "openai/gpt-5",
        "anthropic/claude-sonnet-4",
        "x-ai/grok-4",
        "google/gemini-3.1-pro-preview",
        "deepseek/deepseek-v3.2-speciale",
    ]

    @field_validator("model_names", mode="before")
    @classmethod
    def split_model_names(cls, value: str | List[str]) -> List[str]:
        if isinstance(value, list):
            return value
        if not value:
            return []
        return [item.strip() for item in value.split(",") if item.strip()]

    @field_validator("model_account_configs", mode="before")
    @classmethod
    def parse_model_account_configs(cls, value: str | Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("MODEL_ACCOUNT_CONFIGS must be a JSON object")
        out: Dict[str, Dict[str, str]] = {}
        for model_name, model_cfg in parsed.items():
            if not isinstance(model_name, str) or not isinstance(model_cfg, dict):
                continue
            out[model_name.strip()] = {str(k): str(v) for k, v in model_cfg.items()}
        return out

    def get_model_account(self, model_name: str) -> Dict[str, str]:
        model_cfg = self.model_account_configs.get(model_name, {})
        return {
            "polymarket_api_key": model_cfg.get("polymarket_api_key", self.polymarket_api_key),
            "polymarket_secret": model_cfg.get("polymarket_secret", self.polymarket_secret),
            "polymarket_passphrase": model_cfg.get("polymarket_passphrase", self.polymarket_passphrase),
            "private_key": model_cfg.get("private_key", self.private_key),
            "wallet_address": model_cfg.get("wallet_address", self.wallet_address),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
