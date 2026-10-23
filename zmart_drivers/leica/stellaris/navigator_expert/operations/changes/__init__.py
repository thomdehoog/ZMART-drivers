"""What can be changed on this microscope, and the rules for each change.

The two changes not yet run through the dispatcher's ``run()``, each with its
profile and its confirmation: ``select_job.py`` and ``acquire.py``. What they
share: ``run_change.py`` (the limits, then the dispatcher) and
``confirming.py`` (reading back, and racing two ways to confirm). The job
settings, the objective and the stage are in ``primitives/cam_api/writes.py``
and ``procedures/changes.py``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
