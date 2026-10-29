/**
 * Step 5 — Objective calibration: the reference objective, its presets, and
 * the measurement of the one chosen.
 *
 * Ideally a microscope is parcentric and parfocal: change the objective and
 * the same spot stays centred and in focus, so every objective's offset from
 * every other is zero. Often it is not, so the offsets are measured. Choose
 * the reference objective, and every other objective on the nosepiece gets a
 * preset, starting at the ideal offset of (0, 0, 0) or at what the saved
 * calibration holds. Choose a preset and measure it.
 *
 * On this microscope the measurement is made by the operator's eye, as in
 * the notebook. Under the reference, centre a small feature and focus on it,
 * then Record. Turn to the target (the driver turns the nosepiece and moves
 * nothing else), centre and focus the same feature again, and Record. The
 * difference between the two places the stage stood is the target's offset,
 * shown as three plain tiles; Save and adopt saves every pair measured in
 * this window together with the reference.
 */

import { cell, choice, note, part, press, publishRow, signed } from "../cells";
import { h } from "../dom";
import { once, stale, type View } from "./index";

type Lens = { slot: number; name: string };
type Preset = { target: number; t: { x: number | null; y: number | null; z: number | null }; state: "default" | "published" | "measured";
  recorded: Record<string, any> };

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
  if (!("inPlace" in s)) s.inPlace = s.loaded.current ?? null;
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
        recorded: {},
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
  } else if (reference) presetsCell.body.append(note("No other objective on the nosepiece.", "bad"));

  /* ---- the chosen preset: under the reference, under the target, confirmation -- */
  const current = s.current ? presets[s.current] : null;
  if (!current || !reference) return;
  const target = bySlot(current.target)!;
  const pair = { reference: reference.slot, target: target.slot };
  const work = cell(`Calibration — ${reference.name} vs ${target.name}`);
  host.append(work.box);

  const turn = (lens: Lens) =>
    press(s.inPlace === lens.slot ? `${lens.name} is in place` : `Turn to ${lens.name}`, async () => {
      s.turnFailed = null;
      try {
        await ctx.call("turn", { slot: lens.slot });
        s.inPlace = lens.slot;
      } catch (why) { s.turnFailed = (why as Error).message; }
      ctx.refresh();
    }, { busy: "turning…", disabled: s.inPlace === lens.slot });

  const record = (side: "reference" | "target", lens: Lens) =>
    press(`Record under ${lens.name}`, async () => {
      try {
        const r = await ctx.call("record", { ...pair, side });
        current.recorded[side] = r;
        if (r.translation_um) {
          current.t = { x: r.translation_um.x, y: r.translation_um.y, z: r.translation_um.z };
          current.state = "measured";
        }
      } catch (why) { current.recorded[side] = { failed: (why as Error).message }; }
      ctx.refresh();
    }, { busy: "reading…" });

  const shown = (side: "reference" | "target", into: HTMLElement) => {
    const v = current.recorded[side];
    if (v?.failed) into.append(note(`Failed — ${v.failed}`, "bad"));
    else if (v) into.append(note(`Recorded: x ${Number(v.x_um).toFixed(1)} · y ${Number(v.y_um).toFixed(1)} · z ${Number(v.z_um).toFixed(2)} µm`, "ok"));
  };

  const under = part(work.body, `Under ${reference.name}`, { number: 1, prose:
    "Centre a small, easily recognised feature in the picture and bring it into focus, with the joystick or in NIS-Elements. Then record." });
  under.append(h("div", { class: "setup-row setup-left" }, turn(reference), record("reference", reference)));
  shown("reference", under);

  const then = part(work.body, `Under ${target.name}`, { number: 2, prose:
    `Turn to the ${target.name}; the driver turns the nosepiece and moves nothing else. `
    + "Centre and focus the same feature again, then record." });
  then.append(h("div", { class: "setup-row setup-left" }, turn(target), record("target", target)));
  shown("target", then);
  if (s.turnFailed) work.body.append(note(`Turning failed — ${s.turnFailed}`, "bad"));

  const confirm = part(work.body, "Confirmation", { number: 3, prose:
    `${target.name} relative to ${reference.name}: where the stage stood under the ${target.name}, minus where it stood under the ${reference.name}. `
    + "Save and adopt saves the reference and every pair measured here; an objective not measured keeps what the saved calibration holds for it." });
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
        await ctx.call("publish", { reference: reference.slot });
        s.adopted = true;
        await ctx.finish();
      } catch (why) { s.failed = `Adopting failed — ${(why as Error).message}`; }
      ctx.refresh();
    },
  }));
};
