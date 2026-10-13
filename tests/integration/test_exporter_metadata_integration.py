import os
import shutil
import tempfile
import unittest
import pikepdf
from unittest.mock import MagicMock

from plugins.exporters.tagged_pdf_exporter import TaggedPdfExporter
from engine.document_context import DocumentContext

class MockNode:
    def __init__(self, id, type, text):
        self.id = id
        self.uuid = f"uuid-{id}"
        self.semantic = {"type": type}
        self.text = text
        self.geometry = {"bbox": [0,0,10,10]}
        self.relations = {}

class MockPage:
    def __init__(self):
        self.nodes = [MockNode(1, "h1", "Integration Test Title")]

class MockGraph:
    def __init__(self):
        self.pages = {1: MockPage()}
        
    def get_node(self, uuid):
        return None

class TestExporterMetadataIntegration(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace_dir = os.path.join(self.test_dir, "workspace")
        os.makedirs(self.workspace_dir, exist_ok=True)
        
        # Create a minimal valid PDF
        self.input_pdf = os.path.join(self.test_dir, "input.pdf")
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page()
        pdf.save(self.input_pdf)
        
        # Setup context
        self.context = DocumentContext()
        self.context.profile = {}
        self.context.metadata["workspace_path"] = self.workspace_dir
        self.context.metadata["pdf_path"] = self.input_pdf
        self.context.graph = MockGraph()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_successful_metadata_integration(self):
        # We need to provide enough config to pass validation
        config = {
            "document_type": "Digital PDF",
            "pdfua": {
                "part": 1,
                "display_doc_title": True
            },
            "project_metadata": {
                "language": "en-US"
                # Title is implicitly found in MockGraph (h1)
            }
        }
        
        exporter = TaggedPdfExporter(config)
        
        # Run export
        exporter.run(self.context)
        
        # Verify no validation errors were attached
        self.assertNotIn("errors", self.context.metadata)
        
        # Verify output PDF
        output_pdf = os.path.join(self.workspace_dir, "output", "tagged_output_v2.pdf")
        self.assertTrue(os.path.exists(output_pdf))
        
        with pikepdf.Pdf.open(output_pdf) as pdf:
            # Check Catalog /Lang
            self.assertIn("/Lang", pdf.Root)
            self.assertEqual(str(pdf.Root.Lang), "en-US")
            
            # Check Document Info /Title
            self.assertIn("/Title", pdf.docinfo)
            self.assertEqual(str(pdf.docinfo.Title), "Integration Test Title")
            
            # Check ViewerPreferences
            self.assertIn("/ViewerPreferences", pdf.Root)
            self.assertTrue(bool(pdf.Root.ViewerPreferences.DisplayDocTitle))
            
            # Check XMP Metadata
            self.assertIn("/Metadata", pdf.Root)
            xmp = pdf.Root.Metadata.read_bytes().decode("utf-8")
            self.assertIn("Integration Test Title", xmp)
            self.assertIn("pdfuaid:part", xmp)

    def test_config_override_propagates_to_pdf(self):
        # Verify that changing language in config goes all the way to PDF
        config = {
            "document_type": "Digital PDF",
            "pdfua": {
                "part": 1,
                "display_doc_title": True
            },
            "language": "en-GB" # Application config override
        }
        
        exporter = TaggedPdfExporter(config)
        exporter.run(self.context)
        
        self.assertNotIn("errors", self.context.metadata)
        
        output_pdf = os.path.join(self.workspace_dir, "output", "tagged_output_v2.pdf")
        self.assertTrue(os.path.exists(output_pdf))
        
        with pikepdf.Pdf.open(output_pdf) as pdf:
            self.assertIn("/Lang", pdf.Root)
            self.assertEqual(str(pdf.Root.Lang), "en-GB")
            
    def test_metadata_validation_failure_halts_export(self):
        # Provide config missing required language and pdfua parts (No hidden defaults!)
        config = {
            "document_type": "Digital PDF"
        }
        
        exporter = TaggedPdfExporter(config)
        
        exporter.run(self.context)
        
        # Verify errors were logged because it did not invent en-US or part 1
        self.assertIn("errors", self.context.metadata)
        errors = self.context.metadata["errors"]
        self.assertTrue(any("Language is missing" in e for e in errors))
        self.assertTrue(any("PDF/UA part is missing" in e for e in errors))
        
        # Output PDF should NOT have been generated
        output_pdf = os.path.join(self.workspace_dir, "output", "tagged_output_v2.pdf")
        self.assertFalse(os.path.exists(output_pdf))
