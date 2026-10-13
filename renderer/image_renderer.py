# GUI/renderer/image_renderer.py
import pikepdf
from pikepdf import Name, Array, Dictionary

class ImageRenderer:
    
    @staticmethod
    def wrap_original_content(instructions, image_names, page_num, name_to_id, mcid_start, bad_font_aliases_helv=None, bad_font_aliases_zapf=None, is_digital=False):
        from renderer.render_result import RenderResult
        out = []
        render_results = []
        
        if bad_font_aliases_helv is None: bad_font_aliases_helv = set()
        if bad_font_aliases_zapf is None: bad_font_aliases_zapf = set()
        
        current_font = None
        
        # Artifact block wrapper (Only for non-digital flows)
        if not is_digital:
            out.append(pikepdf.ContentStreamInstruction(Array(), pikepdf.Operator("q")))
            out.append(pikepdf.ContentStreamInstruction(
                Array([Name("/Artifact"), Dictionary(Type=Name("/Pagination"))]),
                pikepdf.Operator("BDC")))
            
        current_mcid = mcid_start
        
        normalized_instructions = []
        i = 0
        while i < len(instructions):
            inst = instructions[i]
            
            # Check for malformed iLovePDF W sequence: W m l l l h n
            if str(inst.operator) == "W" and len(inst.operands) == 0:
                if i + 6 < len(instructions):
                    op_m = instructions[i+1]
                    op_l1 = instructions[i+2]
                    op_l2 = instructions[i+3]
                    op_l3 = instructions[i+4]
                    op_h = instructions[i+5]
                    op_n = instructions[i+6]
                    
                    def safe_float(ops, idx):
                        try:
                            return float(ops[idx])
                        except:
                            return None
                            
                    if (str(op_m.operator) == "m" and len(op_m.operands) == 2 and safe_float(op_m.operands, 0) == 0.0 and safe_float(op_m.operands, 1) == 0.0 and
                        str(op_l1.operator) == "l" and len(op_l1.operands) == 2 and safe_float(op_l1.operands, 0) == 612.0 and safe_float(op_l1.operands, 1) == 0.0 and
                        str(op_l2.operator) == "l" and len(op_l2.operands) == 2 and safe_float(op_l2.operands, 0) == 612.0 and safe_float(op_l2.operands, 1) == 792.0 and
                        str(op_l3.operator) == "l" and len(op_l3.operands) == 2 and safe_float(op_l3.operands, 0) == 0.0 and safe_float(op_l3.operands, 1) == 792.0 and
                        str(op_h.operator) == "h" and len(op_h.operands) == 0 and
                        str(op_n.operator) == "n" and len(op_n.operands) == 0):
                        
                        # Sequence verified. Normalize: m l l l h W n
                        normalized_instructions.extend([op_m, op_l1, op_l2, op_l3, op_h, inst, op_n])
                        i += 7
                        continue

            normalized_instructions.append(inst)
            i += 1
            
        for inst in normalized_instructions:
            # Strip pre-existing marked content delimiters (BDC, BMC, EMC) from the original input PDF
            # so they do not conflict with the surrounding /Artifact block or inject duplicate/corrupted MCIDs.
            # We ONLY do this for scanned/hybrid PDFs. For Digital, we preserve the tags injected by DigitalStreamTagger.
            if not is_digital and inst.operator in (pikepdf.Operator("BDC"), pikepdf.Operator("BMC"), pikepdf.Operator("EMC")):
                continue

            if inst.operator == pikepdf.Operator("Do") and inst.operands:
                op_name = str(inst.operands[0])
                if op_name in image_names:
                    obj_id = name_to_id.get(op_name)
                    # Digital/Hybrid: figures are already wrapped by DigitalStreamTagger
                    # (by bbox). Wrapping again would nest a second MCID around them.
                    if obj_id is not None and not is_digital:
                        # Semantic Figure: Break out of Artifact (if applicable), wrap image in Figure BDC, then resume Artifact
                        if not is_digital:
                            out.append(pikepdf.ContentStreamInstruction(Array(), pikepdf.Operator("EMC")))
                        
                        out.append(pikepdf.ContentStreamInstruction(
                            Array([Name("/Figure"), Dictionary(MCID=current_mcid)]),
                            pikepdf.Operator("BDC")
                        ))
                        
                        out.append(inst)
                        out.append(pikepdf.ContentStreamInstruction(Array(), pikepdf.Operator("EMC")))
                        
                        render_results.append(RenderResult(
                            object_id=obj_id,
                            page=page_num,
                            mcid=current_mcid
                        ))
                        current_mcid += 1
                        
                        if not is_digital:
                            out.append(pikepdf.ContentStreamInstruction(
                                Array([Name("/Artifact"), Dictionary(Type=Name("/Pagination"))]),
                                pikepdf.Operator("BDC")))
                        continue
                    else:
                        # Background/Decorative Image: Remains inside the current Artifact BDC block.
                        # Do NOT allocate an MCID, do NOT wrap in Figure.
                        pass
                    
            if inst.operator == pikepdf.Operator("Tf") and inst.operands:
                current_font = str(inst.operands[0])
                
            elif inst.operator == pikepdf.Operator("Tj") and current_font:
                raw_bytes = bytes(inst.operands[0])
                is_bad = False
                if is_digital:
                    pass  # never rewrite original glyphs of a Digital/Hybrid PDF
                elif current_font in bad_font_aliases_zapf and raw_bytes == b'I':
                    is_bad = True
                elif current_font in bad_font_aliases_helv and raw_bytes in (b'\x7f', b'\x95'):
                    is_bad = True
                    
                if is_bad:
                    # Inject 3 Tr (invisible text) before drawing
                    # Replace the bad bytes with a space to avoid PAC Unicode mapping errors on Artifacts
                    safe_inst = pikepdf.ContentStreamInstruction(pikepdf.Array([pikepdf.String(" ")]), pikepdf.Operator("Tj"))
                    out.append(pikepdf.ContentStreamInstruction(pikepdf.Array([3]), pikepdf.Operator("Tr")))
                    out.append(safe_inst)
                    out.append(pikepdf.ContentStreamInstruction(pikepdf.Array([0]), pikepdf.Operator("Tr")))
                    continue

            out.append(inst)
            
        if not is_digital:
            out.append(pikepdf.ContentStreamInstruction(Array(), pikepdf.Operator("EMC")))
            out.append(pikepdf.ContentStreamInstruction(Array(), pikepdf.Operator("Q")))
        return out, current_mcid, render_results

    @staticmethod
    def get_image_xobject_names(page_obj):
        names = set()
        xobjs = page_obj.get("/Resources", {}).get("/XObject", None)
        if not xobjs:
            return names
        for name, ref in xobjs.items():
            try:
                if ref.get("/Subtype") == Name("/Image"):
                    names.add(str(name))
            except Exception:
                continue
        return names
