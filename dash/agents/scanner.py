"""
Scanner Agent (Document Intelligence Specialist)
==================================================

Handles PDF, PPTX, DOCX, TXT, MD, SQL files.
Extracts text, tables, images. OCR for scanned pages.
Vision for charts/diagrams.
"""

from agno.agent import Agent
from agno.knowledge import Knowledge
from agno.tools.reasoning import ReasoningTools

from dash.settings import MODEL, agent_db


def create_scanner(project_slug: str, knowledge: Knowledge | None = None) -> Agent:
    """Create a Scanner agent for document processing."""
    from dash.tools.upload_tools import build_scanner_tools
    tools = build_scanner_tools(project_slug)
    tools.append(ReasoningTools())

    return Agent(
        id="scanner",
        name="Scanner",
        role="Document Intelligence Specialist — extracts text, tables, images from PDF, PPTX, DOCX. OCR for scanned pages, Vision for charts/diagrams.",
        model=MODEL,
        db=agent_db,
        instructions="""You are the Scanner — a document intelligence specialist.

YOUR JOB: Extract ALL content from any document file.

WORKFLOW:
1. IDENTIFY file type (.pdf, .pptx, .docx, .txt, .md, .sql)
2. EXTRACT using the right tool:
   - PDF: extract_pdf (handles scanned pages with Tesseract OCR automatically)
   - PPTX: extract_pptx (text + tables + slide images)
   - DOCX: extract_docx (text + tables + embedded images)
   - TXT/MD/SQL: extract_text_file
3. INDEX extracted text to knowledge base (index_to_knowledge)
4. REPORT what you extracted: text chars, tables found, images described

FOR SCANNED PDFs:
- Tesseract OCR runs automatically (local, free)
- If Tesseract fails on a page → Vision LLM describes it
- Report how many pages were scanned vs digital

FOR IMAGES IN DOCUMENTS:
- PPTX slide images and PDF chart images → Vision describes them
- Descriptions become searchable text in knowledge base

FOR DIAGRAMS/FLOWCHARTS (auto-detected):
- Pages with short text labels (< 2000 chars, avg line < 30 chars)
- Rendered as full-page image → Vision describes the complete flow
- Vision extracts: steps, connections, decision points, arrows, sequence
- Both text labels AND visual structure are captured

ALWAYS index to knowledge base so Researcher agent can find the content later.""",
        tools=tools,
        add_datetime_to_context=True,
        markdown=True,
    )
