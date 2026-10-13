from typing import List, Dict, Any
from dataclasses import dataclass, field
import uuid

@dataclass
class Structure:
    """
    Represents grouped geometric observations (lines, rectangles).
    Produced by the StructureDetector.
    Does NOT contain semantic interpretation (e.g. 'table', 'input_box').
    Instead, describes the geometric features found.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    page: int = 1
    bbox: List[float] = field(default_factory=list)
    member_observation_ids: List[str] = field(default_factory=list)
    
    # Geometric features extracted by the StructureDetector
    features: Dict[str, Any] = field(default_factory=lambda: {
        "horizontal_lines": 0,
        "vertical_lines": 0,
        "intersections": 0,
        "closed": False,
        "connected_components": 0
    })
    
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
