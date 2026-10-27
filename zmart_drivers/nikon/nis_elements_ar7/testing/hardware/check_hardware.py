"""The first checks at a real Nikon microscope: what the pretend NIS could only assume.

The offline suite runs the driver against a pretend NIS that behaves as the
NIS 7 study showed, and as the plan expects where the study could not tell
(``docs/nikon/PLAN_NIS_ELEMENTS_AR7.md``, section 11). This script asks the
real NIS those open questions, one at a time, and prints each answer, so a
person at the microscope can see whether the driver's assumptions hold.

Start NIS-Elements, run ``start_bridge.mac`` in it, then, on the same computer::

    python check_hardware.py                      read only: nothing moves, no picture
    python check_hardware.py --move               also move the stage 20 um and back, and the focus 2 um and back
    python check_hardware.py --snap D:\\check      also take one picture into that folder
    python check_hardware.py --objective 2        also turn the nosepiece to 2 and back

The moves are small and go through the driver's limits like every other move.
Watch the microscope while they run. Every answer is also saved in
``testing/report/hardware.json``, to attach to a report.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[5]))

from zmart_drivers.nikon.nis_elements_ar7.driver_setup.limits import handshake
from zmart_drivers.nikon.nis_elements_ar7.driver_setup.nis_bridge import macro
from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python import reads, server
from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python.client import NisClient
from zmart_drivers.nikon.nis_elements_ar7.procedures import changes, readings

REPORT = HERE.parents[1] / "report" / "hardware.json"


class Checks:
    """Each check prints its question and NIS's answer, and keeps both for the report."""

    def __init__(self) -> None:
        self.answers: list[dict] = []

    def ask(self, number: str, question: str, how):
        print(f"\n[{number}] {question}")
        try:
            answer = how()
            outcome = "answered"
        except Exception as error:  # noqa: BLE001 -- every failure is an answer worth reporting
            answer = f"{type(error).__name__}: {error}"
            outcome = "failed"
        print(f"    {outcome}: {answer}")
        self.answers.append(
            {
                "check": number,
                "question": question,
                "outcome": outcome,
                "answer": answer,
            }
        )
        return answer if outcome == "answered" else None


def timed(how):
    """*how()*, and how long it took: whether a NIS call waits for the hardware."""
    started = time.monotonic()
    answer = how()
    return {"answer": answer, "seconds": round(time.monotonic() - started, 3)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=server.PORT)
    parser.add_argument(
        "--move",
        action="store_true",
        help="move the stage 20 um and the focus 2 um, and back",
    )
    parser.add_argument("--snap", metavar="FOLDER", help="take one picture into FOLDER")
    parser.add_argument(
        "--objective",
        type=int,
        metavar="POSITION",
        help="turn the nosepiece to POSITION and back",
    )
    args = parser.parse_args(argv)

    client = NisClient(port=args.port, key_file=macro.files()["key"])
    checks = Checks()
    checks.ask(
        "bridge",
        "Does the bridge answer, and is it this driver's version?",
        client.connect,
    )
    checks.ask(
        "version", "Which NIS-Elements is this?", lambda: readings.version(client)
    )

    checks.ask(
        "stage",
        "Where are the stage and the focus (NIS's own micrometres)?",
        lambda: {**readings.xy(client), **readings.z(client)},
    )
    checks.ask(
        "travel",
        "How far may the stage travel, by NIS's own limits?",
        lambda: readings.stage_limits(client),
    )
    checks.ask(
        "11.4",
        "Which of GetSelectedOptConf's three texts holds the name of the selected optical configuration?",
        lambda: {
            "texts": client.read(
                "GetSelectedOptConf",
                [{"ptr": "char"}, {"ptr": "char"}, {"ptr": "char"}, reads.TEXT_SIZE],
            )["values"][:3],
            "configurations": readings.optical_configurations(client),
        },
    )
    checks.ask(
        "nosepiece",
        "Which objectives sit on the nosepiece, and which is in place?",
        lambda: readings.nosepiece(client),
    )
    checks.ask(
        "pfs",
        "Is there a Perfect Focus System, and what does it report?",
        lambda: readings.pfs(client),
    )
    checks.ask(
        "11.2",
        "What does GetOpenedDocumentList answer (the raw text), and which names does the driver read from it?",
        lambda: {
            "raw": client.read(
                "GetOpenedDocumentList", [{"ptr": "char"}, server.DOCUMENT_LIST_SIZE]
            )["values"][0],
            "names": reads.get_open_documents(client),
        },
    )
    checks.ask(
        "11.5",
        "What does Get_ImageInfo answer now (watch NIS for a dialog)?",
        lambda: readings.image_info(client),
    )
    checks.ask(
        "11.6",
        "What does Get_Calibration answer now (2 is taken as micrometres)?",
        lambda: client.read(
            "Get_Calibration",
            [{"ptr": "char"}, {"ptr": "double"}, {"ptr": "double"}, {"ptr": "int"}],
        )["values"],
    )

    changing = args.move or args.snap or args.objective is not None
    if changing:
        status = handshake.connect_handshake(client)
        print(f"\nlimits in force: {status}")

    if args.move:
        start = readings.xy(client)
        z0 = readings.z(client)["z_um"]
        checks.ask(
            "11.1 xy",
            "Does StgMoveXY(x, y, 0) move to an absolute place, and does it wait until the stage arrives?",
            lambda: timed(
                lambda: {
                    "result": changes.move_xy(
                        client, start["x_um"] + 20.0, start["y_um"]
                    ),
                    "now": readings.xy(client),
                }
            ),
        )
        checks.ask(
            "11.1 back",
            "And back again?",
            lambda: timed(
                lambda: changes.move_xy(client, start["x_um"], start["y_um"])[
                    "confirmed"
                ]
            ),
        )
        checks.ask(
            "11.1 z",
            "Does StgMoveZ(z, 0) move the focus to an absolute place, and wait?",
            lambda: timed(
                lambda: {
                    "result": changes.move_z(client, z0 + 2.0),
                    "now": readings.z(client),
                }
            ),
        )
        checks.ask(
            "11.1 z back",
            "And back again?",
            lambda: timed(lambda: changes.move_z(client, z0)["confirmed"]),
        )

    if args.objective is not None:
        before = readings.objective(client)
        z_before = readings.z(client)
        checks.ask(
            "11.9 turn",
            f"Does the nosepiece turn to {args.objective}, and does z read differently after?",
            lambda: timed(
                lambda: {
                    "result": changes.set_objective(client, args.objective),
                    "z before": z_before,
                    "z after": readings.z(client),
                }
            ),
        )
        checks.ask(
            "11.9 back",
            "And back again?",
            lambda: changes.set_objective(client, before["position"])["confirmed"],
        )

    if args.snap:
        folder = Path(args.snap).resolve()
        path = folder / f"check_{time.strftime('%Y%m%d_%H%M%S')}.tif"
        checks.ask(
            "11.3",
            "Does Capture() take a new document, and what does it answer?",
            lambda: timed(lambda: changes.take_picture(client, str(path))),
        )
        if path.exists():

            def look():
                import tifffile

                with tifffile.TiffFile(str(path)) as tif:
                    series = tif.series[0]
                    pixels = series.asarray()
                    return {
                        "axes": series.axes,
                        "shape": list(pixels.shape),
                        "dtype": str(pixels.dtype),
                        "largest value": int(pixels.max()),
                    }

            checks.ask(
                "11.7",
                "Is the TIFF NIS saved the camera's raw values (layout, type, largest value)?",
                look,
            )
        checks.ask(
            "documents after",
            "Which documents are open now (the operator's should all still be there)?",
            lambda: reads.get_open_documents(client),
        )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(checks.answers, indent=2, default=str), encoding="utf-8"
    )
    failed = [a["check"] for a in checks.answers if a["outcome"] == "failed"]
    print(f"\nSaved every answer in {REPORT}")
    print(
        f"{len(checks.answers) - len(failed)} answered, {len(failed)} failed{': ' + ', '.join(failed) if failed else ''}"
    )
    client.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
