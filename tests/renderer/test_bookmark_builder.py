import unittest
import pikepdf
from renderer.bookmark_builder import BookmarkBuilder

class TestBookmarkBuilder(unittest.TestCase):
    def setUp(self):
        self.pdf = pikepdf.Pdf.new()
        self.page1 = self.pdf.add_blank_page()
        self.page2 = self.pdf.add_blank_page()
        
    def test_bookmark_builder_hierarchy(self):
        doc_json = {
            "pages": [
                {
                    "page_number": 1,
                    "elements": [
                        {"type": "paragraph", "content": "Intro"},
                        {"type": "heading", "heading_level": 1, "content": "H1 Main"},
                        {"type": "heading", "heading_level": 2, "content": "H2 Sub"},
                        {"type": "heading", "heading_level": 3, "content": "H3 Minor"},
                        {"type": "heading", "heading_level": 2, "content": "H2 Sub 2"}
                    ]
                },
                {
                    "page_number": 2,
                    "elements": [
                        {"type": "heading", "heading_level": 1, "content": "H1 Second"},
                        {"type": "paragraph", "content": "More text"}
                    ]
                }
            ]
        }
        
        BookmarkBuilder.build(self.pdf, doc_json, [self.page1.obj, self.page2.obj])
        
        # Verify
        with self.pdf.open_outline() as outline:
            # Two top-level H1s
            self.assertEqual(len(outline.root), 2)
            
            h1_main = outline.root[0]
            self.assertEqual(h1_main.title, "H1 Main")
            self.assertEqual(len(h1_main.children), 2)
            
            h2_sub = h1_main.children[0]
            self.assertEqual(h2_sub.title, "H2 Sub")
            self.assertEqual(len(h2_sub.children), 1)
            
            h3_minor = h2_sub.children[0]
            self.assertEqual(h3_minor.title, "H3 Minor")
            
            h2_sub_2 = h1_main.children[1]
            self.assertEqual(h2_sub_2.title, "H2 Sub 2")
            
            h1_second = outline.root[1]
            self.assertEqual(h1_second.title, "H1 Second")
            
if __name__ == '__main__':
    unittest.main()
