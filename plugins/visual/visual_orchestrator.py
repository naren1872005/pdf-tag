from plugins.base_plugin import BasePlugin
from engine.document_context import DocumentContext
from plugins.visual.registry import VisualAnalyzerRegistry
from plugins.visual.layout_segmenter import LayoutSegmenter
from plugins.visual.region_builder import RegionBuilder
from plugins.visual.rule_engine import RuleEngine
from plugins.visual.confidence_engine import ConfidenceEngine
from plugins.visual.decision_engine import DecisionEngine
from plugins.visual.gemini_region_analyzer import GeminiRegionAnalyzer
from plugins.visual.fusion_engine import FusionEngine
from plugins.visual.graph_updater import GraphUpdater
from typing import List
from models.evidence import Evidence

class VisualOrchestrator(BasePlugin):
    """
    Self-contained micro-pipeline for Visual Analysis.
    Progressive Escalation Architecture:
    Layout -> Regions -> Analyzers -> Rules -> Confidence -> Decision -> Gemini -> Fusion -> GraphUpdater
    """
    def __init__(self, config=None):
        self.config = config or {}
        if not self.config.get("rules"):
            try:
                import yaml
                import os
                config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "rules.yaml")
                with open(config_path, "r", encoding="utf-8") as f:
                    self.config["rules"] = yaml.safe_load(f)
            except Exception as e:
                print(f"Warning: Could not load rules.yaml - {e}")
                self.config["rules"] = {}
                
        super().__init__(self.config)
        self.registry = VisualAnalyzerRegistry(self.config)
        self.layout_segmenter = LayoutSegmenter(self.config)
        self.region_builder = RegionBuilder(self.config)
        self.rule_engine = RuleEngine(self.config)
        self.confidence_engine = ConfidenceEngine(self.config)
        self.decision_engine = DecisionEngine(self.config)
        self.gemini_analyzer = GeminiRegionAnalyzer(self.config)
        self.fusion = FusionEngine(self.config)
        self.updater = GraphUpdater(self.config)
        
    def run(self, context: DocumentContext, data=None):
        pdf_path = context.metadata.get("pdf_path")
        
        all_evidence: List[Evidence] = []
        
        # 1. Execute all registered analyzers (Vector, Raster) to generate base Evidence
        for analyzer in self.registry.get_all():
            results = analyzer.analyze(context.graph, pdf_path)
            for res in results:
                # Convert legacy Observation to Evidence if needed
                if hasattr(res, 'type') and not hasattr(res, 'object_type'):
                    ev = Evidence(
                        source=res.source,
                        object_type=res.type,
                        bbox=res.bbox,
                        page=res.page,
                        confidence=res.confidence
                    )
                    if hasattr(res, 'id'):
                        ev.id = res.id
                    all_evidence.append(ev)
                else:
                    all_evidence.append(res)
            
        # 2. Layout Segmentation -> Regions
        regions = self.layout_segmenter.segment(pdf_path)
        
        # 3. Region Builder (Populate with OCR nodes and Analyzer Evidence)
        self.region_builder.populate(regions, context.graph, all_evidence)
        
        # 4. Rule Engine (Fast Path Heuristics) -> generates more Evidence inside region
        self.rule_engine.evaluate(regions)
        
        # 5. Confidence Engine -> Aggregates confidence
        self.confidence_engine.calculate(regions)
        
        # 6. Decision Engine & Gemini (Escalation Path)
        for region in regions:
            if self.decision_engine.needs_escalation(region):
                gemini_evidence = self.gemini_analyzer.analyze(region, pdf_path)
                region.evidence.extend(gemini_evidence)
                
        # 7. Fusion Engine -> Fuses Evidence into VisualObjects per region
        self.fusion.fuse(regions)
        
        # 8. GraphUpdater -> Deterministic Graph Relationships
        self.updater.apply(context.graph, regions)
