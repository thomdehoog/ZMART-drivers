# Plan: a ZMART driver for Nikon NIS-Elements AR 7

Date: 27 October 2026. Author: Thom de Hoog (ZMB, University of Zurich), written with Claude.
Revised after three independent reviews; section 12 lists what they changed.

This page is the plan for the first ZMART driver of a Nikon microscope on NIS-Elements AR 7.
It follows [the anatomy of a ZMART driver](../driver-anatomy.md), with the Leica driver
(`zmart_drivers/leica/stellaris/navigator_expert/`, tag `v0.7.0b9`) as the example. It was
written before the code so that the design could be reviewed, and it names every assumption
that only the microscope can settle (section 11).

## 1. What we know, and where it comes from

Steps 0, 1 and 2 of the NIS 7 study (thomdehoog/nis-useq-aiagent, branches `_nis-7-step0`,
`_nis-7-step1` and `_nis-7-step2`) ran on NIS-Elements AR 7.01.03 (Build 2233) on 25 and
26 October 2026, with the microscope switched off. They settled how NIS can be driven:

- NIS brings its own Python (3.12.4). Inside it, Nikon's `nis.mac` offers the macro
  functions by name: 3089 of 3667 were reachable without hardware. `nis.ptr.double()` and
  the other `nis.ptr` types are arguments that NIS fills in. An unknown name raises
  `AttributeError`, and wrong arguments raise "Bad number of parameters".
- One line of macro starts Python (`Python_RunString`). A macro loop that calls `pump()`
  every 10 ms keeps a socket server alive between its passes. Step 1 sent requests from a
  program outside NIS and got 19 of 19 checks right: every request ran on NIS's own thread,
  and every deliberate failure came back as a reply, not as a dialog or a crash. One round
  trip took 11 ms, two connections at once were fine, and NIS answered again at once after
  being held busy for 3 s.
- On NIS 6.10 a `Capture()` called from a socket thread crashed NIS. Every NIS call must
  therefore run on NIS's own thread, which the macro loop provides.
- With the microscope off, these readings worked: the NIS version, and Z (250 µm, limits 0
  to 500 µm). PFS and the nosepiece answered "not present". `StgGetPosXY` answered
  `DR_NOTAVAILABLE` (−4). `GetOptConfCount` answered 3 when NIS was started with `-passive`
  (step 0) and 0 when it was started normally (steps 1 and 2). `GetSelectedOptConf` gave
  three empty texts. `Capture()` answered −9 with the camera off (step 2).
- Not yet tested on any hardware: moves, a real capture, saving, the selected optical
  configuration with the microscope on, `Get_ImageInfo`, `Get_Calibration` on an image, and
  the document-list functions this plan relies on (section 11).

The NIS 6.10 driver in this repository's history (`zmart_drivers/nikon/nis_elements_6_10`
on `main`) ran the whole ZMART round trip on the Ti2 simulator. Its function names and
arguments are the vocabulary used here.

## 2. Where the driver lives

- The work is on branch `claude/zmart-nikon-nis-driver-ocjk5t` of ZMART-drivers, restarted
  from tag `v0.7.0b9`, where the anatomy lives. `main` still has the old layout and shares
  no history with the tag. Pushing replaces the branch's earlier history, which held no work.
- The package is `zmart_drivers/nikon/nis_elements_ar7/`: the vendor, then the software and
  its edition. Which microscope it is stays in its connection settings, as for the Leica driver.
- Its settings on the computer go under
  `<ProgramData>/zmart-microscopy/nikon/<microscope>/nis_elements_ar7/`. `<microscope>` is
  the connection's `microscope` name, such as `nikon-ti2-zmb`. The bridge's own files (its
  macro, its stop file, its key) go in `<ProgramData>/zmart-microscopy/nikon/bridge/`, one
  folder for the whole computer.

## 3. The tree, as the anatomy puts it

```
nis_elements_ar7/
  __init__.py                 __version__, connect(), Microscope; nothing else
  methods.py                  connect(), the Microscope, its Session, the running log
  README.md
  dispatcher/                 copied unchanged from the Leica driver
  primitives/
    nis_python/               the one class: NIS's own Python, over a local socket
      server.py               the bridge, which runs INSIDE NIS; standard library only, loaded by path
      client.py               NisClient: one socket connection, one request at a time
      transfer.py             BridgeTransport and Request
      reads.py                the readings, one function each
      writes.py               Write and Expect, and the changes as plain data
      profile.py              the table of readings, the waiting rule, how a change is sent and checked
  procedures/
    readings.py               the named readings, each through run()
    changes.py                the named changes, each through run(); take_picture
    absolute_coordinate_system.py   get_xyz, set_xyz, canvas, actuators
    acquire.py                acquire(): snap, stage the TIFF, convert, answer files and planes
    state.py                  get_state, set_state
    save_data/
      save.py                 the staged TIFF -> one OME-Zarr store, the capture log, retry of staged captures
      tiff.py                 reading the staged TIFF: channels, bits, NIS's own description
      ome_zarr.py             write_store and the mean pyramid (adapted from the Leica driver)
      ome_xml.py              the OME 2016-06 XML with the convention's annotations (adapted)
      product.py              AcquisitionMetadata, ChannelMetadata, PlaneMetadata, PlaneIndex, Instrument
      naming.py               Naming, free_stem, the data, log and staging folders
  driver_setup/
    store.py                  MachineProfile: the dated, append-only settings tree, configs, the bridge folder
    notebook_support.py       copied from the Leica driver
    nis_bridge/               macro.py: writes start_bridge.mac for this computer (a command-line program)
    limits/                   config.py, limit_rules.py (NikonLimits), handshake.py, measure_limits.py,
                              defaults/limits.json, notebooks/set_limits.ipynb with bootstrap.py
    origin/                   saved_origin.py, set_origin.py
    orientation/              the Orientation model and loader, defaults,
                              notebooks/set_orientation.ipynb (the turn stated by hand)
    data_location/            check, set_data_location, data_location; notebooks/set_data_location.ipynb
    pixel_size/               the pixel size and image size last seen for each objective and optical configuration
  zmart_controller_plugin/    zmart_controller_plugin.json, zmart_controller_plugin.py, info.py, procedure_menu.py
  testing/
    conftest.py, pytest.ini, .coveragerc, run_ci.py, diagnostics.py
    helpers/mock_nis.py       the stand-in `nis` module with a pretend microscope
    helpers/bridge_stand_in.py  the real server.py over the mock, pumped by a thread
    unit/                     the offline suite (section 10)
    hardware/check_hardware.mac  the first checks at a microscope, inside NIS: reads only, unless asked
    hardware/check_hardware_nis.py
```

These are not in this first driver, and the README says so plainly: a setup wizard, an
objective calibration (so positions are not corrected when the objective changes), a piezo
z drive, z-stacks, the camera exposure as a setting of its own (the optical configuration
carries it), and a `clear_cache` procedure (section 8 says why there is no cache to clear).

## 4. The primitive class: `nis_python`

### The bridge (`server.py`)

The bridge grows out of the step 1 bridge.

- `start_bridge.mac` loads `server.py` by its path and calls `start()`. It then loops:
  `pump()` and `Wait(0.01)`, until the stop file exists. Then it calls `stop()`. Every
  Python line of the macro is guarded as in step 1: when the module is missing, it writes the
  stop file instead of raising at every pass. The macro holds literals only, because NIS
  silently refuses a macro it cannot compile (seen on 6.10). Paths in it use forward slashes,
  the file is plain ASCII, and a path with an apostrophe or a non-ASCII letter is refused by
  the program that writes the macro.
- `start()` removes a stop file left from before, closes a bridge left by an earlier run,
  and writes a fresh random key to `bridge/key.txt`, readable by this Windows user. When it
  cannot listen (the port is busy), it writes the stop file, so the macro ends instead of
  looping over nothing. Nothing happens when `server.py` is merely imported: the state it
  keeps in NIS's `__main__` is made inside `start()`.
- A request is one JSON line: `{"id", "key", "op", "fn", "args"}`. `{"ptr": "double"}` in
  `args` becomes `nis.ptr.double()`. The reply is one line: `{"id", "ok": true,
  "answer": {"return", "values"}, "nis_ms"}`, or `{"id", "ok": false, "error", "kind"}`.
  `values` are the arguments after the call, so whatever NIS wrote into a pointer comes back.
- There are four operations: `ping`, `read` (one function from `READS`), `write` (one
  function from `WRITES`) and `snap` (the capture, section 6). Any other name is refused.
  `shutdown` asks the macro loop to end.
- Socket threads only queue requests. `pump()` runs up to 50 of them on NIS's thread, one at
  a time, waiting at most 20 ms for the first. A request NIS has not taken within 30 s
  answers `queued_timeout`; it never ran, so it may be sent again. A request NIS is running
  may take up to 600 s (a long exposure); after that it answers `running_timeout`, and its
  outcome is unknown, so it is never sent again.
- A closed bridge answers `closed` at once. A bridge whose loop has not pumped for 2 s (the
  macro was stopped with NIS's Stop button) answers `not_pumping` at once. Both make the
  driver drop the connection and connect afresh on its next call.
- Safety inside NIS, independent of the driver:
  - A request without the right key is refused (`not_authorised`) and the connection is closed.
  - A first line that is not JSON, such as a web browser's request, closes the connection.
  - `StgMoveXY` and `StgMoveZ` are refused outside NIS's own stage limits
    (`StgXY_GetLimits`, `StgZ_GetLimits`), whatever the driver asked.
  - `snap` saves only to an absolute `.tif` path that does not exist yet.
  - It listens on 127.0.0.1 only. Every failure is a reply.
- A watcher thread, ported from step 1, notes every dialog window NIS opens, with its text,
  and never clicks. `ping` reports the newest ones, so the driver can say "NIS shows a
  dialog: …" when a call hangs.
- It logs to `zmart-nis-bridge.log` in the user's temp folder, with the function's name
  before every call, so a hang names its call.

### The driver's side

- **The client (`client.py`).** `NisClient.read(fn, args)` sends only functions from
  `READS`. The writes and the snap go through the transfer alone (`client.send_change`),
  and an architecture test checks that nothing else sends them. `BridgeUnavailable` is
  raised when the bridge cannot be reached, is closed, or stopped pumping. `NisRefused`,
  with its `kind` and `code`, is raised when it answered an error. The last reply is kept
  per thread. A reply that times out on the driver's side closes the socket, so a late reply
  can never be taken for the next one, and the next call connects again by itself. The
  client compares the bridge's version with its own and refuses a different one.
- **The transfer (`transfer.py`).** `fill` sets the request's arguments. `send` makes the
  one call; a refusal is kept for the error check, not raised. `await_reply` is immediate,
  and `dialog_warning_since` names a NIS dialog the watcher saw.
- **The profile (`profile.py`).** There is one source, `bridge`. `DATUMS` holds each
  reading's read function and what counts as its answer. `ask()` reads again every 50 ms
  until an answer counts, gives each call only the time left in its window, and stops at
  once on a refusal that cannot change: `not_allowed`, `bad_request`, or a DR code of −2,
  −3, −4, −5 or −7. The waiting rule is four windows of 3 s. `check_reply` sorts a reply by
  its kind alone, never by words in the error: `queued_timeout` is temporary; everything
  else, and a negative DR code, is permanent.

## 5. The readings and the changes

| Reading | NIS function(s) | Answer |
|---|---|---|
| `version` | `Get_InfoStr(1, char)` | the version text |
| `xy` | `StgGetPosXY(double, double)` | `{"x_um", "y_um"}` |
| `z` | `StgGetPosZ(double, 0)` | `{"z_um"}` |
| `stage_limits` | `StgXY_GetLimits`, `StgZ_GetLimits` | NIS's own travel per axis |
| `optical_configurations` | `GetOptConfCount`, `GetOptConfName` | the names (several calls, read together) |
| `selected_optical_configuration` | `GetSelectedOptConf(char, char, char, 256)` | all three texts |
| `nosepiece` | `Stg_IsNosepiecePresent`, `…Positions`, `…Position`, `…ObjectiveName` | present, position (from 1), names |
| `pfs` | `Stg_IsPFSPresent`, `Stg_GetPFSStatus` | present, status, its meaning, on |
| `image_info` | `Get_ImageInfo("", int, int, int, int)` | width, height, bits, components; `{"open": False}` when no image is open |
| `calibration` | `Get_Calibration(char, double, double, int)` | the current image's pixel size; None when uncalibrated or in a unit not known to be µm |

| Change | Through | Limits key | Confirmed by |
|---|---|---|---|
| move xy | `write StgMoveXY(x, y, 0)` | `x_um`, `y_um` | `xy` within 1 µm |
| move z | `write StgMoveZ(z, 0)` | `z_um` | `z` within 0.5 µm; refused while Perfect Focus is on |
| select optical configuration | `write SelectOptConf(name)` | `optical_configuration` | `selected_optical_configuration` holds the name in one of its texts; sent once |
| set objective | `write Stg_SetNosepiecePosition(n)` | `objective_position` | `nosepiece` at n |
| capture | `snap(path)` | none: always allowed | the snap's own answer: a new document, saved |

Each command has one name everywhere: in `writes.py`, in the limits and in the log
(`move_xy`, `move_z`, `select_optical_configuration`, `set_objective`, `capture`).
`NikonLimits.check_command` refuses a command it does not know (it fails closed).

The moves pass `0` as their last argument, meaning absolute coordinates, as step 0 sent
them. Should NIS read that argument otherwise, the read-back fails and the move is recorded
as unconfirmed, rather than carried on from.

## 6. The capture, safe for the operator's own images

The reviews found that confirming a capture by "an image is open" proves nothing, and that
closing the "current" document can close the operator's own image. So the whole capture is
one bridge request, `snap(path)`. NIS runs it in one pass of the macro loop, so nothing the
operator clicks can come in between:

1. Count the open documents (`GetOpenedDocumentList`, given room for no names, answers
   how many there are).
2. `Capture()`. A negative return is a failure: nothing is saved or closed, and the reply
   says why.
3. Count them again. Exactly one more must be open; otherwise the snap fails, saying that
   NIS captured into an existing document (NIS's "capture creates a new document" option
   may be off) and that nothing was saved or closed.
4. NIS makes the new document the current one; read its `Get_ImageInfo` and
   `Get_Calibration`.
5. `ImageSaveAs(path, 18, 0)` writes it as a TIFF into the staging folder. The bridge
   creates the folder first, and then checks that the file exists.
6. `CloseCurrentDocument(2)` closes that new document only. Every other document stays
   exactly as it was.

The reply carries the image's size, bits and calibration, the new document's name and the
saved path. The capture is never sent twice. Should the document-list functions answer
differently on NIS 7 than this plan expects (section 11), the snap fails with what NIS
answered, and nothing of the operator's is touched.

## 7. driver_setup

- **store.py**: the Leica store, trimmed. Its subsystems are `limits`, `orientation`,
  `origin`, `data_location` and `pixel_size`. Each keeps dated snapshot folders, and the
  newest wins. Configs (`config_YYYYMMDD_HHMM`) are chosen when connecting. There is no
  migration code, because there is nothing old to migrate.
- **limits**: a flat `limits.json` with `x_um`, `y_um` and `z_um` (each `{"range": [low,
  high]}`), `objective_position` (`[]` or `{"allowed": [...]}`) and `optical_configuration`
  (`[]` or `{"allowed": [names]}`). The outer bound is NIS's own travel, read at connect: a
  file wider than that is cut to it, and the outer bound is never invented. Until the
  operator has published a file, the session uses NIS's travel for x and y, and z may move
  only 100 µm either way from where it stood at connect. Both are said loudly in the log and
  in `get_info`'s setup. `measure_limits.py` records corners the operator drives to, as in
  the Leica notebook.
- **origin**: `origin.json` holds `x_um`, `y_um`, `z_um` and the objective position it was
  captured under. A damaged file stops the connection. With none saved, positions are plain
  stage coordinates, with a warning.
- **orientation**: the Leica `Orientation` model (a quarter turn and a mirror, lossless),
  with its loader and defaults. The operator states it by hand in the notebook, after
  looking at two pictures taken while moving +x and +y. Until then the setup item reads
  "not measured".
- **data_location**: as in the Leica driver.
- **pixel_size**: after every capture the driver records the image size and pixel size it
  saw, keyed by objective name and optical configuration. `get_state` reads it (section 8).
- **nis_bridge/macro.py**: writes `start_bridge.mac` into the bridge folder (section 4).
  There is no `stop_bridge.mac`: NIS runs one macro at a time, so a stop macro could never
  run while the bridge's loop runs. The bridge is stopped with NIS's Stop button, or from
  the driver (`Microscope.stop_bridge()`).

## 8. The procedures

- **Coordinate system**: one motor per axis, `motoric` (the stage, and the focus drive for
  z). `get_xyz` is the reading minus the origin, with NIS's own numbers as
  `actuator_readings`. The canvas is the limits in force, minus the origin, widened in x and
  y by half a field of view when the pixel size of the current objective is known. When no
  limits are installed, the canvas is the current position alone. `set_xyz` sends `move_xy`
  and then `move_z` through the dispatcher; the gate refuses either before anything moves.
  The answer is read back. A change that was sent but never confirmed is recorded in the
  session, and `get_state` lists it. `set_xyz` raises only when a move failed or was refused.
- **After an objective change**: there is no objective calibration, so positions are not
  corrected. `get_xyz` and `set_xyz` work as before. `observed` says which objective the
  origin was saved under, and whether the current one differs. The description says plainly
  that positions shift by the difference between the two objectives.
- **State**: `changeable = {"objective_position", "optical_configuration"}`. `set_state`
  applies the objective first and then the optical configuration (a configuration can
  include an objective), and reads both back. `observed` holds the NIS version, the optical
  configurations, the objectives, the PFS, the limits in force, the origin's objective, the
  setup readiness and the unconfirmed changes. It also holds the controller's two required
  keys, `image_size_px` and `pixel_size_um`, with `image_size_source` saying where they came
  from:
  1. the `pixel_size` record for the current objective and optical configuration;
  2. otherwise the image open in NIS now, said as "the image open in NIS, which may have
     been taken with other settings";
  3. otherwise none: both keys are None, and `image_size_source` says "unknown: capture one
     image". `get_state` never raises for want of an image. The controller's
     `validate_driver` therefore passes on a real microscope after one capture, which the
     README says.
- **Acquire**: there is one trigger, `snap`, a picture with the settings as they are.
  1. The stage is read first (`at`). When it cannot be read, acquire refuses before
     anything is captured.
  2. When the `pixel_size` record says the current objective is uncalibrated in NIS,
     acquire refuses before capturing, naming NIS's calibration menu.
  3. The state is read (`get_state`), for the store's notes.
  4. `snap` writes `<output folder>/.staging/<stem>.tif`, and a JSON note of the capture
     is written beside it.
  5. The stage is read again. A difference of more than 1 µm from `at`, such as Perfect
     Focus moving z, is noted in the store and among the unconfirmed changes. The picture
     is placed at `at`.
  6. The TIFF is converted to one OME-Zarr store in `<output folder>/data/<stem>.ome.zarr`
     and read back. Only then are the TIFF and its note deleted, and an empty `.staging`
     folder removed. The capture log goes in `<output folder>/log/`.
  7. When the conversion fails, the TIFF and its note stay, and acquire raises with their
     path. The `save_data` procedure converts staged captures later, also in a new session.
     `close()` warns about, and names, any staged files left.

  Acquiring and saving are one step here, because NIS keeps no cache that the driver could
  save from later: the picture is off NIS as soon as the snap answers. That is also why
  there is no `clear_cache`.
- **Save**:
  - **Reading the TIFF.** The staged TIFF is read with tifffile, by its `series[0].axes`:
    - `YX` is one channel, named after the optical configuration.
    - `YXS` with three samples is a colour camera, split into red, green and blue channels.
    - `CYX` (one page per channel) gives one channel per page.
    - Anything else is refused, saying what NIS wrote.
  - **Raw values.** The pixel type must fit the bits NIS reported: an 8-bit file from a
    12-bit camera means NIS saved the picture as displayed, not raw, and is refused. The
    display window of each channel ends at 2^bits − 1.
  - **Placing the picture.** The orientation is applied, swapping the pixel size for a
    quarter turn. One OME-Zarr 0.5 store is written, with a mean pyramid, placed by the
    centre of its first voxel. Its z scale is 1, with no z size claimed for a single plane.
  - **The metadata.** The OME-XML follows the ZMART metadata convention:
    - `controller_state` is `get_state` at the capture.
    - The capture record holds every key: `xyz` (the full `get_xyz` answer), `started` and
      `finished` in UTC, `acquisition_settings` with `asked` and `used`, `driver`, and
      `controller`.
    - `pixels_to_position` is the orientation.
    - The vendor is `nikon-nis-elements`. Its facts are the snap's answer, the stage after
      the capture, the optical configuration, the objective and the PFS.
    - NIS's own TIFF description, when it wrote one, is kept as the vendor's description.
    - Each plane carries its position and its actuator readings (`x/motoric`, `y/motoric`,
      `z/motoric`), and the `StageLabel` carries the position label.
  - **The answer.** `planes` has one entry per channel, all at `at`. Z-stacks are out of scope.

## 9. methods.py and the plugin

- **`DEFAULT_CONNECTION`**: `microscope`, `api_type`, `host`, `password` (always None,
  because NIS asks none), `config` and `output_root`, and the driver's own: `port`,
  `timeout_s`, `load_limits` and `load_origin`. `host` must be this computer.
- **`Microscope`**: `position`, `move_to`, `motors`, `set_origin`, `optical_configurations`,
  `select_optical_configuration`, `objectives`, `change_objective`, `acquisition_settings`,
  `acquire` (which saves), `save_staged`, `state`, `stop_bridge` and `close`. There is also
  `client`, for the setup notebooks; it can read but cannot send a change.
- **`Session`**: the client, the connection, `hash6`, the origin, the orientation, the data
  location, `unconfirmed` and `closed`.
- **The running log**: `<settings>/logs/running_log.txt`, as in the Leica driver.
- **`ZmartControllerPlugin`**: one method per command of controller `v0.4.0rc8`. `acquire`
  answers the files and planes. `get_info` answers `(description, setup,
  chooses_its_own_folder=False, connection_status)`:
  - **setup**: limits, orientation, origin, data location, and pixel calibration in NIS.
  - **connection_status**: the bridge, the NIS version, the stage, the limits and the
    output root. Only a real failure begins with "failed".
  - **description**: which way +z points (towards the sample, on an inverted stand), that an
    optical configuration carries the exposure and the light path, the objectives by
    position, and the one trigger.

  `procedure_menu.py` offers `save_data`, which converts staged captures.

## 10. The mock and the tests

- `helpers/mock_nis.py`: a stand-in for the `nis` module (`mac`, `ptr`, `log`), over a
  pretend microscope.
  - **What it has**:
    - a stage that takes a moment to arrive, and a focus drive;
    - NIS's stage limits;
    - three optical configurations;
    - a nosepiece with named objectives, and a PFS;
    - a list of open documents.
  - **How it behaves**:
    - `Capture()` adds a new document with a pattern that depends on the stage position, so
      a saved store can be checked.
    - `ImageSaveAs` writes a real TIFF.
    - `CloseCurrentDocument` closes only the current document.
  - **Knobs**: `hardware_on` (off: `StgGetPosXY` answers −4 and `Capture` −9, as the real
    runs did), `busy_answers`, `capture_into_current` (no new document), `calibrated`,
    `channels` (mono, RGB, pages), `bits`, and a list of every call received. By default no
    image is open.
- `helpers/bridge_stand_in.py`: the real `server.py`, serving the mock in this process. A
  thread pumps it, standing in for the macro loop, on a free port, with a key file in a
  temporary folder.
- **Unit tests**, each named as a sentence:
  - **the anatomy**: the adapted architecture guard (below);
  - **the bridge**:
    - readings round trip, and every error comes back as a reply;
    - a key is required;
    - a browser's request is cut off;
    - `queued_timeout` comes back when nothing pumps, and `not_pumping` after a stop;
    - reads only take allowed names, and moves outside NIS's travel are refused;
    - a second start replaces the first, and a stale stop file is removed;
    - importing the server changes nothing;
  - **each reading**;
  - **each change**:
    - confirmed;
    - refused by the limits with nothing sent;
    - unconfirmed when the stage does not arrive;
    - sent again after `queued_timeout`, never after `running_timeout`;
    - z refused while Perfect Focus is on;
  - **the snap**:
    - the operator's own image is never closed or overwritten;
    - a capture into an existing document saves nothing;
    - −9 saves nothing;
    - a capture is sent once whatever happens;
  - **limits**:
    - the file is validated;
    - it is cut to NIS's travel;
    - z moves only 100 µm while unpublished;
    - unknown commands are refused;
    - with no limits installed, every change is refused;
  - **the store and each setup item**;
  - **the coordinate system**: the origin arithmetic, the canvas, the read-back;
  - **acquire and save**:
    - `check_store` accepts the store;
    - the capture record holds real values;
    - the translation numbers are right;
    - the pattern reads back, also after a quarter turn and a mirror;
    - RGB and pages become channels;
    - display-scaled pixels are refused;
    - the TIFF is deleted only after success and kept with its note on failure;
    - `save_data` converts a staged capture;
    - no file is overwritten;
  - **get_state**: readable with no image, with an uncalibrated image, and after a capture;
  - **hardware off**: connect works; the stage check in `connection_status` begins with
    "failed"; `get_xyz` fails with a sentence; acquire refuses before capturing;
  - **methods**:
    - connect;
    - close refuses every later call;
    - errors are `ValueError` or `RuntimeError`;
    - the running log is written;
    - `set_state(get_state().changeable)` round trips;
  - **inside the controller** (marked `controller`, skipped without it):
    - `validate_driver` finds no problem after one capture;
    - `ZmartController.acquire` passes `check_acquire_answer`, with and without an output
      folder;
    - an unknown procedure fails;
  - **the macro text**: ASCII, guarded, and refusing a path with an apostrophe;
  - **packaging**: the driver loads without the controller.
- **The adapted architecture guard**:
  - the module names (`nis_elements_ar7.`), the rulebook (`NikonLimits` in
    `driver_setup/limits/limit_rules.py`), and the profile (`primitives/nis_python/profile.py`);
  - `StgMoveXY` and `StgMoveZ` named only in `nis_python/writes.py` and `server.py`;
  - `send_change` used only in `transfer.py`;
  - `server.py` imports only the standard library and `nis`;
  - every allowed name is used, and every used name is allowed;
  - the LAS X reader rules are left out.
- **`run_ci.py`**:
  - lint, which does not fail the run;
  - the driver layer;
  - the controller layer: SKIP without the controller, and `--controller` makes it count;
  - coverage and reports;
  - `--hardware` says how to run the hardware check, which runs inside NIS (section 13).
- **The workflow** `.github/workflows/drivers-offline.yml` gets its own `nikon` jobs, with
  and without the controller, each with its own extras and its own `run_ci.py`.
  `pyproject.toml` gets a `nikon` extra: numpy, tifffile, zarr, ome-zarr-models, ipython.

## 11. What only the microscope can settle

Each of these is said in the README's status, and the hardware check (section 13) checks it first:

1. **The moves.** Does `0` in `StgMoveXY(x, y, 0)` and `StgMoveZ(z, 0)` mean absolute, and
   does each call return once the stage has arrived? If it returns earlier, the read-back
   windows absorb the travel.
2. **The documents.** What does `GetOpenedDocumentList` take and answer on NIS 7? This plan
   expects a text and its size, and an answer of names separated by `;`, a new line or a
   tab. Does `ActivateDocument` take a name? Does `Capture()` make a new document? Does
   `CloseCurrentDocument(2)` close without asking?
3. **`Capture()`.** Its return value with the camera on, and whether it returns only once
   the picture is taken.
4. **The optical configuration.** Which of `GetSelectedOptConf`'s three texts holds the
   name with the microscope on.
5. **The image.** `Get_ImageInfo("", …)`'s arguments, and its answer with no image open
   (and whether it opens a dialog).
6. **The calibration.** `Get_Calibration`'s unit codes. This plan takes 2 as micrometres,
   from the NIS 6.10 simulator, and treats every other code as uncalibrated.
7. **The saved file.** Is `ImageSaveAs(path, 18, 0)` a raw TIFF on NIS 7? How does a
   multi-channel capture come out? ND2, read with the `nd2` package, is the alternative if
   the TIFF is not raw.
8. **The DR codes.** `StgMoveXY`, `StgMoveZ` and `Stg_SetNosepiecePosition` answer `DR_`
   codes, with 0 (`DR_CANCELED`) and below meaning failure.
9. **The stage and the objectives.** Does NIS shift `StgGetPosZ` (or XY) by its own offset
   when the nosepiece turns? Does an operator's "zero XY" in NIS shift `StgGetPosXY`? The
   latter would make `origin.json` wrong.
10. **The macro.** What does NIS do with a Python error raised at macro level? (The guards
    keep it from happening.) Does NIS 7 read a macro file's encoding as ASCII?

## 12. What the reviews changed

Three reviewers read the first version of this plan and the first bridge code, independently:
on the anatomy, on NIS, and on the controller and the metadata convention. They agreed on the
heaviest points. These changed:

- **The capture.** It is now one bridge request that proves a new document appeared, and it
  closes only that document (section 6). Before, a capture that did nothing counted as
  confirmed, and the operator's own image could be saved under the new position or closed.
  A negative return from `Capture()` is a failure, as step 2's −9 showed it can be.
- **`get_state`.** It never raises for want of an image. It answers from the `pixel_size`
  record, the open image, or None, and says which (section 8). Before, it would have failed
  `validate_driver` on every freshly started NIS.
- **Acquire and save are one step**, with the staged TIFF deleted only after the store reads
  back, and a `save_data` procedure for what is left (section 8).
- **The store's notes are specified to the key**, and the tests check their values, not
  only that `check_store` passes.
- **The bridge**:
  - Its macro has the step 1 guards back.
  - A stale stop file can no longer end the next start at once.
  - A stopped or replaced bridge answers at once, instead of timing out.
  - It needs a key, cuts off a browser's request, and enforces NIS's own travel by itself.
  - It watches NIS's dialogs again.
  - Importing it has no side effects.
- **Timeouts.** A request that never ran (`queued_timeout`) and one whose outcome is unknown
  (`running_timeout`) are told apart, and only the first is sent again. Replies are sorted
  by kind, never by words. The client reconnects by itself, and readings keep to their
  window.
- **Who may send a change.** Only the transfer can send a change through the client, so no
  procedure, notebook or check can move the stage around the limits gate.
- **Limits.**
  - The invented "backstop" is gone. NIS's own travel is the outer bound.
  - Without a published file, z may move only 100 µm.
  - One name per change, everywhere.
- **Placement.** The macro writer moved to `driver_setup/nis_bridge/`, and its files to the
  computer's settings folder, out of the installed package. `stop_bridge.mac` is gone,
  because it could never run while the bridge runs.
- **Smaller corrections**:
  - Perfect Focus refuses z moves.
  - An optical configuration is checked against NIS's list and sent once.
  - The calibration's unit is checked.
  - The TIFF's channels and bits follow named rules.
  - Sizes swap with a quarter turn.
  - The CI gets its own Nikon jobs.
  - Section 1 now says that `GetOptConfCount` answered 0 when NIS started normally.


## 13. Every hardware setting, from one table (added 2026-10-29)

The first version changed only the stage, the objective and the optical configuration. This
section adds every piece of hardware NIS controls on its own, and moves the hardware check
into NIS, because the microscope PC may have no Python but NIS's own.

### The table

`primitives/nis_python/devices.py` holds one row per hardware setting: 24 rows, 71 NIS
functions. Each row names the function that reads the setting and the one that changes it,
side by side, and says how to find the devices (one device there or not, devices NIS lists by
name, or every line of every laser), what the setting takes (a switch, a position from 1, or a
number with a tolerance), and whether it is **guarded**.

- Light: shutters, lamps and LEDs (on, brightness, iris, their own ND filter), the ND filter.
- Filters: every filter wheel and turret, by name.
- Optics: light path, condenser, aperture, analyzer, zoom, relay lens, correction collar.
- Focus: Perfect Focus on or off, and its offset.
- Spinning disk: its shutter and speed.
- Guarded: the TIRF position, the laser lines (used, power), the heating stage's temperature,
  the incubator's gas and humidity.

Every function name is in NIS-Elements AR 7.01.03's own list from step 0, and NIS offered every
one without hardware (a test checks both against `testing/data/nis7_functions.csv`).

**Left out, on purpose:** acquisitions NIS runs itself (ND experiments, stage scans, large
images), actions with no reading (perfusion, the slide loader, autofocus runs), raw outputs
(TTL, analog, serial ports, `PI_GcsCommand`), switching the microscope off, and the camera's own
functions, which NIS offers only with the camera on. ZMART workflows build z-stacks and time
series from single moves and pictures.

### How the table is used

The bridge allows the table's functions and no others. The driver reads a setting as
`device/<key>/<device>` through the dispatcher, changes it with `set_device` (limits first,
then the change, then the row's own reading as read-back), and lists every setting that is
there in `get_state`'s changeable part (`"shutter/EPI"`, `"light_path"`). `set_state` applies
the objective, then the optical configuration, then the hardware settings with the light last,
and sends nothing for a setting that already holds the asked value. The limits file may hold
`"devices": {"<key>" or "<key>/<device>": {"allowed": [...]} or {"range": [low, high]}}`; a
guarded setting without an entry is refused.

### What only the microscope can settle

The argument lists follow Nikon's macro reference and the conventions the NIS 7 study confirmed
for the first readings: a count, a position or a state as the return value; a name through an
index from 0, a text place and its size; the device's name first. None is confirmed with the
hardware on (every row says `confirmed=False`). The hardware check reports, for each:

1. whether NIS offers the function, with NIS's own description where it gives one, and what
   NIS's macro reference on the PC says about it (`reference.txt`);
2. whether the device is found, and its value;
3. with its group switched on: whether the change takes the arguments, what it returns (a DR
   code is assumed: 1 or more is success), whether the reading shows it, and whether putting it
   back works.

Open points it settles: whether counts and states come back as the return value or through an
argument; whether name lists count from 0; whether positions count from 1; which Perfect Focus
statuses mean "holding"; how the laser unit numbers its lasers and lines; whether a lamp's
"voltage" is a voltage or a percentage on LED sources.

### The hardware check inside NIS

`testing/hardware/check_hardware.mac` finds `check_hardware_nis.py` (at `C:\ZMART-drivers` or
on the Desktop) and runs it in NIS's own Python, which needs nothing installed. The script
loads the bridge's `server.py` and the table by their paths, and calls NIS exactly as the
bridge does. Part 1 only reads. Part 2 changes what is switched on at the top of the script,
one group at a time: a switch is turned over and back, a choice moves one position and back, a
number (and anything guarded) is set to its own value. Part 3 takes one picture as the snap
does. It replaces `check_hardware.py`, which needed a Python outside NIS. The offline suite runs
the same script, and the macro's own line, against the pretend NIS with every switch on.

Writing `start_bridge.mac` also works with NIS's own `python.exe`: the package now loads
`connect` and `Microscope` (which need numpy) only when they are first used.

### The vendor's own description of each picture

A TIFF keeps little of what NIS knows about how a picture was taken. Before it closes the
new document, the snap reads back the description NIS keeps with the saved picture, with
`LoadImageFullDescription(path, ...)` and its fourteen parts (optics, how it was captured,
sampling, and so on), and writes the parts that say something beside the staged TIFF.
`save_data` keeps that text in the store's OME-XML as the vendor's description, after the
TIFF's own, and deletes the file with the TIFF. When NIS gives none, the picture is saved all
the same and the capture log says why. (An earlier version used `SaveImageFullDescription`,
which NIS's help shows *changes* a picture's description rather than reading it.)


## 14. The setup wizard (added 2026-10-29)

The Nikon driver has the same setup wizard as the Leica driver (`driver_setup/wizard/`): the
same framework and local server, copied unchanged (a test keeps them identical), the same page
built from TypeScript into plain files, and five Nikon steps in `steps.py`:

1. **Connect**: writes `start_bridge.mac`, connects through the bridge, saves the data folder.
2. **Define limits**: stage and focus ranges (four corners and the lowest and highest focus,
   imported from the stage), the objectives and optical configurations automation may choose,
   and the guarded hardware it may set.
3. **Define coordinate system origin**: read, then Save and adopt.
4. **Image-to-stage orientation**: measured from three pictures (here, after +x, after +y),
   by phase correlation over the eight ways a picture can lie
   (`driver_setup/orientation/measure_orientation.py`, numpy only), or chosen by hand. The
   notebook still asks the operator to state it.
5. **Hardware settings**, in place of the Leica wizard's objective calibration: every setting
   NIS reports, with its value; each can be tried through the limits.

The pretend NIS's camera now shows a sample that stays put while the stage moves, and can be
mounted turned or mirrored, so the orientation measurement is tested for all eight mounts. A
browser test walks the whole setup on the pretend NIS.


## 15. The arguments, from NIS's own help (added 2026-10-30)

The second run at the microscope (NIS-Elements AR 7.01.03, the hardware switched off) passed
the whole driver part and the ZMART Controller plugin part, and showed that about twenty of
the table's functions were given the wrong arguments. NIS's own help pages for its device
functions (`Docs\nis\eng_ar\p4c8s*.html`) were then read, and the table now follows them:

- Most readings give the value as the function's own answer and take only the device's name:
  `Stg_GetShutterStateEx(name)`, `Stg_Light_GetState(name)`, `Stg_Filter_GetPosition(name)`,
  `Stg_GetZoomPosition()`, `Stg_GetTIRFPosition()`.
- Lamps, filter changers and heating stages are named with `(index, text)`, without a size.
- The four diaphragms are numbered by NIS (0 the transmitted-light field diaphragm, 1 the
  epi-fluorescence field diaphragm, 2 and 3 the aperture diaphragms), so `aperture` has one
  device for each. The CSU-W1's relay lens is read per camera port.
- Laser units are named, not numbered: `Stg_GetMultiLaserName(index, text)`, then
  `Stg_GetMultiLaserNumLines(name)`, and each line's settings take `(name, line)`. The power is
  a whole number.
- The incubator functions take the incubator's key from NIS's Device Manager first; the driver
  passes an empty key, which NIS accepts, for the one incubator there is.
- Both neutral density filters are set as a density (a number), not as a position.

`testing/data/nis7_signatures.csv` keeps each function's arguments as NIS's help writes them,
and a test checks that every call in the table, and every function of the pretend NIS, takes
exactly that many. The hardware check now copies the help pages that describe the driver's
functions first, and writes each function's own entry into `signatures.txt`.
