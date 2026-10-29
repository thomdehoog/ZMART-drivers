/**
 * Step 5 — Hardware settings: what NIS reports this microscope has.
 *
 * One row per hardware setting NIS reports: shutters, lamps, filter wheels,
 * the light path, Perfect Focus and the others of the driver's table. Each
 * shows what it is, its value now, and a field to try another value. A try
 * goes through the driver like any change: the limits are asked first, and
 * the setting is read back. A guarded setting (lasers, TIRF, heating, gas) is
 * refused until the limits step allows it.
 *
 * When the rows look right (each device found, each named as expected, each
 * following a try), Looks right notes it, and the step is done.
 */

import { cell, field, note, press, publishRow } from "../cells";
import { h } from "../dom";
import { once, type View } from "./index";

type Setting = {
  name: string; label: string | null; group: string; says: string; kind: string;
  unit: string | null; positions: number | null; value: unknown; guarded: boolean;
};

const takes = (x: Setting) =>
  x.kind === "switch" ? "1 or 0"
    : x.kind === "choice" ? (x.positions ? `1 to ${x.positions}` : "a position from 1")
      : `a number${x.unit ? ` (${x.unit})` : ""}`;

export const hardware: View = (host, ctx) => {
  const s = ctx.state;
  if (!once(ctx, "inventory", "inventory")) {
    const { box, body } = cell("Hardware settings");
    body.append(note("Reading…"));
    host.append(box);
    return;
  }
  if (s.inventory.failed) {
    const { box, body } = cell("Hardware settings");
    body.append(note(s.inventory.failed, "bad"));
    host.append(box);
    return;
  }
  const settings = s.inventory.settings as Setting[];
  s.tried ??= {} as Record<string, string>;
  s.why ??= {} as Record<string, string>;

  const groups = [...new Set(settings.map((x) => x.group))];
  if (!settings.length) {
    const { box, body } = cell("Hardware settings");
    body.append(note("NIS reports none of the hardware settings the driver knows on this microscope."));
    host.append(box);
  }
  for (const group of groups) {
    const c = cell(group[0].toUpperCase() + group.slice(1));
    for (const x of settings.filter((y) => y.group === group)) {
      s.tried[x.name] ??= String(x.value ?? "");
      const input = field({ type: "number", value: s.tried[x.name], "aria-label": `${x.name}, a value to try` }, "setup-number");
      input.addEventListener("input", () => { s.tried[x.name] = input.value; });
      const label = x.label && !x.name.endsWith(x.label) ? `${x.name} (${x.label})` : x.name;
      c.body.append(h("div", { class: "setup-row setup-limit" },
        h("label", { title: x.says }, label + (x.guarded ? " · guarded" : "")),
        h("span", { class: "setup-note" }, `now ${x.value ?? "?"} · takes ${takes(x)}`),
        input,
        press("Try", async () => {
          delete s.why[x.name];
          try {
            const r = await ctx.call("set", { name: x.name, value: Number(s.tried[x.name]) });
            x.value = r.value;
            if (r.unconfirmed?.length) s.why[x.name] = "Sent, but not seen to take effect.";
          } catch (why) { s.why[x.name] = (why as Error).message; }
          ctx.refresh();
        }, { busy: "setting…" })));
      c.body.append(note(x.says));
      if (s.why[x.name]) c.body.append(note(s.why[x.name], "bad"));
    }
    host.append(c.box);
  }

  const done = cell("Looks right?", "When each device NIS reports is here, and follows a try, note it.");
  if (s.failed) done.body.append(note(s.failed, "bad"));
  done.body.append(publishRow({
    label: "Looks right",
    published: s.noted ? "Noted." : null,
    onPublish: async () => {
      s.failed = null;
      try {
        await ctx.call("publish");
        s.noted = true;
        await ctx.finish();
      } catch (why) { s.failed = (why as Error).message; }
      ctx.refresh();
    },
  }));
  done.body.append(h("div", { class: "setup-publish" }, press("Read again", async () => {
    delete s.inventory;
    delete s["inventory:asked"];
    ctx.refresh();
  })));
  host.append(done.box);
};
