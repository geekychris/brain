# VaultForge

**Local LLM-powered second brain / knowledge compiler.**

VaultForge ingests your documents, notes, PDFs, and web pages, then uses a local LLM to compile them into a navigable, searchable, Obsidian-compatible Markdown knowledge base with concept extraction, entity linking, and full-text search.

> The LLM is not the final brain; the vault is the brain.

```mermaid
flowchart LR
    A(Capture) --> B(Compile) --> C(Link) --> D(Search) --> E(Ask) --> F(Synthesize)
    style A fill:#7dcfff,stroke:#7dcfff,color:#1a1b26
    style B fill:#bb9af7,stroke:#bb9af7,color:#1a1b26
    style C fill:#9ece6a,stroke:#9ece6a,color:#1a1b26
    style D fill:#7aa2f7,stroke:#7aa2f7,color:#1a1b26
    style E fill:#e0af68,stroke:#e0af68,color:#1a1b26
    style F fill:#ff9e64,stroke:#ff9e64,color:#1a1b26
```

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- **A local LLM server** — any OpenAI-compatible endpoint (llama.cpp, vLLM, Ollama)

### Install

```bash
# Clone and install
cd vaultforge_second_brain
./scripts/install.sh

# Or manually:
pip install -e ".[dev]"
```

### Create a Vault and Start

```bash
# Initialize a new vault
secondbrain init ~/SecondBrain

# Start the web UI
secondbrain serve --vault ~/SecondBrain --port 8184
```

Open **http://localhost:8184** in your browser.

### One-Line Start (after install)

```bash
./scripts/start.sh ~/SecondBrain
```

---

## Architecture

```mermaid
graph TD
    UI["Web UI (FastAPI)<br/>Dashboard | Search | Graph | Notes | Ask | Settings"]

    subgraph Core["Second Brain Core"]
        Ingest["Ingestion<br/>Pipeline"]
        Compiler["Compiler<br/>Pipeline"]
        Query["Query Engine<br/>+ Chat"]
        Raw["Raw Store"]
        Vault["Markdown<br/>Vault"]
        Indexes["FTS5 + Graph<br/>Indexes"]
        EG["Entity Graph"]
        HC["Health Checker"]
        Ann["Annotations"]
    end

    LLM["Local LLM<br/>llama.cpp / vLLM / Ollama"]

    UI --> Core
    Ingest --> Raw
    Ingest --> Compiler
    Compiler --> Vault
    Compiler --> Indexes
    Compiler --> EG
    Vault --> Query
    Indexes --> Query
    Core --> LLM

    style UI fill:#24283b,stroke:#7aa2f7,color:#c0caf5
    style LLM fill:#24283b,stroke:#bb9af7,color:#c0caf5
    style Core fill:#1a1b26,stroke:#3b4261,color:#c0caf5
```

### Data Flow

```mermaid
flowchart LR
    A["Drop files<br/>URLs, text"] --> B["Ingest"]
    B --> C["Store raw<br/>artifact"]
    B --> D["Queue<br/>compile job"]
    D --> E["LLM<br/>Summarize"]
    E --> F["Extract<br/>entities"]
    E --> G["Extract<br/>concepts"]
    F --> H["Entity<br/>Registry"]
    G --> I["Concept<br/>Notes"]
    E --> J["Source<br/>Note"]
    J --> K["FTS5<br/>Index"]
    I --> K
    K --> L["Search /<br/>Browse / Ask"]

    style E fill:#2d1f54,stroke:#bb9af7,color:#c0caf5
```

### Storage

```mermaid
graph TD
    V["vault/"]
    V --> Inbox["inbox/<br/><i>drop files here</i>"]
    V --> Raw["raw/<br/><i>archived originals<br/>never modified</i>"]
    V --> Compiled["compiled/"]
    V --> Daily["daily/<br/><i>journal notes</i>"]
    V --> System["system/"]

    Compiled --> Sources["sources/<br/><i>one per document</i>"]
    Compiled --> Concepts["concepts/<br/><i>extracted concepts</i>"]
    Compiled --> Projects["projects/"]
    Compiled --> People["people/"]
    Compiled --> Maps["maps/<br/><i>index notes</i>"]

    System --> DB["vaultforge.db<br/><i>SQLite + FTS5</i>"]
    System --> Reports["health-reports/"]

    style V fill:#24283b,stroke:#7aa2f7,color:#c0caf5
    style Compiled fill:#24283b,stroke:#9ece6a,color:#c0caf5
    style System fill:#24283b,stroke:#e0af68,color:#c0caf5
```

The Markdown vault is the source of truth. The SQLite database is disposable — it can always be rebuilt from the files.

---

## Web UI

Start with `secondbrain serve --vault <path> --port <port>`.

### Pages

| Page | URL | Description |
|------|-----|-------------|
| **Dashboard** | `/` | Stats, recent notes/sources, compile button |
| **Notes** | `/notes` | Browse all compiled notes, filter by type |
| **Note Detail** | `/notes/<id>` | Metadata, content, links, "Read Full Document" |
| **Concepts** | `/concepts` | All concepts sorted by mention count |
| **Concept Detail** | `/concepts/<name>` | Notes mentioning concept, co-mentioned concepts |
| **Entities** | `/entities` | Searchable entity registry with type filters |
| **Entity Detail** | `/entities/<id>` | Notes mentioning entity, related entities |
| **Graph** | `/graph` | Interactive D3 force graph of concept clusters |
| **Search** | `/search` | Full-text search with facets and query builder |
| **Ingest** | `/ingest` | Upload files, paste URLs, write quick notes |
| **Ask Vault** | `/ask` | Question answering with source citations |
| **Activity Log** | `/log` | Live compile/ingest progress |
| **Health** | `/health` | Orphan notes, broken links, stale content |
| **Vaults** | `/vaults` | Register and switch between multiple vaults |
| **Settings** | `/settings` | LLM backend configuration |

### Document Viewer

Click "Read" on any note to open the viewer:
- **Markdown** — rendered with clickable `[[wikilinks]]`, toggle to raw source
- **PDF** — embedded inline viewer
- **Text/code** — syntax display

Every document has an **annotation toolbar**:
- **Star** — bookmark important notes (searchable via facets)
- **Labels** — categorize with custom labels (e.g. "review-needed", "project-alpha")
- **User Tags** — your own tag taxonomy, separate from LLM-extracted tags
- **Summarize** — one-click LLM summary, saved and displayed inline

### Search

```mermaid
flowchart LR
    Q["User Query"] --> Parse["Parse query<br/>boolean / phrase / field"]
    Parse --> FTS["SQLite FTS5<br/>BM25 ranking"]
    FTS --> Filter["Apply facet<br/>filters"]
    Filter --> Rank["Score &<br/>snippet"]
    Rank --> Results["Results +<br/>Facets"]

    Q --> Sem{"Semantic<br/>mode?"}
    Sem -->|Yes| Embed["Embed query<br/>via LLM"]
    Embed --> VecSearch["Cosine<br/>similarity"]
    VecSearch --> Results

    style FTS fill:#24283b,stroke:#7aa2f7,color:#c0caf5
    style Sem fill:#24283b,stroke:#bb9af7,color:#c0caf5
```

FTS5-powered search with:
- **Boolean operators** — `kafka AND partitions`, `kafka OR redis`, `kafka NOT zookeeper`
- **Exact phrases** — `"machine learning"`
- **Prefix matching** — `mach*`
- **Proximity** — `NEAR(kafka partitions, 5)`
- **Field-specific** — `title : kafka`, `tags : arduino`
- **Combined** — `title : "event sourcing" AND tags : architecture`
- **Faceted filtering** — note type, tags, confidence, starred, labels, user tags
- **Query Builder** — visual clause builder for complex queries
- Porter stemming — "running" matches "run", "runs"

### Concept Graph

```mermaid
graph TD
    ML["Machine Learning"]
    DL["Deep Learning"]
    NLP["Natural Language<br/>Processing"]
    LLM["Large Language<br/>Models"]
    TF["TensorFlow"]
    KB["Knowledge Base"]

    ML --- DL
    ML --- NLP
    DL --- LLM
    NLP --- LLM
    ML --- TF
    LLM --- KB

    style ML fill:#bb9af7,stroke:#bb9af7,color:#1a1b26
    style DL fill:#bb9af7,stroke:#bb9af7,color:#1a1b26
    style NLP fill:#bb9af7,stroke:#bb9af7,color:#1a1b26
    style LLM fill:#7dcfff,stroke:#7dcfff,color:#1a1b26
    style TF fill:#9ece6a,stroke:#9ece6a,color:#1a1b26
    style KB fill:#e0af68,stroke:#e0af68,color:#1a1b26
```

Interactive D3 force-directed visualization:
- Nodes sized by mention count, colored by type
- **Focus** — type a concept name to show only its cluster within N hops
- **Clusters** — connected components detected and listed
- **Summarize Cluster** — LLM generates a summary of related concepts
- Click any node to explore its connections, navigate to concept page or note

---

## CLI Reference

All commands support `--vault <path>` (defaults to current directory).

### `secondbrain init <path>`

Create a new vault with the full directory structure and SQLite database.

```bash
secondbrain init ~/SecondBrain
```

### `secondbrain ingest <files...>`

Ingest one or more files or directories.

```bash
# Single file
secondbrain ingest paper.pdf --vault ~/SecondBrain

# Multiple files
secondbrain ingest file1.md file2.pdf file3.txt --vault ~/SecondBrain

# Entire directory
secondbrain ingest ~/Documents/notes/ --vault ~/SecondBrain

# With glob filter
secondbrain ingest ~/Documents/ --glob "*.pdf" --vault ~/SecondBrain

# Recursive
secondbrain ingest ~/Documents/ --glob "*.md" --recursive --vault ~/SecondBrain
```

Supported formats: PDF, Markdown, text, HTML, CSV, JSON, code files (Python, JavaScript, TypeScript, Go, Rust, Java).

### `secondbrain ingest-url <url>`

Fetch and ingest a web page.

```bash
secondbrain ingest-url https://example.com/article --vault ~/SecondBrain
```

### `secondbrain compile`

Compile all pending sources into Obsidian notes using the LLM.

```bash
secondbrain compile --vault ~/SecondBrain

# Control concurrency (default: 8 parallel LLM calls)
secondbrain compile --vault ~/SecondBrain -j 16

# Use a specific model
secondbrain compile --vault ~/SecondBrain --model llama3
```

### `secondbrain ask <question>`

Ask a question and get a source-grounded answer.

```bash
secondbrain ask "What do I know about Kafka ordering?" --vault ~/SecondBrain
```

### `secondbrain health`

Run vault health checks.

```bash
secondbrain health --vault ~/SecondBrain
secondbrain health --vault ~/SecondBrain --save  # save report to system/health-reports/
```

Detects: orphan notes, broken `[[links]]`, duplicate candidates, stale notes, missing provenance, weak summaries, uncompiled sources.

### `secondbrain status`

Show vault statistics.

```bash
secondbrain status --vault ~/SecondBrain
```

### `secondbrain serve`

Start the web UI.

```bash
secondbrain serve --vault ~/SecondBrain --port 8184
secondbrain serve --vault ~/SecondBrain --host 127.0.0.1 --port 9000
```

On startup, the server:
1. Seeds the default LLM config if none exists
2. Auto-starts compiling any pending sources in the background
3. Serves the web UI

---

## LLM Configuration

VaultForge works with any OpenAI-compatible LLM endpoint.

### Supported Backends

| Backend | API | Example URL |
|---------|-----|-------------|
| **llama.cpp** | `/v1/chat/completions` | `http://localhost:8080` |
| **vLLM** | `/v1/chat/completions` | `http://localhost:8000` |
| **Ollama** | `/api/generate` | `http://localhost:11434` |

### Configure via Web UI

1. Go to **Settings** (`/settings`)
2. Enter server URL and click **Probe for Models** to discover available models
3. Select a model and click **Test Connection**
4. Save the configuration and click **Activate**

You can save multiple configurations and switch between them.

### Configure via CLI

The CLI reads the active config from the vault database. Set it up through the web UI first, then CLI commands will use it automatically.

Override the model for a single command:
```bash
secondbrain compile --vault ~/SecondBrain --model llama3
```

---

## Multi-Vault

Register multiple knowledge bases and switch between them:

1. Create vaults: `secondbrain init ~/Work` and `secondbrain init ~/Personal`
2. In the web UI, go to **Vaults** (`/vaults`)
3. Register each vault path with a name
4. Click **Switch To** to change the active vault

The entire UI — search, notes, graph, everything — switches to the selected vault.

---

## Obsidian Compatibility

Compiled notes use standard Obsidian features:
- YAML frontmatter (title, type, tags, aliases, source_ids, confidence)
- `[[Wikilinks]]` for inter-note links
- Standard Markdown formatting

Point Obsidian at your vault's `compiled/` directory to browse notes alongside VaultForge.

---

## How Compilation Works

```mermaid
flowchart TD
    subgraph Parallel["Phase 1: LLM Calls (8 concurrent)"]
        S1["Source 1"] --> LLM1["LLM Summarize"]
        S2["Source 2"] --> LLM2["LLM Summarize"]
        S3["Source 3"] --> LLM3["LLM Summarize"]
        SN["Source N"] --> LLMN["LLM Summarize"]
    end

    subgraph Write["Phase 2: Write (streaming as each completes)"]
        LLM1 --> W1
        LLM2 --> W1
        LLM3 --> W1
        LLMN --> W1

        W1["Create source note<br/>with frontmatter"] --> W2["Create concept notes<br/>(skip if exists)"]
        W2 --> W3["Register entities<br/>names, types, aliases"]
        W3 --> W4["Index into FTS5"]
    end

    W4 --> Ready["Note visible in UI"]

    style Parallel fill:#1a1b26,stroke:#bb9af7,color:#c0caf5
    style Write fill:#1a1b26,stroke:#9ece6a,color:#c0caf5
    style Ready fill:#1f3d1a,stroke:#9ece6a,color:#9ece6a
```

Each LLM call extracts: title, summary, key ideas, entities, tags, related concepts, and open questions. Notes appear in the UI as each LLM call completes — no waiting for the full batch.

The compiler never overwrites existing concept notes. It only creates new ones or adds source notes. This preserves your manual edits.

---

## Development

### Run Tests

```bash
pip install -e ".[dev]"
python3 -m pytest tests/ -v
```

114 tests covering: database, vault management, frontmatter, ingestion, compilation, query engine, health checks, CLI, LLM client.

### Project Structure

```mermaid
graph LR
    subgraph CLI["cli.py"]
        init & ingest & compile & ask & health & serve
    end

    subgraph DB["database.py"]
        Sources & Notes & Links & Entities
        Jobs & FTS5 & Annotations
    end

    subgraph Compiler["compiler/"]
        CompilePy["compile.py<br/>concurrent LLM pipeline"]
    end

    subgraph Indexes["indexes/"]
        GraphPy["graph.py<br/>co-mention graph + clusters"]
        SearchPy["search.py<br/>FTS5 + facets"]
    end

    subgraph Ingest["ingest/"]
        PipelinePy["pipeline.py<br/>file/URL/text ingestion"]
    end

    subgraph LLMPkg["llm/"]
        ClientPy["client.py<br/>llama.cpp / Ollama / mock"]
    end

    subgraph Query["query/"]
        EnginePy["engine.py<br/>retrieval + Q&A"]
    end

    subgraph Vault["vault/"]
        Manager["manager.py"]
        FM["frontmatter.py"]
        Reg["registry.py<br/>multi-vault"]
    end

    subgraph UIPkg["ui/"]
        AppPy["app.py<br/>FastAPI"]
        Templates["templates/<br/>Jinja2 HTML"]
    end

    CLI --> DB
    CLI --> Compiler
    CLI --> UIPkg
    Compiler --> LLMPkg
    Compiler --> DB
    Compiler --> Indexes
    UIPkg --> DB
    UIPkg --> Indexes
    UIPkg --> Vault
    Query --> LLMPkg
    Ingest --> DB
    Ingest --> Vault

    style CLI fill:#24283b,stroke:#7aa2f7,color:#c0caf5
    style DB fill:#24283b,stroke:#e0af68,color:#c0caf5
    style LLMPkg fill:#24283b,stroke:#bb9af7,color:#c0caf5
    style UIPkg fill:#24283b,stroke:#9ece6a,color:#c0caf5
```

---

## Design Principles

1. **Local-first** — all processing runs locally, no cloud dependencies
2. **File-native** — Markdown files are the source of truth, not a database
3. **Human-editable** — every generated note can be read, edited, and versioned
4. **Provenance-preserving** — every note traces back to its source
5. **Patch-oriented** — existing notes are never overwritten by the compiler
6. **Reviewable** — the LLM proposes; you decide
7. **Obsidian-compatible** — frontmatter, wikilinks, tags, aliases

The vector index is disposable. The Markdown vault is not.
