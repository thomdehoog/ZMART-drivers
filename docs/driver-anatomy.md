# The anatomy of a ZMART driver

Status: design, October 2026. It describes the parts every ZMART driver
should have, what each part is responsible for, and the rules that connect
them. It was written by comparing the Leica Navigator Expert driver (release
candidate 6.0.0rc1) with the Nikon, ZEISS and mesoSPIM drivers and with the
ZMART Controller. Since then two things have settled. The controller's
contract, the `ZmartDriver` class that every driver fills in, is fixed and
described in the controller's
[How do I plug in a ZMART-driver](https://github.com/thomdehoog/ZMART-controller/blob/main/docs/plug_in_a_driver/README.md);
part 8 of this document follows it. And the controller's mock driver,
`zmart_controller.mock`, has been built in exactly this layout, so there is
a complete, small driver to read alongside this text. The last sections say
what is still open and in which order we plan to get there.

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

## The parts at a glance

A driver has nine parts. Read the picture from bottom to top: each part only
uses the parts below it.

```
  experiments and workflows, through the ZMART Controller
  ───────────────────────────────────────────────────────────────
  8  zmart_driver.py             the ZmartDriver class: one method per command
  5  Procedures                 recipes built from get and set actions
  4  Set actions  ──────────┐   change the microscope, then confirm it
     + set dispatcher        │   (limits gate, retry, confirm, give up softly)
  3  Get actions  ◀─────────┘   ask the microscope something
     + get dispatcher            (one read at a time, time limit, "unknown")
  2  Error handling             sort every problem into a kind, then follow the rule
  1  Vendor interface           the only part that is completely microscope-specific
  ───────────────────────────────────────────────────────────────
  vendor software (LAS X, NIS-Elements, ZEN, mesoSPIM-control)

  alongside:  6 Data handling   7 Configuration   9 Testing
```

Suggested folder layout inside a driver:

```
my_driver/
    zmart_driver.json          # the driver's name and how to reach the microscope
    zmart_driver.py            # the ZmartDriver class the controller calls
    __init__.py                # makes the folder a package, so zmart_driver.py can import the parts
    vendor_interface/
    error_handling/
    get_actions/
    set_actions/
    procedures/
    data_handling/
    configuration/
        origin/
        image_stage_registration/
        limits/
        optical_calibration/
        machine_description/
    testing/
        mock_api/
        unit/
        hardware/
        data/
    experimental/              # ideas that are not yet trusted on hardware
```

The folder names `get_actions/` and `set_actions/` are written out in full
on purpose. A folder called plain `set/` would clash with Python's built-in
`set`, and code that uses `set()` could quietly break.

## Words we use

A few words have a precise meaning in this document. Using them consistently
avoids a lot of confusion.

- **Command**: one of the things a workflow can say to the controller, such
  as `set_xyz` or `acquire`. The list is short and the same for every
  microscope. The driver carries a command out with actions.
- **Action**: anything the driver asks the microscope to do. An action is
  either a *get* or a *set*.
- **Get action**: asks the microscope something, such as the stage position
  or the selected objective. It never changes anything.
- **Set action**: changes the microscope. "Set" is meant broadly here: moving
  the stage, changing a laser power, selecting a job, and acquiring an image
  are all set actions, because each one changes the state of the instrument.
- **Primitive**: one plain Python function offered by the vendor interface,
  such as `read_position()` or `move_xy(x, y)`. Get and set actions are built
  on primitives.
- **Dispatcher**: the general engine that runs an action safely. There is one
  for gets and one for sets.
- **Procedure**: a recipe that combines several get and set actions, such as
  autofocus.
- **Raw stage coordinates**: positions in micrometers exactly as the vendor
  software reports them.
- **User coordinates**: positions in micrometers measured from the recorded
  origin. This is what experiments see.

## 1. Vendor interface

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

**The three promises.** Inside, the vendor interface may look like anything.
At its top edge it keeps three promises:

1. **Plain values.** Primitives take and return plain Python values, with
   positions in micrometers. No C types, macro text, log lines or network
   tokens leak above this line.
2. **Classified errors.** Every failure it raises can be sorted by the error
   handling (part 2) into one of the shared kinds.
3. **Replaceable by the mock API.** Everything above the vendor interface must
   run the same way against the mock API (part 9) as against the real
   microscope.

The list of primitives is not the same for every microscope. Leica thinks in
*jobs*, ZEISS in *experiments*, Nikon in *optical configurations*, and
mesoSPIM has none of these. Forcing one fixed list on all of them would either
be too small to be useful or push the awkward fit up into the actions. The
list that must be the same everywhere lives higher up, in the `ZmartDriver`
class (part 8).

**Where a workaround belongs.** Every microscope needs workarounds. A simple
rule decides where each one goes:

- If it is about **how to talk to this vendor** (converting to C types,
  running code inside NIS, parsing a log line, a call that only works after
  selecting a job first), it belongs in the vendor interface.
- If it is about **what a value means** (for example, Leica's focus position
  is the sum of the z-wide and z-galvo drives), it belongs in a get or set
  action.
- If it is **a recipe of several get and set actions**, it is a procedure.

## 2. Error handling

**Purpose.** To decide, in one place, what happens when something goes wrong.
Without this, every action grows its own if-else rules about error messages,
and two actions end up treating the same problem differently.

Error handling has two pieces:

- **The classifier** is written per microscope. It takes whatever the vendor
  reported (an echo text, a gRPC status code, captured error output) and
  sorts it into one of the shared kinds below.
- **The rules** are the same for every microscope. For each kind, they say
  what the get dispatcher and the set dispatcher should do next.

The dispatchers never interpret error messages themselves. They ask the
classifier what kind of problem this is, and then follow the rule.

**The shared kinds and rules.**

| Kind | Example | Get dispatcher | Set dispatcher | What the `ZmartDriver` method answers |
|---|---|---|---|---|
| Refused by limits | A target outside the travel range | – | Stops before anything is sent | Raises `ValueError` |
| Bad request | An unknown option; the vendor says "is invalid" | Raises | Does not retry | Raises `ValueError` |
| Temporary | The software is busy; a short timeout | Reads again | Sends again, up to a set number of times | Raises `RuntimeError`, only if every retry fails |
| Permanent | The vendor reports a failure; a hardware fault | Raises | Does not retry | Raises `RuntimeError` |
| Unknown reading | A stale log entry; a read that timed out | Returns "unknown" with the reason | Counts as "not confirmed yet" | – |
| Unconfirmed | The action was accepted, but the readback never matched | – | Sends again, then gives up softly | `False` and a message saying what could not be confirmed |
| Connection lost | The vendor software was closed | Raises | Raises | Raises `RuntimeError` |
| Stopped by user | See "Stop" under open questions | – | Stops waiting | To be decided |

The last column is what reaches the controller. The controller turns every
one of these into the same answer for the workflow, `success: False` with
the message as its content (a raised error is reported with its text, for
example `ValueError: x=12000 is outside the travel`). The kinds differ in
what the driver does *before* it answers, not in the shape of the answer.
Part 8 says more about the answers.

**Rules that always hold.**

- **An error we do not recognise counts as permanent.** Retrying something we
  do not understand could repeat a harmful action. The Leica driver already
  works this way: its classifier checks the permanent patterns first and
  treats anything unrecognised as permanent.
- **Error messages name keys, never values.** The connection settings can
  contain passwords, so a message may say "the key `password` is missing" but
  never print what a key holds.
- **How many times** to retry and **how long** to wait may be tuned per
  action. **What to do** for each kind of error is fixed by the table.

## 3. Get actions and the get dispatcher

**Purpose.** To give one honest reading of the microscope: the value, where it
came from, and how old it is, or "unknown" when the driver cannot be sure.

**The get dispatcher** is the general engine behind every reading. It:

- lets only one read reach the vendor software at a time, so reads do not pile
  up on a busy microscope;
- applies a time limit and answers "unknown" instead of hanging;
- rejects readings that are too old;
- chooses between sources when a microscope offers more than one. Leica can
  read from the API and from the log file, and races the two. Most
  microscopes have a single source, and the dispatcher must not assume two.

In the Leica driver this engine exists today as `readers/router.py`; in the
mock it is `get_actions/dispatch.py`.

**Get actions** are short definitions that use the dispatcher: which
primitive to call, and what the value means.

**Rules.**

- A get action never changes the microscope.
- A get action never knows a target. It does not know what value anyone is
  hoping for.
- The get dispatcher retries **the read**, never the hardware.

## 4. Set actions and the set dispatcher

**Purpose.** To change the microscope safely, and to know afterwards whether
the change really happened.

**The set dispatcher** runs every set action through the same steps:

1. **Limits gate.** Check the request against the limits from the
   configuration. If it is outside, refuse before anything is sent.
2. **Pre-check.** Ask the get dispatcher whether the microscope is ready, for
   example whether the scanner is idle.
3. **Send** the action through a primitive.
4. **Error check.** Classify any error and follow the rule: retry a temporary
   error, stop on anything else.
5. **Confirm.** Ask the get dispatcher, again and again within a time window,
   whether the value has reached the target.
6. **Send again** if the confirmation did not succeed, up to a set number of
   attempts.
7. **Give up softly** if it is still not confirmed: hand back an outcome that
   says the action is unconfirmed and why, instead of raising. The
   `ZmartDriver` method then decides what that means for the command (part
   8): a setting that could not be confirmed is answered as a failure that
   names the setting, and a move that could not be confirmed is answered as a
   failure that says where the stage is, because carrying on at an unknown
   position is never safe.

In the Leica driver this engine exists today as `confirm_and_fire` in
`commands/dispatch.py`; in the mock it is `set_actions/dispatch.py`.

**Set actions** are short definitions that use the dispatcher. Each one says
which primitive to call and how to confirm it: which reading to check, the
target, the tolerance, and how long to wait. Most confirmations follow that
simple pattern and can be written as a single row of data, the way Leica's
`confirm_specs.py` already does. A few, such as acquisition, an objective
change or a z-stack, need their own code, and that is fine.

**Rules.**

- **Every set action goes through the set dispatcher**, and the limits gate
  is inside the dispatcher. No set action can skip the gate, because there is
  no other way to reach the hardware. (In Leica today each wrapper calls the
  gate itself; moving it into the dispatcher removes the chance of forgetting
  it. The mock's `set_actions/gate.py` is the model.)
- **When the limits could not be loaded, the gate refuses every change.** An
  unknown limit is never treated as "no limit".
- **The set dispatcher uses the get dispatcher**, for the pre-check and for the
  confirmation. A get never calls a set.
- **A single read must fit inside one confirmation window.** Otherwise the set
  dispatcher gives up before the reading arrives, or an abandoned read is
  still running when the next attempt starts. The Leica driver learned this
  the hard way (finding CF-05 in `select_job`).
- **One read at a time is managed per read, not per confirmation.** When Leica
  held that rule around a whole confirmation, the confirmation's own reads
  were blocked (finding CF-01). Only the get dispatcher manages it.
- **One writer at a time.** Set actions assume a single caller. A workflow
  that sends commands from several threads at once can mix up the results.
- **Tuning numbers** (retries, time windows, time limits) live in one named
  file next to the dispatchers (`set_actions/tuning.py` in the mock). The
  driver author sets them; the operator never needs to.

## 5. Procedures

**Purpose.** To offer recipes that combine several steps, such as autofocus,
backlash takeup (always finishing a move from the same side, so that
positions repeat), or parking the z-galvo at zero while keeping the focus.

**Rules.**

- **A procedure uses only get and set actions**, never the vendor interface
  directly. Every step then passes the limits gate and the error rules
  without any extra effort.
- **Each procedure describes itself** with a name and a plain-language
  description. That is what `get_procedures` lists and `run_procedure` runs.
- A procedure that needs image analysis, such as a software autofocus, uses
  the shared algorithms (see "Shared across drivers" below).

## 6. Data handling

**Purpose.** To turn what the microscope produced into ZMART's product, and to
say exactly where it was saved. "Data" here means the image data and the
metadata that travels with it. Configuration is not data in this sense; it
has its own part.

Acquiring the image is a set action (part 4): it starts the capture and
confirms that the capture finished. Data handling starts after that.

**Responsibilities.**

1. **Find** what the vendor software produced: files in a folder, or image
   data handed over directly.
2. **Wait until it is complete.** A file must have stopped growing and the
   export must have finished, so that we never read a half-written file.
3. **Convert** it to flat OME-TIFF (one plane per file) or OME-Zarr.
4. **Name and place** it in the experiment's folder layout, named after the
   position label. A second picture with the same label gets a new name; an
   earlier one is never overwritten. Any further grouping is the driver's
   choice, offered as an acquisition setting (the mock offers `folder`).
5. **Attach the metadata**: the position in user coordinates, the instrument
   state, and the pixel size.
6. **Keep the command log**: for each acquisition, what was asked, what
   happened, how many attempts it took, and whether it was confirmed. The
   dispatchers already produce this information; data handling saves it next
   to the images so the experiment can be traced afterwards.
7. **Report** what was saved, in the two lists `acquire` answers with:
   `files`, the path of every file, and `planes`, one entry per image plane
   with its file, its channel, depth and time index, and the stage position
   it was taken at, in user coordinates.

Problems here go through the same error handling. A file that never appears
after a confirmed capture is a permanent error, and `acquire` then answers
with empty lists and `False`.

## 7. Configuration

**Purpose.** To hold everything the person at the microscope sets up and
saves, and to load it every time the driver connects.

| Folder | What it holds | How often it changes |
|---|---|---|
| `origin/` | The point that reads as (0, 0, 0) in user coordinates. | Often, for example for each new sample. |
| `image_stage_registration/` | How image pixels relate to stage movement: which way each axis points, flips and 90° turns, and the pixel size. | Rarely: at installation, or after hardware changes. |
| `limits/` | How far each axis may travel, and the allowed values for each setting. | Rarely. |
| `optical_calibration/` | The x, y and z offsets between objectives, so that a change of objective keeps the same spot in view. | Rarely. |
| `machine_description/` | The fixed facts about this instrument: its objectives, detectors, axes, motors and serial number, and the vendor software versions the driver was tested with. | Only when the hardware changes. |

The origin has its own folder because recording it is a frequent, everyday
step, while the registration is measured rarely. Someone recording a new
origin should never be one notebook cell away from overwriting the
registration.

**Every item follows the same pattern:**

- **Defaults** shipped with the driver in the repository.
- **Load, check and save**: the saved copy lives under the computer's ZMART
  configuration folder, which `zmart_controller.registry.config_root()` names
  (`C:\ProgramData\zmart-microscopy` on Windows). A malformed file is refused,
  not guessed at. Saved copies never go into the repository.
- **A notebook** that walks the operator through setting it.

**Order at connect.** Connecting is making the `ZmartDriver` (part 8): its
`__init__` opens the vendor software and loads the configuration. Each item
depends on the one before it, so it loads them in this order: machine
description, image-to-stage registration and origin, limits, optical
calibration. Loading the limits is what switches on the limits gate. After
loading, the driver compares what the microscope reports with the machine
description: a serial number that belongs to another instrument is refused,
and an untested software version is warned about. The mock's `__init__`
(today its `connect`) shows these steps in order.

**The coordinate system.** The arithmetic between raw stage coordinates and
user coordinates (subtracting the origin, applying the registration, adding
the objective offsets) lives once, as a pair of plain functions next to this
configuration (`user_from_raw` and `raw_from_user` in the mock's
`configuration/coordinates.py`). The get and set actions for position use
these functions. That way:

- everything above the actions (procedures, `zmart_driver.py`, experiments)
  speaks one coordinate system, the user's;
- the limits gate checks raw stage coordinates, so recording a new origin can
  never move the safe travel range;
- `zmart_driver.py` does no arithmetic of its own.

Today the Leica driver does this arithmetic inside its controller adapter.
Moving it down into the actions means procedures and setup notebooks can no
longer accidentally use a different coordinate system from the experiments.

## 8. The `ZmartDriver` class

**Purpose.** To present the driver to the ZMART Controller in the shape every
microscope shares.

The controller fixes this shape, and it is described call by call, with the
exact inputs and outputs, in the controller's
[How do I plug in a ZMART-driver](https://github.com/thomdehoog/ZMART-controller/blob/main/docs/plug_in_a_driver/README.md#3-writing-a-driver).
This section says how that shape sits on top of the parts above, and which
rules the class follows. When the two disagree, the controller's document
wins, and this one needs updating.

**Two files.** A driver is, to the controller, a folder with two files:

- `zmart_driver.json` holds the driver's name (what `get_instruments` lists
  it under, such as `stellaris`), the name of the class file, and the
  `connection`: how to reach the microscope on this computer. The keys the
  controller knows are `microscope`, `api_type`, `host`, `password` and
  `config`; a driver may add its own, such as where images go. Every key is
  optional, and the driver fills in what is left out.
- `zmart_driver.py` holds the `ZmartDriver` class. The controller makes one
  when it connects, and calls the method of the same name for every command.

The controller's `template/` folder holds both files, with every method left
to fill in. A new driver starts by copying it.

**Connecting.** Making the class is the connection: `__init__(connection)`
opens the vendor software with what is in the `connection` dictionary and
loads this microscope's configuration (part 7). `disconnect` closes the
vendor software again. Everything the class needs between calls, such as
the vendor connection, the two dispatchers and the command log, lives on
`self`.

**The commands.** After `__init__` and `disconnect`, the class has ten
methods, and each one only puts the parts above to work:

| Method | Built from | Hands back |
|---|---|---|
| `get_info` | The machine description and the limits (part 7) | `True, description`: the microscope in plain words, for whoever drives it, a person or a program |
| `get_actuators` | The machine description | `True, (x_motors, y_motors, z_motors)`: the motors per axis, the first one being the default |
| `get_xyz` | The get action for position (part 3) | `True, (x, y, z, actuators, canvas)`: the position in user coordinates, every motor's raw reading, and everywhere a picture can show |
| `set_xyz` | The set action for position (part 4), then `get_xyz` | The same as `get_xyz`, read from the microscope once the stage has arrived |
| `get_state` | The get actions for settings | `True, (changeable, observed)`: the settings `set_state` can apply, and what can only be read |
| `set_state` | One set action per setting | `True, applied`: what took |
| `get_acquisition_settings` | The choices data handling offers (part 6) | `True, {name: {"options": ..., "active": ...}}` |
| `acquire` | The set action for acquisition, then data handling | `True, (files, planes)`: where everything was saved |
| `get_procedures` | The procedures' own descriptions (part 5) | `True, {name: {"description": ...}}` |
| `run_procedure` | One procedure | `True, name` |

**Three outcomes.** Every method hands back a pair. `True` and the values
means the microscope did what was asked. `False` and a message means it did
not, and the message says what happened. A method that raises is the third
outcome, and the controller reports it with the error's text. The controller
turns all three into the one answer a workflow sees, `{"success": ...,
"content": ...}`, so a workflow never has to catch an error itself. This is
how the kinds from part 2 reach the experiment:

- **A request that is wrong raises `ValueError`.** A position outside the
  limits, a setting or a motor this microscope does not have, an acquisition
  setting that is not listed, a procedure name that `get_procedures` does
  not list. The limits gate's refusal (part 4) is such a `ValueError`; the
  method lets it through.
- **A change that could not be confirmed is a failure, `False` and a
  message.** It is never `True` with a flag. The set dispatcher gave up
  softly (part 4); the method says plainly what did not take. For `set_state`
  that is which setting, so the workflow can decide whether to carry on. For
  `set_xyz` that is where the stage is, and the workflow should stop, because
  the sample is not where the experiment thinks it is. For `acquire` it is a
  capture that never produced a complete file, answered with empty `files`
  and `planes`.
- **Anything unsafe is never answered as a success.** A lost connection, a
  permanent fault, a read that timed out while a move was in flight: these
  raise, and the workflow sees `success: False`.

**Rules.**

- `zmart_driver.py` **only maps** the driver's get actions, set actions,
  procedures and data handling onto the commands. It does no coordinate
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
- **A position label names the files.** `acquire` saves under the label it
  was given and never overwrites an earlier picture with the same label.
- **The connection dictionary is handed to `__init__` as it is.** The driver
  fills in every key that is left out from its own defaults, so a nearly
  empty `zmart_driver.json` still connects on the microscope computer.

**Checking and installing.** The controller checks and installs a driver by
its `zmart_driver.json`:

```python
from zmart_controller import mic

mic.validate_driver("C:/drivers/my-scope/zmart_driver.json")   # [] when every answer fits
mic.register_driver("C:/drivers/my-scope/zmart_driver.json")   # once, on the microscope computer
mic.connect("my-scope")                                        # in every session
```

`validate_driver` connects, calls every `get_*` command and checks each
answer against the contract; it moves nothing and acquires nothing. The
driver's own tests check `acquire` with `check_acquire_answer`, since
checking it means taking a picture. `register_driver` writes the JSON's path
into this computer's list of drivers, `drivers.json` under `config_root()`,
and from then on `get_instruments` lists the driver and `connect` finds it by
name.

**The older shape.** Before the class, a driver was a module with one
function per command, `connect` first, each function taking the handle that
`connect` returned and answering `{"success": ..., "content": ...}` itself.
The controller still accepts this shape, and the four drivers in this
repository and the mock still use it, through their `zmart_controller_plugin.py`.
It is the shape to move away from, not to copy: the class keeps the
connection on `self` instead of a handle passed around, and lets the
controller build the answers, so a driver author writes less and the answers
cannot drift between drivers.

## 9. Testing

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
| Leica | `mock_lasx_api.py`, an in-memory stand-in for the LAS X client that tracks jobs, stage and focus and returns realistic errors. |
| mesoSPIM | `mock_mesospim_server.py`, a real socket server that speaks the real protocol and runs the driver's own scripts. |
| ZEISS | `fake_gateway.py`, a real gRPC server built on ZEISS's own service definitions, which can also be started on its own. |
| Nikon | `fake_nis_api.py`, an in-memory stand-in. |
| Mock | MockScope Control in `testing/mock_api/`, pretend vendor software with a pretend microscope behind it: it takes time to move, refuses commands while busy, writes its own file format, and can be told to fail in every way the error rules know. |

The folder is called `testing/` rather than `tests/` on purpose. The mock API
is used beyond the tests: the controller's mock driver runs on it, and
workflows can be tried offline on it. Python packages often leave a folder
called `tests/` out when they are installed, and then the mock API would be
missing exactly where someone wants it.

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
  shared set of tests can check that both dispatchers follow the error rules.
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
        ...                later: the dispatchers, the error rules,
                           configuration loading, OME writers
```

**The algorithms** are the clearest case. Today they live inside the Leica
driver (`algorithms/registration.py` and `algorithms/focus.py`), but nothing
in them is Leica-specific: phase correlation between two images, a vote
across four registration methods, and the Brenner sharpness score work the
same on any microscope. In Leica they are used only by setup (the
orientation measurement and the objective calibration), and every other
driver will need the same for its own setup notebooks. The mock's autofocus
carries its own copy of the focus score for now, marked as such.

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
| Vendor interface | The three promises | Everything inside |
| Error handling | The rules | The classifier |
| Get actions | The get dispatcher | Which primitive, and what the value means |
| Set actions | The set dispatcher and limits gate | Which primitive, the target, the confirmation |
| Procedures | The "get and set actions only" rule; the algorithms | The recipes |
| Data handling | OME-TIFF and OME-Zarr writing, naming, the command log | Finding and reading the vendor's raw output |
| Configuration | Load, check and save; the notebook pattern | Default values; the machine description |
| `ZmartDriver` | The contract: the methods, their answers and the three outcomes; `validate_driver` | Mapping actions onto those methods; the `connection` keys |
| Testing | The dispatcher and error-rule tests; offline before hardware | The mock API; the hardware checks |

## Experimental code

Every driver may have an `experimental/` folder for ideas that are not yet
trusted. Code there is left out of hardware validation and must not be used
by any other part of the driver. The Leica driver already works this way.

## Settled since the first version

- **Unconfirmed results.** A change that could not be confirmed is answered
  as a failure, `False` and a message, never as a success with a flag. The
  controller's contract says so, and the mock does it (part 8). The Leica
  driver still reports `success: True` with `confirmed: False` and must be
  brought in line.
- **Limits in raw coordinates.** The limits are stored and checked in raw
  stage coordinates, so that recording a new origin cannot move the safe
  travel range. The mock's limits gate works this way, and the Leica driver's
  physical backstop already did. How Leica's `limits.json` is interpreted
  today still needs to be confirmed before its gate is moved into the
  dispatcher.
- **How a driver plugs in.** By its `zmart_driver.json`, through
  `register_driver` and `connect` by name, with a `ZmartDriver` class in
  `zmart_driver.py` (part 8).

## Open questions

- **Stop.** No driver and no part of the controller contract offers a way to
  stop a running command. If an acquisition or a tile scan goes wrong, the
  only way out today is the vendor software itself. A defined `stop` would
  touch the set dispatcher (a long confirmation wait has to be
  interruptible), the error rules (a new kind, "stopped by user") and the
  controller contract (a new command). This is the largest safety gap we
  know of, and it needs a decision: add it now, or record it for a later
  version of the contract.

## Where we are, and how we get there

1. **The mock driver.** Done: `zmart_controller.mock` in the controller
   repository is built in this layout, part for part, on its own mock API,
   and its README walks through how a move travels through the parts. It is
   the template a new driver author reads. One step remains: it still plugs
   in through the older module shape (`zmart_controller_plugin.py`), and
   should become a `ZmartDriver` in `zmart_driver.py` like every other driver.
2. **The contract.** Done: the controller's
   [How do I plug in a ZMART-driver](https://github.com/thomdehoog/ZMART-controller/blob/main/docs/plug_in_a_driver/README.md)
   fixes the class, every method's input and output, the three outcomes,
   and the rule for an unconfirmed change. Stop is the one addition still
   open.
3. **Give every driver its two files.** Each of the four drivers here still
   offers the older module shape through its `zmart_controller_plugin.py`,
   and the install steps in their READMEs still point the controller at that
   file, which it no longer accepts. The next step is a `zmart_driver.json`
   beside each plug-in file, naming it as the class file, so that
   `register_driver` and `connect` by name work again; then a `ZmartDriver`
   in `zmart_driver.py` that replaces the plug-in file, driver by driver.
4. **Move the shared parts out of Leica one at a time** (the algorithms, then
   the set dispatcher and the error rules), keeping Leica's tests green after
   every step.
5. **Bring the Nikon, ZEISS and mesoSPIM drivers into the same layout**, each
   in its own pull request, so that a problem in one does not hold up the
   others.
