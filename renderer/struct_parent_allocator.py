class StructParentAllocator:
    """
    Manages the global StructParent namespace for PDF/UA compliance.
    PDF/UA requires that the /ParentTree contains entries for all pages and annotations.
    """
    def __init__(self):
        self._current_id = 0
        self._allocations = {}
        self._annot_allocations = {}

    def allocate_page(self, page_index: int) -> int:
        """Allocate a StructParent ID for a page."""
        sp_id = self._current_id
        self._allocations[f"page_{page_index}"] = sp_id
        self._current_id += 1
        return sp_id

    def allocate_annotation(self, runtime_annot_id: str) -> int:
        """Allocate a StructParent ID for an annotation."""
        if runtime_annot_id in self._annot_allocations:
            return self._annot_allocations[runtime_annot_id]
            
        sp_id = self._current_id
        self._annot_allocations[runtime_annot_id] = sp_id
        self._current_id += 1
        return sp_id

    def get_max_id(self) -> int:
        return self._current_id - 1
