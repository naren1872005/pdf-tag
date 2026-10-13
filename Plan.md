Prompt: Build a Professional Rule-Based PDF Accessibility Tagging Engine
Project Name

PDF Accessibility Tagging Engine Pro

Objective

Build a professional desktop application that converts scanned PDFs into fully PDF/UA-compliant tagged PDFs.

The application must use a hybrid architecture:

AI only performs semantic extraction (already implemented).
The tagging engine must be completely deterministic and rule-based.
Every PDF/UA rule must be enforced by code.
Every stage must be modular, testable, and independently executable.

The system must be designed for future enterprise use and support multiple client configurations.

Existing Pipeline

I already have these two Python scripts:

Stage 1
Scanned PDF

↓

Gemini OCR + Layout Extraction

↓

Semantic JSON

This stage already works.

It produces JSON containing:

Pages
Reading order
Bounding boxes
Paragraphs
Headings
Figures
Tables
Captions
Metadata
Alt text
Confidence

Do NOT rewrite this stage.

Stage 2
Semantic JSON

↓

Tagged PDF

This stage also exists.

It already:

writes text
creates Figure tags
creates Table tags
generates Alt Text
builds StructTree
creates ParentTree
writes MCIDs

Do NOT rewrite from scratch.

Instead, redesign it into a modular architecture.

New Pipeline
Scanned PDF

↓

OCR

↓

Semantic Extraction
(existing)

↓

Semantic JSON

↓

Preprocessing

↓

Document Analysis

↓

Document Graph

↓

Rule Engine

↓

Reading Order Engine

↓

Tag Tree Builder

↓

PDF Renderer

↓

Validation Engine

↓

Compliance Report

↓

Tagged PDF
GUI Requirements

Develop a modern desktop application.

Technology:

Python 3.12+
PySide6 (preferred)
MVC architecture
Dark / Light Theme
Responsive UI
Threaded background processing
Logging console
Progress bars

The GUI should look like a professional Adobe Acrobat accessibility tool.

Main Window Layout
---------------------------------------------------------

Menu Bar

---------------------------------------------------------

Toolbar

---------------------------------------------------------

LEFT PANEL

Project Explorer

Pages

Elements

Rules

Validation

---------------------------------------------------------

CENTER PANEL

Current Page Preview

Bounding Boxes

Reading Order

Tag Tree Preview

---------------------------------------------------------

RIGHT PANEL

Properties

Selected Element

Tag

Attributes

Alt Text

Heading Level

Bounding Box

---------------------------------------------------------

BOTTOM

Console

Progress

Validation Messages

---------------------------------------------------------
Workflow
Step 1

Select PDF

↓

Generate JSON

(or load existing JSON)

↓

Display page preview

Step 2

Build Document Graph

Display

Document

    Page 1

        Heading

        Paragraph

        Figure

        Table

...
Step 3

Run Rule Engine

Display every rule executed.

Example

✓ Rule 001

Only One H1

✓ Passed

------------------

✓ Rule 002

Heading Hierarchy

Fixed

------------------

✓ Rule 003

Caption Rule

Passed
Step 4

Build Tag Tree

Preview

Document

    H1

    P

    Figure

    P

    Table

    L

    Formula
Step 5

Export Tagged PDF

Complete Project Structure
PDF_Tagging_Engine/

│

├── app/

│     main.py

│

├── gui/

│     main_window.py

│     toolbar.py

│     page_viewer.py

│     tree_view.py

│     property_panel.py

│     validation_panel.py

│     console.py

│     dialogs/

│

├── parser/

│     json_loader.py

│     schema_validator.py

│

├── preprocessing/

│     text_normalizer.py

│     bbox_validator.py

│     duplicate_merger.py

│     metadata_normalizer.py

│

├── analysis/

│     document_context.py

│     font_analysis.py

│     repetition_detector.py

│     page_template_detector.py

│     heading_statistics.py

│     layout_analysis.py

│

├── graph/

│     node.py

│     edge.py

│     document_graph.py

│

├── semantic/

│     classifier.py

│     heading_classifier.py

│     artifact_classifier.py

│     caption_classifier.py

│     list_classifier.py

│     formula_classifier.py

│

├── rules/

│     base_rule.py

│

│     registry.py

│

│     heading_rules.py

│     paragraph_rules.py

│     artifact_rules.py

│     figure_rules.py

│     table_rules.py

│     list_rules.py

│     toc_rules.py

│     formula_rules.py

│     link_rules.py

│     footnote_rules.py

│     metadata_rules.py

│     reading_order_rules.py

│

├── reading_order/

│     ordering.py

│     column_detector.py

│     page_sorter.py

│

├── tree/

│     tree_builder.py

│     structure_tree.py

│

├── renderer/

│     pdf_renderer.py

│     mcid_writer.py

│     parent_tree.py

│     struct_tree.py

│

├── validator/

│     validator.py

│     pdfua_validator.py

│     report_generator.py

│

├── reports/

│

├── config/

│     client.json

│

├── tests/

│

├── assets/

│

└── docs/
Rule Engine

Each rule must be completely isolated.

Never create one massive rule file.

Every rule must inherit

BaseRule

Example

class OnlyOneH1Rule(BaseRule):

    name

    priority

    validate()

    repair()

The Rule Engine should automatically register every rule.

Rule Registry

↓

Priority Sort

↓

Execute

↓

Validation

↓

Repair

↓

Log

↓

Next Rule
Rule Categories

Create separate folders for:

Document Rules

Heading Rules

Paragraph Rules

Artifact Rules

Figure Rules

Table Rules

List Rules

Formula Rules

Link Rules

Footnote Rules

Span Rules

TOC Rules

Metadata Rules

Reading Order Rules

Validation Rules
Reading Order Engine

Must be completely independent.

Responsibilities

Detect single-column pages.
Detect multi-column layouts.
Detect sidebars.
Detect pull quotes.
Detect floating figures.
Preserve table reading order.
Preserve list reading order.
Preserve caption order.
Build final logical reading sequence.
Document Analysis Engine

Before applying any rules, analyze the entire document.

Compute:

Largest font size
Font hierarchy
Most common paragraph font
Repeated headers
Repeated footers
Repeated logos
Repeated page numbers
Multi-column pages
Landscape pages
Portrait pages
Heading candidates
Caption candidates
Figure statistics
Table statistics

Store everything in

DocumentContext

Every rule should use this shared context instead of rescanning the document.

Validation Engine

Validate every PDF/UA rule.

Generate:

Errors

Warnings

Auto Repairs

Skipped Rules

Compliance Score

Execution Time
Compliance Report

Generate

HTML

PDF

JSON

Example

=====================================

PDF Accessibility Report

=====================================

Document

PASS

Metadata

PASS

Heading Rules

PASS

Paragraph Rules

PASS

Figure Rules

PASS

Table Rules

PASS

List Rules

PASS

Formula Rules

PASS

Link Rules

WARNING

2 Links Missing Alt Text

Artifact Rules

PASS

Reading Order

PASS

Overall Score

99.2%

=====================================
GUI Features

The application should include:

Open PDF
Load Existing JSON
Generate JSON
Generate Tagged PDF
Run Rule Engine
Run Validation
Export Compliance Report
View Tag Tree
View Reading Order
View Bounding Boxes
View Alt Text
View Element Properties
Search Elements
Filter by Tag Type
Zoom In/Out
Previous/Next Page
Rule Execution Log
Validation Messages
Console Output
Dark Mode / Light Mode
Client Configuration Editor
Settings Dialog
Batch Processing (multiple PDFs)
Keyboard shortcuts for common actions
Design Principles
Modular architecture (single responsibility per module).
No business logic inside GUI classes.
Rule Engine must be fully data-driven.
All configurable behavior must come from client.json.
Every rule should be independently testable with unit tests.
Maintain detailed logs for debugging and auditing.
Keep AI responsibilities limited to semantic extraction; all PDF/UA compliance, structure building, repairs, and validation must be deterministic.

The goal is to build a professional, extensible PDF Accessibility Tagging Engine comparable in architecture to enterprise accessibility remediation tools, with a clean separation between semantic analysis, rule enforcement, rendering, validation, and user interface.

# Metadata & PDF/UA Settings (To Be Implemented)
Document-level metadata requirements for full PDF/UA compliance:
- `/Lang` (Language)
- `dc:title` (Document Title)
- `DisplayDocTitle` (Viewer Preference)
- `pdfuaid` (PDF/UA Identifier)