"""Run on its own (``python -I worker.py <request.json>``): one request to LAS X's data container, answered as JSON.

LAS X keeps its ROI regions and bleach points in its data container, which
LCS does not reach. LAS X's own window reaches it through LAS X's .NET
wrapper of the data container client (``LMSDataContainerWrapper.dll`` in
LAS X's ``BIN``), and so does this program, in a process of its own: it loads
.NET Framework code (through pythonnet, kept apart from the driver's
environment) and must not take the driver down with it.

A request is ``{"do": "read" | "add" | "clear", "set": <element id>, "xml": <set xml, for add>}``. ``add`` loads the
XML as a temporary set, moves its children into the set, and deletes the temporary set, as the viewer's Load ROIs
does; ``clear`` deletes the set's children; each answers the set's XML as it is afterwards. The wrapper's version must
be one the driver knows; another is refused before anything is touched.

Found by observation (simulator, 2026-11-03): the wrapper's public listing (its methods and their
parameters, version 2.65.0.0), the sets' ids in LCS's state tree, and the regions LAS X itself wrote for
shapes drawn in its window.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import json
import os
import sys

#: The wrapper versions this program was learned on (LAS X 4.9.0.30051, simulator, 2026-11-03).
KNOWN_VERSIONS = ("2.65.0.0",)


def answer(**fields) -> None:
    sys.stdout.write(json.dumps(fields))
    sys.stdout.flush()


def main(request_path: str) -> None:
    with open(request_path, encoding="utf-8") as handle:
        request = json.load(handle)
    sys.path.insert(0, request["pythonnet"])
    bin_dir = request["bin"]
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    os.add_dll_directory(bin_dir)
    os.chdir(bin_dir)

    from pythonnet import load

    load("netfx")
    import clr
    from System.Reflection import Assembly

    assembly = Assembly.LoadFrom(os.path.join(bin_dir, "LMSDataContainerWrapper.dll"))
    version = str(assembly.GetName().Version)
    if version not in KNOWN_VERSIONS:
        answer(success=False, message=f"LAS X's data container wrapper is version {version}, not one the driver was "
               f"learned on ({', '.join(KNOWN_VERSIONS)}); nothing was touched")
        return
    clr.AddReference("LMSDataContainerWrapper")
    from LMSDataContainerWrapper import DataContainerClient, DMObject, EElementVisibility  # noqa: I001 -- loaded only now, from LAS X

    if not DataContainerClient.CreateAndInitInstance(request.get("user", "zmart"), "ZMART driver"):
        answer(success=False, message="LAS X's data container did not take the connection; nothing was touched")
        return
    try:
        target, do = int(request["set"]), request["do"]
        if do == "add":
            client = DataContainerClient.Instance()
            loaded, temporary = client.LoadElementFromXML(request["xml"], True, 0)
            if not loaded or not temporary:
                answer(success=False, message="LAS X's data container did not take the regions' XML; nothing was added")
                return
            try:
                for child in list(DMObject.GetChildrenInfo(temporary, EElementVisibility.eElmVis_All)):
                    if not client.MoveElement(child.ElementID, target):
                        answer(success=False, message=f"LAS X's data container did not move {child.Name} into set {target}",
                               xml=DataContainerClient.SaveElementToXML(target, True))
                        return
            finally:
                DMObject.GetObjectPtrFromID(temporary).Delete()
        elif do == "clear":
            for child in list(DMObject.GetChildrenInfo(target, EElementVisibility.eElmVis_All)):
                DMObject.GetObjectPtrFromID(child.ElementID).Delete()
        elif do != "read":
            answer(success=False, message=f"the data container is asked to read, add or clear, not {do!r}")
            return
        answer(success=True, version=version, xml=DataContainerClient.SaveElementToXML(target, True))
    finally:
        DataContainerClient.Exit()


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as why:  # noqa: BLE001 -- every failure is answered, never a traceback the driver must parse
        answer(success=False, message=f"the data container request failed: {type(why).__name__}: {why}")
