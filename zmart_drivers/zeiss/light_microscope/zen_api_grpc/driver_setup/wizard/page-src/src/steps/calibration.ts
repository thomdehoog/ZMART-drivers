/**
 * Step 5 — Objective calibration: the reference objective, its presets, and
 * the measures of the one chosen.
 *
 * Ideally a microscope is parcentric and parfocal: change the objective and
 * the same spot stays centred and in focus, so every objective's offset from
 * every other is zero. Often it is not, so the offsets are measured. Choose
 * the reference objective, and every other objective on the changer gets a
 * preset, starting at the ideal offset of (0, 0, 0) or at what the saved
 * calibration holds. Choose a preset and measure it.
 *
 * The measuring is done with the operator's eye, in ZEN as always: under the
 * reference, centre a small sharp feature in the live view and focus on it,
 * and Record. Turn to the target (the stage does not move), centre and focus
 * the same feature again, and Record. The difference between the two
 * positions is the target's offset, shown as three plain tiles. Save and
 * adopt saves every pair measured in this window together with the reference.
 */

import { cell, choice, note, part, press, publishRow, readout, signed } from "../cells";
import { h } from "../dom";
import { once, stale, type View } from "./index";

type Lens = { position: number; name: string; magnification: number | null };
type Preset = { target: number; t: { x: number | null; y: number | null; z: number | null }; state: "default" | "published" | "measured";
  reference?: Record<string, any>; other?: Record<string, any> };

const lensLabel = (l: Lens | undefined) => (l ? `position ${l.position} · ${l.name}` : "—");
const offsetText = (t: Preset["t"]) => `(${signed(t.x)}, ${signed(t.y)}, ${signed(t.z)}) µm`;
const STATE = { default: "not measured", published: "held", measured: "measured" };
const where = (r: Record<string, any>): [string, string][] => [
  ["x (stage)", `${Number(r.x_um).toFixed(2)} µm`],
  ["y (stage)", `${Number(r.y_um).toFixed(2)} µm`],
  ["z (focus)", `${Number(r.z_um).toFixed(2)} µm`],
];

export const calibration: View = (host, ctx) => {
  const s = ctx.state;
  const presetsCell = cell("Presets", "Each objective is measured against the reference. Until measured, its offset is taken as zero.");
  host.append(presetsCell.box);
  if (!once(ctx, "loaded", "lenses")) {
    presetsCell.body.append(note("Reading…"));
    return;
  }
  if (s.loaded.failed) {
    presetsCell.body.append(note(s.loaded.failed, "bad"));
    return;
  }
  const lenses: Lens[] = s.loaded.lenses;
  const byPosition = (p: number | null) => lenses.find((l) => l.position === p);
  if (!("reference" in s)) s.reference = s.loaded.reference ?? null;
  if (!("inPlace" in s)) s.inPlace = s.loaded.in_place ?? null;
  /* One preset per other objective, starting from what the saved calibration
     holds (relative to its reference), or from zero. */
  if (!s.presets && s.reference !== null) {
    s.presets = {} as Record<string, Preset>;
    const fromSaved = s.loaded.reference === s.reference;
    for (const l of lenses) {
      if (l.position === s.reference) continue;
      const kept = fromSaved ? s.loaded.held[String(l.position)] : undefined;
      s.presets[String(l.position)] = {
        target: l.position,
        t: kept ? { x: kept[0], y: kept[1], z: kept[2] } : { x: 0, y: 0, z: 0 },
        state: kept ? "published" : "default",
      };
    }
  }
  const reference = byPosition(s.reference);
  const warning = stale(ctx);
  if (warning) presetsCell.body.append(note(warning, "bad"));

  if (!lenses.length) presetsCell.body.append(note("ZEN lists no objectives.", "bad"));
  else {
    const select = choice([["", "choose…"], ...lenses.map((l): [string, string] => [String(l.position), lensLabel(l)])],
      reference ? String(reference.position) : "", "reference objective");
    /* Once chosen, the reference is locked: the presets hang from it. Reset
       clears it, and them, to choose again. */
    select.disabled = Boolean(reference);
    select.addEventListener("change", () => {
      s.reference = select.value === "" ? null : Number(select.value);
      s.presets = null;
      s.current = null;
      ctx.refresh();
    });
    const r = h("div", { class: "setup-row setup-reference" }, h("label", {}, "Reference objective"), select);
    if (reference) r.append(press("Reset", () => { s.reference = null; s.presets = null; s.current = null; ctx.refresh(); }));
    presetsCell.body.append(r);
  }
  if (reference && s.loaded.reference !== null && s.loaded.reference !== reference.position) {
    presetsCell.body.append(note(`The saved calibration was measured against ${lensLabel(byPosition(s.loaded.reference))}. `
      + "A different reference starts a new calibration: Save and adopt keeps only the objectives measured here.", "bad"));
  }
  const presets = (s.presets ?? {}) as Record<string, Preset>;
  const positions = Object.keys(presets);
  if (positions.length) {
    const list = h("div", { class: "setup-sets" });
    for (const position of positions) {
      const p = presets[position];
      const b = h("button", {
        type: "button",
        class: "setup-set" + (position === s.current ? " chosen" : "") + (p.state === "default" ? " default" : ""),
      }, h("b", {}, `${reference?.name ?? "reference"} vs ${byPosition(p.target)?.name ?? `position ${position}`}`),
        h("span", { class: "setup-set-pair" }, offsetText(p.t)), h("span", { class: "setup-set-state" }, STATE[p.state]));
      b.addEventListener("click", () => { s.current = position === s.current ? null : position; ctx.refresh(); });
      list.append(b);
    }
    presetsCell.body.append(list);
  } else if (reference) presetsCell.body.append(note("No other objective on the changer.", "bad"));

  /* ---- the chosen preset: reference, target, and the confirmation --------- */
  const current = s.current ? presets[s.current] : null;
  if (!current || !reference) return;
  const target = byPosition(current.target)!;
  const pair = { reference: reference.position, target: target.position };
  const work = cell(`Calibration — ${reference.name} vs ${target.name}`);
  host.append(work.box);

  const turn = (lens: Lens) => press(`Turn to ${lens.name}`, async () => {
    s.turnFailed = null;
    try { s.inPlace = (await ctx.call("turn", { position: lens.position })).in_place; }
    catch (why) { s.turnFailed = (why as Error).message; }
    ctx.refresh();
  }, { busy: "turning…", disabled: s.inPlace === lens.position });
  const record = (side: "reference" | "target", lens: Lens) => press(`Record ${side} (${lens.name})`, async () => {
    try {
      const r = await ctx.call("record", { ...pair, side });
      if (side === "reference") { current.reference = r; current.other = undefined; }
      else {
        current.other = r;
        const t = r.translation_um;
        current.t = { x: t.x, y: t.y, z: t.z };
        current.state = "measured";
      }
    } catch (why) {
      if (side === "reference") current.reference = { failed: (why as Error).message };
      else current.other = { failed: (why as Error).message };
    }
    ctx.refresh();
  }, { busy: "reading…" });

  const one = part(work.body, `Under the reference (${reference.name})`, { number: 1, prose:
    "Find a small, sharp feature of the sample: a bead, or the corner of a grid. "
    + "Centre it in ZEN's live view and focus on it, then Record. The driver reads where the stage and the focus stand." });
  one.append(h("div", { class: "setup-row setup-left" }, turn(reference), record("reference", reference)));
  if (current.reference?.failed) one.append(note(`Failed — ${current.reference.failed}`, "bad"));
  else if (current.reference) one.append(readout(where(current.reference)));

  const two = part(work.body, `Under the target (${target.name})`, { number: 2, prose:
    `Turn to the ${target.name}; the stage does not move. If it sits closer to the sample than the reference, focus away a little first. `
    + "Then centre and focus the same feature again, and Record." });
  two.append(h("div", { class: "setup-row setup-left" }, turn(target), record("target", target)));
  if (s.turnFailed) two.append(note(`Could not turn — ${s.turnFailed}`, "bad"));
  if (current.other?.failed) two.append(note(`Failed — ${current.other.failed}`, "bad"));
  else if (current.other) two.append(readout(where(current.other)));

  const confirm = part(work.body, "Confirmation", { number: 3, prose:
    `${target.name} relative to ${reference.name}. Save and adopt saves the reference and every pair measured here; `
    + "an objective not measured keeps what the saved calibration holds for it." });
  const tiles = h("div", { class: "setup-xyz" });
  for (const [axis, value] of [["X", current.t.x], ["Y", current.t.y], ["Z", current.t.z]] as const) {
    tiles.append(h("div", { class: "setup-xyz-tile" + (current.state === "default" ? " default" : "") },
      h("span", { class: "setup-xyz-axis" }, axis), h("span", { class: "setup-xyz-value" }, signed(value)), h("span", { class: "setup-xyz-unit" }, "µm")));
  }
  confirm.append(tiles);
  confirm.append(note(current.state === "measured" ? "Measured." : current.state === "published" ? "Held, not measured here."
    : "Not measured: left out of the calibration until it is."));
  if (s.failed) confirm.append(note(s.failed, "bad"));
  const measuredHere = Object.values(presets).some((p) => p.state === "measured");
  confirm.append(publishRow({
    label: "Save and adopt",
    published: s.adopted ? "Adopted." : null,
    disabled: !measuredHere,
    onPublish: async () => {
      s.failed = null;
      try {
        await ctx.call("publish", { reference: reference.position });
        s.adopted = true;
        await ctx.finish();
      } catch (why) { s.failed = `Adopting failed — ${(why as Error).message}`; }
      ctx.refresh();
    },
  }));
};
