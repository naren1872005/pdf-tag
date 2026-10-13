import unittest
import json
import pikepdf
from pikepdf import Dictionary, Name, Array

from export.structure_enforcer import StructureEnforcer
from export.accessibility_mapper import AccessibilityMapper
from renderer.struct_tree_builder import StructTreeBuilder


class TestListTaggingHierarchy(unittest.TestCase):

    def setUp(self):
        self.enforcer = StructureEnforcer()

    def test_nested_unordered_list_inside_ordered_item(self):
        """
        Requirements 1 & 4:
        When an unordered list appears inside an existing LI (item 3),
        create a nested L inside that parent item's LBody.
        Nested list items must NOT be siblings of the parent LI.
        Parent list numbering must resume at the top level for item 4 and 5.
        """
        raw_items = [
            {"id": "1", "type": "list_item", "content": "1. First step", "bounding_box": [36.0, 100, 200, 120]},
            {"id": "2", "type": "list_item", "content": "2. Second step", "bounding_box": [36.0, 80, 200, 100]},
            {"id": "3", "type": "list_item", "content": "3. Third step with facts:", "bounding_box": [36.0, 60, 200, 80]},
            {"id": "3a", "type": "list_item", "content": "• Alexandria is a port city", "bounding_box": [48.0, 45, 190, 58]},
            {"id": "3b", "type": "list_item", "content": "• Ancient Alexandria was located", "bounding_box": [48.0, 30, 190, 43]},
            {"id": "3c", "type": "list_item", "content": "• Thousands of artifacts", "bounding_box": [48.0, 15, 190, 28]},
            {"id": "4", "type": "list_item", "content": "4. Fourth top-level step", "bounding_box": [36.0, 0, 200, 14]},
            {"id": "5", "type": "list_item", "content": "5. Fifth top-level step", "bounding_box": [36.0, -20, 200, -1]}
        ]

        doc_json = {
            "pages": [{
                "page_number": 1,
                "elements": raw_items
            }]
        }

        enf_doc = self.enforcer.enforce(doc_json)
        p1_elements = enf_doc["pages"][0]["elements"]

        # Exactly 1 root list element on the page
        self.assertEqual(len(p1_elements), 1)
        root_l = p1_elements[0]
        self.assertEqual(root_l.get("type"), "list")

        # Root list must have exactly 5 top-level list items (1, 2, 3, 4, 5)
        top_items = root_l.get("kids", [])
        self.assertEqual(len(top_items), 5)
        for i, item in enumerate(top_items, 1):
            lbl_child = next(k for k in item["kids"] if k.get("type") == "list_label")
            self.assertEqual(lbl_child["content"], f"{i}.")

        # Item 3 must contain the nested L inside its list_body
        item_3 = top_items[2]
        body_3 = next(k for k in item_3["kids"] if k.get("type") == "list_body")
        self.assertIn("Third step with facts:", body_3.get("content", ""))

        nested_lists = [k for k in body_3.get("kids", []) if k.get("type") == "list"]
        self.assertEqual(len(nested_lists), 1, "Nested list L must exist inside item 3's LBody")

        nested_l = nested_lists[0]
        nested_items = nested_l.get("kids", [])
        self.assertEqual(len(nested_items), 3, "Nested list must contain 3 bullet items")

        for nested_li in nested_items:
            self.assertEqual(nested_li.get("type"), "list_item")
            lbl = next(k for k in nested_li["kids"] if k.get("type") == "list_label")
            self.assertEqual(lbl["content"], "•")

        # Item 4 and 5 must be at the root level, NOT inside item 3
        item_4 = top_items[3]
        lbl_4 = next(k for k in item_4["kids"] if k.get("type") == "list_label")
        self.assertEqual(lbl_4["content"], "4.")

        item_5 = top_items[4]
        lbl_5 = next(k for k in item_5["kids"] if k.get("type") == "list_label")
        self.assertEqual(lbl_5["content"], "5.")

    def test_content_following_list_moved_outside(self):
        """
        Requirement 2:
        Non-list content (footers, page identifiers, headings, paragraphs)
        must remain strictly outside the list structure tags.
        """
        elements = [
            {"id": "h1", "type": "heading", "tag": "H1", "content": "Unit Overview", "bounding_box": [36, 800, 200, 820]},
            {"id": "1", "type": "list_item", "content": "1. Direct students", "bounding_box": [36, 750, 200, 770]},
            {"id": "2", "type": "list_item", "content": "2. Ask students", "bounding_box": [36, 720, 200, 740]},
            {"id": "footer", "type": "paragraph", "content": "T316 Unit 7 The Past", "bounding_box": [36, 20, 140, 32]}
        ]

        doc_json = {"pages": [{"page_number": 1, "elements": elements}]}
        enf_doc = self.enforcer.enforce(doc_json)
        res_els = enf_doc["pages"][0]["elements"]

        # Order must be: Heading H1, List L, Paragraph P (footer)
        types = [e.get("type") for e in res_els]
        self.assertEqual(types, ["heading", "list", "paragraph"])

        # List must only contain item 1 and 2
        root_l = res_els[1]
        self.assertEqual(len(root_l.get("kids", [])), 2)

        # Footer must be completely outside
        footer_el = res_els[2]
        self.assertEqual(footer_el.get("type"), "paragraph")
        self.assertIn("T316", footer_el.get("content", ""))

    def test_deep_three_level_nesting(self):
        """
        Requirements 1:
        Support deeper nesting levels, including lists nested inside other nested lists.
        1. Item 1
           • Sub-bullet
             a. Sub-sub-item A
             b. Sub-sub-item B
           • Sub-bullet 2
        2. Item 2
        """
        items = [
            {"id": "1", "type": "list_item", "content": "1. Step one", "bounding_box": [36.0, 100, 200, 120]},
            {"id": "1b", "type": "list_item", "content": "• Bullet under one", "bounding_box": [48.0, 80, 200, 95]},
            {"id": "1ba", "type": "list_item", "content": "a. Detail A", "bounding_box": [60.0, 60, 200, 75]},
            {"id": "1bb", "type": "list_item", "content": "b. Detail B", "bounding_box": [60.0, 40, 200, 55]},
            {"id": "1c", "type": "list_item", "content": "• Bullet two under one", "bounding_box": [48.0, 20, 200, 35]},
            {"id": "2", "type": "list_item", "content": "2. Step two", "bounding_box": [36.0, 0, 200, 15]}
        ]

        doc_json = {"pages": [{"page_number": 1, "elements": items}]}
        enf_doc = self.enforcer.enforce(doc_json)
        root_l = enf_doc["pages"][0]["elements"][0]

        # Top level has exactly 2 items: 1. and 2.
        self.assertEqual(len(root_l["kids"]), 2)

        # Level 1 item 1
        li_1 = root_l["kids"][0]
        body_1 = next(k for k in li_1["kids"] if k.get("type") == "list_body")
        sub_ls = [k for k in body_1.get("kids", []) if k.get("type") == "list"]
        self.assertEqual(len(sub_ls), 1)

        # Level 2 nested list under item 1 has 2 bullet items
        sub_l = sub_ls[0]
        self.assertEqual(len(sub_l["kids"]), 2)

        # Level 2 bullet 1 contains Level 3 sub-list with a. and b.
        bullet_1 = sub_l["kids"][0]
        body_bullet_1 = next(k for k in bullet_1["kids"] if k.get("type") == "list_body")
        sub_sub_ls = [k for k in body_bullet_1.get("kids", []) if k.get("type") == "list"]
        self.assertEqual(len(sub_sub_ls), 1)

        sub_sub_l = sub_sub_ls[0]
        self.assertEqual(len(sub_sub_l["kids"]), 2)
        lbl_a = next(k for k in sub_sub_l["kids"][0]["kids"] if k.get("type") == "list_label")
        lbl_b = next(k for k in sub_sub_l["kids"][1]["kids"] if k.get("type") == "list_label")
        self.assertEqual(lbl_a["content"], "a.")
        self.assertEqual(lbl_b["content"], "b.")

        # Top level resumed for item 2
        li_2 = root_l["kids"][1]
        lbl_2 = next(k for k in li_2["kids"] if k.get("type") == "list_label")
        self.assertEqual(lbl_2["content"], "2.")

    def test_multi_page_list_continuity(self):
        """
        Requirement 3:
        Lists continuing across pages retain their logical structure and ordering.
        Page 1 ends with 1, 2, 3. Page 2 starts with 4, 5.
        Continuity is linked without duplicate <L> tags.
        """
        page_1_items = [
            {"id": "1", "type": "list_item", "content": "1. Item one", "bounding_box": [36.0, 100, 200, 120]},
            {"id": "2", "type": "list_item", "content": "2. Item two", "bounding_box": [36.0, 80, 200, 100]},
            {"id": "3", "type": "list_item", "content": "3. Item three", "bounding_box": [36.0, 60, 200, 80]},
            {"id": "p1_footer", "type": "paragraph", "content": "Page 1", "bounding_box": [36.0, 20, 100, 30]}
        ]
        page_2_items = [
            {"id": "p2_header", "type": "paragraph", "content": "Unit 7", "bounding_box": [36.0, 820, 100, 835]},
            {"id": "4", "type": "list_item", "content": "4. Item four continuing", "bounding_box": [36.0, 750, 200, 770]},
            {"id": "5", "type": "list_item", "content": "5. Item five continuing", "bounding_box": [36.0, 720, 200, 740]}
        ]

        doc_json = {
            "pages": [
                {"page_number": 1, "elements": page_1_items},
                {"page_number": 2, "elements": page_2_items}
            ]
        }

        enf_doc = self.enforcer.enforce(doc_json)
        p1_list = next(e for e in enf_doc["pages"][0]["elements"] if e.get("type") == "list")
        p2_list = next(e for e in enf_doc["pages"][1]["elements"] if e.get("type") == "list")

        self.assertTrue(p2_list.get("is_continuation"))
        self.assertEqual(p2_list.get("continues_list_id"), str(p1_list.get("id")))

    def test_unrelated_lists_on_consecutive_pages_not_merged(self):
        """
        Requirement 3:
        Do not merge unrelated lists merely because they appear on consecutive pages.
        If Page 2 starts with a new list at 1. or after a heading, do NOT continue.
        """
        page_1_items = [
            {"id": "1", "type": "list_item", "content": "1. Item one", "bounding_box": [36.0, 100, 200, 120]},
            {"id": "2", "type": "list_item", "content": "2. Item two", "bounding_box": [36.0, 80, 200, 100]}
        ]
        page_2_items = [
            {"id": "h2", "type": "heading", "tag": "H2", "content": "Chapter 2 Details", "bounding_box": [36.0, 800, 200, 820]},
            {"id": "p2_1", "type": "list_item", "content": "1. New chapter item", "bounding_box": [36.0, 750, 200, 770]}
        ]

        doc_json = {
            "pages": [
                {"page_number": 1, "elements": page_1_items},
                {"page_number": 2, "elements": page_2_items}
            ]
        }

        enf_doc = self.enforcer.enforce(doc_json)
        p2_list = next(e for e in enf_doc["pages"][1]["elements"] if e.get("type") == "list")
        self.assertFalse(p2_list.get("is_continuation", False))
        self.assertIsNone(p2_list.get("continues_list_id"))


if __name__ == "__main__":
    unittest.main()
