from __future__ import annotations

import json
import os
import pathlib
import stat
import tempfile
import textwrap
import unittest
from unittest import mock

from catalog.collector import CatalogError, collect_source


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.registry = self.root / "sources.yml"
        self.registry.write_text(
            textwrap.dedent(
                """
                sources:
                  - name: example
                    service: example-api
                    owner: platform
                    dsnEnv: EXAMPLE_DSN
                    include: [public.*]
                    exclude: [public.audit_log]
                    labels:
                      environment: test
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        self.tbls = self.root / "tbls"
        self._write_fake_tbls(leak_dsn=False)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_fake_tbls(self, *, leak_dsn: bool) -> None:
        schema_expression = (
            "{'name': 'example', 'dsn': os.environ['EXAMPLE_DSN'], 'tables': []}"
            if leak_dsn
            else "{'name': 'example', 'tables': [{'name': 'widgets', 'comment': 'Things', "
            "'columns': [{'name': 'id', 'type': 'bigint'}]}]}"
        )
        self.tbls.write_text(
            textwrap.dedent(
                f"""#!/usr/bin/env python3
                import json
                import os
                import pathlib
                import sys

                if len(sys.argv) > 1 and sys.argv[1] == 'version':
                    print('tbls version 9.9.9-test')
                    raise SystemExit(0)
                if len(sys.argv) > 1 and sys.argv[1] == 'out':
                    output = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])
                    output.write_text(json.dumps({schema_expression}), encoding='utf-8')
                    raise SystemExit(0)
                if len(sys.argv) > 1 and sys.argv[1] == 'doc':
                    docs = pathlib.Path.cwd() / 'docs'
                    docs.mkdir(parents=True, exist_ok=True)
                    (docs / 'README.md').write_text('# Example schema\n', encoding='utf-8')
                    raise SystemExit(0)
                raise SystemExit(2)
                """
            ),
            encoding="utf-8",
        )
        self.tbls.chmod(self.tbls.stat().st_mode | stat.S_IXUSR)

    def test_collects_immutable_snapshot_and_updates_latest(self) -> None:
        data = self.root / "data"
        with mock.patch.dict(os.environ, {"EXAMPLE_DSN": "postgres://reader:secret@db/example"}):
            first = collect_source(
                registry_path=self.registry,
                source_name="example",
                data_dir=data,
                tbls=str(self.tbls),
            )
            second = collect_source(
                registry_path=self.registry,
                source_name="example",
                data_dir=data,
                tbls=str(self.tbls),
            )

        self.assertEqual(first["fingerprint"], second["fingerprint"])
        snapshot = data / "example" / "snapshots" / first["fingerprint"]
        self.assertTrue((snapshot / "schema.json").is_file())
        self.assertTrue((snapshot / "docs" / "README.md").is_file())
        latest = json.loads((data / "example" / "latest.json").read_text(encoding="utf-8"))
        status = json.loads((data / "example" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["fingerprint"], first["fingerprint"])
        self.assertEqual(status["status"], "current")
        self.assertNotIn("secret", (snapshot / "schema.json").read_text(encoding="utf-8"))

    def test_missing_dsn_environment_variable_fails_before_tbls(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(CatalogError, "EXAMPLE_DSN"):
                collect_source(
                    registry_path=self.registry,
                    source_name="example",
                    data_dir=self.root / "data",
                    tbls=str(self.tbls),
                )

    def test_dsn_leak_fails_and_status_is_redacted(self) -> None:
        self._write_fake_tbls(leak_dsn=True)
        dsn = "postgres://reader:supersecret@db/example"
        data = self.root / "data"
        with mock.patch.dict(os.environ, {"EXAMPLE_DSN": dsn}):
            with self.assertRaisesRegex(CatalogError, "live DSN"):
                collect_source(
                    registry_path=self.registry,
                    source_name="example",
                    data_dir=data,
                    tbls=str(self.tbls),
                )

        status_text = (data / "example" / "status.json").read_text(encoding="utf-8")
        self.assertNotIn(dsn, status_text)
        self.assertNotIn("supersecret", status_text)
        status = json.loads(status_text)
        self.assertEqual(status["status"], "failed")
        self.assertIsNone(status["fingerprint"])


if __name__ == "__main__":
    unittest.main()
