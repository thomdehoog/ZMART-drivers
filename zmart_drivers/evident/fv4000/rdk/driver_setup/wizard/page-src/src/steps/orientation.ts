/**
 * Step 4 — Image-to-stage calibration: which way the picture is turned.
 *
 * The driver images the field where the stage stands, moves a known distance
 * along +X and images again, comes back, moves along +Y and images again, and
 * works out which of the eight ways of laying a picture down lines it up with
 * the stage. The one thing it cannot check is what the pictures need: a
 * specimen under the objective with structure in it, in focus.
 *
 * Before saving, the driver asks for one check picture taken with the
 * measured orientation, so the operator sees that it follows the stage.
 */

import { cell, note, part, picture, press, publishRow } from "../cells";
import { h } from "../dom";
import { once, stale, type View } from "./index";

export const orientation: View = (host, ctx) => {
  const s = ctx.state;
  const m = cell("Orientation", "Focus on a field with structure, then Start. Three images, eight candidates.");
  host.append(m.box);
  if (!once(ctx, "standing", "standing")) {
    m.body.append(note("Reading…"));
    return;
  }
  const warning = stale(ctx);
  if (warning) m.body.append(note(warning, "bad"));
  const held = s.held;
  if (held?.failed) m.body.append(note(`Failed — ${held.failed}`, "bad"));
  else if (held) {
    /* The sheet says what was found: the detected correction above the eight
       candidates, the chosen one marked. */
    const result = part(m.body, "Result");
    if (!held.accepted && held.why) result.append(note(held.why, "bad"));
    if (held.image) result.append(picture(held.image, "the detected correction and the eight candidates"));
  } else if (s.standing?.measured) {
    const d = s.standing;
    const result = part(m.body, "Result", { prose: `In the configuration: ${d.rotation_deg}°${d.reflection ? ", mirrored" : ""}.` });
    if (d.image) result.append(picture(d.image, "the sheet measured for the configuration"));
  }

  /* The driver's own check before it saves: one picture with the measured
     orientation, which should follow the stage. */
  if (held?.accepted) {
    const check = part(m.body, "Check", {
      prose: "One more picture, taken with this orientation. It should follow the stage: moving the stage right moves the picture right.",
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

  const run = press(held ? "Rerun" : "Start", async () => {
    s.adopted = false;
    s.checked = null;
    try { s.held = await ctx.call("measure", { stage_move_um: 40 }); }
    catch (why) { s.held = { failed: (why as Error).message }; }
    ctx.refresh();
  }, { busy: "measuring…" });
  if (!held) {
    m.body.append(h("div", { class: "setup-publish" }, run));
  } else {
    const row = publishRow({
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
    row.prepend(run);
    m.body.append(row);
  }
};
