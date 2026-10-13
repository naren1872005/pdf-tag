# GUI/renderer/render_result.py
from dataclasses import dataclass
from typing import Optional, List, Any

@dataclass
class RenderResult:
    object_id: str
    page: int
    mcid: Any
    bbox: Optional[List[float]] = None
    xref: Optional[int] = None
    render_status: str = "success"
