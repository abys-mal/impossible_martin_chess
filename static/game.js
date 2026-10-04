/* The server supplies pieces, legal-move hints, phases, and clock values. */
"use strict";
const $ = (selector) => document.querySelector(selector);
const board = $("#board");
let state = null, selected = null, color = "white", busy = false, online = true;
let receiptTime = performance.now(), requestSerial = 0, appliedSerial = 0;
let renderedRevision = -1, renderedGame = null, renderedColor = null;
let drag = null, suppressClick = false, promotion = null;
const names = {p:"pawn",n:"knight",b:"bishop",r:"rook",q:"queen",k:"king"};
const initialPieces = [];
for (let file = 0; file < 8; file++) {
  for (const [rank, side, type] of [[1,"white","rnbqkbnr"[file]],[2,"white","p"],[7,"black","p"],[8,"black","rnbqkbnr"[file]]]) {
    initialPieces.push({square:"abcdefgh"[file]+rank,color:side,type});
  }
}
function imagePath(piece) { return `/static/pieces/${piece.color[0]}${piece.type}.svg`; }
function canInteract() { return !!state && state.phase === "human" && !busy && online; }
function notice(message = "") { $("#notice").textContent = message; }
function timeText(seconds) {
  const total = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2,"0")}`;
}
function setColor(nextColor) {
  color = nextColor;
  document.querySelectorAll("[data-color]").forEach(button => {
    const chosen = button.dataset.color === color;
    button.classList.toggle("chosen", chosen);
    button.setAttribute("aria-pressed", String(chosen));
  });
  if (!state) renderBoard();
}
function renderBoard() {
  const side = state ? state.human_color : color;
  const pieces = state ? state.pieces : initialPieces;
  const pieceMap = new Map(pieces.map(piece => [piece.square,piece]));
  const last = state?.move_history.at(-1)?.uci;
  const destinations = new Set((state?.legal_moves || []).filter(move => move.slice(0,2) === selected).map(move => move.slice(2,4)));
  const focusSquare = board.contains(document.activeElement) ? document.activeElement.dataset.square : null;
  const fragment = document.createDocumentFragment();
  for (let row=0; row<8; row++) for (let column=0; column<8; column++) {
    const file = side === "white" ? column : 7-column;
    const rank = side === "white" ? 8-row : row+1;
    const square = "abcdefgh"[file]+rank;
    const piece = pieceMap.get(square);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "square";
    button.dataset.square = square;
    button.classList.toggle("dark", (file+rank)%2 === 1);
    button.classList.toggle("selected", square === selected);
    button.classList.toggle("last-move", !!last && (last.slice(0,2) === square || last.slice(2,4) === square));
    button.classList.toggle("hint", destinations.has(square));
    button.classList.toggle("capture-hint", destinations.has(square) && !!piece);
    button.classList.toggle("own-piece", piece?.color === side);
    const turnColor = state?.fen.split(" ")[1] === "w" ? "white" : "black";
    button.classList.toggle("checked", !!state?.in_check && piece?.type === "k" && piece.color === turnColor);
    button.setAttribute("aria-label", `${square}${piece ? `, ${piece.color} ${names[piece.type]}` : ", empty"}`);
    button.setAttribute("aria-pressed", String(square === selected));
    if (piece) {
      const img = document.createElement("img");
      img.src = imagePath(piece); img.alt = ""; img.draggable = false;
      button.append(img);
    }
    if (column === 0) {
      const label = document.createElement("span"); label.className = "coordinate rank-label"; label.textContent = rank;
      button.append(label);
    }
    if (row === 7) {
      const label = document.createElement("span"); label.className = "coordinate file-label"; label.textContent = "abcdefgh"[file];
      button.append(label);
    }
    fragment.append(button);
  }
  board.replaceChildren(fragment);
  if (focusSquare) board.querySelector(`[data-square="${focusSquare}"]`)?.focus({preventScroll:true});
  board.classList.toggle("locked", !canInteract());
}
function renderState() {
  const active = !!state;
  $("#initial-controls").hidden = active;
  $("#new-game-button").innerHTML = active ? "New game <span>↗</span>" : "Start game <span>↗</span>";
  $("#resign-button").disabled = !active || busy || state.phase === "finished" || !online;
  $("#seed-button").disabled = !active;
  $("#seed-button").textContent = active ? state.seed : "—";
  const humanColor = state?.human_color || color;
  $("#human-color").textContent = humanColor.toUpperCase();
  $("#bot-color").textContent = humanColor === "white" ? "BLACK" : "WHITE";
  if (!active) { renderBoard(); return; }
  const changed = renderedRevision !== state.revision || renderedGame !== state.game_id || renderedColor !== humanColor;
  const content = {
    opening:["HEAD START", `Martin's opening · ${state.opening_done}/3`, "Three actions, all for Martin. Your clock begins as soon as he finishes."],
    human:["YOUR TURN", state.in_check ? "Your king is in check." : "Your move. Take a breath.", "Your clock is running. Moving in under 3 seconds earns a real 15-second penalty."],
    penalty:["PIECE DOWN", "A little too enthusiastic.", "You moved too quickly and knocked over a piece! Wait 15 seconds while your clock keeps running."],
    bot:["MARTIN'S TURN", "Greatness takes a moment.", "Martin is considering something questionable. Your clock is paused."],
    finished:["GAME OVER", state.result || "Game over.", state.winner === "human" ? "You survived the experiment. Care to try a different seed?" : "Every experiment teaches us something. Start a fresh game when you're ready."]
  }[state.phase];
  $("#phase-pill").textContent = content[0];
  $("#phase-pill").classList.toggle("penalty",state.phase === "penalty");
  $("#status-title").textContent = content[1];
  $("#status-description").textContent = content[2];
  $("#human-note").textContent = state.phase === "penalty" ? "Clock still running. Piece temporarily horizontal." :
    state.phase === "human" ? "Your turn · give it at least three seconds." : state.phase === "finished" ? "Experiment complete." : "Clock paused. Enjoy the quiet.";
  $("#move-count").textContent = `${state.move_history.length} ACTIONS`;
  if (changed) {
  const history = $("#history"), keepBottom = history.scrollHeight-history.scrollTop-history.clientHeight < 35;
  const rows = document.createDocumentFragment();
  for (const move of state.move_history) {
    const row = document.createElement("div"); row.className = "history-row";
    const action = document.createElement("span"); action.className="action"; action.textContent=String(move.action).padStart(2,"0");
    const actor = document.createElement("span"); actor.className="actor"; actor.textContent=move.actor === "human" ? "You" : "Martin";
    const san = document.createElement("span"); san.className="notation"; san.textContent=move.san; san.title=move.uci;
    row.append(action,actor,san);
    if (move.phase === "opening") { const tag=document.createElement("span"); tag.className="opening-tag"; tag.textContent="HEAD START"; row.append(tag); }
    rows.append(row);
  }
  if (state.move_history.length) history.replaceChildren(rows);
  if (keepBottom) history.scrollTop=history.scrollHeight;
  const events = document.createDocumentFragment();
  for (const event of [...state.events].reverse()) {
    const p=document.createElement("p"); p.className=`event-row ${event.kind}`; p.textContent=event.message; events.append(p);
  }
  $("#events").replaceChildren(events);
  }
  if (changed) {
    clearDrag(); selected = null;
    if (promotion && (promotion.revision !== state.revision || promotion.game_id !== state.game_id)) closePromotion();
    renderedRevision = state.revision; renderedGame = state.game_id; renderedColor = humanColor;
    renderBoard();
  }
  board.classList.toggle("locked", !canInteract());
  updateTimers();
}
function updateTimers() {
  const elapsed = Math.max(0,(performance.now()-receiptTime)/1000);
  const remaining = state ? Math.max(0,state.human_time-(state.clock_running ? elapsed : 0)) : 1200;
  $("#clock-value").textContent = timeText(remaining);
  $("#human-clock").classList.toggle("running", !!state?.clock_running);
  $("#human-clock").classList.toggle("low", !!state && remaining < 60 && state.phase !== "finished");
  const banner = $("#board-banner");
  banner.hidden = !state || state.phase === "human" || state.phase === "bot";
  banner.classList.toggle("penalty",state?.phase === "penalty");
  if (state?.phase === "penalty") {
    $("#banner-text").textContent="Piece knocked over. Wait it out.";
    $("#banner-count").textContent = `${Math.max(0,Math.ceil(state.penalty_remaining-elapsed))}s`;
  } else if (state?.phase === "opening") {
    $("#banner-text").textContent="Martin's three-move head start"; $("#banner-count").textContent=`${state.opening_done}/3`;
  } else if (state?.phase === "finished") {
    $("#banner-text").textContent=state.result; $("#banner-count").textContent="";
  }
}
async function api(path, body) {
  const serial = ++requestSerial;
  const response = await fetch(path,{method:body === undefined ? "GET" : "POST",headers:body === undefined ? {} : {"Content-Type":"application/json"},body:body === undefined ? undefined : JSON.stringify(body),cache:"no-store",signal:AbortSignal.timeout(6000)});
  const data = await response.json();
  if (!online) notice("Connection restored.");
  online=true; $("#connection-label").textContent="LOCAL SESSION";
  if (data.error) throw new Error(data.error);
  if (serial >= appliedSerial) {
    appliedSerial=serial;
    if (data.game === null) {
      state=null; renderedRevision=-1; renderedGame=null; selected=null; renderState();
    } else if (data.game_id) {
      if (!state || data.game_id !== state.game_id || data.revision >= state.revision) {
        state=data; receiptTime=performance.now(); renderState();
      }
    }
  }
  return data;
}
async function poll() {
  if (!busy) {
    try { await api("/api/state"); }
    catch (error) {
      online=false; $("#connection-label").textContent="RECONNECTING";
      board.classList.add("locked"); $("#resign-button").disabled=true;
      notice("Connection interrupted. The server clock continues; reconnecting…");
    }
  }
  setTimeout(poll,350);
}
async function sendMove(uci) {
  if (!canInteract()) return;
  const payload={move:uci,game_id:state.game_id,revision:state.revision};
  busy=true; selected=null; notice(); renderBoard();
  try {
    const data=await api("/api/move",payload);
    if (data.accepted === false) notice(data.message || "Move rejected.");
  } catch(error) { notice(error.message); }
  finally { busy=false; renderState(); }
}
function chooseDestination(source,target) {
  if (!canInteract()) return;
  const candidates=state.legal_moves.filter(move=>move.slice(0,2)===source && move.slice(2,4)===target);
  if (candidates.some(move=>move.length === 5)) {
    promotion={source,target,game_id:state.game_id,revision:state.revision};
    const options=$("#promotion-options"); options.replaceChildren();
    for (const type of ["q","r","b","n"]) {
      const candidate=candidates.find(move=>move[4]===type); if (!candidate) continue;
      const button=document.createElement("button"); button.type="button"; button.setAttribute("aria-label",`Promote to ${names[type]}`);
      const img=document.createElement("img"); img.src=imagePath({color:state.human_color,type}); img.alt=names[type]; button.append(img);
      button.addEventListener("click",()=>{ closePromotion(); sendMove(candidate); }); options.append(button);
    }
    $("#promotion-dialog").showModal();
  } else {
    // Attempts are validated on the server, even when no hint exists.
    sendMove(source+target);
  }
}
function closePromotion() { promotion=null; $("#promotion-dialog").close(); }
board.addEventListener("click",event=>{
  if (suppressClick) { suppressClick=false; return; }
  if (!canInteract()) return;
  const square=event.target.closest(".square")?.dataset.square; if (!square) return;
  const piece=state.pieces.find(p=>p.square===square);
  if (piece?.color === state.human_color) { selected=selected===square ? null : square; renderBoard(); }
  else if (selected) chooseDestination(selected,square);
});
board.addEventListener("keydown",event=>{
  if (!event.key.startsWith("Arrow")) return;
  event.preventDefault();
  const squares=[...board.querySelectorAll(".square")], index=squares.indexOf(document.activeElement);
  const offset={ArrowLeft:-1,ArrowRight:1,ArrowUp:-8,ArrowDown:8}[event.key];
  squares[Math.max(0,Math.min(63,index+offset))]?.focus();
});
board.addEventListener("pointerdown",event=>{
  if (!canInteract() || event.button !== 0) return;
  const button=event.target.closest(".square"), piece=state.pieces.find(p=>p.square===button?.dataset.square);
  if (piece?.color !== state.human_color) return;
  drag={source:piece.square,x:event.clientX,y:event.clientY,pointer:event.pointerId,piece,active:false,ghost:null};
});
document.addEventListener("pointermove",event=>{
  if (!drag || drag.pointer !== event.pointerId) return;
  if (!drag.active && Math.hypot(event.clientX-drag.x,event.clientY-drag.y)>7) {
    drag.active=true; selected=drag.source;
    const saved=drag; renderBoard(); drag=saved;
    drag.ghost=document.createElement("img"); drag.ghost.className="drag-ghost"; drag.ghost.src=imagePath(drag.piece); drag.ghost.alt="";
    const size=board.clientWidth/8; drag.ghost.style.width=`${size}px`; drag.ghost.style.height=`${size}px`;
    document.body.append(drag.ghost); board.querySelector(`[data-square="${drag.source}"]`)?.classList.add("drag-source");
  }
  if (drag.active) { event.preventDefault(); drag.ghost.style.left=`${event.clientX}px`; drag.ghost.style.top=`${event.clientY}px`; }
},{passive:false});
document.addEventListener("pointerup",event=>{
  if (!drag || drag.pointer !== event.pointerId) return;
  const current=drag;
  const target=document.elementFromPoint(event.clientX,event.clientY)?.closest(".square")?.dataset.square;
  clearDrag();
  if (current.active) {
    suppressClick=true; setTimeout(()=>{suppressClick=false;},0);
    if (target && target !== current.source) chooseDestination(current.source,target);
    else {selected=null;renderBoard();}
  }
});
document.addEventListener("pointercancel",()=>{clearDrag();selected=null;renderBoard();});
function clearDrag() { drag?.ghost?.remove(); drag=null; board.querySelector(".drag-source")?.classList.remove("drag-source"); }
document.querySelectorAll("[data-color]").forEach(button=>button.addEventListener("click",()=>setColor(button.dataset.color)));
$("#new-game-button").addEventListener("click",()=>{
  setColor(state?.human_color || color); $("#new-dialog-error").textContent="";
  $("#seed-input").value=""; $("#new-dialog").showModal();
});
$("#cancel-new").addEventListener("click",()=>$("#new-dialog").close());
$("#new-form").addEventListener("submit",async event=>{
  event.preventDefault(); if (busy) return;
  const text=$("#seed-input").value.trim();
  const seed=text === "" ? null : Number(text);
  if (text !== "" && (!/^\d+$/.test(text) || !Number.isSafeInteger(seed) || seed<0)) {
    $("#new-dialog-error").textContent="Use an integer between 0 and 9007199254740991."; return;
  }
  busy=true; $("#new-form button[type=submit]").disabled=true; notice();
  try { await api("/api/new",{color,seed}); $("#new-dialog").close(); }
  catch(error) {$("#new-dialog-error").textContent=error.message;}
  finally {busy=false;$("#new-form button[type=submit]").disabled=false;renderState();}
});
$("#resign-button").addEventListener("click",()=>$("#resign-dialog").showModal());
$("#cancel-resign").addEventListener("click",()=>$("#resign-dialog").close());
$("#confirm-resign").addEventListener("click",async()=>{
  if (busy || !state) return;
  busy=true; $("#resign-dialog").close();
  try { await api("/api/resign",{game_id:state.game_id}); } catch(error) {notice(error.message);}
  finally {busy=false;renderState();}
});
$("#cancel-promotion").addEventListener("click",closePromotion);
$("#promotion-dialog").addEventListener("cancel",()=>{promotion=null;});
$("#seed-button").addEventListener("click",async()=>{
  if (!state) return;
  try {await navigator.clipboard.writeText(String(state.seed));notice("Game seed copied.");}
  catch {notice(`Game seed: ${state.seed}`);}
});
document.addEventListener("visibilitychange",()=>{if(!document.hidden)updateTimers();});
renderState(); setInterval(updateTimers,100); poll();
