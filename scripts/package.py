#!/usr/bin/env python3
"""Build the reproducible GuatePass delivery archive.

The archive is built from tracked files only, after verifying that the
checkout is clean. Local environments, generated portal configuration,
credentials and reports are deliberately excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


REQUIRED_FILES = (
    "README.md",
    "template.yaml",
    "samconfig.toml",
    "statemachines/espera_recarga.asl.json",
    "seeds/guatepass/clientes.csv",
    "seeds/guatepass/integrantes.csv",
    "seeds/guatepass/peajes.csv",
    "seeds/guatepass/registro_transito.csv",
    "externo/template.yaml",
    "externo/seeds/seed.py",
)

EXCLUDED_PARTS = {
    ".git",
    ".aws-sam",
    ".aws-sam-registro",
    ".pytest_cache",
    "node_modules",
    "__pycache__",
    ".venv",
    ".venv312",
    ".venv-task11",
    ".venv-sam312",
    "reports",
    "reportes",
    "tmp",
}

EXCLUDED_NAMES = {
    ".env",
    "credentials",
    "guatepass.zip",
    "portales/config.json",
}

EXCLUDED_SUFFIXES = (".pyc", ".pem", ".key", ".p12", ".pfx")


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    names = [item for item in result.stdout.decode().split("\0") if item]
    return [root / name for name in names]


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _excluded(path: Path, root: Path) -> bool:
    relative = _relative(path, root)
    parts = set(Path(relative).parts)
    if parts & EXCLUDED_PARTS:
        return True
    if relative in EXCLUDED_NAMES:
        return True
    if path.name.startswith(".env"):
        return True
    return path.suffix.lower() in EXCLUDED_SUFFIXES


def _git_status(root: Path, output: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    )
    output_relative = _relative(output, root) if output.is_relative_to(root) else None
    dirty = []
    for line in result.stdout.splitlines():
        candidate = line[3:].strip() if len(line) >= 3 else line.strip()
        if output_relative and candidate == output_relative:
            continue
        dirty.append(line)
    return dirty


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(output: Path, allow_dirty: bool) -> dict[str, object]:
    root = _root()
    output = output.resolve()
    if output == root / "scripts" / "package.py":
        raise SystemExit("El ZIP no puede reemplazar scripts/package.py")

    dirty = _git_status(root, output)
    if dirty and not allow_dirty:
        raise SystemExit(
            "El checkout tiene cambios. Commitéalos antes de empaquetar o usa "
            "--allow-dirty para una prueba local.\n" + "\n".join(dirty)
        )

    files = [
        path
        for path in _tracked_files(root)
        if path.is_file() and not _excluded(path, root)
    ]
    names = {_relative(path, root) for path in files}
    missing = [name for name in REQUIRED_FILES if name not in names]
    if missing:
        raise SystemExit("Faltan archivos requeridos: " + ", ".join(missing))

    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files, key=lambda item: _relative(item, root)):
            archive.write(path, _relative(path, root))

    manifest = {
        "archive": output.name,
        "files": len(files),
        "sha256": _sha256(output),
        "required_files": list(REQUIRED_FILES),
        "excluded": sorted(EXCLUDED_PARTS | EXCLUDED_NAMES),
    }
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("guatepass.zip"),
        help="Ruta del ZIP de entrega; por defecto, guatepass.zip",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Permite empaquetar un checkout con cambios; no usar para la entrega.",
    )
    args = parser.parse_args()
    build(args.output, args.allow_dirty)
    return 0


if __name__ == "__main__":
    sys.exit(main())
