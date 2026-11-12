"""Private Node.js and .NET for the workspace shell.

Host node and dotnet stay off PATH. A bare `node` or `dotnet` hits a shim
inside the workspace. The shim runs the private copy, or refuses, and never
falls through to the copy installed on the computer.
"""

from __future__ import annotations

from typing import Any, Dict

from Kerniq.interfaces.settings_schema import SettingField, SettingType, SettingsSchema
from Kerniq.plugins.base import Plugin
from Kerniq.plugins.manifest import PluginManifest
from Kerniq.sandbox.runtimes import register_language_runtime, unregister_language_runtime


def _runtime():
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parent / "runtime.py"
    name = "kerniq_lang_sandbox_runtime"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("language sandbox runtime is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _workspace(context) -> "Path":
    from pathlib import Path

    try:
        from Kerniq.settings.ui_store import UiSettingsStore

        state = context.config.storage.state_dir
        raw = Path(UiSettingsStore(state).load().agent.workspace or "./workspace")
    except Exception:
        raw = Path("./workspace")
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    try:
        return raw.resolve()
    except OSError:
        return raw


class PluginImpl(Plugin):
    def __init__(self) -> None:
        self._manifest = PluginManifest(
            plugin_id="plugin_lang_sandbox",
            name="Language sandbox",
            version="1.0.0",
            api_version="1.0",
            entry_point="main.PluginImpl",
            description="Private Node.js and .NET. The copies installed on this computer are not used.",
            capabilities=[],
        )
        self._context = None

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    def settings_schema(self):
        return SettingsSchema(
            component_id="plugin_lang_sandbox",
            component_type="plugin",
            title="Language sandbox",
            translate=False,
            fields=[
                SettingField(
                    key="isolate_node",
                    label="Isolate Node.js",
                    type=SettingType.BOOL,
                    default=True,
                    description="node, npm, and npx run from the workspace sandbox. A Node.js install on this computer is not used.",
                    group="node",
                ),
                SettingField(
                    key="node_version",
                    label="Node.js version",
                    type=SettingType.STRING,
                    default="lts",
                    description="lts, or a version such as 22.21.0. Used when you prepare Node.js.",
                    group="node",
                ),
                SettingField(
                    key="isolate_dotnet",
                    label="Isolate .NET",
                    type=SettingType.BOOL,
                    default=True,
                    description="dotnet and csc run from the workspace sandbox. A .NET install on this computer is not used.",
                    group="dotnet",
                ),
                SettingField(
                    key="dotnet_channel",
                    label=".NET channel",
                    type=SettingType.STRING,
                    default="8.0",
                    description="Install channel, for example 8.0. Used when you prepare .NET.",
                    group="dotnet",
                ),
            ],
        )

    def actions(self) -> list:
        return [
            {"id": "prepare_node", "label": "Prepare Node.js", "description": "Download Node.js into the workspace sandbox."},
            {"id": "prepare_dotnet", "label": "Prepare .NET", "description": "Download the .NET runtime into the workspace sandbox."},
        ]

    async def run_action(self, action_id: str, tool_context: Any) -> Dict[str, Any]:
        workspace = _workspace(self._context)
        values = {}
        try:
            from Kerniq.settings.component_store import ComponentSettingsStore

            values = ComponentSettingsStore(self._context.config.storage.state_dir).load(
                "plugin", "plugin_lang_sandbox", self.settings_schema()
            )
        except Exception:
            values = {}
        if action_id == "prepare_node":
            return _runtime().prepare_node(workspace, str(values.get("node_version") or "lts"))
        if action_id == "prepare_dotnet":
            return _runtime().prepare_dotnet(workspace, str(values.get("dotnet_channel") or "8.0"))
        raise ValueError(f"Plugin has no action {action_id}")

    async def initialize(self, context) -> bool:
        self._context = context
        runtime = _runtime()
        register_language_runtime("plugin_lang_sandbox", runtime.filter_env, runtime.block_command)
        return True

    async def shutdown(self) -> None:
        unregister_language_runtime("plugin_lang_sandbox")
