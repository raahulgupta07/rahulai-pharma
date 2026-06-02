# Attribution

The `dash/skills_cowork/` directory contains files lifted from:

- **OpenCoworkAI/open-cowork** — MIT licensed
  - Source: https://github.com/OpenCoworkAI/open-cowork
  - Path lifted: `.claude/skills/{pptx,docx,xlsx,pdf}/`

Open Cowork's skills are themselves derived from **Anthropic Claude Code skills** (also MIT).

Original `LICENSE.txt` preserved in `dash/skills_cowork/LICENSE.txt`.

No modifications made to lifted Python scripts. Integration glue lives separately in:
- `dash/tools/xlsx_recalc.py`
- `dash/tools/deck_visual_qa.py`
- `dash/tools/deck_edit.py`
- `dash/tools/docx_edit.py`
- `dash/tools/pdf_form.py`

Lifted 2026-05-24 from upstream commit at `main` HEAD.
