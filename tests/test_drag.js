// Run with: node --test tests/test_drag.js
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function client() {
  class Element {
    constructor() {
      this.children = []; this.dataset = {}; this.style = {}; this.listeners = {};
      this.classes = new Set(); this.clientWidth = 640;
      this.classList = {
        add: name => this.classes.add(name), remove: name => this.classes.delete(name),
        contains: name => this.classes.has(name),
        toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name)
      };
    }
    append(...elements) { this.children.push(...elements); }
    replaceChildren(fragment) { this.children = fragment.children; }
    setAttribute() {}
    addEventListener(name, handler) { this.listeners[name] = handler; }
    contains() { return false; }
    focus() {}
    remove() { this.removed = true; }
    setPointerCapture(pointer) { this.captured = pointer; }
    querySelector(selector) {
      return this.children.find(child => selector === ".drag-source" ?
        child.classes.has("drag-source") : child.dataset.square === selector.match(/data-square="(.*?)"/)?.[1]);
    }
    closest() { return this; }
  }
  const elements = new Map();
  const document = new Element();
  document.querySelector = selector => {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  };
  document.querySelectorAll = () => [];
  document.createElement = document.createDocumentFragment = () => new Element();
  document.body = new Element();
  document.elementFromPoint = () => ({closest: () => ({dataset: {square: "f3"}})});
  const requests = [];
  const context = vm.createContext({document, performance: {now: () => 0},
    setTimeout() {}, setInterval() {}, AbortSignal, navigator: {},
    fetch: async (path, options) => {
      requests.push({path, body: JSON.parse(options.body)});
      return {json: async () => ({})};
    }
  });
  const source = fs.readFileSync(require.resolve("../static/game.js"), "utf8")
    .replace("renderState(); setInterval(updateTimers,100); poll();", "");
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code, context);
  const piece = {id: "w-7", square: "g1", color: "white", type: "n"};
  const snapshot = {game_id: "game-1", revision: 1, human_color: "white", phase: "bot",
    pieces: [piece], fen: "position b", move_history: [], events: [], legal_moves: [],
    premove_moves: ["g1f3"], refused_captures: [], human_time: 1200, human_turn_index: 1};
  function update(changes = {}) {
    context.nextState = {...snapshot, ...changes};
    run("state = nextState; renderState();");
  }
  update();
  const board = elements.get("#board");
  function startDrag(active = true) {
    const button = board.querySelector('[data-square="g1"]');
    board.listeners.pointerdown({button: 0, target: button, pointerId: 1, clientX: 100, clientY: 100});
    if (active) document.listeners.pointermove({pointerId: 1, clientX: 120, clientY: 120, preventDefault() {}});
    return run("drag.ghost");
  }
  return {run, update, startDrag, board, document, requests};
}

test("drag survives Martin's move, refreshes hints and drops using the current turn", async () => {
  const c = client(), ghost = c.startDrag();
  c.update({revision: 2, phase: "human", legal_moves: ["g1f3"], human_turn_index: 2});
  assert.equal(c.run("drag.ghost"), ghost);
  assert.equal(ghost.removed, undefined);
  assert.equal(c.board.captured, 1);
  assert.equal(c.board.querySelector('[data-square="g1"]').classes.has("drag-source"), true);
  assert.equal(c.board.querySelector('[data-square="f3"]').classes.has("hint"), true);
  c.document.listeners.pointermove({pointerId: 1, clientX: 220, clientY: 180, preventDefault() {}});
  assert.equal(ghost.style.left, "220px");
  c.document.listeners.pointerup({pointerId: 1, clientX: 220, clientY: 180});
  assert.equal(ghost.removed, true);
  assert.equal(c.requests[0].path, "/api/move");
  assert.deepEqual(c.requests[0].body, {move: "g1f3", game_id: "game-1", revision: 2, target_turn: 3});
});

test("drag survives another opening action and releases as a premove", () => {
  const c = client();
  c.update({revision: 2, phase: "opening", opening_done: 1});
  const ghost = c.startDrag();
  c.update({revision: 3, phase: "opening", opening_done: 2});
  assert.equal(c.run("drag.ghost"), ghost);
  c.document.listeners.pointerup({pointerId: 1, clientX: 200, clientY: 200});
  assert.equal(c.requests[0].path, "/api/premove");
  assert.equal(c.requests[0].body.revision, 3);
});

test("a held piece can begin dragging after Martin's update", () => {
  const c = client();
  c.startDrag(false);
  c.update({revision: 2, phase: "human"});
  c.document.listeners.pointermove({pointerId: 1, clientX: 150, clientY: 150, preventDefault() {}});
  assert.equal(c.run("drag.active"), true);
});

for (const [reason, changes] of [
  ["captured piece", {pieces: []}],
  ["replaced piece", {pieces: [{id: "w-other", square: "g1", color: "white", type: "n"}]}],
  ["moved piece", {pieces: [{id: "w-7", square: "f3", color: "white", type: "n"}]}],
  ["new game", {game_id: "game-2"}],
  ["penalty", {phase: "penalty"}],
  ["game over", {phase: "finished"}]
]) {
  test(`drag cancels for ${reason}`, () => {
    const c = client(), ghost = c.startDrag();
    c.update({revision: 2, ...changes});
    assert.equal(c.run("drag"), null);
    assert.equal(c.run("selected"), null);
    assert.equal(ghost.removed, true);
    c.document.listeners.pointerup({pointerId: 1, clientX: 200, clientY: 200});
    assert.equal(c.requests.length, 0);
  });
}
