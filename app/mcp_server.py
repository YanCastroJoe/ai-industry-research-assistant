from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .docflow import retrieve_documents, verify_citations


mcp = FastMCP("DocFlow Evidence Tools")


@mcp.tool()
def retrieve_project_evidence(query: str, source_text: str) -> dict:
    """Retrieve traceable project evidence for a collaboration goal."""
    return {"evidence": retrieve_documents(query, source_text)}


@mcp.tool()
def verify_document_citations(artifacts: dict[str, str], evidence: list[dict[str, str]]) -> dict:
    """Verify that generated document citations point to the supplied evidence IDs."""
    return verify_citations(artifacts, evidence)


if __name__ == "__main__":
    mcp.run(transport="stdio")
