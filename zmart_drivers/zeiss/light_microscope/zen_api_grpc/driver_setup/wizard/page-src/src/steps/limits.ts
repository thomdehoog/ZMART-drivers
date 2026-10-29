/**
 * Step 2 — Define limits: Configure, then Import X/Y stage limits and Import
 * focus limits.
 *
 * **Configure** is the limits document itself, every entry of it: the stage
 * and focus ranges, which objective positions automation may turn to, which
 * ZEN experiments it may run, and how far a z-stack may reach. It is drawn
 * from the limits the driver holds (saved, or its cautious defaults), so
 * nothing in the file is hidden from the operator who is about to save it.
 *
 * **Import X/Y stage limits** and **Import focus limits** are what is read
 * off the instrument: the operator drives to the four corners of the safe
 * area in ZEN and imports each, and the four readings become the X and Y
 * ranges above; then focuses to the lowest and the highest safe point, and
 * the two readings become the focus range. All of them are ZEN's own
 * numbers, before any origin is applied.
 */

import { cell, choice, field, note, press, publishRow } from "../cells";
import { h } from "../dom";
import { once, type View } from "./index";

type Entry = { range?: (number | null)[]; allowed?: (number | string | boolean)[] } | [];

const FOCUS_ENDS: [string, string][] = [["low", "One end"], ["high", "Other end"]];

const CORNERS: [string, string][] = [
  ["top_left", "Top left"], ["top_right", "Top right"], ["bottom_left", "Bottom left"], ["bottom_right", "Bottom right"],
];

/** A range as the file holds it, in words an operator can check at a glance. */
const asRange = (e: Entry) => {
  const r = (e as { range?: (number | null)[] }).range;
  return Array.isArray(r) && r.length === 2 ? `${r[0] ?? "open"} … ${r[1] ?? "open"}` : "not set";
};
const kindOf = (e: Entry) => (Array.isArray(e) ? "none" : e.range ? "range" : e.allowed ? "allowed" : "none");
/** A typed list as the file keeps it: numbers as numbers, unless the entry holds words (experiment names). */
const allowedFrom = (text: string, words: boolean) =>
  text.split(",").map((t) => t.trim()).filter(Boolean).map((t) => (!words && Number.isFinite(Number(t)) ? Number(t) : t));

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
  s.held ??= structuredClone(doc.document) as Record<string, Entry>;
  s.corners ??= {} as Record<string, { x: number; y: number }>;
  s.focusEnds ??= {} as Record<string, number>;
  const held = s.held as Record<string, Entry>;
  const axes = new Map<string, { label: string; unit: string }>(doc.axes.map((a: any) => [a.key, a]));
  const required = new Set<string>(doc.required);
  const allowedOnly = new Set<string>(doc.allowed_only);
  const words = new Set<string>(doc.words);
  const adopted = s.adopted ? "Adopted." : null;

  const adopt = async () => {
    s.failed = null;
    try {
      await ctx.call("publish", { document: held, corners: s.corners, focus: s.focusEnds });
      s.adopted = true;
      await ctx.finish();
    } catch (why) { s.failed = `Adopting failed — ${(why as Error).message}`; s.adopted = false; }
    ctx.refresh();
  };

  /* ---- Configure: every entry of the file ---------------------------------- */
  const edit = cell("Configure", "Tick a limit to apply it. Ranges include both ends.");
  edit.body.append(note(`From ${doc.source === "published" ? "the published limits" : "the defaults"}.`, ""));
  edit.body.lastElementChild!.classList.add("setup-source");

  const labelFor = (key: string) =>
    axes.has(key) ? `${axes.get(key)!.label} (${axes.get(key)!.unit})`
      : doc.settings[key]?.label ?? key.replace(/^set_/, "").replaceAll("_", " ");

  for (const key of Object.keys(held)) {
    const label = labelFor(key);
    const entry = held[key];
    const applied = required.has(key) || kindOf(entry) !== "none";
    /* The stage and focus axes are always ranges, and the objective positions
       and the experiments are always lists; the z-stack ends may be either. */
    const fixedKind = axes.has(key) ? "range" : allowedOnly.has(key) ? "allowed" : null;
    const kind = kindOf(entry) === "none" ? (fixedKind ?? "range") : kindOf(entry);

    const tick = h("input", { type: "checkbox", "aria-label": `apply a limit to ${label}` }) as HTMLInputElement;
    tick.checked = applied;
    tick.disabled = required.has(key);
    if (required.has(key)) tick.title = "always enforced";
    const kindSelect = choice([["range", "range"], ["allowed", "allowed values"]], kind, `how ${label} is limited`, "setup-kind");
    kindSelect.disabled = !applied || fixedKind !== null;
    const fields: HTMLInputElement[] = kind === "allowed"
      ? [field({ type: "text", placeholder: words.has(key) ? "names, e.g. ZMART_Snap, ZMART_ZStack" : "values, e.g. 1, 2", "aria-label": `${label}, allowed values`,
          value: (entry as { allowed?: unknown[] }).allowed?.join(", ") ?? "" })]
      : [0, 1].map((end) => field({
          type: "number", "aria-label": `${label} ${end ? "maximum" : "minimum"}`,
          placeholder: end ? "no maximum" : "no minimum",
          value: (entry as { range?: (number | null)[] }).range?.[end] ?? "",
        }, "setup-number"));
    for (const f of fields) f.disabled = !applied;

    tick.addEventListener("change", () => {
      held[key] = tick.checked ? (kind === "allowed" ? { allowed: [] } : { range: [null, null] }) : [];
      ctx.refresh();
    });
    kindSelect.addEventListener("change", () => {
      held[key] = kindSelect.value === "range" ? { range: [null, null] } : { allowed: [] };
      ctx.refresh();
    });
    if (kind === "allowed") {
      fields[0].addEventListener("change", () => { held[key] = { allowed: allowedFrom(fields[0].value, words.has(key)) }; });
    } else {
      fields.forEach((f, end) => f.addEventListener("change", () => {
        const range = [...((held[key] as { range?: (number | null)[] }).range ?? [null, null])];
        range[end] = f.value === "" ? null : Number(f.value);
        held[key] = { range };
      }));
    }
    edit.body.append(h("div", { class: "setup-row setup-limit" }, tick, h("label", {}, label), kindSelect, ...fields));
    if (doc.settings[key]?.note) edit.body.append(note(doc.settings[key].note));
  }
  if (s.failed) edit.body.append(note(s.failed, "bad"));
  edit.body.append(publishRow({ label: "Save and adopt", onPublish: adopt, published: adopted }));
  host.append(edit.box);

  /* ---- Import X/Y stage limits: the four corners, read from the stage ----- */
  const imp = cell("Import X/Y stage limits",
    "Drive to each corner of the safe area in ZEN, then import it. The four corners set X and Y above.");
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
            held[doc.measured[0]] = { range: [Math.min(...xs), Math.max(...xs)] };
            held[doc.measured[1]] = { range: [Math.min(...ys), Math.max(...ys)] };
          }
        } catch (why) { s.importFailed = (why as Error).message; }
        ctx.refresh();
      }, { busy: "reading…" })));
  }
  if (s.importFailed) imp.body.append(note(`Could not read the stage — ${s.importFailed}`, "bad"));
  const allFour = CORNERS.every(([k]) => s.corners[k]);
  if (allFour) {
    imp.body.append(note(`X ${asRange(held[doc.measured[0]])} · Y ${asRange(held[doc.measured[1]])} set above.`, "ok"));
  }
  imp.body.append(publishRow({ label: "Save and adopt", onPublish: adopt, published: adopted, disabled: !allFour }));
  host.append(imp.box);

  /* ---- Import focus limits: the two ends of the focus, read from ZEN ------- */
  const foc = cell("Import focus limits",
    "Focus to one end of the travel that is safe for the sample and the objective, and import it; "
    + "then to the other end, and import that. The two ends set the focus range above, whichever way round.");
  for (const [end, label] of FOCUS_ENDS) {
    const got = s.focusEnds[end];
    foc.body.append(h("div", { class: "setup-row" },
      h("label", {}, label),
      h("span", { class: "setup-note" }, got === undefined ? "not yet" : `z ${Number(got).toFixed(1)} µm`),
      press(got === undefined ? "Import" : "Update", async () => {
        s.focusFailed = null;
        try {
          const r = await ctx.call("focus_end", { end });
          s.focusEnds[end] = Number(r.z);
          if (FOCUS_ENDS.every(([e]) => s.focusEnds[e] !== undefined)) {
            const ends = FOCUS_ENDS.map(([e]) => s.focusEnds[e]);
            held[doc.focus] = { range: [Math.min(...ends), Math.max(...ends)] };
          }
        } catch (why) { s.focusFailed = (why as Error).message; }
        ctx.refresh();
      }, { busy: "reading…" })));
  }
  if (s.focusFailed) foc.body.append(note(`Could not read the focus — ${s.focusFailed}`, "bad"));
  const bothEnds = FOCUS_ENDS.every(([e]) => s.focusEnds[e] !== undefined);
  if (bothEnds) foc.body.append(note(`Focus ${asRange(held[doc.focus])} set above.`, "ok"));
  foc.body.append(publishRow({ label: "Save and adopt", onPublish: adopt, published: adopted, disabled: !bothEnds }));
  host.append(foc.box);
};
