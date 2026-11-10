"""tradingview/: the alert templates, and the dry run posting them to a hook."""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Callable
from typing import Any

import pytest
from conftest import ROOT, Run
from mock_pipsocket import MockPipsocket

TEMPLATES = sorted((ROOT / "tradingview" / "templates").glob("*.json"))

# Which fields each action takes, from docs.pipsocket.com/tradingview-messages ("Which fields each
# action takes"): R required, O optional; a field not listed is refused.
COMMON = {
    "action": "R",
    "event_id": "O",
    "sent_at": "O",
    "source_seq": "O",
    "src": "O",
    "accounts": "O",
    "comment": "O",
    "test": "O",
}
MANAGED = {"trailing": "O", "break_even": "O"}
FIELDS: dict[str, dict[str, str]] = {
    "open": {
        "symbol": "R",
        "side": "R",
        "lots": "O",
        "risk": "O",
        "entry": "O",
        "sl": "O",
        "tp": "O",
        **MANAGED,
        "tag": "O",
        "max_age": "O",
        "slippage": "O",
    },
    "close": {"symbol": "R", "side": "O", "portion": "O", "tag": "O", "slippage": "O"},
    "close_all": {"tag": "O", "slippage": "O"},
    "cancel": {"symbol": "O", "side": "O", "tag": "O"},
    "reverse": {
        "symbol": "R",
        "side": "R",
        "lots": "O",
        "risk": "O",
        "sl": "O",
        "tp": "O",
        **MANAGED,
        "tag": "O",
        "max_age": "O",
        "slippage": "O",
    },
    "modify": {
        "symbol": "R",
        "side": "O",
        "entry": "O",
        "sl": "O",
        "tp": "O",
        **MANAGED,
        "tag": "O",
    },
    "strategy": {
        "symbol": "R",
        "lots": "O",
        "sl": "O",
        "tp": "O",
        **MANAGED,
        "tag": "O",
        "position": "R",
        "max_age": "O",
        "slippage": "O",
    },
}
LEVEL_FORMS = {"pips", "points", "price", "percent"}


def documented_problems(message: dict[str, Any]) -> list[str]:
    """How a message breaks the documented field rules (the ones the templates use)."""
    allowed = COMMON | FIELDS[message["action"]]
    problems = [
        f"{name} isn't taken by {message['action']}" for name in message if name not in allowed
    ]
    problems += [
        f"{name} is required"
        for name, rule in allowed.items()
        if rule == "R" and name not in message
    ]
    if "lots" in message and "risk" in message:
        problems.append("exactly one of lots or risk")
    if "risk" in message and "sl" not in message:
        problems.append("risk sizing needs sl")
    for level in ("sl", "tp"):
        if level in message and len(LEVEL_FORMS & message[level].keys()) != 1:
            problems.append(f"{level} needs exactly one of {sorted(LEVEL_FORMS)}")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", message.get("tag", "x")):
        problems.append("tag is up to 16 of A-Z, a-z, 0-9, _ and -")
    if len(message.get("event_id", "")) > 128:
        problems.append("event_id is up to 128 characters")
    return problems


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda path: path.name)
def test_dry_run_posts_the_filled_template_marked_as_a_test(
    mock: MockPipsocket, run: Callable[..., Run], template: pathlib.Path
) -> None:
    result = run(
        "tradingview/dry_run.py",
        str(template),
        "--symbol",
        "OANDA:EURUSD",
        PIPSOCKET_HOOK_URL=mock.hook_url,
    )

    assert result.code == 0, result.output
    assert "200: Recorded" in result.stdout
    assert "whk_mock" not in result.output  # the URL's secret is never printed
    [posted] = mock.hook_posts
    assert posted["test"] is True
    assert "{{" not in json.dumps(posted)
    assert posted.get("symbol", "OANDA:EURUSD") == "OANDA:EURUSD"
    assert documented_problems(posted) == []
    assert mock.calls == []  # nothing but the hook
