import uuid
from typing import Dict, Any, List, Optional

class VisualObject:
    """
    Standardized model for all visual primitives detected by any Analyzer (Vector, Raster, ML).
    """
    def __init__(self, obj_type: str, page: int, bbox: List[float], source: str, confidence: float = 1.0):
        self.id = str(uuid.uuid4())
        self.type = obj_type
        self.page = page
        self.bbox = bbox
        self.confidence = confidence
        self.source = source # 'vector', 'raster', 'ml', 'ocr', 'heuristic', 'manual'
        
        self.state: Optional[str] = None
        self.geometry: Dict[str, Any] = {}
        self.metadata: Dict[str, Any] = {}
        self.relationships: Dict[str, Any] = {} # e.g. parent/child links for widgets
        self.owner_node_uuid: Optional[str] = None
        self.explanation: List[str] = [] # Trace of how this object was derived

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "page": self.page,
            "bbox": self.bbox,
            "confidence": self.confidence,
            "source": self.source,
            "state": self.state,
            "geometry": self.geometry,
            "metadata": self.metadata,
            "relationships": self.relationships,
            "owner_node_uuid": self.owner_node_uuid,
            "explanation": self.explanation
        }
