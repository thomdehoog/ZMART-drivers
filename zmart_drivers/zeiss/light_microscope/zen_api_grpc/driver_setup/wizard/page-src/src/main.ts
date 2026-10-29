/**
 * The setup wizard's page: the ZMART Interface's screen without its top row,
 * showing the ZMART driver configuration workflow.
 *
 * On the left, the rail: every step with its number, blue where you stand,
 * green once done, orange with a "review" tag when done under a setting that
 * changed since, and locked until every step before it is done. On the right,
 * the chosen step's column of cells, a notebook read top to bottom.
 *
 * Each step is drawn again from what it holds whenever it changes, so going
 * back to a step shows what it read and measured in this window.
 */

import { loadOutline, call, type Outline, type StepInfo } from "./api";
import { h } from "./dom";
import { VIEWS, type Ctx } from "./steps/index";
import "./style.css";

const railHost = document.getElementById("steps")!;
const stage = document.getElementById("stage")!;

let outline: Outline;
let active = 0;
/** Steps finished in this window, beside those already done on this computer. */
const finished = new Set<string>();
/** What each step holds in this window: its readings, measurements and choices. */
const memory: Record<string, Record<string, any>> = {};

const byId = (id: string) => outline.steps.find((s) => s.id === id);
const isDone = (s: StepInfo) => s.status.done || finished.has(s.id);
/** A step is reached once every step before it is done. */
const isReachable = (i: number) => outline.steps.slice(0, i).every(isDone);
/** Done, but one of the steps it was measured on was saved again after it. */
function isStale(s: StepInfo): boolean {
  if (!isDone(s) || !s.status.saved_at) return false;
  return s.needs.some((id) => (byId(id)?.status.saved_at ?? "") > (s.status.saved_at as string));
}

function renderRail() {
  railHost.textContent = "";
  outline.steps.forEach((s, i) => {
    const reachable = isReachable(i);
    const stale = isStale(s);
    const b = h("button", {
      class: "step" + (i === active ? " active" : "") + (isDone(s) ? " done" : "") + (stale ? " stale" : "") + (reachable ? "" : " locked"),
      type: "button",
      disabled: reachable ? undefined : "",
    });
    const head = h("div", { class: "step-head" }, h("span", { class: "step-n" }, String(i + 1)), h("span", { class: "step-name" }, s.title));
    if (stale) head.append(h("span", { class: "review-tag" }, "review"));
    b.append(head);
    b.addEventListener("click", () => {
      if (!reachable || i === active || document.body.classList.contains("busy")) return;
      active = i;
      renderAll();
    });
    railHost.append(b);
  });
}

function renderStage(keepScroll = false) {
  const step = outline.steps[active];
  const state = (memory[step.id] ??= {});
  const scroll = stage.scrollTop;
  const column = h("div", { class: "setup-column" });
  const ctx: Ctx = {
    step,
    state,
    call: (action, values = {}) => call(step.id, action, values),
    refresh: () => {
      if (outline.steps[active].id === step.id) renderStage(true);
    },
    async finish() {
      finished.add(step.id);
      outline = await loadOutline();
      renderRail();
    },
    unfinish() {
      finished.delete(step.id);
      step.status.done = false;
      renderRail();
    },
    async reload() {
      finished.clear();
      for (const id of Object.keys(memory)) if (id !== step.id) delete memory[id];
      outline = await loadOutline();
      renderRail();
    },
    isStale: () => isStale(step),
    changedUnder: () =>
      step.needs.filter((id) => (byId(id)?.status.saved_at ?? "") > (step.status.saved_at ?? "")).map((id) => byId(id)!.title),
  };
  VIEWS[step.id](column, ctx);
  stage.textContent = "";
  stage.append(h("div", { class: "setup-notebook" }, column));
  stage.scrollTop = keepScroll ? scroll : 0;
}

function renderAll() {
  renderRail();
  renderStage();
}

loadOutline().then((o) => {
  outline = o;
  active = 0; // a setup always starts by connecting
  renderAll();
});
