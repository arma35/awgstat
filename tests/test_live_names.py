from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import htmlgen


class LiveNamesTests(unittest.TestCase):
    def test_loads_current_names_from_clients_table(self) -> None:
        payload = '[{"clientId":"peer-a=","userData":{"clientName":"phone"}}]'
        completed = subprocess.CompletedProcess(
            ["docker"], 0, stdout=payload, stderr=""
        )
        cfg = {
            "CONTAINER": "amnezia-awg2",
            "AMNEZIA_CLIENTS_TABLE": "/opt/amnezia/awg/clientsTable",
        }
        with patch.object(htmlgen.subprocess, "run", return_value=completed) as run:
            names = htmlgen._load_current_names(cfg)

        self.assertEqual(names, {"peer-a=": "phone"})
        run.assert_called_once_with(
            [
                "docker",
                "exec",
                "amnezia-awg2",
                "cat",
                "/opt/amnezia/awg/clientsTable",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_clients_table_failure_falls_back_without_crashing(self) -> None:
        with patch.object(
            htmlgen.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, ["docker"]),
        ):
            self.assertEqual(htmlgen._load_current_names({}), {})

    def test_reportgen_adapter_supplies_live_names_without_names_config(self) -> None:
        live = {"peer-a=": "renamed-now"}
        observed = {}

        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text('WORKDIR="/tmp/awgstat"\n', encoding="utf-8")

            def fake_main() -> int:
                cfg = htmlgen.reportgen.load_config(config)
                observed["names_path"] = cfg.get("NAMES")
                observed["names"] = htmlgen.reportgen.load_names(Path("unused"))
                observed["changed"] = htmlgen.reportgen.names_map_changed(
                    Path("unused"), Path("unused")
                )
                return 0

            with patch.object(htmlgen.reportgen, "main", side_effect=fake_main):
                result = htmlgen._run_reportgen_with_current_names(live)

        self.assertEqual(result, 0)
        self.assertEqual(observed["names"], live)
        self.assertEqual(observed["names_path"], "${WORKDIR}/.legacy-names.map")
        self.assertFalse(observed["changed"])


if __name__ == "__main__":
    unittest.main()
