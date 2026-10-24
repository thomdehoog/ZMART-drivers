/**
 * Step 5 — Objective calibration: the reference objective, its presets, and
 * the measures of the one chosen.
 *
 * Ideally a microscope is parcentric and parfocal: change the objective and
 * the same spot stays centred and in focus, so every objective's offset from
 * every other is zero. Often it is not, so the offsets are measured. Choose
 * the reference objective, and every other objective on the turret gets a
 * preset, starting at the ideal offset of (0, 0, 0) or at what the saved
 * calibration holds. Choose a preset and measure it.
 *
 * The card of the chosen preset has three parts. Focus (Z): a z-stack under
 * the reference, then under the target. X/Y: a picture under the reference,
 * then under the target, matched pixel by pixel. Confirmation: the offset as
 * three plain tiles, and Save and adopt, which saves every pair measured in
 * this window together with the reference.
 */

import { cell, choice, note, part, picture, press, publishRow, signed } from "../cells";
import { h } from "../dom";
import { once, stale, type View } from "./index";

type Lens = { slot: number; name: string };
type Preset = { target: number; t: { x: number | null; y: number | null; z: number | null }; state: "default" | "published" | "measured";
  focus: Record<string, any>; xy: Record<string, any> };

const lensLabel = (l: Lens | undefined) => (l ? `slot ${l.slot} · ${l.name}` : "—");
const offsetText = (t: Preset["t"]) => `(${signed(t.x)}, ${signed(t.y)}, ${signed(t.z)}) µm`;
const STATE = { default: "not measured", published: "held", measured: "measured" };

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
  const bySlot = (slot: number | null) => lenses.find((l) => l.slot === slot);
  if (!("reference" in s)) s.reference = s.loaded.reference ?? null;
  /* One preset per other objective, starting from what the saved calibration
     holds (relative to its reference), or from zero. */
  if (!s.presets && s.reference !== null) {
    s.presets = {} as Record<string, Preset>;
    const fromSaved = s.loaded.reference === s.reference;
    for (const l of lenses) {
      if (l.slot === s.reference) continue;
      const kept = fromSaved ? s.loaded.held[String(l.slot)] : undefined;
      s.presets[String(l.slot)] = {
        target: l.slot,
        t: kept ? { x: kept[0], y: kept[1], z: kept[2] } : { x: 0, y: 0, z: 0 },
        state: kept ? "published" : "default",
        focus: {},
        xy: {},
      };
    }
  }
  const reference = bySlot(s.reference);
  const warning = stale(ctx);
  if (warning) presetsCell.body.append(note(warning, "bad"));

  if (!lenses.length) presetsCell.body.append(note("No objectives listed.", "bad"));
  else {
    const select = choice([["", "choose…"], ...lenses.map((l): [string, string] => [String(l.slot), lensLabel(l)])],
      reference ? String(reference.slot) : "", "reference objective");
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
  const presets = (s.presets ?? {}) as Record<string, Preset>;
  const slots = Object.keys(presets);
  if (slots.length) {
    const list = h("div", { class: "setup-sets" });
    for (const slot of slots) {
      const p = presets[slot];
      const b = h("button", {
        type: "button",
        class: "setup-set" + (slot === s.current ? " chosen" : "") + (p.state === "default" ? " default" : ""),
      }, h("b", {}, `${reference?.name ?? "reference"} vs ${bySlot(p.target)?.name ?? `slot ${slot}`}`),
        h("span", { class: "setup-set-pair" }, offsetText(p.t)), h("span", { class: "setup-set-state" }, STATE[p.state]));
      b.addEventListener("click", () => { s.current = slot === s.current ? null : slot; ctx.refresh(); });
      list.append(b);
    }
    presetsCell.body.append(list);
  } else if (reference) presetsCell.body.append(note("No other objective on the turret.", "bad"));

  /* ---- the chosen preset: focus, X/Y, and the confirmation ----------------- */
  const current = s.current ? presets[s.current] : null;
  if (!current || !reference) return;
  const target = bySlot(current.target)!;
  const pair = { reference: reference.slot, target: target.slot };
  const work = cell(`Calibration — ${reference.name} vs ${target.name}`);
  host.append(work.box);

  const measure = (action: "focus" | "xy", side: "reference" | "target", lens: Lens, into: Record<string, any>, busy: string) =>
    press(`Measure ${side} (${lens.name})`, async () => {
      try {
        into[side] = await ctx.call(action, { ...pair, side });
        if (action === "xy" && side === "target") {
          const t = into.target.translation_um;
          current.t = { x: t.x, y: t.y, z: t.z };
          current.state = into.target.trusted ? "measured" : current.state;
        }
      } catch (why) { into[side] = { failed: (why as Error).message }; }
      ctx.refresh();
    }, { busy });

  const focus = part(work.body, "Focus (Z)", { number: 1, prose:
    `With the ${reference.name}, focus using Z-wide, keep the Z-galvo at 0, and set up a z-stack in LAS X that brackets the sharp plane; measure. `
    + `Switch only to the ${target.name}, refocus without moving X/Y, and measure again.` });
  for (const [side, lens] of [["reference", reference], ["target", target]] as const) {
    focus.append(h("div", { class: "setup-row setup-left" }, measure("focus", side, lens, current.focus, "measuring…")));
    const v = current.focus[side];
    if (v?.failed) focus.append(note(`Failed — ${v.failed}`, "bad"));
    else if (v) {
      focus.append(note(`${lensLabel({ slot: v.lens, name: v.name })} · peak z ${Number(v.peak_z_um).toFixed(2)} µm`
        + (side === "target" && v.translation_z_um !== null ? ` · Z ${signed(v.translation_z_um)} µm` : ""), "ok"));
      if (v.image) focus.append(picture(v.image, `${side} focus curve and sharpest slice`));
    }
  }

  const xy = part(work.body, "X/Y", { number: 2, prose:
    `Switch back to the ${reference.name}, remove the z-stack, keep the Z-galvo at 0, and measure. `
    + `Then switch to the ${target.name} again without moving X/Y, match the pixel size exactly, and measure.` });
  for (const [side, lens] of [["reference", reference], ["target", target]] as const) {
    xy.append(h("div", { class: "setup-row setup-left" }, measure("xy", side, lens, current.xy, "measuring…")));
    const v = current.xy[side];
    if (v?.failed) xy.append(note(`Failed — ${v.failed}`, "bad"));
    else if (v && side === "reference") xy.append(note(`${lensLabel({ slot: v.lens, name: v.name })} · picture taken.`, "ok"));
    else if (v) {
      const t = v.translation_um;
      xy.append(note(`XY (${signed(t.x)}, ${signed(t.y)}) µm · Z ${signed(t.z)} µm · ${v.trusted ? "trusted" : "weak"}`, v.trusted ? "ok" : "bad"));
      if (v.why) xy.append(note(v.why, "bad"));
      if (v.image) xy.append(picture(v.image, "the two objectives overlaid, as acquired and after the correction"));
    }
  }

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
        await ctx.call("publish");
        s.adopted = true;
        await ctx.finish();
      } catch (why) { s.failed = `Adopting failed — ${(why as Error).message}`; }
      ctx.refresh();
    },
  }));
};
