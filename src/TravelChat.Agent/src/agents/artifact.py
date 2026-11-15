from src.agents.base import AgentSpec, ToolSpec

SPEC = AgentSpec(
    domain="artifact",
    name="artifact_agent",
    description="Makes the current trip plan available as a downloadable file.",
    when_to_use="the user asks for the plan as a file, such as a document or a spreadsheet.",
    how_to_use="Send one request naming the format: html, docx, excel (xlsx) or csv.",
    tools=(
        ToolSpec(
            "artifact__export",
            "Creates the file of the latest plan card in one format: html, docx, xlsx (Excel) or "
            "csv. The app shows the download link itself.",
        ),
    ),
    tier="helper",
    max_calls=1,
)
