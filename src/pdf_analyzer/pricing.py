from dataclasses import dataclass


MODEL_PRICING_USD_PER_MILLION: dict[str, dict[str, float]] = {
    "gemini-3-flash-preview": {"input": 0.50, "output": 3.00},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
}


@dataclass(frozen=True)
class ModelPricing:
    model_name: str
    input_usd_per_million_tokens: float
    output_usd_per_million_tokens: float


@dataclass(frozen=True)
class UsageCostEstimate:
    model_name: str
    prompt_tokens: int
    candidate_tokens: int
    total_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float


def get_model_pricing(model_name: str) -> ModelPricing:
    if model_name in MODEL_PRICING_USD_PER_MILLION:
        pricing = MODEL_PRICING_USD_PER_MILLION[model_name]
        return ModelPricing(
            model_name=model_name,
            input_usd_per_million_tokens=pricing["input"],
            output_usd_per_million_tokens=pricing["output"],
        )

    for known_model, pricing in MODEL_PRICING_USD_PER_MILLION.items():
        if model_name.startswith(known_model):
            return ModelPricing(
                model_name=known_model,
                input_usd_per_million_tokens=pricing["input"],
                output_usd_per_million_tokens=pricing["output"],
            )

    raise ValueError(f"No pricing configured for model {model_name!r}")


def estimate_usage_cost(
    model_name: str,
    *,
    prompt_tokens: int,
    candidate_tokens: int,
    total_tokens: int,
) -> UsageCostEstimate:
    pricing = get_model_pricing(model_name)
    input_cost_usd = (prompt_tokens / 1_000_000) * pricing.input_usd_per_million_tokens
    output_cost_usd = (
        candidate_tokens / 1_000_000
    ) * pricing.output_usd_per_million_tokens
    return UsageCostEstimate(
        model_name=pricing.model_name,
        prompt_tokens=prompt_tokens,
        candidate_tokens=candidate_tokens,
        total_tokens=total_tokens,
        input_cost_usd=input_cost_usd,
        output_cost_usd=output_cost_usd,
        total_cost_usd=input_cost_usd + output_cost_usd,
    )

