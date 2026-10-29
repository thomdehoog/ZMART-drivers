/**
 * The cells every step is made of, as in the ZMART driver configuration
 * workflow of the ZMART Microscopy operator page.
 *
 * A notebook is a column of cells, and each cell is three things: a sentence
 * saying what you are about to do, the control to do it with, and underneath,
 * what came back. These helpers make those three things the same shape on
 * every step, so the column reads as one document rather than five forms.
 *
 * They build elements and nothing else. What a cell says, and what pressing
 * its button does, is the step's.
 */

import { h } from "./dom";

/** A cell: a small heading above a white card, the sentence first inside it. */
export function cell(title: string, prose: string | null = null) {
  const body = h("div", { class: "side-group-body" });
  if (prose) body.append(h("p", { class: "side-note" }, prose));
  const box = h("div", { class: "side-group setup-cell" }, h("div", { class: "side-group-title" }, title), body);
  return { box, body };
}

/** A press. `busy` is the wording while the work is out with the instrument. */
export function press(
  label: string,
  onPress: () => Promise<void> | void,
  { busy = "working…", disabled = false, kind = "" }: { busy?: string; disabled?: boolean; kind?: string } = {},
) {
  const b = h("button", { class: kind ? `run ${kind}` : "run", type: "button" }, label);
  b.disabled = disabled;
  b.addEventListener("click", async () => {
    b.disabled = true;
    b.textContent = busy;
    document.body.classList.add("busy");
    try {
      await onPress();
    } finally {
      document.body.classList.remove("busy");
      b.textContent = label;
      b.disabled = disabled;
    }
  });
  return b;
}

/** What came back, as pairs: a word on the left, its value on the right. */
export function readout(pairs: [string, string][]) {
  const dl = h("dl", { class: "setup-readout" });
  for (const [k, v] of pairs) dl.append(h("dt", {}, k), h("dd", {}, v));
  return dl;
}

/** A sentence in the quieter voice, for a note, a warning ("bad") or a result ("ok"). */
export function note(text: string, kind: "" | "ok" | "bad" = "") {
  return h("p", { class: kind ? `setup-note ${kind}` : "setup-note" }, text);
}

/** A number in a sentence: a few decimals, never a float's tail. */
export const signed = (v: unknown, d = 2) =>
  v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : `${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(d)}`;

/** The step's own footer: its Save and adopt press, and the sentence beside it once saved. */
export function publishRow({ label, onPublish, published, disabled = false }:
  { label: string; onPublish: () => Promise<void>; published?: string | null; disabled?: boolean }) {
  const row = h("div", { class: "setup-publish" }, press(label, onPublish, { busy: "saving…", disabled }));
  if (published) row.append(note(published, "ok"));
  return row;
}

/** The picture a measurement drew of itself, as the notebook shows it. */
export function picture(url: string, alt: string) {
  return h("img", { class: "setup-picture", src: url, alt });
}

/** A part inside a card: a tinted sub-box with a heading, numbered when the card holds several in turn. */
export function part(host: HTMLElement, title: string, { number = null, prose = null }: { number?: number | null; prose?: string | null } = {}) {
  const head = h("div", { class: "setup-part-title" });
  if (number !== null) head.append(h("span", { class: "setup-part-number" }, String(number)));
  head.append(h("span", {}, title));
  const box = h("div", { class: "setup-part" }, head);
  if (prose) box.append(h("p", { class: "side-note" }, prose));
  host.append(box);
  return box;
}

/** A row of the form: what it is on the left, the fields on the right. */
export function row(label: string, ...fields: (Node | string | null)[]) {
  return h("div", { class: "setup-row" }, h("label", {}, label), ...fields);
}

/** A row of the Connect card: a narrow label column, the field filling the rest. */
export function cardRow(label: string, input: Node) {
  return h("div", { class: "setup-card-row" }, h("label", {}, label), input);
}

/** A field, the way the interface draws its fields. */
export function field(props: Record<string, unknown>, extra = "") {
  return h("input", { class: `setup-field ${extra}`.trim(), ...props }) as HTMLInputElement;
}

/** A drop-down of *options* ([value, words]), *chosen* selected. */
export function choice(options: [string, string][], chosen: string | null, label: string, extra = "") {
  const select = h("select", { class: `setup-field ${extra}`.trim(), "aria-label": label }) as HTMLSelectElement;
  for (const [value, words] of options) {
    const o = h("option", { value }, words) as HTMLOptionElement;
    o.selected = value === chosen;
    select.append(o);
  }
  return select;
}
