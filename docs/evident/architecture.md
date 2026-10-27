# How the Evident FV4000 driver is built

The driver `zmart_drivers/evident/fv4000/rdk/` in short: its parts, how a call travels through them to
FLUOVIEW, and what comes back. Using the driver and setting a microscope up are in its
[README](../../zmart_drivers/evident/fv4000/rdk/README.md). Every ZMART driver has the same layout,
described in [the anatomy of a ZMART driver](../driver-anatomy.md); what the manual says that this
driver relies on is in [rdk-notes.md](rdk-notes.md).

## 1. The parts

| Part | What it does | Where |
|---|---|---|
| `methods.py` | What you call: `connect()` and the `Microscope`. Every call starts here. | `methods.py` |
| procedures | Chains of steps: move, change a setting, take a picture, save it, keep the sample in view. | `procedures/` |
| dispatcher | Runs one reading or one change, the same way for every driver. Holds the limits gate. Byte for byte the Leica driver's. | `dispatcher/` |
| primitives | The one way of reaching FLUOVIEW: its Remote Development Kit (RDK), spoken over XML-RPC. | `primitives/rdk/` |
| setup | What is set up once per microscope: limits, origin, orientation, objective calibration, data location, and the frame the driver learns. | `driver_setup/` |
| controller plugin | The ZMART Controller's view of the `Microscope`. | `zmart_controller_plugin/` |

## 2. How a call travels

```
Microscope (methods.py) ──► procedure ──► dispatcher run() ──► primitives/rdk ──► FLUOVIEW (XML-RPC)
```

You call the `Microscope`. It hands the work to a procedure, such as `move_to` or `acquire`. The
procedure asks the dispatcher's `run()` for each reading or change it needs, and `run()` sends it
through the RDK primitive to FLUOVIEW. Every change takes this one road, so every change gets the same
checks.

## 3. One route to FLUOVIEW

| Route | What it is | When it is used |
|---|---|---|
| rdk | FLUOVIEW's Remote Development Kit: XML-RPC calls at `http://<host>:8080/xmlrpc`, made with Python's own standard library. | For every reading and every change. |

There is no second route. When the RDK cannot answer, a reading is unknown and a change is unconfirmed;
nothing is tried another way. `RdkClient` (`primitives/rdk/client.py`) makes one call at a time, keeps
at least 20 ms between calls (the manual warns that a flood of commands closes the socket), and turns a
lost socket into `ConnectionLost`.

## 4. A change, step by step

| Step | What happens |
|---|---|
| 1. Limits | The limits gate is asked once. A refused change is never made and never sent. |
| 2. Wait | The driver waits until FLUOVIEW is IDLING (not scanning, resting or waiting). |
| 3. Send | The change is sent as one `Parameter.setParameter` (or one `Protocol.*` call), and the result code is read: `OK` is done, `UNAVAILABLE` is sent again, `INVALID_PARAMETERS` and the other errors stop. |
| 4. Read back | The same setting is read with `Parameter.getParameter` until it shows the change, or the time is up. A stage move is confirmed within 1 µm in XY and 0.5 µm in z. |
| 5. Answer | The result says whether the change was sent (`success`) and whether it was seen (`confirmed`). |

A capture is the one exception: `Protocol.startProtocol` is sent once and never again, and must be
seen scanning within 15 s. A scan too quick to be seen counts when the file FLUOVIEW named already
opens.

## 5. A reading

A reading is asked by name, for example `xy` or `laser_intensity` (with `about="LASER_488"`). Each is
one `Parameter.getParameter` (or one Maintenance, Protocol or Canvas call), parsed into the driver's
units: nanometres become micrometres, strings become numbers. What counts as an answer is written once
per reading in `primitives/rdk/reads.py`; the waiting rules in `primitives/rdk/profile.py`.

## 6. Waiting

| Rule | Value |
|---|---|
| How long a reading or a read-back waits | 4 windows of 3 s; something is asked or sent again between windows |
| How often the driver looks | every 0.2 s (a call is never fired more often than every 20 ms) |
| After the fourth window | a reading is unknown, a change unconfirmed |
| A capture | waits for idle as long as it takes, then is watched for as long as the scan takes |

The numbers live in `primitives/rdk/profile.py` and `primitives/rdk/client.py`.

## 7. What a change answers

| Key | Meaning |
|---|---|
| `success` | The change was accepted: sent, with result `OK`. |
| `confirmed` | The read-back showed it (`True` or `False`); `None` when nothing could be read back. |
| `message` | A sentence saying what happened. |
| `route` | `"rdk"`; `None` when nothing was sent. |
| `timing` | How long each part took, and how many attempts. |
| `logs` | What happened, step by step. |

Read both `success` and `confirmed`: `success` alone does not mean the microscope shows the change.
A laser power FLUOVIEW clipped is `success` True, `confirmed` False.

## 8. Pictures

| Step | Call | What happens |
|---|---|---|
| Take | `acquire()` | Starts the imaging set up in FLUOVIEW. FLUOVIEW writes its own `.oir` file and names it in its answer; the driver keeps a note of how it was taken. |
| Save | `save()` | Reads the picture back through the RDK's IDA calls (channels, axes, pixel size, the pixels in pieces) and writes it as OME-Zarr under `<output_root>/data/`, with a max projection beside a stack and a log. |

A name is the acquisition type and the workflow's position label; a name already used gets `_001`,
`_002`, and so on, so nothing is overwritten. The driver never deletes a FLUOVIEW file. The size and
pixel size of each saved picture are remembered per objective (`driver_setup/frame.py`), because
FLUOVIEW's settings do not report them.

## 9. Coordinates

| Quantity | How it is made |
|---|---|
| A position | The stage reading, minus the saved origin, minus the objective's measured offset. In micrometres (FLUOVIEW counts in nanometres). |
| z | The focus motor (FLUOVIEW's `Z_STAGE_POSITION_SETTING`). The piezo is read and reported beside it, and moved only by `move_piezo`. |
| Another objective | Its offset comes from the objective calibration. A move to an objective it does not cover is refused. |

## 10. What is loaded at connect

| Item | From | If it is missing |
|---|---|---|
| Limits | the newest saved `limits.json` | the bundled cautious defaults, said loudly |
| Origin | the newest saved `origin.json` | FLUOVIEW's own stage coordinates, with a warning |
| Orientation | the newest saved `orientation.json` | no turn, filled in from the defaults |
| Objective calibration | the newest saved `calibration.json` | no offsets; moves across objectives refused |
| Data location | the newest saved record | nothing is saved until one is set |
| Frame | the newest saved `frame.json` | image size and pixel size not known until a picture is taken |

All of them live in `C:\ProgramData\zmart-microscopy\evident\fv4000\rdk\`, or in a config folder under
it chosen when connecting. At connect the driver also puts FLUOVIEW in REMOTE mode, and at close back in
LOCAL mode.

## 11. Who may import whom

| Part | May import |
|---|---|
| primitives | the dispatcher |
| dispatcher | nothing of the driver |
| procedures | primitives, dispatcher, setup (only the loaders, never the measuring code) |
| setup | primitives, dispatcher, procedures |
| `methods.py` | primitives, dispatcher, procedures, setup |
| controller plugin | `methods.py`, procedures, setup, dispatcher |

`testing/unit/test_architecture_guard.py` holds this table and fails on any import against it;
`testing/unit/test_dispatcher_is_shared.py` fails when the dispatcher differs from the Leica driver's
by one byte.
