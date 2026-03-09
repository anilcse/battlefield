import json
from pathlib import Path
from functools import lru_cache
from typing import Any, Dict, List

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MODEL_NAMES = "openai/gpt-5,anthropic/claude-sonnet-4,x-ai/grok-4,google/gemini-3.1-pro-preview,deepseek/deepseek-v3.2-speciale"


def _parse_model_names(value: str) -> List[str]:
    if not (value or "").strip():
        return [x.strip() for x in _DEFAULT_MODEL_NAMES.split(",") if x.strip()]
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_model_config_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _normalize_account_configs(raw: Any) -> Dict[str, Dict[str, str]]:
    if isinstance(raw, dict):
        out: Dict[str, Dict[str, str]] = {}
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, dict):
                out[k.strip()] = {str(a): str(b) for a, b in v.items()}
        return out
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return _normalize_account_configs(parsed)
        except json.JSONDecodeError:
            pass
    return {}


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
    polymarket_signature_type: int = 1  # 0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE
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
    tournament_duration_days: int = 7
    tournament_start_budget_usd: float = 100.0
    game_trade_size_usd: float = 5.0
    game_edge_threshold: float = 0.10

    default_model_monthly_budget_usd: float = 100.0
    # Path to JSON file for model config (model_names, model_account_configs, default_model_monthly_budget_usd)
    model_config_path: str = ""
    # Store as str so env is never JSON-parsed (avoids JSONDecodeError when MODEL_NAMES is empty/unset)
    model_names_raw: str = Field(default=_DEFAULT_MODEL_NAMES, validation_alias="MODEL_NAMES")

    @model_validator(mode="before")
    @classmethod
    def inject_model_config_from_json(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        path = (data.get("model_config_path") or "").strip()
        if not path:
            for candidate in ("model_config.json", "backend/model_config.json", "../model_config.json"):
                if Path(candidate).is_file():
                    path = candidate
                    break
        if not path:
            return data
        j = _load_model_config_json(path)
        if not j:
            return data
        if "model_names" in j:
            v = j["model_names"]
            data["model_names_raw"] = v if isinstance(v, str) else ",".join(str(x) for x in v) if v else data.get("model_names_raw", _DEFAULT_MODEL_NAMES)
        if "model_account_configs" in j:
            data["model_account_configs"] = _normalize_account_configs(j["model_account_configs"])
        if "default_model_monthly_budget_usd" in j and isinstance(j["default_model_monthly_budget_usd"], (int, float)):
            data["default_model_monthly_budget_usd"] = float(j["default_model_monthly_budget_usd"])
        return data

    @computed_field
    @property
    def model_names(self) -> List[str]:
        return _parse_model_names(self.model_names_raw)

    @field_validator("model_account_configs", mode="before")
    @classmethod
    def parse_model_account_configs(cls, value: str | Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
        return _normalize_account_configs(value) if value else {}

    def get_model_account(self, model_name: str) -> Dict[str, str]:
        model_cfg = self.model_account_configs.get(model_name, {})
        return {
            "polymarket_api_key": model_cfg.get("polymarket_api_key", self.polymarket_api_key),
            "polymarket_secret": model_cfg.get("polymarket_secret", self.polymarket_secret),
            "polymarket_passphrase": model_cfg.get("polymarket_passphrase", self.polymarket_passphrase),
            "private_key": model_cfg.get("private_key", self.private_key),
            "wallet_address": model_cfg.get("wallet_address", self.wallet_address),
            "signature_type": model_cfg.get("signature_type", str(self.polymarket_signature_type)),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
