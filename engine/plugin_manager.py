from typing import List, Dict, Any
from plugins.base_plugin import BasePlugin

class PluginManager:
    """
    Manages the registration and instantiation of all pipeline plugins.
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.plugins: Dict[str, List[BasePlugin]] = {
            "normalizers": [],
            "geometry": [],
            "visual": [],
            "detectors": [],
            "analyzers": [],
            "consistency": [],
            "qa": [],
            "rules": [],
            "exporters": []
        }
        
        self._load_plugins()
        
    def _load_plugins(self):
        # Dynamically discover plugins in the future.
        # For now, register explicitly.
        from plugins.document_type.document_type_detector import DocumentTypeDetector
        self.register_plugin("normalizers", DocumentTypeDetector)
        
        from plugins.detectors.annotation_detector import AnnotationDetector
        self.register_plugin("detectors", AnnotationDetector)
        
        from plugins.normalizers.heading_candidate_validator import HeadingCandidateValidator
        self.register_plugin("normalizers", HeadingCandidateValidator)
        
        from plugins.normalizers.heading_normalizer import HeadingNormalizer
        self.register_plugin("normalizers", HeadingNormalizer)
        
        from plugins.visual.visual_orchestrator import VisualOrchestrator
        self.register_plugin("visual", VisualOrchestrator)
        
        from plugins.exporters.tagged_pdf_exporter import TaggedPdfExporter
        self.register_plugin("exporters", TaggedPdfExporter)
        
        # Semantic Rules
        try:
            from plugins.rules.artifact_rule import ArtifactRule
            from plugins.rules.heading_rule import HeadingHierarchyRule
            from plugins.rules.reading_order_rule import ReadingOrderRule
            from plugins.rules.form_rule import FormObjectReferenceRule
            from plugins.rules.table_rule import TableCompletenessRule
            from plugins.rules.list_rule import ListStructureRule
            from plugins.rules.empty_node_rule import EmptyNodeRule
            from plugins.rules.bullet_list_rule import BulletListRule
            from plugins.rules.inline_link_rule import InlineLinkRule
            
            self.register_plugin("rules", ArtifactRule)
            self.register_plugin("rules", HeadingHierarchyRule)
            self.register_plugin("rules", ReadingOrderRule)
            self.register_plugin("rules", BulletListRule)
            self.register_plugin("rules", FormObjectReferenceRule)
            self.register_plugin("rules", TableCompletenessRule)
            self.register_plugin("rules", ListStructureRule)
            self.register_plugin("rules", EmptyNodeRule)
            self.register_plugin("rules", InlineLinkRule)
        except ImportError as e:
            print(f"Warning: Could not load some semantic rules: {e}")
        
    def register_plugin(self, category: str, plugin_class):
        if category in self.plugins:
            self.plugins[category].append(plugin_class(self.config))
            
    def get_plugins(self, category: str) -> List[BasePlugin]:
        return self.plugins.get(category, [])
