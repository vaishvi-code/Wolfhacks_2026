"""Single-writer local JSON storage with validated atomic replacement."""
import json
import os
from pathlib import Path
import tempfile

from .snapshot import CacheError, snapshot_from_dict


class SnapshotStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self, coverage=None):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError as exc:
            raise CacheError('MISSING', 'No local dynamic snapshot exists.') from exc
        except (UnicodeError, ValueError) as exc:
            raise CacheError('MALFORMED', 'Snapshot is not valid UTF-8 JSON.') from exc
        except OSError as exc:
            raise CacheError('READ_FAILED', 'Unable to read the local snapshot.') from exc
        snapshot = snapshot_from_dict(data)
        if coverage is not None and snapshot.coverage != coverage:
            raise CacheError('COVERAGE_MISMATCH', 'Snapshot belongs to a different configured region.')
        return snapshot

    def save(self, snapshot):
        """Validate before writing; fsync temp file then replace in same directory.

        A failed pre-replace operation leaves the previous file untouched. This
        is atomic visibility, not a multi-writer transaction or backup service.
        """
        data = snapshot.to_dict()
        snapshot_from_dict(data)
        temporary = None
        try:
            payload = json.dumps(data, indent=2, allow_nan=False) + '\n'
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=self.path.parent, prefix=f'.{self.path.name}.',
                                             suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except (OSError, ValueError, TypeError) as exc:
            raise CacheError('WRITE_FAILED', 'Could not atomically persist snapshot; previous file retained.') from exc
        finally:
            if temporary is not None and temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass  # An orphan temp file is preferable to masking the cache error.
