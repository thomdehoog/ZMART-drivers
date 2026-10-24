# How the Leica driver is built

The driver `zmart_drivers/leica/stellaris/navigator_expert/` in short: its parts, how a call travels
through them to LAS X, and what comes back. Using the driver and setting a microscope up are in its
[README](../../zmart_drivers/leica/stellaris/navigator_expert/README.md). Every ZMART driver has the
same layout, described in [the anatomy of a ZMART driver](../driver-anatomy.md).

## 1. The parts

| Part | What it does | Where |
|---|---|---|
| `methods.py` | What you call: `connect()` and the `Microscope`. Every call starts here. | `methods.py` |
| procedures | Chains of steps: move, change a setting, take a picture, save it, keep the sample in view. | `procedures/` |
| dispatcher | Runs one reading or one change, the same way for every driver. Holds the limits gate. | `dispatcher/` |
| primitives | The ways of reaching LAS X: its hardware server, its CAM API and its log files. | `primitives/direct/`, `cam_api/`, `log/` |
| setup | What is set up once per microscope, and the wizard that walks through it. | `driver_setup/` |
| controller plugin | The ZMART Controller's view of the `Microscope`. | `zmart_controller_plugin/` |

## 2. How a call travels

```
Microscope (methods.py) ──► procedure ──► dispatcher run() ──► primitive ──► LAS X
```

You call the `Microscope`. It hands the work to a procedure, such as `move_to` or `acquire`. The
procedure asks the dispatcher's `run()` for each reading or change it needs, and `run()` sends it
through a primitive to LAS X. Every change takes this one road, so every change gets the same checks.

## 3. Two routes to LAS X

| Route | What it is | When it is used |
|---|---|---|
| direct | LAS X's own hardware server. Fast, and always current. | First, for every reading and every change. |
| CAM API | Leica's Python interface, loaded into the driver with `pythonnet`. | Only when the direct route could not send, or could not answer. |
| log | LAS X's log files, read from disk. | Only for a few readings that name it, never to decide whether a change is sent. |

A change is never done twice: once it has gone out on one route, the other never takes it over. Each
result says which route carried it (`route`). To change the order, set the environment variable
`ZMART_WRITE_ROUTES` (`direct,cam`, `cam` or `direct`) before the driver starts.

## 4. A change, step by step

| Step | What happens |
|---|---|
| 1. Limits | The limits gate is asked once. A refused change is never made and never sent. |
| 2. Wait | The driver waits until the scanner is idle. |
| 3. Send | The change is sent, and LAS X's reply is checked. A temporary error is sent again; a permanent one stops. |
| 4. Read back | The microscope is read until it shows the change, or the time is up. |
| 5. Answer | The result says whether the change was sent (`success`) and whether it was seen (`confirmed`). |

A capture is the one exception: it is sent once and never again, and must be seen scanning within 15 s.

## 5. A reading

A reading is asked by name, for example the stage position. The direct route answers first; when it
cannot, the CAM API answers. Each reading says which source answered. What counts as an answer is
written once per reading in `primitives/direct/profile.py`.

## 6. Waiting

| Rule | Value |
|---|---|
| How long a reading or a read-back waits | 4 windows of 3 s; something is asked or sent again between windows |
| How often the driver looks | every 0.1 s |
| After the fourth window | a reading is unknown, a change unconfirmed |
| A capture | waits for idle as long as it takes, then is watched for as long as the scan takes |

The numbers live in `primitives/cam_api/profile.py`.

## 7. What a change answers

| Key | Meaning |
|---|---|
| `success` | The change was accepted: sent, with no permanent error. |
| `confirmed` | The read-back showed it (`True` or `False`); `None` when nothing was read back. |
| `message` | A sentence saying what happened. |
| `route` | `"direct"` or `"cam"`; `None` when nothing was sent. |
| `timing` | How long each part took, and how many attempts. |
| `logs` | What happened, step by step. |

Read both `success` and `confirmed`: `success` alone does not mean the microscope shows the change.

## 8. Pictures

| Step | Call | What happens |
|---|---|---|
| Take | `acquire()` | Runs the selected job. The picture stays in LAS X's cache, with a note of how it was taken. |
| Save | `save()` | Writes the waiting pictures as OME-Zarr under `<output_root>/data/`, with their metadata beside them. |
| Clear | `clear_cache()` | Asks LAS X to let go of the pictures that were saved and read back. The driver never deletes a file itself. |

A name is the acquisition type and the workflow's position label; a name already used gets `_001`,
`_002`, and so on, so nothing is overwritten.

## 9. Coordinates

| Quantity | How it is made |
|---|---|
| A position | The stage reading, minus the saved origin, minus the objective's measured offset. In micrometres. |
| z | The focus: z-wide plus z-galvo. Moves are made with z-wide; the z-galvo is parked at 0. |
| Another objective | Its offset comes from the objective calibration. A move to an objective it does not cover is refused. |

## 10. What is loaded at connect

| Item | From | If it is missing |
|---|---|---|
| Limits | the newest saved `limits.json` | the bundled cautious defaults, said loudly |
| Origin | the newest saved `origin.json` | plain stage coordinates, with a warning |
| Orientation | the newest saved `orientation.json` | no turn, filled in from the defaults |
| Objective calibration | the newest saved `calibration.json` | no offsets; moves across objectives refused |
| Data location | the newest saved record | nothing is saved until one is set |

All of them live in `C:\ProgramData\zmart-microscopy\leica\stellaris_y42h93\navigator_expert\`.

## 11. Who may import whom

| Part | May import |
|---|---|
| primitives | the dispatcher |
| dispatcher | nothing of the driver |
| procedures | primitives, dispatcher, setup |
| setup | primitives, dispatcher, procedures (the wizard also `methods.py`) |
| `methods.py` | primitives, dispatcher, procedures, setup |
| controller plugin | `methods.py`, procedures, setup, dispatcher |

`testing/unit/test_architecture_guard.py` holds this table and fails on any import against it.

## 12. More detail

The full notes on each of these, written while building the driver, are in
[`navigator_expert-internals.md`](navigator_expert-internals.md).
