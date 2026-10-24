/**
 * What each step shows on the right-hand side.
 *
 * A step is a function that draws itself and talks to the driver through
 * `ctx.run`. Every sentence here is for a microscopist who is setting the
 * microscope up for the first time, so each says what to do and why.
 */

import type { Result } from "./api";
import { h } from "./dom";

export interface Context {
  /** Run one of the step's actions. Shows the busy state and any error for you. */
  run(action: string, values?: object, button?: HTMLButtonElement): Promise<Result | null>;
  /** Say that the step is finished, with an optional note under its name. */
  finish(note?: string): void;
  /** Show a picture the driver saved, under the step's text. */
  show(image: string | null | undefined): void;
  /** Show a short message in the step's result area. */
  say(text: string): void;
}

export type StepView = (ctx: Context) => HTMLElement;

const button = (label: string, onclick: (b: HTMLButtonElement) => void, kind = "") => {
  const b = h("button", { class: `press ${kind}`, type: "button" }, label);
  b.addEventListener("click", () => onclick(b));
  return b;
};

const field = (label: string, input: HTMLElement, hint = "") =>
  h("label", { class: "field" }, h("span", { class: "label" }, label), input, hint && h("span", { class: "hint" }, hint));

const fmt = (n: unknown) => (typeof n === "number" ? n.toFixed(1) : String(n));

// 1 ---------------------------------------------------------------------------
const dataLocation: StepView = (ctx) => {
  const folder = h("input", { type: "text", value: "D:\\ZMART-data", size: 40 });
  return h(
    "div",
    {},
    field("Folder", folder, "A full path to a folder that already exists, such as D:\\ZMART-data."),
    button(
      "Check and save",
      async (b) => {
        const r = await ctx.run("save", { folder: folder.value }, b);
        if (r) {
          ctx.say("Saved. Every run of this microscope will go under this folder.");
          ctx.finish(String(folder.value));
        }
      },
      "primary",
    ),
  );
};

// 2 ---------------------------------------------------------------------------
const limits: StepView = (ctx) => {
  const recorded = new Set<number>();
  const save = button(
    "Save the limits",
    async (b) => {
      const r = await ctx.run("save", {}, b);
      if (r) {
        const [x, y] = [r.x_um as number[], r.y_um as number[]];
        ctx.say(`Saved. The stage may travel in X from ${fmt(x[0])} to ${fmt(x[1])} µm, and in Y from ${fmt(y[0])} to ${fmt(y[1])} µm.`);
        ctx.finish();
      }
    },
    "primary",
  );
  save.disabled = true;
  const rows = [1, 2, 3, 4].map((n) => {
    const where = h("span", { class: "where" }, "not recorded yet");
    const b = button(`Record corner ${n}`, async (btn) => {
      const r = await ctx.run("corner", { corner: n }, btn);
      if (!r) return;
      const p = r.position as { x_um: number; y_um: number };
      where.textContent = `X ${fmt(p.x_um)} µm, Y ${fmt(p.y_um)} µm`;
      recorded.add(n);
      save.disabled = recorded.size < 4;
    });
    return h("div", { class: "row" }, b, where);
  });
  return h(
    "div",
    {},
    h("p", {}, "Use the joystick, or LAS X, to drive the stage to a corner of the area it may safely reach, then press that corner's button. Do this for all four corners, in any order."),
    ...rows,
    h("div", { class: "actions" }, save),
  );
};

// 3 ---------------------------------------------------------------------------
const orientation: StepView = (ctx) => {
  const move = h("input", { type: "number", value: 40, min: 1 });
  const save = button("Save the orientation", async (b) => {
    if (await ctx.run("save", {}, b)) {
      ctx.say("Saved.");
      ctx.finish();
    }
  }, "primary");
  save.disabled = true;
  const check = button("Take a check picture", async (b) => {
    const r = await ctx.run("check", {}, b);
    if (r) {
      ctx.show(r.image);
      ctx.say("Does this picture follow the stage? If it does, save the orientation.");
      save.disabled = false;
    }
  });
  check.disabled = true;
  return h(
    "div",
    {},
    h("p", {}, "In LAS X, select the Navigator Expert job and put a field with clearly recognisable structure under the objective. The wizard takes three pictures, at the starting spot and after a small move in X and in Y, and works out how the image is turned."),
    field("Size of the stage move (µm)", move),
    button("Measure the orientation", async (b) => {
      const r = await ctx.run("measure", { move_um: Number(move.value) }, b);
      if (r) {
        ctx.show(r.image);
        ctx.say(`Found: ${r.label}${r.mirrored ? " (the image is also mirrored)" : ""}.`);
        check.disabled = false;
      }
    }, "primary"),
    h("div", { class: "actions" }, check, save),
  );
};

// 4 ---------------------------------------------------------------------------
const PARTS: [string, string, string][] = [
  ["focus_reference", "1. Reference focus", "With the reference objective, focus using Z-wide and keep the Z-galvo at 0. In LAS X turn Set Focus off and set up a manual z-stack that clearly brackets the sharp plane (for example 80 µm in 1 µm steps)."],
  ["focus_target", "2. Target focus", "Switch only to the target objective. Match the reference pixel size, and do not change the job, Z-wide or the Z-galvo."],
  ["xy_reference", "3. Reference position", "Switch back to the reference objective and remove the z-stack. Keep the Z-galvo at 0 and use the same job and pixel size."],
  ["xy_target", "4. Target position", "Switch to the target objective again. Do not move the motorised X/Y, and match the reference pixel size exactly."],
];

const calibration: StepView = (ctx) => {
  const name = h("input", { type: "text", value: "10x-20x" });
  const slot = h("input", { type: "number", value: 1, min: 1 });
  const save = button("Save the calibration", async (b) => {
    if (await ctx.run("save", {}, b)) {
      ctx.say("Saved. To calibrate another pair, reload this step.");
      ctx.finish();
    }
  }, "primary");
  save.disabled = true;
  const measure = h("div", { class: "measure", hidden: "" });
  PARTS.forEach(([part, title, text], i) => {
    const b = button(`Measure: ${title.slice(3)}`, async (btn) => {
      const r = await ctx.run("measure", { part }, btn);
      if (!r) return;
      ctx.say(`${title.slice(3)} done.`);
      if (i === PARTS.length - 1) save.disabled = false;
    });
    measure.append(h("div", { class: "part" }, h("h3", {}, title), h("p", {}, text), b));
  });
  return h(
    "div",
    {},
    h("p", {}, "This step measures one pair of objectives: a reference objective and a target. Complete the limits and orientation steps first. In LAS X, select the job you will use and keep it for the whole run; between measurements you change only the objective."),
    field("Name of the pair", name, "For example 10x-20x."),
    field("Reference objective (turret slot)", slot, "The objective every other one is measured against. Keep it the same for every pair."),
    button("Start", async (b) => {
      const r = await ctx.run("start", { pair_name: name.value, reference_slot: Number(slot.value) }, b);
      if (r) measure.hidden = false;
    }, "primary"),
    measure,
    h("div", { class: "actions" }, save),
  );
};

// 5 ---------------------------------------------------------------------------
const origin: StepView = (ctx) =>
  h(
    "div",
    {},
    h("p", {}, "When the stage stands on that spot and the sample is in focus, press the button. The wizard remembers the spot, the focus and the objective that is in place, so that every later session starts from the same zero point."),
    button("Set the zero point here", async (b) => {
      const r = await ctx.run("set", {}, b);
      if (r) {
        ctx.say("The zero point is saved. The setup is complete.");
        ctx.finish();
      }
    }, "primary"),
  );

export const VIEWS: Record<string, StepView> = {
  data_location: dataLocation,
  limits,
  orientation,
  calibration,
  origin,
};
