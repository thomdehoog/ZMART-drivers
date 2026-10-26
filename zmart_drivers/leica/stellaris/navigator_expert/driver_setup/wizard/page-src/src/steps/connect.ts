/**
 * Step 1 — Connect.
 *
 * The same card the ZMART Interface starts with: the ZMART Controller's seven
 * connection words. The microscope and its API belong to this driver and are
 * only shown; host, client and password can be typed, filled as the driver
 * connects. Below them, the config and its output folder (where this
 * microscope saves its pictures). A config is a folder of settings (limits,
 * origin, orientation, calibration) on this computer, named by when it was
 * made: a new one, or one kept here, chosen from the most recent down.
 */

import { cardRow, cell, choice, field, note, press } from "../cells";
import { h } from "../dom";
import { once, type View } from "./index";

type Typed = [key: string, label: string, type: string];

/** The words that reach the microscope, typed under its microscope and API. */
const REACHING: Typed[] = [
  ["host", "Host", "text"],
  ["client", "Client", "text"],
  ["password", "Password", "password"],
];

/** The output folder, typed under the config in a box of their own. */
const FOLDER: Typed = ["output_root", "Output folder", "text"];

const TYPED: Typed[] = [...REACHING, FOLDER];

/** The Config drop-down's choice that makes a new config, named by when it is made. */
const NEW_CONFIG = "\u0000new";

export const connect: View = (host, ctx) => {
  const s = ctx.state;
  if (!once(ctx, "card", "card")) {
    const { box, body } = cell("Connect to the microscope");
    body.append(note("Reading…"));
    host.append(box);
    return;
  }
  const card = s.card;
  const connected = Boolean(card.connected);
  // What is typed is kept while the step is shown; the password is never filled in.
  s.typed ??= Object.fromEntries(TYPED.map(([key]) => [key, key === "password" ? "" : card[key] ?? ""]));
  // Another config was chosen: its output folder, the rest as typed.
  if (s.refill) { s.typed.output_root = card.output_root ?? ""; s.refill = false; }

  const typedRow = ([key, label, type]: Typed) => {
    const input = field({ type, value: s.typed[key], spellcheck: "false", autocomplete: "off", "aria-label": label });
    input.disabled = connected;
    input.addEventListener("input", () => { s.typed[key] = input.value; });
    return cardRow(label, input);
  };

  const reach = cell("Connect to the microscope");
  const microscope = choice([[card.microscope, card.microscope]], card.microscope, "Microscope");
  const api = choice([[card.api_type, card.api_type]], card.api_type, "API");
  microscope.disabled = api.disabled = true;
  reach.body.append(cardRow("Microscope", microscope), cardRow("API", api), ...REACHING.map(typedRow));
  host.append(reach.box);

  /* The button acts on both boxes, so it stands at the end of the second. */
  const { box, body } = cell("Driver configuration");
  /* The config: a new one first, then every config folder kept on this
     computer, the most recent first. Choosing one opens it, and the rail
     shows what is done in it; a new one has nothing done, and its folder is
     made on Connect. */
  const configs: string[] = card.configs ?? [];
  const isNew = Boolean(card.config) && !configs.includes(card.config);
  const config = choice(
    [[NEW_CONFIG, "New config…"], ...configs.map((name): [string, string] => [name, name])],
    isNew || !card.config ? NEW_CONFIG : card.config, "Config",
  );
  config.disabled = connected;
  config.addEventListener("change", async () => {
    s.failed = null;
    try {
      await ctx.call("choose", { config: config.value === NEW_CONFIG ? "" : config.value });
      // Read the card again for that config: the output folder it saved, if it saved one.
      delete s.card;
      delete s["card:asked"];
      s.refill = true;
      await ctx.reload();
    } catch (why) { s.failed = (why as Error).message; }
    ctx.refresh();
  });
  body.append(cardRow("Config", config));
  if (isNew && !connected) body.append(note(`A new config, ${card.config}, is made when you connect.`));
  body.append(typedRow(FOLDER));
  if (!connected) {
    body.append(note(card.output_root_saved
      ? "The output folder is where this microscope saves its pictures. Change it only if it should move."
      : "The output folder is where this microscope will save its pictures: a folder on a local disk. Connect saves it."));
  }
  if (s.failed) body.append(note(s.failed, "bad"));

  if (connected) {
    const lamp = h("span", { class: "session-state" }, h("span", { class: "lamp" }), "Connected");
    const off = press("Disconnect", async () => {
      try {
        await ctx.call("disconnect");
        card.connected = false;
        card.checks = [];
        ctx.unfinish();
      } catch (why) { s.failed = (why as Error).message; }
      ctx.refresh();
    }, { busy: "disconnecting…", kind: "danger" });
    body.append(h("div", { class: "setup-connected" }, lamp, off));
  } else {
    body.append(h("div", { class: "setup-publish" }, press("Connect", async () => {
      s.failed = null;
      try {
        const r = await ctx.call("connect", { ...s.typed });
        card.connected = true;
        card.checks = r.checks;
        await ctx.finish();
      } catch (why) { s.failed = (why as Error).message; }
      ctx.refresh();
    }, { busy: "connecting…" })));
  }
  host.append(box);

  if (connected && card.checks?.length) {
    const checks = cell("Connection checks");
    const list = h("div", { class: "check-list" });
    for (const [name, value] of card.checks as [string, string][]) {
      list.append(h("div", { class: "check-row" },
        h("span", { class: "check-mark" }, "✓"), h("span", { class: "check-name" }, name), h("span", { class: "check-value" }, value)));
    }
    checks.body.append(list);
    host.append(checks.box);
  }
};
