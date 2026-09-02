"""Build a manual-install release archive; never deploy or publish it."""

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "custom_components" / "busan_city_gas"
version = json.loads((source / "manifest.json").read_text(encoding="utf-8"))["version"]
destination = ROOT / "dist" / "busan_city_gas.zip"
destination.parent.mkdir(exist_ok=True)
with ZipFile(destination, "x", compression=ZIP_DEFLATED) as archive:
    for file in sorted(source.rglob("*")):
        if (
            file.is_file()
            and "__pycache__" not in file.parts
            and file.suffix in {".py", ".json", ".yaml", ".js", ".png", ".txt"}
        ):
            archive.write(file, file.relative_to(ROOT).as_posix())
print(destination)
print("SHA256", hashlib.sha256(destination.read_bytes()).hexdigest())
