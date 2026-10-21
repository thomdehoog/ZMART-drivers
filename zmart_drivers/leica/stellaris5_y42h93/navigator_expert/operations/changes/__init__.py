"""What can be changed on this microscope, and the rules for each change.

One file for each change, with its profile and its confirmation:
``job_settings.py`` (the settings of a job, one table), ``stage.py``,
``objective.py``, ``select_job.py`` and ``acquire.py``. What they share:
``run_change.py`` (the limits, then the dispatcher) and ``confirming.py``
(reading back, and racing two ways to confirm).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
