from dataclasses import dataclass, field
from typing import Any


@dataclass
class Action:
    id: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    requires: list[str] = field(default_factory=list)
    critical: bool = False


@dataclass
class Plan:
    goal: str
    summary: str
    actions: list[Action]
    needs_clarification: bool = False
    clarification_question: str = ""


@dataclass
class ActionResult:
    id: str
    action: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    dry_run: bool = False
