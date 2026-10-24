/**
 * Step 1 — Connect.
 *
 * The same card the ZMART Interface starts with: the microscope, its API, and
 * the folder this microscope saves its pictures in. The settings themselves
 * (limits, orientation, calibration, origin) always live in this computer's
 * configuration folder, C:\ProgramData\zmart-microscopy, so that is shown
 * among the checks rather than asked for.
 */

import { cardRow, cell, choice, field, note, press } from "../cells";
import { h } from "../dom";
import { once, type View } from "./index";

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
  s.folder ??= card.data_folder;

  const { box, body } = cell("Connect to the microscope");
  const microscope = choice([["stellaris", card.microscope]], "stellaris", "microscope");
  const api = choice([["cam", card.api]], "cam", "API");
  microscope.disabled = api.disabled = true;
  const folder = field({ type: "text", value: s.folder, spellcheck: "false", "aria-label": "Data folder" });
  folder.disabled = connected;
  folder.addEventListener("input", () => { s.folder = folder.value; });
  body.append(cardRow("Microscope", microscope), cardRow("API", api), cardRow("Data folder", folder));
  if (!connected) {
    body.append(note(card.data_folder_saved
      ? "Where this microscope saves its pictures. Change it only if it should move."
      : "Where this microscope will save its pictures: a folder on a local disk. Connect saves it."));
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
        const r = await ctx.call("connect", { data_folder: s.folder });
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
