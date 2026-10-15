# Plan: one copy of every capture, from the cache or AutoSave, as OME-TIFF or OME-Zarr

*Status: built on 11 October 2026, on `release-candidate` of ZMART-drivers, ZMART-interface,
ZMART-viewer and ZMART-controller; checked on the LAS X simulator, and on the STELLARIS on 15
October 2026 with the tests of
[`SCOPE_CHECK_CACHE_ROUTE.md`](../zmart_drivers/leica/stellaris5_y42h93/navigator_expert/sessions/SCOPE_CHECK_CACHE_ROUTE.md).
This page records what was built, and where it went another way than planned.*

## What a capture is now

Two choices in the acquisition settings decide what is written:

| Setting | Values | Default |
|---|---|---|
| `source` | `"cache"`: straight from LAS X's cache (the folder the registry names, `D:\Temp2` on Y42H93); `"autosave"`: from AutoSave's files, the backup | `"autosave"` until the microscope check passes |
| `format` | `"ome-zarr"`: one OME-Zarr 0.5 image; `"ome-tiff"`: one OME-TIFF a plane | `"ome-zarr"` |
| `projection` | `"max"`: a stack's max projection beside it; `"none"` | `"max"` |
| `save_to` | a folder for this capture | the data location |

Only the chosen format is written, and nothing converts it afterwards. Each acquisition folder
holds:

```
<run>/<type>/data/          the capture (+ data/metadata: ZMART's state and LAS X's own description)
<run>/<type>/projections/   a stack's max projection (a single plane is its own and gets none)
<run>/<type>/log/           what the driver was asked for each capture, and what it did
<run>/view/                 the viewer's live pictures
```

Every file carries LAS X's full record of the capture in OME 2016-06: the instrument, every
channel's dye, laser, detector and detection band, and, per plane, its canvas position, each
drive's position and its time.

## Where it went another way than planned

- **The OME-Zarr writer lives in the driver** (`output/ome_zarr.py`), as its layout plan has it.
  It does not live in the controller.
- **It is written with OME's `ome-zarr-models` and zarr-python**, not ngio. ngio imports h5py,
  whose HDF5 library clashes with the one OpenCV loads, and the driver uses OpenCV.
- **The store holds only the full-resolution level.** The viewer builds coarser levels itself.
  The store leaves empty values out of its description: a `"unit": null` made the stand-alone
  viewer draw nothing.
- **The analysis needed no change.** Its steps already read OME-Zarr stores.
- **Drivers without a Zarr writer** (the interface's mock, Nikon, ZEISS, mesoSPIM) are still
  converted by the interface, so the viewer works for them. Their own writer is a later step.
- **The data location is a set-up item of the driver**, like the limits, with its own notebook.
  It was not discovered beside AutoSave's folder. It has no default.
- **The controller's contract grew** for this: `get_info` says where a microscope saves
  (`output_root`) and what is set up on it (`setup`), and each acquisition setting may say its
  `default`. Before, a class driver could give the controller only its description.

## Commits

- ZMART-drivers:
  - `9825a8f` the cache route;
  - `f4df6de` the full metadata;
  - `6b948be` the drive positions;
  - `ea78414` the stack heights;
  - `c1dc8b7` the format;
  - `a2c868d` OME-Zarr by default;
  - `450315c` the canvas positions;
  - `6fc6a11` the projections and the log;
  - `b899d7a` the data location;
  - `114da7d` the job match;
  - `c81e1fb` the microscope check;
  - `152c036` the contract;
  - `cb61e14` no empty values.
- ZMART-interface:
  - `9ddea60` the driver's store;
  - `4ce402c` projections and log filed, the viewer reads them;
  - `157a8b6` connect names what is not set up.
- ZMART-viewer: `e2fae2d` reads given projections.
- ZMART-controller: `4ba1afe` the contract.
