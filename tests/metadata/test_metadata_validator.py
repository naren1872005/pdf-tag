import unittest
from metadata.metadata_model import DocumentMetadata
from metadata.metadata_validator import MetadataValidator

class TestMetadataValidator(unittest.TestCase):

    def test_valid_metadata_passes(self):
        meta = DocumentMetadata(
            title="Valid Title",
            language="en-US",
            pdfua_part=1,
            display_doc_title=True
        )
        is_valid, errors = MetadataValidator.validate(meta)
        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)
        
    def test_missing_title_fails(self):
        meta = DocumentMetadata(title="", language="en-US", pdfua_part=1, display_doc_title=True)
        is_valid, errors = MetadataValidator.validate(meta)
        self.assertFalse(is_valid)
        self.assertIn("Title is missing or empty.", errors)
        
        meta2 = DocumentMetadata(title="   ", language="en-US", pdfua_part=1, display_doc_title=True)
        is_valid2, errors2 = MetadataValidator.validate(meta2)
        self.assertFalse(is_valid2)
        self.assertIn("Title is missing or empty.", errors2)
        
    def test_missing_language_fails(self):
        meta = DocumentMetadata(title="Valid", language=None, pdfua_part=1, display_doc_title=True)
        is_valid, errors = MetadataValidator.validate(meta)
        self.assertFalse(is_valid)
        self.assertIn("Language is missing.", errors)
        
    def test_invalid_language_fails(self):
        meta = DocumentMetadata(title="Valid", language="banana_split", pdfua_part=1, display_doc_title=True)
        is_valid, errors = MetadataValidator.validate(meta)
        self.assertFalse(is_valid)
        self.assertTrue(any("not a valid BCP 47 tag" in err for err in errors))
        
    def test_missing_pdfua_part_fails(self):
        meta = DocumentMetadata(title="Valid", language="en", pdfua_part=None, display_doc_title=True)
        is_valid, errors = MetadataValidator.validate(meta)
        self.assertFalse(is_valid)
        self.assertIn("PDF/UA part is missing.", errors)
        
    def test_unsupported_pdfua_part_fails(self):
        meta = DocumentMetadata(title="Valid", language="en", pdfua_part=2, display_doc_title=True)
        is_valid, errors = MetadataValidator.validate(meta)
        self.assertFalse(is_valid)
        self.assertTrue(any("not supported" in err for err in errors))
        
    def test_display_doc_title_fails_if_not_true(self):
        meta = DocumentMetadata(title="Valid", language="en", pdfua_part=1, display_doc_title=None)
        is_valid, errors = MetadataValidator.validate(meta)
        self.assertFalse(is_valid)
        self.assertIn("DisplayDocTitle must be explicitly True.", errors)
        
        meta2 = DocumentMetadata(title="Valid", language="en", pdfua_part=1, display_doc_title=False)
        is_valid2, errors2 = MetadataValidator.validate(meta2)
        self.assertFalse(is_valid2)
        self.assertIn("DisplayDocTitle must be explicitly True.", errors2)
