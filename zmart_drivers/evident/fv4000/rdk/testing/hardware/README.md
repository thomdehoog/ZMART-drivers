# Checks at the microscope

`validate_hardware.py` is run on the computer that reaches FLUOVIEW (the FLUOVIEW
computer itself, or one on the same network), with FLUOVIEW open and its Remote
Development Kit switched on. The offline suite proves the limits gate first; then the
script connects, reads everything the driver can read, and, only when asked, makes a
small move and back and takes one picture.

```powershell
cd zmart_drivers/evident/fv4000/rdk

# Reads only: connect, the version, the stage, the objectives, the channels, the lasers, the state
python testing/run_ci.py --hardware

# Also a 20 um move along X and back, through the limits
python testing/run_ci.py --hardware --allow-move

# Also one capture of what is set up in FLUOVIEW, saved as OME-Zarr under the reports
python testing/run_ci.py --hardware --allow-move --allow-acquire

# FLUOVIEW on another computer
python testing/run_ci.py --hardware --host=10.100.100.100
```

Every run writes `testing/report/hardware_check.json`: each check with PASS or FAIL and
what was read. Nothing in this driver has run on an FV4000 yet (27 October 2026); this
script is the first thing to run there, and the README of the driver lists what to look
at when it does.
