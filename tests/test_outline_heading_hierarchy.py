import unittest
import copy
from export.structure_enforcer import StructureEnforcer, enforce_heading_hierarchy, ensure_h1
from export.accessibility_mapper import AccessibilityMapper
from renderer.bookmark_builder import BookmarkBuilder
import pikepdf

class TestOutlineHeadingHierarchy(unittest.TestCase):
    """
    Tests for hierarchical heading structure based on outline numbering:
      2. -> H1 (strict single-H1 rule)
      2.1 -> H2
      2.2 -> H2
      2.2.1 -> H3
      2.2.1.2 -> H4
      2.3 -> H2
      2.3.1 -> H3
      2.3.1.2 -> H4
    
    Verifies font formatting invariance ("same font, size, bold"):
    Heading levels are determined by outline numbering, not font size differences.
    """

    def setUp(self):
        self.sample_doc = {
            "pages": [
                {
                    "page_number": 1,
                    "elements": [
                        # All headings share the exact SAME font size (11.0), bold weight, and font
                        {"id": 1, "type": "paragraph", "content": "2. Main Chapter", "font_size": 11.0, "is_bold": True},
                        {"id": 2, "type": "paragraph", "content": "Introduction text in chapter 2.", "font_size": 10.0, "is_bold": False},
                        {"id": 3, "type": "paragraph", "content": "2.1 Background and Motivation", "font_size": 11.0, "is_bold": True},
                        {"id": 4, "type": "paragraph", "content": "Background details.", "font_size": 10.0, "is_bold": False},
                        {"id": 5, "type": "paragraph", "content": "2.2 Proposed Methodology", "font_size": 11.0, "is_bold": True},
                        {"id": 6, "type": "paragraph", "content": "2.2.1 System Architecture", "font_size": 11.0, "is_bold": True},
                        {"id": 7, "type": "paragraph", "content": "Architecture explanation.", "font_size": 10.0, "is_bold": False},
                        {"id": 8, "type": "paragraph", "content": "2.2.1.2 Component Integration", "font_size": 11.0, "is_bold": True},
                        {"id": 9, "type": "paragraph", "content": "Integration details.", "font_size": 10.0, "is_bold": False},
                        {"id": 10, "type": "paragraph", "content": "2.3 Experimental Evaluation", "font_size": 11.0, "is_bold": True},
                        {"id": 11, "type": "paragraph", "content": "2.3.1 Benchmark Datasets", "font_size": 11.0, "is_bold": True},
                        {"id": 12, "type": "paragraph", "content": "2.3.1.2 Evaluation Metrics", "font_size": 11.0, "is_bold": True},
                        {"id": 13, "type": "paragraph", "content": "Closing evaluation text.", "font_size": 10.0, "is_bold": False},
                    ]
                }
            ]
        }

    def test_outline_heading_hierarchy_levels(self):
        doc = copy.deepcopy(self.sample_doc)
        enforcer = StructureEnforcer()
        doc = enforcer.enforce(doc)
        ensure_h1(doc)

        elements = doc["pages"][0]["elements"]
        heading_map = {el["id"]: el for el in elements if el.get("type") == "heading"}

        # 2. -> H1 (sole H1 in document)
        self.assertIn(1, heading_map)
        self.assertEqual(heading_map[1]["heading_level"], 1)
        self.assertEqual(heading_map[1]["tag"], "H1")

        # 2.1 -> H2
        self.assertIn(3, heading_map)
        self.assertEqual(heading_map[3]["heading_level"], 2)
        self.assertEqual(heading_map[3]["tag"], "H2")

        # 2.2 -> H2
        self.assertIn(5, heading_map)
        self.assertEqual(heading_map[5]["heading_level"], 2)
        self.assertEqual(heading_map[5]["tag"], "H2")

        # 2.2.1 -> H3
        self.assertIn(6, heading_map)
        self.assertEqual(heading_map[6]["heading_level"], 3)
        self.assertEqual(heading_map[6]["tag"], "H3")

        # 2.2.1.2 -> H4
        self.assertIn(8, heading_map)
        self.assertEqual(heading_map[8]["heading_level"], 4)
        self.assertEqual(heading_map[8]["tag"], "H4")

        # 2.3 -> H2
        self.assertIn(10, heading_map)
        self.assertEqual(heading_map[10]["heading_level"], 2)
        self.assertEqual(heading_map[10]["tag"], "H2")

        # 2.3.1 -> H3
        self.assertIn(11, heading_map)
        self.assertEqual(heading_map[11]["heading_level"], 3)
        self.assertEqual(heading_map[11]["tag"], "H3")

        # 2.3.1.2 -> H4
        self.assertIn(12, heading_map)
        self.assertEqual(heading_map[12]["heading_level"], 4)
        self.assertEqual(heading_map[12]["tag"], "H4")

    def test_strict_single_h1_rule(self):
        doc = copy.deepcopy(self.sample_doc)
        # Add another top-level section: 3. Conclusions
        doc["pages"][0]["elements"].append({
            "id": 14, "type": "paragraph", "content": "3. Conclusions and Next Steps",
            "font_size": 11.0, "is_bold": True
        })

        enforcer = StructureEnforcer()
        doc = enforcer.enforce(doc)
        ensure_h1(doc)

        elements = doc["pages"][0]["elements"]
        h1_elements = [el for el in elements if el.get("type") == "heading" and el.get("heading_level") == 1]
        
        # Strictly only ONE H1 in the document
        self.assertEqual(len(h1_elements), 1)
        self.assertEqual(h1_elements[0]["id"], 1)

        # 3. Conclusions must be demoted to H2 to preserve single-H1 rule
        el_14 = next(el for el in elements if el["id"] == 14)
        self.assertEqual(el_14["type"], "heading")
        self.assertEqual(el_14["heading_level"], 2)
        self.assertEqual(el_14["tag"], "H2")

    def test_accessibility_mapper_tags(self):
        doc = copy.deepcopy(self.sample_doc)
        enforce_heading_hierarchy(doc)
        elements = doc["pages"][0]["elements"]

        h_el = next(el for el in elements if el["id"] == 8) # 2.2.1.2
        tag = AccessibilityMapper.get_tag(h_el.get("type"), h_el)
        self.assertEqual(tag, "H4")

        h_el_3 = next(el for el in elements if el["id"] == 6) # 2.2.1
        tag_3 = AccessibilityMapper.get_tag(h_el_3.get("type"), h_el_3)
        self.assertEqual(tag_3, "H3")

    def test_bookmark_tree_nesting(self):
        doc = copy.deepcopy(self.sample_doc)
        enforcer = StructureEnforcer()
        doc = enforcer.enforce(doc)
        ensure_h1(doc)

        # Create pikepdf dummy document to verify outline hierarchy
        pdf = pikepdf.new()
        page = pdf.add_blank_page()
        BookmarkBuilder.build(pdf, doc, [page])

        with pdf.open_outline() as outline:
            # Only one root bookmark at level 1: "2. Main Chapter"
            self.assertEqual(len(outline.root), 1)
            h1_bm = outline.root[0]
            self.assertIn("2. Main Chapter", h1_bm.title)

            # Subsections under H1: 2.1, 2.2, 2.3
            self.assertEqual(len(h1_bm.children), 3)
            bm_21, bm_22, bm_23 = h1_bm.children
            self.assertIn("2.1", bm_21.title)
            self.assertIn("2.2", bm_22.title)
            self.assertIn("2.3", bm_23.title)

            # Under 2.2: 2.2.1 (H3)
            self.assertEqual(len(bm_22.children), 1)
            bm_221 = bm_22.children[0]
            self.assertIn("2.2.1", bm_221.title)

            # Under 2.2.1: 2.2.1.2 (H4)
            self.assertEqual(len(bm_221.children), 1)
            bm_2212 = bm_221.children[0]
            self.assertIn("2.2.1.2", bm_2212.title)

            # Under 2.3: 2.3.1 (H3)
            self.assertEqual(len(bm_23.children), 1)
            bm_231 = bm_23.children[0]
            self.assertIn("2.3.1", bm_231.title)

            # Under 2.3.1: 2.3.1.2 (H4)
            self.assertEqual(len(bm_231.children), 1)
            bm_2312 = bm_231.children[0]
            self.assertIn("2.3.1.2", bm_2312.title)

if __name__ == "__main__":
    unittest.main()
