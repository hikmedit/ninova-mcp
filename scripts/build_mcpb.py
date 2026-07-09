#!/usr/bin/env python3
"""Build a Claude Desktop extension (.mcpb) for ninova-mcp.

What it does:
  1. Vendors ``ninova_mcp`` + all runtime dependencies into ``server/lib``
     for the CURRENT platform and Python version (compiled wheels such as
     lxml and pydantic-core are platform-specific, so one bundle == one
     platform).
  2. Regenerates the manifest ``version`` and ``tools`` list straight from
     the source of truth in ``ninova_mcp.server`` so they never drift.
  3. Packs everything into ``dist/ninova-mcp-<version>-<platform>.mcpb``
     using the official ``mcpb`` CLI when available, otherwise a plain zip.

Usage:
    python scripts/build_mcpb.py
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MCPB_SRC = ROOT / "mcpb"
BUILD = ROOT / "build" / "mcpb"
DIST = ROOT / "dist"


def _read_version() -> str:
    """Read the version straight from pyproject.toml (no dependencies needed)."""
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def _read_tools() -> list[dict[str, str]] | None:
    """Fresh tool list from source, or None if runtime deps aren't importable.

    On a clean CI runner ``ninova_mcp`` cannot be imported until its
    dependencies exist, so we fall back to the tool list already committed in
    ``mcpb/manifest.json`` instead of failing the build.
    """
    try:
        if str(ROOT / "src") not in sys.path:
            sys.path.insert(0, str(ROOT / "src"))
        from ninova_mcp.server import TOOLS  # noqa: E402

        return [{"name": t["name"], "description": t["description"]} for t in TOOLS]
    except Exception as exc:  # deps not installed in this environment
        print(f"[build] note: keeping committed manifest tools ({exc})")
        return None


def _platform_tag() -> str:
    system = platform.system().lower()  # darwin / windows / linux
    machine = platform.machine().lower()  # arm64 / x86_64 / amd64
    return f"{system}-{machine}"


def _vendor_dependencies(lib_dir: Path) -> None:
    lib_dir.mkdir(parents=True, exist_ok=True)
    print(f"[build] vendoring ninova-mcp + deps into {lib_dir} ...")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(lib_dir),
            "--no-compile",
            str(ROOT),
        ],
        check=True,
    )


def _write_manifest(
    version: str, tools: list[dict[str, str]] | None, dest: Path
) -> None:
    manifest = json.loads((MCPB_SRC / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = version
    if tools is not None:
        manifest["tools"] = tools
    dest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _pack(build_dir: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    mcpb = shutil.which("mcpb")
    npx = shutil.which("npx")
    cmd = None
    if mcpb:
        cmd = [mcpb, "pack", str(build_dir), str(output)]
    elif npx:
        cmd = [npx, "--yes", "@anthropic-ai/mcpb", "pack", str(build_dir), str(output)]

    if cmd:
        print(f"[build] packing with: {' '.join(cmd)}")
        result = subprocess.run(cmd)
        if result.returncode == 0 and output.exists():
            return
        print("[build] mcpb CLI pack failed; falling back to zip")

    print("[build] packing with zipfile fallback")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(build_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(build_dir))


def main() -> int:
    version = _read_version()
    tools = _read_tools()
    tag = _platform_tag()
    print(f"[build] ninova-mcp {version} for {tag} (python {platform.python_version()})")

    if BUILD.exists():
        shutil.rmtree(BUILD)
    (BUILD / "server").mkdir(parents=True, exist_ok=True)

    shutil.copy2(MCPB_SRC / "server" / "main.py", BUILD / "server" / "main.py")
    _write_manifest(version, tools, BUILD / "manifest.json")
    _vendor_dependencies(BUILD / "server" / "lib")

    output = DIST / f"ninova-mcp-{version}-{tag}.mcpb"
    if output.exists():
        output.unlink()
    _pack(BUILD, output)

    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"\n[build] ✅ {output}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
