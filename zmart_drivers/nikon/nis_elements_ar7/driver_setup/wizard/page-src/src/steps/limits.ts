/**
 * Step 2 — Define limits: Configure, then import the stage and focus limits.
 *
 * **Configure** is the limits document itself: the stage and focus ranges,
 * which objectives and optical configurations automation may choose, and,
 * for each guarded piece of hardware NIS reports (lasers, TIRF, heating,
 * gas), whether automation may set it and within which range. It is drawn
 * from the limits the driver holds (saved, or its cautious defaults), so
 * nothing in the file is hidden from the operator who is about to save it.
 *
 * **Import** is what is read off the instrument: the operator drives to the
 * four corners of the safe area, and to the lowest and highest safe focus, in
 * NIS-Elements or with the joystick, and imports each. All numbers are NIS's
 * own stage coordinates, in micrometres.
 */

import { cell, field, note, press, publishRow } from "../cells";
import { h } from "../dom";
import { once, type View } from "./index";

type Range = { range: (number | null)[] };
type Allowed = { allowed: (number | string)[] } | [];

const CORNERS: [string, string][] = [
  ["top_left", "Top left"], ["top_right", "Top right"], ["bottom_left", "Bottom left"], ["bottom_right", "Bottom right"],
];
const FOCUS: [string, string][] = [["low", "Lowest safe focus"], ["high", "Highest safe focus"]];

const fmt = (v: unknown, d = 1) => (v === null || v === undefined || v === "" ? "open" : Number(v).toFixed(d));

export const limits: View = (host, ctx) => {
  const s = ctx.state;
  if (!once(ctx, "loaded", "document")) {
    const { box, body } = cell("Configure");
    body.append(note("Reading…"));
    host.append(box);
    return;
  }
  if (s.loaded.failed) {
    const { box, body } = cell("Configure");
    body.append(note(s.loaded.failed, "bad"));
    host.append(box);
    return;
  }
  const doc = s.loaded;
  s.held ??= structuredClone(doc.document);
  s.held.devices ??= {};
  s.corners ??= {} as Record<string, { x: number; y: number }>;
  s.focus ??= {} as Record<string, number>;
  const held = s.held;
  const adopted = s.adopted ? "Adopted." : null;

  const adopt = async () => {
    s.failed = null;
    try {
      await ctx.call("publish", { document: held, corners: s.corners, focus: s.focus });
      s.adopted = true;
      await ctx.finish();
    } catch (why) { s.failed = `Adopting failed — ${(why as Error).message}`; s.adopted = false; }
    ctx.refresh();
  };

  /* ---- Configure ---------------------------------------------------------------- */
  const edit = cell("Configure", "Every limit the driver asks before it changes anything. Ranges include both ends.");
  edit.body.append(note(`From ${doc.source === "published" ? "the published limits" : "the defaults"}.`, ""));
  edit.body.lastElementChild!.classList.add("setup-source");

  for (const axis of doc.axes as { key: string; label: string; unit: string }[]) {
    const entry = held[axis.key] as Range;
    const travel = doc.travel?.[axis.key];
    const fields = [0, 1].map((end) => {
      const f = field({
        type: "number", "aria-label": `${axis.label} ${end ? "maximum" : "minimum"}`, value: entry.range[end] ?? "",
      }, "setup-number");
      f.addEventListener("change", () => {
        const range = [...entry.range];
        range[end] = f.value === "" ? null : Number(f.value);
        held[axis.key] = { range };
      });
      return f;
    });
    edit.body.append(h("div", { class: "setup-row setup-limit" }, h("label", {}, `${axis.label} (${axis.unit})`), ...fields));
    if (travel) edit.body.append(note(`NIS lets ${axis.label} travel from ${fmt(travel[0])} to ${fmt(travel[1])} µm; the limits are cut to that.`));
  }

  /* A list of what automation may choose; none ticked means any. */
  const chooser = (key: string, title: string, options: [string | number, string][]) => {
    const entry = held[key] as Allowed;
    const allowed = new Set(Array.isArray(entry) ? [] : entry.allowed);
    const row = h("div", { class: "setup-choices" });
    for (const [value, words] of options) {
      const tick = h("input", { type: "checkbox", "aria-label": `${title}: ${words}` }) as HTMLInputElement;
      tick.checked = allowed.has(value);
      tick.addEventListener("change", () => {
        if (tick.checked) allowed.add(value); else allowed.delete(value);
        held[key] = allowed.size ? { allowed: options.map(([v]) => v).filter((v) => allowed.has(v)) } : [];
      });
      row.append(h("label", { class: "setup-choice" }, tick, words));
    }
    edit.body.append(h("div", { class: "setup-row" }, h("label", {}, title), row));
  };
  chooser("objective_position", "Objectives", Object.entries(doc.objectives as Record<string, string>)
    .map(([p, name]): [number, string] => [Number(p), `${p}: ${name}`]));
  chooser("optical_configuration", "Optical configurations",
    (doc.optical_configurations as string[]).map((name): [string, string] => [name, name]));
  edit.body.append(note("Tick the objectives and optical configurations automation may choose; with none ticked, any may be chosen."));

  /* Guarded hardware: refused until allowed here. */
  if ((doc.guarded as unknown[]).length) {
    edit.body.append(h("div", { class: "setup-part-title" }, h("span", {}, "Guarded hardware")));
    edit.body.append(note("Lasers, TIRF, heating and gas are refused until they are allowed here, because a wrong value could harm the sample, the microscope or a person."));
  }
  for (const g of doc.guarded as { key: string; says: string; unit: string | null; kind: string }[]) {
    const entry = held.devices[g.key] as { range?: number[]; allowed?: number[] } | undefined;
    const tick = h("input", { type: "checkbox", "aria-label": `allow ${g.key}` }) as HTMLInputElement;
    tick.checked = Boolean(entry);
    const fields: HTMLInputElement[] = g.kind === "number"
      ? [0, 1].map((end) => field({
          type: "number", "aria-label": `${g.key} ${end ? "maximum" : "minimum"}`,
          value: entry?.range?.[end] ?? "",
        }, "setup-number"))
      : [];
    for (const f of fields) f.disabled = !tick.checked;
    tick.addEventListener("change", () => {
      if (!tick.checked) delete held.devices[g.key];
      else held.devices[g.key] = g.kind === "number" ? { range: [0, 0] } : { allowed: [0, 1] };
      ctx.refresh();
    });
    fields.forEach((f, end) => f.addEventListener("change", () => {
      const range = [...(held.devices[g.key]?.range ?? [0, 0])];
      range[end] = Number(f.value);
      held.devices[g.key] = { range };
    }));
    edit.body.append(h("div", { class: "setup-row setup-limit" }, tick,
      h("label", { title: g.says }, `${g.key}${g.unit ? ` (${g.unit})` : ""}`), ...fields));
  }
  if (s.failed) edit.body.append(note(s.failed, "bad"));
  edit.body.append(publishRow({ label: "Save and adopt", onPublish: adopt, published: adopted }));
  host.append(edit.box);

  /* ---- Import the stage limits: four corners ----------------------------------- */
  const imp = cell("Import X/Y stage limits",
    "Drive to each corner of the safe area in NIS-Elements or with the joystick, then import it. The four corners set X and Y above.");
  for (const [key, label] of CORNERS) {
    const got = s.corners[key];
    imp.body.append(h("div", { class: "setup-row" },
      h("label", {}, label),
      h("span", { class: "setup-note" }, got ? `x ${got.x.toFixed(1)} · y ${got.y.toFixed(1)} µm` : "not yet"),
      press(got ? "Update" : "Import", async () => {
        s.importFailed = null;
        try {
          const r = await ctx.call("corner");
          s.corners[key] = { x: Number(r.x), y: Number(r.y) };
          if (CORNERS.every(([k]) => s.corners[k])) {
            const xs = CORNERS.map(([k]) => s.corners[k].x);
            const ys = CORNERS.map(([k]) => s.corners[k].y);
            held.x_um = { range: [Math.min(...xs), Math.max(...xs)] };
            held.y_um = { range: [Math.min(...ys), Math.max(...ys)] };
          }
        } catch (why) { s.importFailed = (why as Error).message; }
        ctx.refresh();
      }, { busy: "reading…" })));
  }

  /* ---- Import the focus limits: lowest and highest ------------------------------ */
  imp.body.append(h("div", { class: "setup-part-title" }, h("span", {}, "Focus")));
  imp.body.append(note("Focus as low as is safe (the objective not touching the sample holder), import it; then as high as is useful, and import that."));
  for (const [key, label] of FOCUS) {
    const got = s.focus[key];
    imp.body.append(h("div", { class: "setup-row" },
      h("label", {}, label),
      h("span", { class: "setup-note" }, got !== undefined ? `z ${Number(got).toFixed(1)} µm` : "not yet"),
      press(got !== undefined ? "Update" : "Import", async () => {
        s.importFailed = null;
        try {
          const r = await ctx.call("focus");
          s.focus[key] = Number(r.z);
          if (s.focus.low !== undefined && s.focus.high !== undefined) {
            held.z_um = { range: [Math.min(s.focus.low, s.focus.high), Math.max(s.focus.low, s.focus.high)] };
          }
        } catch (why) { s.importFailed = (why as Error).message; }
        ctx.refresh();
      }, { busy: "reading…" })));
  }
  if (s.importFailed) imp.body.append(note(`Could not read the stage — ${s.importFailed}`, "bad"));
  const allFour = CORNERS.every(([k]) => s.corners[k]);
  if (allFour) {
    imp.body.append(note(`X ${fmt(held.x_um.range[0])} … ${fmt(held.x_um.range[1])} · Y ${fmt(held.y_um.range[0])} … ${fmt(held.y_um.range[1])} µm set above.`, "ok"));
  }
  imp.body.append(publishRow({ label: "Save and adopt", onPublish: adopt, published: adopted, disabled: !allFour }));
  host.append(imp.box);
};
