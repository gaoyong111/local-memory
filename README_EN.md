# local-memory

**Local-first persistent memory for AI coding assistants — Chinese-friendly, verbatim storage.**

Store facts, preferences, and workflow knowledge on your machine. Optimized for **Chinese memories**: verbatim ingest (no LLM rewrite on write), hybrid search with Chinese-aware keyword tokenization + bge-m3 vectors + RRF fusion. Inject context via IDE hooks or query on demand through MCP. No cloud dependency — SQLite, Chroma, and Ollama on your machine.

[中文文档 → README.md](README.md)

---

## Why local-memory?

Most AI assistants forget everything between sessions. local-memory gives your agent a **durable, searchable memory layer** that stays on your hardware:

| Capability | What you get |
|------------|--------------|
| **Hybrid search** | Keyword (SQLite) + vector (Chroma / bge-m3) fused with weighted RRF |
| **Verbatim storage** | What you write is what gets stored — no LLM rewriting on ingest |
| **Write policies** | Category tags, structured formatting, LLM dedup on write |
| **Multi-pool** | Separate memory pools for work / personal / experiments |
| **Workspace scoping** | Per-repo read/write rules via `.cursor/memory.json` |
| **IDE integration** | MCP tools + prompt hooks for Cursor and Claude Code |
| **Web UI** | `mem_viewer` for browse, search, edit, and episodic grooming |

---

## Quick start

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) running locally (`bge-m3` for embeddings; optional LLM for dedup/grooming)
- `pip install -r requirements.txt`

### Install

```bash
git clone https://github.com/gaoyong111/local-memory.git
cd local-memory
pip install -r requirements.txt
bash scripts/setup.sh
```

`setup.sh` deploys runtime to `~/.memory/runtime/` and creates the default pool at `~/.memory/pools/default/`, copying example `config.json` and `.env` into the pool.

### Configure LLM / embedder (optional)

Edit the pool config copied by setup, or replace it:

```bash
cp configs/config_ollama.example.json ~/.memory/pools/default/config.json
# Remote LLM: configs/config_api.example.json (embedder still requires local Ollama)
#   echo 'NEWAPI_KEY=your-key' >> ~/.memory/pools/default/.env
#   llm block supports auto_follow=true: follows the current Claude session's online model, effective on cc-switch
ollama pull bge-m3   # required for search with either config
```

### Smoke test

```bash
export MEMORY_DIR=~/.memory PYTHONPATH=~/.memory/runtime
python3 ~/.memory/runtime/search_context.py 'test query'
```

### Connect your IDE

| IDE | Guide |
|-----|-------|
| Cursor | [docs/cursor-setup.md](docs/cursor-setup.md) |
| Claude Code | [docs/claude-code-setup.md](docs/claude-code-setup.md) |

Register the MCP server `local-memory` pointing at `~/.memory/runtime/mcp_server.py`, then optionally enable the prompt hook for automatic context injection.

---

## Architecture at a glance

```text
 IDE Layer          Hook (L2) · MCP · mem_viewer
       │
 Policy Layer       add_policy (B/D/E) · grooming · lineage
       │
 Service Layer      memory_store · llm_client · pool_manager · workspace_config
       │
 Storage Layer      active_memories.db · Chroma · history · deleted_archive
```

**Memory pool** — a self-contained directory (`~/.memory/pools/<id>/`) with its own databases, Chroma collection, and config.

**Project** — a logical tag on each memory (`""` = global, `"my-app"` = project-scoped). Used for filtering and retrieval bias.

**Workspace binding** — optional per-repo config that limits which projects a workspace can read/write.

Details → [docs/architecture.md](docs/architecture.md) · [docs/v2-design.md](docs/v2-design.md)

---

## MCP tools (9 total)

| Tool | Description |
|------|-------------|
| `add_memory` | Add memory with category metadata; runs dedup policy |
| `search_memory` | Hybrid search, optional project filter |
| `get_all_memories` | List memories, optional project filter |
| `delete_memory` | Delete by ID (reason required) |
| `retry_pending` | Retry failed writes / sync operations |
| `run_episodic_grooming` | Batch episodic quality review |
| `confirm_grooming` | Confirm grooming suggestion |
| `list_pools` | List all memory pools |
| `switch_pool` | Switch the active memory pool |

---

## Documentation

| Document | Contents |
|----------|----------|
| [CHANGELOG.md](CHANGELOG.md) | Releases and breaking changes |
| [architecture.md](docs/architecture.md) | Write policies, hybrid search, storage model |
| [v2-design.md](docs/v2-design.md) | Concept model, module map, design decisions |
| [workspace-binding.md](docs/workspace-binding.md) | Per-repo read/write permissions |
| [mem-viewer-design.md](docs/mem-viewer-design.md) | Web UI specification |
| [cursor-setup.md](docs/cursor-setup.md) | Cursor MCP + hooks |
| [claude-code-setup.md](docs/claude-code-setup.md) | Claude Code MCP + hooks |
| [daily-review-integration.md](docs/daily-review-integration.md) | Optional daily review (`review_helpers.py`: `git-log`, `tool-stats`, `diff`, etc.; `INSTALL_DAILY_REVIEW_HELPERS=1` to deploy to skill) |
| [history.md](docs/history.md) | Project lineage (predecessor mem0-local-enhanced, read-only) |

---

## Pool management

```bash
export MEMORY_DIR=~/.memory PYTHONPATH=~/.memory/runtime
python3 ~/.memory/runtime/scripts/pool_cli.py list
python3 ~/.memory/runtime/scripts/pool_cli.py create my-pool
python3 ~/.memory/runtime/scripts/pool_cli.py switch default
python3 ~/.memory/runtime/scripts/pool_cli.py backup default
```

Or use MCP `list_pools` / `switch_pool`.

---

## mem_viewer

Local web UI for browsing and editing memories:

```bash
bash ~/.memory/runtime/mem_viewer.sh
# optional: pass repo root to enable workspace write warnings
bash ~/.memory/runtime/mem_viewer.sh /path/to/your/project
```

---

## Development

[![Tests](https://github.com/gaoyong111/local-memory/actions/workflows/test.yml/badge.svg)](https://github.com/gaoyong111/local-memory/actions/workflows/test.yml)

```bash
pip install -r requirements-dev.txt
export MEMORY_DIR=~/.memory PYTHONPATH=src
python3 -m unittest discover -s tests -v
```

After changing source under `src/` or `configs/`, redeploy (runtime holds config templates for `pool_cli create`; skipping setup breaks pool seeding):

```bash
bash scripts/setup.sh
```

---

## Troubleshooting

### Chroma multi-process / grooming returns nothing

Chroma uses a local SQLite lock. If **MCP is already running**, launching `scripts/episodic_grooming_run.py` separately may return zero suggestions or fail while competing for the client.

**Prefer:** call MCP tool `run_episodic_grooming` while MCP is up; use the CLI script only when MCP is stopped. See [architecture.md](docs/architecture.md) and [daily-review-integration.md](docs/daily-review-integration.md).

### Hook / MCP still behaves like an old build

Runtime code lives under `~/.memory/runtime/`. After editing `src/` or `configs/`, rerun `bash scripts/setup.sh`, then Reload Window or restart MCP.

Hook injection uses header `[local-memory 自动注入的相关记忆]` with lines `- #N [scope] (source) text` (`format_results_lines`). `search_context.py` uses `[local-memory 相关记忆]`. MCP `search_memory` uses `format_mcp_search_output` (id / kw / vec / rrf scores, no injection header).

---

## Data layout

```text
~/.memory/
├── registry.json              # active pool registry
├── runtime/                   # deployed Python modules
└── pools/
    └── default/
        ├── pool.meta.json
        ├── config.json
        ├── active_memories.db
        ├── history.db
        ├── deleted_archive.db
        ├── chroma_db/
        ├── pending/
        └── sync_pending/
```

All data stays local. Back up a pool by copying its directory or using `pool_cli.py backup`.

---

## License

[MIT](LICENSE)
