# GUI_V1/metadata/metadata_validator.py
import re
from typing import List, Tuple
from metadata.metadata_model import DocumentMetadata

class MetadataValidator:
    """
    Validates DocumentMetadata before writing it to a PDF.
    Ensures all strict PDF/UA requirements are met.
    """
    
    @staticmethod
    def validate(metadata: DocumentMetadata) -> Tuple[bool, List[str]]:
        errors = []
        
        # Title validation
        if not metadata.title or not str(metadata.title).strip():
            errors.append("Title is missing or empty.")
            
        # Language validation
        if not metadata.language or not str(metadata.language).strip():
            errors.append("Language is missing.")
        else:
            # Basic check to ensure it looks like a valid BCP 47 tag (e.g. en, en-US)
            # LanguageResolver should have already normalized this.
            if not re.match(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})?$", metadata.language):
                errors.append(f"Language '{metadata.language}' is not a valid BCP 47 tag.")
                
        # PDF/UA Part validation
        if metadata.pdfua_part is None:
            errors.append("PDF/UA part is missing.")
        elif metadata.pdfua_part != 1:
            errors.append(f"PDF/UA part {metadata.pdfua_part} is not supported. Only part 1 is supported.")
            
        # DisplayDocTitle validation
        if metadata.display_doc_title is not True:
            errors.append("DisplayDocTitle must be explicitly True.")
            
        return len(errors) == 0, errors
