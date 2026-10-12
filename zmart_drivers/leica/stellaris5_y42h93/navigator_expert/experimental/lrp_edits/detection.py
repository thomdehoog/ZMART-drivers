"""
Detection edits: a detector's offset and acquisition mode, a channel's band.
==============================================================================
The CAM API has no command for any of these; LAS X keeps them in each job's
settings in the saved experiment, so they are changed there and the
experiment is loaded back (``scanfields.transaction.apply_lrp_change``).

Where LAS X keeps them, inside each ``ATLConfocalSettingDefinition`` of a job:

- ``DetectorList/Detector``, by ``Name``: ``Offset``, ``AcquisitionMode`` and
  ``AcquisitionModeName``: Standard 0 (photon integration), PhotonCounting 1,
  BrightR 2.
- ``Spectro/MultiBand``, by ``Channel``: the band's ``LeftWorld`` and
  ``RightWorld`` in nm, with ``TargetWaveLengthBegin``/``End`` beside them.

An edit changes the matching element in every setting of the job (the
master and each sequential setting), splices only the attributes it sets so
the rest of the file stays as LAS X wrote it, and never adds an attribute.

Dependency direction: imports ``_primitives``' job-block convention only
through the file's own ``BlockName`` marker; stdlib otherwise.
"""

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger(__name__)

#: LAS X's detector acquisition modes, by the name it writes beside the number.
DETECTOR_MODES = {"Standard": 0, "PhotonCounting": 1, "BrightR": 2}

ATTRIBUTE = re.compile(r'(?<=\s)([\w:.-]+)="([^"]*)"')


def number_text(value) -> str:
    """A number as LAS X writes it in the file: ``2.5``, ``500``."""
    return format(float(value), "g")


def job_block_span(text: str, job_name: str):
    """Where the job's block starts and ends in the file's text, or None."""
    start = text.find(f'BlockName="{job_name}"')
    if start == -1:
        return None
    end = text.find("<LDM_Block_Sequence_Block", start + 1)
    return start, end if end != -1 else len(text)


def set_job_element_attributes(lrp_path, job_name, tag, match, changes, caller):
    """Set *changes* on every *tag* element of the job whose attributes include *match*.

    Returns the number of elements changed; the file is written only when
    one was. An element lacking any attribute in *changes* is left alone.
    """
    lrp_path = Path(lrp_path)
    text = lrp_path.read_text(encoding="utf-8")
    span = job_block_span(text, job_name)
    if span is None:
        log.error("%s: job %r not found", caller, job_name)
        return 0
    start, end = span
    block = text[start:end]
    changed = 0

    def edit(found):
        nonlocal changed
        element = found.group(0)
        present = dict(ATTRIBUTE.findall(element))
        if any(present.get(k) != str(v) for k, v in match.items()):
            return element
        if any(k not in present for k in changes):
            return element
        new = element
        for name, value in changes.items():
            new = re.sub(
                rf'(?<=\s){re.escape(name)}="[^"]*"', f'{name}="{value}"', new, count=1
            )
        if new != element:
            changed += 1
        return new

    block = re.sub(rf"<{re.escape(tag)}\s[^>]*>", edit, block)
    if changed:
        lrp_path.write_text(text[:start] + block + text[end:], encoding="utf-8")
    log.info(
        "%s: job=%r %s%s -> %s, %d elements changed",
        caller,
        job_name,
        tag,
        match,
        changes,
        changed,
    )
    return changed


def job_element_values(lrp_path, job_name, tag, match, attribute):
    """Every value of *attribute* on the job's *tag* elements matching *match*; None if no such job."""
    root = ET.parse(Path(lrp_path)).getroot()
    for block in root.findall(".//LDM_Block_Sequence_Block"):
        sequential = block.find(".//LDM_Block_Sequential")
        if sequential is not None and sequential.get("BlockName") == job_name:
            return [
                el.get(attribute)
                for el in block.iter(tag)
                if el.get(attribute) is not None
                and all(el.get(k) == str(v) for k, v in match.items())
            ]
    return None


def all_equal(lrp_path, job_name, tag, match, expected):
    """Whether the job has such elements and each carries every expected attribute value."""
    for attribute, value in expected.items():
        found = job_element_values(lrp_path, job_name, tag, match, attribute)
        if not found or any(v != value for v in found):
            return False
    return True


def lrp_set_detector_offset(lrp_path, value, job_name, detector):
    """Set the offset of the detector named *detector* (e.g. ``"HyD 2"``)."""
    return set_job_element_attributes(
        lrp_path,
        job_name,
        "Detector",
        {"Name": detector},
        {"Offset": number_text(value)},
        "lrp_set_detector_offset",
    )


def lrp_verify_detector_offset(lrp_path, value, job_name, detector):
    return all_equal(
        lrp_path,
        job_name,
        "Detector",
        {"Name": detector},
        {"Offset": number_text(value)},
    )


def lrp_set_detector_mode(lrp_path, mode, job_name, detector):
    """Set the acquisition mode of the detector named *detector*: Standard, PhotonCounting or BrightR."""
    if mode not in DETECTOR_MODES:
        log.error(
            "lrp_set_detector_mode: %r is not one of %s", mode, sorted(DETECTOR_MODES)
        )
        return 0
    return set_job_element_attributes(
        lrp_path,
        job_name,
        "Detector",
        {"Name": detector},
        {"AcquisitionMode": str(DETECTOR_MODES[mode]), "AcquisitionModeName": mode},
        "lrp_set_detector_mode",
    )


def lrp_verify_detector_mode(lrp_path, mode, job_name, detector):
    return mode in DETECTOR_MODES and all_equal(
        lrp_path,
        job_name,
        "Detector",
        {"Name": detector},
        {"AcquisitionMode": str(DETECTOR_MODES[mode]), "AcquisitionModeName": mode},
    )


def lrp_set_detection_band(lrp_path, begin_nm, end_nm, job_name, channel):
    """Set channel *channel*'s detection band to *begin_nm* .. *end_nm*."""
    if not begin_nm < end_nm:
        log.error(
            "lrp_set_detection_band: the band must begin before it ends (%s..%s)",
            begin_nm,
            end_nm,
        )
        return 0
    begin, end = number_text(begin_nm), number_text(end_nm)
    return set_job_element_attributes(
        lrp_path,
        job_name,
        "MultiBand",
        {"Channel": channel},
        {
            "LeftWorld": begin,
            "RightWorld": end,
            "TargetWaveLengthBegin": begin,
            "TargetWaveLengthEnd": end,
        },
        "lrp_set_detection_band",
    )


def lrp_verify_detection_band(lrp_path, begin_nm, end_nm, job_name, channel):
    return all_equal(
        lrp_path,
        job_name,
        "MultiBand",
        {"Channel": channel},
        {"LeftWorld": number_text(begin_nm), "RightWorld": number_text(end_nm)},
    )
