import os
import shutil
import tempfile
import unittest
from pathlib import Path
import json

from engine.project_manager import ProjectManager

class TestProjectManagerMetadata(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace_dir = os.path.join(self.test_dir, "workspace")
        self.pm = ProjectManager(self.workspace_dir)
        
        # Create a dummy input.pdf
        self.dummy_pdf = os.path.join(self.test_dir, "dummy.pdf")
        with open(self.dummy_pdf, "w") as f:
            f.write("%PDF-1.4\n")
            
        self.pm.create_project("test_metadata_proj", self.dummy_pdf)
        
    def tearDown(self):
        self.pm.close_project()
        shutil.rmtree(self.test_dir)

    def test_metadata_json_does_not_exist_returns_empty(self):
        metadata = self.pm.get_project_metadata()
        self.assertEqual(metadata, {})
        
        info = self.pm.get_current_project_info()
        self.assertEqual(info["project_metadata"], {})

    def test_save_metadata_creates_file(self):
        meta = {
            "title": "My Title",
            "author": "Author Name",
            "language": "en-GB"
        }
        self.pm.save_project_metadata(meta)
        
        # Verify file creation
        meta_path = os.path.join(self.workspace_dir, "test_metadata_proj", "metadata.json")
        self.assertTrue(os.path.exists(meta_path))
        
        with open(meta_path, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
            self.assertEqual(saved_data["title"], "My Title")

    def test_reload_project_restores_metadata(self):
        meta = {
            "title": "Restored Title",
            "language": "fr-FR"
        }
        self.pm.save_project_metadata(meta)
        
        # Close and reopen
        self.pm.close_project()
        self.pm.open_project(os.path.join(self.workspace_dir, "test_metadata_proj"))
        
        loaded = self.pm.get_project_metadata()
        self.assertEqual(loaded["title"], "Restored Title")
        self.assertEqual(loaded["language"], "fr-FR")
        
    def test_unicode_metadata_survives(self):
        meta = {
            "title": "Résumé: Café & Übermensch"
        }
        self.pm.save_project_metadata(meta)
        loaded = self.pm.get_project_metadata()
        self.assertEqual(loaded["title"], "Résumé: Café & Übermensch")
        
    def test_empty_optional_fields_handled(self):
        meta = {
            "title": "Doc",
            "author": "",
            "subject": None
        }
        self.pm.save_project_metadata(meta)
        loaded = self.pm.get_project_metadata()
        self.assertEqual(loaded["title"], "Doc")
        self.assertEqual(loaded["author"], "")
        self.assertIsNone(loaded["subject"])
