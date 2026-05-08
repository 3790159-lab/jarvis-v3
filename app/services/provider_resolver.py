from __future__ import annotations

from typing import Any, Dict, Optional


def _normalize_provider_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return str(value).strip().lower()


def _extract_provider_status(ai_health: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    providers = {}
    for item in ai_health.get("providers", []) or []:
        name = _normalize_provider_name(item.get("provider"))
        if not name:
            continue
        providers[name] = {
            "enabled": bool(item.get("enabled", False)),
            "configured": bool(item.get("configured", False)),
            "details": item.get("details", {}) or {},
        }
    return providers


def get_configured_provider_names(ai_health: Dict[str, Any]) -> list[str]:
    providers = _extract_provider_status(ai_health)
    configured = []
    for name, meta in providers.items():
        if meta.get("enabled") and meta.get("configured"):
            configured.append(name)
    return configured


def resolve_provider(
    preferred_provider: Optional[str],
    ai_health: Dict[str, Any],
    fallback_order: Optional[list[str]] = None,
) -> Dict[str, Any]:
    fallback_order = fallback_order or ["ollama", "openai", "anthropic"]

    providers = _extract_provider_status(ai_health)
    requested = _normalize_provider_name(preferred_provider)

    def is_available(name: Optional[str]) -> bool:
        if not name:
            return False
        meta = providers.get(name, {})
        return bool(meta.get("enabled")) and bool(meta.get("configured"))

    if is_available(requested):
        return {
            "requested_provider": requested,
            "resolved_provider": requested,
            "fallback_used": False,
            "configured_providers": get_configured_provider_names(ai_health),
        }

    for candidate in fallback_order:
        candidate = _normalize_provider_name(candidate)
        if is_available(candidate):
            return {
                "requested_provider": requested,
                "resolved_provider": candidate,
                "fallback_used": True,
                "configured_providers": get_configured_provider_names(ai_health),
            }

    for candidate in providers.keys():
        if is_available(candidate):
            return {
                "requested_provider": requested,
                "resolved_provider": candidate,
                "fallback_used": True,
                "configured_providers": get_configured_provider_names(ai_health),
            }

    return {
        "requested_provider": requested,
        "resolved_provider": None,
        "fallback_used": True,
        "configured_providers": get_configured_provider_names(ai_health),
        "error": "No configured LLM providers are available",
    }