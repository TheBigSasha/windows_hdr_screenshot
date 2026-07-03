"""Color-space math.

The capture pipeline hands us **scRGB FP16**: linear light, BT.709 primaries,
where a channel value of ``1.0`` == 80 nits (the scRGB reference white). SDR
"paper white" sits at ``sdr_white_nits / 80`` in this space; anything brighter is
an HDR highlight. Values can be slightly negative (colors outside BT.709).

This module converts that buffer into the various things encoders need:
  * an 8-bit sRGB base image (for previews and the UltraHDR base layer),
  * a linear gain map vs. that base,
  * 10-bit BT.2020 PQ code values (for HEIC),
and answers "does this region actually contain HDR?".
"""
from __future__ import annotations

import numpy as np

SCRGB_REFERENCE_NITS = 80.0


# --------------------------------------------------------------------------- #
# sRGB transfer
# --------------------------------------------------------------------------- #
def srgb_oetf(linear: np.ndarray) -> np.ndarray:
    """Linear -> sRGB encoded (both in [0, 1])."""
    linear = np.clip(linear, 0.0, 1.0)
    return np.where(linear <= 0.0031308,
                    linear * 12.92,
                    1.055 * np.power(linear, 1.0 / 2.4) - 0.055)


def srgb_eotf(encoded: np.ndarray) -> np.ndarray:
    """sRGB encoded -> linear (both in [0, 1])."""
    encoded = np.clip(encoded, 0.0, 1.0)
    return np.where(encoded <= 0.04045,
                    encoded / 12.92,
                    np.power((encoded + 0.055) / 1.055, 2.4))


# --------------------------------------------------------------------------- #
# scRGB -> SDR base
# --------------------------------------------------------------------------- #
def sdr_scale(sdr_white_nits: float) -> float:
    return max(sdr_white_nits, 1.0) / SCRGB_REFERENCE_NITS


def scrgb_to_sdr_linear(linear: np.ndarray, sdr_white_nits: float) -> np.ndarray:
    """Map scRGB so diffuse white -> 1.0, clip to the SDR cube [0, 1].

    Highlights above paper white are clipped here; the gain map restores them.
    """
    out = linear.astype(np.float32) / sdr_scale(sdr_white_nits)
    return np.clip(out, 0.0, 1.0)


def scrgb_to_sdr_u8(linear: np.ndarray, sdr_white_nits: float) -> np.ndarray:
    """scRGB float -> 8-bit sRGB RGB image (H, W, 3) uint8."""
    sdr_lin = scrgb_to_sdr_linear(linear[..., :3], sdr_white_nits)
    enc = srgb_oetf(sdr_lin)
    return np.clip(enc * 255.0 + 0.5, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- #
# HDR detection
# --------------------------------------------------------------------------- #
def hdr_stats(linear: np.ndarray, sdr_white_nits: float) -> dict:
    """Summarise HDR headroom in a captured buffer."""
    rgb = linear[..., :3].astype(np.float32)
    scale = sdr_scale(sdr_white_nits)
    norm = rgb / scale                      # 1.0 == paper white
    lum = norm @ np.array([0.2627, 0.6780, 0.0593], np.float32)  # BT.2020-ish luma
    peak = float(np.max(norm)) if norm.size else 0.0
    # fraction of pixels meaningfully above paper white
    frac = float(np.mean(lum > 1.02)) if lum.size else 0.0
    return {
        "peak_ratio": peak,                                  # brightest channel / paper white
        "peak_nits": float(np.max(rgb)) * SCRGB_REFERENCE_NITS if rgb.size else 0.0,
        "hdr_pixel_fraction": frac,
        "has_hdr": peak > 1.05 and frac > 1e-5,
    }


# --------------------------------------------------------------------------- #
# BT.2020 PQ (for HEIC)
# --------------------------------------------------------------------------- #
_BT709_TO_BT2020 = np.array([
    [0.62740389, 0.32928304, 0.04331307],
    [0.06909729, 0.91954040, 0.01136231],
    [0.01639144, 0.08801331, 0.89559525],
], dtype=np.float32)

_PQ_M1 = 2610.0 / 16384.0
_PQ_M2 = 2523.0 / 4096.0 * 128.0
_PQ_C1 = 3424.0 / 4096.0
_PQ_C2 = 2413.0 / 4096.0 * 32.0
_PQ_C3 = 2392.0 / 4096.0 * 32.0


def pq_oetf(nits: np.ndarray) -> np.ndarray:
    """Absolute luminance (nits) -> PQ signal in [0, 1] (SMPTE ST 2084)."""
    lp = np.clip(nits / 10000.0, 0.0, 1.0)
    num = _PQ_C1 + _PQ_C2 * np.power(lp, _PQ_M1)
    den = 1.0 + _PQ_C3 * np.power(lp, _PQ_M1)
    return np.power(num / den, _PQ_M2)


def scrgb_to_pq_bt2020_u16(linear: np.ndarray, bit_depth: int = 10) -> np.ndarray:
    """scRGB FP16 -> BT.2020 PQ code values, left-justified in uint16.

    Returns (H, W, 3) uint16 with ``bit_depth`` significant bits.
    """
    rgb = np.clip(linear[..., :3].astype(np.float32), 0.0, None)
    nits = rgb * SCRGB_REFERENCE_NITS
    nits2020 = np.clip(nits @ _BT709_TO_BT2020.T, 0.0, 10000.0)
    pq = pq_oetf(nits2020)
    maxv = (1 << bit_depth) - 1
    return np.clip(pq * maxv + 0.5, 0, maxv).astype(np.uint16)


# --------------------------------------------------------------------------- #
# Preview tone-mapping (for the selection overlay only)
# --------------------------------------------------------------------------- #
def scrgb_to_preview_u8(linear: np.ndarray, sdr_white_nits: float) -> np.ndarray:
    """Like scrgb_to_sdr_u8 but with a gentle highlight rolloff so HDR content
    is recognisable in the (SDR) selection overlay rather than flat-white."""
    rgb = np.clip(linear[..., :3].astype(np.float32), 0.0, None) / sdr_scale(sdr_white_nits)
    # Keep midtones exact up to the knee, then roll highlights smoothly into [knee, 1]
    # so HDR content reads as a gradient in the SDR overlay instead of a flat clip.
    knee = 0.8
    span = 1.0 - knee
    comp = knee + span * (1.0 - np.exp(-np.maximum(rgb - knee, 0.0) / span))
    rolled = np.where(rgb <= knee, rgb, comp)
    enc = srgb_oetf(np.clip(rolled, 0.0, 1.0))
    return np.clip(enc * 255.0 + 0.5, 0, 255).astype(np.uint8)
