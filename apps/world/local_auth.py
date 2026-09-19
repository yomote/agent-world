"""127.0.0.1 demo用のserver capability。本人認証ではない。"""

import os
from dataclasses import dataclass
from secrets import token_urlsafe

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class LabCapabilities:
    observer: str
    proposer: str
    operator: str
    executor: str

    @classmethod
    def from_environment(cls) -> "LabCapabilities":
        return cls(
            observer=os.getenv("LAB_OBSERVER_CAPABILITY") or token_urlsafe(24),
            proposer=os.getenv("LAB_PROPOSER_CAPABILITY") or token_urlsafe(24),
            operator=os.getenv("LAB_OPERATOR_CAPABILITY") or token_urlsafe(24),
            executor=os.getenv("LAB_EXECUTOR_CAPABILITY") or token_urlsafe(24),
        )


def require_capability(expected: str):
    def authorize(x_lab_capability: str | None = Header(default=None)) -> str:
        if not x_lab_capability or x_lab_capability != expected:
            raise HTTPException(status_code=403, detail="capability_denied")
        return expected

    return authorize
