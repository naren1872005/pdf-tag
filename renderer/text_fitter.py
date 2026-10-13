# GUI/renderer/text_fitter.py

MIN_FONT_SIZE = 3.0
MAX_FONT_SIZE = 14.0

TZ_MIN_PCT = 85.0
TZ_MAX_PCT = 115.0

TC_MAX_MULT = 0.6
TC_MIN_MULT = -0.3

WRAP_MAX_ITER = 6
WRAP_CONVERGE_TOL = 0.01

OVERFLOW_RETRY_ATTEMPTS = 4
OVERFLOW_RETRY_SHRINK = 0.85

VERTICAL_ASPECT_THRESHOLD = 2.2
VERTICAL_MAX_WIDTH = 40.0
VERTICAL_DIRECTION_DEFAULT = "bottom_to_top"

_ORIENTATION_FIELDS = ("orientation", "rotation", "text_direction", "angle")

class TextFitter:
    
    @staticmethod
    def detect_orientation(el, geom):
        """Returns (is_vertical: bool, direction: 'bottom_to_top' | 'top_to_bottom')."""
        for field in _ORIENTATION_FIELDS:
            val = el.get(field)
            if val in (90, -270, "90"):
                return True, "bottom_to_top"
            if val in (270, -90, "270"):
                return True, "top_to_bottom"

        w, h = geom["width"], geom["height"]
        if w > 0 and h / w >= VERTICAL_ASPECT_THRESHOLD and w <= VERTICAL_MAX_WIDTH:
            return True, VERTICAL_DIRECTION_DEFAULT
        return False, "bottom_to_top"

    @staticmethod
    def wrap_paragraph(font, text, font_size, max_width):
        """Greedy word-wrap of one paragraph to fit max_width at font_size."""
        if not text:
            return [""]
        words = text.split(" ")
        lines, current = [], ""
        for w in words:
            trial = f"{current} {w}".strip()
            if not current or font.text_length(trial, fontsize=font_size) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = w
        if current:
            lines.append(current)
        return lines or [""]

    @staticmethod
    def fit_text_block(font, geom, content, vertical, direction, el_id, max_font_size=None, document_type="Scanned PDF", font_runs=None, source_lines=None, text_generation_mode="auto", crop_offset=(0.0, 0.0)):
        if text_generation_mode == "auto":
            is_native_mode = bool(font_runs and "DIGITAL" in document_type.upper())
        else:
            is_native_mode = (text_generation_mode == "native")
            
        if is_native_mode:
            # Native Font Mode: bypass all text fitting algorithms.
            # We already have exact coordinates, sizes, and fonts in the native runs.
            return [], 0, geom, []
            
        warnings = []
        left, right = geom["left"], geom["right"]
        bottom, top = geom["bottom"], geom["top"]
        box_w, box_h = geom["width"], geom["height"]
        cap = MAX_FONT_SIZE if max_font_size is None else max_font_size

        SINGLE_LINE_MAX_FONT_SIZE = 72.0
        thickness, length = (box_w, box_h) if vertical else (box_h, box_w)
        span = font.ascender - font.descender
        paragraphs = content.split("\n") if content else [""]

        effective_length = length / (TZ_MIN_PCT / 100.0) if not vertical else length

        use_source_lines = False
        source_line_matches = []
        
        if source_lines and not vertical:
            current_offset = 0
            match_failed = False
            for line in source_lines:
                line_text = line.get("text", "")
                start_idx = content.find(line_text.strip(), current_offset)
                if start_idx == -1:
                    start_idx = content.find(line_text, current_offset)
                
                exact_start = content.find(line_text, current_offset)
                if exact_start != -1:
                    start_idx = exact_start
                    
                if start_idx == -1:
                    print(f"[{el_id}] WARNING: Source line matching failed at '{line_text}'.")
                    print(f"  Content length: {len(content)}, current_offset: {current_offset}")
                    print(f"  Looking for: '{line_text.strip()}'")
                    print(f"  Remaining content: '{content[current_offset:current_offset+100]}...'")
                    match_failed = True
                    break
                    
                end_idx = start_idx + len(line_text)
                current_offset = end_idx
                source_line_matches.append({
                    "text": line_text,
                    "bbox": line.get("box_2d", [left, bottom, right, top]),
                    "start": start_idx,
                    "end": end_idx
                })
                
            if not match_failed and source_line_matches:
                use_source_lines = True

        if use_source_lines:
            wrapped = [m["text"] for m in source_line_matches]
            n_lines = max(1, len(wrapped))
            font_size = max(MIN_FONT_SIZE, thickness / (span * n_lines))
        else:
            max_1line_size = SINGLE_LINE_MAX_FONT_SIZE
            if not vertical and len(paragraphs) == 1 and paragraphs[0]:
                nat_1 = font.text_length(paragraphs[0], fontsize=1.0)
                if nat_1 > 0:
                    max_1line_size = effective_length / nat_1

            is_retry = (max_font_size is not None and max_font_size < MAX_FONT_SIZE - 0.01)
            initial_cap = cap

            font_size = max(MIN_FONT_SIZE, min(initial_cap, thickness / span))
            wrapped = paragraphs
            for _ in range(WRAP_MAX_ITER):
                wrapped = []
                for p in paragraphs:
                    wrapped.extend(TextFitter.wrap_paragraph(font, p, font_size, effective_length))
                n_lines = max(1, len(wrapped))
                
                if not vertical and n_lines == 1 and not is_retry:
                    current_cap = min(SINGLE_LINE_MAX_FONT_SIZE, max_1line_size)
                else:
                    current_cap = cap
                    
                candidate_size = max(MIN_FONT_SIZE, min(current_cap, thickness / (span * n_lines)))
                if abs(candidate_size - font_size) < WRAP_CONVERGE_TOL:
                    break
                
                font_size = font_size + 0.5 * (candidate_size - font_size)
                font_size = max(MIN_FONT_SIZE, min(current_cap, font_size))
            else:
                wrapped = []
                for p in paragraphs:
                    wrapped.extend(TextFitter.wrap_paragraph(font, p, font_size, effective_length))

            n_lines = max(1, len(wrapped))
            
            if not vertical:
                font_size = max(MIN_FONT_SIZE, thickness / (span * n_lines))
            else:
                if n_lines * font_size * span > thickness + 0.1:
                    font_size = thickness / (span * n_lines)
                    font_size = max(MIN_FONT_SIZE, font_size)

        line_height = font_size * span
        if not use_source_lines and n_lines * line_height > thickness + 0.5:
            warnings.append(
                f"[{el_id}] {n_lines} line(s) need {n_lines*line_height:.1f}pt but box "
                f"only has {thickness:.1f}pt even at MIN_FONT_SIZE={MIN_FONT_SIZE} -- "
                f"content will overflow the box."
            )

        lines_out = []
        max_extent = 0.0
        current_offset = 0
        for i, line in enumerate(wrapped):
            natural = font.text_length(line, fontsize=font_size) if line else 0.0
            n_chars = max(1, len(line))

            l_left, l_bottom, l_right, l_top = left, bottom, right, top
            l_length = length
            
            if use_source_lines:
                sm = source_line_matches[i]
                l_left, l_bottom, l_right, l_top = sm["bbox"]
                l_length = l_right - l_left

            if vertical:
                tz = 100.0
                tc = 0.0
                if natural > 0:
                    tc_raw = (l_length - natural) / n_chars
                    tc_min = font_size * TC_MIN_MULT
                    tc = max(tc_min, tc_raw)
                    rendered_len = natural + tc * n_chars
                else:
                    rendered_len = 0.0
            else:
                tz = 100.0
                tc = 0.0
                if natural > 0:
                    tz_raw = 100.0 * l_length / natural
                    tz = max(TZ_MIN_PCT, min(TZ_MAX_PCT, tz_raw))
                    
                    rendered_len_tz_only = natural * tz / 100.0
                    tc_raw = (l_length - rendered_len_tz_only) / n_chars
                    tc_max = font_size * TC_MAX_MULT
                    tc_min = font_size * TC_MIN_MULT
                    tc = max(tc_min, min(tc_max, tc_raw))
                    rendered_len = rendered_len_tz_only + tc * n_chars
                else:
                    rendered_len = 0.0

            max_extent = max(max_extent, rendered_len)

            cx, cy = crop_offset
            
            if vertical:
                if direction == "bottom_to_top":
                    a, b, c, d = 0.0, 1.0, -1.0, 0.0
                    e = l_left + font.ascender * font_size + i * line_height + cx
                    f = l_bottom + cy
                else:
                    a, b, c, d = 0.0, -1.0, 1.0, 0.0
                    e = l_right - font.ascender * font_size - i * line_height + cx
                    f = l_top + cy
            else:
                a, b, c, d = 1.0, 0.0, 0.0, 1.0
                if use_source_lines:
                    if "DIGITAL" in document_type.upper():
                        e = l_left + cx
                    else:
                        e = l_left + (l_length - rendered_len) / 2.0 + cx
                    f = l_top - font.ascender * font_size + cy
                else:
                    if "DIGITAL" in document_type.upper():
                        e = l_left + cx
                    else:
                        e = l_left + (l_length - rendered_len) / 2.0 + cx
                        
                    f = l_top - font.ascender * font_size - i * line_height + cy
                    if n_lines == 1 and thickness > span * font_size + 0.5:
                        f -= (thickness - span * font_size) / 2.0

            if use_source_lines:
                start_idx = source_line_matches[i]["start"]
                end_idx = source_line_matches[i]["end"]
                current_offset = end_idx
            else:
                start_idx = content.find(line, current_offset)
                if start_idx == -1:
                    start_idx = current_offset
                end_idx = start_idx + len(line)
                current_offset = end_idx

            lines_out.append({
                "text": line, "tz": tz, "tc": tc, "tm": (a, b, c, d, e, f),
                "source_start": start_idx, "source_end": end_idx
            })

        if vertical:
            if direction == "bottom_to_top":
                actual_rect = (
                    left, bottom,
                    left + span * font_size + (n_lines - 1) * line_height, bottom + max_extent,
                )
            else:
                actual_rect = (
                    right - span * font_size - (n_lines - 1) * line_height, top - max_extent,
                    right, top,
                )
        else:
            y_top = top
            if n_lines == 1 and thickness > span * font_size + 0.5:
                y_top -= (thickness - span * font_size) / 2.0
                
            actual_rect = (
                left, y_top - n_lines * line_height,
                left + max_extent, y_top,
            )

        # Preserve the true bbox from the model as requested by the user
        actual_rect = (left, bottom, right, top)
        
        return lines_out, font_size, actual_rect, warnings
