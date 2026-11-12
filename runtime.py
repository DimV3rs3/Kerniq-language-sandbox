"""Download and place private Node.js and .NET builds. Host copies stay unused."""

from __future__ import annotations

import io
import os
import platform
import shutil
import tarfile
import zipfile
from pathlib import Path

import httpx

from Kerniq.sandbox.proc import hidden_run
from Kerniq.sandbox.runtimes import host_runtime_reason, isolate_runtime_path, runtime_root

_NODE_INDEX = "https://nodejs.org/dist/index.json"
_DOTNET_PS1 = "https://dot.net/v1/dotnet-install.ps1"
_DOTNET_SH = "https://dot.net/v1/dotnet-install.sh"
_MAX_BYTES = 400 * 1024 * 1024


def flags() -> tuple[bool, bool]:
    """(isolate node, isolate dotnet). A missing setting means on."""
    try:
        from Kerniq.settings.component_store import ComponentSettingsStore
        from Kerniq.settings.loader import SettingsProvider

        state = SettingsProvider().config.storage.state_dir
        values = ComponentSettingsStore(state).load("plugin", "plugin_lang_sandbox")
    except Exception:
        values = {}
    if not isinstance(values, dict):
        values = {}
    if values.get("enabled") is False:
        return False, False
    return values.get("isolate_node", True) is not False, values.get("isolate_dotnet", True) is not False


def filter_env(env: dict, workspace: Path) -> dict:
    node, dotnet = flags()
    return isolate_runtime_path(env, workspace, node=node, dotnet=dotnet)


def block_command(command: str) -> str:
    node, dotnet = flags()
    if not node and not dotnet:
        return ""
    try:
        from Kerniq.settings.loader import SettingsProvider
        from Kerniq.settings.ui_store import UiSettingsStore

        cfg = SettingsProvider().config
        raw = Path(UiSettingsStore(cfg.storage.state_dir).load().agent.workspace or "./workspace")
        if not raw.is_absolute():
            raw = Path.cwd() / raw
        workspace = raw.resolve()
    except Exception:
        return ""
    return host_runtime_reason(command, runtime_root(workspace), node=node, dotnet=dotnet)


def _safe_extract_zip(payload: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError("Archive path escapes the runtime folder.")
        archive.extractall(dest)


def _safe_extract_tar(payload: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            name = member.name.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError("Archive path escapes the runtime folder.")
        archive.extractall(dest)


def _pick_node(version: str) -> tuple[str, str]:
    want = (version or "").strip()
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        rows = client.get(_NODE_INDEX).json()
    chosen = None
    if want and want != "lts":
        for row in rows:
            if str(row.get("version") or "") in {want, "v" + want.lstrip("v")}:
                chosen = row
                break
    if chosen is None:
        for row in rows:
            if row.get("lts"):
                chosen = row
                break
    if chosen is None and rows:
        chosen = rows[0]
    if not chosen:
        raise RuntimeError("Node.js release list was empty.")
    ver = str(chosen["version"])
    machine = platform.machine().lower()
    arm = machine in {"aarch64", "arm64"}
    if os.name == "nt":
        name = f"node-{ver}-win-x64.zip"
    elif platform.system() == "Darwin":
        name = f"node-{ver}-darwin-{'arm64' if arm else 'x64'}.tar.gz"
    else:
        name = f"node-{ver}-linux-{'arm64' if arm else 'x64'}.tar.gz"
    return ver, f"https://nodejs.org/dist/{ver}/{name}"


def _find_node_home(extracted: Path) -> Path | None:
    direct = extracted / ("node.exe" if os.name == "nt" else "bin/node")
    if direct.exists():
        return extracted
    for child in extracted.iterdir():
        if not child.is_dir():
            continue
        marker = child / ("node.exe" if os.name == "nt" else "bin/node")
        if marker.exists():
            return child
    return None


def prepare_node(workspace: Path, version: str = "lts") -> dict:
    root = runtime_root(workspace)
    staging = root / ".node-incoming"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    try:
        ver, url = _pick_node(version)
        with httpx.Client(timeout=180.0, follow_redirects=True) as client:
            response = client.get(url)
        if response.status_code != 200:
            return {"ok": False, "error": f"Node.js download failed: HTTP {response.status_code}"}
        if len(response.content) > _MAX_BYTES:
            return {"ok": False, "error": "Node.js download is larger than expected."}
        if url.endswith(".zip"):
            _safe_extract_zip(response.content, staging)
        else:
            _safe_extract_tar(response.content, staging)
        home = _find_node_home(staging)
        if home is None:
            return {"ok": False, "error": "The Node.js archive did not contain node."}
        dest = root / "node"
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.move(str(home), str(dest))
        return {"ok": True, "version": ver, "path": str(dest)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def prepare_dotnet(workspace: Path, channel: str = "8.0") -> dict:
    root = runtime_root(workspace)
    dest = root / "dotnet"
    dest.mkdir(parents=True, exist_ok=True)
    channel = (channel or "8.0").strip() or "8.0"
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            if os.name == "nt":
                script = root / "dotnet-install.ps1"
                script.write_bytes(client.get(_DOTNET_PS1).content)
                proc = hidden_run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                        "-InstallDir",
                        str(dest),
                        "-Channel",
                        channel,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=600,
                    check=False,
                )
                marker = dest / "dotnet.exe"
            else:
                script = root / "dotnet-install.sh"
                script.write_bytes(client.get(_DOTNET_SH).content)
                script.chmod(script.stat().st_mode | 0o755)
                proc = hidden_run(
                    ["sh", str(script), "--install-dir", str(dest), "--channel", channel],
                    capture_output=True,
                    text=True,
                    timeout=600,
                    check=False,
                )
                marker = dest / "dotnet"
        if proc.returncode != 0 or not marker.is_file():
            detail = ((proc.stderr or "") + (proc.stdout or ""))[-400:]
            return {"ok": False, "error": detail or "dotnet-install did not produce a runtime."}
        return {"ok": True, "channel": channel, "path": str(dest)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
