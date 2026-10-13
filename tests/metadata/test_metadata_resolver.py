import unittest
from metadata.metadata_resolver import MetadataResolver

class TestMetadataResolver(unittest.TestCase):

    def test_explicit_title_overrides_h1(self):
        doc_data = {
            "pages": [{
                "elements": [{"type": "heading", "level": 1, "content": "Original H1"}]
            }]
        }
        proj_meta = {"title": "Custom Report Title"}
        
        meta = MetadataResolver.resolve(document_data=doc_data, project_metadata=proj_meta)
        self.assertEqual(meta.title, "Custom Report Title")
        self.assertEqual(meta.title_source, "user/project")

    def test_h1_becomes_title_when_explicit_absent(self):
        doc_data = {
            "pages": [{
                "elements": [{"type": "heading", "level": 1, "content": "Original H1"}]
            }]
        }
        meta = MetadataResolver.resolve(document_data=doc_data, project_metadata={"title": None})
        self.assertEqual(meta.title, "Original H1")
        self.assertEqual(meta.title_source, "semantic_h1")

    def test_missing_title_remains_none(self):
        doc_data = {"pages": [{"elements": [{"type": "paragraph", "content": "Just text"}]}]}
        meta = MetadataResolver.resolve(document_data=doc_data)
        self.assertIsNone(meta.title)
        self.assertIsNone(meta.title_source)

    def test_language_resolution(self):
        # Should delegate to LanguageResolver correctly
        proj_meta = {"language": "en-GB"}
        config = {"language": "en-US"}
        meta = MetadataResolver.resolve(project_metadata=proj_meta, config=config)
        self.assertEqual(meta.language, "en-GB")
        self.assertEqual(meta.language_source, "document")
        
        # Missing language
        meta_missing = MetadataResolver.resolve(project_metadata={"language": ""})
        self.assertIsNone(meta_missing.language)

    def test_optional_metadata(self):
        proj_meta = {
            "author": "Jane Doe",
            "subject": "Accessibility",
            "keywords": "PDF, UA"
        }
        meta = MetadataResolver.resolve(project_metadata=proj_meta)
        
        self.assertEqual(meta.author, "Jane Doe")
        self.assertEqual(meta.author_source, "user/project")
        self.assertEqual(meta.subject, "Accessibility")
        self.assertEqual(meta.subject_source, "user/project")
        self.assertEqual(meta.keywords, "PDF, UA")
        self.assertEqual(meta.keywords_source, "user/project")

    def test_pdfua_defaults(self):
        meta = MetadataResolver.resolve()
        self.assertIsNone(meta.pdfua_part)
        self.assertIsNone(meta.pdfua_part_source)
        self.assertIsNone(meta.display_doc_title)
        self.assertIsNone(meta.display_doc_title_source)

    def test_pdfua_overrides(self):
        config = {
            "pdfua": {
                "part": 2,
                "display_doc_title": False
            }
        }
        meta = MetadataResolver.resolve(config=config)
        self.assertEqual(meta.pdfua_part, 2)
        self.assertFalse(meta.display_doc_title)
