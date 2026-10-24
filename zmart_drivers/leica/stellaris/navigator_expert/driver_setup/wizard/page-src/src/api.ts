/** What the page and the wizard's server say to each other. */

export interface StepStatus {
  done: boolean;
  /** A sentence about what is set up on this computer. */
  note?: string | null;
  /** When it was saved, as an ISO time; used to see what has changed since. */
  saved_at?: string | null;
}

export interface StepInfo {
  id: string;
  title: string;
  purpose: string;
  /** The earlier steps this one was measured on top of. */
  needs: string[];
  status: StepStatus;
}

export interface Outline {
  title: string;
  steps: StepInfo[];
}

/** An action's answer: a few named values, and sometimes `image`, the address of a picture to show. */
export type Result = Record<string, unknown> & { image?: string | null };

/** Fetch the steps and what is already set up. */
export async function loadOutline(): Promise<Outline> {
  const reply = await fetch("api/steps");
  return reply.json();
}

/**
 * Ask the driver to do something. Resolves with the result, or throws an Error
 * whose message is the reason in words, ready to show to the operator.
 */
export async function call(step: string, action: string, values: object = {}): Promise<Result> {
  const reply = await fetch(`api/${step}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(values),
  });
  const answer = await reply.json();
  if (!answer.ok) throw new Error(answer.error);
  return answer.result;
}
