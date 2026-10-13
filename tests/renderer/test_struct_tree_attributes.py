import unittest
import pikepdf
from pikepdf import Name, Array, Dictionary
from renderer.struct_tree_builder import StructTreeBuilder

class TestStructTreeAttributes(unittest.TestCase):

    def setUp(self):
        self.pdf = pikepdf.Pdf.new()
        # Mock a page with MediaBox
        self.page = self.pdf.add_blank_page(page_size=(612, 792))
        
        self.struct_tree_root, self.doc_elem = StructTreeBuilder.create_root(self.pdf)
        
        # We need a mock current_page object that has an 'obj' attribute representing the underlying PDF object
        self.current_page = type("MockPage", (), {"obj": self.page.obj})()

    def test_placement_and_attributes(self):
        # We define a semantic element list
        # Document (doc_elem) will have block-level Figure and Note
        # Also an inline Figure and Note (under a P tag)
        elements = [
            {
                "id": 48,
                "type": "footnote_note",
                "content": "* Where Applicable"
            },
            {
                "id": 35,
                "type": "figure",
                "bounding_box": [100.0, 200.0, 300.0, 400.0]
            },
            {
                "id": 99,
                "type": "paragraph",
                "kids": [
                    {
                        "id": 100,
                        "type": "figure",
                        "bounding_box": [50.0, 50.0, 150.0, 150.0]
                    },
                    {
                        "id": 101,
                        "type": "footnote_note",
                        "content": "Inline Note"
                    }
                ]
            }
        ]
        
        # Basic get_tag_fn for the test
        def get_tag_fn(el_type, el):
            if el_type == "footnote_note": return "Note"
            if el_type == "figure": return "Figure"
            if el_type == "paragraph": return "P"
            return "P"
            
        page_mcid_map = {}
        id_to_mcid = {"48": 0, "35": 1, "99": 2, "100": 3, "101": 4}
        
        StructTreeBuilder.build_tree(
            self.pdf, elements, self.doc_elem, self.struct_tree_root,
            page_mcid_map, self.current_page, get_tag_fn, id_to_mcid
        )
        
        # doc_elem (S=/Document) contains Note, Figure, P
        self.assertEqual(len(self.doc_elem.K), 3)
        
        block_note = self.doc_elem.K[0]
        block_figure = self.doc_elem.K[1]
        p_elem = self.doc_elem.K[2]
        
        # Verify Block Note
        self.assertEqual(block_note.get("/S"), Name("/Note"))
        self.assertEqual(str(block_note.get("/ID")), "node_48")
        self.assertIn("/A", block_note)
        self.assertEqual(block_note.get("/A").get("/O"), Name("/Layout"))
        self.assertEqual(block_note.get("/A").get("/Placement"), Name("/Block"))
        
        # Verify Block Figure
        self.assertEqual(block_figure.get("/S"), Name("/Figure"))
        self.assertIn("/A", block_figure)
        block_fig_attr = block_figure.get("/A")
        self.assertEqual(block_fig_attr.get("/O"), Name("/Layout"))
        self.assertEqual(block_fig_attr.get("/Placement"), Name("/Block"))
        self.assertIn("/BBox", block_fig_attr)
        
        # Verify Inline Elements inside P
        inline_figure = p_elem.K[0]
        inline_note = p_elem.K[1]
        
        self.assertEqual(inline_figure.get("/S"), Name("/Figure"))
        self.assertIn("/A", inline_figure)
        inline_fig_attr = inline_figure.get("/A")
        self.assertEqual(inline_fig_attr.get("/O"), Name("/Layout"))
        self.assertNotIn("/Placement", inline_fig_attr) # No Placement for inline
        self.assertIn("/BBox", inline_fig_attr)
        
        self.assertEqual(inline_note.get("/S"), Name("/Note"))
        self.assertEqual(str(inline_note.get("/ID")), "node_101")
        self.assertNotIn("/A", inline_note) # No A dict created because no Placement
        
        # Serialize to test survival
        self.pdf.save("test_attributes_serialized.pdf")
        
        # Open and verify after serialization
        with pikepdf.Pdf.open("test_attributes_serialized.pdf") as loaded_pdf:
            loaded_doc_elem = loaded_pdf.Root.StructTreeRoot.K[0]
            
            loaded_block_note = loaded_doc_elem.K[0]
            self.assertEqual(loaded_block_note.get("/S"), Name("/Note"))
            self.assertEqual(str(loaded_block_note.get("/ID")), "node_48")
            self.assertEqual(loaded_block_note.get("/A").get("/Placement"), Name("/Block"))
            
            loaded_block_figure = loaded_doc_elem.K[1]
            self.assertEqual(loaded_block_figure.get("/S"), Name("/Figure"))
            loaded_attr = loaded_block_figure.get("/A")
            self.assertEqual(loaded_attr.get("/Placement"), Name("/Block"))
            loaded_bbox = loaded_attr.get("/BBox")
            self.assertEqual(float(loaded_bbox[1]), 200.0)
            
            loaded_p = loaded_doc_elem.K[2]
            loaded_inline_figure = loaded_p.K[0]
            self.assertNotIn("/Placement", loaded_inline_figure.get("/A"))
            self.assertIn("/BBox", loaded_inline_figure.get("/A"))

    def test_note_classification(self):
        # Setup context-aware get_tag_fn logic
        doc_json_unref = {
            "pages": [{"elements": [{"type": "footnote_note", "id": 1}]}]
        }
        
        doc_json_ref = {
            "pages": [{"elements": [{"type": "footnote_note", "id": 1}, {"type": "footnote_reference", "target_id": 1}]}]
        }
        
        def make_custom_get_tag_fn(doc_json):
            has_any_reference = False
            referenced_notes = set()
            def find_references(els):
                nonlocal has_any_reference
                for el in els:
                    if el.get("type") in ["footnote_reference", "hyperlink"]:
                        has_any_reference = True
                        target = el.get("target_id")
                        if target is not None:
                            referenced_notes.add(target)
                    if el.get("kids"):
                        find_references(el["kids"])
            
            for p in doc_json.get("pages", []):
                find_references(p.get("elements", []))
                
            def custom_get_tag_fn(el_type, el):
                if el_type == "footnote_note":
                    if el.get("id") in referenced_notes:
                        return "Note"
                    if has_any_reference and not referenced_notes:
                        return "Note"
                    return "P"
                return "P"
            return custom_get_tag_fn
            
        unref_fn = make_custom_get_tag_fn(doc_json_unref)
        self.assertEqual(unref_fn("footnote_note", {"id": 1}), "P")
        
        ref_fn = make_custom_get_tag_fn(doc_json_ref)
        self.assertEqual(ref_fn("footnote_note", {"id": 1}), "Note")

if __name__ == '__main__':
    unittest.main()
