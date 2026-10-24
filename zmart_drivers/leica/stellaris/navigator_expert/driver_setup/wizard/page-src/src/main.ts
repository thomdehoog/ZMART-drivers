/**
 * The setup wizard's page: the ZMART Interface's screen without its top row.
 *
 * On the left, the rail: every step with its number, blue where you stand,
 * green once done, orange when done under settings that have changed since,
 * and locked until every step before it is done (the interface's rules). On
 * the right, the chosen step's boxes, each a heading over a white card.
 */

import { call, loadOutline, type Outline, type Result, type StepInfo } from "./api";
import { h } from "./dom";
import { VIEWS, type Context, type Press } from "./steps";
import "./style.css";

const railHost = document.getElementById("steps")!;
const stage = document.getElementById("stage")!;
const statusBar = document.getElementById("status-bar")!;

let outline: Outline;
let active = 0;
/** The step whose action is under way, or null: only one at a time. */
let running: string | null = null;
/** Steps finished in this window, beside those already done on this computer. */
const finished = new Set<string>();
/** The presses of the step on show, so they can all be held while one works. */
let presses: { button: HTMLButtonElement; enabled: () => boolean }[] = [];

const byId = (id: string) => outline.steps.find((s) => s.id === id);
const isDone = (s: StepInfo) => s.status.done || finished.has(s.id);
/** A step is reached once every step before it is done. */
const isReachable = (i: number) => outline.steps.slice(0, i).every(isDone);
/** Done, but one of the steps it was measured on was saved again after it. */
function isStale(s: StepInfo): boolean {
  if (!isDone(s) || !s.status.saved_at) return false;
  return s.needs.some((id) => {
    const earlier = byId(id)?.status.saved_at;
    return !!earlier && earlier > (s.status.saved_at as string);
  });
}
/** The names of the steps that changed under *s*, for the sentence that explains the orange. */
const changedUnder = (s: StepInfo) =>
  s.needs.filter((id) => (byId(id)?.status.saved_at ?? "") > (s.status.saved_at ?? "")).map((id) => byId(id)!.title.toLowerCase());

function renderRail() {
  railHost.textContent = "";
  outline.steps.forEach((s, i) => {
    const done = isDone(s);
    const stale = isStale(s);
    const reachable = isReachable(i);
    const b = h("button", {
      class: "step" + (i === active ? " active" : "") + (done ? " done" : "") + (stale ? " stale" : "") + (reachable ? "" : " locked"),
      type: "button",
      disabled: reachable ? undefined : "",
    });
    const head = h("div", { class: "step-head" }, h("span", { class: "step-n" }, String(i + 1)), h("span", { class: "step-name" }, s.title));
    if (running === s.id) head.append(h("span", { class: "spin" }));
    if (stale) head.append(h("span", { class: "review-tag" }, "review"));
    b.append(head);
    b.addEventListener("click", () => {
      if (!reachable || i === active) return;
      active = i;
      renderAll();
    });
    railHost.append(b);
  });
}

function holdPresses() {
  for (const p of presses) p.button.disabled = running !== null || !p.enabled();
}

/** Ask the server again what is set up, after a step has saved something. */
async function refresh() {
  outline = await loadOutline();
  renderRail();
}

function renderStage() {
  const step = outline.steps[active];
  presses = [];

  const ctx: Context = {
    press(label: string, action: string, how: Press): HTMLElement {
      const button = h("button", { class: "run", type: "button" }, label);
      const hint = h("span", { class: "action-hint" });
      const enabled = how.enabled ?? (() => true);
      presses.push({ button, enabled });
      button.disabled = running !== null || !enabled();
      button.addEventListener("click", async () => {
        if (running) return;
        running = step.id;
        button.classList.add("running");
        hint.className = "action-hint";
        hint.textContent = "";
        statusBar.textContent = how.busy;
        statusBar.hidden = false;
        holdPresses();
        renderRail();
        let result: Result | null = null;
        try {
          result = await call(step.id, action, how.values?.() ?? {});
        } catch (why) {
          hint.className = "action-hint bad";
          hint.textContent = (why as Error).message;
        }
        running = null;
        button.classList.remove("running");
        statusBar.hidden = true;
        statusBar.textContent = "";
        if (result) {
          const said = how.said?.(result);
          if (said) {
            hint.className = "action-hint ok";
            hint.textContent = said;
          }
          how.then?.(result);
        }
        holdPresses();
        renderRail();
      });
      return h("div", { class: "side-act" }, button, hint);
    },
    group(title: string, ...children) {
      return h("div", { class: "side-group" }, h("div", { class: "side-group-title" }, title), h("div", { class: "side-group-body" }, ...children));
    },
    note(text: string, warn = false) {
      return h("p", { class: warn ? "side-note warn" : "side-note" }, text);
    },
    field(label: string, input: HTMLInputElement, hint?: string) {
      input.setAttribute("aria-label", label);
      return h("label", { class: "field" }, h("span", {}, label), input, hint ? h("span", { class: "hint" }, hint) : null);
    },
    picture() {
      return h("img", { class: "picture", alt: "", hidden: "" });
    },
    refreshPresses: holdPresses,
    async finish() {
      finished.add(step.id);
      await refresh();
    },
  };

  /* The first box says what the step is for and what this computer already
     has; the step's own boxes follow. */
  const about = [ctx.note(step.purpose)];
  if (isDone(step) && step.status.note) about.push(ctx.note(`Already set up on this computer. ${step.status.note} You can do this step again to replace it.`));
  if (isStale(step))
    about.push(ctx.note(`The ${changedUnder(step).join(" and ")} was saved again after this step. Do this step again, so that it matches.`, true));

  const channel = h("div", { class: "channel" }, ctx.group(step.title, ...about), ...VIEWS[step.id](ctx));
  stage.textContent = "";
  stage.append(channel);
  stage.scrollTop = 0;
}

function renderAll() {
  renderRail();
  renderStage();
}

loadOutline().then((o) => {
  outline = o;
  const firstToDo = o.steps.findIndex((s) => !s.status.done);
  active = firstToDo === -1 ? o.steps.length - 1 : firstToDo;
  renderAll();
});
