/**
 * Step 4 — Image-to-stage calibration: which way the picture is turned, and
 * how large one pixel is.
 *
 * The driver takes a snap with the chosen ZEN experiment where the stage
 * stands, moves the stage a little along X and takes another, then a little
 * along Y and a third, and comes back. How the sample shifted between the
 * snaps says how the camera is turned against the stage and how many
 * micrometres one pixel is. The one thing it cannot check is what the snaps
 * need: a sample under the objective with structure in it, in focus.
 *
 * The three snaps are shown side by side, so the operator can see that they
 * had structure and that it moved; then Save and adopt.
 */

import { cell, choice, note, part, picture, press, publishRow } from "../cells";
import { h } from "../dom";
import { once, stale, type View } from "./index";

const turnWords = (deg: unknown, mirrored: unknown) => `${deg}°${mirrored ? ", mirrored" : ""}`;
const pixelWords = (p: unknown) =>
  Array.isArray(p) && p.length === 2 ? `${Number(p[0]).toFixed(4)} × ${Number(p[1]).toFixed(4)} µm` : "not known";

export const orientation: View = (host, ctx) => {
  const s = ctx.state;
  const m = cell("Orientation",
    "Focus on a field with structure (cells, beads, a grid), choose the experiment the snaps should use, then Start. "
    + "The stage moves a few micrometres along X and along Y, and comes back.");
  host.append(m.box);
  if (!once(ctx, "standing", "standing") || !once(ctx, "listed", "experiments")) {
    m.body.append(note("Reading…"));
    return;
  }
  const warning = stale(ctx);
  if (warning) m.body.append(note(warning, "bad"));
  if (s.listed.failed) {
    m.body.append(note(s.listed.failed, "bad"));
    return;
  }
  const names: string[] = s.listed.experiments ?? [];
  s.experiment ??= s.listed.loaded ?? names[0] ?? "";
  const select = choice(names.map((n): [string, string] => [n, n]), s.experiment, "experiment for the snaps");
  select.addEventListener("change", () => { s.experiment = select.value; });
  m.body.append(h("div", { class: "setup-row" }, h("label", {}, "Experiment"), select));
  if (!names.length) m.body.append(note("ZEN lists no experiment. Save one in ZEN first: a snap with one channel is enough.", "bad"));

  const held = s.held;
  if (held?.failed) m.body.append(note(`Failed — ${held.failed}`, "bad"));
  else if (held && !held.accepted) m.body.append(note(held.why, "bad"));
  else if (held) {
    const result = part(m.body, "Result", { prose: held.description });
    result.append(note(`Turned ${turnWords(held.rotation_deg, held.reflection)} · one pixel ${pixelWords(held.pixel_size_um)}`
      + ` · ${held.image_size_px[0]} × ${held.image_size_px[1]} pixels`, "ok"));
    if (held.image) result.append(picture(held.image, "the three snaps: where the stage stood, after the move along X, after the move along Y"));
    result.append(note("Left: where the stage stood. Middle: after the move along X. Right: after the move along Y. "
      + "The same structure should appear in all three, a little shifted."));
  } else if (s.standing?.measured) {
    const d = s.standing;
    const result = part(m.body, "Result", {
      prose: `In the configuration: turned ${turnWords(d.rotation_deg, d.reflection)}, one pixel ${pixelWords(d.pixel_size_um)}.`,
    });
    if (d.image) result.append(picture(d.image, "the snaps the configuration was measured from"));
  }
  if (s.failed) m.body.append(note(s.failed, "bad"));

  const run = press(held ? "Rerun" : "Start", async () => {
    s.adopted = false;
    try { s.held = await ctx.call("measure", { experiment: s.experiment }); }
    catch (why) { s.held = { failed: (why as Error).message }; }
    ctx.refresh();
  }, { busy: "measuring…", disabled: !names.length });
  if (!held) {
    m.body.append(h("div", { class: "setup-publish" }, run));
  } else {
    const row = publishRow({
      label: "Save and adopt",
      published: s.adopted ? "Adopted." : null,
      disabled: !held.accepted,
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
