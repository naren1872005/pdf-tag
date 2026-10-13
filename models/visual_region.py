import uuid
from typing import List, Dict, Any, Optional

class VisualRegion:
    """
    Represents a structural region on a document page (e.g., a layout block).
    Populated with Evidence and Nodes during the pipeline.
    """
    def __init__(self, page: int, bbox: List[float]):
        self.id = str(uuid.uuid4())
        self.page = page
        self.bbox = bbox # [x0, y0, x1, y1]
        
        # Profile contains region_type, complexity, flags, etc.
        self.profile: Dict[str, Any] = {
            "complexity": "unknown"
        }
        
        # Confidence metrics from different stages
        self.confidence: Dict[str, float] = {
            "overall": 0.0
        }
        
        # Content populated by RegionBuilder
        self.nodes: List[Any] = [] # Graph Nodes
        self.evidence: List[Any] = [] # Evidence objects
        
        # Final visual objects produced by Fusion Engine
        self.visual_objects: List[Any] = []
        
        self.metadata: Dict[str, Any] = {}
        
    def to_dict(self):
        return {
            "id": self.id,
            "page": self.page,
            "bbox": self.bbox,
            "profile": self.profile,
            "confidence": self.confidence,
            "nodes_count": len(self.nodes),
            "evidence_count": len(self.evidence),
            "visual_objects_count": len(self.visual_objects),
            "metadata": self.metadata
        }
