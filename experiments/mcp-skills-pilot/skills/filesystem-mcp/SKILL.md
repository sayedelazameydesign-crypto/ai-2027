---
name: filesystem-mcp
description: >-
  Repository-scoped filesystem operations over MCP (server: @modelcontextprotocol/server-filesystem).
  Use when the task must read, list, search, copy, append, or edit files inside the allowed root.
  Loads tool schemas on demand instead of keeping all 16 definitions in every model turn.
---

# filesystem-mcp

MCP stdio server. Start: `npx -y @modelcontextprotocol/server-filesystem@2.4.0 <ROOT>`
(newline-delimited JSON-RPC; see `scripts/call.py`). **Version pinned to 2.4.0**
(includes the directory-listing symlink fix `GHSA-rq2p-q7c4-6g27`; npm integrity
`sha512-BoT8Gws5MsetfAey4vDyuzy8WYQES6mGYLSShuqSNGAC0X0NBV4p78/R78KfW4RkxtWz+zxhS9jwhzJrQUTt2Q==`).
The server's own `serverInfo.version` reports `1.0.0` — pin by npm package
version, not by serverInfo. Known limit: Windows symlink/Junction race is NOT
closed by 2.4.0 (stronger on Linux/macOS). Official run requirement: Node.js 24+.

**Approval gate (enforced in the wrapper, not in prose):** `scripts/call.py`
refuses the six mutating tools (`write_file`, `edit_file`, `append_file`,
`copy_file`, `move_file`, `create_directory`) unless the execution layer sets
`AI2027_SKILL_WRITE_APPROVAL=1` in the environment after explicit user
approval. Read-only tool calls are unaffected. Without the token the wrapper
returns `write approval required` (rc=1) and never contacts the server.

## Tools

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

### `write_file`

Create a new file or completely overwrite an existing file with new content. Use with caution as it will overwrite existing files without warning. Handles text content with proper encoding. Only works within allowed directories.

Parameters:

- `path` (string)
- `content` (string)

### `append_file`

Append text to the end of a file, creating the file if it does not exist. The existing content is never changed or replaced. The text is appended exactly as given, with no newline added before or after it, so end it with a newline if the next append should start on a new line. Only works within allowed directories.

Parameters:

- `path` (string)
- `content` (string)

### `edit_file`

Make line-based edits to a text file. Edits apply in order, each to the result of the ones before it, and each replaces the first match of its oldText with newText. oldText must match the file's text exactly, except that line endings (LF or CRLF) never matter and, when there is no exact match, a run of whole lines matches if each line is equal once leading and trailing whitespace is ignored (newText then takes the file's indentation). Whitespace inside a line must match. If any edit has no match, nothing is written and the error shows the closest lines of the file, numbered, to copy oldText from. Returns a git-style diff showing the changes made. Only works within allowed directories.

Parameters:

- `path` (string)
- `edits` (array)
- `dryRun` (boolean) (optional)

### `create_directory`

Create a new directory or ensure a directory exists. Can create multiple nested directories in one operation. If the directory already exists, this operation will succeed silently. Perfect for setting up directory structures for projects or ensuring required paths exist. Only works within allowed directories.

Parameters:

- `path` (string)

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

### `move_file`

Move or rename files and directories. Can move files between directories and rename them in a single operation. If the destination exists, the operation will fail. Works across different directories and can be used for simple renaming within the same directory. Both source and destination must be within allowed directories.

Parameters:

- `source` (string)
- `destination` (string)

### `copy_file`

Copy a file to a new location, without reading its content into the conversation. Copies one regular file, keeping its permissions and modification time; directories are not copied. If the destination exists, the operation will fail: it never overwrites. Both source and destination must be within allowed directories.

Parameters:

- `source` (string)
- `destination` (string)

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

