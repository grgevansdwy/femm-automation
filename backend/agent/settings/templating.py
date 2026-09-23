"""The syntax the editable system prompts are written in.

  {{name}}                 replaced by the value the app computes for `name`
  {{#name}} ... {{/name}}  kept only when `name` has a value (non-empty)
  {{^name}} ... {{/name}}  kept only when `name` has NO value

Not str.format / f-strings: the prompts contain literal JSON braces, and a
person editing one shouldn't have to know to double them. A tag alone on its
line takes its line break with it, so a block reads naturally in the editor
without leaving blank lines in what the model is sent. Blocks don't nest.
"""

import re

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_SECTION = re.compile(r"\{\{([#^])\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}(.*?)\{\{/\s*\2\s*\}\}", re.DOTALL)
_OPEN = re.compile(r"\{\{[#^]\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_CLOSE = re.compile(r"\{\{/\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_TAG_LINE = re.compile(r"^[ \t]*(\{\{[#^/]\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\})[ \t]*\n", re.MULTILINE)


def placeholders_in(template: str) -> list[str]:
    """Names used in `template` (as {{name}} or as a block's opening tag), in order of first appearance."""
    found = [(m.start(), m[1]) for m in _PLACEHOLDER.finditer(template)]
    found += [(m.start(), m[1]) for m in _OPEN.finditer(template)]
    return list(dict.fromkeys(name for _, name in sorted(found)))


def render(template: str, values: dict, keep_blocks: bool = False) -> str:
    """Fills in `template`. Blocks are resolved first, then each {{name}} is
    replaced with str(values[name]) in one pass, so a value that itself
    contains "{{...}}" is never expanded again. A name with no value entry is
    left as written rather than raising - a saved prompt was validated when it
    was saved, and a half-rendered prompt is easier to diagnose than a crashed
    search. keep_blocks=True leaves the {{#..}}/{{^..}} tags and their text in
    place (used by the Settings preview, which shows every branch)."""
    if not keep_blocks:
        template = _TAG_LINE.sub(r"\1", template)
        template = _SECTION.sub(
            lambda m: m[3] if bool(values.get(m[2])) == (m[1] == "#") else "", template)
    return _PLACEHOLDER.sub(lambda m: str(values[m[1]]) if m[1] in values else m[0], template)


def check_placeholders(template: str, known: dict, required=()) -> list[str]:
    """Problems with a prompt's placeholders and blocks, as messages a person
    can act on (empty list = fine): names nobody fills in, required ones
    deleted, and block tags that don't pair up."""
    used = placeholders_in(template)
    problems = [f"{{{{{name}}}}} is not a placeholder this prompt understands" for name in used if name not in known]
    problems += [f"{{{{{name}}}}} is required - the app fills in {known[name]}" for name in required if name not in used]
    opened = [m[1] for m in _OPEN.finditer(template)]
    closed = [m[1] for m in _CLOSE.finditer(template)]
    problems += [f"{{{{#{name}}}}} has no matching {{{{/{name}}}}}" for name in dict.fromkeys(opened) if opened.count(name) > closed.count(name)]
    problems += [f"{{{{/{name}}}}} has no matching {{{{#{name}}}}}" for name in dict.fromkeys(closed) if closed.count(name) > opened.count(name)]
    return problems
