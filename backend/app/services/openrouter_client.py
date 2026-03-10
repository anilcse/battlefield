import json
import re
from typing import Any, Dict, Tuple

import httpx

from app.core.config import get_settings


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty model response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("Model response is not valid JSON")
        return json.loads(match.group(0))


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class OpenRouterClient:
    async def forecast_market(
        self,
        model_name: str,
        market_title: str,
        market_context: str,
        system_prompt: str = "",
    ) -> Tuple[dict, float]:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is missing")

        if not system_prompt:
            system_prompt = "You are a quantitative prediction market trading agent. Return strict JSON."

        prompt = market_context if len(market_context) > 200 else (
            "You are forecasting a prediction market outcome.\n"
            "Return strict JSON only with keys: should_trade, skip_reason, probability_yes, confidence, rationale.\n"
            "should_trade: boolean - true if you want to trade this market, false to skip.\n"
            "skip_reason: string - if should_trade is false, explain why.\n"
            "probability_yes and confidence must be decimals between 0 and 1.\n"
            f"Market title: {market_title}\n"
            f"Market context: {market_context}\n"
        )

        body = {
            "model": model_name,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": settings.openrouter_site_url,
            "X-Title": settings.openrouter_app_name,
        }
        url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()
            payload = response.json()

        content = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        data = _extract_json_object(content)

        usage = payload.get("usage", {})
        total_cost = (
            _safe_float(payload.get("total_cost"), 0.0)
            or _safe_float(payload.get("cost"), 0.0)
            or _safe_float(usage.get("cost"), 0.0)
        )
        if total_cost <= 0:
            total_cost = 0.02
        return data, total_cost
