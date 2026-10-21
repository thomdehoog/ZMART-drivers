"""A CAM API request object that knows its own name.

A change is written into one of the CAM API's request objects (``client.PyApiSetZoomByJobName``
and so on). A route that does not use the CAM API to send it, the direct route, needs to know
which command it is, and the object itself does not say. This wrapper carries the name and
passes everything else (``Model``, ``UpdateAsync``, ``UpdateAwaitReceipt``) on to the object.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""


class Request:
    """A CAM request object together with its name, such as ``"PyApiSetZoomByJobName"``."""

    def __init__(self, name, cam_object):
        self.name = name
        self.cam_object = cam_object

    def __getattr__(self, attribute):
        return getattr(self.cam_object, attribute)


def request_of(client, name):
    """The CAM client's request object called *name*, wrapped with its name."""
    return Request(name, getattr(client, name))
