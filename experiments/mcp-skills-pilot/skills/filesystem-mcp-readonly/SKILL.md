---
name: filesystem-mcp-readonly
description: >-
  DEFAULT filesystem skill: read-only inspection over MCP (server started with
  --read-only, so write tools are not even registered). Use for reading, listing,
  searching, and inspecting files inside the allowed root. Mutating operations are
  out of scope here — they belong to the approval-gated write skill.
---

# filesystem-mcp-readonly (DEFAULT)

MCP stdio server, **read-only mode**:
`npx -y @modelcontextprotocol/server-filesystem@2.4.0 --read-only <ROOT>`
(newline-delimited JSON-RPC; see `scripts/call.py`).

Version pinned to **2.4.0** (directory-listing symlink fix `GHSA-rq2p-q7c4-6g27`.
npm integrity `sha512-BoT8Gws5MsetfAey4vDyuzy8WYQES6mGYLSShuqSNGAC0X0NBV4p78/R78KfW4RkxtWz+zxhS9jwhzJrQUTt2Q==`.
Known limit: Windows symlink/Junction race not closed by2.4.0; stronger on Linux/macOS.
`serverInfo.version` reports `1.0.0` — pin by npm package version.
Official run requirement: Node.js 24+ via npx.

## Tools (10 — read surface only)

### `read_file`

Read the complete contents of a file as text. DEPRECATED: Use read_text_file instead.

Parameters:

- `path` (string)
- `tail` (integer) (optional)
- `head` (integer) (optional)

### `read_text_file`

Read the complete contents of a file from the file system as text. Handles various text encodings and provides detailed error messages if the file cannot be read. Use this tool when you need to examine the contents of a single file. Use the 'head' parameter to read only the first N lines of a file, or the 'tail' parameter to read only the last N lines of a file. Operates on the file as text regardless of extension. Only works within allowed directories.

Parameters:

- `path` (string)
- `tail` (integer) (optional)
- `head` (integer) (optional)

### `read_media_file`

Read a file and return it as a base64-encoded content block with its MIME type. Image and audio files are returned as image/audio content; any other file type is returned as an embedded resource. Only works within allowed directories.

Parameters:

- `path` (string)

### `read_multiple_files`

Read the contents of multiple files simultaneously. This is more efficient than reading files one by one when you need to analyze or compare multiple files. Each file's content is returned with its path as a reference. Failed reads for individual files won't stop the entire operation. Only works within allowed directories.

Parameters:

- `paths` (array) [minItems=1]

### `list_directory`

List the files and directories directly inside a path, each marked [FILE] or [DIR]. Use it to see what a directory contains; when you also need each entry's size or want entries sorted by size, use list_directory_with_sizes instead, and for a recursive view use directory_tree. Returns one line per entry. Only works within allowed directories.

Parameters:

- `path` (string)

### `list_directory_with_sizes`

List the files and directories directly inside a path with each file's size, marked [FILE] or [DIR], sorted by name or by size, largest first (sortBy). Use it when sizes matter, such as finding the largest files; for names only, use list_directory, which returns less text. Returns one line per entry, followed by the totals. Only works within allowed directories.

Parameters:

- `path` (string)
- `sortBy` (string) (optional)

### `directory_tree`

Get a recursive tree view of files and directories as a JSON structure. Each entry includes 'name', 'type' (file/directory), and 'children' for directories. Files have no children array, while directories always have a children array (which may be empty), except at the last level when 'depth' limits the tree: those directories are not read and have no children array. The output is formatted with 2-space indentation for readability. Only works within allowed directories.

Parameters:

- `path` (string)
- `excludePatterns` (array) (optional)
- `depth` (integer) (optional)

### `search_files`

Find files and directories by NAME, not by content: this tool never reads or searches inside files. It walks the tree under 'path' and matches a glob pattern against each entry's path relative to 'path' (case-sensitive). Use '*.ext' to match entries directly under 'path', and '**/*.ext' or '**/name' to match at any depth. Returns full paths to all matching items. Great for finding files when you don't know their exact location. Only searches within allowed directories.

Parameters:

- `path` (string)
- `pattern` (string)
- `excludePatterns` (array) (optional)

### `get_file_info`

Retrieve detailed metadata about a file or directory. Returns comprehensive information including size, creation time, last modified time, permissions, type and hard-link count. This tool is perfect for understanding file characteristics without reading the actual content. Only works within allowed directories.

Parameters:

- `path` (string)

### `list_allowed_directories`

Returns the list of directories that this server is allowed to access. Subdirectories within these allowed directories are also accessible. Use this to understand which directories and their nested paths are available before trying to access files.

