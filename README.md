# Ọjà: The Haggling Game

**Tagline:** Talk sweet. Trade smart.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/ayoitstundecu/oja-haggling-game)

## Project description (150 words)

Ọjà is a multiplayer haggling game set in a bustling Lagos open-air market. Gather three to eight people in a private room, or add a bot for solo practice. Each three-minute round gives every trader ₦10,000, three goods to sell at secret costs, and a private shopping list with values. Make offers, counter up to three times, accept a bargain, or walk away; juggle two negotiations at once. Sellers earn profit above cost, shoppers score savings, and completing a list earns a bonus. Blackouts, fuel scarcity, Owambe celebrations, and a final closing rush keep every round lively. Share a four-character room code or public big-screen link, follow the live board, and celebrate the final leaderboard with awards. The lightweight Python server owns room state, timers, trades, and scoring; browsers stay synchronized through server-sent events. Play on phones, laptops, or any shared screen—no accounts, downloads, real money, or paid services required.

## Run locally

Requires Python 3.9 or newer; there are no Python package dependencies.

```sh
python3 server.py
```

Open `http://localhost:8000`, create a room, then join from another browser, private window, phone, or computer on the same Wi-Fi. For another device, use the host computer's local network address (for example, `http://192.168.1.20:8000`) and allow the port through its firewall. The host can add two bots to play alone.

## Test

```sh
python3 -m unittest discover -v
```

The suite covers room creation/rejoin and duplicate names; private hands and public spectators; offers, acceptance, scoring, list-completion bonus, races, counter limits, expiry, pause/resume, events, host handoff, late joining, bots, and a live HTTP room that completes a real trade. Follow the multi-tab walkthrough below to test the actual browser client; no JavaScript runtime is needed by the game server.

## Share and spectate

Room codes contain four characters. Copy the invite or scan the QR code to join with a name. The **Big Screen** link opens `/?view=CODE` in a new tab; it shares the leaderboard, event, market, and deal feed, never a private hand. Spectators are read-only. An in-progress human joiner appears as waiting and receives a fresh hand next round.

## Milestone walkthrough

Use three browser tabs (A, B, C) at `http://localhost:8000`; for physical devices on the same Wi-Fi, replace `localhost` with the host computer's LAN address.

1. **Lobby and sync:** In A create a room as `Host`; copy its four-character code. In B and C join using distinct names. Confirm all three players appear in each lobby immediately. Refresh A in the same browser and verify its host seat is restored; try joining again as `Host` in a private window and verify duplicate-name protection.
2. **Haggling and scoring:** A starts Round 1. In each private hand, look for a good that appears on a different trader's shopping list. Open an offer, send it, then accept or counter it from the other tab. Verify the board removes the sold unit and both wallets, deal counts, and leaderboard scores update. Try up to three counters, walk away, and accept competing offers for one unit to verify the second negotiation closes.
3. **Events, bots, and public board:** Create a fresh room with one human; add two bots and start the round. Wait for the scheduled event (within roughly 25–48 seconds), inspect its countdown and rule, then wait until the last 45 seconds for the closing rush. Use **Big Screen** to open a spectator tab; confirm it shows the live board and scores but no private hand. Join from another tab mid-round and confirm the trader is marked for next round.
4. **Onboarding and polish:** Open a private window with cleared site data and step through the three intro cards. Open a listed good and verify the first-offer tip appears; complete an offer and verify it does not return. Toggle sound, inspect the host's QR code or scan it with a phone, and check that the phone layout keeps the wallet and deal buttons usable.

To test automatic host transfer, keep B connected as another human and close A's room tab; within about 15 seconds B should receive host controls. Reopening A in the same browser reconnects its player seat without taking host control back.

## Rules at a glance

- Four 180-second rounds; at least three active traders (humans and/or bots) are needed to start. A new round starts when the host presses **Open Round**.
- Each active trader gets a ₦10,000 wallet, three one-off goods with a private base cost, and three private shopping values.
- When a trade closes, the buyer pays the seller's quoted price; sales proceeds join the seller's wallet for later purchases.
- Negotiate listed items with a single other trader. An offer expires after 30 seconds, each trader can keep two offers open, and three counters are allowed. After counter three, the other trader can accept or walk away but cannot counter again.
- A completed sale adds seller profit (price minus their current cost basis) and buyer savings (their shopping value minus price). Finishing a shopping list adds 750 points.
- NEPA blackout lasts 30 seconds and blocks new negotiations. Fuel scarcity raises the seller's cost basis by 20%. Owambe doubles the named category's shopping value. The last 45 seconds are a 1.5× closing rush.
- The host can pause/resume, add/remove bots, remove or transfer players, and end the game. An idle market expires after 30 minutes; a disconnected host passes control to a connected human.

## Public deployment

`render.yaml` is a no-build, no-dependency blueprint for a single Render web service. Push this folder to a GitHub repository, then select **New → Blueprint** in Render and connect that repository; Render will provide the public URL. No secrets or paid services are needed to build this demo. On a free instance, the service may sleep while idle.

You can also use the **Deploy to Render** button above to open the preconfigured blueprint directly.

**Live game:** [https://oja-haggling-game.onrender.com](https://oja-haggling-game.onrender.com) · Health: [/api/health](https://oja-haggling-game.onrender.com/api/health)

Room data lives in process memory: a server restart or free-tier sleep that restarts the process clears active rooms. Keep one server instance per deployment; horizontal replicas would not share room state. The QR image uses the free `api.qrserver.com` image endpoint, and the optional display fonts load from Google Fonts; gameplay itself has no runtime package dependency.

## Troubleshooting

- Check server health at `/api/health`.
- If a room code is unknown, the room may have expired after 30 minutes without activity or its server process restarted.
- If another trader disconnects, their open offer expires; a host that drops offline transfers control to an available human after a short grace period.
- The game uses same-origin HTTP and server-sent events. If a device cannot join over the LAN, check the host firewall and confirm both devices use the same network.
