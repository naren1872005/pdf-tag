from typing import List, Dict, Any
from models.visual_region import VisualRegion
from models.evidence import Evidence
from graph.document_graph import DocumentGraph

class RegionBuilder:
    """
    Populates structural VisualRegions with Evidence (from Vector/Raster analyzers) 
    and OCR Nodes based on spatial containment.
    Makes ZERO semantic decisions.
    """
    def __init__(self, config=None):
        self.config = config or {}
        
    def _is_inside(self, inner_bbox: List[float], outer_bbox: List[float]) -> bool:
        """Checks if inner_bbox is contained within outer_bbox (or significantly overlaps)."""
        ix0, iy0, ix1, iy1 = inner_bbox
        ox0, oy0, ox1, oy1 = outer_bbox
        
        # Simple overlap check: center of inner must be inside outer
        cx = (ix0 + ix1) / 2
        cy = (iy0 + iy1) / 2
        return (ox0 <= cx <= ox1) and (oy0 <= cy <= oy1)

    def populate(self, regions: List[VisualRegion], graph: DocumentGraph, all_evidence: List[Evidence]):
        """
        Assigns graph nodes and evidence to the appropriate regions.
        """
        # Group nodes by page
        page_nodes: Dict[int, List[Any]] = {}
        for node_id, node_data in graph.node_map.items():
            page = node_data.page
            if page:
                page_nodes.setdefault(page, []).append((node_id, node_data))
                
        for region in regions:
            # 1. Assign OCR Nodes
            nodes_on_page = page_nodes.get(region.page, [])
            for node_id, node_data in nodes_on_page:
                bbox = node_data.geometry.get("bbox")
                if bbox and self._is_inside(bbox, region.bbox):
                    region.nodes.append(node_data)
                    
            # 2. Assign Evidence
            for ev in all_evidence:
                if ev.page == region.page and self._is_inside(ev.bbox, region.bbox):
                    region.evidence.append(ev)
