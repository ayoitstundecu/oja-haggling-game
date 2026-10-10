(() => {
  "use strict";

  const app = document.querySelector("#app");
  const overlayRoot = document.querySelector("#overlay-root");
  const toastStack = document.querySelector("#toast-stack");
  const soundButton = document.querySelector("#sound-toggle");
  const helpButton = document.querySelector("#help-button");
  const query = new URLSearchParams(location.search);
  const spectatorCode = query.get("view");
  const model = {
    code: null, token: null, state: null, stream: null, lastSignature: "",
    mode: "create", offerCard: null, pending: false, lastDealCount: 0,
    sound: localStorage.getItem("oja-sound") !== "off",
    onboardingPage: 0, onboardingOpen: !spectatorCode && !localStorage.getItem("oja-onboarded"),
    spectator: Boolean(spectatorCode), roomError: "",
  };

  const onboardingCards = [
    { emoji: "🧺", title: "Welcome to Ọjà", text: "Step into a bustling Lagos open-air market. Each round, you receive three secret-cost goods to sell and a private shopping list of three things you want." },
    { emoji: "🤝", title: "Talk your way to a deal", text: "Pick a trader and start an offer. Accept, counter up to three times, or walk away. You can keep two negotiations open at once. The best deals earn both sides points." },
    { emoji: "🏆", title: "Spend smart. Sell sharp.", text: "Sellers score profit over their secret cost; shoppers score savings against their private values. Complete your list for a bonus. Four rounds decide who rules the market." },
  ];

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
  }

  function naira(value) {
    return `₦${Math.max(0, Number(value) || 0).toLocaleString("en-NG")}`;
  }

  function toast(message, kind = "ok") {
    const node = document.createElement("div");
    node.className = `toast${kind === "error" ? " error" : ""}`;
    node.textContent = message;
    toastStack.append(node);
    window.setTimeout(() => node.remove(), 4400);
  }

  async function request(action, payload = {}) {
    const response = await fetch(`/api/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, code: payload.code || model.code, token: payload.token || model.token }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Something went wrong. Try again.");
    if (data.state) receiveState(data.state);
    return data;
  }

  function storageKey(code) { return `oja-room-${String(code).toUpperCase()}`; }

  function remember(code, token) {
    model.code = code;
    model.token = token;
    localStorage.setItem(storageKey(code), token);
    localStorage.setItem("oja-current-room", code);
    connectStream();
  }

  function connectStream() {
    if (model.stream) model.stream.close();
    const query = new URLSearchParams({ code: model.code });
    if (model.token) query.set("token", model.token);
    if (model.spectator) query.set("spectator", "1");
    model.stream = new EventSource(`/api/events?${query}`);
    model.stream.onmessage = event => {
      try {
        const data = JSON.parse(event.data);
        if (data.error) {
          model.roomError = data.error;
          if (model.state) {
            toast(data.error, "error");
            model.state = null;
            model.code = null;
            model.token = null;
            model.spectator = false;
            model.stream?.close();
            render();
          }
          return;
        }
        receiveState(data);
      } catch (error) {
        console.error("Market update could not be read.", error);
      }
    };
    model.stream.onerror = () => {
      if (model.state) document.body.dataset.connection = "reconnecting";
    };
    model.stream.onopen = () => { delete document.body.dataset.connection; };
  }

  function semanticSignature(state) {
    const clean = { ...state, version: undefined, seconds_left: undefined };
    if (clean.event) clean.event = { ...clean.event, seconds_left: undefined };
    clean.negotiations = clean.negotiations.map(({ seconds_left, ...negotiation }) => negotiation);
    return JSON.stringify(clean);
  }

  function receiveState(state) {
    const signature = semanticSignature(state);
    const oldDeals = model.state?.deals?.length || 0;
    model.state = state;
    if (state.deals?.length > oldDeals && model.sound) playDealSound();
    model.stateAt = Date.now();
    model.lastDealCount = state.deals?.length || 0;
    if (signature !== model.lastSignature) {
      model.lastSignature = signature;
      render();
    }
  }

  function playDealSound() {
    try {
      const audio = new (window.AudioContext || window.webkitAudioContext)();
      [660, 880].forEach((frequency, index) => {
        const oscillator = audio.createOscillator();
        const gain = audio.createGain();
        oscillator.frequency.value = frequency;
        oscillator.type = "sine";
        gain.gain.setValueAtTime(.001, audio.currentTime + index * .12);
        gain.gain.exponentialRampToValueAtTime(.14, audio.currentTime + index * .12 + .02);
        gain.gain.exponentialRampToValueAtTime(.001, audio.currentTime + index * .12 + .2);
        oscillator.connect(gain).connect(audio.destination);
        oscillator.start(audio.currentTime + index * .12);
        oscillator.stop(audio.currentTime + index * .12 + .21);
      });
    } catch (error) {
      console.debug("Audio is unavailable in this browser.", error);
    }
  }

  function render() {
    soundButton.textContent = model.sound ? "🔔" : "🔕";
    if (!model.state) {
      app.innerHTML = landingTemplate();
    } else if (model.state.phase === "lobby") {
      app.innerHTML = lobbyTemplate();
    } else if (model.state.phase === "between") {
      app.innerHTML = betweenTemplate();
    } else if (model.state.phase === "finished") {
      app.innerHTML = finishTemplate();
    } else {
      app.innerHTML = gameTemplate();
    }
    if (model.onboardingOpen) showOnboarding();
    else if (model.state?.phase === "playing" && !model.spectator && model.state.is_player && localStorage.getItem("oja-first-offer") !== "yes") {
      const target = document.querySelector(".market-grid");
      if (target && !target.querySelector(".first-tip")) {
        const tip = document.createElement("div");
        tip.className = "first-tip";
        tip.textContent = "Try your first offer: pick a trader's good, choose who trades, and agree a price. Each item is one-of-a-kind!";
        target.prepend(tip);
      }
    }
    updateClocks();
  }

  function landingTemplate() {
    return `
      <div class="landing-wrap">
        <section class="hero">
          <div class="hero-copy">
            <p class="eyebrow">A Lagos market. Your move.</p>
            <h1>Talk sweet.<br><em>Trade smart.</em></h1>
            <p>Read the room, make your pitch, and see who leaves Ọjà with the biggest bag.</p>
          </div>
          <div class="market-illustration" aria-hidden="true">
            <div class="sun"></div><div class="market-orbit">“Oya, bring it down!”</div>
            <div class="market-orbit orbit-two">🥭</div><div class="basket-art">🧺</div>
          </div>
        </section>
        <div class="landing-grid">
          <section class="panel entry-panel">
            <span class="section-label">Jump into the market</span>
            <h2>Make yourself at home.</h2>
            <div class="mode-tabs" role="tablist">
              <button class="mode-tab${model.mode === "create" ? " selected" : ""}" data-mode="create">Start a room</button>
              <button class="mode-tab${model.mode === "join" ? " selected" : ""}" data-mode="join">Join a room</button>
            </div>
            <form id="entry-form" class="entry-form">
              <label class="field-label">Your market name
                <input name="name" maxlength="18" placeholder="e.g. Kemi" autocomplete="nickname" required>
              </label>
              ${model.mode === "join" ? `<label class="field-label">Four-letter room code
                <input name="code" maxlength="4" placeholder="e.g. AJ4K" autocomplete="off" autocapitalize="characters" required>
              </label>` : ""}
              <button class="btn btn-primary wide" type="submit">${model.mode === "create" ? "🧺  Open a market" : "🤝  Join the haggling"}</button>
              <p class="form-footnote">${model.mode === "create" ? "Your friends join with a room code. Need company? Add bots once you’re in." : "Your same-name seat reconnects automatically in this browser."}</p>
            </form>
          </section>
          <section class="panel rules-panel">
            <span class="section-label">How it works</span><h2>Big energy. Better bargains.</h2>
            <div class="rule-row"><span class="rule-icon">🪙</span><div><b>₦10,000 to make moves</b><small>Start fresh each round with 3 hidden-cost goods and 3 things on your wish list.</small></div></div>
            <div class="rule-row"><span class="rule-icon">🤝</span><div><b>Negotiate your way there</b><small>Trade one-to-one, counter up to 3 times, and keep at most 2 deals in play.</small></div></div>
            <div class="rule-row"><span class="rule-icon">🏆</span><div><b>Four rounds. One market legend.</b><small>Score on savings and seller profit. Finish your list for a bonus!</small></div></div>
            <p class="fine-print">For 3–8 people, plus market-savvy bots. No downloads, accounts, or real money.</p>
          </section>
        </div>
      </div>`;
  }

  function playerRoster(scores, hostId) {
    if (!scores.length) return `<div class="empty-state">The market is quiet… for now.</div>`;
    return `<div class="roster">${scores.map((person, index) => `
      <div class="roster-row">
        <span class="roster-avatar">${person.is_bot ? "🤖" : ["🪷", "🌿", "🧿", "🥭"][index % 4]}</span>
        <span class="roster-name">${escapeHtml(person.name)}
          <small>${person.waiting_next_round ? "joins next round" : person.is_bot ? "market bot" : person.online ? "at the market" : "reconnecting"}${person.id === hostId ? " · host" : ""}</small>
        </span>
        ${person.id === hostId ? `<span class="host-tag">HOST</span>` : ""}
      </div>`).join("")}</div>`;
  }

  function roomHeading(title, subtitle, showShare = true) {
    return `<div class="room-heading">
      <div><h1>${title}</h1><p>${subtitle}</p></div>
      <div class="room-actions">
        ${model.spectator ? `<span class="spectator-label">📺 PUBLIC SCREEN</span>` : ""}
        ${!model.spectator ? `<a class="btn btn-outline btn-sm" href="${location.origin}/?view=${encodeURIComponent(model.state.code)}" target="_blank" rel="noopener noreferrer">📺 Big Screen</a>` : ""}
        <span class="room-code">ROOM <b>${escapeHtml(model.state.code)}</b>${showShare && !model.spectator ? `<button type="button" class="btn btn-sm btn-outline" data-action="copy-invite">↗ Invite</button>` : ""}</span>
      </div>
    </div>`;
  }

  function lobbyTemplate() {
    const state = model.state;
    const isHost = state.is_host;
    return `<div class="room-shell">
      ${roomHeading("The market is waking up 🧺", "Invite your crew. Add a bot for a solo practice run.")}
      <div class="lobby-layout">
        <section class="card lobby-card"><span class="section-label">AT THIS MARKET · ${state.human_count} TRADERS · ${state.bot_count} BOTS</span>
          <h2>First come, first haggles.</h2><p>Need at least 3 traders to start. Invite two friends, or add two bots for a solo practice run. The host can add up to 7 bots.</p>
          ${playerRoster(state.scores, state.host_id)}
          ${isHost ? `<div class="host-strip"><span class="section-label">HOST CONTROLS</span>
            <button class="btn btn-outline btn-sm" data-action="bot-add">🤖 Add a bot</button>
            ${state.scores.filter(p => p.is_bot).map(bot => `<button class="btn btn-danger btn-sm" data-action="bot-remove" data-target="${bot.id}">− ${escapeHtml(bot.name)}</button>`).join("")}
          </div>` : `<p class="fine-print">Waiting for the host to open the market…</p>`}
        </section>
        <aside class="lobby-aside">
          <section class="card lobby-share">
            <span class="section-label">SHARE YOUR ROOM CODE</span>
            <div class="share-code">${escapeHtml(state.code)}</div>
            <div class="qr-wrap"><img src="https://api.qrserver.com/v1/create-qr-code/?size=168x168&amp;margin=6&amp;data=${encodeURIComponent(inviteUrl())}" alt="QR code to join room ${escapeHtml(state.code)}" width="168" height="168" loading="lazy"></div>
            <button class="btn btn-outline wide" data-action="copy-invite">📨  Copy invite link</button>
            <a class="btn btn-subtle wide" href="${location.origin}/?view=${encodeURIComponent(state.code)}" target="_blank" rel="noopener noreferrer">📺  Open Big Screen</a>
            <p class="fine-print">Friends enter the code on their own device. Late arrivals can spectate now and play next round.</p>
          </section>
          ${isHost ? `<section class="card lobby-host"><span class="section-label">YOU’RE THE HOST</span><p class="fine-print">Four rounds of deal-making, surprises, and serious bragging rights. This is your market—start whenever you’re ready.</p>
            <button class="btn btn-primary wide" data-action="start" ${state.scores.filter(p => !p.waiting_next_round).length < 3 ? "disabled title=\"At least 3 traders are needed\"" : ""}>Open Round 1 · 3:00 ${state.scores.filter(p => !p.waiting_next_round).length < 3 ? "🔒" : "→"}</button>
          </section>` : ""}
        </aside>
      </div>
      </div>`;
  }

  function eventTemplate() {
    const event = model.state.event;
    if (!event) return "";
    return `<div class="event-ribbon"><span class="event-icon">${escapeHtml(event.emoji)}</span>
      <div class="event-copy"><b>${escapeHtml(event.name)}</b><br>${escapeHtml(event.description)}</div>
      <span class="event-clock" data-event-clock="${Number(event.seconds_left) || 0}">${clock(event.seconds_left)}</span>
    </div>`;
  }

  function clock(seconds) {
    const safe = Math.max(0, Number(seconds) || 0);
    return `${Math.floor(safe / 60)}:${String(Math.floor(safe % 60)).padStart(2, "0")}`;
  }

  function liveBanner() {
    const state = model.state;
    const seconds = Math.max(0, Number(state.seconds_left) || 0);
    const percent = Math.max(0, Math.min(100, seconds / 180 * 100));
    return `<div class="live-banner">
      <div class="live-left"><span class="live-kicker">${state.paused ? "MARKET PAUSED" : "ROUND " + state.round + " OF 4"}</span>
        <span class="live-title">${state.paused ? "Take a breather." : "Make a deal!"}</span></div>
      <div class="round-track"><i style="width:${percent}%"></i></div><div class="timer" data-clock="${seconds}">${clock(seconds)}</div>
    </div>`;
  }

  function playerInventory() {
    const player = model.state.you;
    if (!player || !model.state.is_player) return "";
    const sales = player.sales?.length ? player.sales.map(sale => `
      <div class="private-item"><span>${escapeHtml(sale.emoji)}</span><span class="item-name">${escapeHtml(sale.name)}${sale.available ? "" : " · sold"}</span>
        <span class="amount">${sale.available ? naira(sale.effective_cost) : `<span class="check">✓ sold</span>`}</span></div>`).join("") : `<p class="fine-print">Your list arrives at the next round.</p>`;
    const shopping = player.shopping?.length ? player.shopping.map(item => `
      <div class="private-item"><span>${escapeHtml(item.emoji)}</span><span class="item-name">${escapeHtml(item.name)}</span>
        ${item.bought ? `<span class="check">✓</span>` : `<span class="amount">${naira(item.value)}</span>`}</div>`).join("") : `<p class="fine-print">Your list arrives at the next round.</p>`;
    return `<section class="section-card">
      <div class="section-head"><div><h2>Your private game</h2><p>Only you can see these numbers.</p></div><span class="pill green">PRIVATE</span></div>
      <div class="section-content">
        <div class="wallet-row"><div><small>YOUR WALLET</small><div><span class="pill green">SCORE ${naira(player.score)}</span></div></div><b>${naira(player.wallet)}</b></div>
        <div class="private-grid">
          <div class="private-card"><div class="private-title">📦 Your stock <span class="private-hint">${model.state.event?.id === "fuel" ? "costs +20%" : "your cost"}</span></div>${sales}</div>
          <div class="private-card shopping"><div class="private-title">🛍 Your shopping list <span class="private-hint">value is yours</span></div>${shopping}</div>
        </div>
      </div>
    </section>`;
  }

  function marketTemplate() {
    const state = model.state;
    if (!state.market.length) return `<div class="empty-state">Everything's sold out! The next round brings a fresh market.</div>`;
    const players = state.scores.filter(player => player.id !== state.player_id);
    return `<div class="market-grid">${state.market.map((good, index) => {
      const isOwner = good.seller_id === state.player_id;
      const myList = state.you?.shopping?.some(item => item.good_id === good.good_id && !item.bought);
      const canTrade = !model.spectator && state.is_player && !state.paused && state.phase === "playing";
      const open = model.offerCard === good.good_id + good.seller_id;
      const options = players.map(person => `<option value="${escapeHtml(person.id)}">${escapeHtml(person.name)}${person.is_bot ? " · bot" : ""}</option>`).join("");
      const role = isOwner ? "SELL" : "BUY";
      const suggest = isOwner ? Math.round((GOOD_BASE[good.good_id] || 1400) * 1.26 / 50) * 50 : Math.round((state.you?.shopping?.find(x => x.good_id === good.good_id)?.value || GOOD_BASE[good.good_id] || 1500) * .82 / 50) * 50;
      return `<article class="market-card${open ? " form-open" : ""}${index === 0 && state.deals.length ? " deal-flash" : ""}">
        <span class="good-emoji">${escapeHtml(good.emoji)}</span><h3 class="market-good-title">${escapeHtml(good.name)}</h3>
        <span class="market-meta">${escapeHtml(good.category)}</span>
        <span class="market-owner">Seller <b>${escapeHtml(good.seller)}</b></span>
        ${canTrade ? open ? `<form class="price-form" data-form="offer">
            <input type="hidden" name="good_id" value="${escapeHtml(good.good_id)}">
            <input type="hidden" name="seller_id" value="${escapeHtml(good.seller_id)}">
            <input type="hidden" name="role" value="${role}">
            <div class="form-row">
              <label class="field-label">Trade with<select name="target_id">${options}</select></label>
              <label class="field-label">Your ${role === "SELL" ? "asking" : "offer"} price
                <div class="input-wrap"><span>₦</span><input class="price-input" name="price" type="number" inputmode="numeric" min="1" max="10000" value="${suggest}" required></div>
              </label>
            </div>
            ${role === "BUY" && !myList ? `<div class="fine-print">This item isn't on your shopping list. You can only buy goods on your list.</div>` : ""}
            <div class="form-row"><button class="btn btn-primary btn-sm" type="submit">Send offer</button><button class="btn btn-subtle btn-sm" type="button" data-action="close-offer">Cancel</button></div>
          </form>` : `<button class="btn ${myList || isOwner ? "btn-outline" : "btn-subtle"} btn-sm" data-action="open-offer" data-good="${escapeHtml(good.good_id)}" data-seller="${escapeHtml(good.seller_id)}" ${!myList && !isOwner ? "disabled title=\"Only shop for goods on your list\"" : ""}>
            ${isOwner ? "🏷 Find a buyer" : myList ? "🤝 Make an offer" : "Not on your list"}
          </button>` : ""}
      </article>`;
    }).join("")}</div>`;
  }

  const GOOD_BASE = { tomatoes: 950, pepper: 1250, yam: 1850, ankara: 2450, beads: 2200, basket: 1100, kettle: 2600, sheabutter: 1550 };

  function negotiationsTemplate() {
    const state = model.state;
    if (!state.negotiations.length) return `<div class="empty-state">No deals in play. Pick a good on the board to get the conversation started.</div>`;
    return `<div class="negotiation-list">${state.negotiations.map(negotiation => {
      const isWaiting = negotiation.can_respond;
      const myOffer = negotiation.maker_id === state.player_id;
      const actionText = isWaiting ? `Your move, ${escapeHtml(state.you?.name || "trader")}` : `${myOffer ? "Offer sent" : "Waiting for trader"} · ${negotiation.counters}/3 counters`;
      return `<article class="negotiation${isWaiting ? " awaiting" : ""}">
        <div class="negotiation-top"><span>${escapeHtml(negotiation.emoji)} <b>${escapeHtml(negotiation.good)}</b></span>
          <span class="pill ${isWaiting ? "sand" : "green"}">${isWaiting ? "YOUR TURN" : "IN PLAY"}</span></div>
        <div class="negotiation-price">${naira(negotiation.price)}</div>
        <div class="negotiation-note">${escapeHtml(negotiation.buyer)} ⇄ ${escapeHtml(negotiation.seller)} · ${actionText} · <span data-negotiation-clock="${Number(negotiation.seconds_left) || 0}">${clock(negotiation.seconds_left)}</span> left</div>
        ${isWaiting ? `<div class="negotiation-actions">
          <button class="btn btn-lime btn-sm" data-action="accept" data-negotiation="${negotiation.id}">Accept ✓</button>
          <button class="btn btn-danger btn-sm" data-action="walk" data-negotiation="${negotiation.id}">Walk away</button>
        </div>
        ${negotiation.counters < 3 ? `<form class="counter-form" data-form="counter"><input type="hidden" name="negotiation_id" value="${negotiation.id}">
          <div class="input-wrap"><span>₦</span><input class="price-input" type="number" name="price" min="1" max="10000" inputmode="numeric" placeholder="Counteroffer" required></div>
          <button class="btn btn-outline btn-sm" type="submit">Counter ↗</button></form>` : `<div class="fine-print">Final offer—accept or walk away.</div>`}` : ""}
      </article>`;
    }).join("")}</div>`;
  }

  function scoreboardTemplate() {
    return `<div class="section-card"><div class="section-head"><div><h2>Market leaders</h2><p>Profit + shopping savings</p></div><span class="pill green">LIVE</span></div>
      <div class="section-content">${model.state.scores.map((person, index) => `
        <div class="score-row"><span class="rank">${["1", "2", "3"][index] || (index + 1)}</span>
          <span class="score-name"><span class="online-dot"></span>${escapeHtml(person.name)}${person.id === model.state.host_id ? " 👑" : ""}<small>${person.deals} ${person.deals === 1 ? "deal" : "deals"}${person.is_bot ? " · bot" : ""}</small></span>
          <span class="score-value">${naira(person.score)}</span></div>`).join("") || `<div class="empty-state">Scores show up after the first deal.</div>`}
        ${!model.spectator && model.state.is_host ? hostControls() : ""}
      </div></div>`;
  }

  function hostControls() {
    const state = model.state;
    const others = state.scores.filter(p => p.id !== state.player_id && !p.is_bot && p.id !== state.host_id);
    const bots = state.scores.filter(p => p.is_bot);
    return `<div class="host-strip"><span class="section-label">HOST CONTROLS</span>
      ${state.paused ? `<button class="btn btn-primary btn-sm" data-action="resume">▶ Resume market</button>` : `<button class="btn btn-outline btn-sm" data-action="pause">Ⅱ Pause</button>`}
      <button class="btn btn-outline btn-sm" data-action="bot-add">🤖 Add bot</button>
      ${others.length ? `<label class="host-control"><select id="host-target">${others.map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("")}</select>
        <button class="btn btn-subtle btn-sm" data-action="transfer">↗ Make host</button></label>` : ""}
      ${others.length || bots.length ? `<label class="host-control"><select id="kick-target">${others.map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("")}${bots.map(p => `<option value="${p.id}">${escapeHtml(p.name)} · bot</option>`).join("")}</select>
        <button class="btn btn-danger btn-sm" data-action="kick">Remove</button></label>` : ""}
      <button class="btn btn-danger btn-sm" data-action="end">End game</button>
    </div>`;
  }

  function gameTemplate() {
    const state = model.state;
    return `<div class="room-shell">
      ${roomHeading(model.spectator ? "Ọjà is live 📺" : `Round ${state.round}: make it count! 🛍`, model.spectator ? "The public market board updates live. Player secrets stay private." : state.waiting_next_round ? "You’ve joined mid-round. Your first hand arrives at the next bell." : `Welcome, ${escapeHtml(state.you?.name || "trader")} · haggle your way up the leaderboard.`)}
      ${liveBanner()}${eventTemplate()}
      ${state.paused ? `<div class="event-ribbon"><span class="event-icon">⏸️</span><div class="event-copy"><b>Market paused</b><br>Negotiations and the round timer resume together.</div></div>` : ""}
      <div class="round-layout">
        <div class="market-column">
          ${playerInventory()}
          <section class="section-card"><div class="section-head"><div><h2>Today's market</h2><p>Tap an item to start a private, player-to-player negotiation.</p></div><span class="pill sand">${state.market.length} ${state.market.length === 1 ? "good" : "goods"}</span></div>
            <div class="section-content">${marketTemplate()}</div>
          </section>
          <section class="section-card"><div class="section-head"><div><h2>Haggling in progress</h2><p>Three counteroffers max · two open deals per trader</p></div><span class="pill green">${state.negotiations.length} OPEN</span></div>
            <div class="section-content">${negotiationsTemplate()}</div>
          </section>
        </div>
        <aside class="side-column">
          ${scoreboardTemplate()}
          <section class="section-card"><div class="section-head"><div><h2>Fresh off the press</h2><p>The market never stops talking.</p></div></div>
            <div class="section-content"><div class="feed-list">${state.feed.slice(0, 8).map(item => `<div class="feed-line ${escapeHtml(item.kind)}"><span>${item.kind === "deal" ? "🤝" : item.kind === "event" ? "✨" : "•"}</span>${escapeHtml(item.text)}</div>`).join("") || `<div class="empty-state">The market is warming up.</div>`}</div>
              ${state.deals.slice(-3).reverse().map(deal => `<div class="deal-line"><span class="deal-icon">${escapeHtml(deal.emoji)}</span><span><b>${escapeHtml(deal.good)}</b><br>${escapeHtml(deal.buyer)} bought from ${escapeHtml(deal.seller)} · ${naira(deal.price)}</span></div>`).join("")}
            </div>
          </section>
        </aside>
      </div>
    </div>`;
  }

  function betweenTemplate() {
    const state = model.state;
    return `<div class="room-shell">
      ${roomHeading(`Round ${state.round} is in the books.`, "A fresh hand is coming—your score carries forward.")}
      <div class="card round-summary"><div class="summary-emoji">🔔</div><span class="section-label">ROUND ${state.round} COMPLETE</span><h2>Time to count the takings.</h2>
        <p>Current leaders and trade stories stay on the board. The host can open the next round whenever everyone's ready. Late arrivals get their own shopping list now.</p>
        ${model.spectator ? "" : state.is_host ? `<button class="btn btn-primary" data-action="start">Open Round ${state.round + 1} · 3:00 →</button>` : `<span class="pill sand">Waiting for the host to start Round ${state.round + 1}</span>`}
      </div>${scoreboardTemplate()}${feedPanel()}
    </div>`;
  }

  function feedPanel() {
    return `<section class="section-card" style="max-width:650px;margin:15px auto"><div class="section-head"><h2>Fresh off the press</h2></div><div class="section-content"><div class="feed-list">${model.state.feed.slice(0, 12).map(item => `<div class="feed-line ${escapeHtml(item.kind)}">${escapeHtml(item.text)}</div>`).join("")}</div></div></section>`;
  }

  function finishTemplate() {
    const state = model.state;
    const winner = state.scores[0];
    return `<div class="room-shell">
      ${roomHeading("The market is closed.", "Four rounds of haggling. One name at the top.")}
      <div class="card round-summary">
        <div class="finish-hero"><div class="winner-crown">👑</div><span class="section-label">MARKET MVP</span>
          <h1>${escapeHtml(winner?.name || "Great trading!")}</h1><p style="color:#e1e7de">${naira(winner?.score)} points · ${winner?.deals || 0} trades</p></div>
        ${state.awards.length ? `<div class="awards">${state.awards.map(award => `<span class="award">🏅 ${escapeHtml(award.title)}<b>${escapeHtml(award.name)}</b></span>`).join("")}</div>` : ""}
        ${model.spectator ? "" : state.is_host ? `<button class="btn btn-primary" data-action="start">Play another game ↻</button>` : `<span class="pill sand">Ask your host for another round</span>`}
      </div>
      ${scoreboardTemplate()}${feedPanel()}
    </div>`;
  }

  function updateClocks() {
    const round = document.querySelector("[data-clock]");
    if (round) {
      const base = Math.max(0, Number(model.state?.seconds_left) || 0);
      const elapsed = Math.floor((Date.now() - (model.stateAt || Date.now())) / 1000);
      round.textContent = clock(Math.max(0, base - elapsed));
    }
    const event = document.querySelector("[data-event-clock]");
    if (event) {
      const elapsed = Math.floor((Date.now() - (model.stateAt || Date.now())) / 1000);
      event.textContent = clock(Math.max(0, Number(event.dataset.eventClock) - elapsed));
    }
    document.querySelectorAll("[data-negotiation-clock]").forEach(node => {
      const elapsed = Math.floor((Date.now() - (model.stateAt || Date.now())) / 1000);
      node.textContent = clock(Math.max(0, Number(node.dataset.negotiationClock) - elapsed));
    });
  }

  async function handleEntry(form) {
    const fields = new FormData(form);
    const name = String(fields.get("name") || "").trim();
    if (model.mode === "create") {
      const result = await request("create", { name });
      remember(result.code, result.token);
    } else {
      const code = String(fields.get("code") || "").trim().toUpperCase();
      const token = localStorage.getItem(storageKey(code));
      const result = await request("join", { code, name, token });
      remember(result.code, result.token);
    }
  }

  function inviteUrl() {
    return `${location.origin}/?join=${encodeURIComponent(model.state.code)}`;
  }

  async function copyInvite() {
    const link = inviteUrl();
    try {
      await navigator.clipboard.writeText(`Join my Ọjà market (${model.state.code}): ${link}`);
      toast("Invite link copied. Send it to your crew!");
    } catch {
      window.prompt("Copy this invite link:", link);
    }
  }

  async function doAction(action, target, extra = {}) {
    if (model.pending) return;
    model.pending = true;
    app.classList.add("mutation-pending");
    try {
      if (action === "copy-invite") return await copyInvite();
      const payload = { ...extra };
      if (target) payload.target = target;
      await request(action.replace(/-/g, "_"), payload);
      if (action === "start") toast(`Round ${model.state.round} is open. May the best haggler win!`);
      if (action === "accept") toast("Deal done! Both traders have scored. 🤝");
      if (action === "walk") toast("You walked away. Another deal awaits.");
      if (action === "bot-add") toast("A new bot trader joined the market.");
      if (action === "pause") toast("Market paused. Your round time is safe.");
      if (action === "resume") toast("Let the haggling continue!");
      if (action === "end") toast("Market closed.");
    } finally {
      model.pending = false;
      app.classList.remove("mutation-pending");
    }
  }

  async function handleOffer(form) {
    const fields = new FormData(form);
    const goodId = String(fields.get("good_id"));
    const sellerId = String(fields.get("seller_id"));
    const role = String(fields.get("role"));
    const targetId = String(fields.get("target_id") || "");
    const price = Number(fields.get("price"));
    const buyerId = role === "SELL" ? targetId : model.state.player_id;
    const finalSellerId = role === "SELL" ? model.state.player_id : sellerId;
    if (!buyerId || buyerId === finalSellerId) return toast("Choose another trader for the deal.", "error");
    if (model.pending) return;
    model.pending = true;
    app.classList.add("mutation-pending");
    try {
      await request("offer", { good_id: goodId, seller_id: finalSellerId, buyer_id: buyerId, target_id: targetId, price });
      localStorage.setItem("oja-first-offer", "yes");
      model.offerCard = null;
      model.lastSignature = "";
      toast("Offer sent! Your trader will see it live.");
    } finally {
      model.pending = false;
      app.classList.remove("mutation-pending");
    }
  }

  async function handleFormSubmit(event) {
    const form = event.target.closest("form");
    if (!form) return;
    event.preventDefault();
    try {
      if (form.id === "entry-form") return await handleEntry(form);
      if (form.dataset.form === "offer") return await handleOffer(form);
      if (form.dataset.form === "counter") {
        const fields = new FormData(form);
        return await doAction("counter", null, {
          negotiation_id: fields.get("negotiation_id"), price: Number(fields.get("price")),
        }).then(() => toast("Counteroffer sent. The ball is in their court."));
      }
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function handleClick(event) {
    const mode = event.target.closest("[data-mode]");
    if (mode) {
      model.mode = mode.dataset.mode;
      render();
      return;
    }
    if (event.target.closest("#help-button")) return showOnboarding(true);
    if (event.target.closest("#sound-toggle")) {
      model.sound = !model.sound;
      localStorage.setItem("oja-sound", model.sound ? "on" : "off");
      render();
      toast(model.sound ? "Deal sounds on 🔔" : "Deal sounds off 🔕");
      return;
    }
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    if (action === "open-offer") {
      const key = button.dataset.good + button.dataset.seller;
      model.offerCard = model.offerCard === key ? null : key;
      render();
      const form = document.querySelector(".price-form select");
      if (form) form.focus();
      return;
    }
    if (action === "close-offer") { model.offerCard = null; render(); return; }
    if (action === "counter") {
      const negotiationId = button.dataset.negotiation;
      const form = app.querySelector(`form[data-form="counter"] input[value="${negotiationId}"]`)?.closest("form");
      if (form) form.requestSubmit();
      return;
    }
    if (action === "accept" || action === "walk") {
      doAction(action, null, { negotiation_id: button.dataset.negotiation })
        .catch(error => toast(error.message, "error"));
      return;
    }
    const select = action === "kick" ? document.querySelector("#kick-target") : action === "transfer" ? document.querySelector("#host-target") : null;
    const target = select?.value || button.dataset.target;
    if (action === "kick" && !window.confirm("Remove this trader from the market?")) return;
    if (action === "end" && !window.confirm("End the game for everyone in this room?")) return;
    doAction(action, target).catch(error => toast(error.message, "error"));
  }

  function showOnboarding(force = false) {
    if (!force && !model.onboardingOpen) return;
    model.onboardingOpen = true;
    const card = onboardingCards[model.onboardingPage];
    overlayRoot.innerHTML = `<div class="overlay" role="dialog" aria-modal="true" aria-label="How to play Ọjà">
      <section class="modal-card"><div class="onboarding-mark">${card.emoji}</div>
        <span class="section-label">HOW TO PLAY · ${model.onboardingPage + 1} OF 3</span><h2>${card.title}</h2><p>${card.text}</p>
        <div class="onboarding-footer"><div class="onboarding-dots">${onboardingCards.map((_, i) => `<i class="${i === model.onboardingPage ? "active" : ""}"></i>`).join("")}</div>
          <button class="btn btn-primary" data-onboarding="${model.onboardingPage === 2 ? "done" : "next"}">${model.onboardingPage === 2 ? "Let’s play →" : "Next →"}</button>
        </div></section></div>`;
  }

  function handleOverlayClick(event) {
    const button = event.target.closest("[data-onboarding]");
    if (!button) return;
    if (button.dataset.onboarding === "done") {
      model.onboardingOpen = false;
      model.onboardingPage = 0;
      localStorage.setItem("oja-onboarded", "yes");
      overlayRoot.innerHTML = "";
    } else {
      model.onboardingPage = Math.min(onboardingCards.length - 1, model.onboardingPage + 1);
      showOnboarding();
    }
  }

  app.addEventListener("submit", event => { void handleFormSubmit(event); });
  app.addEventListener("click", handleClick);
  overlayRoot.addEventListener("click", handleOverlayClick);
  soundButton.addEventListener("click", handleClick);
  helpButton.addEventListener("click", handleClick);

  const joinCode = query.get("join");
  if (spectatorCode) {
    model.code = spectatorCode.toUpperCase();
    connectStream();
  } else if (joinCode) {
    model.mode = "join";
    render();
    const codeInput = app.querySelector('input[name="code"]');
    if (codeInput) codeInput.value = joinCode.toUpperCase();
  } else {
    const lastCode = localStorage.getItem("oja-current-room");
    const lastToken = lastCode && localStorage.getItem(storageKey(lastCode));
    if (lastCode && lastToken) {
      model.code = lastCode;
      model.token = lastToken;
      request("join", { code: lastCode, token: lastToken, name: "Trader" })
        .then(result => remember(result.code, result.token))
        .catch(() => { model.code = null; model.token = null; render(); });
    } else render();
  }

  window.setInterval(updateClocks, 900);
})();
