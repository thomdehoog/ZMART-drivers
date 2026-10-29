/**
 * Step 3 — Define coordinate system origin.
 *
 * An origin is not three numbers: it is a reading of every drive -- the
 * stage's X and Y, the focus motor and the z piezo -- and the objective they
 * were read under. So the cell shows the drives one per row, and says plainly
 * that these readings are what will be (0, 0, 0). Nothing here drives the
 * stage: the operator takes the microscope to the origin in FLUOVIEW and presses Read;
 * Save and adopt then saves it as the origin from the next connect on.
 */

import { cell, note, press, publishRow, readout } from "../cells";
import { once, stale, type View } from "./index";

export const origin: View = (host, ctx) => {
  const s = ctx.state;
  const o = cell("Origin", "Drive to the origin in FLUOVIEW, then Read.");
  host.append(o.box);
  if (!once(ctx, "standing", "standing")) {
    o.body.append(note("Reading…"));
    return;
  }
  const warning = stale(ctx);
  if (warning) o.body.append(note(warning, "bad"));
  if (!s.adopted) {
    const d = s.standing?.origin;
    o.body.append(note(d
      ? `Published: (${Number(d.x_um).toFixed(0)}, ${Number(d.y_um).toFixed(0)}, ${Number(d.z_um).toFixed(1)}) µm.`
      : "No origin published."));
  }
  if (s.held) {
    o.body.append(note(s.adopted ? "Now (0, 0, 0):" : "Becomes (0, 0, 0):", s.adopted ? "ok" : ""));
    o.body.append(readout(s.held.actuators));
  }
  if (s.failed) o.body.append(note(s.failed, "bad"));

  const row = publishRow({
    label: "Save and adopt",
    published: s.adopted ? "Adopted." : null,
    disabled: !s.held,
    onPublish: async () => {
      s.failed = null;
      try {
        s.held = await ctx.call("publish");
        s.adopted = true;
        await ctx.finish();
      } catch (why) { s.failed = `Adopting failed — ${(why as Error).message}`; }
      ctx.refresh();
    },
  });
  row.prepend(press("Read", async () => {
    s.failed = null;
    try {
      s.held = await ctx.call("read");
      s.adopted = false;
    } catch (why) { s.failed = `Reading failed — ${(why as Error).message}`; }
    ctx.refresh();
  }, { busy: "reading…" }));
  o.body.append(row);
};
