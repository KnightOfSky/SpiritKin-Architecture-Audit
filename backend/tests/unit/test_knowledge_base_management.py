import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.knowledge_base_management import (
    build_knowledge_base_snapshot,
    build_knowledge_job_history,
    delete_knowledge_source,
    handle_knowledge_base_action,
    import_knowledge_base_files,
    index_knowledge_base,
    load_knowledge_source_registry,
    record_knowledge_job,
    resolve_knowledge_base_path,
    save_knowledge_source,
    sync_knowledge_source,
)
from backend.app.search_management import handle_search_management_action


class KnowledgeBaseManagementTests(unittest.TestCase):
    def test_imports_files_and_indexes_knowledge_base(self):
        with TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                path = Path("state/knowledge_bases/agents/test")
                imported = import_knowledge_base_files(
                    path,
                    [{"path": "note.md", "text": "SpiritKin 知识库支持 Agent 领域资料。"}],
                )
                report = index_knowledge_base("kb_test", path)
                history = build_knowledge_job_history()
            finally:
                os.chdir(previous)

            self.assertEqual(imported["count"], 1)
            self.assertEqual(report.document_count, 1)
            self.assertGreater(report.chunk_count, 0)
            self.assertEqual(history["count"], 1)
            self.assertEqual(history["last_status"], "completed")
            self.assertEqual(history["jobs"][0]["target_id"], "kb_test")
            self.assertTrue((Path(tmp) / "state/knowledge_bases/agents/test/.spiritkin_kb_index.json").exists())

    def test_rejects_paths_outside_knowledge_root(self):
        with TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                with self.assertRaises(ValueError):
                    resolve_knowledge_base_path("../outside")
            finally:
                os.chdir(previous)

    def test_registers_and_syncs_obsidian_source_into_controlled_kb(self):
        with TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                vault = Path("vault")
                vault.mkdir()
                (vault / "Paper.md").write_text("---\ntitle: Paper2Model\ntags: ai paper\n---\n\nPaper note links [[Review]].", encoding="utf-8")
                (vault / "Review.md").write_text("# Review\n\nGood candidate.", encoding="utf-8")

                source = save_knowledge_source(
                    {
                        "source_id": "obsidian-main",
                        "label": "Main Vault",
                        "kind": "obsidian",
                        "path": str(vault),
                        "knowledge_base_id": "wiki_project_knowledge",
                        "tag_filter": ["paper"],
                    }
                )
                synced = sync_knowledge_source("obsidian-main")
                snapshot = build_knowledge_base_snapshot()
            finally:
                os.chdir(previous)

            self.assertTrue(source["ok"])
            self.assertEqual(synced["sync"]["count"], 1)
            self.assertEqual(synced["index"]["knowledge_base_id"], "wiki_project_knowledge")
            target = Path(tmp) / "state/knowledge_bases/wiki/project/_external/obsidian-main/Paper.md"
            self.assertTrue(target.exists())
            self.assertIn("spiritkin_metadata", target.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["external_sources"][0]["status"], "synced")

    def test_deletes_knowledge_source_registry_entry(self):
        with TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                folder = Path("notes")
                folder.mkdir()
                save_knowledge_source({"source_id": "notes", "kind": "folder", "path": str(folder), "knowledge_base_id": "wiki_project_knowledge"})
                result = delete_knowledge_source("notes")
                registry = load_knowledge_source_registry()
            finally:
                os.chdir(previous)

            self.assertTrue(result["deleted"])
            self.assertEqual(registry["sources"], [])

    def test_search_management_can_index_unindexed_knowledge_bases(self):
        with TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                path = Path("state/knowledge_bases/wiki/project")
                path.mkdir(parents=True)
                (path / "overview.md").write_text("Project overview for RAG.", encoding="utf-8")
                result = handle_search_management_action({"action": "index_unindexed_knowledge"})
            finally:
                os.chdir(previous)

            self.assertTrue(result["ok"])
            self.assertGreaterEqual(len(result["indexing"]["indexed"]), 1)
            self.assertGreaterEqual(result["search_management"]["knowledge_jobs"]["count"], 1)
            self.assertTrue((Path(tmp) / "state/knowledge_bases/wiki/project/.spiritkin_kb_index.json").exists())

    def test_search_management_surfaces_knowledge_job_failures(self):
        with TemporaryDirectory() as tmp:
            previous = os.getcwd()
            previous_env = {
                key: os.environ.get(key)
                for key in (
                    "SPIRITKIN_AGENT_MANAGEMENT_PATH",
                    "SPIRITKIN_KNOWLEDGE_JOB_HISTORY_PATH",
                    "SPIRITKIN_MODEL_CATALOG_PATH",
                )
            }
            os.chdir(tmp)
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            os.environ["SPIRITKIN_KNOWLEDGE_JOB_HISTORY_PATH"] = str(Path(tmp) / "jobs.json")
            os.environ["SPIRITKIN_MODEL_CATALOG_PATH"] = str(Path(tmp) / "model_catalog.json")
            try:
                record_knowledge_job(
                    "index",
                    "failed",
                    target_id="kb_bad",
                    target_path="state/knowledge_bases/bad",
                    summary="Unit test failure.",
                    error="ValueError: synthetic indexing failure",
                    started_at=1,
                    actor="unit-test",
                )
                result = handle_search_management_action({"action": "snapshot"})
            finally:
                os.chdir(previous)
                for key, value in previous_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        search = result["search_management"]
        self.assertEqual(search["status"], "needs_attention")
        self.assertEqual(search["knowledge_jobs"]["failed_count"], 1)
        self.assertEqual(search["knowledge_jobs"]["jobs"][0]["target_id"], "kb_bad")
        self.assertIn("kb_job_failures", {item["gap_id"] for item in search["missing_capabilities"]})

    def test_knowledge_base_handle_sync_source_action(self):
        with TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                folder = Path("folder")
                folder.mkdir()
                (folder / "note.md").write_text("Folder source note.", encoding="utf-8")
                handle_knowledge_base_action({"action": "register_source", "source_id": "folder-src", "kind": "folder", "path": str(folder), "knowledge_base_id": "wiki_project_knowledge"})
                result = handle_knowledge_base_action({"action": "sync_source", "source_id": "folder-src"})
            finally:
                os.chdir(previous)

            self.assertTrue(result["ok"])
            self.assertEqual(result["sync"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
