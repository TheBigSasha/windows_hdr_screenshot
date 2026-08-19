"""Small structural AVIF fixtures for metadata-parser tests."""
from __future__ import annotations

import struct


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def fullbox(version: int = 0, flags: int = 0) -> bytes:
    return bytes((version,)) + flags.to_bytes(3, "big")


def _infe(item_id: int, item_type: bytes, name: bytes) -> bytes:
    payload = fullbox(2) + struct.pack(">HH4s", item_id, 0, item_type) + name + b"\0"
    return box(b"infe", payload)


def make_avif(
    *,
    nclx: tuple[int, int, int, int] = (9, 16, 9, 1),
    bit_depth: int = 10,
    chroma: str = "yuv444",
    stray_nclx: bytes = b"",
    gain_map: bool = False,
) -> bytes:
    """Build a metadata-only AVIF with real pitm/ipco/ipma associations."""
    if bit_depth not in (8, 10, 12):
        raise ValueError(bit_depth)
    chroma_flags = {
        "yuv444": 0,
        "yuv422": 1 << 3,
        "yuv420": (1 << 3) | (1 << 2),
    }[chroma]
    depth_flags = (1 << 6 if bit_depth >= 10 else 0) | (1 << 5 if bit_depth == 12 else 0)
    cp, tc, mc, full_range = nclx
    properties = [
        box(b"ispe", fullbox() + struct.pack(">II", 320, 200)),
        box(b"pixi", fullbox() + bytes((3, bit_depth, bit_depth, bit_depth))),
        box(b"av1C", bytes((0x81, 0x20, depth_flags | chroma_flags, 0))),
        box(b"colr", b"nclx" + struct.pack(">HHH", cp, tc, mc) + bytes((full_range << 7,))),
    ]
    ipco = box(b"ipco", b"".join(properties))
    ipma = box(
        b"ipma",
        fullbox() + struct.pack(">IHB", 1, 1, len(properties)) + bytes(range(1, len(properties) + 1)),
    )
    iprp = box(b"iprp", ipco + ipma)

    items = [_infe(1, b"av01", b"Color")]
    extra = b""
    if gain_map:
        items.extend([
            _infe(2, b"av01", b"GainMap"),
            _infe(3, b"tmap", b"ToneMap"),
        ])
        dimg = box(b"dimg", struct.pack(">HHHH", 3, 2, 1, 2))
        extra += box(b"iref", fullbox() + dimg)
        altr = box(b"altr", fullbox() + struct.pack(">IIIII", 7, 3, 3, 1, 2))
        extra += box(b"grpl", altr)

    iinf = box(b"iinf", fullbox() + struct.pack(">H", len(items)) + b"".join(items))
    pitm = box(b"pitm", fullbox() + struct.pack(">H", 1))
    meta = box(b"meta", fullbox() + pitm + iinf + iprp + extra)
    ftyp = box(b"ftyp", b"avif" + b"\0\0\0\0" + b"avifmif1miafMA1B")
    return ftyp + meta + box(b"mdat", stray_nclx)
