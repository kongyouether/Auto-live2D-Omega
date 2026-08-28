from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vts_export import VtsExporter, _safe_name, tune_vtube_config


class VtsExportValidationTests(unittest.TestCase):
    def test_safe_name_preserves_unicode_and_removes_path_punctuation(self):
        self.assertEqual(_safe_name("空游 Ether: v4.psd"), "空游_Ether_v4")

    def test_validate_complete_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            (bundle / "textures").mkdir()
            (bundle / "model.moc3").write_bytes(b"MOC3" + b"\0" * 12)
            (bundle / "model.cdi3.json").write_text("{}", encoding="utf-8")
            (bundle / "model.physics3.json").write_text("{}", encoding="utf-8")
            (bundle / "model.idle.motion3.json").write_text("{}", encoding="utf-8")
            (bundle / "textures" / "atlas.png").write_bytes(b"png")
            (bundle / "model.model3.json").write_text(
                json.dumps(
                    {
                        "FileReferences": {
                            "Moc": "model.moc3",
                            "Textures": ["textures/atlas.png"],
                            "Physics": "model.physics3.json",
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = VtsExporter._validate_bundle(bundle)

            self.assertEqual(result["mocBytes"], 16)
            self.assertEqual(result["textureCount"], 1)
            self.assertTrue(result["physics"])
            self.assertEqual(result["motionCount"], 1)

    def test_validate_rejects_invalid_moc_header(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            (bundle / "textures").mkdir()
            (bundle / "model.moc3").write_bytes(b"NOPE")
            (bundle / "model.model3.json").write_text("{}", encoding="utf-8")
            (bundle / "model.cdi3.json").write_text("{}", encoding="utf-8")
            (bundle / "textures" / "atlas.png").write_bytes(b"png")

            with self.assertRaisesRegex(RuntimeError, "invalid header"):
                VtsExporter._validate_bundle(bundle)

    def test_tune_vtube_config_clamps_tracking_and_physics(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.vtube.json"
            path.write_text(
                json.dumps(
                    {
                        "PhysicsSettings": {"PhysicsStrength": 50, "DraggingPhysicsStrength": 10},
                        "ParameterSettings": [
                            {
                                "OutputLive2D": "ParamAngleX",
                                "OutputRangeLower": -30.0,
                                "OutputRangeUpper": 30.0,
                                "ClampInput": False,
                                "ClampOutput": False,
                            },
                            {
                                "OutputLive2D": "ParamEyeBallX",
                                "OutputRangeLower": 1.0,
                                "OutputRangeUpper": -1.0,
                                "ClampInput": False,
                                "ClampOutput": False,
                            },
                            {
                                "OutputLive2D": "ParamBreath",
                                "UseBreathing": True,
                                "ClampInput": False,
                                "ClampOutput": False,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = tune_vtube_config(path)
            tuned = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(result["physicsStrength"], 25)
            self.assertEqual(tuned["PhysicsSettings"]["DraggingPhysicsStrength"], 5)
            self.assertEqual(
                (tuned["ParameterSettings"][0]["OutputRangeLower"], tuned["ParameterSettings"][0]["OutputRangeUpper"]),
                (-9.0, 9.0),
            )
            self.assertTrue(all(item["ClampInput"] and item["ClampOutput"] for item in tuned["ParameterSettings"]))
            self.assertEqual(tuned["ParameterSettings"][1]["OutputRangeLower"], 1.0)
            self.assertFalse(tuned["ParameterSettings"][2]["UseBreathing"])

    def test_animation_previews_lists_exported_clips_and_native_sheets(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            bundle = project / "exports" / "vts" / "20260828-120000-Ether" / "Ether_vts"
            bundle.mkdir(parents=True)
            (bundle / "model.model3.json").write_text(
                json.dumps({"FileReferences": {"Motions": {
                    "Idle": [{"File": "model.idle.motion3.json"}],
                    "Smile": [{"File": "model.smile.motion3.json"}],
                }}}),
                encoding="utf-8",
            )
            (bundle / "model.idle.motion3.json").write_text(
                json.dumps({"Meta": {"Duration": 6.0, "Loop": True, "CurveCount": 9}}),
                encoding="utf-8",
            )
            (bundle / "model.smile.motion3.json").write_text(
                json.dumps({"Meta": {"Duration": 1.5, "Loop": False, "CurveCount": 3}}),
                encoding="utf-8",
            )
            preview = bundle.parent / "motion_previews"
            preview.mkdir()
            (preview / "idle.png").write_bytes(b"png")

            result = VtsExporter(project).animation_previews()

            self.assertTrue(result["success"])
            self.assertEqual([item["name"] for item in result["items"]], ["idle", "smile"])
            self.assertEqual(result["items"][0]["previewUrl"],
                             "/exports/vts/20260828-120000-Ether/motion_previews/idle.png")
            self.assertIsNone(result["items"][1]["previewUrl"])


if __name__ == "__main__":
    unittest.main()
