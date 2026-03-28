# bot/health/models.py
"""Health monitor data models."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ComponentStatus:
    name: str           # binance | llm | db | scheduler
    ok: bool
    latency_ms: float = 0.0
    error: str = ""
    tokens_used: int = 0


@dataclass
class HealthStatus:
    components: list  # list[ComponentStatus]
    uptime_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.components)

    @property
    def details(self) -> dict:
        return {
            c.name: {"ok": c.ok, "latency_ms": c.latency_ms, "error": c.error}
            for c in self.components
        }

    def summary_text(self) -> str:
        lines = []
        for c in self.components:
            emoji = "✅" if c.ok else "❌"
            detail = f"{c.latency_ms:.0f}ms" if c.latency_ms else ""
            if not c.ok and c.error:
                detail = c.error[:30]
            lines.append(f"  {c.name.capitalize()}: {emoji} {detail}")
        return "\n".join(lines)
