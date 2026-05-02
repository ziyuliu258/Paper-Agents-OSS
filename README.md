<div align="center">

![](./assets/Project_Picture_by_Gemini.png)

# Paper Agents

**An automated paper reading workflow with AI agents**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--v3-green.svg)](./LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/ziyuliu258/Paper-Agents-OSS/pulls)

Paper Agent is a Web-based system that automates academic paper discovery, deep analysis, and knowledge synthesis. Feed it research topics or upload PDFs directly—it generates structured bilingual reports and builds a searchable long-term memory across papers.

[Getting Started](#quick-start) · [Features](#features) · [Documentation](./AGENTS.md) · [Contributing](#contributing)

</div>

---

## Features

- **Automated Paper Discovery** — Multi-source search (ArXiv, Semantic Scholar, OpenAlex, DBLP, OpenReview) with intelligent candidate ranking and topic-fit validation
- **Flexible Input** — Auto-search by topic or manual PDF upload
- **Deep Paper Analysis** — Seven-stage structured interpretation (motivation, method, experiments, ablations, limitations, assessment) with dual-model validation
- **Bilingual Reports** — Generates both English and Chinese markdown reports with automatic translation and review
- **Long-term Memory** — Profile-based knowledge base that captures entities, claims, evidence, and cross-paper synthesis
- **Report Refinement** — Interactive report variants with grounded context-aware revisions
- **Web Workbench** — Full-featured React UI for running jobs, browsing papers, managing profiles, and exploring knowledge graphs

## Architecture

```text
┌─────────────┐
│  Web UI     │  Dashboard / Run / Reports / Papers / Profiles / Workspace / Settings
└──────┬──────┘
       │
┌──────▼──────────────────────────────────────────────────────────┐
│  FastAPI Backend                                                 │
│  ┌────────────────┐  ┌─────────────────┐  ┌──────────────────┐ │
│  │ Paper Selector │→ │ Paper Processor │→ │ Paper Interpreter│ │
│  └────────────────┘  └─────────────────┘  └──────────────────┘ │
│         │                     │                      │           │
│    Multi-source          PDF/HTML              T1-T7 Analysis   │
│    Retrieval &           Extraction             + Report         │
│    Reranking                                    Generation       │
└─────────────────────────────────────────────────────────────────┘
       │                     │                      │
       ▼                     ▼                      ▼
  ┌─────────┐         ┌──────────┐         ┌─────────────┐
  │ Sources │         │ Figures  │         │  Reports    │
  │  Cache  │         │ & Tables │         │  & Memory   │
  └─────────┘         └──────────┘         └─────────────┘
```

**Pipeline Stages:**

1. **Selection** — Search across venues, deduplicate, rerank by embedding + lexical signals, validate topic fit
2. **Processing** — Extract text, figures, and tables from PDF or HTML sources
3. **Interpretation** — Build paper notes, run parallel analysis groups, audit report quality
4. **Memory** — Extract and validate claims with evidence, write to profile-scoped knowledge base
5. **Assembly** — Generate bilingual markdown reports with localized artifacts

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- OpenAI-compatible LLM API access

### Installation

```bash
# 1. Clone repository
git clone https://github.com/ziyuliu258/Paper-Agents-OSS.git
cd Paper-Agents-OSS

# 2. Set up environment
cp .env.example .env
# Edit .env and fill in your API keys

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install frontend dependencies
cd web && npm install && cd ..

# 5. Build frontend
cd web && npm run build && cd ..
```

### Configuration

Edit `config.yaml` to define your research topics:

```yaml
topics:
  - name: "Your Research Area"
    query: "Detailed description of papers you're looking for"
    keywords:
      - "keyword 1"
      - "keyword 2"

selection:
  track: "classic"                    # auto | recent | classic
  preferred_venues: ["ICLR", "NeurIPS", "ICML"]
  date_range_days: 365
  candidate_pool_size: 40

models:
  fast: "gem_flash"
  primary: "gem_pro"
  secondary: "gpt_pro"

report:
  structure_mode: "classic"           # classic | pmrc
```

### Running

```bash
# Start backend server (default port 10086)
python run.py

# Access web UI at http://localhost:10086
```

The system will automatically create `data/memory.db` and necessary directories on first run.

## Usage

### Auto Mode

1. Navigate to the **Run** page in the web UI
2. Select or create a research profile
3. Configure topic, venue preferences, and paper filters
4. Click "Start Pipeline" to begin automated paper discovery and analysis

The system will:
- Search multiple academic databases
- Rank and filter candidates by relevance
- Download the best-matching paper
- Extract figures and content
- Generate a structured analysis report
- Update the profile's long-term memory

### Manual Mode

1. Go to **Run** page and switch to "Manual PDF Upload"
2. Select a profile and upload your PDF
3. The analysis pipeline runs the same as auto mode

### Exploring Results

- **Reports** — View all completed analyses, retry failed jobs, or regenerate reports
- **Papers** — Browse your paper library and open source PDFs/HTML
- **Profiles** — Manage research profiles, view summaries, and explore accumulated knowledge
- **Workspace** — Inspect the knowledge graph, claims, evidence, and cross-paper synthesis

## Project Structure

```text
Paper-Agent/
├── server/                   # FastAPI backend
│   ├── app.py               # Main application entry
│   ├── job_manager.py       # Job lifecycle management
│   ├── routers/             # API endpoints
│   └── database.py          # SQLite persistence
│
├── modules/
│   ├── paper_selector/      # Multi-source search & ranking
│   ├── paper_processor/     # PDF/HTML extraction
│   └── paper_interpreter/   # Analysis pipeline & report generation
│
├── utils/
│   ├── memory.py            # Long-term memory management
│   ├── llm.py               # LLM API wrapper
│   └── config.py            # Configuration loading
│
├── web/                     # React frontend
│   ├── src/
│   │   ├── pages/          # Main UI pages
│   │   ├── components/     # Reusable components
│   │   └── api/client.ts   # Backend API client
│   └── package.json
│
├── tests/                   # Test suite
├── config.yaml              # Default configuration
├── .env.example             # Environment template
└── requirements.txt         # Python dependencies
```

## Advanced Features

### Profile-based Memory

Each research profile maintains its own knowledge base:
- **Entities** — Methods, datasets, metrics, concepts
- **Claims** — Findings, comparisons, limitations, hypotheses
- **Evidence** — Source snippets with section/page anchors
- **Synthesis** — Cross-paper consensus, debates, evolution
- **Relations** — Claim reinforcement, extension, contradiction

### Report Variants

After generating a report, you can request refinements:
- Structure changes (classic ↔ PMRC)
- Detail level adjustments
- Section rewrites with grounded context

### Runtime Settings

Configure LLM providers, API keys, and model aliases through the web UI without editing server files:
- OpenAI-compatible endpoints
- Embedding models
- R2 object storage
- Browser-local or server `.env` configuration

## Development

### Running from Source

```bash
# Backend with auto-reload
python run.py

# Frontend dev server (port 5173)
cd web && npm run dev
```

### Testing

```bash
# Run test suite
pytest tests/

# Specific test groups
pytest tests/test_paper_selector_regressions.py
pytest tests/test_memory_v3_schema_migration.py
```

## Contributing

Contributions are welcome! Please read our [development guide](./AGENTS.md) for:
- Code structure and conventions
- Testing requirements
- Commit message format

## License

This project is licensed under the AGPL-3.0 License - see the [LICENSE](./LICENSE) file for details.

## Citation

If you use Paper Agent in your research, please cite:

```bibtex
@software{paper_agent_2026,
  title = {Paper Agent: An Automated Paper Reading Workflow with AI Agents},
  year = {2026},
  url = {https://github.com/ziyuliu258/Paper-Agents-OSS}
}
```

## Acknowledgments

Built with:
- [FastAPI](https://fastapi.tiangolo.com/) — Modern Python web framework
- [React](https://react.dev/) — Interactive UI
- [OpenAI](https://openai.com/) — LLM capabilities
- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF processing

---

<div align="center">

**[Getting Started](#quick-start)** · **[Documentation](./AGENTS.md)** · **[Issues](https://github.com/ziyuliu258/Paper-Agents-OSS/issues)**

</div>
