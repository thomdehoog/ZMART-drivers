# The vendor contract: what a microscope hands to the dispatcher

The dispatcher is the shared machinery that runs every reading and every change
of a ZMART driver: it asks the limits first, waits by one rule, sends again
when that is safe, and confirms what it sent. It knows no microscope. Each
microscope's vendor part hands it six things, and this page lists them. The
dispatcher is today in the Leica driver,
`zmart_drivers/leica/stellaris5_y42h93/navigator_expert/dispatcher/`, and
[the driver anatomy](driver-anatomy.md) says where it sits among the other
parts.

A small, complete example is the pretend vendor in
`navigator_expert/testing/helpers/pretend_vendor.py`: a made-up microscope with
one stage axis, plugged into the same dispatcher, with tests in
`testing/unit/test_pretend_vendor.py`. It imports nothing but the dispatcher.
The Leica vendor part is the full-size example; where each piece lives there is
named below.

## 1. A transport

How a request reaches the microscope and how its reply is awaited. One object
with five methods. Leica: `operations/sending/transport.py`.

| Method | What it does | Answers |
|---|---|---|
| `fill(request, setup_fn)` | Writes the command's values into the request, by calling `setup_fn` with whatever part of the request holds them. | nothing |
| `clear_reply(client)` | Empties the microscope's reply, so that what is read after the send is the new answer. | nothing |
| `send(client, request, description, *, asynchronous)` | Sends the request. With `asynchronous`, it does not wait for the microscope to say it received it. | `(delivered, notes)`: whether it arrived, and a list of short texts to keep in the log (for LAS X, what the driver answered to dialog boxes) |
| `await_reply(client)` | Waits for the microscope to fill in its reply. | `True` if it did, `False` if the time ran out |
| `dialog_warning_since(started_at, description)` | Says whether a dialog box of the vendor software opened since the command began. Only asked when a command failed or stayed unconfirmed, as a clue. | a text, or `None` |

### More than one way to send

A microscope may be reached more than one way (for LAS X, the CAM API and LAS X's own
hardware server). Each way is a transport as above. The dispatcher's `TransportChain`
(`dispatcher/transport_chain.py`) holds an ordered list of them behind the one
`transport` a profile names, and the vendor decides the order. Leica:
`operations/sending/routes.py` (`ROUTES`, `WRITE_ROUTES`; the order can be
set with the environment variable `ZMART_WRITE_ROUTES`).

The rule that keeps a command from being done twice: a route's `send` raises
`TransportUnavailable` only while **nothing has left yet** (the connection is not
there, or the route has no form for this command). The chain then tries the next
route, and the log says so. Anything else, such as a send that went out but was not
acknowledged, is never a reason to use another route, because the command may already
have been carried out. A transport that does not need a fallback needs no chain: the
profile can name it directly.

## 2. Command profiles

How the dispatcher treats one command. A frozen dataclass (the dispatcher
changes a field with `dataclasses.replace`), with these fields. Leica:
`CommandProfile` in `operations/sending/command_profile.py`, one beside each change in `operations/changes/`.

| Field | Meaning |
|---|---|
| `transport` | The transport above. |
| `waiting` | The waiting numbers (part 5). |
| `max_retries` | How many times a temporary failure is sent again. |
| `max_confirm_attempts` | How many confirmation windows the change gets. |
| `refire_on_unconfirmed` | Whether the command is sent again between two windows. |
| `retry_backoff`, `retry_escalate` | A pause before sending again (seconds, or `None`), and whether it doubles each time. |
| `skip_reply_check` | Do not wait for the reply; the confirmation is the proof. |
| `send_without_receipt` | Send without waiting for the microscope to say it received it. |
| `success_on_unconfirmed` | What an unconfirmed change answers: `True` keeps the workflow going, with the change recorded as unconfirmed. |
| `never_resend` | The command reaches the microscope once and never again, such as the start of an acquisition. The dispatcher enforces this whatever the other fields say. |
| `limits_setup_hint` | Optional: where the operator creates the limits files, said when no limits are installed. |

The four functions of one change are handed to `door.write` with each call, so
that they can carry the call's target:

| Function | Answers |
|---|---|
| `setup_fn(part_of_request)` | nothing; it writes the values |
| `pre_check_fn()` | `{"success": bool, "logs": [...]}`; for example, is the scanner idle |
| `error_check_fn()` | `{"success": bool, "error": text, "transient": bool, "logs": [...]}` |
| `confirm_fn()` | `{"success": bool, "logs": [...]}`, plus anything worth showing when it stays unconfirmed |

## 3. Reading sources

How a reading is answered. One object (Leica: the module
`operations/readings/sources.py`) with:

| Name | What it is |
|---|---|
| `waiting` | The waiting numbers (part 5). |
| `UnsupportedSource` | The error class for a source a reading does not have. |
| `spec(datum)` | The vendor's description of one reading, such as `"x"` or `"selected_job"`. The dispatcher does not look inside it. |
| `mode_for(datum)` | The family the vendor's policy reads this datum from, such as `"direct"`. |
| `legs(spec, datum, mode)` | The sources that take part, in the order they are asked; or a `Reading` carrying `UnsupportedSource`. |
| `first_stage(spec, mode, fallback)` | `(watch, rest_mode)`: a source tried alone before the others, or `None`, and the family that answers if it cannot. `watch(succeeded=, deadline=, observed_after=, kwargs=, waiting=)` answers a `Reading` or `None`. |
| `success(spec, value, about)` | Whether `value` counts as an answer. `about` is what a reading of one item is about (for LAS X, the name of a job), or `None`. |
| `ask(leg, spec, client, *, until, answer_counts, about, source_kwargs)` | Asks one source, again and again until `answer_counts(reading)` or the moment `until`; answers the last `Reading`. Turns on a shared channel, such as LAS X's CAM API, are kept here. |

A `Reading` (from `dispatcher/reader.py`) holds the value, the source that gave
it, when it was seen (`time.time()`), its age, and the error when the source
could not answer.

## 4. Error sorting

The dispatcher never reads an error message. The vendor's `error_check_fn`
reads the microscope's reply and says two things: whether the command failed,
and whether the failure is temporary (`"transient": True`), so that sending
again is worthwhile. Anything the vendor does not recognise should count as
permanent. Leica: `classify_error` in `operations/sending/error_classification.py`.

## 5. Waiting numbers

Every wait follows one rule: a number of windows of a set length, sending again
between them where that is safe. Any object with four numbers:

| Name | Leica | Meaning |
|---|---|---|
| `WINDOWS` | 4 | how many windows |
| `WINDOW_S` | 3 | how long one window lasts, in seconds |
| `POLL_S` | 0.1 | how often a source is looked at again, in seconds |
| `ANSWER_POLL_S` | 0.01 | how often a reply already asked for is checked, in seconds |

Leica: `connection/waiting.py`.

## 6. Limits

An object that answers `check_command(command, values)` by raising
`LimitViolation` or `LimitsError` (from `dispatcher/limit_errors.py`) when the
values are not allowed, and by returning when they are. It also answers
`status()` with a `LimitsStatus` (where the limits came from) and `describe()`
with that as a dictionary. The driver installs it for each connection when it
connects, with `gate.install(client, gate.GateState(limits=...))`. Until it is
installed, every change is refused. Leica: `LeicaLimits` in
`operations/changes/limit_rules.py`, installed by
`driver_setup/limits/handshake.py`.

## What comes back

`door.read(sources, datum, client, ...)` answers a `Reading`, or `None` when
nothing counted in time. `door.write(client, request, description, command=,
values=, profile=, ...)` answers `{"success", "confirmed", "message", "timing",
"logs"}`: `confirmed` is `True` when the readback showed the change, `False`
when it never did, and `None` when there was nothing to confirm or the command
failed before it.

## Not yet shared

Three things a second vendor would still write for itself, though they are
the same for every microscope: the shape of the command profile (part 2), the
list of kinds of problem and the rule for each, and the running log. They move
into the dispatcher when it becomes a package shared by all drivers.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com)
