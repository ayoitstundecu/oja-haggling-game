"""Authoritative gameplay and HTTP smoke tests for Ọjà."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import server


class GameRulesTests(unittest.TestCase):
    def setUp(self):
        server.ROOMS.clear()
        self.handler = object.__new__(server.Handler)
        self.room, self.host = server.make_room("Ayo")
        joined = self.action("join", name="Tolu")
        self.buyer = server.participant(self.room, joined["token"])
        joined = self.action("join", name="Sade")
        self.third = server.participant(self.room, joined["token"])
        self.prepare_round()

    def action(self, action, player=None, **values):
        return self.handler.action(action, {
            "code": self.room["code"],
            "token": player["token"] if player else "",
            **values,
        })

    def prepare_round(self):
        self.action("start", self.host)
        self.room["event"] = None
        self.seller = self.host
        self.goods = {
            "tomatoes": {"good_id": "tomatoes", "cost": 1000, "available": True},
            "pepper": {"good_id": "pepper", "cost": 1200, "available": True},
            "yam": {"good_id": "yam", "cost": 1500, "available": True},
        }
        self.seller["sales"] = list(self.goods.values())
        self.buyer["shopping"] = {"tomatoes": 3000, "pepper": 3300, "yam": 3700}
        self.buyer["wallet"] = 10000

    def offer(self, seller, buyer, good_id="tomatoes", price=2000):
        result = self.action(
            "offer", seller, seller_id=seller["id"], buyer_id=buyer["id"],
            target_id=buyer["id"], good_id=good_id, price=price,
        )
        return self.room["negotiations"][next(iter(self.room["negotiations"]))]

    def test_four_character_room_code_and_duplicate_name_reconnect(self):
        self.assertEqual(len(self.room["code"]), 4)
        with self.assertRaisesRegex(ValueError, "already in this room"):
            self.action("join", name="ayo")
        self.host["wallet"] = 4321
        result = self.action("join", name="Ayo", token=self.host["token"])
        self.assertEqual(result["state"]["player_id"], self.host["id"])
        self.assertEqual(result["state"]["you"]["wallet"], 4321)

    def test_private_hand_and_public_spectator_snapshot(self):
        seller_state = server.snapshot(self.room, self.seller["token"])
        buyer_state = server.snapshot(self.room, self.buyer["token"])
        spectator_state = server.snapshot(self.room, None, spectator=True)
        self.assertEqual(len(seller_state["you"]["sales"]), 3)
        self.assertEqual(len(seller_state["you"]["shopping"]), 3)
        self.assertEqual(len(buyer_state["you"]["shopping"]), 3)
        self.assertNotIn("you", spectator_state)
        self.assertNotIn('"cost":', json.dumps(spectator_state))

    def test_successful_deal_scores_both_sides_and_hides_private_profit(self):
        negotiation = self.offer(self.seller, self.buyer)
        result = self.action("accept", self.buyer, negotiation_id=negotiation["id"])
        self.assertEqual(result["state"]["you"]["wallet"], 8000)
        self.assertEqual(self.seller["wallet"], 12000)
        self.assertEqual(self.buyer["score"], 1000)
        self.assertEqual(self.seller["score"], 1000)
        self.assertFalse(self.goods["tomatoes"]["available"])
        self.assertNotIn("savings", result["state"]["deals"][-1])
        self.assertNotIn("profit", result["state"]["deals"][-1])

    def test_invalid_offer_is_rejected_without_opening_negotiation(self):
        with self.assertRaisesRegex(ValueError, "between ₦1 and ₦10,000"):
            self.offer(self.seller, self.buyer, price=10001)
        self.assertEqual(self.room["negotiations"], {})

    def test_completing_shopping_list_awards_bonus_and_closes_related_negotiations(self):
        self.buyer["shopping"]["tomatoes"] = 3000
        self.buyer["shopping"]["pepper"] = 3300
        self.buyer["shopping"]["yam"] = 3700
        for good_id, price in (("tomatoes", 2000), ("pepper", 2100), ("yam", 2200)):
            negotiation = self.offer(self.seller, self.buyer, good_id, price)
            self.action("accept", self.buyer, negotiation_id=negotiation["id"])
        self.assertEqual(self.buyer["score"], 4450)
        self.assertEqual(self.room["deals"][-1]["bonus"], 750)
        self.assertEqual(self.buyer["shopping_bought"], {"tomatoes", "pepper", "yam"})

    def test_fun_awards_use_shopping_savings_bargain_and_generosity(self):
        negotiation = self.offer(self.seller, self.buyer, price=1500)
        self.action("accept", self.buyer, negotiation_id=negotiation["id"])
        self.action("end", self.host)
        self.assertEqual(
            [(award["title"], award["name"]) for award in self.room["awards"]],
            [("Shrewdest Trader", "Tolu"), ("Best Bargain", "Tolu"), ("Softest Heart", "Ayo")],
        )
        replay = self.action("start", self.host)
        self.assertEqual(replay["state"]["round"], 1)
        self.assertEqual(self.host["score"], 0)

    def test_fuel_scarcity_increases_seller_cost_not_buyer_transport(self):
        self.room["event"] = {"id": "fuel", "description": "Seller costs +20%."}
        seller_state = server.snapshot(self.room, self.seller["token"])
        self.assertEqual(seller_state["you"]["sales"][0]["effective_cost"], 1200)
        negotiation = self.offer(self.seller, self.buyer)
        result = self.action("accept", self.buyer, negotiation_id=negotiation["id"])
        self.assertEqual(self.buyer["wallet"], 8000)
        self.assertEqual(self.seller["wallet"], 12000)
        self.assertEqual(self.seller["score"], 800)
        self.assertEqual(result["state"]["deals"][-1]["transport"], 0)

    def test_owambe_and_closing_rush_multipliers(self):
        self.room["event"] = {"id": "owambe", "category": "Fresh food"}
        negotiation = self.offer(self.seller, self.buyer)
        self.action("accept", self.buyer, negotiation_id=negotiation["id"])
        self.assertEqual(self.buyer["score"], 4000)
        self.assertEqual(self.seller["score"], 1000)

        self.goods["pepper"]["available"] = True
        self.buyer["shopping_bought"].clear()
        self.room["event"] = {"id": "rush"}
        negotiation = self.offer(self.seller, self.buyer, "pepper", 2000)
        self.action("accept", self.buyer, negotiation_id=negotiation["id"])
        self.assertEqual(self.seller["score"], 2200)

    def test_blackout_blocks_new_offers_but_existing_deal_can_close(self):
        negotiation = self.offer(self.seller, self.buyer, "pepper", 2000)
        self.room["event"] = {"id": "blackout"}
        with self.assertRaisesRegex(ValueError, "NEPA blackout"):
            self.offer(self.seller, self.buyer, "yam", 2000)
        result = self.action("accept", self.buyer, negotiation_id=negotiation["id"])
        self.assertEqual(len(result["state"]["deals"]), 1)

    def test_three_counters_limit_and_final_offer_remains_actionable(self):
        negotiation = self.offer(self.seller, self.buyer)
        for index in range(3):
            recipient = self.room["players"][negotiation["recipient_id"]]
            self.action("counter", recipient, negotiation_id=negotiation["id"], price=2100 + 50 * index)
        self.assertEqual(negotiation["counters"], 3)
        with self.assertRaisesRegex(ValueError, "limit of 3 counters"):
            recipient = self.room["players"][negotiation["recipient_id"]]
            self.action("counter", recipient, negotiation_id=negotiation["id"], price=2300)
        recipient = self.room["players"][negotiation["recipient_id"]]
        result = self.action("accept", recipient, negotiation_id=negotiation["id"])
        self.assertEqual(len(result["state"]["deals"]), 1)

    def test_open_negotiation_limit_and_race_cancel_other_bids(self):
        second_buyer = self.third
        second_buyer["shopping"] = {"tomatoes": 3000, "pepper": 3000, "yam": 3000}
        negotiation = self.offer(self.seller, self.buyer, "tomatoes")
        self.offer(self.seller, second_buyer, "tomatoes", 1900)
        with self.assertRaisesRegex(ValueError, "2 open negotiations"):
            self.offer(self.seller, second_buyer, "pepper", 2100)
        self.action("accept", self.buyer, negotiation_id=negotiation["id"])
        self.assertEqual(self.room["negotiations"], {})
        with self.assertRaisesRegex(ValueError, "no longer waiting"):
            self.action("accept", second_buyer, negotiation_id="cancelled-race")

    def test_negotiation_expiry_and_paused_time(self):
        negotiation = self.offer(self.seller, self.buyer)
        start_expiry = negotiation["expires_at"]
        self.action("pause", self.host)
        self.room["pause_started"] -= 60
        self.action("resume", self.host)
        self.assertGreaterEqual(negotiation["expires_at"], start_expiry + 60)
        negotiation["expires_at"] = time.time() - 1
        with self.assertRaisesRegex(ValueError, "expired"):
            self.action("accept", self.buyer, negotiation_id=negotiation["id"])
        self.assertNotIn(negotiation["id"], self.room["negotiations"])

    def test_one_random_event_per_round_and_final_rush(self):
        self.room["next_event_at"] = 0
        blackout = next(event for event in server.EVENTS if event["id"] == "blackout")
        with patch("server.random.choice", return_value=blackout):
            server.maybe_start_event(self.room)
        self.assertTrue(self.room["random_event_used"])
        self.room["event"]["ends_at"] = time.time() - 1
        server.round_end_if_needed(self.room)
        server.maybe_start_event(self.room)
        self.assertIsNone(self.room["event"])
        self.room["round_ends_at"] = time.time() + 40
        server.maybe_start_event(self.room)
        self.assertEqual(self.room["event"]["id"], "rush")

    def test_host_transfers_to_connected_human_after_disconnect(self):
        next_host = self.buyer
        self.host["online"] = False
        self.host["last_seen"] = time.time() - server.HOST_TRANSFER_SECONDS - 1
        next_host["online"] = True
        next_host["connections"] = 1
        server.update_room_presence(self.room, time.time())
        self.assertEqual(self.room["host_id"], next_host["id"])
        self.assertEqual(self.room["host_token"], next_host["token"])

    def test_idle_room_expires_after_thirty_minutes_without_activity(self):
        self.assertEqual(server.ROOM_IDLE_SECONDS, 30 * 60)
        created = self.room["last_activity_at"]
        self.assertFalse(server.room_has_expired(self.room, created + server.ROOM_IDLE_SECONDS))
        self.assertTrue(server.room_has_expired(self.room, created + server.ROOM_IDLE_SECONDS + 1))

    def test_late_joiner_waits_for_next_round(self):
        result = self.action("join", name="Late trader")
        late = server.participant(self.room, result["token"])
        self.assertEqual(late["status"], "waiting")
        self.assertTrue(result["state"]["waiting_next_round"])
        self.assertTrue(next(person for person in result["state"]["scores"] if person["id"] == late["id"])["waiting_next_round"])

    def test_solo_host_can_add_a_bot_and_start(self):
        server.ROOMS.clear()
        room, host = server.make_room("Solo")
        code = room["code"]
        with self.assertRaisesRegex(ValueError, "3 active players"):
            self.handler.action("start", {"code": code, "token": host["token"]})
        self.handler.action("bot_add", {"code": code, "token": host["token"]})
        self.handler.action("bot_add", {"code": code, "token": host["token"]})
        bot = next(player for player in room["players"].values() if player["is_bot"])
        self.handler.action("bot_remove", {"code": code, "token": host["token"], "target": bot["id"]})
        state = server.snapshot(room, host["token"])
        self.assertEqual(state["bot_count"], 1)
        with self.assertRaisesRegex(ValueError, "3 active players"):
            self.handler.action("start", {"code": code, "token": host["token"]})
        self.handler.action("bot_add", {"code": code, "token": host["token"]})
        result = self.handler.action("start", {"code": code, "token": host["token"]})
        self.assertEqual(result["state"]["phase"], "playing")
        self.assertEqual(result["state"]["bot_count"], 2)


class HttpSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        cls.port = probe.getsockname()[1]
        probe.close()
        env = {**os.environ, "PORT": str(cls.port)}
        cls.process = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=Path(__file__).resolve().parent,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.base = f"http://127.0.0.1:{cls.port}"
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(cls.base + "/api/health", timeout=1):
                    break
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        else:
            cls.process.terminate()
            raise RuntimeError("The HTTP server did not start for smoke tests.")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)

    def post(self, action, payload):
        request = urllib.request.Request(
            f"{self.base}/api/{action}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_live_http_create_join_deal_and_public_spectator(self):
        with urllib.request.urlopen(self.base + "/") as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"app.js", response.read())
        for asset in ("/style.css", "/app.js"):
            with urllib.request.urlopen(self.base + asset) as response:
                self.assertEqual(response.status, 200)
                self.assertGreater(int(response.headers["Content-Length"]), 100)

        _, owner_data = self.post("create", {"name": "Host"})
        code, host_token = owner_data["code"], owner_data["token"]
        self.assertEqual(len(code), 4)
        traders = [(host_token, owner_data["state"])]
        for name in ("Bisi", "Femi", "Tola", "Jide", "Kemi", "Sade", "Dele"):
            _, joined = self.post("join", {"code": code, "name": name})
            traders.append((joined["token"], joined["state"]))
        status, _ = self.post("start", {"code": code, "token": host_token})
        self.assertEqual(status, 200)
        round_states = []
        for token, _ in traders:
            _, current = self.post("state", {"code": code, "token": token})
            round_states.append((token, current["state"]))
        seller_token, seller_state = round_states[0]
        matched = None
        for buyer_token, buyer_state in round_states[1:]:
            wanted = {item["good_id"]: item for item in buyer_state["you"]["shopping"]}
            for sale in seller_state["you"]["sales"]:
                item = wanted.get(sale["good_id"])
                if item and item["value"] > sale["cost"]:
                    matched = (buyer_token, sale, item)
                    break
            if matched:
                break
        self.assertIsNotNone(matched, "Eight independent hands should share at least one tradable good.")
        buyer_token, sale, item = matched
        price = (sale["cost"] + item["value"]) // 2
        status, offer_response = self.post("offer", {
            "code": code, "token": seller_token,
            "seller_id": seller_state["player_id"],
            "buyer_id": next(state["player_id"] for token, state in round_states if token == buyer_token),
            "target_id": next(state["player_id"] for token, state in round_states if token == buyer_token),
            "good_id": sale["good_id"], "price": price,
        })
        self.assertEqual(status, 200)
        negotiation_id = offer_response["state"]["negotiations"][0]["id"]
        status, accepted = self.post("accept", {"code": code, "token": buyer_token, "negotiation_id": negotiation_id})
        self.assertEqual(status, 200)
        self.assertEqual(accepted["state"]["deals"][-1]["price"], price)
        self.assertNotIn("you", serverless_spectator := self.sse_snapshot(code))
        self.assertTrue(serverless_spectator["spectator"])

    def sse_snapshot(self, code):
        request = urllib.request.Request(f"{self.base}/api/events?code={code}&spectator=1")
        response = urllib.request.urlopen(request, timeout=4)
        try:
            line = response.readline().decode()
            return json.loads(line.removeprefix("data: ").strip())
        finally:
            response.close()


if __name__ == "__main__":
    unittest.main()
