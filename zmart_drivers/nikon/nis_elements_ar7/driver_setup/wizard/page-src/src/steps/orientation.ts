/**
 * Step 4 — Image-to-stage orientation: which way the picture is turned.
 *
 * The driver takes a picture where the stage stands, moves a little along +X
 * and takes another, then along +Y and takes a third, comes back, and works
 * out which of the eight ways of laying a picture down lines it up with the
 * stage. The one thing it cannot check is what the pictures need: a sample
 * under the objective with structure in it, in focus.
 *
 * The first picture is shown turned the measured way, so the operator can
 * see that it looks right before saving. When the measurement cannot decide
 * (an empty field, say), the orientation can also be chosen by hand.
 */

import { cell, choice, field, note, part, picture, press, publishRow } from "../cells";
import { h } from "../dom";
import { once, stale, type View } from "./index";

const TURNS: [string, string][] = [["0", "not turned"], ["90", "90° clockwise"], ["180", "180°"], ["270", "270° clockwise"]];

export const orientation: View = (host, ctx) => {
  const s = ctx.state;
  const m = cell("Orientation", "Focus on a field with structure, then Start. Three pictures, eight candidates.");
  host.append(m.box);
  if (!once(ctx, "standing", "standing")) {
    m.body.append(note("Reading…"));
    return;
  }
  const warning = stale(ctx);
  if (warning) m.body.append(note(warning, "bad"));
  m.body.append(note(s.standing?.measured ? `In the configuration: ${s.standing.label}.` : "No orientation is saved yet: pictures are kept as the camera gives them."));

  s.step ??= "";
  const step = field({ type: "number", value: s.step, placeholder: "a fifth of the field", "aria-label": "how far the stage moves, in µm" }, "setup-number");
  step.addEventListener("input", () => { s.step = step.value; });
  m.body.append(h("div", { class: "setup-row" }, h("label", {}, "Stage move (µm)"), step));

  const held = s.held;
  if (held?.failed) m.body.append(note(`Failed — ${held.failed}`, "bad"));
  else if (held) {
    const result = part(m.body, "Result");
    if (held.accepted) {
      result.append(note(`Measured: ${held.label} (a ${Number(held.step_um).toFixed(1)} µm move).`, "ok"));
      result.append(note("The first picture, turned this way: moving the stage right should move the sample left in it, and moving the stage down should move it up."));
      if (held.image) result.append(picture(held.image, "the first picture, turned the measured way"));
    } else {
      result.append(note(`Not decided: ${held.why}. Choose a field with clear structure, in focus, and Rerun, or choose the orientation by hand below.`, "bad"));
    }
  }
  if (s.failed) m.body.append(note(s.failed, "bad"));

  const run = press(held ? "Rerun" : "Start", async () => {
    s.adopted = false;
    try { s.held = await ctx.call("measure", { step_um: s.step === "" ? null : Number(s.step) }); }
    catch (why) { s.held = { failed: (why as Error).message }; }
    ctx.refresh();
  }, { busy: "measuring…" });
  const row = publishRow({
    label: "Save and adopt",
    published: s.adopted ? "Adopted." : null,
    disabled: !held?.accepted,
    onPublish: async () => {
      s.failed = null;
      try {
        await ctx.call("publish");
        s.adopted = true;
        await ctx.finish();
      } catch (why) { s.failed = `Adopting failed — ${(why as Error).message}`; }
      ctx.refresh();
    },
  });
  row.prepend(run);
  m.body.append(row);

  /* By hand, for a field that cannot decide, or a camera whose mount is known. */
  const hand = cell("Or choose it by hand", "The quarter turn, clockwise, and whether a mirror is needed, that bring the picture to the stage's directions.");
  s.byHand ??= { rotate_deg: "0", mirrored: false };
  const turn = choice(TURNS, s.byHand.rotate_deg, "quarter turn");
  turn.addEventListener("change", () => { s.byHand.rotate_deg = turn.value; });
  const mirror = h("input", { type: "checkbox", "aria-label": "mirrored" }) as HTMLInputElement;
  mirror.checked = s.byHand.mirrored;
  mirror.addEventListener("change", () => { s.byHand.mirrored = mirror.checked; });
  hand.body.append(h("div", { class: "setup-row" }, h("label", {}, "Turn"), turn),
    h("div", { class: "setup-row" }, h("label", {}, "Mirrored"), mirror));
  hand.body.append(publishRow({
    label: "Save this one",
    published: s.adoptedByHand ? "Adopted." : null,
    onPublish: async () => {
      s.failed = null;
      try {
        await ctx.call("publish", { by_hand: true, rotate_deg: Number(s.byHand.rotate_deg), mirrored: s.byHand.mirrored });
        s.adoptedByHand = true;
        await ctx.finish();
      } catch (why) { s.failed = `Adopting failed — ${(why as Error).message}`; }
      ctx.refresh();
    },
  }));
  host.append(hand.box);
};
