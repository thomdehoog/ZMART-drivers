/** The step views, by step id, and what each is handed. */

import type { Result, StepInfo } from "../api";
import { calibration } from "./calibration";
import { connect } from "./connect";
import { limits } from "./limits";
import { origin } from "./origin";
import { orientation } from "./orientation";

export interface Ctx {
  step: StepInfo;
  /** What this step holds in this window; kept when the operator walks away and back. */
  state: Record<string, any>;
  /** Run one of the step's actions on the driver. Throws an Error with the reason in words. */
  call(action: string, values?: object): Promise<Result>;
  /** Draw the step again from what it holds. */
  refresh(): void;
  /** Say that the step is finished: its badge turns green and the next step opens. */
  finish(): Promise<void>;
  /** Say that the step is no longer done (Connect, after Disconnect). */
  unfinish(): void;
  /** Another config was chosen: forget every other step's work in this window and read the rail again. */
  reload(): Promise<void>;
  /** Whether a step this one was measured on was saved again after it. */
  isStale(): boolean;
  /** The titles of the steps saved again after this one. */
  changedUnder(): string[];
}

export type View = (host: HTMLElement, ctx: Ctx) => void;

/**
 * Load something a step needs from the driver once, then draw again.
 * While it is on its way the step shows a quiet "Reading…".
 */
export function once(ctx: Ctx, key: string, action: string, values: object = {}): boolean {
  if (key in ctx.state) return true;
  if (!ctx.state[`${key}:asked`]) {
    ctx.state[`${key}:asked`] = true;
    ctx
      .call(action, values)
      .then((r) => { ctx.state[key] = r; })
      .catch((why: Error) => { ctx.state[key] = { failed: why.message }; })
      .finally(() => ctx.refresh());
  }
  return false;
}

/** The orange sentence of a step measured on a setting saved again since. */
export function stale(ctx: Ctx): string | null {
  if (!ctx.isStale()) return null;
  return `The ${ctx.changedUnder().join(" and ").toLowerCase()} was saved again after this step. Do this step again, so that it matches.`;
}

export const VIEWS: Record<string, View> = { connect, limits, origin, orientation, calibration };
