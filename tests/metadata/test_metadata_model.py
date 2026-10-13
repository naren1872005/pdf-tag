import unittest
from metadata.metadata_model import DocumentMetadata

class TestDocumentMetadata(unittest.TestCase):
    
    def test_document_metadata_initialization(self):
        # Test default initialization
        meta = DocumentMetadata()
        self.assertIsNone(meta.title)
        self.assertIsNone(meta.title_source)
        self.assertIsNone(meta.language)
        
        # Test full initialization
        meta2 = DocumentMetadata(
            title="Revenue Cycle",
            title_source="semantic_json",
            language="en-GB",
            language_source="client_config",
            pdfua_part=1,
            pdfua_part_source="application_config",
            display_doc_title=True,
            display_doc_title_source="application_config",
            author="John Doe",
            author_source="explicit"
        )
        
        self.assertEqual(meta2.title, "Revenue Cycle")
        self.assertEqual(meta2.title_source, "semantic_json")
        self.assertEqual(meta2.language, "en-GB")
        self.assertEqual(meta2.language_source, "client_config")
        self.assertEqual(meta2.pdfua_part, 1)
        self.assertTrue(meta2.display_doc_title)
        self.assertEqual(meta2.author, "John Doe")
