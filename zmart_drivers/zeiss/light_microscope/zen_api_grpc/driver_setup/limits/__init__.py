"""The limits: how far the stage and the focus may travel, which objectives and experiments may be chosen.

Measured once with the notebook (``notebooks/set_limits.ipynb``) and saved
as ``limits/<datetime>/limits.json`` in this microscope's settings; loaded
at every connect by the handshake, which hands them to the dispatcher's
gate. The rules themselves are in ``limit_rules.py``.
"""
