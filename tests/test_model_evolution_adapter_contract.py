from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

import model_evolution
from model_evolution.adapters import load_adapter
from model_evolution.cli import _parser


class _EntryPoint:
    def __init__(self, value: object):
        self.value = value

    def load(self) -> object:
        return self.value


class _EntryPoints:
    def __init__(self, matches: list[_EntryPoint]):
        self.matches = matches

    def select(self, *, group: str, name: str) -> list[_EntryPoint]:
        if group == "model_evolution.adapters" and name == "example":
            return self.matches
        return []


class _ExampleAdapter:
    name = "example"


class _WrongNameAdapter:
    name = "different"


class AdapterContractTests(unittest.TestCase):
    def test_public_api_exports_the_supported_library_contract(self) -> None:
        self.assertEqual(
            set(model_evolution.__all__),
            {
                "ModelEvolution",
                "ProjectAdapter",
                "ProjectConfig",
                "initialize_project",
                "load_project",
                "new_id",
            },
        )

    def test_every_adapter_is_loaded_from_package_entry_points(self) -> None:
        with patch("model_evolution.adapters.entry_points", return_value=_EntryPoints([])):
            with self.assertRaisesRegex(ValueError, "unknown Model Evolution adapter"):
                load_adapter("latent-arborist")

    def test_adapter_registration_name_must_match_adapter_name(self) -> None:
        entry_points = _EntryPoints([_EntryPoint(_WrongNameAdapter)])
        with patch("model_evolution.adapters.entry_points", return_value=entry_points):
            with self.assertRaisesRegex(ValueError, "registered as example.*declares different"):
                load_adapter("example")

    def test_adapter_classes_are_instantiated(self) -> None:
        entry_points = _EntryPoints([_EntryPoint(_ExampleAdapter)])
        with patch("model_evolution.adapters.entry_points", return_value=entry_points):
            adapter = load_adapter("example")
        self.assertIsInstance(adapter, _ExampleAdapter)

    def test_run_planning_requires_an_explicit_run_adapter(self) -> None:
        parser = _parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["run", "plan", "--slug", "run", "--study", "study"])


if __name__ == "__main__":
    unittest.main()
