"""Checkpoint + progress files on the reports volume (atomic writes)."""
import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Checkpoint:
    """State: {"stage": ..., "scanners": {...}, "done_files": [...], "ai": {...}}"""

    def __init__(self, review_dir: Path):
        self.dir = Path(review_dir)
        self.path = self.dir / "checkpoint.json"
        self.progress_path = self.dir / "progress.json"
        self.state = {"stage": "start", "scanners": {}, "done_files": [], "ai_files": {}, "ai_summary": None}

    def load(self) -> bool:
        if self.path.is_file():
            try:
                self.state.update(json.loads(self.path.read_text()))
                return True
            except json.JSONDecodeError:
                return False
        return False

    def save(self) -> None:
        atomic_write_json(self.path, self.state)

    def write_progress(self, percent: int, message: str) -> None:
        atomic_write_json(self.progress_path, {"progress": percent, "message": message, "stage": self.state["stage"]})

    def paused(self) -> bool:
        return (self.dir / "PAUSE").exists()
