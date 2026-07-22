"""
nvcolor.py — NVIDIA desktop colour control.

Digital Vibrance is set through NVAPI (nvapi64.dll) using the (undocumented but
stable) Digital Vibrance Control interface. Gamma is set through the Windows
GDI gamma ramp (SetDeviceGammaRamp), which writes the same GPU LUT the NVIDIA
Control Panel "Gamma" slider uses.

Vibrance is expressed as 0-100 %, matching the NVIDIA Control Panel scale:
50 % = neutral default, below 50 % desaturates, above 50 % oversaturates.
Gamma is expressed as a float, default 1.00 (range clamped to 0.30 - 2.80).
"""

import math
import ctypes
from ctypes import c_int, c_uint, c_void_p, Structure, POINTER, byref

# --------------------------------------------------------------------------- #
#  NVAPI (Digital Vibrance)
# --------------------------------------------------------------------------- #

# nvapi64.dll / nvapi.dll export a single function, nvapi_QueryInterface(id),
# which returns a pointer to the real function for the given interface id.
_IDS = {
    "NvAPI_Initialize":               0x0150E828,
    "NvAPI_Unload":                   0xD22BDD7E,
    "NvAPI_EnumNvidiaDisplayHandle":  0x9ABDD40D,
    "NvAPI_GetDVCInfo":               0x4085DE45,
    "NvAPI_SetDVCLevel":              0x172409B4,
    "NvAPI_GetDVCInfoEx":             0x0E45002D,
    "NvAPI_SetDVCLevelEx":            0x4A82C2B1,
}

_nvapi = None
_query = None
_funcs = {}
_initialized = False


class NV_DISPLAY_DVC_INFO(Structure):
    _fields_ = [
        ("version",      c_uint),
        ("currentLevel", c_int),
        ("minLevel",     c_int),
        ("maxLevel",     c_int),
    ]


class NV_DISPLAY_DVC_INFO_EX(Structure):
    # Extended DVC: full 0-100 scale with 50 as the neutral default,
    # matching the NVIDIA Control Panel (supports desaturation below 50).
    _fields_ = [
        ("version",      c_uint),
        ("currentLevel", c_int),
        ("minLevel",     c_int),
        ("maxLevel",     c_int),
        ("defaultLevel", c_int),
    ]


def _load():
    global _nvapi, _query
    if _nvapi is not None:
        return
    last = None
    for name in ("nvapi64.dll", "nvapi.dll"):
        try:
            _nvapi = ctypes.WinDLL(name)
            break
        except OSError as e:
            last = e
    if _nvapi is None:
        raise OSError("nvapi64.dll not found (no NVIDIA driver?)") from last
    _query = _nvapi.nvapi_QueryInterface
    _query.restype = c_void_p
    _query.argtypes = [c_uint]


def _get(name, restype, argtypes):
    fn = _funcs.get(name)
    if fn is not None:
        return fn
    addr = _query(_IDS[name])
    if not addr:
        raise OSError(f"NVAPI function unavailable: {name}")
    fn = ctypes.CFUNCTYPE(restype, *argtypes)(addr)   # NVAPI is __cdecl
    _funcs[name] = fn
    return fn


def _init():
    global _initialized
    if _initialized:
        return
    _load()
    _get("NvAPI_Initialize", c_int, [])()
    _initialized = True


def _enum_displays():
    enum = _get("NvAPI_EnumNvidiaDisplayHandle", c_int, [c_uint, POINTER(c_void_p)])
    handles = []
    i = 0
    while True:
        h = c_void_p()
        if enum(i, byref(h)) != 0:      # non-zero -> end of enumeration / error
            break
        handles.append(h)
        i += 1
    return handles


def available():
    """True if an NVIDIA GPU / NVAPI is usable on this machine."""
    try:
        _init()
        return len(_enum_displays()) > 0
    except Exception:
        return False


def _set_vibrance_ex(percent):
    """Extended DVC (0-100, 50 = neutral). Returns count applied, or None if
    the extended interface isn't available on this driver."""
    try:
        get = _get("NvAPI_GetDVCInfoEx", c_int,
                   [c_void_p, c_uint, POINTER(NV_DISPLAY_DVC_INFO_EX)])
        setl = _get("NvAPI_SetDVCLevelEx", c_int,
                    [c_void_p, c_uint, POINTER(NV_DISPLAY_DVC_INFO_EX)])
    except OSError:
        return None
    ver = ctypes.sizeof(NV_DISPLAY_DVC_INFO_EX) | (1 << 16)
    applied = 0
    for h in _enum_displays():
        info = NV_DISPLAY_DVC_INFO_EX()
        info.version = ver
        if get(h, 0, byref(info)) != 0:
            continue
        lo, hi = info.minLevel, info.maxLevel
        info.currentLevel = int(round(lo + (percent / 100.0) * (hi - lo)))
        setl(h, 0, byref(info))
        applied += 1
    return applied


def _set_vibrance_basic(percent):
    """Fallback for old drivers: basic DVC only covers neutral->max, so map
    the upper half of the slider and treat <=50 % as neutral."""
    get = _get("NvAPI_GetDVCInfo", c_int,
               [c_void_p, c_uint, POINTER(NV_DISPLAY_DVC_INFO)])
    setl = _get("NvAPI_SetDVCLevel", c_int, [c_void_p, c_uint, c_int])
    frac = max(0.0, (percent - 50.0) / 50.0)      # 50 %->0.0, 100 %->1.0
    applied = 0
    for h in _enum_displays():
        info = NV_DISPLAY_DVC_INFO()
        info.version = ctypes.sizeof(NV_DISPLAY_DVC_INFO) | (1 << 16)
        if get(h, 0, byref(info)) != 0:
            continue
        setl(h, 0, int(round(info.minLevel + frac * (info.maxLevel - info.minLevel))))
        applied += 1
    return applied


def set_vibrance(percent):
    """Set Digital Vibrance on every NVIDIA display (0-100, 50 = neutral).
    Returns the number of displays updated."""
    _init()
    percent = max(0.0, min(100.0, float(percent)))
    applied = _set_vibrance_ex(percent)
    if applied is None:
        applied = _set_vibrance_basic(percent)
    return applied


def get_vibrance():
    """Live Digital Vibrance (0-100) from the first NVIDIA display, or None.
    Reflects changes made in the NVIDIA Control Panel."""
    try:
        _init()
        get = _get("NvAPI_GetDVCInfoEx", c_int,
                   [c_void_p, c_uint, POINTER(NV_DISPLAY_DVC_INFO_EX)])
        ver = ctypes.sizeof(NV_DISPLAY_DVC_INFO_EX) | (1 << 16)
        for h in _enum_displays():
            info = NV_DISPLAY_DVC_INFO_EX()
            info.version = ver
            if get(h, 0, byref(info)) != 0:
                continue
            lo, hi = info.minLevel, info.maxLevel
            if hi == lo:
                return None
            return int(round((info.currentLevel - lo) * 100.0 / (hi - lo)))
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------- #
#  Gamma  (GDI gamma ramp)
# --------------------------------------------------------------------------- #

_gdi32 = ctypes.WinDLL("gdi32")
_user32 = ctypes.WinDLL("user32")
_gdi32.SetDeviceGammaRamp.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi32.SetDeviceGammaRamp.restype = ctypes.c_int
_gdi32.GetDeviceGammaRamp.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi32.GetDeviceGammaRamp.restype = ctypes.c_int
_user32.GetDC.argtypes = [ctypes.c_void_p]
_user32.GetDC.restype = ctypes.c_void_p
_user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


def set_gamma(gamma):
    """Apply a gamma ramp to the primary display. gamma 1.00 == neutral."""
    gamma = max(0.30, min(2.80, float(gamma)))
    ramp = (ctypes.c_ushort * 256 * 3)()
    inv = 1.0 / gamma
    for i in range(256):
        v = int(round(65535.0 * ((i / 255.0) ** inv)))
        v = max(0, min(65535, v))
        ramp[0][i] = v      # R
        ramp[1][i] = v      # G
        ramp[2][i] = v      # B
    hdc = _user32.GetDC(None)
    try:
        ok = _gdi32.SetDeviceGammaRamp(hdc, byref(ramp))
    finally:
        _user32.ReleaseDC(None, hdc)
    return bool(ok)


def get_gamma():
    """Approximate gamma derived from the primary display's live LUT, or None.
    Reflects Control Panel changes. Assumes a gamma-shaped ramp; NVCP
    brightness/contrast tweaks can skew the estimate slightly."""
    ramp = (ctypes.c_ushort * 256 * 3)()
    hdc = _user32.GetDC(None)
    try:
        ok = _gdi32.GetDeviceGammaRamp(hdc, byref(ramp))
    finally:
        _user32.ReleaseDC(None, hdc)
    if not ok:
        return None
    mid = ramp[1][128] / 65535.0        # green midpoint;  mid == x ** (1/gamma)
    x = 128 / 255.0
    if not (0.0 < mid < 1.0):
        return None
    try:
        inv = math.log(mid) / math.log(x)
    except ValueError:
        return None
    if inv <= 0:
        return None
    return round(1.0 / inv, 2)
