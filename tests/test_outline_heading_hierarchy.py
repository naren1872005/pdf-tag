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

    def test_chapter_5_heading_hierarchy(self):
        """
        Verify:
          Chapter 5 -> H1
          5.1 / 5.2 -> H2
          5.2.1 -> H3
          5.2.1.1 -> H4
          5.2.1.1.1 -> H5
          5.2.1.1.1.1 -> H6
        """
        doc = {
            "pages": [
                {
                    "page_number": 1,
                    "elements": [
                        {"id": "ch5", "type": "paragraph", "content": "Chapter 5: Advanced Strategy"},
                        {"id": "s51", "type": "paragraph", "content": "5.1 Foundation Concepts"},
                        {"id": "s52", "type": "paragraph", "content": "5.2 Execution Methodology"},
                        {"id": "s521", "type": "paragraph", "content": "5.2.1 Framework Details"},
                        {"id": "s5211", "type": "paragraph", "content": "5.2.1.1 Step-by-Step Configuration"},
                        {"id": "s52111", "type": "paragraph", "content": "5.2.1.1.1 Parameter Tuning"},
                        {"id": "s521111", "type": "paragraph", "content": "5.2.1.1.1.1 Edge Case Optimization"},
                    ]
                }
            ]
        }
        enforce_heading_hierarchy(doc)
        ensure_h1(doc)

        els = {e["id"]: e for e in doc["pages"][0]["elements"]}
        self.assertEqual(els["ch5"]["tag"], "H1")
        self.assertEqual(els["ch5"]["heading_level"], 1)

        self.assertEqual(els["s51"]["tag"], "H2")
        self.assertEqual(els["s51"]["heading_level"], 2)

        self.assertEqual(els["s52"]["tag"], "H2")
        self.assertEqual(els["s52"]["heading_level"], 2)

        self.assertEqual(els["s521"]["tag"], "H3")
        self.assertEqual(els["s521"]["heading_level"], 3)

        self.assertEqual(els["s5211"]["tag"], "H4")
        self.assertEqual(els["s5211"]["heading_level"], 4)

        self.assertEqual(els["s52111"]["tag"], "H5")
        self.assertEqual(els["s52111"]["heading_level"], 5)

        self.assertEqual(els["s521111"]["tag"], "H6")
        self.assertEqual(els["s521111"]["heading_level"], 6)

        # Strictly only one H1 in the document
        h1s = [e for e in els.values() if e.get("heading_level") == 1]
        self.assertEqual(len(h1s), 1)

    def test_no_misclassification_headers_footers_captions_lists(self):
        """
        Verify that page headers, footers, captions, list items, and form labels
        are NOT misclassified as headings.
        """
        doc = {
            "pages": [
                {
                    "page_number": 1,
                    "elements": [
                        {"id": "hdr", "type": "header", "content": "Confidential Document - Internal Use Only", "bounding_box": [36, 800, 300, 820]},
                        {"id": "title", "type": "paragraph", "content": "Accessibility Engineering Manual", "is_bold": True},
                        {"id": "p1", "type": "paragraph", "content": "1. Review all guidelines before proceeding with remediation.", "bounding_box": [36, 600, 400, 620]},
                        {"id": "fig_cap", "type": "paragraph", "content": "Figure 4.1: Architecture Flow of Tagging Engine", "bounding_box": [36, 450, 350, 470]},
                        {"id": "tbl_cap", "type": "paragraph", "content": "Table 2.1: Heading Level Distribution across Samples", "bounding_box": [36, 300, 350, 320]},
                        {"id": "form_lbl", "type": "paragraph", "content": "Reviewer Name:", "bounding_box": [36, 150, 150, 170]},
                        {"id": "ftr", "type": "footer", "content": "Page 1 of 12", "bounding_box": [250, 20, 350, 35]},
                    ]
                }
            ]
        }
        enforce_heading_hierarchy(doc)
        ensure_h1(doc)

        els = {e["id"]: e for e in doc["pages"][0]["elements"]}

        # Only the main document title should be H1
        self.assertEqual(els["title"].get("tag"), "H1")
        self.assertEqual(els["title"].get("heading_level"), 1)

        # None of the others should be headings
        self.assertNotEqual(els["hdr"].get("type"), "heading")
        self.assertNotEqual(els["p1"].get("type"), "heading")
        self.assertNotEqual(els["fig_cap"].get("type"), "heading")
        self.assertNotEqual(els["tbl_cap"].get("type"), "heading")
        self.assertNotEqual(els["form_lbl"].get("type"), "heading")
        self.assertNotEqual(els["ftr"].get("type"), "heading")

        # Exactly one H1 in the document
        h1s = [e for e in els.values() if e.get("heading_level") == 1]
        self.assertEqual(len(h1s), 1)

    def test_unnumbered_headings_hierarchy(self):
        """
        Verify that unnumbered headings (Introduction, Methodology, Conclusion, References)
        receive consistent hierarchy and exactly one H1 near the beginning.
        """
        doc = {
            "pages": [
                {
                    "page_number": 1,
                    "elements": [
                        {"id": "h_intro", "type": "paragraph", "content": "Introduction"},
                        {"id": "p_body1", "type": "paragraph", "content": "This paper explores automated PDF accessibility tagging."},
                        {"id": "h_meth", "type": "paragraph", "content": "Methodology"},
                        {"id": "p_body2", "type": "paragraph", "content": "We implemented a deterministic structure tree pipeline."},
                        {"id": "h_conc", "type": "paragraph", "content": "Conclusion"},
                        {"id": "p_body3", "type": "paragraph", "content": "The system achieves 100% compliance with PDF/UA-1 standards."},
                        {"id": "h_ref", "type": "paragraph", "content": "References"},
                        {"id": "p_body4", "type": "paragraph", "content": "[1] ISO 14289-1:2014 Document management applications."},
                    ]
                }
            ]
        }
        enforce_heading_hierarchy(doc)
        ensure_h1(doc)

        els = {e["id"]: e for e in doc["pages"][0]["elements"]}
        self.assertEqual(els["h_intro"]["tag"], "H1")
        self.assertEqual(els["h_intro"]["heading_level"], 1)

        self.assertEqual(els["h_meth"]["tag"], "H2")
        self.assertEqual(els["h_meth"]["heading_level"], 2)

        self.assertEqual(els["h_conc"]["tag"], "H2")
        self.assertEqual(els["h_conc"]["heading_level"], 2)

        self.assertEqual(els["h_ref"]["tag"], "H2")
        self.assertEqual(els["h_ref"]["heading_level"], 2)

        # Exactly one H1
        h1s = [e for e in els.values() if e.get("heading_level") == 1]
        self.assertEqual(len(h1s), 1)

if __name__ == "__main__":
    unittest.main()
