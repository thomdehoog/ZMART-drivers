/**
 * What each step shows in its boxes.
 *
 * A step is a function that builds its boxes with the helpers in `Context`
 * and talks to the driver through `ctx.press`. Every sentence here is for a
 * microscopist setting the microscope up for the first time, so each says
 * what to do and why.
 */

import type { Result } from "./api";
import { h } from "./dom";

/** How one button behaves. */
export interface Press {
  /** The sentence at the foot of the rail while the microscope works. */
  busy: string;
  /** The values sent with the action, read when the button is pressed. */
  values?: () => object;
  /** What the action came to, said beside the button in green. */
  said?: (r: Result) => string | null;
  /** Anything else to do with the answer. */
  then?: (r: Result) => void;
  /** Whether the button may be pressed now; asked again after every action. */
  enabled?: () => boolean;
}

export interface Context {
  /** A button and the sentence beside it, in one row. */
  press(label: string, action: string, how: Press): HTMLElement;
  /** A box: a heading over a white card holding *children*. */
  group(title: string, ...children: (Node | null)[]): HTMLElement;
  /** A sentence inside a box; *warn* writes it in the warning ink. */
  note(text: string, warn?: boolean): HTMLElement;
  /** A label and its field, on one row. */
  field(label: string, input: HTMLInputElement, hint?: string): HTMLElement;
  /** An empty picture, shown once given a source. */
  picture(): HTMLImageElement;
  /** Ask every button again whether it may be pressed. */
  refreshPresses(): void;
  /** Say that the step is finished: its badge turns green and the next step opens. */
  finish(): Promise<void>;
}

export type StepView = (ctx: Context) => HTMLElement[];

const fmt = (n: unknown) => (typeof n === "number" ? n.toFixed(1) : String(n));
const input = (props: Record<string, unknown>) => h("input", props) as HTMLInputElement;
const show = (img: HTMLImageElement, src: unknown) => {
  if (typeof src !== "string") return;
  img.src = src;
  img.hidden = false;
};

// 1 ---------------------------------------------------------------------------
const dataLocation: StepView = (ctx) => {
  const folder = input({ type: "text", value: "D:\\ZMART-data", spellcheck: "false" });
  return [
    ctx.group(
      "Choose the folder",
      ctx.field("Folder", folder, "A full path to a folder that already exists, such as D:\\ZMART-data."),
      ctx.press("Check and save", "save", {
        busy: "Checking the folder…",
        values: () => ({ folder: folder.value }),
        said: () => "Saved. Every run of this microscope will go under this folder.",
        then: () => ctx.finish(),
      }),
    ),
  ];
};

// 2 ---------------------------------------------------------------------------
const limits: StepView = (ctx) => {
  const recorded = new Set<number>();
  const corners = [1, 2, 3, 4].map((n) =>
    ctx.press(`Record corner ${n}`, "corner", {
      busy: `Reading where the stage stands, for corner ${n}…`,
      values: () => ({ corner: n }),
      said: (r) => {
        const p = r.position as { x_um: number; y_um: number };
        return `X ${fmt(p.x_um)} µm, Y ${fmt(p.y_um)} µm`;
      },
      then: () => {
        recorded.add(n);
        ctx.refreshPresses();
      },
    }),
  );
  return [
    ctx.group(
      "Record the four corners",
      ctx.note("Use the joystick, or LAS X, to drive the stage to a corner of the area it may safely reach, then press that corner's button. Do this for all four corners, in any order. Recording a corner again replaces it."),
      ...corners,
    ),
    ctx.group(
      "Save",
      ctx.press("Save the limits", "save", {
        busy: "Checking and saving the limits…",
        enabled: () => recorded.size === 4,
        said: (r) => {
          const [x, y] = [r.x_um as number[], r.y_um as number[]];
          return `Saved. The stage may travel in X from ${fmt(x[0])} to ${fmt(x[1])} µm, and in Y from ${fmt(y[0])} to ${fmt(y[1])} µm.`;
        },
        then: () => ctx.finish(),
      }),
    ),
  ];
};

// 3 ---------------------------------------------------------------------------
const orientation: StepView = (ctx) => {
  const move = input({ type: "number", value: 40, min: 1 });
  let measured = false;
  let checked = false;
  const found = ctx.picture();
  const check = ctx.picture();
  return [
    ctx.group(
      "Measure",
      ctx.note("In LAS X, select the Navigator Expert job and put a field with clearly recognisable structure under the objective. The wizard takes three pictures, at the starting spot and after a small move in X and in Y, and works out how the image is turned."),
      ctx.field("Stage move (µm)", move, "How far the stage moves between the pictures."),
      ctx.press("Measure the orientation", "measure", {
        busy: "Taking three pictures and comparing them…",
        values: () => ({ move_um: Number(move.value) }),
        said: (r) => `Found: ${r.label}${r.mirrored ? " (the image is also mirrored)" : ""}.`,
        then: (r) => {
          measured = true;
          checked = false;
          show(found, r.image);
          ctx.refreshPresses();
        },
      }),
      found,
    ),
    ctx.group(
      "Check",
      ctx.note("One more picture, taken with the orientation just measured. It should follow the stage: moving the stage right moves the picture right."),
      ctx.press("Take a check picture", "check", {
        busy: "Taking the check picture…",
        enabled: () => measured,
        said: () => "Does this picture follow the stage? If it does, save the orientation.",
        then: (r) => {
          checked = true;
          show(check, r.image);
          ctx.refreshPresses();
        },
      }),
      check,
    ),
    ctx.group(
      "Save",
      ctx.press("Save the orientation", "save", {
        busy: "Saving the orientation…",
        enabled: () => measured && checked,
        said: () => "Saved.",
        then: () => ctx.finish(),
      }),
    ),
  ];
};

// 4 ---------------------------------------------------------------------------
const PARTS: [string, string, string][] = [
  ["focus_reference", "Reference focus", "With the reference objective, focus using Z-wide and keep the Z-galvo at 0. In LAS X turn Set Focus off and set up a manual z-stack that clearly brackets the sharp plane (for example 80 µm in 1 µm steps)."],
  ["focus_target", "Target focus", "Switch only to the target objective. Match the reference pixel size, and do not change the job, Z-wide or the Z-galvo."],
  ["xy_reference", "Reference position", "Switch back to the reference objective and remove the z-stack. Keep the Z-galvo at 0 and use the same job and pixel size."],
  ["xy_target", "Target position", "Switch to the target objective again. Do not move the motorised X/Y, and match the reference pixel size exactly."],
];

const calibration: StepView = (ctx) => {
  const name = input({ type: "text", value: "10x-20x", spellcheck: "false" });
  const slot = input({ type: "number", value: 1, min: 1 });
  let started = false;
  const measuredParts = new Set<string>();
  const parts = PARTS.map(([part, title, text], i) => {
    const box = ctx.group(
      `${i + 1}. ${title}`,
      ctx.note(text),
      ctx.press(`Measure: ${title}`, "measure", {
        busy: `Measuring the ${title.toLowerCase()}…`,
        enabled: () => started,
        values: () => ({ part }),
        said: () => `${title} done.`,
        then: () => {
          measuredParts.add(part);
          ctx.refreshPresses();
        },
      }),
    );
    box.hidden = true;
    return box;
  });
  return [
    ctx.group(
      "The pair",
      ctx.note("This step measures one pair of objectives: a reference objective and a target. In LAS X, select the job you will use and keep it for the whole run; between measurements you change only the objective."),
      ctx.field("Name of the pair", name, "For example 10x-20x."),
      ctx.field("Reference slot", slot, "The turret slot of the objective every other one is measured against. Keep it the same for every pair."),
      ctx.press("Start", "start", {
        busy: "Starting the calibration…",
        values: () => ({ pair_name: name.value, reference_slot: Number(slot.value) }),
        said: () => "Started. Measure the four parts below, in order.",
        then: () => {
          started = true;
          for (const p of parts) p.hidden = false;
          ctx.refreshPresses();
        },
      }),
    ),
    ...parts,
    ctx.group(
      "Save",
      ctx.press("Save the calibration", "save", {
        busy: "Saving the calibration…",
        enabled: () => measuredParts.has("xy_target"),
        said: () => "Saved. To calibrate another pair, open this step again.",
        then: () => ctx.finish(),
      }),
    ),
  ];
};

// 5 ---------------------------------------------------------------------------
const origin: StepView = (ctx) => [
  ctx.group(
    "Set the zero point",
    ctx.note("When the stage stands on that spot and the sample is in focus, press the button. The wizard remembers the spot, the focus and the objective that is in place, so that every later session starts from the same zero point."),
    ctx.press("Set the zero point here", "set", {
      busy: "Reading where the stage and the focus stand…",
      said: () => "The zero point is saved. The setup is complete.",
      then: () => ctx.finish(),
    }),
  ),
];

export const VIEWS: Record<string, StepView> = {
  data_location: dataLocation,
  limits,
  orientation,
  calibration,
  origin,
};
