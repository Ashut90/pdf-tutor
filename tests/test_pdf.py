"""Tests for core PDF utilities."""
import pytest
from pdf_tutor.core import pdf


def test_build_chapter_list_empty_toc():
    """With no TOC, chapters should be auto-split into 20-page chunks."""
    chapters = pdf.build_chapter_list([], total=45)
    assert len(chapters) == 3          # 0-20, 20-40, 40-45
    assert chapters[0][2] == 1         # first page is 1-indexed
    assert chapters[-1][3] == 45       # last page is total


def test_build_chapter_list_with_toc():
    """A TOC should produce chapters with inferred end pages."""
    toc = [
        [1, "Chapter 1", 1],
        [1, "Chapter 2", 10],
        [1, "Chapter 3", 20],
    ]
    chapters = pdf.build_chapter_list(toc, total=30)
    assert chapters[0] == (1, "Chapter 1", 1, 9)
    assert chapters[1] == (1, "Chapter 2", 10, 19)
    assert chapters[2] == (1, "Chapter 3", 20, 30)


def test_build_chapter_list_nested():
    """Nested sections infer end page from next same-or-higher level entry."""
    toc = [
        [1, "Chapter 1", 1],
        [2, "Section 1.1", 2],
        [2, "Section 1.2", 5],
        [1, "Chapter 2", 10],
    ]
    chapters = pdf.build_chapter_list(toc, total=20)
    assert chapters[0] == (1, "Chapter 1", 1, 9)
    assert chapters[1] == (2, "Section 1.1", 2, 4)
    assert chapters[2] == (2, "Section 1.2", 5, 9)
