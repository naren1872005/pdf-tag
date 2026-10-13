from plugins.rules.base_rule import BaseSemanticRule
from engine.document_context import DocumentContext
from plugins.rules.action_queue import ActionQueue, GraphAction
from graph.node import Node
import uuid

class TableCompletenessRule(BaseSemanticRule):
    """
    Priority 7 Rule.
    Fills in missing empty TD elements so that all rows in a table have the same number of columns.
    Ensures PDF/UA compliance for logical table matrices without altering existing data.
    """
    name = "TableCompletenessRule"
    priority = 7
    
    def run(self, context: DocumentContext, action_queue: ActionQueue):
        nodes = list(context.graph.node_map.values())
        for node in nodes:
            sem_type = node.semantic.get("type", "")
            if sem_type == "table":
                row_uuids = node.relations.get("children", [])
                if not row_uuids:
                    continue
                
                # Determine max columns and find a reference row
                max_cols = 0
                reference_row = None
                rows_data = []
                for r_uuid in row_uuids:
                    row_node = context.graph.get_node(r_uuid)
                    if row_node and row_node.semantic.get("type", "") == "table_row":
                        cells = row_node.relations.get("children", [])
                        if len(cells) > max_cols:
                            max_cols = len(cells)
                            reference_row = row_node
                        rows_data.append(row_node)
                        
                if max_cols == 0 or not reference_row:
                    continue
                    
                # Get column spans from the reference row
                ref_cells = reference_row.relations.get("children", [])
                column_spans = []
                for c_uuid in ref_cells:
                    c_node = context.graph.get_node(c_uuid)
                    c_bbox = c_node.geometry.get("bbox", [0,0,0,0]) if c_node else [0,0,0,0]
                    column_spans.append(c_bbox)
                
                # Sort columns left to right just in case
                column_spans.sort(key=lambda b: b[0])
                        
                widest_col = max((b[2] - b[0]) for b in column_spans) if column_spans else 0

                # Pad rows that have fewer cells than max_cols
                for row_node in rows_data:
                    cells = row_node.relations.get("children", [])
                    if len(cells) >= max_cols:
                        continue
                    # A row with a spanning cell (e.g. one header across 3 columns) is
                    # complete: padding it would invent cells under the span. The
                    # exporter's StructureEnforcer builds the exact grid (spans + empty TDs).
                    spans_cols = False
                    for cell_uuid in cells:
                        c_node = context.graph.get_node(cell_uuid)
                        cb = c_node.geometry.get("bbox", [0, 0, 0, 0]) if c_node else [0, 0, 0, 0]
                        if widest_col and (cb[2] - cb[0]) > 1.5 * widest_col:
                            spans_cols = True
                            break
                    if spans_cols:
                        continue
                        
                    # Map existing cells to column indices based on X proximity
                    existing_mapped = {}
                    for cell_uuid in cells:
                        c_node = context.graph.get_node(cell_uuid)
                        if not c_node:
                            continue
                        c_bbox = c_node.geometry.get("bbox", [0,0,0,0])
                        
                        # Find closest column
                        col_idx = min(range(max_cols), key=lambda c: abs(c_bbox[0] - column_spans[c][0]))
                        
                        # Handle collisions by shifting right
                        while col_idx in existing_mapped and col_idx < max_cols - 1:
                            col_idx += 1
                        existing_mapped[col_idx] = cell_uuid
                        
                    new_children = []
                    r_bbox = row_node.geometry.get("bbox", [0,0,0,0])
                    
                    for col_idx in range(max_cols):
                        if col_idx in existing_mapped:
                            new_children.append(existing_mapped[col_idx])
                        else:
                            # Generate a new empty cell for this column
                            empty_id = str(uuid.uuid4())
                            raw_data = {
                                "id": empty_id,
                                "uuid": empty_id,
                                "content": "",
                                "page_number": getattr(row_node, "page", None) or row_node.metadata.get("page")
                            }
                            empty_node = Node(raw_data)
                            empty_node.semantic["type"] = "table_data_cell"
                            
                            # Use X geometry of the column, Y geometry of the row
                            c_span = column_spans[col_idx]
                            empty_node.geometry = {"bbox": [c_span[0], r_bbox[1], c_span[2], r_bbox[3]]}
                            empty_node.relations["parent"] = row_node.uuid
                            
                            # Add to graph
                            context.graph.add_node(empty_node)
                            new_children.append(empty_node.uuid)
                            
                            row_node.add_history(f"{self.name}: Inserted empty TD at col {col_idx}")
                            action_queue.reports.append({
                                "rule": self.name,
                                "message": f"Inserted empty TD at column {col_idx} in row {row_node.uuid}",
                                "modified_nodes": [row_node.uuid, empty_node.uuid]
                            })
                            
                    # Replace children with correctly ordered mixed list
                    row_node.relations["children"] = new_children
