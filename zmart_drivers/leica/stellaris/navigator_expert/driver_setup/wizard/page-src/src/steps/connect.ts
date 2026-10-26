/**
 * Step 1 — Connect.
 *
 * The same card the ZMART Interface starts with: the ZMART Controller's seven
 * connection words. The microscope and its API belong to this driver and are
 * only shown; host, client, password, configuration and the output folder
 * (where this microscope saves its pictures) can be typed, filled as the driver
 * connects. A word the driver does not need is simply not used by it. The
 * settings themselves (limits, orientation, calibration, origin) always live
 * in this computer's configuration folder, C:\ProgramData\zmart-microscopy,
 * so that is shown among the checks rather than asked for.
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

const TYPED: Typed[] = [...REACHING, ["config", "Config", "text"], FOLDER];

/** The Config drop-down's choice that makes a new configuration, named in a field below it. */
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
  /* The config is chosen: a new one first, named in the field that appears
     for it, then the previous ones -- this microscope's current setup and
     every one the driver keeps by name. */
  const configs: string[] = card.configs ?? [];
  s.configChoice ??= !s.typed.config ? "" : configs.includes(s.typed.config) ? s.typed.config : NEW_CONFIG;
  const config = choice(
    [[NEW_CONFIG, "New config…"], ["", "Current setup"], ...configs.map((name): [string, string] => [name, name])],
    s.configChoice, "Config",
  );
  config.disabled = connected;
  config.addEventListener("change", () => {
    s.configChoice = config.value;
    s.typed.config = config.value === NEW_CONFIG ? "" : config.value;
    ctx.refresh();
  });
  body.append(cardRow("Config", config));
  if (s.configChoice === NEW_CONFIG) {
    const name = field({ type: "text", value: s.typed.config, spellcheck: "false", autocomplete: "off",
      "aria-label": "New config name", placeholder: "a name for the new config" });
    name.disabled = connected;
    name.addEventListener("input", () => { s.typed.config = name.value; });
    body.append(cardRow("Name", name));
  }
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
