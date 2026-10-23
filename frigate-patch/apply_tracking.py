"""Apply the optional tracking bundle only to the exact verified base sources."""
import hashlib
import json
from pathlib import Path
import re


def apply_unified(source, patch):
    original = source.splitlines(keepends=True)
    lines = patch.splitlines(keepends=True)
    result = []
    cursor = 0
    index = 0
    while index < len(lines):
        match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', lines[index])
        if not match:
            index += 1
            continue
        start = int(match[1]) - 1
        if start < cursor or start > len(original):
            raise ValueError('Invalid or overlapping patch hunk')
        result.extend(original[cursor:start])
        old, new = [], []
        index += 1
        while index < len(lines) and not lines[index].startswith('@@ '):
            line = lines[index]
            if line.startswith((' ', '-')):
                old.append(line[1:])
            if line.startswith((' ', '+')):
                new.append(line[1:])
            if not line.startswith((' ', '-', '+')):
                raise ValueError('Unsupported patch line')
            index += 1
        if len(old) != int(match[2] or 1) or len(new) != int(match[4] or 1):
            raise ValueError('Patch hunk size mismatch')
        if original[start:start + len(old)] != old:
            raise ValueError('Patch context mismatch')
        result.extend(new)
        cursor = start + len(old)
    result.extend(original[cursor:])
    return ''.join(result)


def apply_bundle(root, manifest_path, patch_path):
    manifest = json.loads(manifest_path.read_text())
    sections = {}
    current = None
    for line in patch_path.read_text().splitlines(keepends=True):
        if line.startswith('--- a/'):
            current = line.removeprefix('--- a/').strip()
            sections[current] = []
        elif line.startswith('+++ b/'):
            if current != line.removeprefix('+++ b/').strip():
                raise ValueError('Patch path mismatch')
        elif current:
            sections[current].append(line)
    if sections.keys() != manifest.keys():
        raise ValueError('Patch file list differs from manifest')
    prepared = []
    # Verify every input, hunk and output before modifying any source file.
    for relative, hashes in manifest.items():
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ValueError('Invalid source path')
        path = root / relative
        source = path.read_bytes()
        if hashlib.sha256(source).hexdigest() != hashes['before']:
            raise ValueError(f'Unexpected upstream source: {relative}; review for this release')
        updated = apply_unified(source.decode(), ''.join(sections[relative])).encode()
        if hashlib.sha256(updated).hexdigest() != hashes['after']:
            raise ValueError(f'Patched source hash mismatch: {relative}')
        prepared.append((path, updated))
    for path, updated in prepared:
        path.write_bytes(updated)


if __name__ == '__main__':
    here = Path(__file__).resolve().parent
    apply_bundle(Path('/opt/frigate'), here / 'tracking-manifest.json', here / 'tracking.patch')
