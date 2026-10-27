# The check at NIS-Elements

The offline suite runs against a mock. This folder holds the check that runs at
NIS-Elements itself, on the Ti2 simulator or on a microscope, with the bridge
running (`start_bridge.mac`; the bridge's README says how to start it).

```
python zmart_drivers/nikon/nis_elements_ar610/testing/run_ci.py --hardware --save-to D:\ZMART-data\check
```

or the script itself, `check_microscope.py --yes --save-to <folder>`. It connects
as a user does, reads everything, moves the stage 20 micrometres along x and
back, selects the first optical configuration, sets the exposure, takes one
picture and saves it as an OME-Zarr store, and prints PASS or FAIL for each
step. The stage ends where it started. Keep the objective clear of the sample
on a first run, and narrow the limits first for anything you cannot replace.

**Status.** The driver has not yet been run at NIS-Elements. The bridge it
builds on was validated on the Ti2 simulator of NIS-Elements AR 6.10.02 (see
the bridge's README); the driver's first run there is the next step, and this
check is what to run.
