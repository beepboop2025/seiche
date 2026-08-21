"""Offline contracts for MCP client examples and distribution status claims."""

from __future__ import annotations

import csv
import json
import unittest
from datetime import datetime
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[2]
CLIENTS = ROOT / "integrations" / "mcp-clients"
SUBMISSIONS = ROOT / "distribution" / "submissions.csv"
PUBLIC_MCP_URL = "https://api.seiche.info/mcp"

JSON_CLIENTS = (
    "claude-code.mcp.json",
    "cursor.mcp.json",
    "vscode.mcp.json",
    "gemini.settings.json",
)
EXPECTED_SURFACES = {
    "Official MCP Registry",
    "Glama remote connector",
    "mcp.so",
    "LobeHub",
    "MCP Index",
    "Lulu MCPs",
    "MCPBeat",
    "ConnectorZone",
    "ZBS Index",
    "CorpusIQ Hermes",
    "PulseMCP",
    "Docker MCP Catalog",
    "punkpeye awesome-mcp-servers",
    "mcpservers.org",
    "MCPub",
    "Smithery",
    "OpenAI",
    "Docker / GHCR",
    "OpenBB",
    "Hugging Face",
    "Kaggle",
    "Zenodo",
}
LISTED_SURFACES = {
    "Official MCP Registry",
    "Glama remote connector",
    "Smithery",
    "mcp.so",
    "LobeHub",
    "MCP Index",
    "Lulu MCPs",
    "MCPBeat",
    "ConnectorZone",
    "ZBS Index",
    "CorpusIQ Hermes",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _json(name: str) -> dict:
    value = json.loads(_read(CLIENTS / name))
    if not isinstance(value, dict):
        raise TypeError(f"{name} must contain a JSON object")
    return value


def _submission_rows() -> list[dict[str, str]]:
    with SUBMISSIONS.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise AssertionError("distribution/submissions.csv must not be empty")
    return rows


class MCPClientExampleContracts(unittest.TestCase):
    def test_examples_use_each_clients_current_remote_shape(self) -> None:
        self.assertEqual(
            _json("claude-code.mcp.json"),
            {"mcpServers": {"seiche": {"type": "http", "url": PUBLIC_MCP_URL}}},
        )
        self.assertEqual(
            _json("cursor.mcp.json"),
            {"mcpServers": {"seiche": {"url": PUBLIC_MCP_URL}}},
        )
        self.assertEqual(
            _json("vscode.mcp.json"),
            {"servers": {"seiche": {"type": "http", "url": PUBLIC_MCP_URL}}},
        )
        self.assertEqual(
            _json("gemini.settings.json"),
            {"mcpServers": {"seiche": {"httpUrl": PUBLIC_MCP_URL, "trust": False}}},
        )
        self.assertEqual(
            tomllib.loads(_read(CLIENTS / "codex.config.toml")),
            {"mcp_servers": {"seiche": {"url": PUBLIC_MCP_URL}}},
        )

    def test_examples_are_anonymous_remote_configs_without_secret_placeholders(
        self,
    ) -> None:
        names = (*JSON_CLIENTS, "codex.config.toml")
        forbidden = (
            "localhost",
            "127.0.0.1",
            "authorization",
            "bearer",
            "api_key",
            "api-key",
            "password",
            "secret",
            "token",
            "command",
        )
        for name in names:
            document = _read(CLIENTS / name)
            lowered = document.lower()
            with self.subTest(client=name):
                self.assertEqual(document.count(PUBLIC_MCP_URL), 1)
                self.assertNotIn("http://", lowered)
                self.assertNotIn("${", document)
                for value in forbidden:
                    self.assertNotIn(value, lowered)

    def test_docs_keep_local_configuration_separate_from_public_listing(self) -> None:
        readme = _read(CLIENTS / "README.md")
        flat_readme = " ".join(readme.split())
        openai = _read(CLIENTS / "openai.md")
        for required in (
            "usable client configurations, not vendor endorsements",
            "indexed, not owner-claimed",
            "https://getlulu.dev/mcps/seiche",
            "owner-published entry",
            "owner-verified, healthy remote connector",
            "/.well-known/mcp/server-card.json",
        ):
            self.assertIn(required, flat_readme)
        self.assertIn("ChatGPT does not use local Codex configuration", flat_readme)
        self.assertNotIn("ChatGPT desktop", readme)
        self.assertNotIn("ChatGPT desktop", openai)
        self.assertIn("It is not evidence of OpenAI review", openai)
        self.assertIn("portal receipt and a public listing", openai)
        self.assertFalse((ROOT / "smithery.yaml").exists())
        self.assertFalse((ROOT / "smithery.yml").exists())


class SubmissionLedgerContracts(unittest.TestCase):
    def test_project_front_door_exposes_every_distribution_surface(self) -> None:
        readme = _read(ROOT / "README.md")
        for surface in (
            "OpenBB",
            "Zenodo",
            "Hugging Face",
            "Kaggle",
            "Smithery",
            "MCP directories",
            "Research notebooks",
            "Python / R / JavaScript",
            "Docker",
            "Academic dataset",
            "Data catalogs",
            "AI integrations",
        ):
            self.assertIn(f"**{surface}**", readme)
        self.assertIn("distribution/submissions.csv", readme)
        self.assertIn("no deposit or DOI claimed", readme)
        self.assertIn("Listed but stale; authenticated rescan pending", readme)

    def test_listing_state_ownership_and_freshness_are_independent(self) -> None:
        rows = _submission_rows()
        by_surface = {row["surface"]: row for row in rows}
        self.assertEqual(set(by_surface), EXPECTED_SURFACES)
        self.assertEqual(len(by_surface), len(rows), "surface names must be unique")

        listed = {
            surface
            for surface, row in by_surface.items()
            if row["status"] == "listed"
        }
        self.assertEqual(listed, LISTED_SURFACES)
        self.assertEqual(
            {row["status"] for row in rows}, {"listed", "prepared", "blocked"}
        )
        self.assertLessEqual(
            {row["ownership"] for row in rows},
            {
                "owner_published",
                "owner_verified",
                "unclaimed",
                "third_party_index",
                "owner_required",
                "not_applicable",
            },
        )
        self.assertLessEqual(
            {row["freshness"] for row in rows},
            {"current", "stale", "pending"},
        )

        for surface, row in by_surface.items():
            with self.subTest(surface=surface):
                self.assertTrue(row["public_evidence_url"].startswith("https://"))
                self.assertTrue((ROOT / row["artifact_path"]).exists())
                checked = datetime.fromisoformat(row["checked_utc"])
                self.assertIsNotNone(checked.tzinfo)
                self.assertTrue(row["notes"].strip())
                if row["status"] == "listed":
                    self.assertTrue(row["receipt_url"].startswith("https://"))
                else:
                    self.assertEqual(row["receipt_url"], "")
                self.assertTrue(row["owner_gate"].strip())

    def test_verified_receipts_identify_the_canonical_records(self) -> None:
        by_surface = {row["surface"]: row for row in _submission_rows()}
        official = by_surface["Official MCP Registry"]
        glama = by_surface["Glama remote connector"]
        smithery = by_surface["Smithery"]
        lulu = by_surface["Lulu MCPs"]

        self.assertEqual(official["target"], "io.github.beepboop2025/seiche")
        self.assertIn("/versions/latest", official["public_evidence_url"])
        self.assertIn("version 0.10.1", official["notes"])
        self.assertEqual(glama["ownership"], "owner_verified")
        self.assertIn("eleven live tool links", glama["notes"])
        self.assertEqual(smithery["ownership"], "owner_published")
        self.assertEqual(smithery["freshness"], "stale")
        self.assertIn("only ten tools", smithery["notes"])
        self.assertEqual(lulu["public_evidence_url"], "https://getlulu.dev/mcps/seiche")
        self.assertIn("does not mean owner-claimed", lulu["notes"])


if __name__ == "__main__":
    unittest.main()
