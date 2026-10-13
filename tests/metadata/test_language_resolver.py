import unittest
from metadata.language_resolver import LanguageResolver

class TestLanguageResolver(unittest.TestCase):
    
    def test_explicit_document_language_wins(self):
        lang, source = LanguageResolver.resolve("en-GB", "en-US", "de-DE")
        self.assertEqual(lang, "en-GB")
        self.assertEqual(source, "document")
        
    def test_client_config_used_when_document_absent(self):
        lang, source = LanguageResolver.resolve("", "en-US", "de-DE")
        self.assertEqual(lang, "en-US")
        self.assertEqual(source, "client_config")
        
        lang2, source2 = LanguageResolver.resolve(None, "en-US", "de-DE")
        self.assertEqual(lang2, "en-US")
        self.assertEqual(source2, "client_config")
        
    def test_application_config_used_when_both_absent(self):
        lang, source = LanguageResolver.resolve(None, "   ", "de-DE")
        self.assertEqual(lang, "de-DE")
        self.assertEqual(source, "application_config")
        
    def test_unresolved_when_nothing_exists(self):
        lang, source = LanguageResolver.resolve(None, None, None)
        self.assertIsNone(lang)
        self.assertIsNone(source)
        
        lang2, source2 = LanguageResolver.resolve("", "   ", None)
        self.assertIsNone(lang2)
        self.assertIsNone(source2)
        
    def test_normalization_formats(self):
        self.assertEqual(LanguageResolver.normalize("en_US"), "en-US")
        self.assertEqual(LanguageResolver.normalize("en-us"), "en-US")
        self.assertEqual(LanguageResolver.normalize("English (United States)"), "en-US")
        self.assertEqual(LanguageResolver.normalize("en-gb"), "en-GB")
        
    def test_invalid_or_ambiguous_does_not_silently_become_en_us(self):
        self.assertIsNone(LanguageResolver.normalize("English"))
        self.assertIsNone(LanguageResolver.normalize("banana"))
        self.assertIsNone(LanguageResolver.normalize("German"))
        
    def test_bcp47_passthrough(self):
        self.assertEqual(LanguageResolver.normalize("fr"), "fr")
        self.assertEqual(LanguageResolver.normalize("es-419"), "es-419")
        self.assertEqual(LanguageResolver.normalize("zh-tw"), "zh-TW")
