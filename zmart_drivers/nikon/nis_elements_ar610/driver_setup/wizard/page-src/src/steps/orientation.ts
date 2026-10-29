/**
 * Step 4 — Image-to-stage calibration: which way the picture is turned.
 *
 * The driver takes a picture where the stage stands, steps the stage a known
 * distance along X and takes another, then the same along Y, and puts the
 * stage back. From which way the picture shifted, it works out which of the
 * eight ways of laying a picture down (four quarter turns, each mirrored or
 * not) lines it up with the stage, and how large a pixel is on the sample.
 * The one thing it cannot check is what the pictures need: a sample under
 * the objective with structure in it, in focus.
 *
 * Before saving, the driver takes one check picture laid down the measured
 * way, so the operator sees that it follows the stage.
 */

import { cell, note, part, picture, press, publishRow, readout, row, field } from "../cells";
import { h } from "../dom";
import { once, stale, type View } from "./index";

export const orientation: View = (host, ctx) => {
  const s = ctx.state;
  const m = cell("Orientation",
    "Focus on a field with structure, then Start. The stage steps along X and along Y and comes back; a picture is taken at each place.");
  host.append(m.box);
  if (!once(ctx, "standing", "standing")) {
    m.body.append(note("Reading…"));
    return;
  }
  const warning = stale(ctx);
  if (warning) m.body.append(note(warning, "bad"));
  s.step ??= "50";
  const held = s.held;
  if (held?.failed) m.body.append(note(`Failed — ${held.failed}`, "bad"));
  else if (held) {
    const result = part(m.body, "Result");
    if (held.accepted) result.append(readout(held.rows));
    else result.append(note(held.why, "bad"));
  } else if (s.standing?.measured) {
    const d = s.standing;
    part(m.body, "Result", { prose: `In the configuration: turned ${d.rotation_deg}°${d.reflection ? ", mirrored" : ""}`
      + (d.pixel_size_um ? `, ${Number(d.pixel_size_um).toFixed(4)} µm per pixel.` : ".") });
  }

  /* The driver's own check before it saves: one picture laid down with the
     measured orientation, which should follow the stage. */
  if (held?.accepted) {
    const check = part(m.body, "Check", {
      prose: "One more picture, laid down with this orientation. It should follow the stage: "
        + "a feature on the right of the field is to the right on the stage.",
    });
    check.append(h("div", { class: "setup-publish" }, press(s.checked ? "Take it again" : "Take a check picture", async () => {
      s.checkFailed = null;
      try {
        const r = await ctx.call("check");
        s.checked = r.image;
      } catch (why) { s.checkFailed = (why as Error).message; }
      ctx.refresh();
    }, { busy: "imaging…" })));
    if (s.checkFailed) check.append(note(`Failed — ${s.checkFailed}`, "bad"));
    if (s.checked) check.append(picture(s.checked, "the check picture, with the measured orientation"));
  }
  if (s.failed) m.body.append(note(s.failed, "bad"));

  const stepField = field({ type: "number", value: s.step, min: "1", "aria-label": "stage step in micrometres" }, "setup-number");
  stepField.addEventListener("input", () => { s.step = stepField.value; });
  m.body.append(row("Stage step (µm)", stepField));
  m.body.append(note("The step must be smaller than the field of view, so the two pictures overlap. 50 µm suits a 10x objective; take less at higher magnification."));

  const run = press(held ? "Rerun" : "Start", async () => {
    s.adopted = false;
    s.checked = null;
    try { s.held = await ctx.call("measure", { stage_move_um: Number(s.step) }); }
    catch (why) { s.held = { failed: (why as Error).message }; }
    ctx.refresh();
  }, { busy: "measuring…" });
  if (!held) {
    m.body.append(h("div", { class: "setup-publish" }, run));
  } else {
    const publish = publishRow({
      label: "Save and adopt",
      published: s.adopted ? "Adopted." : null,
      disabled: !held.accepted || !s.checked,
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
    publish.prepend(run);
    m.body.append(publish);
  }
};
