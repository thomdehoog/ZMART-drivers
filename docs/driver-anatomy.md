# The anatomy of a ZMART driver

Every ZMART driver has the same files and folders, with the same names and the
same jobs. This page describes them. The Leica driver,
`zmart_drivers/leica/stellaris/navigator_expert/`, is the first driver built
this way and the example to read beside the text. A new driver copies its
layout.

What a new microscope brings to the shared dispatcher is on one page:
[the vendor contract](vendor-contract.md). The Nikon driver,
`zmart_drivers/nikon/nis_elements_ar610/`, is the second driver in this
anatomy, and the smaller one to read first. The controller's side, the
`ZmartControllerPlugin` class, is fixed by the controller's
[How do I plug in a ZMART-driver](https://github.com/ZMB-UZH/ZMART-controller/blob/main/docs/plug_in_a_driver/README.md).

## The tree

```
<driver>/
  __init__.py                connect() and Microscope, nothing else
  methods.py                 what the user calls: connect(), the Microscope, its Session
  procedures/                chains of steps; one file each, a folder when it needs more
  dispatcher/                the same in every driver:
      dispatch.py            run(): a reading or a write, and the shape of a result
      read.py                the read loop
      write.py               the write loops: send, then confirm
      limits.py              the limits gate
  primitives/                one folder per class of primitive, nothing else
      <class>/               its transfer, its reads, its writes and one profile
  driver_setup/              what the operator sets up once: limits, origin,
                             orientation, optical calibration, data location;
                             and wizard/, the window that walks through them
  zmart_controller_plugin/   the ZMART Controller's view of methods.py
  testing/                   the stand-in for the vendor software and the tests
  README.md
```

## The rules

- **The dispatcher is the same in every driver.** It knows no microscope and
  imports nothing of the driver. A driver copies it unchanged.
- **A primitive is one read or one write through a transfer.** Each class of
  primitive has one profile. The profile tells the dispatcher how to handle
  that class. What differs per microscope is how many classes there are and
  what they are called.
- **Primitives import only the dispatcher**, and other primitives. Never a
  procedure, never `methods.py`.
- **Anything that does not reach the vendor software through a primitive is a
  procedure.** Saving files is a procedure. Working the vendor's window, or
  its file cache, is a procedure.
- **Everything the user does starts in `methods.py`.**
- **A folder only where there is more than one file.**

`testing/unit/test_architecture_guard.py` checks these rules. A driver that
leaves the anatomy fails it.

## The model

```
methods ──► procedures ──► dispatcher run() ──► primitives ──► vendor software
   │            │               ▲
   └────────────┼───────────────┘
                └── some procedures do not use the dispatcher
driver_setup        set up once by the operator, loaded at connect
```

| Part | What it is |
|---|---|
| methods | What a user calls: `connect()` and the `Microscope`. Each method calls a procedure or the dispatcher. |
| procedures | Chains of steps. Most run their primitives through the dispatcher's `run()`. |
| dispatcher | Runs one primitive. A reading runs the read loop. A write asks the limits once, then runs the write loops. |
| primitives | One read or one write each, grouped in classes. Each class has its transfer and its profile. |
| driver_setup | Limits, origin, orientation, optical calibration, data location. |

## Words

| Word | Meaning |
|---|---|
| Reading | A question to the microscope, such as where the stage is. It changes nothing. |
| Write | A change to the microscope: a move, a setting, a job, a picture. |
| Primitive | One read or one write through a transfer. It does not retry, check limits or confirm. |
| Class | The primitives that reach the vendor software the same way, for example its API or its log. |
| Transfer | How a class gets a request to the vendor software and its answer back. |
| Profile | What the dispatcher needs to handle a class: how to ask it, what counts as an answer, how to send. |
| Read order | Which class a reading asks first, and which answers when that one cannot. |
| Read-back | Reading the state again after a write, to see that it took effect. |
| Confirmed | The read-back showed the change. Unconfirmed: the windows ran out without it. |
| Limits gate | The check in the dispatcher that refuses a write before anything is sent. |
| Session | What the driver remembers about one connection. |
| Raw coordinates | Stage positions in micrometres, as the vendor software reports them. |
| User coordinates | Positions in micrometres from the saved origin. Experiments see these. |

## The dispatcher

The dispatcher has one entry:

```python
dispatch.run(profile, client, primitive, *, max_retries=None, dialogs=None, **asked)
```

`profile` is everything of the microscope the dispatcher needs. `primitive` is a
reading, given by its name, or a write, given as plain data. Everything the
dispatcher asks of the profile and of a write is listed in
[the vendor contract](vendor-contract.md).

### One waiting rule

Every wait follows one rule: a number of windows of a set length (Leica: four
windows of three seconds). Within a window the sources are looked at again
every `POLL_S`. Between windows something is sent again where that is safe.
After the last window a reading is unknown and a write is unconfirmed. The
numbers come from the profile.

### A reading

A reading is run by its name: `run(profile, client, "xy")`.

1. The profile names the reading's read order: the class it asks first, and
   the class that answers when that one cannot.
2. A class the profile names to go first is asked alone, in the caller's own
   thread (Leica: the direct reader). When it cannot answer, the next class
   answers within the same time.
3. Otherwise the classes the read order allows are asked one after the other,
   each in its own share of the window. The first answer that counts wins.
4. What counts is declared once per reading. A caller can narrow it with
   `accept`, for example a target value.
5. An answer seen before `observed_after` does not count. A read-back uses this,
   so an answer from before the write cannot confirm it.
6. "Not what we hoped for yet" is an answer, not an error. Only a class that
   cannot answer at all hands over to the next.

The result is a `Reading`: the value, the class that gave it (`source`), when it
was seen and how old it was, and the error when no answer came. When nothing
counted in time, the result is None or a `Reading` with the error.

The named readings (`get_xy(client)`, `get_selected_job(client)`, ...) live in
`procedures/readings.py`. Each passes its name to `run()`.

### A write

A write is plain data. The dispatcher reads these fields of it:

| Field | Meaning |
|---|---|
| `command` | Its name in the limits. |
| `values` | What the limits gate is shown. |
| `request` | The name of the vendor request that carries it. |
| `fill` | Writes the values into the request. |
| `description` | The label in the log. |
| `ready` | A reading to wait for before every send (Leica: the scanner idle). |
| `confirm` | The reading that shows the write took effect, and how close counts. |
| `without_receipt` | Sent without waiting for a receipt; no reply is checked. |
| `never_resend` | Sent once and never again. |

`run()` takes a write through these steps:

1. **Limits.** The gate is asked once, before the request is made. A refused
   write is never made and never sent. The result says why, with `success`
   False.
2. **Request.** The profile makes the request (`make_request`).
3. **Ready.** The `ready` reading is waited for. A write that is not ready
   after the windows is not sent.
4. **Fill and send.** The request is filled in and sent through the transfer.
   The vendor's dialogs, when handed in (`dialogs`), are answered before the
   send and watched for while it runs.
5. **Reply.** The profile checks the reply (`check_reply`). A temporary error
   is sent again, up to `max_retries` times. A permanent error stops. Without
   receipt there is no reply to check.
6. **Read-back.** The `confirm` reading is watched for one window. Only answers
   seen after the write was handed over count. The read-back is the
   dispatcher's. A write never calls the dispatcher.
7. **Send again.** When the window ends without the change, the write waits
   for `ready` and is sent again, up to the profile's number of windows.
8. **Never resend.** A write marked `never_resend` is sent once, gets one
   window, and is never sent again: not after a temporary error, not after an
   unconfirmed window. No caller and no profile can switch this off.

### What a write answers

Every write answers the same dictionary: `success`, `confirmed`, `message`,
`timing`, `logs`, and `route`, the transfer that carried it (None when nothing
was sent).

| `success` | `confirmed` | Meaning |
|---|---|---|
| True | True | Sent, and the read-back showed it. |
| True | False | Sent and accepted, but the read-back never showed it. Recorded; the workflow carries on. |
| True | None | Sent; there was nothing to read back. |
| False | None | Refused by the limits, not ready, refused by the vendor, or not delivered. |

`success` alone does not prove the change took effect. A caller that needs
proof checks `confirmed`. A step that depends on a change that was not
confirmed does not run as if it were, and says so.

### The limits

The gate keeps, for each connection, the limits object the driver installed
when it connected. It asks that object one question:
`check_command(command, values)`. When no limits are installed, every write is
refused. Stage positions are checked in raw coordinates, so a new origin never
moves the safe travel range. The rules themselves are the driver's
(`driver_setup/limits/`).

## The primitives

A class of primitive is one way of reaching the vendor software. Its folder
holds:

| File | What it holds |
|---|---|
| `transfer.py` | How a request reaches the vendor software and how its reply comes back. |
| `reads.py` | The reads of this class, one function each. |
| `writes.py` | The writes of this class, as plain data. |
| `profile.py` | What the dispatcher needs to handle this class. |

A class has only the files it needs. A class that cannot write has no
`writes.py`. A class may have a few more files where its transfer needs them
(Leica: the CAM API's `client.py`).

The profile handed to `run()` is the one that knows the read order. It brings
together what the other classes' profiles say.

## Procedures

A procedure is a chain of steps. Most run their primitives through `run()`, so
every step passes the limits and the error rules. A procedure that does not
reach the vendor software through a primitive (saving files, working the
vendor's window) does its work itself.

A procedure is one file. It becomes a folder only when it needs more than one.
Procedures are handed the `Session`; they do not import `methods.py`.

## methods.py

`methods.py` is what the user calls: `connect()`, the `Microscope` and its
`Session`. `connect()` opens the vendor software, installs the limits, loads
what the operator set up, and hands back a `Microscope`. Each method of the
`Microscope` calls a procedure or the dispatcher. A request that is wrong
raises `ValueError`. A change that failed raises `RuntimeError`. A change that
was sent but never seen is recorded in the `Session`, and the work goes on.

The `Session` holds the open connection and what the driver remembers between
calls: the origin, the calibration and orientation loaded at connect, the
captures not yet saved, and the changes that were never confirmed.

`methods.py` also starts the running log: one file in the configuration
folder that records what the driver sent, what came back and what it decided.
It is renamed at about five megabytes, and the three newest old files are
kept.

## driver_setup

One folder per item the operator sets up once:

| Folder | What it holds |
|---|---|
| `limits/` | How far each axis may travel, and the allowed values of each setting. The rules the gate asks. |
| `origin/` | The point that reads as (0, 0, 0) in user coordinates. |
| `orientation/` | How image pixels relate to stage movement, and the pixel size. |
| `optical_calibration/` | The x, y and z offsets between objectives. |
| `data_location/` | Where the captures are saved when the workflow does not say. |

Each item has its defaults, its loader, the code that measures it, and a
notebook that only calls that code. The saved values live in the computer's
configuration folder (`C:\ProgramData\zmart-microscopy` on Windows), never in
the repository. A malformed file is refused, not guessed at. The run-time
procedures read the saved values through the loader; they never import the
measuring code.

Beside the items, a driver may have a setup wizard, `driver_setup/wizard/`:
one window that walks the operator through the items in order, as their
notebooks do, so a microscope can be set up without opening a notebook. It is
not an item of its own. It holds no measuring code: like a notebook, it only
calls the items' code.

| Path | What it holds |
|---|---|
| `wizard/framework.py` | Steps and the actions the page may call. Knows no microscope. |
| `wizard/steps.py` | This driver's steps, written against the items' own code. |
| `wizard/server.py`, `wizard/launcher.py` | The local web server (this computer only) and the window that shows the page. |
| `wizard/page/` | The built page (HTML, CSS, JavaScript). It ships with the driver, so running the wizard needs only Python. |
| `wizard/page-src/` | The page's source, for changing it; built into `page/` with Node and Vite. Never needed to run the wizard. |

The wizard connects to the microscope the way a user does, through
`methods.connect()`, and shows the driver's version among what it found. So it
is the one part of `driver_setup/` that may import `methods.py` and
`__version__`, and nothing else beyond what `driver_setup/` may import. The
items themselves never import it.

## zmart_controller_plugin

The controller's view of `methods.py`. It holds:

| File | What it holds |
|---|---|
| `zmart_controller_plugin.json` | The driver's name and its connection. |
| `zmart_controller_plugin.py` | The `ZmartControllerPlugin` class: one method per controller command, each a call of the `Microscope`. |
| `info.py` | The description `get_info` answers. |
| `procedure_menu.py` | The procedures `get_procedures` lists and `run_procedure` runs. |

The class does no coordinate arithmetic and no safety checks. Each method
answers `True` and the values, or lets the `Microscope`'s error through. The
controller turns both into `{"success": ..., "output": ...}`.

## testing

```
testing/
    helpers/      the stand-in for the vendor software, and small test helpers
    unit/         tests against the stand-in, on any computer
    hardware/     checks that run at the microscope or the vendor's simulator
    data/         sample files: vendor logs, example configuration
    run_ci.py     runs the offline suite, or the hardware checks
```

The driver never imports from `testing/`. The import guard fails when:

- a part imports a part it may not (the table `ALLOWED`; `driver_setup/wizard/`
  may also import `methods.py` and `__version__`);
- the driver's root holds a folder or file that is not in the tree above;
- `dispatcher/` holds anything but its four files;
- `primitives/` holds a loose file, or a class without a profile;
- a folder in `procedures/` or `primitives/` holds only one file;
- the stage is moved, or the limits are checked, anywhere but in their place.

## Errors

The dispatcher never reads an error message. A class's profile reads the
vendor's reply and says whether the write failed and whether the failure is
temporary. Only a temporary failure is sent again. A message the profile does
not recognise counts as permanent.

| Kind | Example | A write |
|---|---|---|
| Bad request | An unknown option; "is invalid" | Not sent again. |
| Temporary | The software is busy | Sent again, up to `max_retries` times. |
| Permanent | A hardware fault | Not sent again. |
| Connection lost | The vendor software was closed | Fails. |
| Refused by limits | A target outside the travel | Not sent. |
| Unknown reading | A read that timed out | Counts as "not confirmed yet". |
| Unconfirmed | Accepted, never seen | Sent again between windows, then recorded. |

## The Leica driver

The Leica driver is the worked example. It reaches LAS X three ways.

### Its tree

```
navigator_expert/
  __init__.py
  methods.py                     connect(), the Microscope, its Session, the running log
  procedures/
      changes.py                 the named changes: move_xy, move_z, set_objective,
                                 change (a job setting), select_job, acquire, take_picture
      readings.py                the named readings: get_xy, get_selected_job, ...
      absolute_coordinate_system.py  position, move_to, motors, zero_z_galvo
      keep_in_view.py            an objective or job change that keeps the sample in view
      acquire.py                 take a picture, and the note kept for saving it
      state.py                   get_state, set_state
      answer_dialogs.py          notice a LAS X warning and press OK
      clear_cache/               let LAS X go of saved pictures: clear.py,
                                 data_container.py, navigator.py
      save_data/                 read the cache folder, write OME-Zarr
  dispatcher/                    dispatch.py, read.py, write.py, limits.py
  primitives/
      cam_api/                   client.py, transfer.py, reads.py, writes.py,
                                 parsing.py, profile.py
      direct/                    client.py, transfer.py, reads.py, writes.py,
                                 state_tree.py, profile.py
      log/                       reads.py, profile.py
  driver_setup/                  limits/ (with limit_rules.py), origin/, orientation/,
                                 optical_calibration/, data_location/, store.py,
                                 wizard/ (the ZMART driver configuration workflow)
  zmart_controller_plugin/
  testing/
```

### Its classes

| Class | Transfer | Reads | Writes | Profile |
|---|---|---|---|---|
| `cam_api/` | The LAS X CAM API, in-process through `pythonnet`; one request at a time per connection | Scan status, job settings, hardware info, xy, jobs, selected job, and z from the saved experiment | Every change, as a CAM request (`Write`, in `writes.py`) | The waiting rule; LAS X's reply checked and sorted temporary or permanent |
| `direct/` | LAS X's hardware server: its state tree, and the XML element each command ends in | Scan status, job settings, hardware info, xy, jobs, selected job, each z drive | What each CAM request becomes as an element (`writes.py`) | The read order, what counts as an answer, the routes; the profile handed to `run()` |
| `log/` | LAS X's log files, read from disk | Scan status, job settings, hardware info, xy, selected job | None | How old a log answer may be for each reading |

A change is built once, as a CAM request. When the direct route carries it,
`direct/writes.py` turns that request into the hardware server's element.

### The read order

Every reading asks the direct reader first. When it cannot answer, the CAM API
answers. The log takes part only where the read order says so.

| Reading | First | Fallback |
|---|---|---|
| Every reading | direct | CAM API |
| A reading a write waits on (scanner idle, stage arrived) | direct | CAM API, never the log |
| The job-change confirmation | direct | hybrid: the CAM API's job list, then the log |

The same order holds for writes: the direct route first, the CAM API when the
direct route could not send. A route that has sent never hands the write on.
`ZMART_WRITE_ROUTES` (`direct,cam`, `cam` or `direct`) changes the order.

### Confirming a job change

The direct reader confirms a job change. When it cannot answer, hybrid takes
over: the CAM API's job list, then the log's `CurrentBlock` event. The CAM
API's list can be stale. So when its answer from before the change is missing,
or already names the target, the log alone confirms.

### z of the drive carrying the z-stack

The CAM API and the log are stale for the drive that carries the job's
z-stack. Its z is read from the direct reader. When the hardware server cannot
be reached, it is read from the experiment LAS X saves (`.lrp`).

### The capture

| Step | Rule |
|---|---|
| Before the send | Wait for the scanner to be idle, as long as it takes. |
| The send | Once. Never again, not even after a temporary error. |
| The start | The scan must be seen scanning within 15 s of the send. Otherwise the capture fails. |
| The finish | Two idle readings in a row, waited for as long as it takes. |

A capture seen scanning and then idle is confirmed. A capture never seen
scanning fails: `take_picture` raises. The picture stays in LAS X's cache.
Saving it is `save_data`; letting LAS X go of it is `clear_cache`.

### A move, through the parts

1. The workflow calls `set_xyz`. The plugin calls `Microscope.move_to`.
2. `move_to` hands the position to `procedures/absolute_coordinate_system.py`,
   which turns user coordinates into raw coordinates.
3. The procedure calls `move_xy` in `procedures/changes.py`. It builds the
   write (`cam_api/writes.move_xy`) and hands it to `run()` with the direct
   profile.
4. The gate asks the limits. A position outside them is refused, and nothing
   is made or sent.
5. The write loops wait for the scanner to be idle, send the move through the
   direct route (or the CAM API), and read the stage back within 20 µm.
6. The answer is confirmed, or unconfirmed and recorded. The workflow carries
   on.

### Where the Leica driver differs

- `zmart_controller_plugin/info.py` and `procedure_menu.py` reach past
  `methods.py` into the procedures, the readings and the limits.
- `clear_cache` works through LAS X's window and Data Container, without the
  dispatcher.
- The kinds of error and their rule live in `cam_api/profile.py`, not in the
  dispatcher.
- The serial number is reported but not compared with a machine description.
- The direction of a saved stack is checked in the code and the offline
  tests, not yet on a microscope.

## The Nikon driver

The Nikon driver, `zmart_drivers/nikon/nis_elements_ar610/`, is the second driver
built this way, for a Nikon microscope (a Ti2) run by NIS-Elements AR 6.10. It reaches NIS-Elements
through the [nis-bridge](https://github.com/thomdehoog/nis-useq-aiagent/tree/bridge-hybrid-610/1_nis_bridge):
a small server that a NIS macro runs inside NIS-Elements' own Python, which
takes one request at a time over a socket on the same computer and answers it
once NIS has carried it out.

### Its classes

Both classes travel over the same bridge connection. What tells them apart is
how the bridge answers inside NIS-Elements, and that decides the pace at which
each may be asked.

| Class | Transfer | Reads | Writes | Profile |
|---|---|---|---|---|
| `dll/` | The bridge; inside NIS, `g5_regprocs.dll`, the C library that exports most of NIS's macro functions | The stage, NIS's own limits, the optical configurations, the nosepiece, the Perfect Focus System, the version | A move, an optical configuration, the objective, the PFS, the focus sweep, a capture (`Write`, in `writes.py`) | The waiting rule; the bridge's reply sorted temporary or permanent; the table of every reading and its class; the profile handed to `run()` |
| `macro/` | The bridge; inside NIS, a few lines of NIS macro run with `RunMacro`, because NIS 6.10 cannot hand a value back from its Python safely | The camera report, with the exposure | The exposure | Its own pace: asked again every quarter second, since one camera report costs NIS's main thread a tenth of a second |

### The read order

Each reading has one class. There is no fallback: a class that cannot answer
(the bridge gone, NIS refusing) ends the reading with that error, and the
session can `reconnect()` once the bridge runs again.

### The bridge's reply

The bridge answers every request before the call returns, so a send carries
its reply with it, and "without a receipt" makes no difference. The profile
sorts the reply: a request the bridge dropped because it had not started in
time is temporary (sending it again is safe); a request still running when
the driver stopped waiting is not; a wrong request, NIS's refusal, or a lost
connection is permanent.

### The limits

The envelope in the limits file is cut to what NIS-Elements itself allows,
read from the instrument at every connect; NIS's limits are also the backstop,
checked after the envelope on every move. There is nothing to type in for the
backstop: it comes from the instrument.

### The capture

One picture with the settings as they are (`snap`): NIS captures, saves a TIFF
in a staging folder, and closes the picture it opened. The capture is sent
once and never again. `save_data` turns the TIFF into the capture's OME-Zarr
store, with the note taken at the capture, and removes the TIFF.

### Where the Nikon driver differs

- NIS-Elements cannot report which optical configuration is selected, so
  selecting one has no read-back; the session remembers what it selected.
- A change of objective is refused before the nosepiece turns when the
  calibration does not cover the pair, since the refusal is decided from the
  two slots alone.
- The dispatcher is the Leica driver's, byte for byte; the guard test checks
  it, and the driver's CI lints everything but it.
- There is no setup wizard; the notebooks are the way.
- It has not yet been run at NIS-Elements; its offline suite stands a mock
  NIS-Elements behind the real bridge server.

The ZEISS and mesoSPIM drivers and the controller's mock driver are not in
this beta. When they come back, they come back in this anatomy.

## Open questions

- **Stop.** No driver offers a way to stop a running command.
- **Two programs at once.** The dispatcher assumes one caller. Who may
  connect, and what happens when a second program tries, is not decided.
- **Required metadata.** Which fields every driver must fill, and how
  "unknown" is written.
- **Axes that are not x, y, z.** The mesoSPIM's focus drive and rotation.
