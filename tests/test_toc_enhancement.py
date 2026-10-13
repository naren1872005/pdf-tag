import unittest
import os
import io
import re
import fitz
import pikepdf
from pikepdf import Name, Dictionary, Array, String

from parser.gemini_extractor import (
    PageElement,
    get_page_link_annotations,
    assemble_toc_section
)
from export.accessibility_mapper import AccessibilityMapper
from renderer.page_renderer import PageRenderer
from renderer.struct_tree_builder import StructTreeBuilder
from renderer.parent_tree_builder import ParentTreeBuilder
from renderer.struct_parent_allocator import StructParentAllocator


class TestTOCEnhancement(unittest.TestCase):

    def setUp(self):
        self.page_height = 792.0
        self.page_width = 612.0

    def test_accessibility_mapper_mappings(self):
        """Verify that TOC semantic types map to valid PDF/UA structure types."""
        self.assertEqual(AccessibilityMapper.get_tag("toc"), "TOC")
        self.assertEqual(AccessibilityMapper.get_tag("toci"), "TOCI")
        self.assertEqual(AccessibilityMapper.get_tag("toc_item"), "TOCI")
        self.assertEqual(AccessibilityMapper.get_tag("reference"), "Reference")
        self.assertEqual(AccessibilityMapper.get_tag("link"), "Link")
        self.assertEqual(AccessibilityMapper.get_tag("objr"), "OBJR")

    def test_toc_assembly_linked(self):
        """
        Verify that matched TOC entries generate the exact semantic hierarchy:
        TOC -> TOCI -> Reference -> Link -> OBJR
        with content only on the Link leaf.
        """
        toc_entries = [
            {
                "type": "toc_entry",
                "id": 1,
                "reading_order": 1,
                "bounding_box": [50.0, 700.0, 500.0, 715.0],
                "content": "Chapter 1. Introduction ..................... 1",
                "confidence": 0.98,
                "toc_level": 1
            },
            {
                "type": "toc_entry",
                "id": 2,
                "reading_order": 2,
                "bounding_box": [50.0, 680.0, 500.0, 695.0],
                "content": "Chapter 2. Fundamentals ..................... 15",
                "confidence": 0.97,
                "toc_level": 1
            }
        ]

        link_annotations = [
            {
                "xref": 101,
                "a_idx": 0,
                "runtime_annot_id": "page_0_annot_0",
                "pdf_rect": [50.0, 700.0, 500.0, 715.0],
                "link_data": {"kind": 1, "page": 1}
            },
            {
                "xref": 102,
                "a_idx": 1,
                "runtime_annot_id": "page_0_annot_1",
                "pdf_rect": [50.0, 680.0, 500.0, 695.0],
                "link_data": {"kind": 1, "page": 15}
            }
        ]

        matched_indices = set()
        toc_container, next_id = assemble_toc_section(
            toc_entries, link_annotations, matched_indices, page_num=1, start_id=10
        )

        self.assertIsNotNone(toc_container)
        self.assertEqual(toc_container["type"], "toc")
        self.assertEqual(len(matched_indices), 2)
        self.assertEqual(len(toc_container["kids"]), 2)

        # Check first entry: TOC -> TOCI -> Reference -> Link -> OBJR
        toci_1 = toc_container["kids"][0]
        self.assertEqual(toci_1["type"], "toci")
        self.assertEqual(toci_1["toc_level"], 1)
        self.assertEqual(len(toci_1["kids"]), 1)

        ref_1 = toci_1["kids"][0]
        self.assertEqual(ref_1["type"], "reference")
        self.assertNotIn("content", ref_1)  # Content moved to Link
        self.assertEqual(len(ref_1["kids"]), 1)

        link_1 = ref_1["kids"][0]
        self.assertEqual(link_1["type"], "link")
        self.assertEqual(link_1["content"], "Chapter 1. Introduction ..................... 1")
        self.assertEqual(link_1["annotation"]["runtime_annot_id"], "page_0_annot_0")
        self.assertEqual(len(link_1["kids"]), 1)

        objr_1 = link_1["kids"][0]
        self.assertEqual(objr_1["type"], "objr")
        self.assertEqual(objr_1["annotation"]["runtime_annot_id"], "page_0_annot_0")

    def test_toc_assembly_unlinked_scanned(self):
        """
        Verify that unlinked TOC entries (e.g. from scanned PDFs) generate:
        TOC -> TOCI -> Reference
        and NEVER invent Link or OBJR objects.
        """
        toc_entries = [
            {
                "type": "toc_entry",
                "id": 1,
                "reading_order": 1,
                "bounding_box": [60.0, 720.0, 480.0, 735.0],
                "content": "Foreword .................................... iii",
                "confidence": 0.95,
                "toc_level": 1
            }
        ]

        link_annotations = []  # No link annotations
        matched_indices = set()
        toc_container, next_id = assemble_toc_section(
            toc_entries, link_annotations, matched_indices, page_num=1, start_id=1
        )

        self.assertIsNotNone(toc_container)
        self.assertEqual(toc_container["type"], "toc")
        self.assertEqual(len(toc_container["kids"]), 1)

        toci = toc_container["kids"][0]
        self.assertEqual(toci["type"], "toci")
        self.assertEqual(len(toci["kids"]), 1)

        ref = toci["kids"][0]
        self.assertEqual(ref["type"], "reference")
        # Content stays on Reference leaf
        self.assertEqual(ref["content"], "Foreword .................................... iii")
        # Strictly NO kids, NO Link, NO OBJR
        self.assertNotIn("kids", ref)

    def test_toc_assembly_unmatched_partial(self):
        """
        Verify that unmatched entries in a mixed page remain unlinked
        without fabricating fake Link or OBJR objects.
        """
        toc_entries = [
            {
                "type": "toc_entry",
                "id": 1,
                "reading_order": 1,
                "bounding_box": [50.0, 700.0, 500.0, 715.0],
                "content": "Linked Entry ................................ 1",
                "confidence": 0.99,
                "toc_level": 1
            },
            {
                "type": "toc_entry",
                "id": 2,
                "reading_order": 2,
                "bounding_box": [50.0, 650.0, 500.0, 665.0],
                "content": "Unmatched Entry ............................. 5",
                "confidence": 0.99,
                "toc_level": 1
            }
        ]

        # Only one annotation exists, matching entry 1
        link_annotations = [
            {
                "xref": 201,
                "a_idx": 0,
                "runtime_annot_id": "page_0_annot_0",
                "pdf_rect": [50.0, 700.0, 500.0, 715.0],
                "link_data": {"kind": 1, "page": 1}
            }
        ]

        matched_indices = set()
        toc_container, next_id = assemble_toc_section(
            toc_entries, link_annotations, matched_indices, page_num=1, start_id=1
        )

        self.assertEqual(len(matched_indices), 1)
        # Entry 1 is linked
        ref_1 = toc_container["kids"][0]["kids"][0]
        self.assertIn("kids", ref_1)
        self.assertEqual(ref_1["kids"][0]["type"], "link")

        # Entry 2 is unlinked (no Link or OBJR)
        ref_2 = toc_container["kids"][1]["kids"][0]
        self.assertNotIn("kids", ref_2)
        self.assertEqual(ref_2["content"], "Unmatched Entry ............................. 5")

    def test_toc_assembly_nested_hierarchy(self):
        """
        Verify that nested TOC entries (toc_level > 1) produce valid sub-TOC containers
        inside the parent TOCI in accordance with ISO 32000-1 §14.8.4.3.3.
        """
        toc_entries = [
            {
                "type": "toc_entry",
                "id": 1,
                "reading_order": 1,
                "bounding_box": [50.0, 700.0, 500.0, 715.0],
                "content": "Chapter 1. Introduction ..................... 1",
                "confidence": 0.99,
                "toc_level": 1
            },
            {
                "type": "toc_entry",
                "id": 2,
                "reading_order": 2,
                "bounding_box": [70.0, 680.0, 500.0, 695.0],
                "content": "1.1 Background ............................. 2",
                "confidence": 0.98,
                "toc_level": 2
            },
            {
                "type": "toc_entry",
                "id": 3,
                "reading_order": 3,
                "bounding_box": [70.0, 660.0, 500.0, 675.0],
                "content": "1.2 Objectives ............................. 5",
                "confidence": 0.98,
                "toc_level": 2
            },
            {
                "type": "toc_entry",
                "id": 4,
                "reading_order": 4,
                "bounding_box": [50.0, 640.0, 500.0, 655.0],
                "content": "Chapter 2. Related Work .................... 10",
                "confidence": 0.99,
                "toc_level": 1
            }
        ]

        matched_indices = set()
        toc_container, next_id = assemble_toc_section(
            toc_entries, [], matched_indices, page_num=1, start_id=1
        )

        # Root TOC should have 2 top-level TOCIs (Chapter 1 and Chapter 2)
        self.assertEqual(len(toc_container["kids"]), 2)
        toci_ch1 = toc_container["kids"][0]
        toci_ch2 = toc_container["kids"][1]

        self.assertEqual(toci_ch1["kids"][0]["content"], "Chapter 1. Introduction ..................... 1")
        self.assertEqual(toci_ch2["kids"][0]["content"], "Chapter 2. Related Work .................... 10")

        # Chapter 1 TOCI has Reference + nested sub-TOC
        self.assertEqual(len(toci_ch1["kids"]), 2)
        sub_toc = toci_ch1["kids"][1]
        self.assertEqual(sub_toc["type"], "toc")
        self.assertEqual(len(sub_toc["kids"]), 2)

        # Sub-TOC has Section 1.1 and Section 1.2
        self.assertEqual(sub_toc["kids"][0]["kids"][0]["content"], "1.1 Background ............................. 2")
        self.assertEqual(sub_toc["kids"][1]["kids"][0]["content"], "1.2 Objectives ............................. 5")

    def test_end_to_end_pdf_export_linked_toc(self):
        """
        End-to-end PDF export test:
        1. Construct a pikepdf document with a real link annotation.
        2. Build a document JSON with a linked TOC entry.
        3. Run PageRenderer, StructTreeBuilder, and ParentTreeBuilder.
        4. Validate that:
           - OBJR is an indirect dictionary with /Type /OBJR, /Obj <annot>, /Pg <page>
           - OBJR is NEVER a /StructElem /S /OBJR
           - Annot has /StructParent matching ParentTree Nums entry pointing to /Link
           - Text is rendered exactly once into the content stream
        """
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page(page_size=(self.page_width, self.page_height))
        page.Tabs = Name("/S")

        # Create a real link annotation on the page
        annot_rect = Array([50.0, 700.0, 500.0, 715.0])
        link_annot = pdf.make_indirect(Dictionary(
            Type=Name("/Annot"),
            Subtype=Name("/Link"),
            Rect=annot_rect,
            A=Dictionary(S=Name("/GoTo"), D=Array([page.obj, Name("/Fit")]))
        ))
        page.Annots = pdf.make_indirect(Array([link_annot]))

        sp_allocator = StructParentAllocator()
        page_sp_id = sp_allocator.allocate_page(0)
        page.StructParents = page_sp_id

        doc_json = {
            "type": "document",
            "metadata": {"title": "TOC Test", "page_count": 1},
            "pages": [
                {
                    "page_number": 1,
                    "elements": [
                        {
                            "type": "toc",
                            "id": 1,
                            "reading_order": 1,
                            "page_number": 1,
                            "bounding_box": [50.0, 700.0, 500.0, 715.0],
                            "kids": [
                                {
                                    "type": "toci",
                                    "id": 2,
                                    "reading_order": 1,
                                    "page_number": 1,
                                    "bounding_box": [50.0, 700.0, 500.0, 715.0],
                                    "toc_level": 1,
                                    "kids": [
                                        {
                                            "type": "reference",
                                            "id": 3,
                                            "reading_order": 1,
                                            "page_number": 1,
                                            "bounding_box": [50.0, 700.0, 500.0, 715.0],
                                            "kids": [
                                                {
                                                    "type": "link",
                                                    "id": 4,
                                                    "reading_order": 1,
                                                    "page_number": 1,
                                                    "bounding_box": [50.0, 700.0, 500.0, 715.0],
                                                    "content": "Chapter 1. Introduction ..................... 1",
                                                    "annotation": {
                                                        "runtime_annot_id": "page_0_annot_0",
                                                        "rect": [50.0, 700.0, 500.0, 715.0]
                                                    },
                                                    "kids": [
                                                        {
                                                            "type": "objr",
                                                            "id": 5,
                                                            "reading_order": 1,
                                                            "page_number": 1,
                                                            "annotation": {
                                                                "runtime_annot_id": "page_0_annot_0"
                                                            }
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        struct_tree_root, doc_elem = StructTreeBuilder.create_root(pdf)

        def custom_get_tag_fn(el_type, el):
            return AccessibilityMapper.get_tag(el_type, el)

        elements = doc_json["pages"][0]["elements"]

        # 1. Render page content
        mcid_counter, render_results, annotation_map = PageRenderer.render_page(
            pdf=pdf,
            page_idx=0,
            elements=elements,
            font=fitz.Font("helv"),
            name_to_id={},
            get_tag_fn=custom_get_tag_fn,
            document_type="digital"
        )

        id_to_mcid = {rr.object_id: rr.mcid for rr in render_results}
        page_mcid_map = {}

        # 2. Build Structure Tree
        StructTreeBuilder.build_tree(
            pdf,
            elements,
            doc_elem,
            struct_tree_root,
            page_mcid_map,
            page,
            custom_get_tag_fn,
            id_to_mcid,
            sp_allocator=sp_allocator,
            annotation_map=annotation_map
        )

        # 3. Build Parent Tree
        ParentTreeBuilder.build(pdf, struct_tree_root, [page_mcid_map], sp_allocator)

        # Save to memory and inspect
        out_bytes = io.BytesIO()
        pdf.save(out_bytes)
        out_bytes.seek(0)

        with pikepdf.open(out_bytes) as loaded_pdf:
            loaded_root = loaded_pdf.Root.StructTreeRoot
            loaded_doc = loaded_root.K[0]

            # Structural hierarchy check
            toc_elem = loaded_doc.K[0]
            self.assertEqual(str(toc_elem.get("/S")), "/TOC")

            toci_elem = toc_elem.K[0]
            self.assertEqual(str(toci_elem.get("/S")), "/TOCI")

            ref_elem = toci_elem.K[0]
            self.assertEqual(str(ref_elem.get("/S")), "/Reference")

            link_elem = ref_elem.K[0]
            self.assertEqual(str(link_elem.get("/S")), "/Link")

            # Check Link's /K content
            link_k = link_elem.get("/K")
            self.assertIsNotNone(link_k)
            self.assertEqual(len(link_k), 2)  # [ mcid_integer, objr_dictionary ]

            objr_dicts = [k for k in link_k if isinstance(k, Dictionary) and k.get("/Type") == Name("/OBJR")]
            self.assertEqual(len(objr_dicts), 1)
            objr_dict = objr_dicts[0]

            mcid_items = [k for k in link_k if k != objr_dict]
            self.assertEqual(len(mcid_items), 1)
            mcid_val = int(mcid_items[0])
            self.assertEqual(mcid_val, 0)

            # CRITICAL CHECK: OBJR must be a dictionary with /Type /OBJR, NEVER /StructElem /S /OBJR
            self.assertEqual(str(objr_dict.get("/Type")), "/OBJR")
            self.assertNotIn("/StructElem", str(objr_dict.get("/Type")))
            self.assertNotIn("/S", objr_dict)  # Must not have /S key

            # Check OBJR pointers
            self.assertEqual(str(objr_dict.get("/Pg")), str(loaded_pdf.pages[0].obj))
            self.assertEqual(str(objr_dict.get("/Obj")), str(loaded_pdf.pages[0].Annots[0]))

            # Check Annotation /StructParent
            annot_obj = loaded_pdf.pages[0].Annots[0]
            self.assertIn("/StructParent", annot_obj)
            annot_sp = int(annot_obj.StructParent)

            # Check ParentTree mapping
            parent_tree = loaded_root.ParentTree
            nums = list(parent_tree.Nums)
            # nums is [page_sp, [mcids...], annot_sp, link_elem]
            keys = [int(x) for x in nums[::2]]
            self.assertIn(annot_sp, keys)

            # Find annot_sp in nums and verify target is the Link element
            sp_idx = keys.index(annot_sp) * 2
            target_struct_elem = nums[sp_idx + 1]
            self.assertEqual(str(target_struct_elem.get("/S")), "/Link")
            self.assertEqual(str(target_struct_elem.ID), str(link_elem.ID))

            # Verify text is rendered exactly once
            contents_obj = loaded_pdf.pages[0].Contents
            if isinstance(contents_obj, pikepdf.Array):
                content_stream = b"".join(s.read_bytes() for s in contents_obj).decode("latin-1", errors="ignore")
            else:
                content_stream = contents_obj.read_bytes().decode("latin-1", errors="ignore")
            # Should have exactly ONE /Span <</MCID 0>> BDC
            self.assertEqual(content_stream.count("/MCID"), 1)
            self.assertEqual(len(re.findall(r'/MCID\s+0', content_stream)), 1)

    def test_end_to_end_pdf_export_unlinked_scanned_toc(self):
        """
        Verify export of unlinked TOC entries (e.g. scanned PDF without link annotations):
        - Structure is TOC -> TOCI -> Reference
        - MCID is assigned to Reference
        - No Link and no OBJR are emitted
        """
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page(page_size=(self.page_width, self.page_height))

        sp_allocator = StructParentAllocator()
        page_sp_id = sp_allocator.allocate_page(0)
        page.StructParents = page_sp_id

        doc_json = {
            "type": "document",
            "metadata": {"title": "Scanned TOC Test", "page_count": 1},
            "pages": [
                {
                    "page_number": 1,
                    "elements": [
                        {
                            "type": "toc",
                            "id": 1,
                            "reading_order": 1,
                            "page_number": 1,
                            "bounding_box": [50.0, 700.0, 500.0, 715.0],
                            "kids": [
                                {
                                    "type": "toci",
                                    "id": 2,
                                    "reading_order": 1,
                                    "page_number": 1,
                                    "bounding_box": [50.0, 700.0, 500.0, 715.0],
                                    "toc_level": 1,
                                    "kids": [
                                        {
                                            "type": "reference",
                                            "id": 3,
                                            "reading_order": 1,
                                            "page_number": 1,
                                            "bounding_box": [50.0, 700.0, 500.0, 715.0],
                                            "content": "Preface ..................................... v"
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        struct_tree_root, doc_elem = StructTreeBuilder.create_root(pdf)

        def custom_get_tag_fn(el_type, el):
            return AccessibilityMapper.get_tag(el_type, el)

        elements = doc_json["pages"][0]["elements"]

        mcid_counter, render_results, annotation_map = PageRenderer.render_page(
            pdf=pdf,
            page_idx=0,
            elements=elements,
            font=fitz.Font("helv"),
            name_to_id={},
            get_tag_fn=custom_get_tag_fn,
            document_type="scanned"
        )

        id_to_mcid = {rr.object_id: rr.mcid for rr in render_results}
        page_mcid_map = {}

        StructTreeBuilder.build_tree(
            pdf,
            elements,
            doc_elem,
            struct_tree_root,
            page_mcid_map,
            page,
            custom_get_tag_fn,
            id_to_mcid,
            sp_allocator=sp_allocator,
            annotation_map=annotation_map
        )

        ParentTreeBuilder.build(pdf, struct_tree_root, [page_mcid_map], sp_allocator)

        out_bytes = io.BytesIO()
        pdf.save(out_bytes)
        out_bytes.seek(0)

        with pikepdf.open(out_bytes) as loaded_pdf:
            loaded_root = loaded_pdf.Root.StructTreeRoot
            loaded_doc = loaded_root.K[0]

            toc_elem = loaded_doc.K[0]
            self.assertEqual(str(toc_elem.get("/S")), "/TOC")

            toci_elem = toc_elem.K[0]
            self.assertEqual(str(toci_elem.get("/S")), "/TOCI")

            ref_elem = toci_elem.K[0]
            self.assertEqual(str(ref_elem.get("/S")), "/Reference")

            # Reference's /K should contain the MCID directly (no Link child)
            self.assertEqual(len(ref_elem.K), 1)
            self.assertEqual(int(ref_elem.K[0]), 0)

            # Absolutely no /Link or /OBJR in the document
            content_str = out_bytes.getvalue().decode("latin-1", errors="ignore")
            self.assertNotIn("/S /Link", content_str)
            self.assertNotIn("/Type /OBJR", content_str)

    def test_real_pdf_toc_page_link_extraction(self):
        """
        Verify on a real-world document (workspace/P1 9/input.pdf, Page 5)
        that get_page_link_annotations extracts all 13 links with exact runtime_annot_ids,
        and assemble_toc_section matches simulated TOC entries against them.
        """
        pdf_path = "workspace/P1 9/input.pdf"
        if not os.path.exists(pdf_path):
            self.skipTest(f"Test PDF {pdf_path} not found")

        doc = fitz.open(pdf_path)
        page_index = 4  # Page 5
        page = doc[page_index]
        page_height_pt = page.rect.height

        links = get_page_link_annotations(doc, page, page_index, page_height_pt)
        self.assertEqual(len(links), 13)

        # Check all runtime IDs follow the pattern page_4_annot_{0..12}
        expected_ids = [f"page_4_annot_{i}" for i in range(13)]
        actual_ids = [l["runtime_annot_id"] for l in links]
        self.assertEqual(actual_ids, expected_ids)

        # Build simulated TOC entries matching each link's vertical line
        toc_entries = []
        for i, l in enumerate(links):
            rect = l["pdf_rect"]
            toc_entries.append({
                "type": "toc_entry",
                "id": i + 1,
                "reading_order": i + 1,
                "bounding_box": [rect[0], rect[1], rect[2], rect[3]],
                "content": f"Entry {i+1} .................................... {i+1}",
                "confidence": 0.99,
                "toc_level": 1
            })

        matched_indices = set()
        toc_container, next_id = assemble_toc_section(
            toc_entries, links, matched_indices, page_num=5, start_id=100
        )

        self.assertIsNotNone(toc_container)
        self.assertEqual(len(matched_indices), 13)  # All 13 matched
        self.assertEqual(len(toc_container["kids"]), 13)

        for toci in toc_container["kids"]:
            ref = toci["kids"][0]
            link = ref["kids"][0]
            self.assertEqual(link["type"], "link")
            objr = link["kids"][0]
            self.assertEqual(objr["type"], "objr")
            self.assertTrue(objr["annotation"]["runtime_annot_id"].startswith("page_4_annot_"))

        doc.close()

    def test_nested_toc_export_to_pdf(self):
        """
        Verify export of a multi-level nested TOC:
        TOC (level 1)
          └── TOCI (Chapter 1)
               ├── Reference
               └── TOC (level 2)
                    ├── TOCI (Section 1.1) -> Reference
                    └── TOCI (Section 1.2) -> Reference
          └── TOCI (Chapter 2) -> Reference
        """
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page(page_size=(self.page_width, self.page_height))

        sp_allocator = StructParentAllocator()
        page_sp_id = sp_allocator.allocate_page(0)
        page.StructParents = page_sp_id

        toc_entries = [
            {"type": "toc_entry", "id": 1, "reading_order": 1, "bounding_box": [50, 700, 500, 715], "content": "Chapter 1", "toc_level": 1, "confidence": 1.0},
            {"type": "toc_entry", "id": 2, "reading_order": 2, "bounding_box": [70, 680, 500, 695], "content": "Section 1.1", "toc_level": 2, "confidence": 1.0},
            {"type": "toc_entry", "id": 3, "reading_order": 3, "bounding_box": [70, 660, 500, 675], "content": "Section 1.2", "toc_level": 2, "confidence": 1.0},
            {"type": "toc_entry", "id": 4, "reading_order": 4, "bounding_box": [50, 640, 500, 655], "content": "Chapter 2", "toc_level": 1, "confidence": 1.0}
        ]

        matched_indices = set()
        toc_container, next_id = assemble_toc_section(toc_entries, [], matched_indices, page_num=1, start_id=1)

        struct_tree_root, doc_elem = StructTreeBuilder.create_root(pdf)
        def custom_get_tag_fn(el_type, el):
            return AccessibilityMapper.get_tag(el_type, el)

        elements = [toc_container]
        mcid_counter, render_results, annotation_map = PageRenderer.render_page(
            pdf=pdf, page_idx=0, elements=elements, font=fitz.Font("helv"),
            name_to_id={}, get_tag_fn=custom_get_tag_fn, document_type="scanned"
        )
        id_to_mcid = {rr.object_id: rr.mcid for rr in render_results}
        page_mcid_map = {}

        StructTreeBuilder.build_tree(
            pdf, elements, doc_elem, struct_tree_root, page_mcid_map, page,
            custom_get_tag_fn, id_to_mcid, sp_allocator=sp_allocator
        )
        ParentTreeBuilder.build(pdf, struct_tree_root, [page_mcid_map], sp_allocator)

        out_bytes = io.BytesIO()
        pdf.save(out_bytes)
        out_bytes.seek(0)

        with pikepdf.open(out_bytes) as loaded_pdf:
            root_toc = loaded_pdf.Root.StructTreeRoot.K[0].K[0]
            self.assertEqual(str(root_toc.get("/S")), "/TOC")
            self.assertEqual(len(root_toc.K), 2)  # Chapter 1, Chapter 2

            ch1_toci = root_toc.K[0]
            self.assertEqual(str(ch1_toci.get("/S")), "/TOCI")
            self.assertEqual(len(ch1_toci.K), 2)  # Reference + nested sub-TOC

            ch1_ref = ch1_toci.K[0]
            self.assertEqual(str(ch1_ref.get("/S")), "/Reference")

            sub_toc = ch1_toci.K[1]
            self.assertEqual(str(sub_toc.get("/S")), "/TOC")
            self.assertEqual(len(sub_toc.K), 2)  # Section 1.1, Section 1.2

            sec1_toci = sub_toc.K[0]
            self.assertEqual(str(sec1_toci.get("/S")), "/TOCI")
            self.assertEqual(str(sec1_toci.K[0].get("/S")), "/Reference")


if __name__ == "__main__":
    unittest.main()
