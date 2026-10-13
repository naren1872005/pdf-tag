from typing import List
from models.evidence import Evidence, EvidenceStatus
from models.visual_object import VisualObject
from models.visual_region import VisualRegion

class FusionEngine:
    """
    Fuses Evidence across all layers (Vector, Raster, Rules, Gemini).
    Produces VisualObjects and tracks explainability.
    """
    def __init__(self, config=None):
        self.config = config or {}

    def _calculate_iou(self, bbox1: List[float], bbox2: List[float]) -> float:
        """Calculates Intersection over Union between two bounding boxes."""
        x_left = max(bbox1[0], bbox2[0])
        y_top = max(bbox1[1], bbox2[1])
        x_right = min(bbox1[2], bbox2[2])
        y_bottom = min(bbox1[3], bbox2[3])

        if x_right < x_left or y_bottom < y_top:
            return 0.0

        intersection_area = (x_right - x_left) * (y_bottom - y_top)
        bb1_area = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
        bb2_area = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
        
        # Avoid division by zero
        if float(bb1_area + bb2_area - intersection_area) <= 0:
            return 0.0
            
        iou = intersection_area / float(bb1_area + bb2_area - intersection_area)
        return iou

    def fuse(self, regions: List[VisualRegion]):
        """
        Merges Evidence into VisualObjects for each region.
        """
        for region in regions:
            # 1. Sort evidence by confidence (highest first)
            sorted_evidence = sorted(region.evidence, key=lambda x: x.confidence, reverse=True)
            
            merged_objects = []
            
            for ev in sorted_evidence:
                # Skip suppressed evidence or structural tables
                if ev.status == EvidenceStatus.SUPPRESSED or ev.object_type == "table":
                    continue
                    
                # 2. Check if this evidence overlaps with an already created VisualObject
                is_merged = False
                for v_obj in merged_objects:
                    if self._calculate_iou(ev.bbox, v_obj.bbox) > 0.5:
                        # Merge into existing (we trust the higher confidence one which created v_obj)
                        v_obj.explanation.append(f"Merged with {ev.source} ({ev.object_type}) confidence={ev.confidence}")
                        is_merged = True
                        break
                        
                if not is_merged:
                    # 3. Create a new VisualObject
                    v_obj = VisualObject(
                        obj_type=ev.object_type,
                        page=ev.page,
                        bbox=ev.bbox,
                        source=ev.source,
                        confidence=ev.confidence
                    )
                    v_obj.explanation.append(f"Created from {ev.source} with confidence={ev.confidence}")
                    merged_objects.append(v_obj)
                    
            region.visual_objects = merged_objects
