"""Capability policy with an intentionally absent external executor."""

from __future__ import annotations

from dataclasses import asdict, dataclass


READ_MODES = frozenset({"evaluation", "preview", "recommendation", "approved"})
WRITE_FREE_MODES = frozenset({"evaluation", "preview", "recommendation"})


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    mode: str
    capability: str
    approval_state: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class CapabilityPolicy:
    """Decide whether a mode can use a capability.

    The v0.1 companion has no external-write implementation. Approval is a
    recorded human decision, not an execution token. That keeps evaluation,
    preview, recommendation, approval, and execution as distinct states.
    """

    def decide(self, mode: str, capability: str, approval_state: str = "pending") -> PolicyDecision:
        if capability == "local_read":
            return PolicyDecision(mode in READ_MODES, mode, capability, approval_state, "local reads are permitted")
        if capability == "local_record":
            return PolicyDecision(
                mode in READ_MODES,
                mode,
                capability,
                approval_state,
                "local ledger writes are permitted and do not mutate providers",
            )
        if capability == "external_write":
            reason = "approval required" if approval_state != "approved" else "external executor is not implemented"
            return PolicyDecision(False, mode, capability, approval_state, reason)
        return PolicyDecision(False, mode, capability, approval_state, "unknown capability")

