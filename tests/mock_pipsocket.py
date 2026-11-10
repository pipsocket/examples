"""A local mock of the Pipsocket API, its account stream and a TradingView hook.

The examples run against it as they are, with PIPSOCKET_BASE_URL and PIPSOCKET_STREAM_URL
pointing here and a made-up key. It keeps one demo account's positions, pending orders, deals and
trade requests, answers the calls the examples make the way the docs at docs.pipsocket.com
describe, and checks the shape of every request body and every answer against the API's OpenAPI
document (tests/openapi.json, written by tests/snapshot_openapi.py), so its fields and states can't
drift from the API's without a test failing. That checks shapes, not behaviour: what the mock does
with a call is modelled on the docs.

Tests change the account through `broker` before an example runs (its trade mode, a symbol's
filling modes, what the next trade answers) and read `calls` afterwards: every call the example
made, with the journal files as they were when each trade call arrived.
"""

from __future__ import annotations

import asyncio
import copy
import json
import math
import pathlib
import threading
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType, web
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

KEY = "psk_mock0000000000000000000000000000000000000"
ACCOUNT = "acc_01m6tx3k9f2qw8r7bznd5yvh4c"
HOOK_PATH = "/t/hk_mock0000000000/whk_mock0000000000000000"
RESERVED_MAGIC = 2**52

Json = dict[str, Any]

OPENAPI = json.loads((pathlib.Path(__file__).parent / "openapi.json").read_text())
_REGISTRY = Registry().with_resource(
    "urn:openapi", Resource.from_contents(OPENAPI, default_specification=DRAFT202012)
)

RETCODES = {
    10008: "TRADE_RETCODE_PLACED",
    10009: "TRADE_RETCODE_DONE",
    10010: "TRADE_RETCODE_DONE_PARTIAL",
    10013: "TRADE_RETCODE_INVALID",
    10014: "TRADE_RETCODE_INVALID_VOLUME",
    10015: "TRADE_RETCODE_INVALID_PRICE",
    10018: "TRADE_RETCODE_MARKET_CLOSED",
    10030: "TRADE_RETCODE_INVALID_FILL",
}

STATUS_FOR_STATE = {
    "completed": 200,
    "placed": 200,
    "partial": 200,
    "executed": 200,
    "rejected": 422,
    "pending": 202,
    "unknown": 202,
    "not_sent": 503,
    "not_executed": 409,
}


def _pointer(*parts: str) -> str:
    return "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def _validator(pointer: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"urn:openapi#/{pointer}"}, registry=_REGISTRY)


def schema_errors(template: str, method: str, status: int, body: Any) -> list[str]:
    """How an answer differs from what the OpenAPI document says that operation answers."""
    operation = OPENAPI["paths"][template][method.lower()]
    if str(status) not in operation["responses"]:
        return [f"{method} {template} doesn't answer {status}"]
    pointer = _pointer(
        "paths",
        template,
        method.lower(),
        "responses",
        str(status),
        "content",
        "application/json",
        "schema",
    )
    return [error.message for error in _validator(pointer).iter_errors(body)]


def body_errors(template: str, method: str, body: Any) -> list[str]:
    """How a request body differs from the operation's documented body."""
    pointer = _pointer(
        "paths", template, method.lower(), "requestBody", "content", "application/json", "schema"
    )
    return [error.message for error in _validator(pointer).iter_errors(body)]


def symbol_spec(name: str = "EURUSD", **changes: Any) -> Json:
    """A symbol as GET /symbols/{symbol} answers, from the docs' example."""
    spec: Json = {
        "available": True,
        "contract_size": 100000,
        "currency_base": "EUR",
        "currency_margin": "EUR",
        "currency_profit": "USD",
        "description": "Euro vs US Dollar",
        "digits": 5,
        "execution": "market",
        "execution_mode": 2,
        "expiration_mode": 15,
        "expirations": ["gtc", "day", "specified", "specified_day"],
        "filling_mode": 2,
        "filling_modes": ["ioc"],
        "freeze_level": 0,
        "name": name,
        "order_mode": 127,
        "order_types": ["market", "limit", "stop", "stop_limit", "sl", "tp", "close_by"],
        "point": 0.00001,
        "stops_level": 0,
        "tick_size": 0.00001,
        "tick_value": 1,
        "trade_mode": 4,
        "trading": "full",
        "volume_max": 500,
        "volume_min": 0.01,
        "volume_step": 0.01,
    }
    spec.update(changes)
    return spec


def empty_sent() -> Json:
    """MT5's MqlTradeRequest with its empty values."""
    return {
        "action": "deal",
        "comment": "",
        "deviation": 0,
        "expiration": 0,
        "magic": 0,
        "order": 0,
        "position": 0,
        "position_by": 0,
        "price": 0,
        "sl": 0,
        "stoplimit": 0,
        "symbol": "",
        "tp": 0,
        "type": "buy",
        "type_filling": "fok",
        "type_time": "gtc",
        "volume": 0,
    }


@dataclass
class Call:
    """One call an example made."""

    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]  # names in lower case
    body: Any
    status: int = 0
    answer: Any = None
    journal: str | None = None  # the journal file as it was when a trade call arrived

    @property
    def key(self) -> str | None:
        return self.headers.get("idempotency-key")

    @property
    def is_trade(self) -> bool:
        return (
            self.method in ("POST", "PATCH", "DELETE")
            and "/v1/accounts/" in self.path
            and (
                self.path.endswith("/orders")
                or "/orders/" in self.path
                or "/positions/" in self.path
            )
        )


@dataclass
class Broker:
    """The account behind the mock, and how it answers. Tests change it before a run."""

    account: Json = field(
        default_factory=lambda: {
            "balance": 10000,
            "company": "Example Broker Ltd",
            "connected": True,
            "credit": 0,
            "currency": "USD",
            "equity": 10000,
            "id": ACCOUNT,
            "kill_switch": {"deviation": None, "engaged": False, "since": None},
            "leverage": 500,
            "login": 5058427372,
            "margin": 0,
            "margin_free": 10000,
            "margin_mode": "hedging",
            "server": "ExampleBroker-Demo",
            # Broker server time, which is neither UTC nor this machine's clock: an example that
            # used either would ask for the wrong history range.
            "server_time": 1794067825,
            "status": "connected",
            "trade_allowed": True,
            "trade_mode": "demo",
        }
    )
    symbols: dict[str, Json] = field(default_factory=lambda: {"EURUSD": symbol_spec()})
    # Each symbol's prices, (bid, ask): each tick read moves one along, then stays on the last.
    prices: dict[str, list[tuple[float, float]]] = field(
        default_factory=lambda: {"EURUSD": [(1.0841, 1.08412)]}
    )
    price_index: dict[str, int] = field(default_factory=dict[str, int])
    positions: list[Json] = field(default_factory=list[Json])
    orders: list[Json] = field(default_factory=list[Json])
    deals: list[Json] = field(default_factory=list[Json])
    history_orders: list[Json] = field(default_factory=list[Json])
    requests: dict[str, Json] = field(default_factory=dict[str, Json])
    keys: dict[str, str] = field(default_factory=dict[str, str])
    # How the next trade calls answer, in order. Empty means "fill".
    # - "fill", "partial" (about half filled), "placed" (taken, no deal), "reject"
    # - "pending" (pending, then completed on the request's second lookup), "pending_forever"
    # - "lost": 503 engine_unavailable on every attempt, nothing recorded, so the outcome is unknown
    # - "executed_then_lost": executed and recorded, but the first answer is a 503; the same key
    #   then finds the request
    plan: list[str] = field(default_factory=list[str])
    # Lookups of a pending request left before it settles.
    settles_after: dict[str, int] = field(default_factory=dict[str, int])
    # False: the account's terminal isn't running, so calls that need it answer 503
    # account_not_connected (the account itself and the kill switch still answer).
    terminal_running: bool = True
    # A history range longer than this many seconds answers 422 range_too_large.
    longest_range: int = 40 * 86400
    # Pending requests' outcomes, applied when they settle.
    held: dict[str, tuple[Json, Json | None]] = field(
        default_factory=dict[str, tuple[Json, Json | None]]
    )
    # Changes to the account applied after it has been read this many times: (reads, changes).
    # For an account that changes under a running example.
    account_changes_after: tuple[int, Json] | None = None
    account_reads: int = 0
    next_ticket: int = 881402400
    next_request: int = 1


class MockPipsocket:
    """The mock server, on its own thread and event loop."""

    def __init__(self) -> None:
        self.broker = Broker()
        self.calls: list[Call] = []
        self.settled_now: list[Json] = []
        self.hook_posts: list[Json] = []
        self.problems: list[str] = []  # answers or bodies that don't match the API document
        # Where the example keeps its journal (*-journal.jsonl), read when a trade call arrives.
        self.journal_dir: pathlib.Path | None = None
        self.start_positions: list[Json] = []
        self.sockets: list[web.WebSocketResponse] = []
        self.upgrades = 0
        self.lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        self.runner: web.AppRunner | None = None
        self.port = 0
        ready = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        self.thread.start()
        if not ready.wait(10):
            raise RuntimeError("the mock didn't start")

    # Lifecycle.

    def _run(self, ready: threading.Event) -> None:
        asyncio.set_event_loop(self.loop)
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self.runner = web.AppRunner(app)
        self.loop.run_until_complete(self.runner.setup())
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        self.loop.run_until_complete(site.start())
        server = site._server  # pyright: ignore[reportPrivateUsage]
        assert server is not None
        self.port = server.sockets[0].getsockname()[1]  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
        ready.set()
        self.loop.run_forever()

    def stop(self) -> None:
        async def close() -> None:
            for socket in list(self.sockets):
                await socket.close()
            if self.runner is not None:
                await self.runner.cleanup()

        asyncio.run_coroutine_threadsafe(close(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)

    def reset(self) -> None:
        with self.lock:
            self.broker = Broker()
            self.calls = []
            self.hook_posts = []
            self.problems = []
            self.journal_dir = None
            self.start_positions = []
            self.upgrades = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def stream_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    @property
    def hook_url(self) -> str:
        return self.base_url + HOOK_PATH

    # What tests read.

    def trades(self) -> list[Call]:
        return [call for call in self.calls if call.is_trade]

    def calls_to(self, method: str, suffix: str) -> list[Call]:
        prefix = f"/v1/accounts/{ACCOUNT}"
        return [c for c in self.calls if c.method == method and c.path == prefix + suffix]

    # Stream control, from the test's thread.

    def push(self, event: Json) -> None:
        """Send an event to every open stream."""
        asyncio.run_coroutine_threadsafe(self._broadcast(event), self.loop).result(5)

    def drop_streams(self) -> None:
        """Close every open stream, as a network failure would."""

        async def drop() -> None:
            for socket in list(self.sockets):
                await socket.close()

        asyncio.run_coroutine_threadsafe(drop(), self.loop).result(5)

    async def _broadcast(self, event: Json) -> None:
        for socket in list(self.sockets):
            if not socket.closed:
                await socket.send_str(json.dumps(event))

    # The router.

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        if request.path == HOOK_PATH and request.method == "POST":
            return await self._hook(request)
        prefix = f"/v1/accounts/{ACCOUNT}"
        if not request.path.startswith("/v1/accounts/"):
            return self._unsupported(request)
        if request.headers.get("Authorization") != f"Bearer {KEY}":
            return self._json(401, {"error": "invalid_api_key", "message": "Not a current key."})
        if request.path != prefix and not request.path.startswith(prefix + "/"):
            return self._json(404, {"error": "no_such_account", "message": "No such account."})
        if request.path == prefix + "/stream":
            return await self._stream(request)

        raw = await request.read()
        body: Any = json.loads(raw) if raw else None
        call = Call(
            request.method,
            request.path,
            dict(request.query),
            {
                name.lower(): value
                for name, value in request.headers.items()
                if name.lower() != "authorization"
            },
            body,
        )
        if call.is_trade and self.journal_dir is not None:
            journals = sorted(self.journal_dir.glob("*-journal.jsonl"))
            call.journal = "".join(journal.read_text() for journal in journals)
        with self.lock:
            if not self.calls:
                self.start_positions = copy.deepcopy(self.broker.positions)
            self.calls.append(call)
            template, status, answer = self._route(
                request.method, request.path[len(prefix) :] or "/", call
            )
            call.status, call.answer = status, answer
            if template is not None:
                for problem in schema_errors(template, request.method, status, answer):
                    self.problems.append(f"{request.method} {request.path} {status}: {problem}")
            settled = list(self.settled_now)
            self.settled_now.clear()
            if (
                call.is_trade
                and isinstance(answer, dict)
                and answer.get("state") in ("completed", "placed", "partial", "rejected")
            ):
                settled.append(answer)
            events = [event for request in settled for event in self._events(request)]
        for event in events:
            await self._broadcast(event)
        return self._json(status, answer)

    def _unsupported(self, request: web.Request) -> web.Response:
        self.problems.append(f"the mock doesn't answer {request.method} {request.path}")
        return self._json(501, {"error": "mock_unsupported", "message": "Not in the mock."})

    @staticmethod
    def _json(status: int, body: Any) -> web.Response:
        return web.json_response(body, status=status)

    def _route(self, method: str, path: str, call: Call) -> tuple[str | None, int, Any]:
        parts = path.strip("/").split("/") if path != "/" else []
        b = self.broker
        t = "/v1/accounts/{account}"
        if not b.terminal_running and parts and parts[0] not in ("requests", "kill-switch"):
            return _template_for(method, parts), 503, _refusal("account_not_connected")
        match method, parts:
            case "GET", []:
                b.account_reads += 1
                if b.account_changes_after and b.account_reads > b.account_changes_after[0]:
                    b.account.update(b.account_changes_after[1])
                return t, 200, copy.deepcopy(b.account)
            case "GET", ["positions"]:
                return t + "/positions", 200, copy.deepcopy(b.positions)
            case "GET", ["orders"]:
                return t + "/orders", 200, copy.deepcopy(b.orders)
            case "GET", ["symbols"]:
                summary = [
                    {
                        k: s[k]
                        for k in (
                            "available",
                            "contract_size",
                            "currency_base",
                            "currency_margin",
                            "currency_profit",
                            "description",
                            "digits",
                            "name",
                            "trading",
                            "volume_max",
                            "volume_min",
                            "volume_step",
                        )
                    }
                    | {"in_market_watch": True, "path": "Forex\\" + s["name"]}
                    for s in b.symbols.values()
                ]
                return t + "/symbols", 200, summary
            case "GET", ["symbols", name]:
                if name not in b.symbols:
                    return t + "/symbols/{symbol}", 404, _refusal("no_such_symbol")
                return t + "/symbols/{symbol}", 200, copy.deepcopy(b.symbols[name])
            case "GET", ["symbols", name, "tick"]:
                if name not in b.symbols:
                    return t + "/symbols/{symbol}/tick", 404, _refusal("no_such_symbol")
                bid, ask = self._next_price(name)
                return t + "/symbols/{symbol}/tick", 200, self._tick(name, bid, ask)
            case "GET", ["history", "deals" | "orders" as what]:
                return self._history(t + "/history/" + what, what, call)
            case "GET", ["requests", request_id]:
                return t + "/requests/{request}", *self._lookup(request_id)
            case "GET", ["kill-switch"]:
                return t + "/kill-switch", 200, copy.deepcopy(b.account["kill_switch"])
            case "POST", ["kill-switch"]:
                return t + "/kill-switch", 200, self._engage(call)
            case "DELETE", ["kill-switch"]:
                b.account["kill_switch"] = {"deviation": None, "engaged": False, "since": None}
                return t + "/kill-switch", 200, copy.deepcopy(b.account["kill_switch"])
            case "POST", ["orders"]:
                return t + "/orders", *self._trade(t + "/orders", call, self._place)
            case "DELETE", ["orders", ticket]:
                template = t + "/orders/{order}"
                return template, *self._trade(template, call, lambda c: self._cancel(int(ticket)))
            case "POST", ["positions", ticket, "close"]:
                template = t + "/positions/{position}/close"
                return template, *self._trade(template, call, lambda c: self._close(int(ticket), c))
            case _:
                self.problems.append(f"the mock doesn't answer {method} {path}")
                return None, 501, {"error": "mock_unsupported", "message": "Not in the mock."}

    # Reads.

    def _next_price(self, name: str) -> tuple[float, float]:
        prices = self.broker.prices[name]
        index = self.broker.price_index.get(name, 0)
        self.broker.price_index[name] = min(index + 1, len(prices) - 1)
        return prices[index]

    def _price(self, name: str) -> tuple[float, float]:
        prices = self.broker.prices[name]
        return prices[max(self.broker.price_index.get(name, 0) - 1, 0)]

    def _tick(self, name: str, bid: float, ask: float) -> Json:
        time_msc = self.broker.account["server_time"] * 1000
        return {
            "ask": ask,
            "bid": bid,
            "flags": 6,
            "last": 0,
            "symbol": name,
            "time_msc": time_msc,
            "volume": 0,
        }

    def _history(self, template: str, what: str, call: Call) -> tuple[str, int, Any]:
        try:
            start, end = int(call.query["from"]), int(call.query["to"])
        except (KeyError, ValueError):
            return template, 422, _refusal("invalid_request")
        if end < start:
            return template, 422, _refusal("invalid_request")
        if end - start > self.broker.longest_range:
            return template, 422, _refusal("range_too_large")
        rows = self.broker.deals if what == "deals" else self.broker.history_orders
        time_field = "time_msc" if what == "deals" else "time_done_msc"
        found = [r for r in rows if start * 1000 <= r[time_field] <= end * 1000 + 999]
        return template, 200, copy.deepcopy(found)

    def _lookup(self, request_id: str) -> tuple[int, Any]:
        request = self.broker.requests.get(request_id)
        if request is None:
            return 404, _refusal("no_such_request")
        left = self.broker.settles_after.get(request_id)
        if left is not None:
            if left <= 1:
                del self.broker.settles_after[request_id]
                self._settle(request)
            else:
                self.broker.settles_after[request_id] = left - 1
        return 200, copy.deepcopy(request)

    # Trades.

    def _trade(self, template: str, call: Call, act: Any) -> tuple[int, Any]:
        """Idempotency, the body check and the kill switch, then the trade itself."""
        key = call.key
        if key is not None and key in self.broker.keys:
            request = self.broker.requests[self.broker.keys[key]]
            return STATUS_FOR_STATE[request["state"]], copy.deepcopy(request)
        if call.method != "DELETE":
            problems = body_errors(template, call.method, call.body)
            if problems:
                return 422, _refusal("invalid_request", "; ".join(problems))
        outcome = self.broker.plan.pop(0) if self.broker.plan else "fill"
        if outcome == "lost":
            self.broker.plan.insert(0, "lost")  # every attempt, retries included
            return 503, _refusal("engine_unavailable")
        if template.endswith("/orders") and self.broker.account["kill_switch"]["engaged"]:
            return 423, _refusal("kill_switch_engaged")
        if call.body is not None and (call.body.get("magic") or 0) >= RESERVED_MAGIC:
            return 422, _refusal("magic_reserved")
        request, effects = act(call)
        if isinstance(request, int):
            return request, effects  # a refusal (status, body): nothing recorded
        if outcome == "reject" and effects is not None:
            self._reject(request, 10018)
            effects = None
        elif outcome == "placed" and effects is not None:
            self._placed_without_deal(request)
            effects = None
        elif outcome == "partial" and effects is not None:
            self._make_partial(request, effects)
        if effects is not None:
            self._apply(effects)
        if outcome in ("pending", "pending_forever") and request["state"] != "rejected":
            self._hold(request, effects)
            if outcome == "pending":
                self.broker.settles_after[request["request"]] = 2
        if key is not None:
            self.broker.keys[key] = request["request"]
        self.broker.requests[request["request"]] = request
        if outcome == "executed_then_lost":
            # Recorded and executed, but the answer never reached the client: only the same
            # key finds it.
            return 503, _refusal("engine_unavailable")
        return STATUS_FOR_STATE[request["state"]], copy.deepcopy(request)

    def _placed_without_deal(self, request: Json) -> None:
        """MT5 took it (10008) but made no deal: no position."""
        result = request["result"]
        request["state"] = "placed"
        request["message"] = "The request was placed."
        result.update(retcode=10008, retcode_name=RETCODES[10008], deal=0, volume=0, price=0)

    def _make_partial(self, request: Json, effects: Json) -> None:
        """MT5 filled part of it (10010): about half, in whole volume steps."""
        symbol = self.broker.symbols[request["sent"]["symbol"]]
        step = symbol["volume_step"]
        done = round(max(step, round(request["sent"]["volume"] / 2 / step) * step), 8)
        request["state"] = "partial"
        request["message"] = "The request was partly filled."
        request["result"].update(retcode=10010, retcode_name=RETCODES[10010], volume=done)
        for deal in effects["add_deals"]:
            deal["volume"] = done
        if request["sent"]["position"]:  # a close: part of the position stays open
            position = effects["remove_positions"][0]
            effects["add_positions"] = [dict(position, volume=round(position["volume"] - done, 8))]
        else:
            effects["add_positions"][0]["volume"] = done

    def _new_request(self, sent: Json) -> Json:
        number = self.broker.next_request
        self.broker.next_request += 1
        return {
            "request": f"req_{number:024x}",
            "state": "completed",
            "sent": sent,
            "result": None,
            "message": "The request was completed.",
        }

    def _ticket(self) -> int:
        self.broker.next_ticket += 1
        return self.broker.next_ticket

    def _result(
        self,
        retcode: int,
        *,
        order: int = 0,
        deal: int = 0,
        volume: float = 0,
        price: float = 0,
        symbol: str = "",
    ) -> Json:
        bid, ask = self._price(symbol) if symbol in self.broker.prices else (0, 0)
        return {
            "ask": ask,
            "bid": bid,
            "comment": "Request executed" if retcode in (10008, 10009) else "Rejected",
            "deal": deal,
            "order": order,
            "position": 0,
            "price": price,
            "retcode": retcode,
            "retcode_external": 0,
            "retcode_name": RETCODES[retcode],
            "volume": volume,
        }

    def _reject(self, request: Json, retcode: int) -> None:
        request["state"] = "rejected"
        request["error"] = "trade_rejected"
        request["message"] = "MT5 rejected the request."
        request["result"] = self._result(retcode, symbol=request["sent"]["symbol"])

    def _hold(self, request: Json, effects: Json | None) -> None:
        """No answer from the trade server yet: the trade takes effect when it settles."""
        if effects is not None:
            self._unapply(effects)
        settled = {k: request[k] for k in ("state", "result", "message")}
        self.broker.held[request["request"]] = (settled, effects)
        request["state"] = "pending"
        request["result"] = None
        request["message"] = "No answer from the trade server yet. It is not sent again."

    def _settle(self, request: Json) -> None:
        settled, effects = self.broker.held.pop(request["request"])
        request.update(settled)
        if effects is not None:
            self._apply(effects)
        self.settled_now.append(request)

    def _apply(self, effects: Json) -> None:
        self.broker.positions.extend(effects.get("add_positions", []))
        self.broker.deals.extend(effects.get("add_deals", []))
        for position in effects.get("remove_positions", []):
            self.broker.positions.remove(position)
        for order in effects.get("remove_orders", []):
            self.broker.orders.remove(order)

    def _unapply(self, effects: Json) -> None:
        for position in effects.get("add_positions", []):
            self.broker.positions.remove(position)
        for deal in effects.get("add_deals", []):
            self.broker.deals.remove(deal)
        self.broker.positions.extend(effects.get("remove_positions", []))
        self.broker.orders.extend(effects.get("remove_orders", []))

    def _check_order(self, body: Json, symbol: Json, whole: float | None = None) -> int | None:
        """The retcode MT5 would reject a market order with, or None. `whole` is the volume of
        the position a close is for: closing all of it is allowed whatever the symbol's minimum."""
        if body.get("type_filling") not in symbol["filling_modes"]:
            return 10030
        closes_all = whole is not None and math.isclose(body["volume"], whole)
        if not closes_all and not _volume_ok(body["volume"], symbol):
            return 10014
        if symbol["execution"] in ("instant", "request") and not body.get("price"):
            return 10015
        if symbol["trading"] != "full":
            return 10018
        return None

    def _place(self, call: Call) -> tuple[Json, Json | None] | tuple[int, Json]:
        body: Json = call.body
        if body["type"] not in ("buy", "sell"):
            self.problems.append("the mock only fills market orders")
            return 501, _refusal("mock_unsupported")
        sent = empty_sent() | body | {"action": "deal"}
        request = self._new_request(sent)
        symbol = self.broker.symbols.get(body["symbol"])
        retcode = 10013 if symbol is None else self._check_order(body, symbol)
        if retcode is not None:
            self._reject(request, retcode)
            return request, None
        if self.broker.account["margin_mode"] == "netting" and any(
            p["symbol"] == body["symbol"] for p in self.broker.positions
        ):
            self.problems.append("the mock doesn't net an order into an existing position")
            return 501, _refusal("mock_unsupported")
        bid, ask = self._price(body["symbol"])
        price = ask if body["type"] == "buy" else bid
        order, deal = self._ticket(), self._ticket()
        time_msc = self.broker.account["server_time"] * 1000
        position = {
            "comment": body.get("comment", ""),
            "identifier": order,
            "magic": body.get("magic", 0),
            "price_open": price,
            "sl": body.get("sl", 0),
            "symbol": body["symbol"],
            "ticket": order,
            "time_msc": time_msc,
            "tp": body.get("tp", 0),
            "type": body["type"],
            "volume": body["volume"],
        }
        effects: Json = {
            "add_positions": [position],
            "add_deals": [_deal(deal, order, order, body, "in", price, time_msc)],
        }
        request["result"] = self._result(
            10009, order=order, deal=deal, volume=body["volume"], price=price, symbol=body["symbol"]
        )
        return request, effects

    def _close(self, ticket: int, call: Call) -> tuple[Json, Json | None] | tuple[int, Json]:
        body: Json = call.body
        position = next((p for p in self.broker.positions if p["ticket"] == ticket), None)
        if position is None:
            return 404, _refusal("no_such_position")
        side = "sell" if position["type"] == "buy" else "buy"
        sent = empty_sent() | body
        sent |= {"action": "deal", "symbol": position["symbol"], "type": side, "position": ticket}
        request = self._new_request(sent)
        symbol = self.broker.symbols[position["symbol"]]
        retcode = self._check_order(body, symbol, whole=position["volume"])
        if retcode is None and body["volume"] > position["volume"] + 1e-9:
            retcode = 10014
        if retcode is not None:
            self._reject(request, retcode)
            return request, None
        bid, ask = self._price(position["symbol"])
        price = bid if position["type"] == "buy" else ask
        order, deal = self._ticket(), self._ticket()
        time_msc = self.broker.account["server_time"] * 1000
        fields = body | {"symbol": position["symbol"], "type": side}
        out = _deal(deal, order, position["identifier"], fields, "out", price, time_msc)
        effects: Json = {"add_positions": [], "add_deals": [out], "remove_positions": [position]}
        if body["volume"] < position["volume"] - 1e-9:
            rest = dict(position, volume=round(position["volume"] - body["volume"], 8))
            effects["add_positions"].append(rest)
        request["result"] = self._result(
            10009,
            order=order,
            deal=deal,
            volume=body["volume"],
            price=price,
            symbol=position["symbol"],
        )
        return request, effects

    def _cancel(self, ticket: int) -> tuple[Json, Json | None] | tuple[int, Json]:
        order = next((o for o in self.broker.orders if o["ticket"] == ticket), None)
        if order is None:
            return 404, _refusal("no_such_order")
        sent = empty_sent() | {
            "action": "remove",
            "order": ticket,
            "symbol": order["symbol"],
            "type": order["type"],
        }
        request = self._new_request(sent)
        request["result"] = self._result(10009, order=ticket, symbol=order["symbol"])
        return request, {"remove_orders": [order]}

    def _engage(self, call: Call) -> Json:
        """Block new orders, then close every position and cancel every pending order."""
        deviation = (call.body or {}).get("deviation", 20)
        self.broker.account["kill_switch"] = {
            "deviation": deviation,
            "engaged": True,
            "since": 1794056825,
        }
        closed: list[Json] = []
        for position in list(self.broker.positions):
            symbol = self.broker.symbols[position["symbol"]]
            modes = symbol["filling_modes"]
            filling = next((m for m in ("fok", "ioc") if m in modes), modes[0])
            body = {"volume": position["volume"], "type_filling": filling, "deviation": deviation}
            request, effects = self._close(position["ticket"], Call("POST", "", {}, {}, body))
            assert isinstance(request, dict) and isinstance(effects, dict)
            self._apply(effects)
            self.broker.requests[request["request"]] = request
            closed.append({"position": position["ticket"], "request": request})
        cancelled: list[Json] = []
        for order in list(self.broker.orders):
            request, effects = self._cancel(order["ticket"])
            assert isinstance(request, dict) and isinstance(effects, dict)
            self._apply(effects)
            self.broker.requests[request["request"]] = request
            cancelled.append({"order": order["ticket"], "request": request})
        # Requests sent before it that could still add exposure: the switch can't sweep them yet.
        unanswered = list(self.broker.held)
        return {
            "flat": not unanswered,
            "cancelled": copy.deepcopy(cancelled),
            "closed": copy.deepcopy(closed),
            "remaining": {"positions": 0, "orders": 0},
            "unanswered": unanswered,
            "kill_switch": copy.deepcopy(self.broker.account["kill_switch"]),
            "message": "The kill switch is engaged."
            + ("" if unanswered else " The account is flat."),
        }

    def _events(self, request: Json) -> list[Json]:
        """The request event, and the deal event of a fill, for a request that settled."""
        events = [{"type": "request"} | copy.deepcopy(request)]
        deal = (request.get("result") or {}).get("deal")
        events += [{"type": "deal", "deal": d} for d in self.broker.deals if d["ticket"] == deal]
        return events

    # The stream.

    async def _stream(self, request: web.Request) -> web.StreamResponse:
        self.upgrades += 1
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        self.sockets.append(socket)
        await socket.send_str(json.dumps({"type": "subscribed", "account": ACCOUNT}))
        try:
            async for message in socket:
                if message.type != WSMsgType.TEXT:
                    continue
                if message.data == "ping":
                    await socket.send_str("pong")
                    continue
                received = json.loads(message.data)
                if received.get("path") == "/ticks" and received.get("method") == "PUT":
                    symbols = received["body"]["symbols"]
                    await socket.send_str(
                        json.dumps(
                            {
                                "type": "response",
                                "id": received["id"],
                                "status": 200,
                                "body": {"symbols": symbols},
                            }
                        )
                    )
                    for name in symbols:
                        bid, ask = self._price(name)
                        await socket.send_str(
                            json.dumps({"type": "tick"} | self._tick(name, bid, ask))
                        )
                else:
                    self.problems.append(f"the mock stream doesn't answer {received}")
        finally:
            self.sockets.remove(socket)
        return socket

    # The TradingView hook.

    async def _hook(self, request: web.Request) -> web.Response:
        raw = await request.text()
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            message = {"_not_json": raw}
        with self.lock:
            self.hook_posts.append(message)
        return web.Response(status=200, text="")


def _template_for(method: str, parts: list[str]) -> str:
    """The document's path template for a path under the account, such as /positions/7001/close."""
    for template, operations in OPENAPI["paths"].items():
        segments = template.split("/")[4:]  # after /v1/accounts/{account}
        if (
            method.lower() in operations
            and len(segments) == len(parts)
            and all(
                segment == part or segment.startswith("{")
                for segment, part in zip(segments, parts, strict=True)
            )
        ):
            return template
    raise KeyError(f"{method} {parts}")


def _refusal(code: str, message: str | None = None) -> Json:
    return {"error": code, "message": message or f"Refused: {code}."}


def _volume_ok(volume: float, symbol: Json) -> bool:
    if volume < symbol["volume_min"] - 1e-9 or volume > symbol["volume_max"] + 1e-9:
        return False
    steps = (volume - symbol["volume_min"]) / symbol["volume_step"]
    return math.isclose(steps, round(steps), abs_tol=1e-6)


def _deal(
    ticket: int, order: int, position: int, body: Json, entry: str, price: float, time_msc: int
) -> Json:
    return {
        "comment": body.get("comment", ""),
        "commission": -0.07,
        "entry": entry,
        "fee": 0,
        "magic": body.get("magic", 0),
        "order": order,
        "position": position,
        "price": price,
        "profit": 0 if entry == "in" else 0.12,
        "reason": "expert",
        "swap": 0,
        "symbol": body["symbol"],
        "ticket": ticket,
        "time_msc": time_msc,
        "type": body["type"],
        "volume": body["volume"],
    }
