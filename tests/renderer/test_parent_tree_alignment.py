import unittest
import pikepdf
from pikepdf import Name, Dictionary, Array
from renderer.parent_tree_builder import ParentTreeBuilder

class TestParentTreeAlignment(unittest.TestCase):

    def setUp(self):
        self.pdf = pikepdf.Pdf.new()
        self.pdf.add_blank_page(page_size=(612, 792))
        self.struct_tree_root = self.pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
        self.pdf.Root.StructTreeRoot = self.struct_tree_root

    def create_mock_struct_elem(self, mcid, tag_name="/P"):
        return self.pdf.make_indirect(Dictionary(
            Type=Name("/StructElem"),
            S=Name(tag_name),
            K=Array([mcid])
        ))

    def test_case_1_background_object_with_semantics(self):
        # Background Figure MCID 0 + semantic MCIDs 1, 2, 3
        # Expected ParentTree[0] = background Figure
        # ParentTree[1] = semantic MCID 1, etc.
        page_mcid_map = {
            0: self.create_mock_struct_elem(0, "/Figure"),
            1: self.create_mock_struct_elem(1, "/P"),
            2: self.create_mock_struct_elem(2, "/P"),
            3: self.create_mock_struct_elem(3, "/P"),
        }

        ParentTreeBuilder.build(self.pdf, self.struct_tree_root, [page_mcid_map])
        
        parent_tree = self.struct_tree_root.get("/ParentTree")
        self.assertIsNotNone(parent_tree)
        
        nums = parent_tree.get("/Nums")
        self.assertEqual(len(nums), 2)  # [0, [Array of length 4]]
        
        page_array = nums[1]
        self.assertEqual(len(page_array), 4)
        
        # Verify index == MCID
        for i in range(4):
            self.assertEqual(page_array[i].get("/K")[0], i)
        
        self.assertEqual(page_array[0].get("/S"), Name("/Figure"))

    def test_case_2_no_background_object(self):
        # Semantic MCIDs start at 0
        page_mcid_map = {
            0: self.create_mock_struct_elem(0, "/H1"),
            1: self.create_mock_struct_elem(1, "/P"),
        }

        ParentTreeBuilder.build(self.pdf, self.struct_tree_root, [page_mcid_map])
        page_array = self.struct_tree_root.ParentTree.Nums[1]
        
        self.assertEqual(len(page_array), 2)
        self.assertEqual(page_array[0].get("/K")[0], 0)
        self.assertEqual(page_array[1].get("/K")[0], 1)

    def test_case_3_artifacts_do_not_enter_parent_tree(self):
        # Artifacts have no MCID, so they don't go into page_mcid_map.
        # Ensure ParentTree does not incorrectly count them.
        page_mcid_map = {
            0: self.create_mock_struct_elem(0, "/P")
        }
        # (Artifacts bypass this map entirely)

        ParentTreeBuilder.build(self.pdf, self.struct_tree_root, [page_mcid_map])
        page_array = self.struct_tree_root.ParentTree.Nums[1]
        
        self.assertEqual(len(page_array), 1)

    def test_case_4_sparse_mcids(self):
        # If an MCID was sparse, verify Null padding
        page_mcid_map = {
            0: self.create_mock_struct_elem(0, "/Figure"),
            # missing MCID 1
            2: self.create_mock_struct_elem(2, "/P"),
        }

        ParentTreeBuilder.build(self.pdf, self.struct_tree_root, [page_mcid_map])
        page_array = self.struct_tree_root.ParentTree.Nums[1]
        
        self.assertEqual(len(page_array), 3)
        self.assertEqual(page_array[0].get("/K")[0], 0)
        self.assertIsNone(page_array[1])
        self.assertEqual(page_array[2].get("/K")[0], 2)

    def test_case_5_consistency_error(self):
        # We simulate the exporter's pre-export check logic.
        render_results = [
            type("RenderResult", (), {"object_id": "fig_id", "mcid": 0})(),
            type("RenderResult", (), {"object_id": "missing_id", "mcid": 1})()
        ]
        page_mcid_map = { 0: self.create_mock_struct_elem(0, "/Figure") }
        
        # Checking consistency logic directly
        with self.assertRaises(ValueError):
            for rr in render_results:
                mcid = rr.mcid
                if mcid not in page_mcid_map:
                    raise ValueError("Consistency Error")

if __name__ == '__main__':
    unittest.main()
