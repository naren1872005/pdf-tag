# GUI/renderer/parent_tree_builder.py
import pikepdf
from pikepdf import Name, Array, Dictionary

class ParentTreeBuilder:
    
    @staticmethod
    def build(pdf, struct_tree_root, all_page_mcid_maps, sp_allocator=None):
        parent_tree_nums = []
        
        for page_idx, page_mcid_map in enumerate(all_page_mcid_maps):
            if not page_mcid_map:
                continue
            
            # The PDF/UA spec requires the ParentTree array index to strictly equal the MCID.
            # To account for sparse or missing MCIDs, we must pad the array with Null objects.
            max_mcid = max(page_mcid_map.keys()) if page_mcid_map else -1
            
            elems_array = pikepdf.Array()
            for i in range(max_mcid + 1):
                struct_elem = page_mcid_map.get(i)
                if struct_elem is not None:
                    elems_array.append(struct_elem)
                else:
                    elems_array.append(None)
            
            # Use the pre-allocated StructParent id for this page
            page_sp_id = pdf.pages[page_idx].get("/StructParents", page_idx)
            if isinstance(page_sp_id, pikepdf.Object):
                page_sp_id = int(page_sp_id)
                
            parent_tree_nums.extend([page_sp_id, elems_array])
                
        if hasattr(pdf, "_annot_struct_map"):
            print(f"[ParentTreeBuilder] Found _annot_struct_map with {len(pdf._annot_struct_map)} items")
            for sp_id, elem_dict in pdf._annot_struct_map.items():
                parent_tree_nums.extend([sp_id, elem_dict])
        else:
            print("[ParentTreeBuilder] _annot_struct_map not found on pdf!")
                
        if len(parent_tree_nums) > 0:
            # The PDF/UA spec requires that Nums array keys must be strictly sorted in ascending order.
            pairs = []
            for i in range(0, len(parent_tree_nums), 2):
                pairs.append((parent_tree_nums[i], parent_tree_nums[i+1]))
            pairs.sort(key=lambda x: x[0])
            
            sorted_nums = []
            for p in pairs:
                sorted_nums.extend([p[0], p[1]])
                
            parent_tree = pdf.make_indirect(Dictionary(Nums=sorted_nums))
            struct_tree_root.ParentTree = parent_tree
            if sp_allocator is not None:
                struct_tree_root.ParentTreeNextKey = sp_allocator.get_max_id() + 1

