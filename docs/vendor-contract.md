# The vendor contract: what a new microscope brings

The dispatcher runs every reading and every write of a ZMART driver. It knows
no microscope. A new microscope brings its primitive classes, and one profile
that the dispatcher's `run()` is handed. This page lists exactly what the
dispatcher asks of them. [The driver anatomy](driver-anatomy.md) says where
each part sits.

The dispatcher is in the Leica driver,
`zmart_drivers/leica/stellaris/navigator_expert/dispatcher/`. A small,
complete example is the pretend vendor,
`navigator_expert/testing/helpers/pretend_vendor.py`: a made-up microscope with
one stage axis, run through the same dispatcher, with tests in
`testing/unit/test_pretend_vendor.py`. It imports nothing but the dispatcher.
A real second vendor is the Nikon driver, `zmart_drivers/nikon/ti2/nis_elements/`:
its `primitives/dll/profile.py` is the profile handed to `run()`, and its two
classes travel over one bridge connection to NIS-Elements.

## What a new microscope brings

One folder in `primitives/` for each way of reaching the vendor software. Each
holds:

| File | What it holds |
|---|---|
| `transfer.py` | How a request reaches the vendor software, and how its reply comes back. |
| `reads.py` | One function per reading this class can answer. |
| `writes.py` | The writes, as plain data. Only for a class that can write. |
| `profile.py` | What the dispatcher needs to handle this class. |

The Leica driver has three classes:

| Class | Transfer | Reads | Writes | Profile |
|---|---|---|---|---|
| `cam_api/` | The LAS X CAM API | `READS` in `reads.py` | `Write` and its builders in `writes.py` | The waiting rule, `check_reply` |
| `direct/` | LAS X's hardware server | `READS` in `profile.py`, from `reads.py` | `TRANSLATIONS` in `writes.py`: each CAM request as an element | The read order, what counts, the routes, `WRITING`; the profile handed to `run()` |
| `log/` | LAS X's log files | `READS` in `profile.py`, from `reads.py` | None | `MAX_AGE_S`: how old an answer may be |

## The profile handed to run()

`run(profile, client, primitive, *, max_retries=None, dialogs=None, **asked)`.
The profile is any object with the names below. Leica:
`primitives/direct/profile.py`.

### For a reading

| Name | What it is |
|---|---|
| `waiting` | The waiting numbers: `WINDOWS`, `WINDOW_S`, `POLL_S`, `ANSWER_POLL_S`. |
| `spec(datum)` | The profile's row for one reading. The dispatcher does not look inside it. |
| `mode_for(datum)` | The class the reading asks first, such as `"direct"`. |
| `first_stage(spec, mode, fallback)` | `(watch, rest_mode)`: a class asked alone first, or `None`, and the class that answers when it cannot. `watch(succeeded=, deadline=, observed_after=, kwargs=, waiting_for_change=, about=)` answers a `Reading` or `None`. |
| `legs(spec, datum, mode)` | The classes that take part, in the order they are asked; or a `Reading` carrying `UnsupportedSource`. |
| `success(spec, value, about)` | Whether `value` counts as an answer. `about` is what a reading of one item is about (Leica: a job's name), or `None`. |
| `ask(leg, spec, client, *, until, answer_counts, about, source_kwargs)` | Asks one class, again until `answer_counts(reading)` or the moment `until`; answers the last `Reading`. Turns on a shared channel are kept here. |
| `UnsupportedSource` | The error class for a class a reading does not have. |

### For a write

| Name | What it is |
|---|---|
| `limits_setup_hint` | Where the operator creates the limits files; said when no limits are installed. |
| `make_request(client, write)` | The request that carries `write`. Called only after the limits allowed it. |
| `check_reply(client, write)` | The reply checked: `{"success", "error", "transient", "logs"}`. `transient` True means sending again is worthwhile. |
| `WRITING` | How the write loops send. A frozen dataclass (the dispatcher changes it with `dataclasses.replace`), with the fields below. |

`WRITING`:

| Field | Meaning | Leica |
|---|---|---|
| `transport` | The transfer the write goes through. | `ROUTED`: direct, then the CAM API |
| `waiting` | The waiting numbers. | 4 windows of 3 s |
| `max_retries` | How often a temporary error is sent again. | 3 |
| `max_confirm_attempts` | How many read-back windows a write gets. | 4 |
| `refire_on_unconfirmed` | Whether the write is sent again between windows. | True |
| `send_without_receipt` | Send without waiting for a receipt. | False |
| `never_resend` | Sent once, never again. | False |

A write's own `without_receipt` and `never_resend` are handed to the loops by
`run()`. The `ready` and `confirm` readings of a write are run by the reading
part of the same profile.

## A transfer

What `WRITING.transport` must offer:

| Method | What it does | Answers |
|---|---|---|
| `fill(request, setup_fn)` | Writes the values into the request, by calling `setup_fn` with the part of the request that holds them. | nothing |
| `clear_reply(client)` | Empties the reply, so what is read after the send is the new answer. | nothing |
| `send(client, request, description, *, asynchronous)` | Sends the request. With `asynchronous`, it does not wait for a receipt. | `(delivered, notes)`: whether it arrived, and short texts for the log |
| `await_reply(client)` | Waits for the reply to be filled in. | `True`, or `False` when the time ran out |
| `dialog_warning_since(started_at, description)` | Whether a dialog of the vendor software opened since the write began. Asked only when a write failed or stayed unconfirmed. | a text, or `None` |

A transport may say which route carried the last send in `carried_by`; the
result then names it under `route`.

### More than one route

The dispatcher's `TransportChain` (`dispatcher/write.py`) holds an ordered list
of transfers behind the one `transport`. The vendor gives the order. A route's
`send` raises `TransportUnavailable` only while nothing has left yet. The chain
then tries the next route. A send that went out, answered or not, is never
handed to another route.

## A write

Plain data, read by its fields. Leica: `Write` in
`primitives/cam_api/writes.py`.

| Field | Read by | Meaning |
|---|---|---|
| `command` | the gate | Its name in the limits. |
| `values` | the gate | What the limits are shown. |
| `request` | the profile | The name of the vendor request that carries it. |
| `fill` | the transfer | `fill(part_of_request)`: writes the values. |
| `description` | the loops | The label in the log. |
| `ready` | the loops | An `Expect` waited for before every send, or `None`. |
| `confirm` | the loops | An `Expect` read back after the send, or `None`. |
| `without_receipt` | the loops | Sent without a receipt; no reply checked. |
| `never_resend` | the loops | Sent once, never again. |

The Leica `Write` has two more fields, read by its own profile:
`accepted_replies` (LAS X replies that mean "already so") and `experiment`
(what opens the experiment a capture goes into, for the direct route).

An `Expect` names a reading and what it must show:

| Field | Meaning |
|---|---|
| `reading` | The reading's name. |
| `accept` | `accept(value) -> bool`: how close counts. |
| `about` | What the reading is about, or `None`. |
| `mode` | The class asked first; `None` takes the profile's. |
| `fallback` | The class that answers when the first cannot; `None` takes the profile's. |
| `without_limit` | Optional. Waited for as long as it takes, not the windows. |

## A reading

A reading is a name the profile knows (`spec(datum)`). Its answer is a
`Reading` (`dispatcher/read.py`):

| Field | Meaning |
|---|---|
| `value` | The answer. |
| `source` | The class that gave it. |
| `observed_at` | When it was seen (`time.time()`). |
| `age_s` | How old it was. |
| `error` | Why there was no answer, or `None`. |

What counts as an answer is declared once per reading, by the profile's
`success`. A caller narrows it with `accept`. "Not what we hoped for yet" is an
answer; only a class that cannot answer hands over to the next.

## The dialogs

Optional. When `run()` is handed `dialogs`, the write loops use:

| Name | What it does |
|---|---|
| `answer_warnings()` | Answers a dialog it may answer. Raises while one it leaves to the operator is open. |
| `watching()` | A context manager that answers dialogs while the send runs. |
| `answered_since(moment)` | What it answered since a `time.monotonic()`, each with a `message`. |

Leica: `procedures/answer_dialogs.py`.

## The limits

An object that answers `check_command(command, values)` by raising
`LimitViolation` or `LimitsError` (from `dispatcher/limits.py`) when the values
are not allowed. It also answers `status()` with a `LimitsStatus` and
`describe()` with that as a dictionary. The driver installs it at connect, with
`limits.install(client, limits.GateState(limits=...))`. Until then every write
is refused. Leica: `LeicaLimits` in `driver_setup/limits/limit_rules.py`,
installed by `driver_setup/limits/handshake.py`.

## What comes back

A reading answers a `Reading`, or `None` when nothing counted in time. A write
answers `{"success", "confirmed", "message", "timing", "logs", "route"}`.
`confirmed` is True when the read-back showed the change, False when it never
did, and None when there was nothing to read back or the write failed first.

## Not yet shared

The list of kinds of error and the rule for each (only a temporary error is
sent again) are in the Leica driver's `cam_api/profile.py`. A second vendor
writes its own until they move into the dispatcher.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com)
