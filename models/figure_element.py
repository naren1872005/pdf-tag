from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class FigureExtractedContent(BaseModel):
    """
    Pydantic model validating figure elements and their extracted semantic content.
    Supports both internal canonical field names and project-standard aliases.
    """
    model_config = ConfigDict(populate_by_name=True)

    source_page_number: int = Field(..., alias="page_number", description="Source page number (1-indexed)")
    figure_bounding_box: List[float] = Field(..., alias="bounding_box", description="Bounding box [x0, y0, x1, y1] in PDF space")
    extracted_text: str = Field(default="", alias="content", description="Extracted text content")
    semantic_element_type: str = Field(default="figure", alias="type", description="Semantic role: figure, heading, paragraph, list_item, table, formula")
    proposed_accessibility_tag: str = Field(default="Figure", alias="tag", description="PDF/UA structure tag: Figure, H1..H6, P, L, LI, Table")
    reading_order_position: Optional[int] = Field(default=None, alias="reading_order", description="1-indexed reading order position")
    confidence_score: float = Field(default=0.95, alias="confidence", description="Detection/OCR confidence score (0.0 to 1.0)")
    relationships_to_surrounding: Dict[str, Any] = Field(default_factory=dict, alias="relationships", description="Preceding/succeeding node relations, column index, caption link")
    alt_text: Optional[str] = Field(default=None, description="Meaningful alternative description for figures")
    heading_level: Optional[int] = Field(default=None, description="Heading level (1-6) when element is a heading")
    font_size: Optional[float] = Field(default=None, description="Estimated font size")
    id: Optional[Any] = Field(default=None, description="Unique element identifier")
    xref: Optional[int] = Field(default=None, description="PyMuPDF image object xref")
    kids: Optional[List[Dict[str, Any]]] = Field(default=None, description="Child elements (e.g. for tables, lists)")

