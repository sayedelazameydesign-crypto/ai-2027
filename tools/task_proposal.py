#!/usr/bin/env python3
"""Generate a read-only exact-replacement proposal; never run code or change files."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

MAX_BYTES = 256 * 1024
FORBIDDEN = {'.git', '.env', '.netrc', '.npmrc', '.pypirc', '.git-credentials', 'credentials.json'}


class ProposalError(ValueError):
    pass


def validate_path(value: object) -> list[str]:
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise ProposalError('مسار غير صالح')
    parts = value.split('/')
    if any(part in ('', '.', '..') for part in parts) or PurePosixPath(value).is_absolute():
        raise ProposalError('المسار يجب أن يكون نسبياً وداخل مساحة العمل')
    for part in parts:
        low = part.lower()
        if (low in FORBIDDEN or low.startswith('.env.') or
                low.endswith(('.pem', '.key', '.p12', '.pfx'))):
            raise ProposalError('المسار محظور لحماية الملفات الحساسة')
    return parts


def read_bounded(root: Path, parts: list[str]) -> bytes:
    if not hasattr(os, 'O_NOFOLLOW') or not os.supports_dir_fd or os.open not in os.supports_dir_fd:
        raise ProposalError('المنصة لا تدعم قراءة آمنة للمسارات دون روابط رمزية')
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
    fds = []
    try:
        root_fd = os.open(root, flags | os.O_DIRECTORY)
        fds.append(root_fd)
        directory = root_fd
        for part in parts[:-1]:
            directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            fds.append(directory)
        fd = os.open(parts[-1], flags | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory)
        fds.append(fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            raise ProposalError('الملف ليس ملفاً عادياً أو يتجاوز الحد المسموح')
        data = b''
        while len(data) <= MAX_BYTES:
            chunk = os.read(fd, min(65536, MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        if len(data) > MAX_BYTES:
            raise ProposalError('الملف يتجاوز الحد المسموح')
        return data
    except OSError as exc:
        raise ProposalError('تعذر فتح الملف بأمان؛ تحقق من المسار والروابط الرمزية') from exc
    finally:
        for fd in reversed(fds):
            os.close(fd)


def propose(root: Path, request: dict) -> dict:
    if not isinstance(request, dict) or set(request) != {'path', 'old', 'new'}:
        raise ProposalError('يجب تقديم path وold وnew فقط')
    parts = validate_path(request['path'])
    old, new = request['old'], request['new']
    if not isinstance(old, str) or not old or not isinstance(new, str) or old == new:
        raise ProposalError('يجب أن يكون old نصاً غير فارغ وأن يختلف عن new')
    if max(len(old.encode('utf-8')), len(new.encode('utf-8'))) > MAX_BYTES:
        raise ProposalError('النص المطلوب أكبر من الحد المسموح')
    try:
        original_bytes = read_bounded(root, parts)
        original = original_bytes.decode('utf-8')
        if '\x00' in original:
            raise ProposalError('الملفات الثنائية غير مدعومة')
        if original.count(old) != 1:
            raise ProposalError('يجب أن يظهر النص القديم مرة واحدة بالضبط')
        updated = original.replace(old, new, 1)
        updated_bytes = updated.encode('utf-8')
        if len(updated_bytes) > MAX_BYTES:
            raise ProposalError('نتيجة التعديل أكبر من الحد المسموح')
    except UnicodeError as exc:
        raise ProposalError('الملف أو النص ليس UTF-8 صالحاً') from exc
    name = '/'.join(parts)
    diff = ''.join(difflib.unified_diff(original.splitlines(keepends=True),
                                        updated.splitlines(keepends=True),
                                        fromfile='a/' + name, tofile='b/' + name))
    before = hashlib.sha256(original_bytes).hexdigest()
    after = hashlib.sha256(updated_bytes).hexdigest()
    # Bind explicit human confirmation to this exact path and both byte versions.
    identity = json.dumps({'version': 1, 'workspace': str(root.absolute()), 'path': name,
                           'before': before, 'after': after},
                          sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    proposal_id = hashlib.sha256(identity).hexdigest()
    return {'status': 'proposal_only', 'path': name, 'diff': diff,
            'before_sha256': before, 'proposed_sha256': after, 'proposal_id': proposal_id,
            'file_modified': False, 'tests_run': False, 'model_used': False,
            'warning': 'Preview only; not tested or applied. Diff may contain sensitive source text.'}


def approve(root: Path, request: dict, expected_id: str) -> dict:
    """Re-read and match a reviewed proposal. This receipt does not apply the edit."""
    if not isinstance(expected_id, str) or len(expected_id) != 64 or any(c not in '0123456789abcdef' for c in expected_id):
        raise ProposalError('معرّف الموافقة غير صالح')
    result = propose(root, request)
    if not hmac.compare_digest(result['proposal_id'], expected_id):
        raise ProposalError('تغير الملف أو الطلب منذ المعاينة؛ راجع مقترحاً جديداً')
    result['status'] = 'confirmed_not_applied'
    result['warning'] = ('Explicit local confirmation only; no authentication, modification or test. '
                         'Revalidate immediately before any future application.')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='workspace directory')
    parser.add_argument('--confirm', metavar='PROPOSAL_ID', help='confirm the reviewed preview only; does not apply it')
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES * 3 + 1)
        if len(raw) > MAX_BYTES * 3:
            raise ProposalError('الطلب أكبر من الحد المسموح')
        request = json.loads(raw)
        result = approve(args.root, request, args.confirm) if args.confirm is not None else propose(args.root, request)
    except (ProposalError, ValueError, UnicodeError, OSError) as exc:
        print(json.dumps({'status': 'rejected', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
