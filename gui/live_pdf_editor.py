import os
import shutil
import tempfile
import pikepdf
from pikepdf import Name, Dictionary
from PySide6.QtCore import QThread, Signal, QObject, QMutex, QWaitCondition
from typing import List, Dict, Any, Optional

from engine.document_context import DocumentContext

class LivePdfEditorWorker(QThread):
    progress_signal = Signal(str, int)  # message, percentage
    finished_signal = Signal(bool, str) # success, message
    trigger_fast_export_signal = Signal(list) # active_edits
    
    def __init__(self, project_path: str, input_pdf: str, config: dict, main_window: QObject):
        super().__init__()
        self.project_path = project_path
        self.input_pdf = input_pdf
        self.config = config
        self.main_window = main_window # We need this to trigger the fallback pipeline
        
        self._mutex = QMutex()
        self._condition = QWaitCondition()
        self._is_running = True
        self.is_processing = False
        self._pending_update = False
        self._latest_edits = []
        
    def schedule_update(self, active_edits: list):
        """Queue a new update with the latest edits, debouncing the thread."""
        self._mutex.lock()
        self._latest_edits = list(active_edits)
        self._pending_update = True
        self._condition.wakeOne()
        self._mutex.unlock()
        
    def stop(self):
        self._mutex.lock()
        self._is_running = False
        self._condition.wakeOne()
        self._mutex.unlock()
        self.wait()

    def run(self):
        while True:
            self._mutex.lock()
            while not self._pending_update and self._is_running:
                self._condition.wait(self._mutex)
                
            if not self._is_running:
                self._mutex.unlock()
                break
                
            edits_to_process = list(self._latest_edits)
            self._pending_update = False
            self._mutex.unlock()
            
            self._process_edits(edits_to_process)

    def _process_edits(self, active_edits: list):
        if not active_edits:
            return
            
        self.is_processing = True
        self.progress_signal.emit("Updating tagged PDF...", 0)
        
        # Check if any edit involves an Artifact (or if we need fallback)
        requires_full_export = False
        for edit in active_edits:
            if edit.__class__.__name__ in ['UpdateBBoxAction', 'RemoveTagAction', 'BatchRemoveTagAction', 'CreateTagAction', 'UpdateTextAction']:
                requires_full_export = True
                break
            
            new_role = getattr(edit, 'new_role', None)
            if new_role == "Artifact":
                requires_full_export = True
                break
                
        output_dir = os.path.join(self.project_path, "output")
        tagged_pdf = None
        if os.path.exists(output_dir):
            for f in os.listdir(output_dir):
                if f.endswith("_tagged.pdf"):
                    tagged_pdf = os.path.join(output_dir, f)
                    break
                
        if not tagged_pdf or not os.path.exists(tagged_pdf):
            requires_full_export = True
            
        if requires_full_export:
            # We must trigger the PipelineWorker (fast-path export) because Artifacts require content stream generation
            self.progress_signal.emit("Artifact detected: Running fast-path exporter...", 50)
            
            # Pass ALL historical edits so that the exporter applies them on top of the baseline rules_v1.json graph
            all_edits = list(self.main_window.action_history.undo_stack)
            self.trigger_fast_export_signal.emit(all_edits)
            print("[LivePdfEditor] Artifact detected. Emitted trigger_fast_export_signal. Returning.")
            self.finished_signal.emit(True, "Delegated to PipelineWorker.")
            self.is_processing = False
            return

        # Attempt lightweight pikepdf mutation
        try:
            print("[LivePdfEditor] Entering _apply_pikepdf_mutation...")
            self._apply_pikepdf_mutation(tagged_pdf, active_edits)
            print("[LivePdfEditor] Exited _apply_pikepdf_mutation successfully.")
            self.finished_signal.emit(True, "Tagged PDF updated.")
        except PermissionError as pe:
            # File is locked. Don't fallback, just tell user.
            self.finished_signal.emit(False, str(pe))
        except Exception as e:
            # If the PDF lacks /ID mapping or is corrupted, fallback to fast export
            self.progress_signal.emit(f"Live mutation failed ({str(e)}), falling back to fast-path exporter...", 50)
            self.trigger_fast_export_signal.emit(active_edits)
        finally:
            self.is_processing = False


    def _apply_pikepdf_mutation(self, pdf_path: str, active_edits: list):
        """
        Mutate the StructTreeRoot of the existing tagged PDF safely.

        ParentTree architecture (verified from actual generated PDFs):
          page.StructParents = N  → key into ParentTree.Nums
          ParentTree.Nums = [N, [sparse_array_indexed_by_MCID], ...]
          sparse_array[MCID] = indirect StructElem (or Null for gaps)

        For CreateTagAction:
          - tag_selection returns a list of MCIDs (one per contiguous range)
          - a single StructElem is created with K = single_int or Array of ints
          - the page's sparse ParentTree array is extended/updated so every
            MCID in used_mcids resolves to that StructElem

        For UpdateRoleAction:
          - walk StructTreeRoot /K tree, find StructElems with /ID = "node_{node_id}"
          - update their /S field
        """
        from engine.file_lock import check_writable
        check_writable(pdf_path, action="retry")   # true "open in another program" only
        from export.accessibility_mapper import AccessibilityMapper
        from plugins.digital.digital_stream_tagger import DigitalStreamTagger

        edit_map = {}    # node_id_str -> new_pdf_tag  (for UpdateRoleAction)
        text_edit_map = {} # node_id_str -> new_text (for UpdateTextAction)
        create_tags = [] # [(node_id, node_dict)]       (for CreateTagAction)

        for edit in active_edits:
            action_type = type(edit).__name__
            if action_type == "UpdateRoleAction":
                node_id_str = str(edit.node_id)
                tag = AccessibilityMapper.get_tag(edit.new_role)
                edit_map[node_id_str] = tag
            elif action_type == "UpdateTextAction":
                node_id_str = str(edit.node_id)
                text_edit_map[node_id_str] = edit.new_text
            elif action_type == "CreateTagAction":
                create_tags.append((edit.node_id, edit.node_dict))

        temp_pdf = pdf_path + ".tmp.pdf"

        with pikepdf.Pdf.open(pdf_path) as pdf:
            struct_tree = pdf.Root.get("/StructTreeRoot")
            if not struct_tree:
                raise ValueError("PDF missing StructTreeRoot — cannot perform live mutation.")

            parent_tree = struct_tree.get("/ParentTree")
            if not parent_tree or "/Nums" not in parent_tree:
                raise ValueError("PDF missing ParentTree — cannot perform live mutation.")

            nums = parent_tree.Nums  # mutable pikepdf.Array [key0, arr0, key1, arr1, ...]

            # ------------------------------------------------------------------
            # Helper: resolve page index → (nums_key, sparse_mcid_array)
            # using the page's StructParents value as the lookup key.
            # ------------------------------------------------------------------
            def _get_page_mcid_array(page_idx: int):
                """
                Returns (nums_index_of_key, page_sp_key, sparse_array).
                The nums_index_of_key is the position of the key in nums (even index).
                sparse_array is a pikepdf.Array indexed by MCID.
                """
                page_obj_local = pdf.pages[page_idx]
                sp = page_obj_local.get("/StructParents")
                if sp is None:
                    print("[LivePdfEditor] sp is None. Returning None.")
                    return None, None, None
                page_sp_key = int(sp)
                nums_list = list(nums)
                for i in range(0, len(nums_list), 2):
                    if int(nums_list[i]) == page_sp_key:
                        val = nums_list[i + 1]
                        if isinstance(val, pikepdf.Array):
                            print(f"[LivePdfEditor] Found array. Returning.")
                            return i, page_sp_key, val
                        # Single-element (rare): wrap
                        print(f"[LivePdfEditor] Wrapping array. Returning.")
                        return i, page_sp_key, pikepdf.Array([val])
                print(f"[LivePdfEditor] Page index not found. Returning None.")
                return None, page_sp_key, None

            # ------------------------------------------------------------------
            # Helper: find next safe MCID for a page's sparse array
            # ------------------------------------------------------------------
            def _next_safe_mcid(sparse_array) -> int:
                if sparse_array is None:
                    print("[LivePdfEditor] sparse_array is None. Returning 0.")
                    return 0
                print(f"[LivePdfEditor] Returning len(sparse_array): {len(list(sparse_array))}")
                return len(list(sparse_array))  # array is indexed by MCID, so len = next available

            # ------------------------------------------------------------------
            # PROCESS CreateTagActions FIRST
            # ------------------------------------------------------------------
            if create_tags:
                for node_id, node_dict in create_tags:
                    bbox = node_dict.get('geometry', {}).get('bbox') or node_dict.get('bbox', [0,0,0,0])
                    new_role = (node_dict.get('semantic') or {}).get('type', 'P')
                    pdf_tag = AccessibilityMapper.get_tag(new_role)
                    page_idx = (node_dict.get('metadata') or {}).get('page') or node_dict.get('page', 1)
                    if isinstance(page_idx, int) and page_idx > 0:
                        page_idx = page_idx - 1  # convert 1-based to 0-based
                    else:
                        page_idx = 0

                    if page_idx >= len(pdf.pages):
                        raise ValueError(f"Page index {page_idx} out of range for CreateTagAction {node_id}")

                    page_obj = pdf.pages[page_idx]

                    # Get/create the page's ParentTree entry
                    nums_pos, page_sp_key, sparse_array = _get_page_mcid_array(page_idx)

                    if sparse_array is None:
                        # Page has no StructParents yet — assign one
                        next_key = int(struct_tree.get("/ParentTreeNextKey", len(list(nums)) // 2))
                        page_obj.StructParents = next_key
                        sparse_array = pikepdf.Array()
                        nums.append(next_key)
                        nums.append(sparse_array)
                        struct_tree.ParentTreeNextKey = next_key + 1
                        page_sp_key = next_key
                        nums_pos = len(list(nums)) - 2

                    next_mcid = _next_safe_mcid(sparse_array)

                    # Run the stream tagger
                    instructions = list(pikepdf.parse_content_stream(page_obj))
                    new_instrs, used_mcids, tagged = DigitalStreamTagger.tag_selection(
                        pdf, page_obj, instructions, bbox, pdf_tag, next_mcid
                    )

                    if not tagged:
                        raise ValueError(
                            f"CreateTagAction {node_id}: selected region contains already-tagged "
                            "content or no native text was found within the bounding box."
                        )

                    # Write the modified content stream
                    page_obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(new_instrs))

                    # Build StructElem with correct K
                    if len(used_mcids) == 1:
                        k_value = used_mcids[0]
                    else:
                        k_value = pikepdf.Array(used_mcids)

                    # Find the Document StructElem (first child of StructTreeRoot) to parent under
                    k_root = struct_tree.get("/K")
                    parent_elem = struct_tree  # fallback
                    if isinstance(k_root, pikepdf.Array) and len(list(k_root)) > 0:
                        first_child = list(k_root)[0]
                        if isinstance(first_child, pikepdf.Dictionary) and str(first_child.get("/S", "")) == "/Document":
                            parent_elem = first_child

                    new_elem = pdf.make_indirect(Dictionary(
                        Type=Name("/StructElem"),
                        S=Name(f"/{pdf_tag}"),
                        TPNodeID=pikepdf.String(f"node_{node_id}"),
                        K=k_value,
                        Pg=page_obj.obj,
                        P=parent_elem
                    ))

                    # Attach to parent's K array
                    if "/K" not in parent_elem or not isinstance(parent_elem.get("/K"), pikepdf.Array):
                        parent_elem.K = pikepdf.Array()
                    parent_elem.K.append(new_elem)

                    # Update the ParentTree sparse array for every used MCID
                    # Extend the array to cover all new MCIDs, then set each position
                    sparse_list = list(sparse_array)
                    max_needed = max(used_mcids)
                    while len(sparse_list) <= max_needed:
                        sparse_list.append(None)   # PDF null (pikepdf has no Null class)
                    for mcid in used_mcids:
                        sparse_list[mcid] = new_elem

                    # Rebuild the sparse array in-place
                    new_sparse = pikepdf.Array(sparse_list)
                    # Replace in nums: nums[nums_pos + 1] = new_sparse
                    nums_as_list = list(nums)
                    nums_as_list[nums_pos + 1] = new_sparse
                    # Rebuild nums
                    new_nums = pikepdf.Array(nums_as_list)
                    parent_tree.Nums = new_nums

            # ------------------------------------------------------------------
            # PROCESS UpdateRoleActions
            # ------------------------------------------------------------------
            if edit_map or text_edit_map:
                def _walk_tree(node, found_count=0):
                    if isinstance(node, pikepdf.Dictionary):
                        node_type = node.get("/Type")
                        if node_type and str(node_type) == "/StructElem":
                            obj_id = node.get("/TPNodeID") or node.get("/ID")
                            if obj_id:
                                id_str = str(obj_id)
                                if id_str.startswith("node_"):
                                    nid = id_str[5:]
                                    if nid in edit_map:
                                        node.S = Name(f"/{edit_map[nid]}")
                                        found_count += 1
                                    if nid in text_edit_map:
                                        node.ActualText = pikepdf.String(text_edit_map[nid])
                                        found_count += 1

                            kids = node.get("/K")
                            if kids is not None:
                                if isinstance(kids, pikepdf.Array):
                                    for kid in list(kids):
                                        found_count = _walk_tree(kid, found_count)
                                elif isinstance(kids, pikepdf.Dictionary):
                                    found_count = _walk_tree(kids, found_count)
                    elif isinstance(node, pikepdf.Array):
                        for item in list(node):
                            found_count = _walk_tree(item, found_count)
                    return found_count

                found = _walk_tree(struct_tree)
                expected = len(edit_map) + len(text_edit_map)
                if found < expected:
                    self.progress_signal.emit("Warning: PDF lacks /ID mappings for some nodes. Re-run pipeline to regenerate a mapped PDF.", 50)

            # /ID only on Note tags, numbered 1..N (a role edit may add/remove a Note)
            try:
                from renderer.struct_tree_builder import finalize_element_ids
                finalize_element_ids(pdf)
            except Exception as _e:
                print(f"[LivePdfEditor] Element ID pass failed: {_e}")

            pdf.save(temp_pdf)

        # ------------------------------------------------------------------
        # Atomic file replacement (Windows-safe: remove then move)
        # ------------------------------------------------------------------
        from engine.file_lock import safe_replace
        safe_replace(temp_pdf, pdf_path, action="retry")   # never deletes the old PDF first

