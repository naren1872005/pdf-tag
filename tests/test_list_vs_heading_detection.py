import unittest
from export.structure_enforcer import StructureEnforcer, normalize_word_spacing
from ocr.paddle_engine import PaddleLayoutExtractor

class TestListVsHeadingDetection(unittest.TestCase):
    def setUp(self):
        self.enforcer = StructureEnforcer()

    def test_numbered_questions_not_headings(self):
        """Questions with numbers like '1. Who do you think...' should be classified as list items, not headings."""
        doc = {
            "pages": [{
                "page_number": 1,
                "elements": [
                    {"id": 1, "type": "paragraph", "content": "UNIT 7: ANCIENT CIVILIZATIONS", "is_bold": True},
                    {"id": 2, "type": "paragraph", "content": "1. Who do you think the last person to see this sphinx was?", "is_bold": True},
                    {"id": 3, "type": "paragraph", "content": "2. Why do you think someone made the sphinx?", "is_bold": True},
                    {"id": 4, "type": "paragraph", "content": "Section 2: Key Concepts", "is_bold": True}
                ]
            }]
        }
        
        doc = self.enforcer.enforce(doc)
        elements = doc["pages"][0]["elements"]
        
        # Section 2 should be a heading
        headings = [e for e in elements if e.get("type") == "heading"]
        heading_texts = [(h.get("content") or "") for h in headings]
        self.assertTrue(any("UNIT 7" in t for t in heading_texts))
        self.assertTrue(any("Section 2" in t for t in heading_texts))
        
        # 1. and 2. should be in a list, not in headings
        self.assertFalse(any("Who do you think" in t for t in heading_texts))
        self.assertFalse(any("Why do you think" in t for t in heading_texts))
        
        lists = [e for e in elements if e.get("type") == "list"]
        self.assertTrue(len(lists) >= 1)

    def test_numbered_instructions_not_headings(self):
        """Numbered instructions like '1. Read aloud the quote...' should be classified as list items."""
        doc = {
            "pages": [{
                "page_number": 1,
                "elements": [
                    {"id": 1, "type": "heading", "heading_level": 1, "content": "Activity Guide"},
                    {"id": 2, "type": "paragraph", "content": "1. Read aloud the quote at the top of page 317", "is_bold": True},
                    {"id": 3, "type": "paragraph", "content": "2. Then reread the quote in chunks", "is_bold": True},
                    {"id": 4, "type": "paragraph", "content": "3. Model thinking about this quotation", "is_bold": True}
                ]
            }]
        }
        
        doc = self.enforcer.enforce(doc)
        elements = doc["pages"][0]["elements"]
        
        headings = [e for e in elements if e.get("type") == "heading"]
        heading_texts = [(h.get("content") or "") for h in headings]
        for instr in ["Read aloud", "reread the quote", "Model thinking"]:
            self.assertFalse(any(instr in t for t in heading_texts))

    def test_single_h1_rule_preserved(self):
        """Verify that exactly one H1 exists and secondary H1s are demoted to H2."""
        doc = {
            "pages": [{
                "page_number": 1,
                "elements": [
                    {"id": 1, "type": "heading", "heading_level": 1, "content": "Document Main Title"},
                    {"id": 2, "type": "heading", "heading_level": 1, "content": "Another Major Section"}
                ]
            }]
        }
        
        doc = self.enforcer.enforce(doc)
        elements = doc["pages"][0]["elements"]
        h1s = [e for e in elements if e.get("type") == "heading" and e.get("heading_level") == 1]
        self.assertEqual(len(h1s), 1)
        self.assertEqual(h1s[0]["content"], "Document Main Title")

    def test_list_enforcement_structure(self):
        """Verify that enforce wraps list_item into L > LI > Lbl + LBody."""
        doc = {
            "pages": [{
                "page_number": 1,
                "elements": [
                    {"id": 1, "type": "list_item", "content": "1. Who do you think was the last person?"},
                    {"id": 2, "type": "list_item", "content": "2. Why do you think someone made this?"}
                ]
            }]
        }
        
        doc = self.enforcer.enforce(doc)
        elements = doc["pages"][0]["elements"]
        self.assertEqual(len(elements), 1)
        l_node = elements[0]
        self.assertEqual(l_node.get("type"), "list")
        kids = l_node.get("kids", [])
        self.assertEqual(len(kids), 2)
        for li in kids:
            self.assertEqual(li.get("type"), "list_item")
            child_types = [c.get("type") for c in li.get("kids", [])]
            self.assertIn("list_label", child_types)
            self.assertIn("list_body", child_types)


class TestWordSpacingPreservation(unittest.TestCase):
    def test_normalize_word_spacing(self):
        # Colon without space
        self.assertEqual(normalize_word_spacing("Ask:What two things"), "Ask: What two things")
        # Joined words
        self.assertEqual(normalize_word_spacing("Read thename of person"), "Read the name of person")
        self.assertEqual(normalize_word_spacing("scholars wholivedduring the ages"), "scholars who lived during the ages")
        self.assertEqual(normalize_word_spacing("drops ofwater"), "drops of water")
        self.assertEqual(normalize_word_spacing("resembles thefuture"), "resembles the future")
        self.assertEqual(normalize_word_spacing("tell students aboutIbn"), "tell students about Ibn")
        
        # Preserve URLs, emails, decimals
        self.assertEqual(normalize_word_spacing("Visit https://example.com/test"), "Visit https://example.com/test")
        self.assertEqual(normalize_word_spacing("Contact info@example.com"), "Contact info@example.com")
        self.assertEqual(normalize_word_spacing("Value is 3.1415"), "Value is 3.1415")
        
        # Preserve hyphens
        self.assertEqual(normalize_word_spacing("high-priority task"), "high-priority task")

    def test_join_line_spans(self):
        spans = [
            {"bbox": [10, 10, 50, 20], "size": 10, "text": "Hello"},
            {"bbox": [55, 10, 100, 20], "size": 10, "text": "world"}
        ]
        # gap is 55 - 50 = 5 > 0.75 -> space inserted
        text = PaddleLayoutExtractor._join_line_spans(spans)
        self.assertEqual(text, "Hello world")

        # Hyphen at end of span
        hyphen_spans = [
            {"bbox": [10, 10, 50, 20], "size": 10, "text": "multi-"},
            {"bbox": [52, 10, 90, 20], "size": 10, "text": "threaded"}
        ]
        text_hyphen = PaddleLayoutExtractor._join_line_spans(hyphen_spans)
        self.assertEqual(text_hyphen, "multi-threaded")


if __name__ == "__main__":
    unittest.main()
