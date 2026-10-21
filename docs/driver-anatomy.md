# The anatomy of a ZMART driver

Status: October 2026. This document describes the parts every ZMART driver
should have, what each part is responsible for, and the rules that connect
them. It was written by comparing the Leica Navigator Expert driver with the
Nikon, ZEISS and mesoSPIM drivers and with the ZMART Controller, and it was
then simplified while we walked the Leica driver through it. The simplified
design is in "The simplified anatomy", right after the overview.

The Leica driver (`zmart_drivers/leica/stellaris5_y42h93/navigator_expert/`)
now has this layout, so it is the example to read alongside the text. Where
this document says "today", it means that driver. The Nikon, ZEISS and
mesoSPIM drivers are not in this beta. What a new microscope has to
hand to the shared dispatcher is written on one page, in
[the vendor contract](vendor-contract.md).

The controller's side of the contract, the `ZmartControllerPlugin` class that every
driver fills in, is fixed and described in the controller's
[How do I plug in a ZMART-driver](https://github.com/ZMB-UZH/ZMART-controller/blob/main/docs/plug_in_a_driver/README.md);
"The `ZmartControllerPlugin` class" below follows it. The controller's mock driver,
`zmart_controller.mock`, is a complete, small driver to read alongside this
text. It was built in the first version of the layout (it has `get_actions/`
and `set_actions/` folders, for example), so it still has to be brought into
the simplified layout. The last sections say what is still open and in which
order we plan to get there.

A note on the numbering. The numbered sections further down are the parts in
more detail: 1 Vendor interface, 2 Dispatcher, 3 Readings and changes,
4 Procedures, 5 Saving a capture, 6 Configuration, 7 The `ZmartControllerPlugin` class,
and 8 Testing. When the text says "part 7", it means section 7.

## Why a common anatomy

ZMART drives very different microscopes: a Leica confocal through LAS X, a
Nikon through NIS-Elements, a ZEISS through ZEN, and a mesoSPIM light-sheet.
An experiment written for one of them should run on the others unchanged.
That only works if every driver is built from the same parts, in the same
order, with the same safety rules. When the parts are the same, a fix in one
place can reach every microscope, a new driver has a clear template to
follow, and someone reading an unfamiliar driver already knows where to look.

The microscopes themselves will always differ. The idea of this anatomy is to
keep those differences in one place, at the bottom, so that everything above
it can look and behave the same.

## Words we use

A few words have a precise meaning in this document. They are used from the
overview onward, so they are explained first.

- **Command**: one of the things a workflow can ask the controller to do, such
  as `set_xyz` or `acquire`. The list is short and the same for every
  microscope.
- **Reading and change**: a *reading* asks the microscope something, such as
  the stage position, and never changes anything. A *change* alters the state
  of the microscope. "Change" is meant broadly: moving the stage, setting a
  laser power, selecting a job and acquiring a picture are all changes.
- **Primitive**: one small function that does one thing with the vendor's
  software, such as reading the stage position or pressing one button. A
  primitive does not retry, check limits or wait for confirmation. The
  dispatcher does those jobs.
- **Family**: a group of primitives that reach the software in the same way,
  for example by the software's own state ("direct"), by its programming
  interface ("API"), by its log file ("log"), or through its window
  ("interface").
- **Vendor interface**: the folder that holds everything specific to one
  microscope's software: the connection, the primitives, and the sorting of
  its errors. It is the part you write for a new microscope.
- **Dispatcher**: the shared engines that run readings and changes safely: one
  conversation with the microscope at a time, the limits check, retries,
  confirmation, and what to do when something goes wrong. It is the same for
  every microscope.
- **Handling notes**: the information, kept in the vendor interface, that tells
  the dispatcher how to treat each primitive: which families to try and in
  what order, how to confirm a change, how close counts as arrived, how long
  to wait, and whether it is safe to send the command again. In the Leica
  driver the handling notes are the folder `operations/`,
  and the note for one command is called its *command profile*.
- **Session**: what the driver remembers about one connection between two
  commands: the open link to the vendor software, the origin, the loaded
  calibration, the changes that could not be confirmed, and the captures that
  are not yet saved or still sit in the vendor's cache.
- **Route and tier**: a *route* is the list of primitives to try for one job,
  in order. Each step of that list is a *tier*.
- **Fallback**: trying the next tier of a route because the one before it
  could not answer at all.
- **Readback**: reading the state again after a change, to see whether the
  microscope really did it.
- **Confirmation**: the readback loop after a change. A change is *confirmed*
  when the readback shows the target, and *unconfirmed* when the time ran out
  without it.
- **Point of trust**: the one source a reading believes. When the driver reads
  a value it does not know the right answer in advance, so it believes one
  source at a time.
- **Limits gate**: the check, inside the dispatcher, that refuses a change
  before anything is sent when it is outside the allowed range.
- **Procedure**: a recipe that combines several readings and changes, such as
  autofocus or the saving of a picture.
- **Raw stage coordinates**: positions in micrometers exactly as the vendor
  software reports them.
- **User coordinates**: positions in micrometers measured from the recorded
  origin. This is what experiments see.

## The parts at a glance

A driver is built from the parts below. Read the picture from top to bottom:
each part uses the parts below it, and never the parts above.

```
  experiments and workflows, through the ZMART Controller   ·   a notebook or a script
  ───────────────────────────────────────────────────────────────
  zmart_controller_plugin/   the ZmartControllerPlugin class: the controller's commands, each a call below
  driver_api/         the driver used by itself: connect() and the Microscope
  procedures/             the recipes: take a picture, save it, clear the cache,
                          the coordinate system, an objective change
  driver_setup/           what the operator sets up once and saves: limits, origin,
                          orientation, optical calibration, the data location
  session/                what the driver remembers about one connection
  operations/             what the microscope does: each change and reading in one place,
                          with its profile and its confirmation, and how a command is sent
  dispatcher/             the shared machinery that runs every reading and change:
                          the limits gate, the waiting rule, sending again, confirming
  primitives/             one read or write each, one folder for each way of reaching
                          the vendor software
  connection/             opening the vendor software, and the waiting numbers
  ───────────────────────────────────────────────────────────────
  vendor software (LAS X, NIS-Elements, ZEN, mesoSPIM-control)

  alongside:  testing/
```

The dispatcher sits at the bottom of the picture because it imports nothing
from the rest of the driver. It knows no microscope. The vendor part plugs
into it: it hands the dispatcher the way to send a request, the command
profiles, the sources a reading can ask, and the numbers to wait by.
[The vendor contract](vendor-contract.md) lists all of it on one page. A test,
`testing/unit/test_architecture_guard.py`, reads every file and fails when a
part imports a part it should not.

The folder layout inside a driver, as the Leica driver has it:

```
navigator_expert/
    __init__.py                offers connect() and the Microscope, and nothing else

    driver_api/            the driver used by itself, documented in the driver's README
        microscope.py          connect() and the Microscope: everything the driver can do
        acquire.py             take a picture and keep a note of it for saving (pending_capture.py)
        state.py               the microscope's state, saved with every picture
        running_log.py         the driver's rolling log file, for debugging

    zmart_controller_plugin/   everything that faces the controller, as the controller names it
        zmart_controller_plugin.json   the driver's name and its connection, nothing else
        zmart_controller_plugin.py     the ZmartControllerPlugin class: each command a call of the Microscope
        info.py                the description get_info answers
        procedure_menu.py      the named procedures for get_procedures and run_procedure

    procedures/                the recipes, one folder each
        save_data/             write the waiting captures as OME-Zarr, with their log (save_waiting, save, ome_zarr, naming)
        clear_cache/           have LAS X let go of the saved pictures (clear, release)
        absolute_coordinate_system/  the one coordinate system: the arithmetic and the moves (coordinates, frame)
        keep_in_view/  change the lens and move the stage to keep the same spot

    session/
        handle.py              the open connection and everything the driver remembers between commands
        loaded_config.py       the orientation and calibration loaded at connect

    driver_setup/              one folder for each thing the operator sets up: its defaults,
        limits/                its check, the code that measures it, and the notebook that sets it
        origin/
        orientation/
        optical_calibration/
        data_location/
        store.py               load, check and save, for every item

    operations/                what the microscope does; written for each microscope
        changes/               one file per change, with its profile and its confirmation:
                               job_settings.py (the settings of a job: one table, one runner),
                               stage.py, objective.py, select_job.py, acquire.py; run_change.py
                               and confirming.py are what they share
        readings/              the table of readings, the sources, the named readings, and
                               reading_settings.py (which source each reading asks)
        sending/               how a command reaches LAS X: the command profile, the routes
                               (direct first, the CAM API behind it), the error sorting, the
                               idle check, and the limits rulebook

    primitives/                the smallest single reads and writes, one folder for each way of reaching LAS X
        direct/                reading LAS X's own state, and sending, through its hardware server
        api/                   the CAM API
        log/                   what LAS X writes in its log files (reading only); log_settings.py says where
        interface/             LAS X's window: pressing buttons, answering dialogs
        capture/               the finished picture in LAS X's cache, and LAS X's description of it
        values/                the values in what LAS X hands back: parsing.py (its text), derived.py
                               (what a job's settings mean: its field of view, its z-stack)

    connection/                reach LAS X, keep the link; waiting.py holds the waiting numbers,
                               lasx_settings.py how the CAM API is opened

    Each layer imports only the layers below it, bottom to top: connection,
    primitives, dispatcher, operations, session, procedures and driver_setup,
    driver_api, zmart_controller_plugin. The settings of each layer live in it,
    and testing/unit/test_architecture_guard.py keeps the order.

    dispatcher/                the same in every driver; imports nothing of the driver
        door.py                the one door: read and write
        reader.py              waits for a reading, asking the sources in turn
        send_loop.py           sends a change and checks the reply, sending again when that is safe
        confirm_loop.py        reads the change back until it shows, sending again between windows
        gate.py                the limits gate, asked before anything is sent
        limit_errors.py        the shared words for trouble with limits

    testing/
        helpers/               the stand-in for LAS X (mock_lasx_api.py) and small test helpers
        unit/                  tests against the stand-in, on any computer
        hardware/              checks that only run at the microscope or the LAS X simulator
        calibration/           tests of the setup measurements on a simulated microscope
        data/                  sample LAS X files, logs, example configuration
        run_ci.py              the one command that runs the offline suite, or the hardware checks
    README.md
```

Everything that faces the controller, the class file and `zmart_controller_plugin.json`,
sits together in `zmart_controller_plugin/`, so that the controller's side is in
one place. The operator registers the driver's own folder, the one that holds
`zmart_controller_plugin/`; the controller finds both files there by their names and
imports the class as part of the driver's package, so it can use the driver's code
(controller 0.4.0rc2).

What changed from the first version of this layout: the `actions/` folder is
gone, `client.py` is split into `connection/` and the `primitives/` folder,
the `output/` folder is gone (saving is now a procedure), OME-TIFF is no
longer written, only OME-Zarr, and taking, saving and clearing a picture are
three separate steps. "The simplified anatomy" explains why.

**A command travelling through the layers.** Take "move the stage to x = 500
micrometers" as the workflow says it:

1. The workflow calls `set_xyz` on the controller, which calls the
   `ZmartControllerPlugin` method of the same name. That method calls the
   `Microscope`'s `move_to`, as a notebook using the driver by itself would.
2. `move_to` hands the position to the coordinate system procedure
   (`procedures/absolute_coordinate_system/frame.py`), which converts it from
   user coordinates to raw stage coordinates, using the recorded origin and
   the objective offsets.
3. The procedure asks for the change `move_xy`
   (`operations/changes/stage.py`), which goes to
   the dispatcher's door, `write`.
4. The door asks the limits gate first. A position outside the allowed range
   is refused here, and nothing is sent.
5. The send loop sends the move through the transport that the move's command
   profile names, and sends it again only if the problem was temporary.
6. The confirm loop reads the position back through the reader. The reader
   asks the direct source first, and the next one only if that one cannot
   answer, for up to four windows of three seconds.
7. The answer is "confirmed", or "unconfirmed" (the move was accepted but
   never seen); either way it is recorded, and the workflow carries on. The
   controller's mock README walks through the same journey for the mock.

One name needs a word of care. No folder in a driver is called `config/`,
because a package with that common name gets imported in place of other
packages.

## The simplified anatomy

This section records the decisions of October 2026. They came from walking the
Leica driver through the anatomy, file by file, and the Leica driver has since
been reshaped to match them. Where it still differs, "What this means for the
Leica driver" at the end of this section says so.

### The one rule: a new microscope means a new vendor interface

The point of the whole anatomy is that when someone writes a driver for a new
microscope, such as a ZEISS, an Evident or a Nikon, the work is in one place:
`connection/`, `primitives/` and `operations/`. Everything else, the dispatcher,
the procedures, the `Microscope` and the `ZmartControllerPlugin` class, should
already work.

What a driver author writes:

- **The connection**: how to reach this vendor's software, keep the link
  open, and close it.
- **The primitives**: the smallest single reads and writes that the software
  offers.
- **The error classification**: the sorting of this vendor's failures into the
  shared kinds of problem.
- **The operations** (the `operations/` folder): each change and reading,
  with how the dispatcher should treat it. In the Leica driver each change is
  one file in `operations/changes/`, holding its command profile and its
  confirmation; the settings of a job are one table there.
- **The metadata map**: how this vendor describes a picture, and how that
  becomes the standard metadata.

What else a new driver needs, all of it small and mostly data, not code: the
tuning numbers (how long a confirmation waits), the configuration that is
measured on the instrument (limits, orientation, calibration), a few tests,
and any recipes that microscope needs and others do not.

Two checks keep this honest:

1. **The leak test.** If supporting a new microscope ever requires editing
   one of the dispatcher's engines, then something that belongs in the vendor
   interface has leaked upward. Move it down.
2. **The size check.** Outside its vendor interface, a minimal driver should
   be about as small as the controller's mock driver. If it is much larger,
   something that should be shared has crept into the driver.

### Primitives, and the families they come in

A **primitive** is one small function that does one thing with the vendor
software: read one value, change one setting, press one button, fetch one
picture. A primitive does not retry, does not check limits and does not wait
for confirmation. Those jobs belong to the dispatcher, so that they are done
the same way for every primitive.

Primitives are grouped into **families** by the way they reach the software:

| Family | What it uses | Reads | Writes |
|---|---|---|---|
| Direct | The software's own state, read as it is held inside the program | Yes | Where the software allows it |
| API | The vendor's programming interface (for Leica, the CAM API) | Yes | Yes |
| Log | What the software writes in its log file | Yes | No |
| Capture | The place where the finished picture is handed over, and where it is released afterwards | Yes | Releasing a picture, where the software allows it |
| Interface | The vendor's window, worked the way a person would: pressing a button, switching a tab, answering a dialog (on Windows, through UI Automation) | Yes, what the window shows | Yes |

**The interface family is the last resort.** Some things can only be done
through the window. LAS X, for example, keeps every picture in a cache until
it is restarted, and it offers no command that empties the part of the cache
that the Navigator canvas holds. So the Leica driver presses the canvas's
Clear button. Such a press is an ordinary primitive. In the design, the
dispatcher runs it like any other change, with the limits gate and the error
rules. Because a button press often has no readback, its handling notes may
declare it "cannot be confirmed", which is recorded as accepted and not as
confirmed (see part 3), or confirm it indirectly, for instance by checking that
the Clear button can be reached. Today the Leica `clear_cache` procedure still
calls the button primitives itself, without the dispatcher; that is one of the
differences listed at the end of this section.

A recipe can combine families. Leica's `clear_cache` is a procedure that shows
the canvas while LAS X is idle (interface), deletes each saved picture from
the cache after checking its name (capture), and then clears the canvas
(interface). It only lets go of pictures that `save_data` has written. It
exists only for LAS X. Another vendor has no such procedure, or has one of its
own, which is why procedures are the optional, microscope-specific layer.

A microscope uses the families it has. A ZEISS may have a single one. The
Leica driver has all of them. The direct readers are the closest to the real
state of the instrument, which is why they come first wherever they exist.

### Routes, tiers and fallback

A **route** says which primitives to use for one job, in order of preference.
It is a list of **tiers**. In a reading, a tier holds one primitive. In a
confirmation, a tier may hold several, which are looked at one after the other
within the same time window (see "Confirmation"). For example:

```
read the selected job:    [ direct ]  then  [ api ]  then  [ log ]
start an acquisition:     [ api ]
confirm a stage move:     [ direct ]  then  [ api, log ]
```

The routes are written in the driver's handling notes. The dispatcher follows
them and never decides on its own which primitive to trust.

The rules that go with routes:

- **Fallback happens only when a tier cannot answer.** That means a primitive
  could not be called, raised an error, or could not give a value. The next
  tier then takes over. "The value is not what we hoped for yet" is an answer,
  not an error, and never starts a fallback. A slower source would only be
  reporting an older state.
- **A reading has a single point of trust.** When the driver reads a value, it
  does not know the right answer in advance. So it asks the sources one at a
  time, in the order of the route, and believes the first one that answers. It
  does not compare two answers and pick a winner. That is why the first source
  of a route matters most. For the Leica direct readers, we guard against one
  of them being wrong or stuck by comparing the readers with each other in the
  tests at the microscope. Those tests are required, not optional.
- **The result says who answered.** Every reading and every confirmation names
  the primitive and the tier it came from, so that anyone reading the command
  log can see when a fallback was used.
- **Writes fall back more carefully than reads.** A write may go to the next
  tier only when it certainly was not delivered. If a send timed out and
  nobody knows whether it arrived, the dispatcher reads the state back and
  does not send again by another route. An acquisition is never sent twice.
  Where a microscope offers only one way to send a command, as with Leica's
  API, there simply is no fallback for it.

### The two loops and the send styles

Every change runs two loops, whatever the vendor software is.

1. **The send loop.** Make the call, and learn whether it went through. A
   transient problem may be tried again; a fatal one stops here.
2. **The confirm loop.** Read the state until it shows the target. When a window
   ends without it, send again, but only when sending again is safe.

How a vendor's call behaves differs a great deal. The existing drivers show four
styles:

- **Wait for a receipt**: Leica's z moves, settings, objective changes and job
  selection. The call returns when LAS X says it has received the command, not
  when the work is done.
- **Fire and do not wait**: Leica's stage moves and acquisitions, and the
  mesoSPIM's acquisition (followed by a wait for the file to appear). The call
  returns at once, and the confirm loop does the real waiting.
- **The call blocks until the work is done**: ZEISS (stage moves and
  acquisitions), Nikon (moves and captures) and the mesoSPIM's moves.
- **Immediate accept or refuse**: the Evident protocol, which answers `+` or
  `-` at once, with no detail. A bare `-` is a fatal refusal.

None of that may show above the vendor interface. How the call travels (a .NET
object, a message over a local network connection, a gRPC call, a line of
text) is hidden inside the primitive. Each writing primitive hands the
dispatcher the same small result, one of five outcomes:

| Outcome | Meaning | What the send loop does |
|---|---|---|
| Accepted | The vendor said it has the command | Goes to the confirm loop |
| Refused | The vendor said no, a fatal kind of problem | Stops and reports |
| Not delivered | It certainly never arrived, a transient kind | Sends again, up to a set number of times |
| Unknown | It was sent, but no reply came in time | Sends again only if the command is safe to repeat; otherwise goes to the confirm loop |
| Done | The call blocked until the work was finished | Reads back once, then finishes |

**No reply counts as a failure.** A microscope should answer within about one
second. If nothing comes back, the call is treated as having failed and, where
it is safe, it is sent again. The waiting time for a reply is a number in the
handling notes for each primitive, with one second as the default for quick
commands. Some calls legitimately take much longer, such as a ZEISS move or a
Nikon capture, and they say so there.

**An acquisition is never sent again. Never.** If an acquisition were sent a
second time, nobody could tell which of the two the microscope ends up running:
the first one may only be slow. So an acquisition is not retried after an
error, is not retried after silence, is not sent again between confirmation
windows, and never falls back to another primitive. This is a fixed rule of the
dispatcher, not a setting that a handling note could change.

The handling notes say which style each primitive has and whether sending it
again is safe. Sending again is a property declared for each primitive, never a
default: an absolute move or a setting is safe to repeat, an acquisition is
not. The error classification is what tells "not delivered" from "unknown". A
dropped link before the request was sent is the first, and a timeout after it
was sent is the second.

Today ZEISS and the mesoSPIM treat both as transient and retry. That does no
harm for what they retry now, because those are absolute moves and settings,
and the mesoSPIM's retry in fact fails at once, since its connection was
closed and is not reopened. It would duplicate a relative move, or a
zeroing of the axes, the day a reconnect is added. Leica re-sends a missed
receipt up to four times, which is also safe only because those commands are
absolute. Nikon does not retry at all. The rule in this table, "safe to send
again" declared for each primitive, is what keeps all of these safe when a new
command is added.

Two things follow for the confirm loop. It has two kinds of confirmation: reach
a value, and complete a process (an acquisition starts and then finishes, or a
file stops growing). And the units and the tolerance, how close counts as
arrived, belong to the handling notes, because the dispatcher compares numbers
and does not know what they mean.

**A test of the abstraction.** The dispatcher is plug-in enough if it runs, with
no change, against a fake vendor of each style. The controller's mock already
takes time to move and refuses commands while busy. Add two small fakes, one
that blocks until done and one that answers accepted or refused at once. This
belongs in the size check.

### Confirmation

After a change, the dispatcher checks that the microscope really did it. This
is a special kind of reading, because the driver knows exactly which value it
expects. It watches for that value and waits until it appears or the time is
up.

- The tiers work as for any reading. The first tier that can answer is the one
  that watches. If every primitive in the tier errors, the next tier takes
  over for the following time window.
- If a primitive answers "not yet" until the time is up, the change is
  **unconfirmed**. Nothing else is tried, because the question has already
  been answered by the source closest to the real state.
- **A confirmation is the one place where several primitives may share a
  tier.** The driver knows exactly which value it is waiting for, so any
  primitive that sees that value can say so, and the first one to do so
  confirms. The primitives are looked at one after the other, many times
  within the window, never in parallel (see "Taking turns with the vendor
  software"). A tier of several primitives is allowed in a confirmation and in
  nothing else. A source that can lag behind, such as a log, may only count if
  what it saw was observed after the command was sent.
- The usual confirmation uses the direct primitives first. A race between
  several sources has to be written down for that one change.

### What a command answers: success and confirmed

Every change gives two answers, because "did it go through?" and "did we see it
happen?" are different questions.

| Success | Confirmed | What it means | What the workflow sees |
|---|---|---|---|
| Yes | Yes | The microscope did it, and we saw it. | `success: True` with the values. |
| Yes | No ("unconfirmed") | The microscope accepted the command, but the state was never seen. | `success: True` with the values. The change is recorded in the result and in the logs, and `get_state` lists it as unconfirmed. The workflow carries on. |
| No | – | The microscope refused it, a readback showed something different, or the link was lost. All retries are used up. | `success: False` with a message in `output`. |

**How long the driver waits.** The shared rule is four windows of three
seconds, so twelve seconds at most. Between the windows the command is sent
again when that is safe, which makes up to three resends. After the last
window, the change is unconfirmed. These numbers are a default and may be
changed for one command in its handling notes.

**The driver never breaks the workflow.** A command does not stop the run
because something went wrong. Once its retries and its twelve seconds are used
up, it records what happened and goes back to the workflow, which carries on
with its next step. Two guardrails keep this safe:

1. **Moving on never means pretending it worked.** The failure stays visible,
   in the result, in the log of the acquisition and in the running log (see
   "The running log"). Otherwise a picture taken after a failed move would get
   a position label that is not true, and nobody could tell afterwards.
2. **A step that depends on a failed or unconfirmed step does not run as if it
   had succeeded.** Leica's compensating stage move after an objective change,
   and the label a picture is saved under after a job change, must not run on a
   change that was only accepted. The driver would otherwise move the stage
   for a lens that never arrived. The skipped step is recorded, and the command
   answers `success: False` with a message that names it, because the state is
   not what was asked for. This is a recorded result and not a crash; the
   workflow carries on. A refusal by the limits gate is safe to move past,
   because nothing was sent.

The one exception is the check at connect (see "Pinned to one microscope and
one software version"). It happens once, before any experiment runs, and
refusing there is what protects the run.

### Taking turns with the vendor software

Only one request reaches the vendor software at a time. This is a rule of the
whole driver. A reading and a change take the same turn, so a write never
overlaps a read.

The reason is vendor knowledge, so it belongs in the vendor interface. Leica's
programming interface cannot tell two requests apart: if two are in flight, each
may read the other's answer. A different microscope may not need the rule.
Reading a log file, or a file on disk, does not reach the vendor software at
all, so it does not need a turn.

Proposal, not yet confirmed: the handling notes mark each primitive with the
channel it uses, and primitives on the same channel take turns. A primitive
that cannot be abandoned halfway, because the software then drops the next
command (Leica's API is one), is marked "do not abandon".

What the Leica driver does today. The shared reader (`dispatcher/reader.py`)
asks the sources one after the other, and each gets its own slice of the
window, so that one slow source cannot use up the whole window. The turns on
LAS X's API are kept by the vendor part (`_ApiTurns` in
`operations/readings/sources.py`): a request holds the connection's
turn until its own wait for the answer ends, and a call that never returns is
abandoned at the end of its slice without giving the turn back early. The old
habit of starting the API and the log in two threads and taking whichever
answers first is gone, with one exception: confirming a job change when the
direct reader cannot answer (see the end of this section).

### The running log

The driver keeps one log file for debugging, which grows over time. It is
separate from the log of one acquisition, which stays beside that
acquisition's pictures and says how they were made.

The running log lives in the ZMART configuration folder, next to the saved
settings, in a rolling file that starts a new one when it gets large, so that
it cannot fill the disk. Each line has a time and records one thing: every
send, every retry, how an error was sorted, every fallback to another
primitive, every confirmation window, and the final answer. It never prints a
secret, such as a password or a token. The Leica driver has it in
`driver_api/running_log.py`: it copies every message the driver
already writes into one file, renamed at about five megabytes, with the three
newest old files kept. Whether the shared dispatcher should own it is still
open.

### Pinned to one microscope and one software version

A driver is pinned to one microscope and one version of its vendor software,
as recorded in its machine description. At connect the driver checks both, and
refuses to run on anything else. This is what makes it safe for a driver to
rely on parts of the vendor software that the vendor does not document, such
as the state that the Leica direct readers read. When the vendor software is
upgraded, the driver is not trusted again until its readers have been checked
against each other on the new version and the pin has been moved on purpose.

The check lives in one place. The vendor interface offers a primitive that
reports the microscope's serial number and the software version (call it
`identify`), and only the `ZmartControllerPlugin` class compares what it reports with the
machine description. That primitive is part of what a driver author writes.
The Leica driver does not check this yet: it reports the serial number under
`observed` in `get_state`, but compares it with nothing, because there is no
machine description yet.

This is also why Leica's reliance on LAS X's hardware server is acceptable. The
hardware server is part of LAS X itself, so it is always there on the
STELLARIS, and the pin guarantees which LAS X version the driver meets. If a
later LAS X version ever locks the hardware server (by encrypting it, for
example), we will deal with that then, by adding a different primitive for the
new version.

### Who converts the coordinates

The conversion is a procedure of its own, `procedures/absolute_coordinate_system/`.
Below it, in `operations/readings/`, the driver only reports clear readings from
the microscope: the stage position, each z drive and the selected objective.
The procedure turns those readings into the driver's one absolute coordinate
system, and turns a requested position back into moves. It takes into account
the origin that was saved, the way the z drives share the focus, and the
measured offsets between objectives (the centring and focus offsets). The
limits are checked on the raw stage coordinates, so no origin can move the safe
travel range. The saving procedure then applies the measured image orientation,
so that how the stage moves in x, y and z is also how the images come out. The
`ZmartControllerPlugin` class does no arithmetic of its own: it calls the procedure.

### Limits do not know the microscope

The limits gate lives in the dispatcher and is the same for every driver, so
it cannot know the command names of any one microscope. Instead each
primitive that changes something says what it limits in its handling notes:
which value is limited, in which units, and under which key in the limits
file. The gate reads that and nothing else. What may and may not be done is
therefore decided by the primitives a driver has, and a new driver adds
limits by writing the notes for its own primitives. Anything specific to one
microscope belongs in the vendor interface, as far as it can.

Today the gate asks one question of the limits object the vendor installs:
`check_command(command, values)`, which raises when the values are not allowed.
For the Leica driver that object is `LeicaLimits`, in
`operations/changes/limit_rules.py`, built from the
operator's `limits.json` when the driver connects
(`driver_setup/limits/handshake.py`).

Proposal, not yet confirmed: a limit is a small description with a fixed shape,
so that the gate can stay free of vendor knowledge. It names the axis or
setting, the units, and either a range (smallest and largest value) or a list
of allowed values. An empty list means "reviewed and not restricted", and a
setting with no entry at all is refused, as the Leica gate does today.

Some microscopes also have a limit that is built into the hardware, such as
Leica's lower limit for the wide z drive, which it can never go below. That is
simply one more range, written in the vendor interface by the person who knows
the hardware. The gate checks it as well as the operator's limits file, and the
stricter of the two wins. The operator cannot widen it. The Leica gate does more
than the old table of names, because it checks two z drives under one command,
so it will need a real rewrite and not just a rename.

### Error vocabulary, classification and rules

Three words, so that each one says what it does:

- **Error vocabulary**: the list of kinds of problem. It is the same for every
  microscope.
- **Error classification**: the sorting of this vendor's failures into that
  list. This is the part a driver author writes.
- **Error rules**: what to do for each kind. It is the same for every
  microscope.

The table of kinds in part 1 stays as it is.

Today all three sit in one Leica file,
`operations/sending/error_classification.py`: the
list of kinds (`Kind`), the rule for each (`sends_again`: only a temporary
problem is sent again), and the sorting of LAS X's messages
(`classify_error`). The dispatcher only sees the result of a command's error
check, a small dictionary that says whether the command failed and whether the
failure is `transient`. That keeps the dispatcher free of LAS X, but it means
the vocabulary and the rules are not yet shared: a second vendor would write
its own. Moving `Kind` and `sends_again` into the dispatcher is a step for when
the dispatcher becomes a package of its own.

### The dispatcher is fully shared

Because the handling notes now live in the vendor interface, nothing in the
`dispatcher/` folder belongs to one microscope. Even the waiting rule's
numbers are handed in: Leica's are four windows of three seconds, in
`connection/waiting.py`, and a microscope that needs other
numbers hands in its own. The whole folder can move to the shared package
once a second driver uses it.

This is now true of the Leica code. Its `dispatcher/` folder imports
nothing from the rest of the driver, and the architecture guard test keeps it
that way. Everything that is LAS X's way of doing things is handed in by the
vendor part:

- a **transport** in each command profile, which fills in a request, clears
  the reply, sends, waits for the reply and reports an open dialog
  (`operations/sending/transport.py`);
- the **command profiles**, with their pre-check (is the scanner idle), their
  error check and their confirmation (each beside its change in `operations/changes/`);
- the **sources** a reading can ask, and the source tried alone first
  (`operations/readings/sources.py`);
- the **waiting numbers** (`connection/waiting.py`), handed in as
  `profile.waiting` for a change and `sources.waiting` for a reading;
- the **limits object**, which answers `check_command`, and the hint that says
  where the operator creates the limits files.

[The vendor contract](vendor-contract.md) lists these on one page. The few
fallbacks of a reading (which family answers when the direct reader cannot)
are named fields of the reading profile, `StateReaderProfile`, and "hybrid",
the API and the log together, is a Leica recipe that the dispatcher never
names.

**The handling notes may hold code (decided).** Most confirmations are one row of data:
which reading, the target, how close counts. A few need code of their own, such
as an objective change, a z-stack or an acquisition, and these are the biggest
part of the Leica driver today. A handling note may therefore name a function that lives in the vendor interface and has a fixed shape. It
is given the dispatcher's way of reading, the target, the tolerance and the
time limit, and it answers whether the state was reached. The dispatcher calls
it and does not look inside.

### Actions dissolve

The first version had an `actions/` folder between the dispatcher and the
procedures. In practice an action did one of two things. Either it was a
table entry (which primitive, how to confirm, how close counts as arrived),
or it was a real recipe (selecting a job and moving the stage to make up for
the new objective). The table entries are now the handling notes in the
vendor interface. The recipes are procedures. That removes one layer and
leaves each piece of knowledge in one place.

### Saving is a procedure, and only OME-Zarr

Saving a picture is a recipe with several steps, so it is a procedure. In
the Leica driver, taking a picture, saving it and letting the vendor software
go of it are three separate steps, because each can go wrong on its own and
each has its own moment:

1. **`acquire`** (`driver_api/acquire.py`) runs the selected job and leaves
   the picture in LAS X's cache. It writes no files. It notes what only the
   moment of the capture can tell, where the stage stood, which job ran and
   the state of the microscope, and keeps that note with the session
   (a `PendingCapture` in `handle.unsaved`).
2. **`save_data`** (`procedures/save_data/`) reads each waiting picture from
   the cache, through the capture primitives, turns LAS X's description into
   the standard metadata through the metadata map, and writes an OME-Zarr
   store, with its projection and its log. It records which cache picture each
   saved capture came from (`handle.in_cache`). A capture that cannot be saved
   stays waiting, so a later call can try again.
3. **`clear_cache`** (`procedures/clear_cache/`) lets LAS X go of the pictures
   that were saved, and only those.

The controller's `acquire` command runs the first two, never the third. Both
`save_data` and `clear_cache` are also offered through `run_procedure`, so a
workflow can take pictures quickly and save them later, and clear the cache
when it chooses, for example between plates.

The writer, which also orients, names and saves the files so that a
half-written file is never left behind, is the same for every microscope. It
lives in `procedures/save_data/ome_zarr.py` until the shared package takes it.

For this design, OME-TIFF is dropped and OME-Zarr is the only format.

Saving does not need an engine of its own. The dispatcher exists to talk to
the instrument safely: one conversation at a time, the limits check, retries
and confirmation. Saving writes files and does not touch the instrument, so
reading the picture from the vendor's cache does not go through it either: it
is a file read. The writer keeps its own safety rules: it writes each file
whole, never overwrites an earlier picture, and checks that what it wrote
reads back the same. The capture's log, the record of what was asked and what
happened, is gathered by `acquire` (the attempts, the confirmation, the
unconfirmed changes) and written by `save_data` beside the images.

The **standard metadata** is one record that every driver fills in: the pixel
size and the z spacing, the channels and their wavelengths, the stage position
of each plane, the objective and settings used, when the capture began and
ended, and which command produced it. A driver that cannot fill a field, for
example the wavelength on a camera-only system, must say "unknown". It must
not guess or leave a blank that looks like a value. Which fields are required
is still to be decided.

**Who builds the record (planned for later).** Metadata handling is not part of
the first refactor. It will be built later, on top of the primitives, once they
are in place. What follows is the plan, so that the primitives are shaped to
allow it. The record is put together in two steps, and the split matters,
because the metadata is more than one reading.

1. **The metadata map** (in the vendor interface) translates the vendor's own
   description of a picture into the standard fields, for example "this
   vendor's channel entry becomes that wavelength field". It is pure
   translation and touches no hardware.
2. **The `acquire` and `save_data` procedures** assemble the whole record. The pieces come
   from different places: the stage position, the objective and the job
   settings are read through the dispatcher, so the fallback rules and an
   honest "unknown" apply to each; the wavelengths and pixel size come from
   the vendor's description through the map; and the start and end times are
   measured by the procedure itself while it watches the acquisition, not
   read from the microscope.

A single primitive could not build this record, because it combines several
readers. That is why assembling it is a recipe, and why the vendor interface
keeps only the translation.

### What this means for the Leica driver

The Leica driver has been reshaped to this anatomy in small steps, each
checked with the offline tests, and the whole of it was run on the LAS X
simulator on 21 October 2026 with `run_ci.py --hardware`. The picture and the
folder tree at the top of this document are its layout. What was done, in
short:

- **Scan fields are gone.** The driver used to read and edit LAS X's scan-field
  template files. Scan fields are not something this driver does, so that code
  was removed. The stacked z drive is read only from LAS X's hardware server,
  and refused with a clear message when the server cannot be reached. The
  hardware server is part of LAS X and always present on the STELLARIS, so this
  loses nothing.
- **The `actions/` and `output/` folders are gone.** What the actions held is
  now the handling notes in `operations/` (command
  profiles, settings, confirmations, readings) and the recipes in
  `procedures/`. What output held is now the capture primitives in
  `primitives/capture/` (LAS X's files, its description of a
  picture, the shape of a capture) and the saving in `procedures/save_data/`.
- **The dispatcher knows no microscope.** `dispatcher/` holds the door, the
  reader, the send loop, the confirm loop and the limits gate, and imports
  nothing else of the driver. The vendor part hands in everything LAS X does
  its own way (see "The dispatcher is fully shared").
- **One waiting rule.** Every wait on LAS X follows four windows of three
  seconds, with the numbers in `connection/waiting.py`. A
  command profile can say `never_resend`; the acquisition does, and nothing a
  caller asks for can switch the resend back on.
- **Primitives are grouped by channel**, not by reading or writing: `api`,
  `direct`, `log`, `interface` and `capture`.
- **The coordinate system is a procedure**,
  `procedures/absolute_coordinate_system/`: `coordinates.py` holds the plain
  arithmetic, `frame.py` decides what to read and what to move, and the
  `ZmartControllerPlugin` class converts nothing itself.
- **The session has a folder of its own.** `session/handle.py` holds the open
  connection and what the driver remembers between commands, and
  `session/loaded_config.py` the orientation and calibration loaded at connect.
- **Taking, saving and clearing are three steps** (see "Saving is a procedure,
  and only OME-Zarr"). Only OME-Zarr is written. The OME-TIFF writer and its
  repair code are gone, and so is the old `cleanup_cache` setting. The
  calibration and orientation steps read their frames back from the saved
  store, by the plane number the capture gave each plane.
- **The driver can be used by itself.** `connect()` hands back a `Microscope`
  (`driver_api/microscope.py`) with everything the driver can do, under
  plain names, and the driver's README documents it. `ZmartControllerPlugin` only gives
  each controller command its name and answer, by calling the `Microscope`,
  and the guard test lets it import nothing else. The package offers
  `connect()` and the `Microscope` only; the low-level reads and commands on a
  raw LAS X client are no longer its surface. The old adapter is gone, and the
  driver keeps a running log of every part (`driver_api/running_log.py`).

**Where the Leica tree still differs from the plan.** Each item is listed on
purpose, so that it cannot be forgotten.

1. **The fallback for confirming `select_job` still races two legs.** The
   direct reader confirms a job change alone, as it is first choice for every
   read. Only when it cannot answer do the API and the log run together in two
   threads (`operations/changes/select_job.py` and the
   race in `confirming.py`). The log leg reads a file, which never reaches
   LAS X, so the one-request-at-a-time rule still holds.
2. **The error vocabulary and its rules are not yet shared.** They sit in the
   Leica file `error_classification.py`, beside the sorting (see "Error
   vocabulary, classification and rules").
3. **The shape of a command profile is Leica's.** `CommandProfile` lives in
   `operations/sending/command_profile.py`, and the dispatcher reads its
   fields by name. A second vendor needs the same fields; the shape belongs to
   the dispatcher, with only the values in the vendor part.
4. **Some procedures still call primitives directly.** `clear_cache` presses
   the canvas buttons and deletes cache pictures without the dispatcher, and a
   few recipes read through the vendor's named readings rather than through
   a small set of vendor-neutral primitives.
5. **A limit is not yet a declared description.** The gate asks
   `check_command` and names no Leica class, but the shape of a limit (axis,
   unit, range or allowed values) is still the Leica file format.
6. **No machine description and no pin check.** The serial number is reported
   but not compared with anything (see "Pinned to one microscope and one
   software version").
7. **`vendor_side/` and a full metadata map.** `vendor_side/` is not needed for
   LAS X. `primitives/capture/metadata_map.py` reads LAS X's description of a
   picture into the standard fields; translating every vendor field is the
   later metadata work.
8. **Open issue: the direction of a saved stack has not been confirmed on a
   microscope.** The driver saves planes with z ascending, the same direction as
   `set_xyz`, and each plane's z is worked out from the job's stack settings, not
   measured from the picture. That is consistent in the code and in the offline
   tests, but nobody has yet checked on a microscope that the plane labelled
   with a given z really was taken there. A check: take a stack of a bead at a
   known depth, score the sharpness of every plane, and see that the sharpest
   plane is the one whose z matches the bead's focus. The same check with the
   stack on the other z drive would test that the two drives count the same way.

**About the Leica recipes in `procedures/`.** In the plan, `procedures/` is
written by each driver. The recipes there know LAS X and its two z drives, and
they belong there. A new microscope writes its own. The shared part is what
lies below them: the dispatcher and its rules.

**The order, and a safety net.** The reshaping was done in small steps, each
followed by the offline tests and the hardware tests: first the moves that
change no behaviour (folder and file names), then the handling notes, then the
recipes, and only then the changes in behaviour. Each step must pass one extra
test: the limits, calibration and origin that operators have already saved on
the LAS X computer still load, without anyone redoing a calibration.

## 1. Vendor interface

> **Note, October 2026.** In the Leica driver the vendor interface is three folders at the driver's top level: `connection/`, `primitives/` (a folder for each way of reaching LAS X) and `operations/` (each change and reading, and how a command is sent). The sorting of errors is `operations/sending/error_classification.py`, which today also holds the list of kinds and their rules (see "Error vocabulary, classification and rules"), and the metadata map is `primitives/capture/metadata_map.py`. See "The simplified anatomy".

**Purpose.** This is where each microscope is allowed to be completely
different. It opens the connection to the vendor software and does whatever
that software needs, so that the parts above can stay simple.

What it looks like today in each driver:

| Driver | What the vendor interface has to deal with |
|---|---|
| Nikon | A small Python program runs inside NIS-Elements and calls the C functions in `g5_regprocs.dll`. Our driver talks to it with one JSON message per line over a local network socket. |
| mesoSPIM | Python script templates are sent into mesoSPIM-control and run there. |
| ZEISS | A secure gRPC connection (a network protocol) to the ZEN API gateway, with a control token on every call. |
| Leica | Calls into the LAS X CAM API, reading the command echo after each call, and parsing the LAS X log files. |
| Mock | Starts MockScope Control, pretend vendor software that lives in the driver's own `testing/mock_api/`, and logs in with a token. |

**Responsibilities.**

- Open and close the connection to the vendor software.
- Check the vendor software version when connecting. A version the driver has
  never been tested against should be refused, or at least warned about
  loudly.
- Offer a list of *primitives*: plain functions that each do one thing.
- Sort every error the vendor software raises into one of the shared kinds
  of problem (see "Errors" below), in its `error_classification.py`.

**The three promises.** Inside, the vendor interface may look like anything.
At its top edge it keeps three promises:

1. **Plain values.** Primitives take and return plain Python values, with
   positions in micrometers. No C types, macro text, log lines or network
   tokens leak above this line.
2. **Classified errors.** Every failure it raises can be sorted into one of
   the shared kinds of problem, and `error_classification.py` does the sorting, so that
   nothing above this line ever reads an error message.
3. **Replaceable by the mock API.** Everything above the vendor interface must
   run the same way against the mock API (part 8) as against the real
   microscope.

The list of primitives is not the same for every microscope. Leica thinks in
*jobs*, ZEISS in *experiments*, Nikon in *optical configurations*, and
mesoSPIM has none of these. Forcing one fixed list on all of them would either
be too small to be useful or push the awkward fit up into the layers above. The
list that must be the same everywhere lives higher up, in the `ZmartControllerPlugin`
class (part 7).

**The vendor side.** Some vendor software cannot be driven from outside at
all; a piece of our code has to run inside it. The Nikon driver runs a small
bridge inside NIS-Elements, installed as macros, and the mesoSPIM driver sends
Python script templates into mesoSPIM-control to be run there. That code is
part of the vendor interface, in its own folder, `vendor_side/`, together
with whatever installs it. It follows three rules of its own: it uses the
standard library only, because it runs in the vendor's Python, not ours; it
imports nothing from the driver except the message format it shares with
the connection and the primitives; and nothing in the driver imports from it. A patch to the vendor
software itself, such as mesoSPIM's Remote Scripting server, lives here too,
with a note on how it is applied.

**Where a workaround belongs.** Every microscope needs workarounds. A simple
rule decides where each one goes:

- If it is about **how to talk to this vendor** (converting to C types,
  running code inside NIS, parsing a log line, a call that only works after
  selecting a job first), it belongs in the vendor interface.
- If it is about **what a value means** (for example, Leica's focus position
  is the sum of the z-wide and z-galvo drives), it belongs in a reading or
  change.
- If it is **a recipe of several readings and changes**, it is a procedure.

**Errors: the seam between the vendor interface and the dispatcher.** Every
microscope reports problems in its own way: LAS X as echo text, ZEN as a
gRPC status code, NIS as captured error output. If each command interpreted
those messages itself, every command would grow its own if-else rules, and two
commands would end up treating the same problem differently. So the driver
decides in two places, and only two, what happens when something goes wrong:

- **The sorting** is written per microscope, in the vendor interface's
  `error_classification.py`. It takes whatever the vendor reported and sorts it into one
  of the shared kinds below.
- **The rules** are the same for every microscope. For each kind, they say
  what the reader and a change do next. (Today the Leica rules sit beside the
  sorting, as `sends_again`; they move to the dispatcher when it becomes
  shared.)

The dispatcher never interprets error messages itself. They ask the
sorting what kind of problem this is, and then follow the rule.

**The shared kinds and rules.**

| Kind | Example | Reader | Change | What the `ZmartControllerPlugin` method answers |
|---|---|---|---|---|
| Bad request | An unknown option; the vendor says "is invalid" | Raises | Does not retry | Raises `ValueError` |
| Temporary | The software is busy; a short timeout | Reads again | Sends again, up to a set number of times | Raises `RuntimeError`, only if every retry fails |
| Permanent | The vendor reports a failure; a hardware fault | Raises | Does not retry | Raises `RuntimeError` |
| Connection lost | The vendor software was closed | Raises | Raises | Raises `RuntimeError` |
| Outcome unknown | The reply never came, after the request was sent | – | Reads back first; never sends again blindly | `False` and a message, if the readback does not show the target |
| Refused by limits | A target outside the travel range | – | Stops before anything is sent | Raises `ValueError` |
| Unknown reading | A stale log entry; a read that timed out | Returns "unknown" with the reason | Counts as "not confirmed yet" | – |
| Unconfirmed | The command was accepted, but the readback never matched | – | Sends again, then gives up softly | `True`, with the change recorded as unconfirmed; `False` only when a readback contradicts it |
| Stopped by user | See "Stop" under open questions | – | Stops waiting | To be decided |

The first four kinds are how the vendor software can fail, and the sorting
produces them. The others are not errors the vendor raises but outcomes the
dispatcher reaches on its own; they are in the same table so that every
rule stands in one place.

**Outcome unknown deserves a word.** A short timeout *before* anything was sent
is temporary, and sending again is harmless. A timeout *after* the request was
sent is different: the vendor software may still carry the command out. A
ZEN call can give up while the experiment is still acquiring, and a mesoSPIM
script can run on after its socket drops. Sending a move again in that state
can move the stage twice, unless the move is absolute, in which case the
second send does no harm. So a change is never sent again after a send whose
reply was lost, unless the handling notes declare that command safe to
repeat. It reads back first, and only when the readback does not show the
target does it report a failure. Today ZEISS and the mesoSPIM do retry such a
timeout (see "The two loops and the send styles"), and only the fact that
they retry absolute moves keeps that safe.

**The sorting may have to read text.** Not every vendor gives a code. The
mesoSPIM returns a traceback, NIS buries its code inside a message, and the
Evident RDK answers a bare minus with no detail at all. Sorting from message
text is allowed, in the vendor interface and nowhere else. Where we control
the protocol, as with our own bridge, it should carry the vendor's code or
the kind itself, so that no text has to be parsed. A vendor that gives no
detail leaves every refusal permanent, under the first rule below; that is
the safe reading, and it should be written down as a choice, not left to
happen.

The last column is what reaches the controller. The controller turns every
one of these into the same answer for the workflow, `success: False` with
the message as its content (a raised error is reported with its text, for
example `ValueError: x=12000 is outside the travel`). The kinds differ in
what the driver does *before* it answers, not in the shape of the answer.
Part 7 says more about the answers.

**Rules that always hold.**

- **An error we do not recognise counts as permanent.** Retrying something we
  do not understand could repeat a harmful action. The Leica driver already
  works this way: its sorting checks the permanent patterns first and
  treats anything unrecognised as permanent.
- **Error messages name keys, never values.** The connection settings can
  contain passwords, so a message may say "the key `password` is missing" but
  never print what a key holds.
- **How many times** to retry and **how long** to wait may be tuned per
  command. **What to do** for each kind of error is fixed by the table.

## 2. Dispatcher

**Purpose.** To run every reading and every change safely, the same way every
time, so that a command's handling note can stay a few lines long and never
has to think about retries, limits or confirmation itself. The dispatcher is
the part that is meant to be identical in every driver. It knows no
microscope: the numbers and the ways of talking that differ for one
microscope are handed in by its vendor part (see
[the vendor contract](vendor-contract.md)).

In the Leica driver it is six files in `dispatcher/`:

| File | What it does |
|---|---|
| `door.py` | The one door. `read` waits for a reading; `write` asks the limits gate and then runs a change. It also builds the small timing and log records every result carries. |
| `reader.py` | The engine behind every reading. |
| `send_loop.py` | The first loop of a change: send it and check the reply. |
| `confirm_loop.py` | The second loop of a change: read it back until it shows, and send again between windows when that is safe. |
| `gate.py` | The limits gate, asked before anything is sent. |
| `limit_errors.py` | The shared words for trouble with limits: a malformed limits file, a value outside the limits, where the limits in force came from. |

**The reader** (`reader.py`) is the engine behind every reading. It:

- follows the one waiting rule, four windows of three seconds in the Leica
  driver, with the numbers the vendor hands in (`waiting`);
- within a window, asks the sources the vendor's policy allows one after the
  other, each in its own slice of the window, and takes the first answer that
  counts as a success;
- lets the vendor name one source that is tried alone first (for LAS X, the
  direct reader), and the family that answers when that one cannot;
- rejects an answer seen before a given moment, so that a confirmation never
  believes a reading from before its command was sent;
- answers "unknown" instead of hanging, when no answer counted in time.

It hands back a *reading*: the value, the source it came from, when it was
seen and how old it was, or an empty reading with the reason when the driver
cannot be sure. Most microscopes have a single source, and the reader does
not assume two.

**A change** runs through the same steps, in `door.write`, the send loop and
the confirm loop:

1. **Limits gate.** The door asks the gate whether the limits of this
   connection allow the change. If not, nothing is sent.
2. **Pre-check.** The command's profile may ask whether the microscope is
   ready, for example whether the scanner is idle.
3. **Send** the request through the profile's transport.
4. **Error check.** The profile's error check reads the reply and says whether
   the command failed and whether the failure is temporary. A temporary
   failure is sent again, up to a set number of times; anything else stops.
5. **Confirm.** The profile's confirmation reads the state, for one window,
   until it shows the target.
6. **Send again** if the window ended without it, up to a set number of
   windows, but only when the profile allows it. A command marked
   `never_resend`, such as the start of an acquisition, is sent once.
7. **Give up softly** if it is still not confirmed: hand back a result that
   says the change is unconfirmed and why, instead of raising. Not confirmed
   is not the same as contradicted: a readback that shows a *different* value
   is a failure, a readback that never arrived is not. Part 7 says what the
   workflow sees.

Every change answers with the same small dictionary: `success`, `confirmed`,
`message`, the timing of each step and the log entries collected on the way.

**When the send already waits.** On most of our microscopes the vendor call
only returns when the action is done: a ZEN call blocks, a mesoSPIM script
runs `wait_until_done`, a NIS bridge call runs on the single main thread. Then
"send, then read back until it matches" collapses to one readback after the
call returns, and the confirmation is insurance rather than a wait. In the
design this is a mode of a change, *blocking send*: one send with a time limit
of its own, then a single readback. The time limit may be long (a mesoSPIM
acquisition takes minutes) or absent; it is never silently replaced by a short
default, which is a bug one of our drivers has today. While such a send runs,
nothing can be read, so a pre-check that needs the instrument's state is
declared "not available" for that microscope rather than faked. The Leica
dispatcher does not have this mode yet, because LAS X never blocks.

**A reading need not be instrument state.** The reader's sources are whatever
can answer a question honestly: the vendor's API, a log file, or the size of a
file on disk. The mesoSPIM cannot say whether it is idle, so the only proof
that an acquisition finished is that its file has stopped growing. That is a
reading like any other, with a source, an age and a possible "unknown".

**The limits gate** (`gate.py`) keeps, for each connection, the limits
object that the connect-time handshake installed, and asks it
`check_command(command, values)` for every change. When no limits could be
installed, it refuses every change and says why. An unknown limit is never
treated as "no limit". Stage positions are checked in raw stage coordinates
(part 6).

**Rules.**

- **Every change goes through the door**, and the limits gate is inside it. No
  command can skip the gate, because the door is the only way to send.
- **A change uses the reader**, for the pre-check and for the confirmation.
  The reader never runs a change.
- **The reader asks again, never the hardware.**
- **A single read must fit inside one confirmation window.** Otherwise the
  confirm loop gives up before the reading arrives, or an abandoned read is
  still running when the next attempt starts. The Leica driver learned this
  the hard way (finding CF-05 in `select_job`).
- **One request at a time is kept per request, not per confirmation.** When
  Leica held that rule around a whole confirmation, the confirmation's own
  reads were blocked (finding CF-01). In the Leica driver the turns are kept
  by the vendor part, because only LAS X's API needs them.
- **One writer at a time.** The dispatcher assumes a single caller. A workflow
  that sends commands from several threads at once can mix up the results.

## 3. Readings and changes (formerly "Actions")

> **Note, October 2026.** There is no `actions/` folder any more. What it held
> became the handling notes in the vendor interface (which primitive, how to
> confirm, how close counts as arrived) and, for the real recipes, procedures.
> The rules below still describe how readings and changes are treated. See
> "The simplified anatomy".

**Purpose.** To say, for each thing the driver can ask of the microscope, which
primitive to call and what the value means. The dispatcher runs it; all the
care about retries, limits and confirmation is the dispatcher's.

**Readings** ask the microscope something, such as the stage position or the
selected objective, and say what the value means. For example, Leica's focus
position is the sum of the z-wide and z-galvo drives, and a raw stage position
becomes a user position through the configuration's coordinate functions
(see "Configuration").

**Changes** alter the microscope: a move, a setting, an objective, an
acquisition. Each one names the primitive to call and how to confirm it: which
reading to check, the target, the tolerance, and how long to wait. Most
confirmations are a single row of data. A few, such as acquisition, an
objective change or a z-stack, need code of their own (see "The dispatcher is
fully shared").

**Rules.**

- A reading never changes the microscope.
- A reading never knows a target. It does not know what value anyone is
  hoping for.
- A change confirms itself through readings, never the other way round.
- A command never reaches the vendor interface around the dispatcher.
- **A change may declare that it cannot be confirmed.** NIS has no way to read
  the exposure or the current optical configuration back; a loaded ZEN
  experiment is a handle, not a value; and the Evident protocol, as far as the
  current spike goes, has no reading at all. Two of our drivers "confirm"
  these by echoing the value they were given, which proves nothing. Instead
  the change says so in its handling note, the dispatcher records it as
  *accepted* and not confirmed, the command log says the same, and `get_info`
  names the settings this applies to, so whoever drives the microscope knows
  which answers are the vendor's word rather than a reading. Where a side
  effect can be seen, such as a file appearing, that can serve as the
  confirmation.

## 4. Procedures

**Purpose.** To offer recipes that combine several steps, such as autofocus,
or parking the z-galvo at zero while keeping the focus.

**Rules.**

- **A procedure goes only through the dispatcher**, with the name of a command
  and its values, for example a move to a position or the reading of the
  selected job. It never calls the vendor interface directly. Every step then
  passes the limits gate and the error rules without any extra effort. (Before
  October 2026 this rule said "only actions"; the actions folder is gone, see
  "The simplified anatomy".)
- **Each procedure describes itself** with a name and a plain-language
  description. That is what `get_procedures` lists and `run_procedure` runs.
- **The setup measurements are procedures too.** Measuring the image-to-stage
  registration, calibrating an objective pair and recording the origin all
  move the stage and take pictures, so they are recipes like any other. Each
  one ends by saving its result into the configuration (part 6), and the
  item's notebook does nothing but call it.
- **The pure algorithms live here, as plain modules**, next to the recipes
  that use them: the focus score, the image registration. They take images
  and numbers and return numbers, and never touch a microscope, so they can
  be tested on sample images alone. Nothing in them is specific to one
  microscope, which is why they will move to the shared package once a
  second driver uses them (see "Shared across drivers" below). The mock has
  `procedures/focus_score.py` this way.

## 5. Saving a capture (formerly "Output")

> **Note, October 2026.** This part is superseded. There is no `output/` folder any more. Saving is the `save_data` procedure, run after `acquire`; finding and reading the vendor's picture belongs to the vendor interface (the capture primitives and the metadata map), and the writer is shared. Only OME-Zarr is part of the design. See "Saving is a procedure, and only OME-Zarr". The responsibilities below still hold.

**Purpose.** To turn what the microscope produced for one `acquire` call into
ZMART's product of that call, and to say exactly where it was saved. The
product is the image data in a standard format, the metadata that travels
with it, and the log of how it came about. Configuration is not output; it
has its own part.

Acquiring the image is a change (part 3): it starts the capture and
confirms that the capture finished. The output part starts after that.

**Who organises what.** Two parties organise the data, and the line between
them matters. The driver organises *one acquisition*: it guarantees that a
single call's product has the same shape on every microscope, a standard
format, named after the acquisition type and the position label, never
overwriting, with a plane table and a log beside it. The workflow organises
*the experiment*: where acquisitions go, how they group and in which order is
the experiment's layout, which the driver only receives, as the three
parameters of `acquire` that organise a run: the `output_folder` to save in,
the `acquisition_type` (such as `overview` or `targets`) and the
`position_label`, which every file name is made of,
`<acquisition_type>_<position_label>`. A driver whose vendor software always
saves in its own place saves there, lists the real paths, and says so in
`get_info` (`chooses_its_own_folder`); it is never refused. The analysis
engine and the viewer read across acquisitions, so that layout has to be one
the workflow controls, not one each driver invents. For the same reason the
shape of an acquisition, a single image, a z-stack, a time series, is not a
folder level: it is already in the plane table, where the z-stack has
several `z` values and the time series several `t`.

**Responsibilities.**

1. **Find** what the vendor software produced: files in a folder, or image
   data handed over directly. When the vendor software runs on another
   computer, as ZEN and mesoSPIM-control do, this step includes reaching the
   file, over a shared disk or a transfer. A file that cannot be reached is
   its own outcome, a failure that says so, never a success with a path on a
   computer the workflow cannot see.
2. **Wait until it is complete.** A file must have stopped growing and the
   export must have finished, so that we never read a half-written file.
3. **Convert** it to OME-Zarr, the only format in this design. NIS can write
   OME-TIFF, ZEN writes CZI and the mesoSPIM writes its own TIFF stacks, so
   the picture always has to be converted. A light-sheet volume of thousands
   of planes becomes one Zarr store, and the plane table says which planes it
   holds.
4. **Name and place** it in the output folder `acquire` was given, named
   after the acquisition type and the position label. A second picture with
   the same name gets a new name; an earlier one is never overwritten.
5. **Attach the metadata**: the position in user coordinates, the instrument
   state, and the pixel size.
6. **Keep the command log**: for each acquisition, what was asked, what
   happened, how many attempts it took, and whether it was confirmed. The
   dispatcher already produces this information; the output part saves it
   next to the images so the experiment can be traced afterwards.
7. **Report** what was saved, in the two lists `acquire` answers with:
   `files`, the path of every file, and `planes`, one entry per image plane
   with its file, its channel, depth and time index, and the stage position
   it was taken at, in user coordinates: the centre of the field of view. A
   store the driver writes (OME-Zarr) is placed by the centre of its first
   voxel (OME-NGFF RFC-5).

Problems here go through the same sorting and rules. A file that never appears
after a confirmed capture is a permanent error, and `acquire` then answers
with empty lists and `False`.

## 6. Configuration

**Purpose.** To hold everything the person at the microscope sets up and
saves, and to load it every time the driver connects.

This part holds data, the checks on that data, and the coordinate arithmetic,
and nothing that touches an image or moves the stage. Measuring an item is a
procedure (part 4), which writes its result here. The dependency runs one
way: procedures know the configuration, the configuration knows nothing
about procedures.

| Folder | What it holds | How often it changes |
|---|---|---|
| `origin/` | The point that reads as (0, 0, 0) in user coordinates. | Often, for example for each new sample. |
| `orientation/` | How image pixels relate to stage movement: which way each axis points, flips and 90° turns, and the pixel size. | Rarely: at installation, or after hardware changes. |
| `limits/` | How far each axis may travel, and the allowed values for each setting. | Rarely. |
| `optical_calibration/` | The x, y and z offsets between objectives, so that a change of objective keeps the same spot in view. | Rarely. |
| `machine_description/` | The fixed facts about this instrument: its objectives, detectors, axes, motors and serial number, and the vendor software versions the driver was tested with. (The Leica driver does not have it yet.) | Only when the hardware changes. |
| `data_location/` | Where on this computer the captures are saved, when the workflow does not say. | Once, when the driver is installed. |

The origin has its own folder because recording it is a frequent, everyday
step, while the registration is measured rarely. Someone recording a new
origin should never be one notebook cell away from overwriting the
registration.

**An item may come from the vendor, or not apply.** NIS knows its own
travel limits, ZEN reports the pixel size live, the mesoSPIM has no
image-to-stage registration to speak of. Where the vendor is the source, the
vendor interface offers a reading for it and the item in `driver_setup/`
holds only what the operator adds, such as a narrowing of the vendor's
range, or nothing at all. At connect, the class reads the vendor's value,
applies what is saved, and hands the result on. The item still lives here,
it is still data, and the one-way rule still holds. An item that does not
apply to a microscope is declared so in its folder, not left out, so that
`get_info` can say it and a reader of the driver is not left guessing.
Limits are kept per motor, since a piezo has a range of its own, and a
setting's allowed values are never optional: a setting with no limits entry
is refused, not passed through.

**Every item follows the same pattern:**

- **Defaults** shipped with the driver in the repository.
- **Load, check and save**: the saved copy lives under the computer's ZMART
  configuration folder, which `zmart_controller.registry.config_root()` names
  (`C:\ProgramData\zmart-microscopy` on Windows). A malformed file is refused,
  not guessed at. Saved copies never go into the repository.
- **A notebook** in the item's folder that walks the operator through
  setting it. The notebook holds no logic of its own: measuring the item is
  a procedure (part 4), and the notebook only calls it, shows the result,
  and saves it. An interface can then do the same setup later by calling the
  same procedure. The mock has no notebooks yet; it records its origin
  through `procedures.record_origin` and saves through `configuration.save`,
  which is what such a notebook would call.

**Setting an item up.** The main way to set up each item is its notebook. The
reason is the record: a notebook that has been run keeps everything that was
sent to the microscope and everything that came back, so the setup can be
looked at again later. The Leica driver already keeps the run notebook beside
the saved result. The notebook only calls the item's procedure, so the same
setup can also be offered through an interface later. That comes after the
notebooks, and a setup run from an interface has no record of its own until
we decide how to keep one. The notebook and procedure names:

| Item | Notebook | Procedure |
|---|---|---|
| `machine_description/` | `describe_machine.ipynb` | None yet: typed in, or read from the vendor software |
| `orientation/` | `set_orientation.ipynb` | `measure_orientation` |
| `origin/` | `set_origin.ipynb` | `record_origin` |
| `limits/` | `set_limits.ipynb` | `measure_limits` |
| `optical_calibration/` | `calibrate_objectives.ipynb` | `calibrate_objectives` |

The Leica driver has `set_limits.ipynb`, `set_orientation.ipynb`,
`calibrate_objective_pair.ipynb` (for the optical calibration, one pair of
objectives at a time) and `set_data_location.ipynb`, each in the `notebooks/`
folder of its item. It records the origin with the `set_origin` command
rather than a notebook, and it has no machine description yet.

**Order at connect.** Connecting is making the `ZmartControllerPlugin` (part 7): its
`__init__` opens the vendor software and loads the configuration. Each item
depends on the one before it, so it loads them in this order: machine
description, image-to-stage registration and origin, limits, optical
calibration. Loading the limits is what switches on the limits gate. After
loading, the driver compares what the microscope reports with the machine
description: a serial number that belongs to another instrument is refused,
and so is a software version the driver is not pinned to (see "The simplified
anatomy"). The mock's `__init__`
(today its `connect`) shows these steps in order.

**The coordinate system.** The arithmetic between raw stage coordinates and
user coordinates (subtracting the origin, adding the objective offsets) lives
once, in the plain functions of `procedures/absolute_coordinate_system/coordinates.py`.
The recipes that read and move the stage in that system sit beside them. That
way:

- the controller and the experiments speak one coordinate system, the user's;
- the vendor code below the procedures reports and sends raw stage
  coordinates only;
- the limits gate checks raw stage coordinates, so recording a new origin can
  never move the safe travel range;
- the `ZmartControllerPlugin` class does no arithmetic.

## 7. The `ZmartControllerPlugin` class

> **Note, October 2026.** The class file now lives in `zmart_controller_plugin/`, not at the top of the driver folder; see the layout at the start of this document. The older `zmart_controller_plugin.py` goes away once the class has driven the real microscope.

**Purpose.** To present the driver to the ZMART Controller in the shape every
microscope shares.

The controller fixes this shape, and it is described call by call, with the
exact inputs and outputs, in the controller's
[How do I plug in a ZMART-driver](https://github.com/ZMB-UZH/ZMART-controller/blob/main/docs/plug_in_a_driver/README.md#3-writing-a-driver).
This section says how that shape sits on top of the parts above, and which
rules the class follows. When the two disagree, the controller's document
wins, and this one needs updating.

**Two files.** To the controller, a driver is a folder holding
`zmart_controller_plugin/`, with two files:

- `zmart_controller_plugin.json` holds the driver's name (what `get_instruments` lists
  it under, such as `stellaris`) and the `connection`, and nothing else: how to reach the microscope on this computer. The keys the
  controller knows are `microscope`, `api_type`, `host`, `password` and
  `config`; a driver may add its own, such as where images go. Every key is
  optional, and the driver fills in what is left out.
- `zmart_controller_plugin.py` holds the `ZmartControllerPlugin` class. The controller makes one
  when it connects, and calls the method of the same name for every command.

The controller's `template/` folder holds both files, with every method left
to fill in. A new driver starts by copying it.

**Connecting.** Making the class is the connection: `__init__(connection)`
opens the vendor software with what is in the `connection` dictionary and
loads this microscope's configuration (part 6). In the Leica driver the class
makes the driver's own `Microscope`, which does this, and every method is a
call of it: the driver works the same with or without the controller. `disconnect` closes the
vendor software again. Everything the class needs between calls, such as
the vendor connection, the session and the command log, lives on
`self`.

**The commands.** After `__init__` and `disconnect`, the class has ten
methods, and each one only puts the parts above to work:

| Method | Built from | Hands back |
|---|---|---|
| `get_info` | The machine description and the limits (part 6) | `True, (description, setup, chooses_its_own_folder)`: the microscope in plain words, for whoever drives it, a person or a program, what is set up once on it, and whether its software always saves in its own place |
| `get_actuators` | The machine description | `True, (x, y, z)`: per axis `{"options": [...], "default": ...}`, the motors and the one the driver chooses when a command does not |
| `get_xyz` | The reading for position (part 3) | `True, (x, y, z, actuators, canvas)`: the position in user coordinates, every motor's raw reading, and everywhere a picture can show |
| `set_xyz` | The change for position (part 3), then `get_xyz` | The same as `get_xyz`, read from the microscope once the stage has arrived |
| `get_state` | The readings for the settings | `True, (changeable, observed)`: the settings `set_state` can apply, and what can only be read |
| `set_state` | One change per setting | `True, applied`: what took |
| `get_acquisition_settings` | How a capture can be triggered | `True, {name: {"options": ..., "default": ..., "active": ...}}`, usually only `trigger` |
| `acquire` | The `acquire` procedure, then `save_data` (never `clear_cache`) | `True, (files, planes)`: where everything was saved |
| `get_procedures` | The procedures' own descriptions (part 4) | `True, {name: {"description": ...}}` |
| `run_procedure` | One procedure | `True, name` |

**Three outcomes.** Every method hands back a pair. `True` and the values
means the microscope did what was asked. `False` and a message means it did
not, and the message says what happened. A method that raises is the third
outcome, and the controller reports it with the error's text. The controller
turns all three into the one answer a workflow sees, `{"success": ...,
"output": ...}`, so a workflow never has to catch an error itself. This is
how the kinds from the errors table reach the experiment:

- **A request that is wrong raises `ValueError`.** A position outside the
  limits, a setting or a motor this microscope does not have, an acquisition
  setting that is not listed, a procedure name that `get_procedures` does
  not list. The limits gate's refusal (part 2) is such a `ValueError`; the
  method lets it through.
- **A change that could not be confirmed is recorded, and the workflow
  carries on.** The driver confirms what it can: it reads back, re-sends,
  and gives one more look before giving up. When the readback never came,
  the change is not known to have failed, only not known to have happened,
  so the method answers `True`, writes the change down as unconfirmed (in
  the command log, in a warning, and under `observed` in `get_state`, so a
  workflow that cares can look), and goes on. A move answers with the
  position read back from the microscope, so the workflow sees where the
  stage really is. The answer is `False` only when something contradicts
  the change: the readback showed a different value, the vendor refused, or
  the connection dropped. A workflow that must not go on without proof reads
  `get_state`; the driver never stops it on its own. This rule came from a
  real run: a job selection whose readback lagged stopped a whole experiment
  that would have been fine.
- **Anything unsafe is never answered as a success.** A lost connection, a
  permanent fault, a read that timed out while a move was in flight: these
  raise, and the workflow sees `success: False`.

**Rules.**

- `zmart_controller_plugin.py` **only maps** the driver's readings, changes, procedures and data
  handling onto the commands. It does no coordinate
  arithmetic and no safety checks of its own; those already happened further
  down. Reading the mock's class file should feel like reading a table of
  contents of the parts beneath it.
- **`get_xyz` and `set_xyz` answer the same.** A move ends by reading the
  position back from the microscope, so the workflow sees where the stage
  really is, not the numbers it asked for. Under `actuators`, every motor
  of each axis carries its own raw reading, in micrometres and with nothing
  subtracted, so that the workflow can see how the motors share the position.
- **`get_info` is the description a stranger reads.** It says what the other
  commands cannot: what each setting means, in which unit and within which
  bounds, which objective sits in which slot, which way +z points. The ZMART
  AI agent builds its picture of the instrument from this text, so it is
  worth writing well. Bounds come from the limits, so the description never
  promises more than the driver allows.
- **The acquisition type and the position label name the files.** `acquire`
  saves under `<acquisition_type>_<position_label>`, in the output folder it
  was given, and never overwrites an earlier picture with the same name.
- **The connection dictionary is handed to `__init__` as it is.** The driver
  fills in every key that is left out from its own defaults, so a nearly
  empty `zmart_controller_plugin.json` still connects on the microscope computer.

**Checking and installing.** The controller checks and installs a driver by
its `zmart_controller_plugin.json`:

```python
from zmart_controller import mic

mic.validate_driver("C:/drivers/my-scope")   # [] when every answer fits
mic.register_driver("C:/drivers/my-scope")   # once, on the microscope computer
mic.connect("my-scope")                                        # in every session
```

`validate_driver` connects, calls every `get_*` command and checks each
answer against the contract; it moves nothing and acquires nothing. The
driver's own tests check `acquire` with `check_acquire_answer`, since
checking it means taking a picture. `register_driver` writes the driver's folder
into this computer's list of drivers, `drivers.json` under `config_root()`,
and from then on `get_instruments` lists the driver and `connect` finds it by
name.

**The older shape.** Before the class, a driver was a module with one
function per command, `connect` first, each function taking the handle that
`connect` returned and answering `{"success": ..., "output": ...}` itself.
The controller still accepts this shape, and the four drivers in this
repository and the mock still use it, through their `zmart_controller_plugin.py`.
It is the shape to move away from, not to copy: the class keeps the
connection on `self` instead of a handle passed around, and lets the
controller build the answers, so a driver author writes less and the answers
cannot drift between drivers.

## 8. Testing

**Purpose.** To make every other part safe to change, and to let anyone try
the driver on a laptop without a microscope.

```
testing/
    mock_api/      the stand-in for the vendor software
    unit/          tests that run against the mock API, on any computer
    hardware/      checks that run on the real microscope
    data/          sample files: vendor exports, log files, example configuration
```

**The mock API** stands in for the vendor software at the bottom edge of the
vendor interface. Each driver already has one, though at different depths:

| Driver | Mock API |
|---|---|
| Leica | `testing/helpers/mock_lasx_api.py`, an in-memory stand-in for the LAS X client that tracks jobs, stage and focus and returns realistic errors. |
| mesoSPIM | `mock_mesospim_server.py`, a real socket server that speaks the real protocol and runs the driver's own scripts. |
| ZEISS | `fake_gateway.py`, a real gRPC server built on ZEISS's own service definitions, which can also be started on its own. |
| Nikon | `fake_nis_api.py`, an in-memory stand-in. |
| Mock | MockScope Control in `testing/mock_api/`, pretend vendor software with a pretend microscope behind it: it takes time to move, refuses commands while busy, writes its own file format, and can be told to fail in every way the error rules know. |

Between the mock API and the real microscope there can be one more step: a
simulator that the vendor supplies, such as the LAS X simulator, which runs
the real vendor software without a microscope. For integration testing it
gives the same information as the microscope, with one exception: its
pictures are not taken from a sample but come from a loop of stored images.
So the simulator can prove that the driver's commands, readings,
confirmations and saving work end to end, but it cannot prove anything that
depends on what is in the picture, such as autofocus, the orientation
measurement or the objective calibration. Those need the real microscope.
The driver is meant to work on the simulator and on the microscope without a
separate variant for each. If a small difference turns up, it is handled as a
small change in the tuning numbers or in the handling notes of the primitive
concerned, never in the shared dispatcher.

The folder is called `testing/` rather than `tests/` on purpose. The mock API
is used beyond the tests: the controller's mock driver runs on it, and
workflows can be tried offline on it. Python packages often leave a folder
called `tests/` out when they are installed, and then the mock API would be
missing exactly where someone wants it.

A driver's tests live inside the driver, in `unit/` and `hardware/`, so that
each driver has one suite that runs on its own. The mock is the one
deliberate exception: it lives in the controller repository, whose tests all
sit at that repository's top level, so its tests are in `tests/mock_tests/`
there, and only its mock API is in `testing/`.

**Rules.**

- **The driver never imports from `testing/`.** Only tests and the mock driver
  use the mock API. A layer check (Leica's `test_architecture_guard.py` is the
  model) enforces this, together with the "each part only uses the parts
  below it" rule from the picture at the top.
- **Hardware runs start offline.** A hardware run first proves the
  limits gate and the error rules against the mock API, and stops before
  touching the stage if either fails. The Leica driver already does this for
  the limits gate.
- **The mock API can produce every kind of error on purpose**, so that one
  shared set of tests can check that the sorting, the reader and the change loops follow
  the rules.
- **Every suite plugs the driver into the controller and lets the controller
  check the answers**, with `validate_driver` and `check_acquire_answer`.
  That is the one test that proves the driver fits, and it costs nothing to
  keep.

## Shared across drivers

Some code is the same for every microscope and should live once, in a shared
package that all drivers use, instead of being copied into each one. Copies
drift apart, and safety behaviour must not drift.

```
zmart_drivers/
    shared/
        algorithms/        image registration and focus scoring
        ...                later: the dispatcher, configuration loading,
                           OME writers
```

**The dispatcher** is the other clear case, and the reason it has a folder
of its own: everything in it is written once for every microscope, so the
folder can move to the shared package as a whole. The numbers that differ for
one microscope are handed in by its vendor part. The Leica dispatcher already
imports nothing of LAS X; what still has to come with it is the shape of a
command profile and the list of kinds of problem (see "What this means for the
Leica driver").

**The algorithms** are the clearest case today. In a driver they live in
`procedures/` as plain modules (part 4); Leica has them beside the setup items
that use them, `driver_setup/orientation/registration.py` and
`driver_setup/optical_calibration/focus.py`. Nothing in them
is Leica-specific: phase correlation between two images, a vote across four
registration methods, and the Brenner sharpness score work the same on any
microscope. In Leica they are used by the setup procedures (the orientation
measurement and the objective calibration), and every other driver will need
the same for its own. The mock, which lives in the controller repository and
depends on nothing, keeps its own copy of the focus score either way.

**Rules for the shared package.**

- **Algorithms never touch a microscope.** They take images and numbers and
  return numbers, so they can be tested on sample images alone.
- **Drivers use the shared package; the shared package knows nothing about any
  driver.**
- **Only move something into the shared package once two drivers actually use
  it the same way.** Until then it stays inside the driver, even if it looks
  similar to something shared. This keeps us from building general solutions
  for cases we have not seen yet.

What is the same in every driver, and what differs:

| Part | The same everywhere | Specific to each microscope |
|---|---|---|
| Vendor interface | The three promises; the names of the kinds of problem | Everything inside, including the sorting of errors |
| Dispatcher | Everything: both engines, the limits gate, the error vocabulary and rules, the routes mechanism | Nothing (the numbers live in the handling notes) |
| Operations (`operations/`) | The shape of an entry | Which primitives, in which order, how to confirm, how long to wait |
| Procedures | The "only through the dispatcher" rule; the algorithms | The recipes |
| Saving | OME-Zarr writing, naming, the command log | Finding and reading the vendor's raw product, and its description of it |
| Configuration | Load, check and save; the notebook pattern | Default values; the machine description |
| `ZmartControllerPlugin` | The contract: the methods, their answers and the three outcomes; `validate_driver` | Mapping the dispatcher and the procedures onto those methods; the `connection` keys |
| Testing | The dispatcher and error-rule tests; offline before hardware | The mock API; the hardware checks |

## Experimental code

Every driver may have an `experimental/` folder for ideas that are not yet
trusted. Code there is left out of hardware validation and must not be used
by any other part of the driver. The Leica driver has none at the moment.

## Settled since the first version

- **Unconfirmed results.** A change that could not be confirmed is recorded
  and the workflow carries on; only a contradicted, refused or lost change is
  a failure (part 7). The mock and the Leica driver both do this, and the
  controller's guide says so. An earlier version of this document had it
  the other way round, with every unconfirmed change a failure, and that
  rule stopped a real experiment on a job selection whose readback lagged.
- **Limits in raw coordinates.** The limits are stored and checked in raw
  stage coordinates, so that recording a new origin cannot move the safe
  travel range. The mock's limits gate works this way, and the Leica driver's
  physical backstop already did. How Leica's `limits.json` is interpreted
  today still needs to be confirmed before its gate is moved into the
  dispatcher.
- **How a driver plugs in.** By its `zmart_controller_plugin.json`, through
  `register_driver` and `connect` by name, with a `ZmartControllerPlugin` class in
  `zmart_controller_plugin.py` (part 7).
- **Where error handling lives.** It is not a part of its own. The sorting of
  errors sits in the vendor interface, since only vendor code knows what the
  vendor's messages mean, and the rules sit in the dispatcher, since the
  dispatcher is the only thing that follows them. The kinds table above is
  the seam between the two.
- **The vendor side.** Code that runs inside the vendor software lives in
  `primitives/vendor_side/`, standard library only, importing nothing
  of the driver but the shared message format.
- **Actions and the dispatcher are separate folders.** The dispatcher is the
  same for every microscope and will move to the shared package; the actions
  are what differs. Readings and changes are two files in one folder, not two
  folders. (Replaced in October 2026: the actions folder is gone; see the last
  item below.)
- **A skipped dependent step is a failure.** When a step that depends on an
  unconfirmed or failed step is not run, the command answers `success: False`
  with a message naming it. It is a recorded result, not a crash.
- **Handling notes may hold code.** The few confirmations that do not fit one
  row of data are small functions in the vendor interface with a fixed shape,
  which the dispatcher calls without looking inside.
- **Never break the workflow.** A command records a failure, writes it in the
  logs and goes back to the workflow; it does not stop the run. A step that
  depends on a failed step does not run as if it had succeeded. See "What a
  command answers: success and confirmed".
- **One read at a time, and one point of trust.** A reading asks one source at
  a time and believes it; fallback is only for a source that cannot answer.
  Several sources may share a tier only in a confirmation, and even then they
  are looked at in turn.
- **Pinned.** A driver is pinned to one microscope and one software version.
  The Leica driver may rely on LAS X's hardware server, which is part of LAS X.
- **The answer key is `output`.** The controller wraps every answer as
  `success` and `output`; it was `content` before controller 0.4.0rc2.
- **Simplified anatomy (October 2026).** No `actions/` folder, no `output/`
  folder, OME-Zarr only, handling notes in the vendor interface, and a
  fully shared dispatcher with routes, tiers and fallback only on error. See
  "The simplified anatomy".

## Open questions

- **The family names.** The design above calls the families direct, API, log,
  capture and interface. If a driver needs the Windows COM interface as a family of its
  own, it would be added to the list. Which name fits the Leica CAM API best
  should be settled before the files are renamed.

- **Required metadata (later).** Metadata handling comes after the primitives,
  and it is adapted to each microscope. Which fields every driver must fill in
  the standard metadata, and how "unknown" is written, are decided then. Until
  then, check how much of the OME-Zarr writer relies on the Leica OME metadata
  code.

- **Two programs using the microscope at once.** A setup notebook, the
  operator page and a workflow could all try to connect together. The rule is
  one writer at a time, but not who may connect, or what happens when a second
  one tries. Stop (below) is part of the same question.

- **Existing saved settings.** Operators already have limits, calibration and
  origin saved on the LAS X computer, in folders whose layout and notebook
  names this design changes. The refactor must keep loading them, or migrate
  them, so that no one has to redo a calibration.

- **Which settings produced a picture.** Each acquisition should record which
  limits and calibration snapshot it ran with, so that a picture can be traced
  afterwards. This is cheap to add early, before the metadata step.

- **Cross-cutting needs.** Three things every driver needs are not designed
  yet. *Stop*: a way to interrupt a running command (see below). *Dialogs*: the
  vendor software can pop up message boxes that block it, and Leica has about
  500 lines to handle them, yet the design mentions dialogs only for the
  interface family. *Who may connect*: see "Two programs using the microscope at
  once" above.

- **Proposals waiting for a decision.** "Taking turns with the vendor
  software", "The running log" and the limit description are written as
  proposals in "The simplified anatomy".
  Each is marked there.

- **Axes that are neither x, y, z nor settings.** The mesoSPIM has a focus
  drive and a rotation axis. Today they are reachable only through
  procedures, which hides them from the controller's vocabulary. Whether they
  become settings, extra actuators, or stay procedures is a question for the
  controller contract, not for this document.

- **Stop.** No driver and no part of the controller contract offers a way to
  stop a running command. If an acquisition or a tile scan goes wrong, the
  only way out today is the vendor software itself. A defined `stop` would
  touch the confirm loop (a long confirmation wait has to be
  interruptible), the rules (a new kind, "stopped by user") and the
  controller contract (a new command). ZEN offers a native stop primitive,
  which makes the gap concrete: using it needs a second caller while a change
  is running, which the "one writer" rule forbids today. This is the largest
  safety gap we know of, and it needs a decision: add it now, or record it
  for a later version of the contract.

## Where we are, and how we get there

1. **The mock driver.** Partly done: `zmart_controller.mock` in the controller
   repository follows the first version of this layout, on its own mock API,
   and its README walks through how a move travels through the parts. It is
   the template a new driver author reads. It still has to be brought into the
   simplified layout (it has `get_actions/`, `set_actions/` and
   `error_handling/` folders and no `dispatcher/`), and it still plugs in
   through the older module shape (`zmart_controller_plugin.py`). It should
   become a `ZmartControllerPlugin` in `zmart_controller_plugin.py` like every other driver.
2. **The contract.** Done: the controller's
   [How do I plug in a ZMART-driver](https://github.com/ZMB-UZH/ZMART-controller/blob/main/docs/plug_in_a_driver/README.md)
   fixes the class, every method's input and output, the three outcomes,
   and the rule for an unconfirmed change. Stop is the one addition still
   open.
3. **Give every driver its two files.** Each of the four drivers here still
   offers the older module shape through its `zmart_controller_plugin.py`,
   and the install steps in their READMEs still point the controller at that
   file, which it no longer accepts. The next step is a `zmart_controller_plugin.json`
   beside each plug-in file, naming it as the class file, so that
   `register_driver` and `connect` by name work again; then a `ZmartControllerPlugin`
   in `zmart_controller_plugin.py` that replaces the plug-in file, driver by driver.
4. **What a review of the four drivers found**, in October 2026, which the
   dispatcher fixes once for all of them. Each driver copied Leica's adapter
   and each drifted the same way: a limits refusal is raised as
   `RuntimeError` instead of `ValueError`; an unconfirmed change was a
   silent success, with nothing written down; a malformed origin file is ignored instead of refused; a
   repeated label overwrites the earlier picture; no command log is kept; the
   vendor software version is reported but never checked. The three strains
   the review found in several drivers at once, a send that already waits, a
   lost reply after sending, and a change that cannot be read back, are now
   in parts 2 and 3 above, and the mock's dispatcher has to carry them before
   the first driver copies it.
5. **Reshape Leica one small step at a time.** Done in October 2026, in this
   order: the moves that change no behaviour (folder and file names), then the
   handling notes, then the recipes, then the changes in behaviour. After
   every step the offline tests passed, and the whole was run on the LAS X
   simulator. It still has to run at the STELLARIS. What still differs from
   the plan is listed in "What this means for the Leica driver".
6. **Prove the abstraction with small fake vendors.** Begun: a pretend vendor
   (`navigator_expert/testing/helpers/pretend_vendor.py`) moves, reads,
   retries and sends once through the shared dispatcher, importing nothing
   else. Building it found two leftovers of LAS X in the dispatcher, both
   fixed: the reader spoke of a job and the API, and the rule that a command
   is sent only once lived in Leica's profile. Still to come: one fake whose
   calls block until done, and one that answers accepted or refused at once.
7. **Bring the Nikon, ZEISS and mesoSPIM drivers into the same layout**, each
   in its own pull request, so that a problem in one does not hold up the
   others.
