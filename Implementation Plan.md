Yes. Based on the current architecture, the original plan, and the three PAC reports, I would make the implementation plan **narrow and test-driven** rather than touching the whole tagging engine.

The immediate milestone should be:

> **Make the generated PDF correctly declare document metadata and language, then verify the result against PAC before moving back to tagging-rule improvements.**

## Implementation Plan — PDF/UA Metadata & Language

### Phase 0 — Freeze the current implementation

Before changing anything, create a regression baseline.

Record for all three samples:

| Sample   | Pages | Metadata failures | Natural-language failures | Document settings failures |
| -------- | ----: | ----------------: | ------------------------: | -------------------------: |
| Sample 1 |     1 |                 3 |                        58 |                          1 |
| Sample 2 |     2 |                 3 |                        93 |                          1 |
| Sample 3 |     2 |                 3 |                         1 |                          1 |

These values come directly from your uploaded PAC reports.   

**Do not modify the tagging rules during this phase.**

---

# Phase 1 — Inspect the existing renderer

First locate and inspect:

```text
renderer/
├── pdf_renderer.py
├── mcid_writer.py
├── parent_tree.py
├── struct_tree.py
```

Also inspect:

```text
config/
├── client.json
```

and the pipeline code responsible for:

```text
Semantic JSON
      ↓
Document Graph
      ↓
Renderer
```

### Specifically determine

Where the current implementation creates:

* PDF Catalog
* Info dictionary
* StructTreeRoot
* MarkInfo
* Metadata object
* ViewerPreferences
* page objects
* content streams

The objective is **not to rewrite the renderer**.

We need to find the correct insertion points.

---

# Phase 2 — Create a canonical metadata model

Add:

```text
metadata/
├── __init__.py
├── metadata_model.py
├── metadata_resolver.py
├── language_resolver.py
├── xmp_builder.py
├── metadata_writer.py
└── metadata_validator.py
```

### `metadata_model.py`

Create one canonical object representing document-level metadata.

Conceptually:

```python
class DocumentMetadata:
    title
    language
    author
    subject
    keywords
    creator
    producer

    pdfua_part
    display_doc_title
```

The important fields for this milestone are:

```text
title
language
pdfua_part
display_doc_title
```

Everything else can remain optional.

---

# Phase 3 — Implement metadata resolution

Create:

```text
metadata/metadata_resolver.py
```

Its responsibility:

```text
Semantic JSON
      ↓
Project configuration
      ↓
Client configuration
      ↓
User overrides
      ↓
Resolved DocumentMetadata
```

The renderer should **never decide where metadata comes from**.

For example:

```text
client.json
      ↓
default language = en-GB
```

while the document JSON may provide:

```text
title = "Accessibility Test Document"
```

The resolver produces:

```text
DocumentMetadata
    title = ...
    language = en-GB
    pdfua_part = 1
    display_doc_title = true
```

---

# Phase 4 — Implement deterministic language resolution

Create:

```text
metadata/language_resolver.py
```

Initial priority should be simple:

```text
Explicit project/document language
        ↓
Client configuration
        ↓
System default
        ↓
Fail with validation error
```

For example:

```json
{
    "language": {
        "default": "en-GB",
        "required": true,
        "allow_overrides": true
    }
}
```

Do **not** let Gemini silently determine the final compliance language.

AI may provide a detected language as input, but the final language used for PDF/UA generation must be resolved deterministically.

---

# Phase 5 — Write Catalog `/Lang`

This is the first actual PDF modification.

The renderer must produce:

```text
PDF Catalog
    /Lang (en-GB)
```

For a document configured as American English:

```text
/Lang (en-US)
```

For British English:

```text
/Lang (en-GB)
```

### Important

Do not put `/Lang` only into the StructTree.

It needs to be associated with the document/catalog appropriately so PAC can identify the document's natural language.

Your current PAC reports explicitly show:

```text
Language
(no language)
```

for all three samples.   

---

# Phase 6 — Implement XMP metadata

Create:

```text
metadata/xmp_builder.py
```

Its only responsibility should be:

```text
DocumentMetadata
       ↓
XMP packet
```

The XMP should contain at minimum:

```text
dc:title
pdfuaid:part
```

and appropriate standard XMP/PDF metadata.

For PDF/UA-1:

```text
pdfuaid:part = 1
```

The generated PDF should have an actual `/Metadata` stream associated with the Catalog.

This directly targets the three repeated metadata failures in all three PAC reports.   

---

# Phase 7 — Synchronize PDF title

Do **not** assume XMP title alone is enough.

Set the traditional PDF document title as well:

```text
Info dictionary
    /Title (...)
```

and:

```text
XMP
    dc:title
```

Both should originate from:

```text
DocumentMetadata.title
```

Therefore:

```text
DocumentMetadata
       │
       ├── PDF /Title
       │
       └── XMP dc:title
```

This avoids the two metadata representations becoming inconsistent.

---

# Phase 8 — Implement `DisplayDocTitle`

Add:

```text
ViewerPreferences
    /DisplayDocTitle true
```

This should be generated automatically by the renderer when PDF/UA mode is enabled.

Do not depend on Acrobat's default behaviour.

Configuration could eventually support:

```json
{
    "pdfua": {
        "enabled": true,
        "part": 1,
        "display_doc_title": true
    }
}
```

For the current milestone:

```text
display_doc_title = true
```

should be the default.

---

# Phase 9 — Add metadata validation before PDF export

Create:

```text
metadata/metadata_validator.py
```

It should validate the **resolved metadata model before rendering**.

Example:

```text
Metadata Validation

✓ Title exists
✓ Language exists
✓ PDF/UA part configured
✓ DisplayDocTitle enabled
```

If:

```text
language = None
```

the pipeline should not silently generate a PDF.

Instead:

```text
ERROR
Document language is required for PDF/UA output.
```

This is much better than discovering the problem only after PAC.

---

# Phase 10 — Add PDF-level metadata validation

You need a second validation layer.

There are two different questions:

### Internal validation

Did our application resolve the metadata correctly?

```text
DocumentMetadata
```

### Output validation

Did the renderer actually write it correctly?

```text
Generated PDF
```

Therefore:

```text
Metadata Model Validator
             ↓
        PDF Renderer
             ↓
Generated PDF Validator
```

The second validator should inspect the generated PDF and verify:

```text
✓ Catalog /Lang exists
✓ Catalog /Metadata exists
✓ XMP dc:title exists
✓ XMP pdfuaid:part exists
✓ ViewerPreferences exists
✓ DisplayDocTitle = true
✓ PDF /Title exists
```

This catches renderer bugs.

---

# Phase 11 — Implement language-of-content handling

After the document-level `/Lang` works, investigate the remaining Natural Language failures.

Your PAC results are:

```text
Sample 1 → 58
Sample 2 → 93
Sample 3 → 1
```

  

Do not create 93 special-case fixes.

Create a deterministic language inheritance model:

```text
Document
    /Lang = en-GB
        ↓
Structure elements inherit
        ↓
Content inherits
```

Then support explicit overrides later:

```text
Document
 ├── P → en-GB
 ├── P → en-GB
 ├── Span → fr-FR
 └── P → en-GB
```

---

# Phase 12 — Handle language-bearing content separately

Once the default language is implemented, inspect whether the remaining PAC failures come from:

```text
Text
Alt text
ActualText
E
Annotations
Form fields
Outlines
Metadata
```

Do not assume all 58/93 failures are the same issue.

Your validator should report something like:

```text
Natural Language

11-001 Page content          PASS
11-002 Alt/ActualText        PASS
11-003 Outline entries       PASS
11-004 Annotation contents   PASS
11-005 Form field text       PASS
11-006 Metadata              PASS
```

That gives you a real diagnostic system rather than a generic:

```text
93 language errors
```

---

# Phase 13 — Add automated unit tests

Create:

```text
tests/
├── metadata/
│   ├── test_metadata_model.py
│   ├── test_metadata_resolver.py
│   ├── test_language_resolver.py
│   ├── test_xmp_builder.py
│   └── test_metadata_validator.py
```

### Test cases

#### Test 1 — language

```text
en-GB
```

must resolve correctly.

#### Test 2 — title

Title must be preserved.

#### Test 3 — PDF/UA identifier

```text
pdfuaid:part = 1
```

must be generated.

#### Test 4 — missing language

Must fail validation.

#### Test 5 — missing title

Must fail validation.

#### Test 6 — XMP generation

XMP must contain:

```text
dc:title
pdfuaid:part
```

#### Test 7 — viewer preference

Must produce:

```text
DisplayDocTitle = true
```

---

# Phase 14 — Add integration tests

Create:

```text
tests/integration/
    test_pdf_metadata_output.py
```

Pipeline:

```text
Test Semantic JSON
       ↓
Pipeline
       ↓
Renderer
       ↓
Generated PDF
       ↓
PDF metadata inspection
```

Check the actual PDF.

This is important because a unit test saying:

```python
metadata.language == "en-GB"
```

doesn't prove that:

```text
/Lang (en-GB)
```

actually exists in the final PDF.

---

# Phase 15 — Run the three real PAC samples

Use the exact three samples you provided.

For each:

```text
Input
 ↓
Current pipeline
 ↓
New metadata subsystem
 ↓
Tagged PDF
 ↓
PAC
```

Record:

```text
Before
```

and:

```text
After
```

Example:

```text
                    BEFORE      AFTER
Metadata             3 FAIL      0 FAIL
Language              58 FAIL     ?
Document settings      1 FAIL      0 FAIL
```

Don't declare success merely because the overall PAC score improved.

We want checkpoint-level evidence.

---

# Phase 16 — Update the GUI

Only after the backend works.

Add a new:

```text
Document Properties
```

section.

Display:

```text
Metadata

Title:
[ Accessibility Test Document ]

Language:
[ English (United Kingdom) ]

PDF/UA:
[ PDF/UA-1 ]

Display document title:
[✓]
```

And show status:

```text
✓ Metadata valid
✓ Language valid
✓ PDF/UA identifier valid
```

If something is missing:

```text
✗ Document language is missing
```

---

# Phase 17 — Add pipeline integration

Your existing pipeline currently has:

```text
Raw JSON
Normalized
Geometry
Visual
Detection
Analysis
Consistency
QA
Rules
```

Don't turn metadata into another giant semantic stage.

Instead add it around the rendering boundary:

```text
Rules
 ↓
Metadata Resolution
 ↓
Reading Order / Final Structure
 ↓
Tag Tree
 ↓
PDF Rendering
 ↓
Output Validation
```

If your current pipeline ordering makes Reading Order occur elsewhere, preserve the existing architecture. The important part is that **metadata resolution happens before PDF rendering**.

---

# Phase 18 — PAC regression gate

Eventually your pipeline should have a compliance gate:

```text
Generate PDF
      ↓
Internal validation
      ↓
PDF output validation
      ↓
PAC/manual validation
      ↓
Compliance report
```

The GUI should show:

```text
PDF/UA Readiness

Metadata             PASS
Language             PASS
Structure             PASS
Reading Order         PASS
Alternative Text      PASS
Document Settings     PASS

Overall:
READY FOR VALIDATION
```

Don't call it:

```text
100% PDF/UA compliant
```

until your validation scope actually supports that claim.

---

# Recommended implementation order

If you're going to implement this with your coding agent, give it tasks in this exact order:

```text
1. Inspect existing renderer and PDF object creation
        ↓
2. Create DocumentMetadata model
        ↓
3. Create MetadataResolver
        ↓
4. Create LanguageResolver
        ↓
5. Implement Catalog /Lang
        ↓
6. Implement XMP metadata stream
        ↓
7. Implement dc:title
        ↓
8. Implement pdfuaid:part
        ↓
9. Implement /Title synchronization
        ↓
10. Implement DisplayDocTitle
        ↓
11. Implement metadata model validator
        ↓
12. Implement generated-PDF metadata validator
        ↓
13. Implement language inheritance
        ↓
14. Diagnose remaining PAC natural-language checkpoints
        ↓
15. Unit tests
        ↓
16. Integration tests
        ↓
17. Run all 3 sample PDFs
        ↓
18. Compare PAC results
        ↓
19. GUI integration
        ↓
20. Only then return to remaining tagging rules
```

## The key rule for the implementation

**Do not let the coding agent modify unrelated tagging behaviour while implementing this.**

The scope should initially be:

```text
metadata/
language/
renderer metadata writing/
validation/
tests/
configuration/
```

Not:

```text
heading rules
table rules
list rules
figure detection
reading-order algorithms
Gemini extraction
```

That isolation matters because your current three PAC reports give you a clean baseline. If the next PAC run changes 20 unrelated things, you won't know what actually fixed the failures.

### First milestone

Your first concrete milestone should therefore be:

> **All three sample PDFs generate with a valid document title, `/Lang`, XMP metadata, PDF/UA-1 identification, and `DisplayDocTitle=true`, with automated tests proving that these values exist in the actual generated PDF.**

Once that passes, **then** we take the remaining PAC Natural Language, Fonts, Content, Structure Tree, and other failures one checkpoint at a time.



Should not change any existing functionality or logic, just add the missing functionality and logic.