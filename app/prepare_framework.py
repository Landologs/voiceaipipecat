"""Stage the supplied framework for pip without changing its source directory."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    source = ROOT / "pipecat"
    destination = ROOT / ".build/pipecat"
    files = {str(path.relative_to(source)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(source.rglob("*")) if path.is_file()}
    record = ROOT / "framework-source.json"
    if record.exists() and json.loads(record.read_text()) != files:
        raise RuntimeError("Pipecat snapshot differs from framework-source.json; review before rebuilding")
    shutil.copytree(source, destination, dirs_exist_ok=True)
    # Source archives lack Git-driven setuptools file discovery. Include the existing models.
    with (destination / "MANIFEST.in").open("a", encoding="utf-8") as stream:
        stream.write("\ninclude src/pipecat/audio/vad/data/silero_vad.onnx\n"
                     "include src/pipecat/audio/turn/smart_turn/data/smart-turn-v3.2-cpu.onnx\n")
    record.write_text(json.dumps(files, indent=2) + "\n", encoding="utf-8")
    print("Framework staged; source fingerprint verified. No packages installed by this command.")


if __name__ == "__main__":
    prepare()
