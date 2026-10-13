import os
import time
import json
from pathlib import Path
from typing import Dict, Any
from engine.plugin_manager import PluginManager
from engine.document_context import DocumentContext
from plugins.rules.action_queue import ActionQueue

class PipelineManager:
    """
    Orchestrates the 10-Stage Multimodal Document AI Pipeline.
    Enforces strict Single Responsibility across Geometry, Visual, Semantics, and Consistency.
    """
    def __init__(self, workspace_path: str, config: Dict[str, Any]):
        self.workspace = Path(workspace_path)
        self.config = config
        self.plugin_manager = PluginManager(config)
        
        self.metadata = {
            "started": time.time(),
            "finished": None,
            "processing_time": {},
            "warnings": [],
            "pages": 0,
            "elements": 0,
            "workspace_path": str(self.workspace)
        }
        
        # Setup workspace layout
        for folder in ["input", "semantic", "graph", "output", "logs", "config"]:
            (self.workspace / folder).mkdir(parents=True, exist_ok=True)
            
    def _time_stage(self, stage_name: str, func):
        start = time.perf_counter()
        result = func()
        elapsed = time.perf_counter() - start
        self.metadata["processing_time"][stage_name] = f"{elapsed:.3f} s"
        return result

    def _save_stage_json(self, folder: str, filename: str, data: Any):
        if not self.config.get("save_intermediate", True):
            return
        filepath = self.workspace / folder / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def run(self, raw_json_path: str):
        with open(raw_json_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            
        if isinstance(raw_data, list):
            pages_dict = {}
            for el in raw_data:
                page_num = el.get("page_number") or el.get("metadata", {}).get("page") or el.get("semantic", {}).get("page_number", 1)
                pages_dict.setdefault(page_num, []).append(el)
            raw_data = {"pages": [{"page_number": p, "elements": elements} for p, elements in pages_dict.items()]}
        doc_context = DocumentContext()
        doc_context.metadata = self.metadata
        
        # Load cached profile if exists
        profile_path = self.workspace / "profile.json"
        if profile_path.exists():
            try:
                with open(profile_path, "r", encoding="utf-8") as f:
                    doc_context.profile = json.load(f)
            except Exception:
                pass
        
        # 1. Normalization (Operates on Raw JSON)
        self._time_stage("Normalization", lambda: self._run_plugins("normalizers", doc_context, raw_data))
        self._save_stage_json("semantic", "normalized_v1.json", raw_data)
        
        # 2. Document Graph Builder
        def build_graph():
            doc_context.graph = doc_context.graph.from_json(raw_data)
        self._time_stage("Graph Build", build_graph)
        
        # All following stages operate ONLY on the Document Graph
        
        # 3. Geometry Analysis
        self._time_stage("Geometry Analysis", lambda: self._run_graph_plugins("geometry", doc_context))
        self._save_stage_json("graph", "geometry_v1.json", doc_context.graph.to_json())
        
        # 4. Visual Analysis
        self._time_stage("Visual Analysis", lambda: self._run_graph_plugins("visual", doc_context))
        self._save_stage_json("graph", "visual_v1.json", doc_context.graph.to_json())
        
        # 5. Semantic Detection (Multimodal)
        self._time_stage("Semantic Detection", lambda: self._run_graph_plugins("detectors", doc_context))
        self._save_stage_json("graph", "detected_v1.json", doc_context.graph.to_json())
        
        # 6. Semantic Analysis (Hierarchies)
        self._time_stage("Semantic Analysis", lambda: self._run_graph_plugins("analyzers", doc_context))
        self._save_stage_json("graph", "analysis_v1.json", doc_context.graph.to_json())
        
        # 7. Consistency Analysis
        self._time_stage("Consistency Analysis", lambda: self._run_graph_plugins("consistency", doc_context))
        self._save_stage_json("graph", "consistency_v1.json", doc_context.graph.to_json())
        
        # 8. QA
        self._time_stage("QA", lambda: self._run_graph_plugins("qa", doc_context))
        self._save_stage_json("graph", "qa_v1.json", doc_context.graph.to_json())
        
        # 9. Rule Engine
        action_queue = ActionQueue()
        def run_rules():
            rules = self.plugin_manager.get_plugins("rules")
            for rule in rules:
                rule.run(doc_context, action_queue)
            action_queue.apply_all(doc_context.graph)
        self._time_stage("Rules", run_rules)
        self._save_stage_json("graph", "rules_v1.json", doc_context.graph.to_json())
        
        # 9.5 Digital Font Mapping (Phase 1)
        doc_type = self.config.get("document_type") or doc_context.profile.get("document_type", "")
        if "DIGITAL" in doc_type.upper() or "HYBRID" in doc_type.upper():
            def run_font_mapping():
                from plugins.digital.native_text_extractor import NativeTextExtractor
                from plugins.digital.font_resource_resolver import FontResourceResolver
                from plugins.digital.font_mapper import FontMapper
                
                input_pdf_path = self.config.get("input_pdf") or str(self.workspace / "input.pdf")
                if input_pdf_path and os.path.exists(input_pdf_path):
                    # We map per page
                    all_maps = []
                    all_reports = []
                    
                    # Group nodes by page
                    nodes_by_page = {}
                    for node in doc_context.graph.node_map.values():
                        p = getattr(node, "page", None) or node.metadata.get("page", 1)
                        nodes_by_page.setdefault(p, []).append(node)
                        
                    resolver = FontResourceResolver()
                    
                    for p, page_nodes in nodes_by_page.items():
                        extracted = NativeTextExtractor.extract_page(input_pdf_path, p)
                        f_map, f_rep = FontMapper.map_nodes(page_nodes, extracted, resolver)
                        all_maps.extend(f_map)
                        all_reports.extend(f_rep)
                        
                    # Output the JSON files as specified in Phase 1 constraints
                    with open(self.workspace / "native_font_map.json", "w", encoding="utf-8") as f:
                        json.dump(all_maps, f, indent=2, ensure_ascii=False)
                    with open(self.workspace / "font_mapping_report.json", "w", encoding="utf-8") as f:
                        json.dump(all_reports, f, indent=2, ensure_ascii=False)
                        
            self._time_stage("Font Mapping", run_font_mapping)

        self.context = doc_context
        # 10. Export
        self._time_stage("Export", lambda: self._run_graph_plugins("exporters", doc_context))
        
        # Finish
        self.metadata["finished"] = time.time()
        self._save_stage_json("logs", "pipeline_metadata.json", self.metadata)
        
    def _run_plugins(self, category: str, context: DocumentContext, data: Any):
        plugins = self.plugin_manager.get_plugins(category)
        for plugin in plugins:
            plugin.run(context, data)
            
    def _run_graph_plugins(self, category: str, context: DocumentContext):
        plugins = self.plugin_manager.get_plugins(category)
        for plugin in plugins:
            plugin.run(context)
