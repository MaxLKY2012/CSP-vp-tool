"""Build a clean source release using only the Python standard library."""
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
RELEASE_FILES = (
    "app.py",
    "README.md",
    "start.bat",
    "start.sh",
    "web/index.html",
    "web/style.css",
    "web/app.js",
    "tests/test_app.py",
    "tools/build_release.py",
)


def main():
    date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    target = output / f"csp-vp-tool-{date}.zip"
    staging = target.with_suffix(".zip.tmp")
    # Fail before producing an incomplete archive if a required file is missing.
    for name in RELEASE_FILES:
        if not (ROOT / name).is_file():
            raise FileNotFoundError(name)
    with ZipFile(staging, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name in RELEASE_FILES:
            archive.write(ROOT / name, "csp-vp-tool/" + name)
    with ZipFile(staging) as archive:
        invalid = archive.testzip()
        if invalid:
            raise ValueError(f"ZIP integrity check failed: {invalid}")
    staging.replace(target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".zip.sha256").write_text(f"{digest}  {target.name}\n", encoding="ascii")
    print(f"Archive: {target}")
    print(f"Files: {len(RELEASE_FILES)}; bytes: {target.stat().st_size}")
    print(f"SHA-256: {digest}")


if __name__ == "__main__":
    main()
