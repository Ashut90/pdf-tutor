# Development Notes

## Module responsibilities

| Module | Responsibility |
|--------|----------------|
| `config.py` | Theme tokens, fonts, provider definitions, limits |
| `core/pdf.py` | PDF parsing: TOC, text extraction, page rendering |
| `ai/client.py` | Unified streaming client across 4 providers |
| `rendering/visuals.py` | Mermaid / chart / graphviz image generation |
| `learning/modes.py` | Teaching-mode system prompts incl. VARK |
| `ui/app.py` | Tkinter GUI, event wiring, export tools |

## Adding a new AI provider

1. Add an entry to `PROVIDERS` in `config.py`
2. Add a branch in `AIClient.chat()` in `ai/client.py`
3. If the API format differs from OpenAI's, add a dedicated `_xxx_stream()` method

## Adding a new teaching mode

Add an entry to `MODES` in `learning/modes.py` with keys:
`icon`, `sys` (system prompt), `user` (default prompt), `followups` (list).
The UI auto-generates a button for it.

## Per-provider request limits

Free cloud tiers cap total tokens per request. `ui/app.py` truncates the
PDF chunk for `groq`/`openrouter`, sends full content to `gemini` (1M ctx)
and `ollama` (local, no limit).
