"""Read-only Win32 ACL checks for the owner's local handoff directories."""
from __future__ import annotations

import ctypes
import os
import re
import stat
import struct
from ctypes import wintypes
from pathlib import Path

# Binary well-known SIDs, independent of the Windows display language.
_SYSTEM = bytes.fromhex("01 01 00 00 00 00 00 05 12 00 00 00")  # S-1-5-18
_ADMINISTRATORS = bytes.fromhex("01 02 00 00 00 00 00 05 20 00 00 00 20 02 00 00")  # S-1-5-32-544
_DESCRIPTOR = struct.Struct("<BxHIIII")  # SECURITY_DESCRIPTOR_RELATIVE
_ACL = struct.Struct("<BxHHH")
_ACE = struct.Struct("<BBH")


def _sid(data: bytes, start: int, limit: int) -> bytes:
    if not 0 <= start <= limit - 8 or data[start] != 1 or data[start + 1] > 15:
        raise ValueError("invalid SID")
    end = start + 8 + 4 * data[start + 1]
    if end > limit:
        raise ValueError("truncated SID")
    return data[start:end]


def _private_descriptor(data: bytes, user: bytes, *, require_user_owner: bool) -> bool:
    """Read the OS's binary DACL, including inherited and inherit-only grants."""
    trusted = {user, _SYSTEM, _ADMINISTRATORS}
    try:
        revision, control, owner, _, _, dacl = _DESCRIPTOR.unpack_from(data)
        # Require SE_SELF_RELATIVE and SE_DACL_PRESENT, and reject a NULL DACL
        # (allows everyone). No best-effort interpretation of unknown ACEs.
        if (revision != 1 or control & 0x8004 != 0x8004
                or min(owner, dacl) < _DESCRIPTOR.size or max(owner, dacl) >= len(data)):
            return False
        owner_sid = _sid(data, owner, len(data))
        if owner_sid not in trusted or (require_user_owner and owner_sid != user):
            return False
        revision, size, count, _ = _ACL.unpack_from(data, dacl)
        end = dacl + size
        offset = dacl + _ACL.size
        if revision not in (2, 4) or not offset <= end <= len(data):
            return False
        for _ in range(count):
            kind, _, length = _ACE.unpack_from(data, offset)
            next_offset = offset + length
            if kind not in (0, 1) or length < 16 or next_offset > end:
                return False
            who = _sid(data, offset + 8, next_offset)  # ACE_HEADER and ACCESS_MASK precede the SID
            if len(who) + 8 != length or (kind == 0 and who not in trusted):
                return False
            offset = next_offset
    except (ValueError, struct.error):
        return False
    return True


def _bind(dll, name, result, *args):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = args
    return function


def _query_buffer(function, *args):
    needed = wintypes.DWORD()
    function(*args, None, 0, ctypes.byref(needed))
    if not 0 < needed.value <= 256 * 1024:
        raise OSError("Windows security information unavailable")
    buffer = ctypes.create_string_buffer(needed.value)
    if not function(*args, buffer, len(buffer), ctypes.byref(needed)):
        raise OSError("Windows security information unreadable")
    return buffer


class _WindowsSecurity:
    def __init__(self):
        kernel = ctypes.WinDLL("kernel32.dll", winmode=0x800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
        advapi = ctypes.WinDLL("advapi32.dll", winmode=0x800)
        self._process = _bind(kernel, "GetCurrentProcess", wintypes.HANDLE)
        self._close = _bind(kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
        self._open = _bind(advapi, "OpenProcessToken", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD,
                           ctypes.POINTER(wintypes.HANDLE))
        self._token_info = _bind(advapi, "GetTokenInformation", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD)
        self._sid_length = _bind(advapi, "GetLengthSid", wintypes.DWORD, wintypes.LPVOID)
        self._attributes = _bind(kernel, "GetFileAttributesW", wintypes.DWORD, wintypes.LPCWSTR)
        self._drive_type = _bind(kernel, "GetDriveTypeW", wintypes.UINT, wintypes.LPCWSTR)
        self._volume = _bind(kernel, "GetVolumeInformationW", wintypes.BOOL, wintypes.LPCWSTR, wintypes.LPWSTR,
                             wintypes.DWORD, wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD,
                             wintypes.LPWSTR, wintypes.DWORD)
        self._security = _bind(advapi, "GetFileSecurityW", wintypes.BOOL, wintypes.LPCWSTR, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD)

    def fixed_ntfs(self, root: str) -> bool:
        if self._drive_type(root) != 3:  # DRIVE_FIXED, never UNC, remote or removable
            return False
        flags, name = wintypes.DWORD(), ctypes.create_unicode_buffer(32)
        return (bool(self._volume(root, None, 0, None, None, ctypes.byref(flags), name, len(name)))
                and name.value == "NTFS" and bool(flags.value & 0x8))  # FILE_PERSISTENT_ACLS

    def user(self) -> bytes:
        token = wintypes.HANDLE()
        if not self._open(self._process(), 0x8, ctypes.byref(token)):  # TOKEN_QUERY
            raise OSError("Windows process identity unavailable")
        try:
            info = _query_buffer(self._token_info, token, 1)  # TokenUser; first field is the SID pointer
            sid = ctypes.cast(info, ctypes.POINTER(ctypes.c_void_p))[0]
            if not sid:
                raise OSError("Windows process identity has no SID")
            length = self._sid_length(sid)
            if not 8 <= length <= 68:
                raise OSError("Windows process identity has an invalid SID")
            return ctypes.string_at(sid, length)
        finally:
            self._close(token)

    def attributes(self, path: Path) -> int:
        attrs = self._attributes(str(path))
        if attrs == 0xffffffff or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError("unreadable Windows path or reparse point")
        return attrs

    def descriptor(self, path: Path) -> bytes:
        return _query_buffer(self._security, str(path), 0x5).raw  # OWNER | DACL_SECURITY_INFORMATION


def is_private_windows_dir(path: Path) -> bool:
    """Conservatively allow user-owned trees under a private local NTFS profile.

    OS-managed ancestors above the profile and admins/SYSTEM are trusted. This
    cannot protect against others executing as the owner or privileged software.
    """
    try:
        # Reject nonlocal paths before doing any filesystem I/O on the target.
        if (not re.fullmatch(r"[A-Za-z]:\\", path.anchor)
                or any(part == ".." or ":" in part for part in path.parts[1:])):
            return False
        profile = Path.home()
        if profile not in path.parents:
            return False
        security = _WindowsSecurity()
        if not security.fixed_ntfs(path.anchor):
            return False
        user = security.user()
        # Walk from the drive root down, without following junctions or other
        # reparse points. Require the profile and intervening dirs to be private
        # too: a writable parent could let another account replace the target.
        for entry in reversed((path, *path.parents)):
            if not security.attributes(entry) & stat.FILE_ATTRIBUTE_DIRECTORY:
                return False
            if (entry == profile or profile in entry.parents) and not _private_descriptor(
                    security.descriptor(entry), user, require_user_owner=entry == path):
                return False
        # Windows can bypass a parent's traverse check. A private directory
        # does not alone make a pre-existing file/object's ACL private.
        pending = [path]
        while pending:
            with os.scandir(pending.pop()) as children:
                for child in children:
                    entry = Path(child.path)
                    attrs = security.attributes(entry)
                    if not _private_descriptor(security.descriptor(entry), user, require_user_owner=False):
                        return False
                    if attrs & stat.FILE_ATTRIBUTE_DIRECTORY:
                        pending.append(entry)
        return True
    except (AttributeError, OSError, ValueError):
        return False
