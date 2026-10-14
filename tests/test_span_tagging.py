import unittest
import pikepdf
from pikepdf import Dictionary, Name, Array

from export.span_enforcer import SpanEnforcer
from export.structure_enforcer import StructureEnforcer
from export.accessibility_mapper import AccessibilityMapper
from renderer.struct_tree_builder import StructTreeBuilder
from renderer.struct_parent_allocator import StructParentAllocator


class TestSpanTagging(unittest.TestCase):

    def setUp(self):
        self.enforcer = SpanEnforcer()
        self.struct_enforcer = StructureEnforcer()

    def test_unordered_list_bullet_span(self):
        """
        Requirement 1: Unordered List Bullets — Lbl > Span
        When an unordered list uses a bullet symbol, the bullet label must contain
        a Span whose actual text is 'Bullet'.
        Ordered list labels (1., 2.) must remain unchanged.
        """
        doc_json = {
            "pages": [{
                "page_number": 1,
                "elements": [
                    {
                        "id": "li1",
                        "type": "list_item",
                        "tag": "LI",
                        "kids": [
                            {"id": "lbl1", "type": "list_label", "tag": "Lbl", "content": "•"},
                            {"id": "lbody1", "type": "list_body", "tag": "LBody", "content": "Alexandria is a port city..."}
                        ]
                    },
                    {
                        "id": "li2",
                        "type": "list_item",
                        "tag": "LI",
                        "kids": [
                            {"id": "lbl2", "type": "list_label", "tag": "Lbl", "content": "1."},
                            {"id": "lbody2", "type": "list_body", "tag": "LBody", "content": "First ordered instruction."}
                        ]
                    }
                ]
            }]
        }

        res = self.enforcer.enforce(doc_json)
        p1 = res["pages"][0]["elements"]

        # 1. Unordered bullet item: Lbl > Span with content "•" and actual_text "Bullet"
        li1 = p1[0]
        lbl1 = next(k for k in li1["kids"] if k.get("type") in ("list_label", "lbl"))
        self.assertEqual(len(lbl1.get("kids", [])), 1)
        span1 = lbl1["kids"][0]
        self.assertEqual(span1.get("type"), "span")
        self.assertEqual(span1.get("tag"), "Span")
        self.assertEqual(span1.get("content"), "•")
        self.assertEqual(span1.get("actual_text"), " Bullet ")
        self.assertEqual(lbl1.get("content", ""), "")

        # LBody must hold its content directly without a child <P> tag
        lbody1 = next(k for k in li1["kids"] if k.get("type") in ("list_body", "lbody"))
        self.assertEqual(lbody1.get("content"), "Alexandria is a port city...")
        self.assertEqual(len(lbody1.get("kids", [])), 0)

        # 2. Ordered item: Lbl must NOT have "Bullet" Span
        li2 = p1[1]
        lbl2 = next(k for k in li2["kids"] if k.get("type") in ("list_label", "lbl"))
        self.assertEqual(lbl2.get("content"), "1.")
        # Must not be converted to Bullet
        self.assertFalse(any(k.get("actual_text") == " Bullet " for k in lbl2.get("kids", [])))

    def test_nested_list_bullets_hierarchy(self):
        """
        Nested unordered lists must retain correct hierarchy:
        Parent LI > LBody > L > LI > Lbl > Span('Bullet').
        """
        raw_items = [
            {"id": "1", "type": "list_item", "content": "1. Main step", "bounding_box": [36, 100, 200, 120]},
            {"id": "1a", "type": "list_item", "content": "• Sub bullet 1", "bounding_box": [48, 80, 190, 95]},
            {"id": "1b", "type": "list_item", "content": "• Sub bullet 2", "bounding_box": [48, 60, 190, 75]},
            {"id": "2", "type": "list_item", "content": "2. Next main step", "bounding_box": [36, 40, 200, 55]}
        ]
        doc = {"pages": [{"page_number": 1, "elements": raw_items}]}
        doc = self.struct_enforcer.enforce(doc)
        doc = self.enforcer.enforce(doc)

        root_l = doc["pages"][0]["elements"][0]
        item1 = root_l["kids"][0]
        body1 = next(k for k in item1["kids"] if k.get("type") == "list_body")
        nested_l = next(k for k in body1["kids"] if k.get("type") == "list")
        sub_items = nested_l["kids"]

        self.assertEqual(len(sub_items), 2)
        for sub_li in sub_items:
            sub_lbl = next(k for k in sub_li["kids"] if k.get("type") == "list_label")
            self.assertEqual(len(sub_lbl.get("kids", [])), 1)
            span = sub_lbl["kids"][0]
            self.assertEqual(span.get("tag"), "Span")
            self.assertEqual(span.get("actual_text"), " Bullet ")
            self.assertEqual(span.get("content"), "•")

    def test_lbody_unwrap_redundant_p_and_lbl_span_content(self):
        """
        Verify that:
        1. LBody's content is stored directly on LBody, unwrapping any redundant <P> child.
        2. Bullet marker resides inside Span (content='•'), and Span's actual_text is ' Bullet '.
        3. Lbl container has content=''.
        """
        doc_json = {
            "pages": [{
                "page_number": 1,
                "elements": [
                    {
                        "id": "li_user_case",
                        "type": "list_item",
                        "tag": "LI",
                        "kids": [
                            {
                                "id": "lbl_case",
                                "type": "list_label",
                                "tag": "Lbl",
                                "content": "•",
                                "kids": [
                                    {
                                        "id": "span_old",
                                        "type": "span",
                                        "tag": "Span",
                                        "content": "Bullet",
                                        "actual_text": "Bullet"
                                    }
                                ]
                            },
                            {
                                "id": "lbody_case",
                                "type": "list_body",
                                "tag": "LBody",
                                "content": "",
                                "kids": [
                                    {
                                        "id": "p_child",
                                        "type": "paragraph",
                                        "tag": "P",
                                        "content": "Participate in a Discussion Learning Strategies"
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }]
        }
        res = self.enforcer.enforce(doc_json)
        li = res["pages"][0]["elements"][0]
        lbl = li["kids"][0]
        lbody = li["kids"][1]

        # Lbl should be empty container
        self.assertEqual(lbl.get("content", ""), "")
        # Span must contain bullet marker '•' and actual_text=' Bullet '
        self.assertEqual(len(lbl["kids"]), 1)
        span = lbl["kids"][0]
        self.assertEqual(span.get("content"), "•")
        self.assertEqual(span.get("actual_text"), " Bullet ")

        # LBody must contain the content directly without child P
        self.assertEqual(lbody.get("content"), "Participate in a Discussion Learning Strategies")
        self.assertEqual(len(lbody.get("kids", [])), 0)

    def test_labels_such_as_name(self):
        """
        Requirement 2: Labels such as 'Name:'
        When a line contains a field label like 'Name:', 'Date:', 'Address:',
        represent the label using a Span inside the appropriate semantic container.
        """
        elements = [
            {"id": "p1", "type": "paragraph", "tag": "P", "content": "Name: John Smith", "bounding_box": [10, 10, 100, 20]},
            {"id": "p2", "type": "paragraph", "tag": "P", "content": "Date: October 14, 2026", "bounding_box": [10, 30, 100, 40]},
            {"id": "p3", "type": "paragraph", "tag": "P", "content": "Note that: this ordinary prose should not be split into a form label.", "bounding_box": [10, 50, 200, 60]}
        ]
        doc = {"pages": [{"page_number": 1, "elements": elements}]}
        doc = self.enforcer.enforce(doc)
        p1_res, p2_res, p3_res = doc["pages"][0]["elements"]

        # p1: <P><Span>Name:</Span><Span>John Smith</Span></P>
        self.assertEqual(p1_res.get("tag"), "P")
        self.assertEqual(len(p1_res.get("kids", [])), 2)
        self.assertEqual(p1_res["kids"][0].get("tag"), "Span")
        self.assertEqual(p1_res["kids"][0].get("content"), "Name:")
        self.assertEqual(p1_res["kids"][1].get("tag"), "Span")
        self.assertEqual(p1_res["kids"][1].get("content"), "John Smith")

        # p2: <P><Span>Date:</Span><Span>October 14, 2026</Span></P>
        self.assertEqual(p2_res.get("tag"), "P")
        self.assertEqual(len(p2_res.get("kids", [])), 2)
        self.assertEqual(p2_res["kids"][0].get("content"), "Date:")
        self.assertEqual(p2_res["kids"][1].get("content"), "October 14, 2026")

        # p3: Prose with 'Note that:' must NOT be split into label spans
        self.assertEqual(p3_res.get("tag"), "P")
        self.assertEqual(len(p3_res.get("kids", [])), 0)
        self.assertIn("Note that:", p3_res.get("content"))

    def test_words_split_across_lines(self):
        """
        Requirement 3: Words Split Across Lines
        Detect words split across visual lines with hyphenation (accessi- bility)
        and wrap in Spans within parent container.
        """
        elements = [
            {
                "id": "p1",
                "type": "paragraph",
                "tag": "P",
                "content": "This document ensures accessi-\nbility for all readers.",
                "bounding_box": [10, 10, 200, 30]
            }
        ]
        doc = {"pages": [{"page_number": 1, "elements": elements}]}
        doc = self.enforcer.enforce(doc)
        p1 = doc["pages"][0]["elements"][0]

        self.assertEqual(p1.get("tag"), "P")
        self.assertEqual(len(p1.get("kids", [])), 2)
        span1, span2 = p1["kids"]
        self.assertEqual(span1.get("tag"), "Span")
        self.assertEqual(span2.get("tag"), "Span")
        self.assertTrue(span1["content"].endswith("accessi-"))
        self.assertTrue(span2["content"].startswith("bility"))

    def test_standalone_lines_span(self):
        """
        Requirement 4: Standalone Lines Must Use Span Where Appropriate
        Short standalone lines or text fragments separated by line breaks
        should be grouped in Spans inside parent P.
        """
        elements = [
            {
                "id": "p1",
                "type": "paragraph",
                "tag": "P",
                "content": "Line One Information\nLine Two Information",
                "bounding_box": [10, 10, 150, 30]
            },
            {
                "id": "p2",
                "type": "paragraph",
                "tag": "P",
                "content": "Important information",
                "is_standalone_line": True,
                "bounding_box": [10, 40, 150, 50]
            }
        ]
        doc = {"pages": [{"page_number": 1, "elements": elements}]}
        doc = self.enforcer.enforce(doc)
        p1, p2 = doc["pages"][0]["elements"]

        self.assertEqual(p1.get("tag"), "P")
        self.assertEqual(len(p1.get("kids", [])), 2)
        self.assertEqual(p1["kids"][0].get("content"), "Line One Information")
        self.assertEqual(p1["kids"][1].get("content"), "Line Two Information")

        self.assertEqual(p2.get("tag"), "P")
        self.assertEqual(len(p2.get("kids", [])), 1)
        self.assertEqual(p2["kids"][0].get("tag"), "Span")
        self.assertEqual(p2["kids"][0].get("content"), "Important information")

    def test_underlined_text_span(self):
        """
        Requirement 5: Underlined Text — Preserve Underline Formatting with Span
        When text is underlined within a paragraph, preserve existing paragraph tag
        and represent the underlined text with a Span that carries Underline='true'.
        """
        elements = [
            {
                "id": "p1",
                "type": "paragraph",
                "tag": "P",
                "content": "Read the important instructions carefully.",
                "bounding_box": [10, 10, 200, 20],
                "_spans_meta": [
                    {"text": "Read the ", "underline": False},
                    {"text": "important instructions", "underline": True},
                    {"text": " carefully.", "underline": False}
                ]
            }
        ]
        doc = {"pages": [{"page_number": 1, "elements": elements}]}
        doc = self.enforcer.enforce(doc)
        p1 = doc["pages"][0]["elements"][0]

        self.assertEqual(p1.get("tag"), "P")
        self.assertEqual(len(p1.get("kids", [])), 3)

        span_pre = p1["kids"][0]
        span_u = p1["kids"][1]
        span_post = p1["kids"][2]

        self.assertEqual(span_pre.get("content"), "Read the")
        self.assertEqual(span_u.get("content"), "important instructions")
        self.assertTrue(span_u.get("underline"))
        self.assertEqual(span_u.get("attributes", {}).get("Underline"), "true")
        self.assertEqual(span_post.get("content"), "carefully.")

    def test_struct_tree_builder_span_pdf_attributes(self):
        """
        Verify that StructTreeBuilder generates valid PDF /StructElem objects
        with /S /Span and Layout TextDecorationType /Underline attribute.
        """
        pdf = pikepdf.new()
        page = pdf.add_blank_page()
        struct_tree_root, doc_elem = StructTreeBuilder.create_root(pdf)

        elements = [
            {
                "id": "p1",
                "type": "paragraph",
                "tag": "P",
                "kids": [
                    {
                        "id": "sp1",
                        "type": "span",
                        "tag": "Span",
                        "content": "underlined phrase",
                        "underline": True,
                        "attributes": {"Underline": "true"}
                    }
                ]
            }
        ]

        page_mcid_map = {}
        id_to_mcid = {"sp1": 0}
        sp_allocator = StructParentAllocator()

        StructTreeBuilder.build_tree(
            pdf=pdf,
            elements=elements,
            parent_elem=doc_elem,
            struct_tree_root=struct_tree_root,
            page_mcid_map=page_mcid_map,
            current_page=page,
            get_tag_fn=AccessibilityMapper.get_tag,
            id_to_mcid=id_to_mcid,
            sp_allocator=sp_allocator
        )

        # Inspect Document kids
        p_elem = doc_elem.K[0]
        self.assertEqual(str(p_elem.S), "/P")

        span_elem = p_elem.K[0]
        self.assertEqual(str(span_elem.S), "/Span")

        # Verify /A attribute has /O /Layout and /TextDecorationType /Underline
        self.assertIn("/A", span_elem)
        a_dict = span_elem.A
        self.assertEqual(str(a_dict.get("/O")), "/Layout")
        self.assertEqual(str(a_dict.get("/TextDecorationType")), "/Underline")

        # Verify MCID is inside Span, not duplicated on P
        self.assertEqual(list(span_elem.K), [0])
        self.assertEqual(len(p_elem.K), 1)  # only contains span_elem

        pdf.close()


if __name__ == "__main__":
    unittest.main()
