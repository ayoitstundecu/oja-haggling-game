#!/usr/bin/env python3
"""Self-contained, authoritative server for Ọjà: The Haggling Game."""
from __future__ import annotations

import json
import os
import random
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "8000"))
ROUND_SECONDS = 180
ROOM_IDLE_SECONDS = 30 * 60
MAX_HUMANS = 8
HOST_TRANSFER_SECONDS = 15
NEGOTIATION_SECONDS = 30
GOODS = [
    {"id": "tomatoes", "name": "Roma tomatoes", "emoji": "🍅", "base": 950, "category": "Fresh food"},
    {"id": "pepper", "name": "Ata rodo pepper", "emoji": "🌶️", "base": 1250, "category": "Fresh food"},
    {"id": "yam", "name": "White yam", "emoji": "🍠", "base": 1850, "category": "Fresh food"},
    {"id": "ankara", "name": "Ankara fabric", "emoji": "🧵", "base": 2450, "category": "Style"},
    {"id": "beads", "name": "Coral beads", "emoji": "📿", "base": 2200, "category": "Style"},
    {"id": "basket", "name": "Woven market basket", "emoji": "🧺", "base": 1100, "category": "Home"},
    {"id": "kettle", "name": "Enamel kettle", "emoji": "🫖", "base": 2600, "category": "Home"},
    {"id": "sheabutter", "name": "Shea butter", "emoji": "🧴", "base": 1550, "category": "Care"},
]
GOOD_BY_ID = {item["id"]: item for item in GOODS}
EVENTS = [
    {"id": "blackout", "name": "NEPA blackout", "emoji": "🕯️", "description": "Only existing negotiations can be played."},
    {"id": "fuel", "name": "Fuel scarcity", "emoji": "⛽", "description": "Seller costs rise 20% while fuel is scarce."},
    {"id": "owambe", "name": "Owambe!", "emoji": "🎉", "description": "One hot-list category is worth double to shoppers."},
]
ROOMS: dict[str, dict] = {}
LOCK = threading.RLock()


def money(value: float) -> int:
    return int(round(value))


def new_round_data(players: list[dict], number: int) -> None:
    for player in players:
        selection = random.sample(GOODS, 3)
        sale_goods = []
        for good in selection:
            cost = max(250, int(good["base"] * random.uniform(0.78, 1.04) // 50 * 50))
            sale_goods.append({"good_id": good["id"], "cost": cost, "available": True})
        wants = random.sample(GOODS, 3)
        shopping_list = {
            good["id"]: int(good["base"] * random.uniform(1.16, 1.65) // 50 * 50)
            for good in wants
        }
        player["sales"] = sale_goods
        player["shopping"] = shopping_list
        player["wallet"] = 10000
        player["round_profit"] = 0
        player["round_savings"] = 0
        player["shopping_bought"] = set()
        player["round_deals"] = 0
    return


def add_feed(room: dict, text: str, kind: str = "market") -> None:
    room["feed"].insert(0, {"id": secrets.token_hex(5), "text": text, "kind": kind, "at": time.time()})
    del room["feed"][24:]
    room["version"] += 1


def active_players(room: dict) -> list[dict]:
    return [player for player in room["players"].values() if player["status"] == "active"]


def round_end_if_needed(room: dict) -> None:
    if room["phase"] != "playing" or room["paused"]:
        return
    remaining = room["round_ends_at"] - time.time()
    room["remaining"] = max(0, remaining)
    if remaining > 0:
        if room["event"] and room["event"].get("ends_at") and room["event"]["ends_at"] <= time.time():
            room["event"] = None
            room["next_event_at"] = time.time() + random.randint(35, 55)
            room["version"] += 1
        return
    room["negotiations"] = {}
    if room["round"] >= 4:
        room["phase"] = "finished"
        room["event"] = None
        room["remaining"] = 0
        finalize_awards(room)
        add_feed(room, "The market is closed! Final scores are in.", "event")
    else:
        room["phase"] = "between"
        room["remaining"] = 0
        add_feed(room, f"Round {room['round']} is over. Catch your breath!", "event")
    room["version"] += 1


def score_for(player: dict) -> int:
    return player["score"]


def finalize_awards(room: dict) -> None:
    eligible = [player for player in room["players"].values() if player["status"] != "kicked"]
    awards = [
        ("Shrewdest Trader", "total_savings"),
        ("Best Bargain", "best_bargain_savings"),
        ("Softest Heart", "best_generosity"),
    ]
    room["awards"] = [
        {"title": title, "name": max(eligible, key=lambda person: person[metric])["name"] if eligible else "—"}
        for title, metric in awards
    ]


def scores_snapshot(room: dict) -> list[dict]:
    return sorted(
        [
            {
                "id": player["id"],
                "name": player["name"],
                "score": player["score"],
                "deals": player["deals"],
                "is_bot": player["is_bot"],
                "online": player.get("online", False),
                "waiting_next_round": player["status"] == "waiting",
            }
            for player in room["players"].values()
            if player["status"] != "kicked"
        ],
        key=lambda item: (-item["score"], item["name"].casefold()),
    )


def market_snapshot(room: dict) -> list[dict]:
    goods = []
    for seller in room["players"].values():
        if seller["status"] == "waiting":
            continue
        for sale in seller.get("sales", []):
            if sale["available"]:
                good = GOOD_BY_ID[sale["good_id"]]
                goods.append({
                    "seller_id": seller["id"],
                    "seller": seller["name"],
                    "good_id": good["id"],
                    "name": good["name"],
                    "emoji": good["emoji"],
                    "category": good["category"],
                    "cost_hint": None,
                })
    return goods


def snapshot(room: dict, token: str | None, spectator: bool = False) -> dict:
    round_end_if_needed(room)
    player = next((p for p in room["players"].values() if p["token"] == token and p["status"] != "kicked"), None)
    now = time.time()
    negotiation_list = []
    for negotiation in room["negotiations"].values():
        view = {key: value for key, value in negotiation.items()}
        view["can_respond"] = player and player["id"] == negotiation["recipient_id"] and not spectator
        view["can_withdraw"] = player and player["id"] in (negotiation["maker_id"], negotiation["recipient_id"]) and not spectator
        view["seconds_left"] = max(0, int(negotiation["expires_at"] - now))
        negotiation_list.append(view)
    public = {
        "code": room["code"],
        "phase": room["phase"],
        "round": room["round"],
        "rounds": 4,
        "seconds_left": max(0, int(room["round_ends_at"] - now)) if room["phase"] == "playing" and not room["paused"] else int(room.get("remaining", ROUND_SECONDS)),
        "paused": room["paused"],
        "event": room["event"] and {
            **room["event"],
            "seconds_left": max(0, int(room["event"].get("ends_at", now) - now)),
        },
        "scores": scores_snapshot(room),
        "market": market_snapshot(room),
        "negotiations": negotiation_list,
        "deals": [
            {key: value for key, value in deal.items() if key not in ("profit", "savings")}
            for deal in room["deals"][-20:]
        ],
        "feed": room["feed"],
        "awards": room.get("awards", []),
        "host_id": room["host_id"],
        "human_count": sum(1 for p in room["players"].values() if not p["is_bot"] and p["status"] not in ("waiting", "kicked")),
        "bot_count": sum(1 for p in room["players"].values() if p["is_bot"] and p["status"] not in ("waiting", "kicked")),
        "player_id": player["id"] if player else None,
        "is_host": bool(player and player["id"] == room["host_id"]),
        "spectator": spectator,
        "is_player": bool(player and player["status"] != "waiting"),
        "waiting_next_round": bool(player and player["status"] == "waiting"),
        "version": room["version"],
    }
    if player and not spectator:
        public["you"] = {
            "id": player["id"],
            "name": player["name"],
            "wallet": player.get("wallet", 10000),
            "score": player["score"],
            "round_profit": player.get("round_profit", 0),
            "round_savings": player.get("round_savings", 0),
            "sales": [
                {
                    **sale,
                    "effective_cost": money(sale["cost"] * (1.2 if room["event"] and room["event"]["id"] == "fuel" else 1)),
                    "name": GOOD_BY_ID[sale["good_id"]]["name"],
                    "emoji": GOOD_BY_ID[sale["good_id"]]["emoji"],
                    "category": GOOD_BY_ID[sale["good_id"]]["category"],
                }
                for sale in player.get("sales", [])
            ],
            "shopping": [
                {
                    "good_id": good_id,
                    "value": value * (2 if room["event"] and room["event"].get("id") == "owambe" and room["event"].get("category") == GOOD_BY_ID[good_id]["category"] else 1),
                    "base_value": value,
                    "name": GOOD_BY_ID[good_id]["name"],
                    "emoji": GOOD_BY_ID[good_id]["emoji"],
                    "category": GOOD_BY_ID[good_id]["category"],
                    "bought": good_id in player.get("shopping_bought", set()),
                }
                for good_id, value in player.get("shopping", {}).items()
            ],
            "is_bot": player["is_bot"],
        }
    return public


def participant(room: dict, token: str) -> dict:
    player = next((p for p in room["players"].values() if p["token"] == token), None)
    if not player or player["status"] == "kicked":
        raise ValueError("Your seat has expired. Rejoin with the room code.")
    return player


def find_room(code: str) -> dict:
    room = ROOMS.get(code.upper().strip())
    if not room:
        raise ValueError("Room not found. Check the code and try again.")
    return room


def make_room(name: str) -> tuple[dict, dict]:
    clean_name = validate_name(name)
    while True:
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(4))
        if code not in ROOMS:
            break
    token, player_id = secrets.token_urlsafe(24), secrets.token_hex(8)
    player = make_player(clean_name, token, player_id, False)
    room = {
        "code": code,
        "players": {player_id: player},
        "host_id": player_id,
        "host_token": token,
        "phase": "lobby",
        "round": 0,
        "round_ends_at": 0,
        "remaining": ROUND_SECONDS,
        "paused": False,
        "pause_started": None,
        "event": None,
        "random_event_used": False,
        "next_event_at": 0,
        "deals": [],
        "negotiations": {},
        "feed": [],
        "version": 1,
        "updated_at": time.time(),
        "last_activity_at": time.time(),
        "awards": [],
    }
    ROOMS[code] = room
    add_feed(room, f"{clean_name} opened the market. Who's joining?", "event")
    return room, player


def make_player(name: str, token: str, player_id: str, is_bot: bool) -> dict:
    return {
        "id": player_id, "token": token, "name": name, "is_bot": is_bot, "status": "active",
        "online": not is_bot, "score": 0, "total_savings": 0, "total_spent": 0,
        "deals": 0, "sales": [], "shopping": {}, "wallet": 10000,
        "best_bargain_savings": 0, "best_generosity": 0,
        "round_profit": 0, "round_savings": 0, "shopping_bought": set(), "round_deals": 0,
        "last_seen": time.time(), "connections": 0,
    }


def validate_name(name: object) -> str:
    if not isinstance(name, str):
        raise ValueError("Enter a name to join the market.")
    clean = re.sub(r"\s+", " ", name).strip()
    if not clean or len(clean) > 18:
        raise ValueError("Names must be 1–18 characters.")
    if any(ord(char) < 32 for char in clean):
        raise ValueError("That name contains unsupported characters.")
    return clean


def host_only(room: dict, player: dict) -> None:
    if player["id"] != room["host_id"]:
        raise ValueError("Only the host can do that.")


def require_playing(room: dict) -> None:
    round_end_if_needed(room)
    if room["phase"] != "playing" or room["paused"]:
        raise ValueError("The market is not open right now.")


def maybe_start_event(room: dict) -> None:
    if room["phase"] != "playing" or room["paused"]:
        return
    if room["round_ends_at"] - time.time() <= 45 and (not room["event"] or room["event"]["id"] != "rush"):
        room["event"] = {"id": "rush", "name": "Closing rush", "emoji": "🏃", "description": "Profit and savings score 1.5×!", "ends_at": room["round_ends_at"]}
        add_feed(room, "Closing rush! Every deal scores 1.5× until the bell.", "event")
        return
    if room["event"]:
        return
    if room.get("random_event_used"):
        return
    if time.time() < room["next_event_at"] and room["round_ends_at"] - time.time() > 45:
        return
    event = dict(random.choice(EVENTS))
    room["event"] = {
        **event,
        "ends_at": time.time() + (30 if event["id"] == "blackout" else 38),
    }
    room["next_event_at"] = room["event"]["ends_at"]
    room["random_event_used"] = True
    if event["id"] == "owambe":
        event["category"] = random.choice(sorted({good["category"] for good in GOODS}))
        room["event"]["category"] = event["category"]
        room["event"]["description"] = f"{event['category']} shopping-list values are doubled."
    if event["id"] == "blackout":
        room["event"]["description"] = "30 seconds: only existing negotiations can be played."
    add_feed(room, f"{event['emoji']} {room['event']['name']}! {room['event']['description']}", "event")


def start_round(room: dict) -> None:
    players = active_players(room)
    if len(players) < 3:
        raise ValueError("Add traders until there are 3 active players, including bots.")
    if room["phase"] == "finished":
        room["round"] = 0
        room["deals"] = []
        room["awards"] = []
        for player in room["players"].values():
            if player["status"] == "kicked":
                continue
            player["score"] = player["total_savings"] = player["total_spent"] = player["deals"] = 0
            player["best_bargain_savings"] = player["best_generosity"] = 0
    room["round"] += 1
    new_round_data(players, room["round"])
    room["phase"] = "playing"
    room["paused"] = False
    room["remaining"] = ROUND_SECONDS
    room["round_ends_at"] = time.time() + ROUND_SECONDS
    room["event"] = None
    room["random_event_used"] = False
    room["next_event_at"] = time.time() + random.randint(25, 48)
    room["negotiations"] = {}
    add_feed(room, f"Round {room['round']} is open! Find a deal.", "event")


def effective_seller_cost(room: dict, sale: dict) -> int:
    return money(sale["cost"] * (1.2 if room["event"] and room["event"]["id"] == "fuel" else 1))


def effective_buyer_value(room: dict, buyer: dict, sale: dict) -> int:
    value = buyer["shopping"][sale["good_id"]]
    if room["event"] and room["event"]["id"] == "owambe" and room["event"].get("category") == GOOD_BY_ID[sale["good_id"]]["category"]:
        return value * 2
    return value


def current_deal_terms(room: dict, buyer: dict, seller: dict, sale: dict, price: int) -> tuple[int, int, int]:
    total = price
    value = effective_buyer_value(room, buyer, sale)
    multiplier = 1.5 if room["event"] and room["event"]["id"] == "rush" else 1
    savings = money((value - total) * multiplier)
    profit = money((price - effective_seller_cost(room, sale)) * multiplier)
    return total, savings, profit


def apply_deal(room: dict, negotiation: dict) -> None:
    seller = room["players"].get(negotiation["seller_id"])
    buyer = room["players"].get(negotiation["buyer_id"])
    if not seller or not buyer:
        raise ValueError("A trader left before this deal could close.")
    sale = next((item for item in seller["sales"] if item["good_id"] == negotiation["good_id"] and item["available"]), None)
    if not sale:
        raise ValueError("That item just sold. Find another on the market.")
    total, savings, profit = current_deal_terms(room, buyer, seller, sale, negotiation["price"])
    if negotiation["good_id"] not in buyer["shopping"]:
        raise ValueError("That item isn't on the buyer's shopping list.")
    if negotiation["good_id"] in buyer["shopping_bought"]:
        raise ValueError("The buyer has already found this item.")
    if buyer["wallet"] < total:
        raise ValueError("The buyer doesn't have enough in their wallet.")
    if savings < 0 or profit < 0:
        raise ValueError("This offer doesn't work for both traders. Counter or walk away.")
    generosity = max(0, money((effective_buyer_value(room, buyer, sale) + effective_seller_cost(room, sale)) / 2 - negotiation["price"]))
    buyer["wallet"] -= total
    seller["wallet"] += negotiation["price"]
    buyer["total_spent"] += total
    buyer["round_savings"] += savings
    buyer["total_savings"] += savings
    buyer["score"] += savings
    buyer["shopping_bought"].add(negotiation["good_id"])
    buyer["deals"] += 1
    buyer["round_deals"] += 1
    seller["round_profit"] += profit
    seller["score"] += profit
    buyer["best_bargain_savings"] = max(buyer["best_bargain_savings"], savings)
    seller["best_generosity"] = max(seller["best_generosity"], generosity)
    seller["deals"] += 1
    seller["round_deals"] += 1
    sale["available"] = False
    good = GOOD_BY_ID[negotiation["good_id"]]
    deal = {
        "good_id": good["id"], "good": good["name"], "emoji": good["emoji"],
        "seller": seller["name"], "buyer": buyer["name"], "price": negotiation["price"],
        "transport": 0, "savings": savings, "profit": profit,
        "generosity": generosity,
        "round": room["round"], "at": time.time(),
    }
    room["deals"].append(deal)
    del room["negotiations"][negotiation["id"]]
    for other_id, other in list(room["negotiations"].items()):
        item_sold_to_this_buyer = other["good_id"] == negotiation["good_id"] and other["buyer_id"] == buyer["id"]
        seller_listing_sold = other["good_id"] == negotiation["good_id"] and other["seller_id"] == seller["id"]
        if item_sold_to_this_buyer or seller_listing_sold:
            del room["negotiations"][other_id]
            add_feed(room, f"Negotiation ended: {good['name']} has found a new home.", "event")
    if len(buyer["shopping_bought"]) == 3:
        buyer["score"] += 750
        buyer["total_savings"] += 750
        deal["bonus"] = 750
        add_feed(room, f"{buyer['name']} completed their list — ₦750 bonus!", "deal")
    room["version"] += 1
    add_feed(room, f"{buyer['name']} bought {good['name']} from {seller['name']} for ₦{negotiation['price']:,}!", "deal")


def respond_to_bot(room: dict, negotiation: dict) -> None:
    if negotiation["id"] not in room["negotiations"]:
        return
    bot = room["players"].get(negotiation["recipient_id"])
    if not bot or not bot["is_bot"]:
        return
    seller = room["players"].get(negotiation["seller_id"])
    buyer = room["players"].get(negotiation["buyer_id"])
    good = GOOD_BY_ID[negotiation["good_id"]]
    if not seller or not buyer:
        room["negotiations"].pop(negotiation["id"], None)
        return
    sale = next((s for s in seller["sales"] if s["good_id"] == negotiation["good_id"] and s["available"]), None)
    if not sale:
        room["negotiations"].pop(negotiation["id"], None)
        return
    total, savings, profit = current_deal_terms(room, buyer, seller, sale, negotiation["price"])
    if negotiation["good_id"] in buyer["shopping"] and buyer["wallet"] >= total and savings >= 0 and profit >= 0:
        apply_deal(room, negotiation)
        return
    if negotiation["counters"] >= 3:
        room["negotiations"].pop(negotiation["id"], None)
        add_feed(room, f"{bot['name']} walked away from {good['name']}.", "market")
        return
    if negotiation["seller_id"] == bot["id"]:
        floor = effective_seller_cost(room, sale)
        suggested = max(floor, min(negotiation["price"] + random.randint(100, 350), buyer["shopping"].get(negotiation["good_id"], floor)))
    else:
        value = buyer["shopping"].get(negotiation["good_id"], 0)
        suggested = min(value, max(effective_seller_cost(room, sale), negotiation["price"] - random.randint(100, 350)))
    if suggested <= 0 or suggested == negotiation["price"]:
        room["negotiations"].pop(negotiation["id"], None)
        add_feed(room, f"{bot['name']} walked away from {good['name']}.", "market")
        return
    negotiation["price"] = suggested
    negotiation["maker_id"] = bot["id"]
    negotiation["recipient_id"] = negotiation["buyer_id"] if bot["id"] == negotiation["seller_id"] else negotiation["seller_id"]
    negotiation["counters"] += 1
    negotiation["updated_at"] = time.time()
    negotiation["expires_at"] = negotiation["updated_at"] + NEGOTIATION_SECONDS
    add_feed(room, f"{bot['name']} countered at ₦{suggested:,} for {good['name']}.", "market")


def update_room_presence(room: dict, now: float) -> None:
    for person in room["players"].values():
        if not person["is_bot"] and person["connections"] == 0 and now - person["last_seen"] > 12:
            person["online"] = False
    host = room["players"].get(room["host_id"])
    if not host or host["online"] or now - host["last_seen"] < HOST_TRANSFER_SECONDS or room["phase"] == "finished":
        return
    next_host = next(
        (person for person in room["players"].values()
         if person["id"] != host["id"] and not person["is_bot"]
         and person["status"] == "active" and person["online"]),
        None,
    )
    if next_host:
        room["host_id"] = next_host["id"]
        room["host_token"] = next_host["token"]
        add_feed(room, f"{next_host['name']} took over as host.", "event")


def room_has_expired(room: dict, now: float) -> bool:
    return now - room["last_activity_at"] > ROOM_IDLE_SECONDS


def tick_rooms() -> None:
    while True:
        time.sleep(1)
        now = time.time()
        with LOCK:
            for code, room in list(ROOMS.items()):
                if room_has_expired(room, now):
                    del ROOMS[code]
                    continue
                update_room_presence(room, now)
                for negotiation_id, negotiation in list(room["negotiations"].items()):
                    if now >= negotiation["expires_at"]:
                        del room["negotiations"][negotiation_id]
                        add_feed(room, f"Negotiation expired: {negotiation['good']} is back on the board.", "event")
                if room["phase"] == "playing" and not room["paused"]:
                    round_end_if_needed(room)
                    if room["phase"] != "playing":
                        continue
                    maybe_start_event(room)
                    for negotiation in list(room["negotiations"].values()):
                        if negotiation["recipient_id"] in room["players"] and room["players"][negotiation["recipient_id"]]["is_bot"]:
                            respond_to_bot(room, negotiation)
                    room["version"] += 1


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        if os.environ.get("OJA_LOG_HTTP") == "1":
            super().log_message(fmt, *args)

    def set_security_headers(self, content_type: str = "application/json; charset=utf-8", cache: str = "no-store") -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")

    def json_response(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.set_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.set_security_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path)
        if path.path == "/api/health":
            return self.json_response({"ok": True, "game": "oja"})
        if path.path == "/api/events":
            return self.event_stream(urllib.parse.parse_qs(path.query))
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        if path.path not in assets:
            return self.json_response({"error": "Not found."}, 404)
        filename, content_type = assets[path.path]
        page = (ROOT / filename).read_bytes()
        self.send_response(200)
        self.set_security_headers(content_type)
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 32_768:
                return self.json_response({"error": "Request is too large."}, 413)
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("Invalid request.")
            action = urllib.parse.urlparse(self.path).path.removeprefix("/api/")
            with LOCK:
                result = self.action(action, data)
            self.json_response(result)
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            self.json_response({"error": str(error)}, 400)
        except BrokenPipeError:
            return

    def action(self, action: str, data: dict) -> dict:
        if action == "create":
            room, player = make_room(data.get("name"))
            room["updated_at"] = time.time()
            return {"ok": True, "code": room["code"], "token": player["token"], "state": snapshot(room, player["token"])}
        if action == "join":
            code = str(data.get("code", "")).strip().upper()
            room = find_room(code)
            name = validate_name(data.get("name"))
            token = data.get("token")
            reconnect = next((p for p in room["players"].values() if p["token"] == token), None) if isinstance(token, str) else None
            if reconnect and reconnect["status"] == "kicked":
                raise ValueError("You have been removed from this market.")
            if reconnect:
                reconnect["online"] = True
                reconnect["last_seen"] = time.time()
                if reconnect["status"] == "disconnected" and room["phase"] in ("lobby", "between"):
                    reconnect["status"] = "active"
                room["updated_at"] = time.time()
                room["last_activity_at"] = time.time()
                room["version"] += 1
                return {"ok": True, "code": code, "token": reconnect["token"], "state": snapshot(room, token)}
            existing = next((p for p in room["players"].values() if p["name"].casefold() == name.casefold() and p["status"] != "kicked"), None)
            if existing:
                raise ValueError("That name is already in this room. Rejoin with the same browser to restore your seat.")
            humans = sum(1 for p in room["players"].values() if not p["is_bot"] and p["status"] != "kicked")
            if humans >= MAX_HUMANS:
                raise ValueError("This room already has 8 human traders.")
            player_id = secrets.token_hex(8)
            token = secrets.token_urlsafe(24)
            player = make_player(name, token, player_id, False)
            if room["phase"] == "playing" or room["phase"] == "finished":
                player["status"] = "waiting"
            room["players"][player_id] = player
            room["updated_at"] = time.time()
            room["last_activity_at"] = time.time()
            room["version"] += 1
            add_feed(room, f"{name} joined the market." if player["status"] == "active" else f"{name} joined and will play next round.", "event")
            return {"ok": True, "code": code, "token": token, "state": snapshot(room, token)}
        if action == "state":
            room, player = self.get_authenticated_room(data)
            return {"ok": True, "state": snapshot(room, player["token"] if player else None, data.get("spectator") is True)}
        room, player = self.get_authenticated_room(data)
        if player is None:
            raise ValueError("Join this room to take that action.")
        room["updated_at"] = time.time()
        room["last_activity_at"] = time.time()
        if action == "start":
            host_only(room, player)
            if room["phase"] not in ("lobby", "between", "finished"):
                raise ValueError("The market is already open.")
            for late in room["players"].values():
                if late["status"] == "waiting":
                    late["status"] = "active"
                    late["online"] = True
            start_round(room)
        elif action == "pause":
            host_only(room, player)
            require_playing(room)
            room["remaining"] = max(0, room["round_ends_at"] - time.time())
            room["paused"] = True
            room["pause_started"] = time.time()
            add_feed(room, "The host paused the market.", "event")
        elif action == "resume":
            host_only(room, player)
            if room["phase"] != "playing" or not room["paused"]:
                raise ValueError("The market isn't paused.")
            room["round_ends_at"] = time.time() + room["remaining"]
            pause_duration = time.time() - room["pause_started"]
            if room["event"] and room["event"].get("ends_at"):
                room["event"]["ends_at"] += pause_duration
            for negotiation in room["negotiations"].values():
                negotiation["expires_at"] += pause_duration
            room["paused"] = False
            room["pause_started"] = None
            add_feed(room, "The host reopened the market.", "event")
        elif action == "end":
            host_only(room, player)
            room["phase"] = "finished"
            room["paused"] = False
            room["negotiations"] = {}
            room["remaining"] = 0
            room["event"] = None
            finalize_awards(room)
            add_feed(room, "The host closed the market.", "event")
        elif action == "kick":
            host_only(room, player)
            target = room["players"].get(data.get("target"))
            if not target or target["id"] == player["id"]:
                raise ValueError("Choose another player to remove.")
            target["status"] = "kicked"
            target["online"] = False
            for key, negotiation in list(room["negotiations"].items()):
                if target["id"] in (negotiation["seller_id"], negotiation["buyer_id"]):
                    del room["negotiations"][key]
            add_feed(room, f"{target['name']} was removed from the market.", "event")
        elif action == "transfer":
            host_only(room, player)
            target = room["players"].get(data.get("target"))
            if not target or target["is_bot"] or target["status"] in ("kicked", "waiting"):
                raise ValueError("Choose an active human trader.")
            room["host_id"] = target["id"]
            room["host_token"] = target["token"]
            add_feed(room, f"{target['name']} is now the host.", "event")
        elif action == "bot_add":
            host_only(room, player)
            bots = [p for p in room["players"].values() if p["is_bot"] and p["status"] != "kicked"]
            if len(bots) >= 7:
                raise ValueError("This market already has 7 bots.")
            bot_name = f"{random.choice(['Aunty', 'Baba', 'Oga', 'Mama', 'Tunde', 'Sade'])} {random.choice(['Bisi', 'Femi', 'Dele', 'Kemi', 'Jide', 'Tola'])}"
            while any(p["name"].casefold() == bot_name.casefold() and p["status"] != "kicked" for p in room["players"].values()):
                bot_name += "!"
            bot_id = secrets.token_hex(8)
            bot = make_player(bot_name, secrets.token_urlsafe(24), bot_id, True)
            if room["phase"] == "playing":
                bot["status"] = "waiting"
            room["players"][bot_id] = bot
            add_feed(room, f"{bot_name} joined the market." if bot["status"] == "active" else f"{bot_name} will join next round.", "event")
        elif action == "bot_remove":
            host_only(room, player)
            target = room["players"].get(data.get("target"))
            if not target or not target["is_bot"]:
                raise ValueError("Choose a bot to remove.")
            target["status"] = "kicked"
            for key, negotiation in list(room["negotiations"].items()):
                if target["id"] in (negotiation["seller_id"], negotiation["buyer_id"]):
                    del room["negotiations"][key]
            add_feed(room, f"{target['name']} left the market.", "event")
        elif action == "offer":
            require_playing(room)
            if room["event"] and room["event"]["id"] == "blackout":
                raise ValueError("NEPA blackout: wait until the lights return to start a new negotiation.")
            seller = room["players"].get(data.get("seller_id"))
            buyer = room["players"].get(data.get("buyer_id"))
            good_id, price, target_id = data.get("good_id"), data.get("price"), data.get("target_id")
            if not seller or not buyer or player["id"] not in (seller["id"], buyer["id"]) or seller["id"] == buyer["id"]:
                raise ValueError("Choose yourself and one other trader for this offer.")
            if seller["status"] != "active" or buyer["status"] != "active":
                raise ValueError("Choose two traders who are active in this round.")
            if not isinstance(price, int) or price < 1 or price > 10000:
                raise ValueError("Offers must be between ₦1 and ₦10,000.")
            sale = next((item for item in seller["sales"] if item["good_id"] == good_id and item["available"]), None)
            if not sale:
                raise ValueError("That item is not available from this seller.")
            if good_id not in buyer["shopping"]:
                raise ValueError("The buyer isn't shopping for this item. Choose another good or trader.")
            if good_id in buyer["shopping_bought"]:
                raise ValueError("The buyer has already checked this item off their list.")
            if buyer["wallet"] < price:
                raise ValueError("The buyer doesn't have enough in their wallet for that offer.")
            open_count = sum(1 for n in room["negotiations"].values() if player["id"] in (n["seller_id"], n["buyer_id"]))
            if open_count >= 2:
                raise ValueError("You already have 2 open negotiations. Close one to start another.")
            recipient_id = target_id
            if not recipient_id or recipient_id not in (seller["id"], buyer["id"]) or recipient_id == player["id"]:
                recipient_id = buyer["id"] if player["id"] == seller["id"] else seller["id"]
            if any(n["seller_id"] == seller["id"] and n["buyer_id"] == buyer["id"] and n["good_id"] == good_id for n in room["negotiations"].values()):
                raise ValueError("You already have a negotiation for this item.")
            negotiation = {
                "id": secrets.token_hex(6), "seller_id": seller["id"], "seller": seller["name"],
                "buyer_id": buyer["id"], "buyer": buyer["name"], "good_id": good_id,
                "good": GOOD_BY_ID[good_id]["name"], "emoji": GOOD_BY_ID[good_id]["emoji"],
                "price": price, "maker_id": player["id"], "recipient_id": recipient_id,
                "counters": 0, "opened_at": time.time(), "updated_at": time.time(),
                "expires_at": time.time() + NEGOTIATION_SECONDS,
            }
            room["negotiations"][negotiation["id"]] = negotiation
            add_feed(room, f"{player['name']} opened a negotiation for {negotiation['good']}.", "market")
            if room["players"][recipient_id]["is_bot"]:
                respond_to_bot(room, negotiation)
        elif action in ("accept", "counter", "walk"):
            require_playing(room)
            negotiation = room["negotiations"].get(data.get("negotiation_id"))
            if not negotiation or negotiation["recipient_id"] != player["id"]:
                raise ValueError("That negotiation is no longer waiting for you.")
            if time.time() >= negotiation["expires_at"]:
                del room["negotiations"][negotiation["id"]]
                add_feed(room, f"Negotiation expired: {negotiation['good']} is back on the board.", "event")
                raise ValueError("This negotiation expired. Start a fresh offer.")
            seller = room["players"].get(negotiation["seller_id"])
            buyer = room["players"].get(negotiation["buyer_id"])
            sale = next((s for s in seller["sales"] if s["good_id"] == negotiation["good_id"]), None) if seller else None
            if not sale or not sale["available"]:
                del room["negotiations"][negotiation["id"]]
                raise ValueError("That item just sold. Find another on the market.")
            if action == "accept":
                apply_deal(room, negotiation)
            elif action == "walk":
                del room["negotiations"][negotiation["id"]]
                add_feed(room, f"{player['name']} walked away from {negotiation['good']}.", "market")
            else:
                if negotiation["counters"] >= 3:
                    raise ValueError("This negotiation has reached its limit of 3 counters.")
                price = data.get("price")
                if not isinstance(price, int) or price < 1 or price > 10000:
                    raise ValueError("Counteroffers must be between ₦1 and ₦10,000.")
                if not buyer or not seller:
                    raise ValueError("A trader left this negotiation.")
                if negotiation["good_id"] not in buyer["shopping"]:
                    raise ValueError("That trader can't accept this item. Choose another good or person.")
                if buyer["wallet"] < price:
                    raise ValueError("The buyer can't afford that counteroffer.")
                negotiation["price"] = price
                negotiation["maker_id"] = player["id"]
                negotiation["recipient_id"] = negotiation["buyer_id"] if player["id"] == negotiation["seller_id"] else negotiation["seller_id"]
                negotiation["counters"] += 1
                negotiation["updated_at"] = time.time()
                negotiation["expires_at"] = negotiation["updated_at"] + NEGOTIATION_SECONDS
                add_feed(room, f"{player['name']} countered at ₦{price:,} for {negotiation['good']}.", "market")
                if room["players"][negotiation["recipient_id"]]["is_bot"]:
                    respond_to_bot(room, negotiation)
        else:
            raise ValueError("Unknown action.")
        room["version"] += 1
        return {"ok": True, "state": snapshot(room, player["token"])}

    def get_authenticated_room(self, data: dict) -> tuple[dict, dict | None]:
        room = find_room(str(data.get("code", "")))
        token = data.get("token")
        player = None
        if isinstance(token, str) and token:
            player = participant(room, token)
            player["online"] = True
            player["last_seen"] = time.time()
        return room, player

    def event_stream(self, query: dict) -> None:
        code = query.get("code", [""])[0].upper()
        token = query.get("token", [""])[0]
        spectator = query.get("spectator", [""])[0] == "1"
        self.send_response(200)
        self.set_security_headers("text/event-stream; charset=utf-8", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last_version = -1
        try:
            while True:
                with LOCK:
                    room = ROOMS.get(code)
                    if room is None:
                        payload = {"error": "This market has closed or expired."}
                        version = 0
                    else:
                        player = next((p for p in room["players"].values() if p["token"] == token), None)
                        if player:
                            if player["status"] == "kicked":
                                payload = {"error": "You have been removed from this market."}
                                version = -1
                                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
                                self.wfile.flush()
                                return
                            player["connections"] += 1 if last_version == -1 else 0
                            player["connections"] = max(1, player["connections"])
                            player["online"] = True
                            player["last_seen"] = time.time()
                        payload = snapshot(room, token, spectator)
                        version = payload["version"]
                if version != last_version:
                    self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_version = version
                else:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            with LOCK:
                room = ROOMS.get(code)
                player = next((p for p in room["players"].values() if p["token"] == token), None) if room else None
                if player:
                    player["connections"] = max(0, player["connections"] - 1)
                    if player["connections"] == 0:
                        player["online"] = False
                        player["last_seen"] = time.time()


def main() -> None:
    threading.Thread(target=tick_rooms, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"Ọjà is serving on http://0.0.0.0:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
