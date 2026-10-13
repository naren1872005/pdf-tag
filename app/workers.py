import os
import json
from PySide6.QtCore import QThread, Signal
from parser.gemini_extractor import build_json, read_review_pages
from engine.pipeline_manager import PipelineManager

class PipelineWorker(QThread):
    finished_signal = Signal(bool, str, str) # success, message, output_pdf
    progress_signal = Signal(int, int, str) # current, total, message
    
    def __init__(self, workspace_path: str, pdf_path: str, config: dict, active_edits=None):
        super().__init__()
        self.workspace_path = workspace_path
        self.pdf_path = pdf_path
        self.config = config
        self.active_edits = active_edits or []
        self._is_stopped = False
        # pages of this run's extraction the user should check (see read_review_pages)
        self.review_pages = {"text_from_pdf": [], "not_read": []}
        
    def stop(self):
        self._is_stopped = True
        
    def run(self):
        try:
            raw_json = os.path.join(self.workspace_path, "semantic", "raw_semantic.json")
            rules_json = os.path.join(self.workspace_path, "graph", "rules_v1.json")
            import time
            from parser.gemini_scheduler import GeminiRequestScheduler
            
            extraction_elapsed = 0.0
            
            # Phase 1: Gemini Extraction (Immutable Phase)
            # a page that could not be read last time is tried again (the pages
            # already read come from the cache)
            # (not while manual edits exist: they belong to the current elements)
            retry_unread = (not self.active_edits and os.path.exists(raw_json)
                            and bool(read_review_pages(raw_json)["not_read"]))
            if not os.path.exists(raw_json) or retry_unread:
                start_extraction = time.perf_counter()
                build_json(
                    pdf_path=self.pdf_path,
                    output_json=raw_json,
                    model="gemini-3.6-flash",
                    pages_spec="",
                    dpi=200,
                    debug=False,
                    progress_callback=lambda cur, tot, msg: self.progress_signal.emit(cur, tot, msg),
                    is_stopped_callback=lambda: self._is_stopped
                )
                
                if self._is_stopped:
                    self.finished_signal.emit(False, "Pipeline stopped by user. Progress has been cached.", "")
                    return
                
                extraction_elapsed = time.perf_counter() - start_extraction
                self.review_pages = read_review_pages(raw_json)
                
            manager = PipelineManager(self.workspace_path, self.config)
            manager.metadata["pdf_path"] = self.pdf_path
            
            if not self.active_edits:
                # No manual edits, run the standard pipeline
                self.progress_signal.emit(0, 0, "Running Semantic Pipeline Engine...")
                manager.run(raw_json)
                output_pdf = manager.metadata.get("output_pdf")
                
                final_msg = "Pipeline execution completed successfully."
                self.finished_signal.emit(True, final_msg, output_pdf or "")
                return
                
            # We have active manual edits
            if not os.path.exists(rules_json):
                self.progress_signal.emit(0, 100, "Generating baseline hierarchical graph...")
                manager.run(raw_json)
                
            self.progress_signal.emit(50, 100, "Applying active manual edits for export...")
            import json
            with open(rules_json, "r", encoding="utf-8") as f:
                rules_data = json.load(f)
                
            from engine.document_context import DocumentContext
            from graph.document_graph import DocumentGraph
            from gui.edit_actions import UpdateRoleAction
            
            doc_context = DocumentContext()
            doc_context.metadata = manager.metadata
            doc_context.graph = DocumentGraph.from_json(rules_data)
            
            # print(f"[DEBUG] Processing {len(self.active_edits)} edits")
            for action in self.active_edits:
                action_type = type(action).__name__
                if action_type == "UpdateRoleAction":
                    for node in doc_context.graph.get_all_nodes():
                        if str(node.id) == str(action.node_id):
                            action._set_canonical_role(node.__dict__, action.new_role)
                            break
                elif action_type == "CreateTagAction":
                    from graph.node import Node
                    new_node = Node(action.node_dict)
                    doc_context.graph.add_node(new_node)
                elif action_type == "UpdateBBoxAction":
                    for node in doc_context.graph.get_all_nodes():
                        if str(node.id) == str(action.node_id) or str(node.uuid) == str(action.node_id):
                            old_bbox = action.old_bbox
                            new_bbox = action.new_bbox
                            node.geometry['bbox'] = new_bbox
                            if 'lines' in node.semantic:
                                del node.semantic['lines']
                                
                            if old_bbox and new_bbox and len(old_bbox) == 4 and len(new_bbox) == 4:
                                old_w = max(0.001, old_bbox[2] - old_bbox[0])
                                old_h = max(0.001, old_bbox[3] - old_bbox[1])
                                scale_x = (new_bbox[2] - new_bbox[0]) / old_w
                                scale_y = (new_bbox[3] - new_bbox[1]) / old_h
                                
                                def transform_children(parent_node):
                                    for child_uuid in parent_node.relations.get("children", []):
                                        child = doc_context.graph.get_node(child_uuid)
                                        if child:
                                            cb = child.geometry.get("bbox")
                                            if cb and len(cb) == 4:
                                                cx0 = new_bbox[0] + (cb[0] - old_bbox[0]) * scale_x
                                                cy0 = new_bbox[1] + (cb[1] - old_bbox[1]) * scale_y
                                                cx1 = new_bbox[0] + (cb[2] - old_bbox[0]) * scale_x
                                                cy1 = new_bbox[1] + (cb[3] - old_bbox[1]) * scale_y
                                                child.geometry['bbox'] = [cx0, cy0, cx1, cy1]
                                            if 'lines' in child.semantic:
                                                del child.semantic['lines']
                                            transform_children(child)
                                transform_children(node)
                            break
                elif action_type == "RemoveTagAction":
                    for node in doc_context.graph.get_all_nodes():
                        if str(node.id) == str(action.node_id) or str(node.uuid) == str(action.node_id):
                            doc_context.graph.remove_node(node.uuid)
                            break
                elif action_type == "BatchRemoveTagAction":
                    wanted = set()
                    for nd in action.node_dicts:
                        for key in ("id", "node_id", "uuid"):
                            if nd.get(key) is not None:
                                wanted.add(str(nd.get(key)))
                    for node in list(doc_context.graph.get_all_nodes()):
                        if str(node.id) in wanted or str(node.uuid) in wanted:
                            try:
                                doc_context.graph.remove_node(node.uuid)
                            except Exception:
                                pass
                elif action_type == "UpdateTextAction":
                    for node in doc_context.graph.get_all_nodes():
                        if str(node.id) == str(action.node_id) or str(node.uuid) == str(action.node_id):
                            node.text = action.new_text
                            if 'content' in node.semantic:
                                node.semantic['content'] = action.new_text
                            if 'actual_text' in node.semantic:
                                node.semantic['actual_text'] = action.new_text
                            
                            # Flatten: if we overwrite text on a parent, remove its kids so we don't emit empty structure tags
                            if 'children' in node.relations:
                                node.relations['children'] = []
                                
                            break
                            
            # Purge font_runs for edited nodes so that the renderer falls back to synthetic text and the changes are visible in Acrobat
            map_path = os.path.join(self.workspace_path, "native_font_map.json")
            if os.path.exists(map_path):
                import json
                try:
                    with open(map_path, "r", encoding="utf-8") as f:
                        fm = json.load(f)
                    
                    text_updated_ids = set()
                    
                    def collect_related_nodes(node):
                        if node.id is not None:
                            text_updated_ids.add(str(node.id))
                        if node.uuid is not None:
                            text_updated_ids.add(str(node.uuid))
                            
                        parent_uuid = node.relations.get("parent")
                        if parent_uuid:
                            text_updated_ids.add(str(parent_uuid))
                            
                        for child_uuid in node.relations.get("children", []):
                            child_node = doc_context.graph.get_node(child_uuid)
                            if child_node:
                                collect_related_nodes(child_node)

                    for action in self.active_edits:
                        if type(action).__name__ in ("UpdateTextAction", "UpdateBBoxAction"):
                            text_updated_ids.add(str(action.node_id))
                            for node in doc_context.graph.get_all_nodes():
                                if str(node.uuid) == str(action.node_id) or str(node.id) == str(action.node_id):
                                    collect_related_nodes(node)
                                    break
                            
                    if text_updated_ids:
                        new_fm = [entry for entry in fm if str(entry.get("node_id")) not in text_updated_ids]
                        if len(new_fm) != len(fm):
                            with open(map_path, "w", encoding="utf-8") as f:
                                json.dump(new_fm, f, indent=2)
                except Exception as e:
                    print(f"Failed to purge native font map for edited nodes: {e}")
                    
            self.progress_signal.emit(80, 100, "Exporting active hierarchical state...")
            exporters = manager.plugin_manager.get_plugins("exporters")
            for exporter in exporters:
                exporter.run(doc_context)
                
            output_pdf = manager.metadata.get("output_pdf")
            final_msg = "Exported active edited state successfully."
            self.finished_signal.emit(True, final_msg, output_pdf or "")
            
        except PermissionError as pe:
            self.finished_signal.emit(False, str(pe), "")
        except Exception as e:
            import traceback
            traceback.print_exc()
            if type(e).__name__ in ("AllGeminiCredentialsUnavailableError", "QuotaExhaustedError", "RuntimeError"):
                msg = "The cloud engine is temporarily unavailable or overloaded.\n\nCompleted pages have been saved.\n\nYou can resume this document in a few minutes."
                self.finished_signal.emit(False, msg, "")
            else:
                self.finished_signal.emit(False, str(e), "")

