/** The setup wizard's page: the steps on the left, the chosen step on the right. */

import { call, loadOutline, type Outline } from "./api";
import { h } from "./dom";
import { VIEWS, type Context } from "./steps";
import "./style.css";

const rail = document.getElementById("rail")!;
const panel = document.getElementById("panel")!;

let outline: Outline;
let current = 0;
const finished = new Set<string>();
const notes = new Map<string, string>();

/** A step can be opened once every step before it is done. */
const reachable = (i: number) => outline.steps.slice(0, i).every((s) => finished.has(s.id));

function drawRail() {
  rail.textContent = "";
  rail.append(h("h1", {}, outline.title));
  outline.steps.forEach((step, i) => {
    const state = finished.has(step.id) ? "done" : reachable(i) ? "open" : "locked";
    const item = h(
      "button",
      { class: `rail-step ${state}${i === current ? " current" : ""}`, type: "button", disabled: state === "locked" ? "" : undefined },
      h("span", { class: "num" }, finished.has(step.id) ? "✓" : String(i + 1)),
      h("span", { class: "name" }, step.title, notes.get(step.id) ? h("small", {}, notes.get(step.id)) : null),
    );
    item.addEventListener("click", () => { current = i; drawAll(); });
    rail.append(item);
  });
}

function drawPanel() {
  const step = outline.steps[current];
  const message = h("p", { class: "message", role: "status" });
  const picture = h("img", { class: "picture", alt: "", hidden: "" });

  const ctx: Context = {
    async run(action, values, btn) {
      message.className = "message";
      message.textContent = "Working…";
      if (btn) btn.disabled = true;
      try {
        const result = await call(step.id, action, values);
        message.textContent = "";
        return result;
      } catch (why) {
        message.className = "message error";
        message.textContent = (why as Error).message;
        return null;
      } finally {
        if (btn) btn.disabled = false;
      }
    },
    finish(note) {
      finished.add(step.id);
      if (note) notes.set(step.id, note);
      drawRail();
      next.hidden = current >= outline.steps.length - 1;
    },
    show(image) {
      picture.hidden = !image;
      if (image) picture.src = image;
    },
    say(text) {
      message.className = "message ok";
      message.textContent = text;
    },
  };

  const next = h("button", { class: "press next", type: "button", hidden: finished.has(step.id) && current < outline.steps.length - 1 ? undefined : "" }, "Next step →");
  next.addEventListener("click", () => { current += 1; drawAll(); });

  panel.textContent = "";
  const already = step.status.done && step.status.note
    ? h("p", { class: "already" }, `Already set up on this computer. ${step.status.note}. You can run this step again to replace it.`)
    : null;
  panel.append(
    h("header", {}, h("span", { class: "kicker" }, `Step ${current + 1} of ${outline.steps.length}`), h("h2", {}, step.title), h("p", { class: "purpose" }, step.purpose)),
    ...(already ? [already] : []),
    VIEWS[step.id](ctx),
    message,
    picture,
    next,
  );
}

function drawAll() {
  drawRail();
  drawPanel();
}

loadOutline().then((o) => {
  outline = o;
  for (const s of o.steps) if (s.status.done) { finished.add(s.id); if (s.status.note) notes.set(s.id, s.status.note); }
  current = Math.max(0, o.steps.findIndex((s) => !finished.has(s.id)));
  drawAll();
});
