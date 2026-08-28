"""Experimental VTube Studio bundle export bridge.

The web renderer in this project does not use Live2D Cubism internally.  This
module delegates the closed ``.moc3`` authoring step to an optional, isolated
adapter and then validates the produced runtime bundle before exposing it to
the desktop UI.

The adapter is intentionally not bundled or installed automatically.  Set
``AUTO_VTS_ADAPTER_ROOT`` to an image2live2d checkout, or place that checkout
at ``../_research/image2live2d`` relative to this project.  Its own virtual
environment must exist at ``.venv``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from urllib.parse import quote
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


REQUIRED_BUNDLE_FILES = (
    "model.moc3",
    "model.model3.json",
    "model.cdi3.json",
    "textures/atlas.png",
)

# Auto-live2D's browser preview uses deliberately restrained normalized transforms.  VTube Studio's
# auto-setup maps face tracking to the entire Cubism parameter range and leaves extrapolation enabled,
# which makes this generated rig move several times farther and exposes layer seams.  These ranges
# reproduce the preview more closely while retaining useful tracking motion.
VTS_SAFE_OUTPUT_RANGES = {
    "ParamAngleX": (-9.0, 9.0),
    "ParamAngleY": (-7.5, 7.5),
    "ParamAngleZ": (-6.0, 6.0),
    "ParamBodyAngleX": (-3.0, 3.0),
    "ParamBodyAngleY": (-3.0, 3.0),
    "ParamBodyAngleZ": (-3.0, 3.0),
    "ParamEyeLOpen": (0.0, 1.0),
    "ParamEyeROpen": (0.0, 1.0),
    "ParamMouthOpenY": (0.0, 1.0),
}


def tune_vtube_config(path: Path) -> dict:
    """Apply the safe Auto-live2D tracking profile to a VTube Studio model config."""
    config_path = path.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    changed: list[str] = []
    for setting in config.get("ParameterSettings", []):
        param_id = setting.get("OutputLive2D")
        setting["ClampInput"] = True
        setting["ClampOutput"] = True
        if param_id == "ParamBreath" and setting.get("UseBreathing"):
            # The exported Idle clip already owns ParamBreath.  VTS auto-breath driving the same
            # parameter at the same time doubles the motion and makes layer seams much easier to see.
            setting["UseBreathing"] = False
            setting["Name"] = "Breath driven by exported Idle"
        if param_id in VTS_SAFE_OUTPUT_RANGES:
            lower, upper = VTS_SAFE_OUTPUT_RANGES[param_id]
            setting["OutputRangeLower"] = lower
            setting["OutputRangeUpper"] = upper
            changed.append(param_id)

    physics = config.setdefault("PhysicsSettings", {})
    physics["PhysicsStrength"] = 25
    physics["DraggingPhysicsStrength"] = min(int(physics.get("DraggingPhysicsStrength", 10)), 5)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=4), encoding="utf-8")
    return {
        "path": str(config_path),
        "parameters": changed,
        "physicsStrength": physics["PhysicsStrength"],
    }


@dataclass(frozen=True)
class AdapterPaths:
    root: Path
    python: Path
    emit_tool: Path


def _safe_name(value: str) -> str:
    name = Path(value or "model.psd").stem.strip()
    name = re.sub(r"[^0-9A-Za-z._\-\u0080-\uffff]+", "_", name).strip("._")
    return name[:80] or "model"


class VtsExporter:
    """Run and validate the optional PSD -> VTube Studio adapter."""

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.exports_root = self.project_root / "exports" / "vts"
        self.last_bundle: Path | None = None

    def _adapter_paths(self) -> AdapterPaths:
        configured = os.environ.get("AUTO_VTS_ADAPTER_ROOT", "").strip()
        root = Path(configured).expanduser() if configured else self.project_root.parent / "_research" / "image2live2d"
        root = root.resolve()
        python = root / ".venv" / "Scripts" / "python.exe"
        emit_tool = root / "tools" / "emit_cubism_bundle.py"
        return AdapterPaths(root=root, python=python, emit_tool=emit_tool)

    def status(self) -> dict:
        adapter = self._adapter_paths()
        missing = [
            str(path)
            for path in (adapter.root, adapter.python, adapter.emit_tool)
            if not path.exists()
        ]
        return {
            "available": not missing,
            "adapterRoot": str(adapter.root),
            "missing": missing,
            "experimental": True,
        }

    def export(self, psd_path: Path, source_name: str, *, acknowledged: bool) -> dict:
        if not acknowledged:
            raise ValueError("Experimental MOC3 export must be acknowledged in the UI")

        source = psd_path.resolve()
        runtime_root = (self.project_root / ".runtime").resolve()
        if runtime_root not in source.parents or source.name != "current.psd":
            raise ValueError("Only the PSD loaded into the desktop runtime can be exported")
        if not source.is_file():
            raise FileNotFoundError("Load a PSD before exporting a VTube Studio model")

        adapter = self._adapter_paths()
        state = self.status()
        if not state["available"]:
            raise RuntimeError("VTS export adapter is unavailable: " + ", ".join(state["missing"]))

        model_name = _safe_name(source_name)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        export_root = self.exports_root / f"{stamp}-{model_name}"
        work_dir = export_root / "work"
        layer_dir = work_dir / "layers"
        inp_path = work_dir / f"{model_name}.inp"
        bundle_dir = export_root / f"{model_name}_vts"
        export_root.mkdir(parents=True, exist_ok=False)

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        commands = [
            [
                str(adapter.python),
                "-m",
                "image2live2d",
                "--psd",
                str(source),
                "--work-dir",
                str(layer_dir),
                "-o",
                str(inp_path),
                "--name",
                model_name,
            ],
            [
                str(adapter.python),
                str(adapter.emit_tool),
                str(layer_dir),
                str(bundle_dir),
            ],
        ]

        logs: list[str] = []
        for command in commands:
            result = subprocess.run(
                command,
                cwd=adapter.root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
                check=False,
            )
            logs.append(result.stdout.strip())
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
                raise RuntimeError(f"VTS export adapter failed: {detail}")

        validation = self._validate_bundle(bundle_dir)
        self.last_bundle = bundle_dir
        report = {
            "source": str(source),
            "sourceName": source_name,
            "modelName": model_name,
            "createdAt": datetime.now().isoformat(timespec="seconds"),
            "adapterRoot": str(adapter.root),
            "experimentalMoc3Writer": True,
            "validation": validation,
            "adapterLog": [entry for entry in logs if entry],
        }
        report_path = export_root / "export-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        archive_path = Path(shutil.make_archive(str(bundle_dir), "zip", root_dir=bundle_dir))

        return {
            "success": True,
            "modelName": model_name,
            "folder": str(bundle_dir),
            "zip": str(archive_path),
            "report": str(report_path),
            "validation": validation,
        }

    def _latest_bundle(self) -> Path:
        if self.last_bundle and (self.last_bundle / "model.model3.json").is_file():
            return self.last_bundle
        candidates = [path.parent for path in self.exports_root.glob("*-*/*_vts/model.model3.json")]
        if not candidates:
            raise FileNotFoundError("Export a VTube Studio model before reviewing animations")
        self.last_bundle = max(candidates, key=lambda path: (path / "model.model3.json").stat().st_mtime)
        return self.last_bundle

    def animation_previews(self) -> dict:
        """List the actual exported motion3 clips and any native-rendered audit sheets."""
        bundle = self._latest_bundle()
        model = json.loads((bundle / "model.model3.json").read_text(encoding="utf-8"))
        files: list[str] = []
        for entries in (model.get("FileReferences", {}).get("Motions", {}) or {}).values():
            for entry in entries:
                rel = entry.get("File")
                if rel and rel not in files:
                    files.append(rel)
        preview_dir = bundle.parent / "motion_previews"
        items = []
        for rel in files:
            motion_path = bundle / rel
            if not motion_path.is_file():
                continue
            motion = json.loads(motion_path.read_text(encoding="utf-8"))
            filename = Path(rel).name
            name = filename.removeprefix("model.").removesuffix(".motion3.json")
            preview_path = preview_dir / f"{name}.png"
            detail_path = preview_dir / f"{name}_extremes.png"
            url = None
            detail_url = None
            if preview_path.is_file():
                relative = preview_path.relative_to(self.project_root).as_posix()
                url = "/" + quote(relative, safe="/")
            if detail_path.is_file():
                relative = detail_path.relative_to(self.project_root).as_posix()
                detail_url = "/" + quote(relative, safe="/")
            items.append({
                "name": name,
                "duration": float(motion.get("Meta", {}).get("Duration", 0.0)),
                "loop": bool(motion.get("Meta", {}).get("Loop", False)),
                "curveCount": int(motion.get("Meta", {}).get("CurveCount", 0)),
                "previewUrl": url,
                "detailPreviewUrl": detail_url,
            })
        return {"success": True, "bundle": str(bundle), "items": sorted(items, key=lambda item: item["name"])}

    def render_animation_previews(self, *, force: bool = False) -> dict:
        """Render five representative Cubism-Core frames for every exported motion."""
        bundle = self._latest_bundle()
        state = self.animation_previews()
        if state["items"] and not force and all(
            item["previewUrl"] and (item["name"] != "sweep" or item["detailPreviewUrl"])
            for item in state["items"]
        ):
            return state

        adapter = self._adapter_paths()
        render_tool = adapter.root / "tools" / "render_motions.py"
        if not render_tool.is_file():
            raise FileNotFoundError(f"Animation render tool is unavailable: {render_tool}")
        preview_dir = bundle.parent / "motion_previews"
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        if not env.get("CUBISM_CORE"):
            default_core = Path(
                "C:/Program Files (x86)/Steam/steamapps/common/VTube Studio/"
                "VTube Studio_Data/Plugins/x86_64/Live2DCubismCore.dll"
            )
            if default_core.is_file():
                env["CUBISM_CORE"] = str(default_core)
        result = subprocess.run(
            [str(adapter.python), str(render_tool), str(bundle), "--out", str(preview_dir), "--size", "260"],
            cwd=adapter.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            raise RuntimeError(f"Animation preview render failed: {detail}")
        if any(item["name"] == "sweep" for item in state["items"]):
            detail_result = subprocess.run(
                [str(adapter.python), str(render_tool), str(bundle), "--out", str(preview_dir),
                 "--clip", "sweep", "--extremes", "--size", "180"],
                cwd=adapter.root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
                check=False,
            )
            if detail_result.returncode != 0:
                detail = detail_result.stderr.strip() or detail_result.stdout.strip() or f"exit code {detail_result.returncode}"
                raise RuntimeError(f"Sweep detail preview render failed: {detail}")
        return self.animation_previews()

    @staticmethod
    def _validate_bundle(bundle_dir: Path) -> dict:
        missing = [rel for rel in REQUIRED_BUNDLE_FILES if not (bundle_dir / rel).is_file()]
        if missing:
            raise RuntimeError("Incomplete VTube Studio bundle: " + ", ".join(missing))

        moc_path = bundle_dir / "model.moc3"
        if moc_path.read_bytes()[:4] != b"MOC3":
            raise RuntimeError("Generated model.moc3 has an invalid header")

        model_path = bundle_dir / "model.model3.json"
        model = json.loads(model_path.read_text(encoding="utf-8"))
        refs = model.get("FileReferences", {})
        referenced = [refs.get("Moc"), *(refs.get("Textures") or [])]
        if refs.get("Physics"):
            referenced.append(refs["Physics"])
        unresolved = [rel for rel in referenced if not rel or not (bundle_dir / rel).is_file()]
        if unresolved:
            raise RuntimeError("model3.json contains unresolved references: " + ", ".join(map(str, unresolved)))

        motion_count = len(list(bundle_dir.glob("*.motion3.json")))
        return {
            "mocBytes": moc_path.stat().st_size,
            "textureCount": len(refs.get("Textures") or []),
            "physics": bool(refs.get("Physics")),
            "motionCount": motion_count,
        }
