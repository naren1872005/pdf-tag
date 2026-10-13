from typing import Dict, Any
import fitz
from plugins.base_plugin import BasePlugin
from engine.document_context import DocumentContext

class DocumentTypeDetector(BasePlugin):
    """
    Stage 1: Analyzes the PDF to determine its type (Digital, Scanned, Hybrid)
    This drives the execution strategy for the rest of the pipeline.
    """
    def run(self, context: DocumentContext, data=None):
        pdf_path = context.metadata.get("pdf_path")
        if not pdf_path:
            return

        try:
            doc = fitz.open(pdf_path)
            
            # Simple heuristic implementation
            total_text_length = 0
            total_drawings = 0
            total_images = 0
            
            for page in doc:
                total_text_length += len(page.get_text())
                total_drawings += len(page.get_drawings())
                total_images += len(page.get_images())
                
            doc.close()
            
            if total_text_length > 100 and total_drawings > 0:
                doc_type = "hybrid"
            elif total_text_length > 100 and total_drawings == 0 and total_images == 0:
                doc_type = "digital"
            elif total_text_length < 50 and total_images > 0:
                doc_type = "scanned"
            else:
                doc_type = "scanned" # Safe fallback
                
            context.metadata["document_type"] = doc_type
            context.metadata["analysis_strategy"] = self._get_strategy(doc_type)
            
        except Exception as e:
            print(f"DocumentTypeDetector Error: {e}")
            context.metadata["document_type"] = "unknown"
            
    def _get_strategy(self, doc_type: str) -> str:
        strategies = {
            "digital": "vector_first",
            "scanned": "raster_first",
            "hybrid": "run_both"
        }
        return strategies.get(doc_type, "run_both")
