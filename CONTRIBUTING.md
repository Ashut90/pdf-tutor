# Contributing to PDF Tutor

Thanks for your interest in contributing!

## Development setup

```bash
git clone https://github.com/YOUR_USERNAME/pdf-tutor.git
cd pdf-tutor
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
```

## Before submitting a PR

1. Run the test suite: `pytest -v`
2. Lint the code: `ruff check pdf_tutor/`
3. Keep modules focused — UI in `ui/`, AI logic in `ai/`, etc.
4. Add tests for new logic in `tests/`

## Project conventions

- Each module has a single clear responsibility (see README architecture)
- No API keys committed — they belong in the UI at runtime only
- Prefer offline-capable features with cloud as an enhancement
