from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    attempt: int
    remaining: int
    retry_allowed: bool
    action: str
    fallback_category: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    max_attempts: int = 2

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")


class RecoveryState:
    """Bound retry decisions by normalized tool-call signature."""

    def __init__(self, policy: RecoveryPolicy | None = None) -> None:
        self.policy = policy or RecoveryPolicy()
        self._attempts: dict[str, int] = {}

    def record(
        self,
        signature: str,
        error_category: str | None,
        *,
        retryable: bool,
    ) -> RecoveryDecision:
        del error_category
        if not retryable:
            return RecoveryDecision(
                attempt=1,
                remaining=0,
                retry_allowed=False,
                action="stop",
            )
        attempt = self._attempts.get(signature, 0) + 1
        self._attempts[signature] = attempt
        remaining = max(0, self.policy.max_attempts - attempt)
        if remaining:
            return RecoveryDecision(
                attempt=attempt,
                remaining=remaining,
                retry_allowed=True,
                action="regenerate",
            )
        return RecoveryDecision(
            attempt=attempt,
            remaining=0,
            retry_allowed=False,
            action="fallback",
            fallback_category="tool_retry_exhausted",
        )

    def attempts_for(self, signature: str) -> int:
        return self._attempts.get(signature, 0)

    def exhausted(self, signature: str) -> bool:
        return self.attempts_for(signature) >= self.policy.max_attempts
