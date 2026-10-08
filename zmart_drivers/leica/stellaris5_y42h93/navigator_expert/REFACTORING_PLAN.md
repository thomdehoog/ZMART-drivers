# Refactoring plan: Leica STELLARIS driver (`navigator_expert`)

This document says how we intend to reshape the Leica driver so that it matches
[the anatomy of a ZMART driver](../../../../docs/driver-anatomy.md), and in
which order. It is the companion to the
[release candidate review](RELEASE_CANDIDATE_REVIEW.md): the review lists what
is wrong, this plan says how the code should be rearranged so that those
problems are fixed at their root and cannot quietly come back.

- **Code looked at:** the driver at release candidate `6.0.0rc1`, in this
  repository, on 2026-10-08.
- **How it was looked at:** the driver's own documentation (its README, the
  release candidate review and the anatomy proposal), its import graph as
  computed from the source, and a run of the offline test suite on Linux.
  Nothing was changed in the driver while writing this plan.
- **Who it is for:** whoever picks up the driver next. You do not need to have
  written any of it. Each step below says what to change, why, and how you
  will know it worked.

## Contents

1. [The short version](#1-the-short-version)
2. [Where the driver stands today](#2-where-the-driver-stands-today)
3. [The five refactors, in order](#3-the-five-refactors-in-order)
4. [After the five](#4-after-the-five)
5. [What we deliberately leave alone](#5-what-we-deliberately-leave-alone)
6. [How to keep the suite green while doing this](#6-how-to-keep-the-suite-green-while-doing-this)

---

## 1. The short version

The Leica driver is large (about 25,000 lines of code) and well tested (about
1,300 offline tests), and its safety core is sound: no reviewer has found a way
to move the stage past the limits. So this is not a rewrite.

We also do not want to start by moving folders around to match the anatomy
proposal. The real problems are about **what a result means** and **which part
of the code owns a decision**, not about where a file sits. Renaming folders
first would move those problems around without fixing them, and it would make
every open review finding harder to apply because the line numbers would no
longer match.

Instead we do five targeted refactors, each small enough to keep the test suite
green, in this order:

1. **Make CI green**, so that every later step is checked.
2. **Make "success" mean success**, so that an unconfirmed change is never
   treated as one that happened.
3. **Turn the hand-rolled command dispatch into the one set dispatcher** the
   anatomy describes, with the limits gate inside it, and make the command
   profiles pure data.
4. **Pull the coordinate frame and the procedures out of the controller
   adapter**, and stop remembering where the stage was driven to.
5. **Give the scan-field template a real state**, and stop reading the z
   position by saving the experiment.

Once these are done, the import graph is acyclic and the folder layout of the
anatomy proposal becomes a mechanical rename, one folder per pull request.

---

## 2. Where the driver stands today

### 2.1 The test suite is red

Running the offline suites on Linux (Python 3.11, dependencies from
`pyproject.toml`'s `leica` and `test` extras) on 2026-10-08:

| Suite | Result |
|---|---|
| `tests/` together with `calibration/tests/` | 1304 passed, **6 failed**, 1 skipped |

These are the same six failures the release candidate review recorded on
2026-09-30 (its findings T1, T2 and T3). Nothing has been fixed since. While
they stay red, a new regression hides behind them.

### 2.2 The import graph has cycles

The README says the driver has "no circular imports". At the level of the
folders this is not the case. An *import cycle* means that folder A needs
something from folder B to load, and B needs something from A, so neither can
be understood, tested or moved on its own. Counting only the imports written at
the top of each file, there are 38 cycles between the driver's folders. The
code keeps them from crashing with 83 imports placed inside functions, so that
the import happens later, at the moment the function runs. That works, but it
hides the tangle rather than removing it, and it is the reason the anatomy's
"each part only uses the parts below it" rule cannot yet be enforced by a test.

Three cycles carry almost all of the others:

- **`config` and `commands` need each other.** The command profiles in
  `config/profiles.py` import the confirmation functions so that they can
  pre-bind them, and the command wrappers import the profiles.
- **`readers` leads back to itself through `config` and `commands`.** The
  reader router asks the profiles for its modes, and the profiles reach into
  the commands.
- **`commands` leads back to itself through `limits` and `scanfields`.** The
  adaptive-limits helper imports the scan-field parsers, and the scan-field
  file module imports the commands to save and load experiments. From
  `scanfields` the path also runs on into `acquisition` and `readers`.

### 2.3 The limits gate is called by hand

Every mutating command is supposed to check the limits before anything is sent
to LAS X, and it does. But each of the 26 command wrappers in
`commands/commands.py` makes that call itself, and two more live in
`scanfields/files.py`. A guard test checks that nothing *outside* the commands
layer moves the stage, which is good, but nothing checks that a *new* wrapper
inside the layer remembered to call the gate. The anatomy's rule is that the
gate sits inside the dispatcher, so that forgetting it is impossible.

### 2.4 An unconfirmed change counts as a success

Every command profile sets `success_on_unconfirmed=True`. This means a command
that LAS X accepted but whose read-back never matched still answers
`success: True`, with `confirmed: False` beside it. The README asks callers to
check both flags. In practice the adapter checks only `success` in five
places, the capture step raises on nothing, and an objective change that was
never confirmed still moves the stage to compensate (review findings H2, H3 and
H11). The controller's contract now says the opposite: an unconfirmed set
action answers `success: False` with `confirmed: False`.

### 2.5 The adapter does six jobs

`zmart_adapter/zmart_adapter.py` is 1,669 lines. It holds the frame arithmetic
(origin, objective offset, how z-wide and z-galvo share a focus move), the
three procedures (autofocus, backlash take-up, parking the galvo), the whole
acquire pipeline (strip the template, select the job, settle, capture, save,
work out where each plane was taken), the `set_origin` setup step, the
plain-language description for `get_info`, and the envelope the controller
expects. The anatomy wants that file to do one thing: map the driver's actions
onto the eleven functions the controller calls.

The adapter also remembers where the last `set_xyz` drove the stage, in
`handle.driven_to`, and labels every saved plane with that memory. The review
lists five ways that memory goes stale (finding H8). Memory of a position is a
shape of state the driver should not have; a reading is always available.

### 2.6 The scan-field template can be destroyed

The strip-and-restore mechanism keeps the operator's hand-drawn scan fields
safe while the driver acquires at the current position. Its state ("stripped"
or not) is judged only by comparing file times (finding M10). Worse, reading
the z position of a job that carries a z-stack *saves the experiment* in order
to read the position from the saved file. While the stripped copy is loaded,
that save overwrites the operator's real template with the empty one (finding
H4). In the anatomy's words, this is a get action that performs a set, and it
is the single most dangerous path in the driver today.

---

## 3. The five refactors, in order

Each step is a pull request, or a few. Each one ends with the full offline
suite green and, where it touches what the hardware does, a run on the LAS X
simulator.

### Step 1. Make CI green

**What.** Apply the three fixes the release candidate review already spells
out:

- T1: the three adapter tests about plane positions must stand in for the
  output root, which otherwise is discovered from LAS X AutoSave on the
  author's PC. Build those handles with an explicit `output_root`, as the
  neighbouring tests do.
- T2: the mock hardware fixture still publishes its calibration under a name
  the adapter no longer looks for. Publish it as the flat default.
- T3 and M4: the calibration code must read image geometry through the API
  leg, not the default hybrid mode. Pass `mode="api"` where the comment already
  says it does. The two failing tests then pass.

**Why first.** Nothing below is safe to do while the suite is red. Every later
step relies on the tests to say whether a change in meaning broke a caller.

**Done when.** `python run_ci.py` exits 0 on Linux and Windows.

### Step 2. Make "success" mean success

**What.** Flip the default of `success_on_unconfirmed` in `CommandProfile` to
`False`. The dispatcher then reports an unconfirmed set action as
`success: False, confirmed: False`, which is what the controller contract and
the anatomy ask for. Then let the suite show which callers relied on the old
soft outcome, and decide each one deliberately:

- `capture.acquire` must raise when the acquisition is unconfirmed (H3). Its
  docstring and the README already promise that.
- `set_objective` and `select_job` compensate the stage only when the change
  is confirmed, and compute the move from the objective slot that was read
  back, not from the one requested (H2).
- The adapter's `acquire`, `set_state` and autofocus require confirmation of
  the job switch (H11).
- Where a workflow genuinely should carry on after an unconfirmed setting (a
  laser intensity that did not read back within the window, for example), the
  caller says so explicitly by looking at `confirmed`, and the reason travels
  in the content.

Add a guard test, next to the existing architecture guard, that no product
module reads the `success` key of a command result without also reading
`confirmed`.

**Why second.** This is the most consequential change in the plan and it
touches no folder. Three high-severity findings share this root cause. Doing
it before the structural work means the structure is built on the right
meaning.

**Done when.** The guard test passes, the review's H2, H3 and H11 are closed,
and the simulator run shows an unconfirmed objective change leaving the stage
where it was.

### Step 3. One set dispatcher, with the gate inside, and profiles as data

**What.** Two changes that belong together.

First, move the limits check into the internal dispatch helper
(`_dispatch` in `commands/commands.py`), keyed by the command's name. The 26
hand-placed calls disappear. The architecture guard test changes from "nothing
outside `commands/` calls the gate" to "every wrapper goes through the
dispatcher", which is a stronger and simpler statement. The two gate calls in
`scanfields/files.py` route through the same helper.

Second, make the command profiles pure data. Today `config/profiles.py`
imports the confirmation functions so it can pre-bind them, and that import is
the `config ↔ commands` cycle. Tolerances, polling windows, retry ceilings and
timeouts stay in `config/`, as numbers. The binding of a command to its
confirmation function moves into `commands/`, as a small table keyed by the
same command name. Nothing in `config/` imports from `commands/` any more.

**Why.** It makes forgetting the gate impossible, which is the anatomy's rule
for set actions. It breaks the two cycles that run through `config`, which in
turn lets the deferred imports in `dispatch.py` and `confirmations.py` become
ordinary ones. And it is the shape the anatomy wants for a shared set
dispatcher later: the engine knows nothing about zoom or objectives, it only
runs the steps and asks the gate.

**Done when.** `config/` imports nothing from `commands/`; the guard test
proves every wrapper dispatches through the helper; the per-command tests in
`test_core_driver.py` pass unchanged.

### Step 4. Take the frame and the procedures out of the adapter

**What.** Three moves and one deletion.

- **The coordinate frame** becomes a pair of plain functions, `to_user` and
  `to_stage` (or similar names), that live next to the configuration they
  depend on: the origin and the objective calibration. They do the arithmetic
  the adapter does today: subtract the origin, subtract the objective offset
  for the lens in place, and decide how a focus move is split between z-wide
  and z-galvo. The position actions use these functions; the adapter no longer
  does arithmetic of its own.
- **The procedures** (autofocus, backlash take-up, zero the z-galvo) each
  become a small module in a `procedures/` folder, with a name and a
  plain-language description that `get_procedures` lists. Each one uses only
  get and set actions, never the vendor interface directly.
- **The acquire pipeline** (strip, select, settle, capture, save, label the
  planes) becomes its own module in the acquisition folder, so the adapter
  only calls it.
- **`handle.driven_to` is deleted.** Before capture, the pipeline reads the
  stage position once, through the routed readers, and labels every plane
  from that reading. The adapter already reads the hardware for the plane
  heights, so this adds no new call that could hang. Fixing the plane heights
  for galvo stacks and multichannel stacks (H6, H7) belongs in the same
  change, since both live in the plane-labelling code.

What remains in `zmart_adapter.py` is the mapping onto the controller's eleven
functions and the description text. That is a file of a few hundred lines, and
it can then be merged into `zmart_controller_plugin.py` as the anatomy
suggests.

**Why.** Five of the review's staleness findings (H8) come from the adapter
remembering state instead of reading it. Setup notebooks and procedures can
today use a different coordinate system from experiments; with the frame as a
shared pair of functions that is no longer possible.

**Done when.** `zmart_adapter.py` contains no arithmetic on positions, no
`driven_to`, and the tests in `test_zmart_adapter.py` that exercise frame math
import the frame functions directly.

### Step 5. A real template state, and no z readback through a save

**What.**

- **An explicit marker for "stripped".** When the driver strips the template,
  it writes a small sidecar file beside it saying so, and which file is the
  operator's original. The state is read from that marker, never from file
  times. Restoring removes it. If LAS X is found to have a different template
  loaded than the marker claims, the driver refuses to acquire and says why
  (M10).
- **A scratch name for save-and-read.** When the driver needs to read the z
  position from a saved experiment, it saves under a scratch name that can
  never collide with the operator's template, and reads from there (H4). The
  same change gives `apply_lrp_change` the same protection.
- **Restore copies the edited file back before loading** (H5).
- **Every template file is written through a temporary file and a rename**, the
  way image and JSON writes already are (M17).
- **Adaptive limits import only the parsers.** The parsers move to a leaf
  module that imports nothing from `commands/`, which breaks the
  `commands → limits → scanfields → commands` cycle.

**Why.** This is the one place where the driver can silently destroy work the
operator did by hand. It is also the last large cycle in the import graph.

**Done when.** A regression test strips, reads the position on a z-stack job,
restores, and checks that the operator's scan fields survive with the same
object count. The import graph of the driver's folders has no cycles, and a
guard test keeps it that way.

---

## 4. After the five

With the cycles gone, the remaining work is mechanical and low-risk. One
folder per pull request:

- **Rename folders to the anatomy's names** (`vendor_interface/`,
  `error_handling/`, `get_actions/`, `set_actions/`, `procedures/`,
  `data_handling/`, `configuration/`, `testing/`). The guard test for "each
  part only uses the parts below it" is written at this point, because only
  now can it pass.
- **Move the algorithms to a shared package.** Registration and focus scoring
  in `algorithms/` are not Leica-specific and are used only by the setup
  notebooks. They are the anatomy's clearest case for `zmart_drivers/shared/`.
  Fix the two known defects first (L1, the mirrored focus peak; M5, trusting
  featureless images).
- **Move the mock LAS X client out of `tests/`** into `testing/mock_api/`. The
  controller's mock driver wants it, and installed packages drop their test
  folders, so today it is missing exactly where someone would want to try the
  driver on a laptop.
- **Merge the three notebook bootstrap files** into one. They differ only in
  the notebook name and the two marker strings.
- **The documentation pass** the review asks for, the setup notebooks
  especially, comes last, once the behaviour it describes has stopped moving.

---

## 5. What we deliberately leave alone

- **The set dispatcher stays in the Leica driver for now.** The ZEISS driver
  has its own, shorter version of the same engine and would be the natural
  second user of a shared one. The anatomy's own rule says to move something
  into the shared package only once two drivers use it the same way, and that
  is not yet true. Step 3 shapes the Leica dispatcher so that the move is
  easy when the time comes.
- **The hardware validators wait until step 2 is settled.** The three
  validator scripts and their run reports encode today's meaning of `success`.
  Changing them before the meaning is fixed would mean changing them twice.
- **The limits, orientation and calibration subsystems keep their shape.** The
  review found their validation sound. Their snapshot handling has two medium
  findings (M12) that are ordinary bug fixes, not refactors.
- **No folder is renamed before step 5.** Renaming earlier would move the open
  review findings' line numbers out from under anyone applying them.

---

## 6. How to keep the suite green while doing this

- **One meaning change per pull request.** Step 2 changes what a result means;
  do not combine it with a structural move, or a failing test will not say
  which of the two broke it.
- **Write the guard test before the move it protects.** Each step above names
  a guard test. Write it first, watch it fail, then make the change.
- **Run the offline suite the way CI does**, from the repository root:
  `python zmart_drivers/leica/stellaris5_y42h93/navigator_expert/run_ci.py`.
  It runs both suites, lint and coverage, and writes its reports to
  `tests/_report/`.
- **Run on the LAS X simulator after steps 2, 4 and 5.** Those three change
  what the hardware does, not only what the code reports. Steps 1 and 3 are
  pure reorganisation and the offline suite is enough.
- **Update this plan as you go.** When a step is done, say so at the top of
  its section with the date and the pull request, so the next person knows
  where to start.
