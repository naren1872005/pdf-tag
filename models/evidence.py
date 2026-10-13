import uuid
from typing import List, Dict, Any, Optional
from enum import Enum

class EvidenceStatus(Enum):
    ACTIVE = "active"
    SUPPRESSED = "suppressed"
    CONFIRMED = "confirmed"

class Evidence:
    """
    Unified intermediate object across the Visual Analysis pipeline.
    Replaces raw outputs from Vector, Raster, Rules, and Gemini.
    """
    def __init__(self, 
                 source: str, 
                 object_type: str, 
                 bbox: List[float], 
                 page: int,
                 confidence: float = 1.0, 
                 supporting_data: Optional[Dict[str, Any]] = None, 
                 metadata: Optional[Dict[str, Any]] = None):
        self.id = str(uuid.uuid4())
        self.source = source # e.g., 'vector', 'raster_checkbox', 'rule_checkbox', 'gemini'
        self.object_type = object_type # e.g., 'rectangle', 'horizontal_line', 'checkbox', 'underline_field'
        self.bbox = bbox # [x0, y0, x1, y1]
        self.page = page
        self.confidence = confidence
        self.supporting_data = supporting_data or {}
        self.metadata = metadata or {}
        self.status = EvidenceStatus.ACTIVE
        self.history: List[str] = [f"Created by {source}"] # Trace
        
    def to_dict(self):
        return {
            "id": self.id,
            "source": self.source,
            "object_type": self.object_type,
            "bbox": self.bbox,
            "page": self.page,
            "confidence": self.confidence,
            "status": self.status.value,
            "history": self.history,
            "supporting_data": self.supporting_data,
            "metadata": self.metadata
        }
