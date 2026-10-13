import unittest
import pikepdf
from metadata.metadata_model import DocumentMetadata
from metadata.metadata_writer import MetadataWriter

class TestMetadataWriter(unittest.TestCase):

    def setUp(self):
        self.pdf = pikepdf.Pdf.new()

    def test_writer_writes_all_properties(self):
        meta = DocumentMetadata(
            title="Writer Test",
            language="en-GB",
            pdfua_part=1,
            display_doc_title=True
        )
        
        # Seed an unrelated property in Root to ensure it's preserved
        self.pdf.Root.Outlines = pikepdf.Dictionary()
        
        MetadataWriter.write(self.pdf, meta)
        
        # Check Catalog /Lang
        self.assertIn("/Lang", self.pdf.Root)
        self.assertEqual(str(self.pdf.Root.Lang), "en-GB")
        
        # Check Info /Title
        self.assertIn("/Title", self.pdf.docinfo)
        self.assertEqual(str(self.pdf.docinfo.Title), "Writer Test")
        
        # Check XMP Metadata
        self.assertIn("/Metadata", self.pdf.Root)
        metadata_stream = self.pdf.Root.Metadata
        self.assertEqual(str(metadata_stream.Type), "/Metadata")
        self.assertEqual(str(metadata_stream.Subtype), "/XML")
        xmp_data = metadata_stream.read_bytes().decode("utf-8")
        self.assertIn("Writer Test", xmp_data)
        self.assertIn("pdfuaid:part", xmp_data)
        
        # Check ViewerPreferences
        self.assertIn("/ViewerPreferences", self.pdf.Root)
        self.assertTrue(bool(self.pdf.Root.ViewerPreferences.DisplayDocTitle))
        
        # Check unrelated property is preserved
        self.assertIn("/Outlines", self.pdf.Root)
        
        # Check default initial view properties
        self.assertEqual(str(self.pdf.Root.PageMode), "/UseNone")
        self.assertEqual(str(self.pdf.Root.PageLayout), "/SinglePage")

    def test_writer_updates_existing_viewer_preferences(self):
        meta = DocumentMetadata(
            title="Update VP",
            language="en-US",
            display_doc_title=True
        )
        
        # Seed an existing ViewerPreferences dictionary
        self.pdf.Root.ViewerPreferences = pikepdf.Dictionary()
        self.pdf.Root.ViewerPreferences.FitWindow = True
        
        MetadataWriter.write(self.pdf, meta)
        
        self.assertIn("/ViewerPreferences", self.pdf.Root)
        self.assertTrue(bool(self.pdf.Root.ViewerPreferences.DisplayDocTitle))
        self.assertTrue(bool(self.pdf.Root.ViewerPreferences.FitWindow)) # Preserved

    def test_writer_skips_missing_optional_data(self):
        # Even though validator would reject this, writer should be dumb
        meta = DocumentMetadata() 
        
        MetadataWriter.write(self.pdf, meta)
        
        self.assertNotIn("/Lang", self.pdf.Root)
        self.assertNotIn("/Title", self.pdf.docinfo)
        # DisplayDocTitle shouldn't be added since it's not explicitly True
        self.assertNotIn("/ViewerPreferences", self.pdf.Root)
        
        # Metadata will be attached but won't contain title or pdfua_part
        self.assertIn("/Metadata", self.pdf.Root)
        xmp_data = self.pdf.Root.Metadata.read_bytes().decode("utf-8")
        self.assertNotIn("dc:title", xmp_data)
