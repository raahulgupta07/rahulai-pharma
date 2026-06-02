"""
Vision Agent (Visual Recognition Specialist)
==============================================

Handles JPG, PNG image files.
Tesseract OCR first (free, local), Vision LLM for charts/diagrams.
"""

from agno.agent import Agent
from agno.tools.reasoning import ReasoningTools

from dash.settings import MODEL, agent_db


def create_vision_agent() -> Agent:
    """Create a Vision agent for image processing."""
    from dash.tools.upload_tools import build_vision_tools
    tools = build_vision_tools()
    tools.append(ReasoningTools())

    return Agent(
        id="vision",
        name="Vision",
        role="Visual Recognition Specialist — OCR text from images, describe charts/diagrams/photos, classify image content.",
        model=MODEL,
        db=agent_db,
        instructions="""You are Vision — a visual recognition specialist.

YOUR JOB: Extract ALL information from any image file.

WORKFLOW:
1. CLASSIFY the image first (classify_image):
   - "document" → has readable text → use OCR
   - "certificate" → has text + stamps → OCR + Vision
   - "chart_or_photo" → no text → use Vision to describe

2. EXTRACT based on classification:
   - Document/Certificate: ocr_image (Tesseract, free, local)
   - Chart/Graph: describe_image (Vision LLM, extracts data points)
   - Photo: describe_image (Vision LLM, describes content)
   - Diagram: describe_image (Vision LLM, describes structure)

3. REPORT what you found:
   - For OCR: extracted text
   - For charts: data points, trends, labels
   - For photos: what's visible, any text/signage

ALWAYS try OCR first (free). Only use Vision LLM if OCR gets no text or
if the image is clearly a chart/diagram that needs AI understanding.""",
        tools=tools,
        add_datetime_to_context=True,
        markdown=True,
    )
