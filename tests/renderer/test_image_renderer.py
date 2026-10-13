import unittest
import pikepdf
from pikepdf import Operator
from renderer.image_renderer import ImageRenderer

class TestImageRenderer(unittest.TestCase):

    def test_case_1_background_image_no_mcid(self):
        # Case 1: Background image /X0 -> Artifact, no MCID
        instructions = [
            pikepdf.ContentStreamInstruction(pikepdf.Array([pikepdf.Name("/X0")]), Operator("Do"))
        ]
        image_names = ["/X0"]
        name_to_id = {} # Image NOT in semantic graph
        
        out, next_mcid, render_results = ImageRenderer.wrap_original_content(
            instructions, image_names, page_num=1, name_to_id=name_to_id, mcid_start=0
        )
        
        # mcid should not increment
        self.assertEqual(next_mcid, 0)
        # no render results
        self.assertEqual(len(render_results), 0)
        
        # check output does not contain BDC with Figure
        figure_tags = [inst for inst in out if inst.operator == Operator("BDC") and len(inst.operands) > 0 and inst.operands[0] == pikepdf.Name("/Figure")]
        self.assertEqual(len(figure_tags), 0)

    def test_case_2_semantic_figure(self):
        # Case 2: Semantic Figure -> Figure, MCID, StructElem, ParentTree entry
        instructions = [
            pikepdf.ContentStreamInstruction(pikepdf.Array([pikepdf.Name("/X0")]), Operator("Do"))
        ]
        image_names = ["/X0"]
        name_to_id = {"/X0": "sem_123"}
        
        out, next_mcid, render_results = ImageRenderer.wrap_original_content(
            instructions, image_names, page_num=1, name_to_id=name_to_id, mcid_start=0
        )
        
        self.assertEqual(next_mcid, 1)
        self.assertEqual(len(render_results), 1)
        self.assertEqual(render_results[0].mcid, 0)
        
        figure_tags = [inst for inst in out if inst.operator == Operator("BDC") and len(inst.operands) > 0 and inst.operands[0] == pikepdf.Name("/Figure")]
        self.assertEqual(len(figure_tags), 1)

    def test_case_3_background_plus_semantic(self):
        # Case 3: Background image + semantic content
        instructions = [
            pikepdf.ContentStreamInstruction(pikepdf.Array([pikepdf.Name("/X0")]), Operator("Do")),
            pikepdf.ContentStreamInstruction(pikepdf.Array([pikepdf.Name("/X1")]), Operator("Do"))
        ]
        image_names = ["/X0", "/X1"]
        name_to_id = {"/X1": "sem_124"} # Only X1 is semantic
        
        out, next_mcid, render_results = ImageRenderer.wrap_original_content(
            instructions, image_names, page_num=1, name_to_id=name_to_id, mcid_start=0
        )
        
        self.assertEqual(next_mcid, 1)
        self.assertEqual(len(render_results), 1)
        self.assertEqual(render_results[0].object_id, "sem_124")
        self.assertEqual(render_results[0].mcid, 0) # X1 gets the first MCID

    def test_case_4_unknown_image_not_full_page(self):
        # Case 4: Unknown image that is NOT full-page background
        # It's treated exactly like Case 1 because it's not in name_to_id
        instructions = [
            pikepdf.ContentStreamInstruction(pikepdf.Array([pikepdf.Name("/X2")]), Operator("Do"))
        ]
        image_names = ["/X2"]
        name_to_id = {}
        
        out, next_mcid, render_results = ImageRenderer.wrap_original_content(
            instructions, image_names, page_num=1, name_to_id=name_to_id, mcid_start=0
        )
        
        self.assertEqual(next_mcid, 0)
        self.assertEqual(len(render_results), 0)

if __name__ == '__main__':
    unittest.main()
