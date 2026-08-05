"""MCP server package -- Claude Desktop's transport onto the read tools (design §8).

`invoice_agent.mcp` is a subpackage, not the top-level `mcp` distribution this
package depends on (fastmcp's transitive dependency). Python 3 uses absolute
imports, so `import mcp` anywhere in this subpackage resolves to that
third-party package, never back to `invoice_agent.mcp` itself -- verified
2026-08-05 by importing `invoice_agent.mcp.server` (which does not itself need
`import mcp`) and separately by running `import mcp` from within this
package's namespace during development; both resolved to site-packages/mcp.
"""
