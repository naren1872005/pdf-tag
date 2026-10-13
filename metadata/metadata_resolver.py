# GUI_V1/metadata/metadata_resolver.py
from typing import Dict, Any, Optional
from metadata.metadata_model import DocumentMetadata
from metadata.language_resolver import LanguageResolver

class MetadataResolver:
    """
    Resolves the full DocumentMetadata object by inspecting document data,
    configuration, and explicit project metadata.
    Does NOT depend on pikepdf.
    """
    
    @staticmethod
    def resolve(
        document_data: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        project_metadata: Optional[Dict[str, Any]] = None
    ) -> DocumentMetadata:
        
        document_data = document_data or {}
        config = config or {}
        project_metadata = project_metadata or {}
        
        meta = DocumentMetadata()
        
        # 1. Title Resolution
        title_val = project_metadata.get("title")
        if title_val and str(title_val).strip():
            meta.title = str(title_val).strip()
            meta.title_source = "user/project"
        else:
            h1 = MetadataResolver._find_first_h1(document_data)
            if h1:
                meta.title = h1
                meta.title_source = "semantic_h1"

        # 2. Language Resolution
        doc_lang = project_metadata.get("language")
        client_lang = config.get("language")
        app_lang = config.get("pdfua", {}).get("language")
        
        resolved_lang, lang_source = LanguageResolver.resolve(
            document_config=doc_lang,
            client_config=client_lang,
            application_config=app_lang
        )
        meta.language = resolved_lang
        meta.language_source = lang_source
        
        # 3. Optional Metadata (Author, Subject, Keywords)
        for field in ["author", "subject", "keywords"]:
            val = project_metadata.get(field)
            if val and str(val).strip():
                setattr(meta, field, str(val).strip())
                setattr(meta, f"{field}_source", "user/project")
                
        # 4. PDF/UA Configuration
        pdfua_config = config.get("pdfua", {})
        
        part_val = pdfua_config.get("part")
        if part_val is not None:
            meta.pdfua_part = int(part_val)
            meta.pdfua_part_source = "application_config"
        
        display = pdfua_config.get("display_doc_title")
        if display is not None:
            meta.display_doc_title = bool(display)
            meta.display_doc_title_source = "application_config"
            
        return meta

    @staticmethod
    def _find_first_h1(document_data: Dict[str, Any]) -> Optional[str]:
        """
        Extracts the first H1 from the semantic JSON structure.
        """
        pages = document_data.get("pages", [])
        for page in pages:
            elements = page.get("elements", [])
            for el in elements:
                res = MetadataResolver._search_h1_recursive(el)
                if res:
                    return res
        return None
        
    @staticmethod
    def _search_h1_recursive(element: Dict[str, Any]) -> Optional[str]:
        t = element.get("type", "")
        # Try finding level 1 heading
        is_h1 = (
            t in ("h1", "heading_1") or 
            (t == "heading" and (
                element.get("level") == 1 or 
                element.get("heading_level") == 1 or 
                element.get("metadata", {}).get("level") == 1
            ))
        )
        if is_h1:
            content = element.get("content", "").strip()
            if content:
                return content
            
        kids = element.get("kids", [])
        for kid in kids:
            res = MetadataResolver._search_h1_recursive(kid)
            if res:
                return res
        return None
