"""Bounded ISO-BMFF metadata inspection for AVIF images.

This module intentionally parses only the small subset of HEIF/AVIF metadata
needed by HDR Shot.  It never decodes image payloads and it associates image
properties through ``pitm``/``ipma`` instead of accepting an incidental
``nclx`` byte string elsewhere in the file.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_BOXES = 4096
MAX_ITEMS = 1024
MAX_PROPERTIES = 1024
MAX_ASSOCIATIONS = 4096


class AvifContainerError(ValueError):
    """The AVIF container is malformed, ambiguous, or exceeds parser limits."""


@dataclass(frozen=True)
class _Box:
    kind: bytes
    start: int
    payload: int
    end: int


@dataclass
class _Budget:
    boxes: int = 0

    def consume(self) -> None:
        self.boxes += 1
        if self.boxes > MAX_BOXES:
            raise AvifContainerError(f"container exceeds {MAX_BOXES} boxes")


def _uint(data: bytes, offset: int, width: int, end: int) -> int:
    if width not in (1, 2, 4, 8) or offset < 0 or offset + width > end:
        raise AvifContainerError("truncated integer field")
    return int.from_bytes(data[offset:offset + width], "big")


def _boxes(data: bytes, start: int, end: int, budget: _Budget):
    if start < 0 or end < start or end > len(data):
        raise AvifContainerError("invalid box bounds")
    pos = start
    while pos < end:
        if end - pos < 8:
            raise AvifContainerError("truncated box header")
        size = _uint(data, pos, 4, end)
        kind = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if end - pos < 16:
                raise AvifContainerError("truncated extended box header")
            size = _uint(data, pos + 8, 8, end)
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or size > end - pos:
            raise AvifContainerError(
                f"invalid {kind.decode('latin1', 'replace')!r} box size {size}"
            )
        budget.consume()
        box_end = pos + size
        yield _Box(kind, pos, pos + header, box_end)
        pos = box_end


def _fullbox(data: bytes, box: _Box) -> tuple[int, int, int]:
    if box.end - box.payload < 4:
        raise AvifContainerError("truncated full-box header")
    version = data[box.payload]
    flags = int.from_bytes(data[box.payload + 1:box.payload + 4], "big")
    return version, flags, box.payload + 4


def _one(boxes: list[_Box], kind: bytes, *, required: bool = False) -> _Box | None:
    found = [box for box in boxes if box.kind == kind]
    if len(found) > 1:
        raise AvifContainerError(
            f"ambiguous duplicate {kind.decode('latin1', 'replace')!r} boxes"
        )
    if required and not found:
        raise AvifContainerError(f"missing {kind.decode('latin1', 'replace')!r} box")
    return found[0] if found else None


def _parse_ftyp(data: bytes, box: _Box) -> tuple[str, list[str]]:
    length = box.end - box.payload
    if length < 8 or (length - 8) % 4:
        raise AvifContainerError("malformed ftyp box")
    major = data[box.payload:box.payload + 4].decode("latin1")
    brands = [major]
    for pos in range(box.payload + 8, box.end, 4):
        brand = data[pos:pos + 4].decode("latin1")
        if brand not in brands:
            brands.append(brand)
    if not ({"avif", "avis"} & set(brands)):
        raise AvifContainerError("ISO-BMFF file does not advertise an AVIF brand")
    return major, brands


def _parse_pitm(data: bytes, box: _Box) -> int:
    version, _, pos = _fullbox(data, box)
    width = 2 if version == 0 else 4 if version == 1 else 0
    if not width or pos + width != box.end:
        raise AvifContainerError("unsupported or malformed pitm box")
    return _uint(data, pos, width, box.end)


def _parse_infe(data: bytes, box: _Box) -> tuple[int, str]:
    version, _, pos = _fullbox(data, box)
    if version == 2:
        item_id = _uint(data, pos, 2, box.end)
        pos += 2
    elif version == 3:
        item_id = _uint(data, pos, 4, box.end)
        pos += 4
    else:
        raise AvifContainerError(f"unsupported infe version {version}")
    if pos + 6 > box.end:
        raise AvifContainerError("truncated infe box")
    pos += 2  # item_protection_index
    item_type = data[pos:pos + 4].decode("latin1")
    return item_id, item_type


def _parse_iinf(data: bytes, box: _Box, budget: _Budget) -> dict[int, str]:
    version, _, pos = _fullbox(data, box)
    width = 2 if version == 0 else 4
    count = _uint(data, pos, width, box.end)
    pos += width
    if count > MAX_ITEMS:
        raise AvifContainerError(f"container exceeds {MAX_ITEMS} items")
    children = list(_boxes(data, pos, box.end, budget))
    entries = [child for child in children if child.kind == b"infe"]
    if len(entries) != count:
        raise AvifContainerError("iinf entry_count does not match infe boxes")
    result: dict[int, str] = {}
    for child in entries:
        item_id, item_type = _parse_infe(data, child)
        if item_id in result:
            raise AvifContainerError(f"duplicate item id {item_id}")
        result[item_id] = item_type
    return result


def _parse_property(data: bytes, box: _Box) -> dict[str, Any]:
    prop: dict[str, Any] = {"type": box.kind.decode("latin1")}
    if box.kind == b"colr":
        if box.end - box.payload < 4:
            raise AvifContainerError("truncated colr property")
        colour_type = data[box.payload:box.payload + 4]
        prop["colour_type"] = colour_type.decode("latin1")
        if colour_type == b"nclx":
            if box.end - box.payload != 11:
                raise AvifContainerError("malformed nclx property")
            cp, tc, mc = struct.unpack_from(">HHH", data, box.payload + 4)
            range_byte = data[box.payload + 10]
            if range_byte & 0x7F:
                raise AvifContainerError("nclx reserved range bits are nonzero")
            prop["nclx"] = {
                "color_primaries": cp,
                "transfer_characteristics": tc,
                "matrix_coefficients": mc,
                # ISO/IEC 23001-8 stores full_range_flag in the high bit.
                "full_range_flag": range_byte >> 7,
            }
        elif colour_type in {b"prof", b"rICC"}:
            prop["icc_profile"] = True
    elif box.kind == b"ispe":
        version, flags, pos = _fullbox(data, box)
        if version != 0 or flags != 0 or pos + 8 != box.end:
            raise AvifContainerError("malformed ispe property")
        prop["width"] = _uint(data, pos, 4, box.end)
        prop["height"] = _uint(data, pos + 4, 4, box.end)
    elif box.kind == b"pixi":
        version, flags, pos = _fullbox(data, box)
        if version != 0 or flags != 0 or pos >= box.end:
            raise AvifContainerError("malformed pixi property")
        channels = data[pos]
        pos += 1
        if channels == 0 or channels > 16 or pos + channels != box.end:
            raise AvifContainerError("invalid pixi channel count")
        bits = list(data[pos:box.end])
        if any(bit == 0 or bit > 16 for bit in bits):
            raise AvifContainerError("invalid pixi bit depth")
        prop["channel_bit_depths"] = bits
        prop["bit_depth"] = max(bits)
    elif box.kind == b"av1C":
        if box.end - box.payload < 4:
            raise AvifContainerError("truncated av1C property")
        marker_version = data[box.payload]
        if marker_version & 0x80 == 0 or marker_version & 0x7F != 1:
            raise AvifContainerError("unsupported av1C marker/version")
        flags = data[box.payload + 2]
        high_bitdepth = (flags >> 6) & 1
        twelve_bit = (flags >> 5) & 1
        monochrome = (flags >> 4) & 1
        subsampling_x = (flags >> 3) & 1
        subsampling_y = (flags >> 2) & 1
        if twelve_bit and not high_bitdepth:
            raise AvifContainerError("invalid av1C bit-depth flags")
        prop["bit_depth"] = 12 if twelve_bit else 10 if high_bitdepth else 8
        if monochrome:
            prop["chroma_format"] = "yuv400"
        else:
            prop["chroma_format"] = {
                (0, 0): "yuv444",
                (1, 0): "yuv422",
                (1, 1): "yuv420",
            }.get((subsampling_x, subsampling_y), "unknown")
    return prop


def _parse_ipma(data: bytes, box: _Box) -> dict[int, list[int]]:
    version, flags, pos = _fullbox(data, box)
    if version not in (0, 1):
        raise AvifContainerError(f"unsupported ipma version {version}")
    item_width = 2 if version == 0 else 4
    entry_count = _uint(data, pos, 4, box.end)
    pos += 4
    if entry_count > MAX_ITEMS:
        raise AvifContainerError(f"container exceeds {MAX_ITEMS} property entries")
    wide_index = bool(flags & 1)
    result: dict[int, list[int]] = {}
    association_total = 0
    for _ in range(entry_count):
        item_id = _uint(data, pos, item_width, box.end)
        pos += item_width
        count = _uint(data, pos, 1, box.end)
        pos += 1
        association_total += count
        if association_total > MAX_ASSOCIATIONS:
            raise AvifContainerError(
                f"container exceeds {MAX_ASSOCIATIONS} property associations"
            )
        indices: list[int] = []
        for _ in range(count):
            if wide_index:
                raw = _uint(data, pos, 2, box.end)
                pos += 2
                index = raw & 0x7FFF
            else:
                raw = _uint(data, pos, 1, box.end)
                pos += 1
                index = raw & 0x7F
            if index:
                indices.append(index)
        if item_id in result:
            raise AvifContainerError(f"duplicate ipma entry for item {item_id}")
        result[item_id] = indices
    if pos != box.end:
        raise AvifContainerError("trailing bytes in ipma box")
    return result


def _parse_iprp(
    data: bytes, box: _Box, budget: _Budget
) -> tuple[list[dict[str, Any]], dict[int, list[int]]]:
    children = list(_boxes(data, box.payload, box.end, budget))
    ipco = _one(children, b"ipco", required=True)
    assert ipco is not None
    property_boxes = list(_boxes(data, ipco.payload, ipco.end, budget))
    if len(property_boxes) > MAX_PROPERTIES:
        raise AvifContainerError(f"container exceeds {MAX_PROPERTIES} properties")
    properties = [_parse_property(data, prop) for prop in property_boxes]
    associations: dict[int, list[int]] = {}
    ipma_boxes = [child for child in children if child.kind == b"ipma"]
    if not ipma_boxes:
        raise AvifContainerError("missing 'ipma' box")
    for ipma in ipma_boxes:
        for item_id, indices in _parse_ipma(data, ipma).items():
            if item_id in associations:
                raise AvifContainerError(f"duplicate ipma entry for item {item_id}")
            associations[item_id] = indices
    return properties, associations


def _parse_iref(data: bytes, box: _Box, budget: _Budget) -> dict[tuple[str, int], list[int]]:
    version, _, pos = _fullbox(data, box)
    width = 2 if version == 0 else 4 if version == 1 else 0
    if not width:
        raise AvifContainerError(f"unsupported iref version {version}")
    result: dict[tuple[str, int], list[int]] = {}
    for ref in _boxes(data, pos, box.end, budget):
        cursor = ref.payload
        from_id = _uint(data, cursor, width, ref.end)
        cursor += width
        count = _uint(data, cursor, 2, ref.end)
        cursor += 2
        if count > MAX_ITEMS or cursor + count * width != ref.end:
            raise AvifContainerError("malformed item reference")
        targets = [_uint(data, cursor + i * width, width, ref.end) for i in range(count)]
        key = (ref.kind.decode("latin1"), from_id)
        if key in result:
            raise AvifContainerError(f"duplicate item reference {key}")
        result[key] = targets
    return result


def _parse_grpl(data: bytes, box: _Box, budget: _Budget) -> list[tuple[str, list[int]]]:
    result: list[tuple[str, list[int]]] = []
    for group in _boxes(data, box.payload, box.end, budget):
        version, _, pos = _fullbox(data, group)
        if version != 0 or pos + 8 > group.end:
            raise AvifContainerError("unsupported or truncated entity group")
        pos += 4  # group_id
        count = _uint(data, pos, 4, group.end)
        pos += 4
        if count > MAX_ITEMS or pos + count * 4 != group.end:
            raise AvifContainerError("malformed entity group")
        ids = [_uint(data, pos + i * 4, 4, group.end) for i in range(count)]
        result.append((group.kind.decode("latin1"), ids))
    return result


def _single_property(
    properties: list[dict[str, Any]], indices: list[int], kind: str
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for index in indices:
        if index < 1 or index > len(properties):
            raise AvifContainerError(f"property association index {index} is out of range")
        prop = properties[index - 1]
        if prop["type"] == kind:
            matches.append(prop)
    if len(matches) > 1:
        raise AvifContainerError(f"primary item has ambiguous {kind} properties")
    return matches[0] if matches else None


def inspect_avif(data: bytes) -> dict[str, Any]:
    """Return bounded, primary-item-associated AVIF metadata.

    ``is_hdr`` is tri-state: ``True`` for a structurally valid ISO gain map or
    a 10/12-bit BT.2020 PQ/HLG primary image, ``False`` for explicitly SDR
    CICP, and ``None`` when the metadata cannot decide.
    """
    if len(data) > MAX_FILE_BYTES:
        raise AvifContainerError(f"file exceeds {MAX_FILE_BYTES} bytes")
    budget = _Budget()
    top = list(_boxes(data, 0, len(data), budget))
    ftyp = _one(top, b"ftyp", required=True)
    meta = _one(top, b"meta", required=True)
    assert ftyp is not None and meta is not None
    major_brand, brands = _parse_ftyp(data, ftyp)

    meta_version, meta_flags, meta_pos = _fullbox(data, meta)
    if meta_version != 0 or meta_flags != 0:
        raise AvifContainerError("unsupported meta full-box header")
    children = list(_boxes(data, meta_pos, meta.end, budget))
    pitm = _one(children, b"pitm", required=True)
    iinf = _one(children, b"iinf", required=True)
    iprp = _one(children, b"iprp", required=True)
    assert pitm is not None and iinf is not None and iprp is not None

    primary_id = _parse_pitm(data, pitm)
    item_types = _parse_iinf(data, iinf, budget)
    if primary_id not in item_types:
        raise AvifContainerError("pitm references an unknown primary item")
    properties, associations = _parse_iprp(data, iprp, budget)
    primary_indices = associations.get(primary_id, [])
    if not primary_indices:
        raise AvifContainerError("primary item has no property associations")

    ispe = _single_property(properties, primary_indices, "ispe")
    pixi = _single_property(properties, primary_indices, "pixi")
    av1c = _single_property(properties, primary_indices, "av1C")
    colr = _single_property(properties, primary_indices, "colr")

    nclx = colr.get("nclx") if colr else None
    bit_depths = [
        value for value in (
            pixi.get("bit_depth") if pixi else None,
            av1c.get("bit_depth") if av1c else None,
        ) if value is not None
    ]
    if len(set(bit_depths)) > 1:
        raise AvifContainerError("pixi and av1C bit depths disagree")
    bit_depth = bit_depths[0] if bit_depths else None

    refs: dict[tuple[str, int], list[int]] = {}
    iref = _one(children, b"iref")
    if iref is not None:
        refs = _parse_iref(data, iref, budget)
    groups: list[tuple[str, list[int]]] = []
    grpl = _one(children, b"grpl")
    if grpl is not None:
        groups = _parse_grpl(data, grpl, budget)

    tmap_ids = [item_id for item_id, kind in item_types.items() if kind == "tmap"]
    valid_tmaps: list[int] = []
    for tmap_id in tmap_ids:
        derived = refs.get(("dimg", tmap_id), [])
        if len(derived) < 2:
            continue
        if any(
            group_kind == "altr" and tmap_id in ids and any(item in ids for item in derived)
            for group_kind, ids in groups
        ):
            valid_tmaps.append(tmap_id)
    gain_map_present = bool(tmap_ids)
    gain_map_valid = bool(valid_tmaps)

    transfer = nclx.get("transfer_characteristics") if nclx else None
    primaries = nclx.get("color_primaries") if nclx else None
    matrix = nclx.get("matrix_coefficients") if nclx else None
    pq = bit_depth in (10, 12) and (primaries, transfer, matrix) == (9, 16, 9)
    hlg = bit_depth in (10, 12) and primaries == 9 and transfer == 18
    if gain_map_valid:
        representation = "gain_map"
        is_hdr: bool | None = True
    elif pq:
        representation = "pq"
        is_hdr = True
    elif hlg:
        representation = "hlg"
        is_hdr = True
    elif nclx is not None:
        representation = "sdr"
        is_hdr = False
    else:
        representation = "unknown"
        is_hdr = None

    return {
        "container": "avif",
        "major_brand": major_brand,
        "compatible_brands": brands,
        "primary_item_id": primary_id,
        "primary_item_type": item_types[primary_id],
        "item_types": dict(sorted(item_types.items())),
        "width": ispe.get("width") if ispe else None,
        "height": ispe.get("height") if ispe else None,
        "bit_depth": bit_depth,
        "channel_bit_depths": pixi.get("channel_bit_depths") if pixi else None,
        "chroma_format": av1c.get("chroma_format") if av1c else None,
        "color_primaries": primaries,
        "transfer_characteristics": transfer,
        "matrix_coefficients": matrix,
        "full_range_flag": nclx.get("full_range_flag") if nclx else None,
        "icc_profile_present": bool(colr and colr.get("icc_profile")),
        "hdr_representation": representation,
        "is_hdr": is_hdr,
        "gainmap_metadata_present": gain_map_present,
        "gainmap_metadata_valid": gain_map_valid,
        "gainmap_item_id": valid_tmaps[0] if valid_tmaps else None,
        "metadata_standard": "ISO 21496-1" if gain_map_valid else "CICP/NCLX" if nclx else None,
        "measurement_source": "metadata_only",
        "viewer_compatibility": "not_tested",
    }
