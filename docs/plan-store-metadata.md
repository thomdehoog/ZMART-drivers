# Plan: everything about a capture in its OME-Zarr store

A capture's OME-Zarr store is the complete record of that capture: its pixels and
everything that says how they were made. The Leica driver writes it as the agreement in
ZMART-controller [`docs/the_store`](https://github.com/ZMB-UZH/ZMART-controller/blob/release-candidate/docs/the_store/README.md)
says; the interface, the analysis and the viewer take what they need from it. The files
beside it (the log, LAS X's description, `summary.json`) stay as extras for debugging;
the store does not depend on them.

## What we found

- The store is valid OME-Zarr 0.5 (ome-zarr-models accepts it), and the standard OME
  fields it fills are right: pixel size, channels and wavelengths, objective, pinhole,
  offset, acquisition time, plane positions.
- Most of what LAS X knows does not reach the store. LAS X's description that the driver
  already copies holds what a `.lif` holds (756 against 772 facts for Overview007; the 12
  extra in the `.lif` are project bookkeeping). Of its 324 meaningful values, 64 reach the
  store's OME-XML. Missing: detectors and gating, scan settings, beam path, laser lines
  and real power, filter wheels, sequential settings, stack settings, carrier, ROIs, LUTs.
- The `observed` half of the controller state is dropped (`driver_api/acquire.py:108`).
- What the capture actually did (settings asked and used, confirmed or not, start and
  finish) is only in `log/`, written after the store.
- The turn or mirror the driver applies to the pixels is recorded nowhere.
- Small OME-Zarr points: no `metadata` on the multiscale, `type: "mean"` without a
  pyramid, no `omero.rdefs`, uncompressed chunks.
- Readers: napari (ome-zarr-py 0.21) opens today's store, where the root is the image,
  and finds nothing in a bioformats2raw layout; Fiji and OMERO (Bio-Formats with
  OMEZarrReader 0.6.0, built on the Zarr v2 library jzarr) cannot read OME-Zarr 0.5 at
  all; no tool reads `OME/METADATA.ome.xml`. So the layout stays as it is.

## 1. The layout

Unchanged: the store's root is the image, its pixels at `0`, the XML at
`OME/METADATA.ome.xml`. New: `"zmart": {"store": "1"}` beside `ome` in `zarr.json`.

## 2. The Leica driver

What goes into the store, layer by layer (the agreement's sections in brackets):

- **Layer 1** (3): `metadata` saying the store has one level and, for a single plane, that
  its z scale is a placeholder; no `type` while there is one level; `omero.rdefs` and per
  channel `coefficient`, `family`, `inverted`; zstd compression, once the viewer is shown
  to read it.
- **Layer 2** (4): what is there now, plus LAS X's words translated into OME fields
  wherever OME has one: laser power (the white-light laser output, and the AOTF as
  attenuation), detector gain and gating, the stack's z step and direction;
  `Description` (the LAS X image name) and `StageLabel` (the position label) in their
  places.
- **Layer 3** (5), only what the controller contract defines: the controller state
  (`get_state()` whole), the capture record, the image frame (the turn or mirror applied
  to the pixels), the actuator readings per plane position (as `get_xyz` names them, and
  the used actuator).
- **Layer 4** (6), what is the Leica driver's or LAS X's own: LAS X's description, whole,
  its root carrying `xmlns=""`; beside it the readable LAS X key–value list there is now,
  the LAS X job settings and hardware report, the capture's route and timing, the
  changes it could not confirm, and how it makes a position from its readings (the
  origin, the objective calibration offset, z = z-wide + z-galvo).

The export state embedded now is split between layer 3 (what the controller says) and
layer 4 (what LAS X says). With LAS X's description whole, nothing LAS X knows is lost,
including the detector gain it reports twice (2.5 and 10 for Overview007): the OME Gain
field keeps the value the driver uses now.

Where:

- `procedures/save_data/ome_zarr.py` `write_store`: layer 1 and the version, for a capture
  and its projection alike.
- `procedures/save_data/ome_canonical.py`: layers 2 to 4, schema-valid in the order the
  schema requires; the provenance keys it looks for and never finds (`:377-383`) come
  from the capture record.
- `primitives/capture/metadata_map.py`: the new layer-2 fields.
- `driver_api/acquire.py:108`: keeps `observed` as well as `changeable`.
- `procedures/save_data/save.py` / `save_waiting.py`: the capture record and the image
  frame are known before the store is written and handed to it; the log is written from
  the same record.
- `procedures/save_data/save.py:271`: `summary.json`'s pixel size is swapped with the image
  when the orientation turns it, as the store's already is.
- `procedures/save_data/naming.py`: its description of OME-TIFF goes.

## 3. The store is the one source

Whatever the store says, its readers take from it, and nothing is handed alongside but its
path. Today the interface hands the analysis a pixel size, a stage position and an
image-to-stage matrix the store already holds, and its pixel-size fallback is what the
microscope reports *now*, which can be another job's.

- **Analysis**: an OME-Zarr store alone is its input, as for the for-coders template now.
  The geometry fields given alongside go.
- **Interface**: hands the analysis the store's path and nothing else
  (`analysis/detection.py:89-96`, and `:36-61` with its fallback to the microscope's
  current pixel size). Takes frame size, pixel size and position from the store
  (`storage/zarr_positions.py` `frame_um_of`, `storage/jpeg_tiles.py`) and channel names
  from it rather than numbering them.
- **Viewer**: already places, scales and names channels from the store; its z reference
  comes from the store too (`views/publishing.py:527-534`), not from what the interface
  sends.
- **The stores the interface writes** for drivers that save TIFF (`storage/images.py`,
  `storage/zarr_positions.py`) carry layer 1 and the version; layers 2 and 3 as far as the
  interface knows them.
- What lies beyond layer 1 (the objective, the controller state, the capture record) is
  read through one small helper per repo, by namespace, when a reader needs it.

## 4. Checks

- ome-zarr-models accepts the store as an OME-Zarr 0.5 image.
- The OME-XML is valid against schema 2016-06, annotations included.
- The LAS X description in the store has the same content as the one LAS X gave. (That it
  holds what a `.lif` holds was checked once, by hand: Overview007, 756 against 772 facts.)
- The controller state, the capture record, the image frame and the actuator readings
  are in the store, under their namespaces; nothing Leica's is among them.
- napari (ome-zarr-py) opens the store.
- The whole chain on the LAS X simulator: the for-coders notebook, and the interface's scan
  with the viewer.

## 5. Order

1. The agreement, in ZMART-controller `docs/the_store`.
2. The Leica driver writes it: drivers 0.7.0b4.
3. The readers take what they need from the store (3): analysis 0.6.0rc5, viewer 0.5.0rc9,
   interface 0.1.0rc4, and the for-coders template.
4. The checks of 4 on the simulator, then on the STELLARIS.
