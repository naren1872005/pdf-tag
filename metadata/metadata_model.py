# GUI_V1/metadata/metadata_model.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class DocumentMetadata:
    """
    Canonical representation of document-level metadata for PDF/UA generation.
    Tracks both the resolved values and their sources for auditing purposes.
    """
    
    # Required core fields
    title: Optional[str] = None
    title_source: Optional[str] = None
    
    language: Optional[str] = None
    language_source: Optional[str] = None
    
    pdfua_part: Optional[int] = None
    pdfua_part_source: Optional[str] = None
    
    display_doc_title: Optional[bool] = None
    display_doc_title_source: Optional[str] = None
    
    # Optional metadata fields
    author: Optional[str] = None
    author_source: Optional[str] = None
    
    subject: Optional[str] = None
    subject_source: Optional[str] = None
    
    keywords: Optional[str] = None
    keywords_source: Optional[str] = None
    
    creator: Optional[str] = None
    creator_source: Optional[str] = None
    
    producer: Optional[str] = None
    producer_source: Optional[str] = None
